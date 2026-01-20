import asyncio
from crawl4ai import AsyncWebCrawler

async def run_crawler(url):
    """
    The asynchronous core that launches the browser (Playwright) via Crawl4AI.
    """
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(
            url=url,
            bypass_cache=True,  # Always get fresh data
            magic=True,         # Handles popups/cookie banners automatically
            word_count_threshold=10  # Ignores tiny useless text
        )
        return result.markdown

def scrape_with_god_mode(url):
    """
    The wrapper function your Agent calls.
    It handles the complex 'Async' stuff so your App doesn't crash.
    """
    try:
        # Use asyncio.run() which manages the loop lifecycle automatically and correctly
        return asyncio.run(run_crawler(url))
    except Exception as e:
        print(f"❌ Searcher Crash: {e}")
        return None
