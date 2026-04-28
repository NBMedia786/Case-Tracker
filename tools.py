import os
import requests
from typing import List
from langchain.tools import tool
from dotenv import load_dotenv
from searcher import scrape_with_god_mode

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_URL = "https://api.tavily.com/search"

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL = "https://google.serper.dev/search"


def _tavily_request(query: str, num: int = 10) -> list:
    """Call Tavily Search API. Returns normalized list of {title, url, content}."""
    if not TAVILY_API_KEY:
        return []
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": min(num, 20),
        "search_depth": "advanced",
        "include_answer": False,
        "include_raw_content": False,
    }
    response = requests.post(TAVILY_URL, json=payload, timeout=30)
    response.raise_for_status()
    raw = response.json().get("results", [])
    return [
        {
            "title": r.get("title", "No title"),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in raw if r.get("url")
    ]


def _serper_request(query: str, num: int = 10) -> list:
    """Call Serper API. Returns normalized list of {title, url, content}."""
    if not SERPER_API_KEY:
        return []
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": min(num, 20)}
    response = requests.post(SERPER_URL, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    raw = response.json().get("organic", [])
    return [
        {
            "title": r.get("title", "No title"),
            "url": r.get("link", ""),
            "content": r.get("snippet", ""),
        }
        for r in raw if r.get("link")
    ]


def _hybrid_search(query: str, num: int = 10) -> list:
    """
    Hybrid search: Tavily first, Serper fallback if Tavily errors or returns empty.
    Returns normalized result list.
    """
    try:
        results = _tavily_request(query, num=num)
        if results:
            print(f"[Search] Tavily returned {len(results)} results")
            return results
        print("[Search] Tavily returned no results, falling back to Serper...")
    except Exception as e:
        print(f"[Search] Tavily failed ({e}), falling back to Serper...")

    try:
        results = _serper_request(query, num=num)
        if results:
            print(f"[Search] Serper (fallback) returned {len(results)} results")
        return results
    except Exception as e:
        print(f"[Search] Serper fallback also failed: {e}")
        return []


@tool
def search_web(query: str) -> str:
    """
    Search the web using a hybrid Tavily→Serper fallback chain and return top 10 results.

    Args:
        query: The search query string.

    Returns:
        A formatted string containing the top 10 search results with URLs and snippets.
    """
    if not TAVILY_API_KEY and not SERPER_API_KEY:
        return "Error: At least one of TAVILY_API_KEY or SERPER_API_KEY must be set."

    try:
        results = _hybrid_search(query, num=10)

        if not results:
            return f"No search results found for query: '{query}'"

        formatted_results = []
        for i, result in enumerate(results[:10], 1):
            formatted_results.append(
                f"{i}. **{result['title']}**\n"
                f"   URL: {result['url']}\n"
                f"   Snippet: {result['content']}\n"
            )

        return f"**Search Results for '{query}':**\n\n" + "\n".join(formatted_results)

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
    Utility function to get just the URLs from a hybrid search.

    Args:
        query: The search query string.

    Returns:
        A list of URLs from the search results.
    """
    if not TAVILY_API_KEY and not SERPER_API_KEY:
        return []

    try:
        results = _hybrid_search(query, num=10)
        return [r["url"] for r in results[:10] if r.get("url")]
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
