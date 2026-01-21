import os
import requests
from typing import List
from langchain.tools import tool
from dotenv import load_dotenv
from searcher import scrape_with_god_mode

load_dotenv()

SERPER_API_KEY = os.getenv("SERPER_API_KEY")


@tool
def search_web(query: str) -> str:
    """
    Search the web using Serper API (Google Search) and return top 5 results.
    
    Args:
        query: The search query string to look up on Google.
    
    Returns:
        A formatted string containing the top 5 search results with URLs and snippets.
    """
    if not SERPER_API_KEY:
        return "Error: SERPER_API_KEY environment variable is not set."
    
    try:
        url = "https://google.serper.dev/search"
        
        headers = {
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json"
        }
        
        payload = {
            "q": query,
            "num": 5  # Request top 5 results
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        
        results = data.get("organic", [])
        
        if not results:
            return f"No search results found for query: '{query}'"
        
        formatted_results = []
        for i, result in enumerate(results[:5], 1):
            title = result.get("title", "No title")
            link = result.get("link", "No URL")
            snippet = result.get("snippet", "No description available")
            
            formatted_results.append(
                f"{i}. **{title}**\n"
                f"   URL: {link}\n"
                f"   Snippet: {snippet}\n"
            )
        
        return f"**Search Results for '{query}':**\n\n" + "\n".join(formatted_results)
    
    except requests.exceptions.Timeout:
        return f"Error: Search request timed out for query: '{query}'"
    except requests.exceptions.HTTPError as e:
        return f"Error: HTTP error occurred during search: {str(e)}"
    except requests.exceptions.RequestException as e:
        return f"Error: Failed to perform search: {str(e)}"
    except Exception as e:
        return f"Error: Unexpected error during search: {str(e)}"


@tool
def scrape_content(urls: List[str]) -> str:
    """
    Scrape and extract clean text content from a list of URLs using God Mode (Crawl4AI).
    Limits content to 5000 characters per URL to save context window.
    
    Args:
        urls: A list of URLs to scrape content from.
    
    Returns:
        A formatted string containing the extracted content from each URL.
    """
    if not urls:
        return "Error: No URLs provided for scraping."
    
    results = []
    
    for url in urls:
        try:
            content = scrape_with_god_mode(url)
            
            if len(content) > 5000:
                content = content[:5000] + "\n\n[...content truncated at 5000 characters...]"
            
            results.append(f"## Content from: {url}\n\n{content}\n")
        except Exception as e:
            results.append(f"## Content from: {url}\n\nError: Failed to scrape - {str(e)}\n")
    
    return "\n---\n\n".join(results)



def get_search_urls(query: str) -> List[str]:
    """
    Utility function to get just the URLs from a search query.
    Useful when you need to search and then scrape.
    
    Args:
        query: The search query string.
    
    Returns:
        A list of URLs from the search results.
    """
    if not SERPER_API_KEY:
        return []
    
    try:
        url = "https://google.serper.dev/search"
        
        headers = {
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json"
        }
        
        payload = {
            "q": query,
            "num": 5
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        results = data.get("organic", [])
        
        return [result.get("link") for result in results[:5] if result.get("link")]
    
    except Exception:
        return []


def search_and_scrape(query: str) -> str:
    """
    Combined utility function that searches for a query and scrapes the top results.
    
    Args:
        query: The search query string.
    
    Returns:
        Combined search results and scraped content.
    """
    urls = get_search_urls(query)
    
    if not urls:
        return f"No search results found for: {query}"
    
    scraped = scrape_content.invoke({"urls": urls[:3]})
    
    return f"**Research Results for '{query}':**\n\n{scraped}"
