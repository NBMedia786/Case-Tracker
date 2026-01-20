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
# Import your new local browser engine
from searcher import scrape_with_god_mode

# Load environment variables
load_dotenv()


# ==================== State Definition ====================

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


# ==================== LLM Initialization ====================

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


# ==================== Node Functions ====================

def node_search(state: AgentState) -> AgentState:
    """
    Hybrid Search Node:
    - Attempt 0: If 'docket_url' exists, scrape it directly.
    - Attempt 1+: If Docket failed or missing, use Google Search.
    """
    case_name = state["case_name"]
    docket_url = state.get("docket_url")
    search_attempts = state.get("search_attempts", 0)

    # --- STRATEGY 1: DIRECT DOCKET SCRAPE (Attempt 0) ---
    if search_attempts == 0 and docket_url:
        print(f"🔗 Checking Official Docket URL: {docket_url}")
        try:
            # Use God Mode directly on the target
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
                # Fall through to Google Search logic below (but increment attempt so we don't loop)
                search_attempts += 1 
        except Exception as e:
            print(f"❌ Docket scrape failed: {e}")
            search_attempts += 1 

    # --- STRATEGY 2: GOOGLE SEARCH (Fallback) ---
    # If we are here, either Docket URL was missing, failed, or we are on retry loops
    try:
        if search_attempts == 0:
            query = f"latest court hearing {case_name}"
        elif search_attempts == 1:
            query = f"docket schedule {case_name} official record"
        else:
            query = f"court case status {case_name}"
        
        print(f"🔍 Search attempt {search_attempts + 1} (Google): '{query}'")
        
        # Perform search
        search_results = search_web.invoke({"query": query})
        
        # Get URLs and scrape content
        urls = get_search_urls(query)
        scraped_data = ""
        
        if urls:
            # Scrape top 2 to save time if falling back
            scraped_parts = []
            for search_url in urls[:2]:
                print(f"🚀 Engaging God Mode (Searcher) for: {search_url}")
                page_content = scrape_with_god_mode(search_url)
                
                if page_content:
                    print(f"✅ Downloaded {len(page_content)} characters of clean Markdown.")
                    scraped_parts.append(f"## Web Source: {search_url}\n\n{page_content[:5000]}") # Limit per source
                else:
                    print("❌ Scrape failed. Skipping this source.")
            
            scraped_data = "\n\n---\n\n".join(scraped_parts)
        
        # Combine previous scraped data with new data
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
    
    Extracts:
    - Next Hearing Date
    - Last Hearing Date
    - Case Status
    - Victim/Suspect names
    
    Returns a structured JSON response.
    """
    case_name = state["case_name"]
    scraped_data = state.get("scraped_data", "")
    search_results = state.get("search_results", "")
    
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
        
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)
        
        # Parse the JSON response
        try:
            verdict = json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON object from text
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                verdict = json.loads(json_match.group())
            else:
                raise ValueError("Could not parse JSON from response")
        
        # Validate and normalize the verdict
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
        
        print(f"📋 Analysis complete: {json.dumps(verdict, indent=2)}")
        
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
    """
    Decision node: Determines whether to continue searching or end.
    
    Logic:
    - If Gemini finds a valid future date -> Go to END
    - If Gemini says 'Unknown' AND search_attempts < 2 -> Loop back to node_search
    - If search_attempts >= 2 -> Go to END (mark as 'Manual Review')
    """
    verdict = state.get("final_verdict", {})
    search_attempts = state.get("search_attempts", 0)
    
    next_hearing_date = verdict.get("next_hearing_date", "Unknown")
    
    # Check if we have a valid future date
    if next_hearing_date and next_hearing_date != "Unknown":
        try:
            # Try to parse the date
            parsed_date = datetime.strptime(next_hearing_date, "%Y-%m-%d").date()
            today = date.today()
            
            if parsed_date >= today:
                print(f"✅ Valid future hearing date found: {next_hearing_date}")
                return "end"
            else:
                print(f"⚠️ Date found but it's in the past: {next_hearing_date} (Keeping it for reference)")
                # We DO NOT return "Unknown" anymore. We keep the date.
                pass
                return "end"
        except ValueError:
            # Invalid date format, treat as unknown
            print(f"⚠️ Invalid date format: {next_hearing_date}")
    
    # Check if we should retry
    if next_hearing_date == "Unknown" and search_attempts < 2:
        print(f"🔄 Retrying search (attempt {search_attempts + 1}/2)")
        return "node_search"
    
    # Max attempts reached or other conditions
    if search_attempts >= 2:
        print("⚠️ Max search attempts reached. Marking for manual review.")
        verdict["requires_manual_review"] = True
        verdict["notes"] = f"{verdict.get('notes', '')} [Max search attempts reached]"
    
    return "end"


# ==================== Graph Construction ====================

def build_research_agent():
    """
    Build and compile the LangGraph research agent.
    
    Returns:
        A compiled LangGraph application.
    """
    # Create the graph
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("node_search", node_search)
    workflow.add_node("node_analyze", node_analyze)
    
    # Set entry point
    workflow.set_entry_point("node_search")
    
    # Add edges
    workflow.add_edge("node_search", "node_analyze")
    
    # Add conditional edge from analyze to either search or end
    workflow.add_conditional_edges(
        "node_analyze",
        node_decision,
        {
            "node_search": "node_search",
            "end": END
        }
    )
    
    # Compile the graph
    return workflow.compile()


# Create the compiled research agent
research_agent = build_research_agent()


# ==================== Public API ====================

def research_case(case_name: str, docket_url: Optional[str] = None) -> dict:
    """
    Research a legal case using the autonomous agent.
    
    Args:
        case_name: The name of the legal case to research.
        docket_url: Optional URL of the official docket to check first.
    
    Returns:
        A dictionary containing the research results.
    """
    print(f"\n{'='*60}")
    print(f"🔎 Starting research for case: {case_name}")
    if docket_url:
        print(f"🔗 Docket URL provided: {docket_url}")
    print(f"{'='*60}\n")
    
    initial_state: AgentState = {
        "case_name": case_name,
        "docket_url": docket_url,
        "search_attempts": 0,
        "scraped_data": "",
        "final_verdict": {},
        "search_results": "",
        "error_message": ""
    }
    
    # Run the agent
    final_state = research_agent.invoke(initial_state)
    
    print(f"\n{'='*60}")
    print("✅ Research complete!")
    print(f"{'='*60}\n")
    
    return {
        "case_name": case_name,
        "search_attempts": final_state.get("search_attempts", 0),
        "verdict": final_state.get("final_verdict", {}),
        "success": not final_state.get("final_verdict", {}).get("requires_manual_review", False)
    }


# ==================== CLI for Testing ====================

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

# Alias for compatibility
# Alias for compatibility
def process_case(case_name, docket_url=None):
    return research_case(case_name, docket_url)
