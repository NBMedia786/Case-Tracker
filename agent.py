import os
import json
import re
from typing import TypedDict, Literal, Optional
from datetime import datetime, date
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from tools import search_web, get_search_urls
from searcher import scrape_with_god_mode, scrape_multiple_with_god_mode
import dateutil.parser

load_dotenv()



class AgentState(TypedDict):
    """
    State for the research agent.
    
    Attributes:
        case_name: The name of the legal case to research.
        search_attempts: Number of search attempts made (for retry logic).
        scraped_data: Accumulated scraped content from web searches.
        final_verdict: The extracted case information (dict with hearing date, status, names).
        search_results: Raw search results from the last search.
        error_message: Any error message encountered during processing.
    """
    case_name: str
    docket_url: Optional[str]
    search_attempts: int
    scraped_data: str
    final_verdict: dict
    search_results: str
    error_message: str
    case_id: Optional[int]



def get_gemini_llm():
    """Initialize and return the Gemini 2.5 Pro model."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable is not set.")
    
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-pro",
        google_api_key=api_key,
        temperature=0.1,  # Low temperature for more deterministic extraction
        convert_system_message_to_human=True
    )



PROGRESS = {}

from db import update_case, get_supabase_client

def update_progress(case_id, step, percent, message):
    """
    Update the progress for a specific case.
    Persists to BOTH memory (fast access) and Database (resilience).
    """
    if case_id:
        status_payload = {
            "step": step,
            "percent": percent,
            "message": message,
            "status": "processing"
        }
        if percent >= 100:
            status_payload["status"] = "complete"
            
        PROGRESS[case_id] = status_payload

        try:
            db_status = "processing"
            if percent >= 100:
                db_status = "complete"
            
            update_case(case_id, {
                "processing_status": db_status,
                "progress_percent": percent,
                "progress_message": message
            })
        except Exception as e:
            print(f"⚠️ Progress DB save failed: {e}")



def node_search(state: AgentState) -> AgentState:
    """
    Hybrid Search Node:
    - Attempt 0: If 'docket_url' exists, scrape it directly.
    - Attempt 1+: If Docket failed or missing, use Google Search.
    """
    case_name = state["case_name"]
    case_id = state.get("case_id")  # Get case_id from state
    docket_url = state.get("docket_url")
    search_attempts = state.get("search_attempts", 0)

    update_progress(case_id, "search", 20 + (search_attempts * 10), f"Searching: Attempt {search_attempts + 1}")

    if search_attempts == 0 and docket_url:
        print(f"🔗 Checking Official Docket URL: {docket_url}")
        update_progress(case_id, "search", 25, "Accessing Official Docket...")
        try:
            scraped_content = scrape_with_god_mode(docket_url)

            if scraped_content:
                print(f"✅ Successfully scraped official docket ({len(scraped_content)} chars)")
                return {
                    **state,
                    "search_attempts": search_attempts + 1,
                    "scraped_data": f"## OFFICIAL DOCKET SOURCE ({docket_url})\n\n{scraped_content[:20000]}",
                    "search_results": f"Direct scrape of {docket_url}",
                    "error_message": ""
                }
            else:
                print("❌ Official docket scrape returned empty. Falling back to search.")
                search_attempts += 1 
        except Exception as e:
            print(f"❌ Docket scrape failed: {e}")
            search_attempts += 1 

    try:
        update_progress(case_id, "search", 30 + (search_attempts * 10), "Running Google Search...")
        if search_attempts == 0:
            query = f"latest court hearing {case_name}"
        elif search_attempts == 1:
            query = f"docket schedule {case_name} official record"
        else:
            query = f"court case status {case_name}"
        
        print(f"🔍 Search attempt {search_attempts + 1} (Google): '{query}'")
        
        search_results = search_web.invoke({"query": query})
        
        update_progress(case_id, "search", 40 + (search_attempts * 10), "Scanning Search Results...")

        urls = get_search_urls(query)
        scraped_data = ""
        
        if urls:
            target_urls = urls[:2] # Limit to top 2
            print(f"🚀 Engaging God Mode (Searcher) for {len(target_urls)} URLs...")
            
            update_progress(case_id, "search", 45 + (search_attempts * 10), f"Reading {len(target_urls)} Sources...")
            
            batch_results = scrape_multiple_with_god_mode(target_urls)
            
            scraped_parts = []
            for url, content in batch_results.items():
                if content:
                    print(f"✅ Downloaded {len(content)} characters from {url}")
                    scraped_parts.append(f"## Web Source: {url}\n\n{content[:5000]}")
                else:
                     print(f"❌ Scrape failed for {url}")
            
            scraped_data = "\n\n---\n\n".join(scraped_parts)
        
        previous_data = state.get("scraped_data", "")
        combined_data = f"{previous_data}\n\n--- Search Attempt {search_attempts + 1} ---\n\n{scraped_data}"
        
        return {
            **state,
            "search_attempts": search_attempts + 1,
            "search_results": search_results,
            "scraped_data": combined_data.strip(),
            "error_message": ""
        }
    
    except Exception as e:
        print(f"❌ Search error: {e}")
        return {
            **state,
            "search_attempts": search_attempts + 1,
            "error_message": f"Search failed: {str(e)}"
        }


def node_analyze(state: AgentState) -> AgentState:
    """
    Analyze node: Feeds scraped data to Gemini 2.5 Pro for information extraction.
    """
    case_name = state["case_name"]
    case_id = state.get("case_id")
    scraped_data = state.get("scraped_data", "")
    search_results = state.get("search_results", "")
    
    update_progress(case_id, "analyze", 70, "Analyzing Legal Data (Gemini)...")

    current_date = datetime.now().strftime("%Y-%m-%d")
    
    if not scraped_data and not search_results:
        return {
            **state,
            "final_verdict": {
                "next_hearing_date": "Unknown",
                "last_hearing_date": "Unknown",
                "case_status": "Unknown",
                "victim_name": "Unknown",
                "suspect_name": "Unknown",
                "confidence": "low",
                "notes": "No data available to analyze.",
                "requires_manual_review": True
            }
        }
    
    try:
        llm = get_gemini_llm()
        
        system_prompt = f"""You are a legal research assistant. Analyze the following text regarding the case '{case_name}'.
    Current Date: {current_date}

    Your Goal: Extract the timeline of the case.
    
    1. **Next Hearing Date:** Find any scheduled court date happening AFTER {current_date}. 
       - If none, return "Unknown".
    2. **Last Hearing Date:** Find the most recent court date that happened BEFORE {current_date}.
       - If the case is Closed, this is the date it was closed/verdict read.
    3. **Status:** "Open", "Closed", or "Verdict Reached".
    
    Return STRICT JSON format:
    {{
        "next_hearing_date": "YYYY-MM-DD" or "Unknown",
        "last_hearing_date": "YYYY-MM-DD" or "Unknown",
        "case_status": "Open/Closed/Verdict Reached",
        "victim_name": "Name or Unknown",
        "suspect_name": "Name or Unknown",
        "confidence": "high/medium/low",
        "notes": "A professional 2-sentence summary of the latest updates.",
        "requires_manual_review": true/false
    }}
    
    Respond ONLY with the JSON object."""

        user_prompt = f"""Analyze the following content for the legal case: "{case_name}"
        
=== SEARCH RESULTS ===
{search_results}

=== SCRAPED WEB CONTENT ===
{scraped_data[:15000]}  # Limit to avoid token overflow
"""
 
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]
        
        response = llm.invoke(messages)
        response_text = response.content.strip()
        
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)
        
        try:
            verdict = json.loads(response_text)
        except json.JSONDecodeError:
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                verdict = json.loads(json_match.group())
            else:
                raise ValueError("Could not parse JSON from response")
        
        verdict = {
            "next_hearing_date": verdict.get("next_hearing_date", "Unknown"),
            "last_hearing_date": verdict.get("last_hearing_date", "Unknown"),
            "case_status": verdict.get("case_status", "Unknown"),
            "victim_name": verdict.get("victim_name", "Unknown"),
            "suspect_name": verdict.get("suspect_name", "Unknown"),
            "confidence": verdict.get("confidence", "low"),
            "notes": verdict.get("notes", ""),
            "requires_manual_review": verdict.get("requires_manual_review", False)
        }

        # ✅ FIX: Status Normalization (Vocabulary Mismatch)
        raw_status = verdict.get("case_status", "Unknown")
        status_map = {
            "Dismissed": "Closed",
            "Settled": "Closed",
            "Adjudicated": "Verdict Reached",
            "Sentenced": "Verdict Reached",
            "Stayed": "Open",
            "Adjourned": "Open", 
            "Active": "Open",
            "Pending": "Open" 
        }

        # If exact match fails, check the map
        if raw_status not in ["Open", "Closed", "Verdict Reached", "Unknown"]:
             # Try direct map
            mapped = status_map.get(raw_status)
            if not mapped:
                # Try case insensitive
                for key, val in status_map.items():
                    if key.lower() in raw_status.lower():
                        mapped = val
                        break
            
            if mapped:
                print(f"🔄 Normalized Status: '{raw_status}' -> '{mapped}'")
                verdict["case_status"] = mapped
            else:
                # If still unknown but suggests closure
                if any(x in raw_status.lower() for x in ['close', 'end', 'finish']):
                    verdict["case_status"] = "Closed"
                else:
                    verdict["case_status"] = "Open" # Default fallback safety

        
        
        print(f"📋 Analysis complete: {json.dumps(verdict, indent=2)}")
        
        update_progress(case_id, "analyze", 90, "Finalizing Verdict...")

        return {
            **state,
            "final_verdict": verdict,
            "error_message": ""
        }
    
    except Exception as e:
        print(f"❌ Analysis error: {e}")
        return {
            **state,
            "final_verdict": {
                "next_hearing_date": "Unknown",
                "last_hearing_date": "Unknown",
                "case_status": "Unknown",
                "victim_name": "Unknown",
                "suspect_name": "Unknown",
                "confidence": "low",
                "notes": f"Analysis failed: {str(e)}",
                "requires_manual_review": True
            },
            "error_message": str(e)
        }


def node_decision(state: AgentState) -> Literal["node_search", "end"]:
    verdict = state.get("final_verdict", {})
    search_attempts = state.get("search_attempts", 0)
    
    status = verdict.get("case_status", "Unknown")
    next_date = verdict.get("next_hearing_date", "Unknown")

    if status in ["Closed", "Verdict Reached"]:
        print(f"🛑 Case is {status}. Stopping research.")
        return "end"

    if next_date and next_date != "Unknown":
        try:
            # Try standard format first
            parsed_date = datetime.strptime(next_date, "%Y-%m-%d").date()
        except ValueError:
            try:
                # Fallback for other formats
                parsed_date = dateutil.parser.parse(next_date).date()
            except:
                parsed_date = None

        if parsed_date:
            if parsed_date >= date.today():
                print(f"✅ Future hearing found: {next_date} (parsed as {parsed_date})")
                return "end"
            else:
                print(f"⚠️ Date is in the past: {next_date} (parsed as {parsed_date}). Case is OPEN. Retrying...")
                verdict["last_hearing_date"] = next_date 
                verdict["next_hearing_date"] = "Unknown"
        else:
            print(f"⚠️ Could not parse date: {next_date}")

    if search_attempts < 2:
        print(f"🔄 Retrying search (attempt {search_attempts + 1}/2)")
        return "node_search"
    
    return "end"



def build_research_agent():
    """Build and compile the LangGraph research agent."""
    workflow = StateGraph(AgentState)
    workflow.add_node("node_search", node_search)
    workflow.add_node("node_analyze", node_analyze)
    workflow.set_entry_point("node_search")
    workflow.add_edge("node_search", "node_analyze")
    workflow.add_conditional_edges("node_analyze", node_decision, {
            "node_search": "node_search",
            "end": END
        })
    return workflow.compile()

research_agent = build_research_agent()



def research_case(case_name: str, docket_url: Optional[str] = None, case_id: Optional[int] = None) -> dict:
    """
    Research a legal case using the autonomous agent.
    """
    print(f"\n{'='*60}")
    print(f"🔎 Starting research for case: {case_name}")
    if docket_url:
        print(f"🔗 Docket URL provided: {docket_url}")
    print(f"{'='*60}\n")
    
    update_progress(case_id, "start", 5, "Initializing Agent...")

    initial_state: AgentState = {
        "case_name": case_name,
        "docket_url": docket_url,
        "case_id": case_id,             # Pass ID to state 
        "search_attempts": 0,
        "scraped_data": "",
        "final_verdict": {},
        "search_results": "",
        "error_message": ""
    }
    
    final_state = research_agent.invoke(initial_state)
    
    update_progress(case_id, "complete", 100, "Research Complete!")

    print(f"\n{'='*60}")
    print("✅ Research complete!")
    print(f"{'='*60}\n")
    
    return {
        "case_name": case_name,
        "search_attempts": final_state.get("search_attempts", 0),
        "verdict": final_state.get("final_verdict", {}),
        "success": not final_state.get("final_verdict", {}).get("requires_manual_review", False)
    }



if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        case_name = " ".join(sys.argv[1:])
    else:
        case_name = input("Enter case name to research: ").strip()
    
    if not case_name:
        print("Error: Please provide a case name.")
        sys.exit(1)
    
    result = research_case(case_name)
    
    print("\n📊 Final Result:")
    print(json.dumps(result, indent=2, default=str))

def process_case(case_name, docket_url=None, case_id=None):
    return research_case(case_name, docket_url, case_id)
