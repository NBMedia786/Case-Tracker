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


async def run_crawler_batch(urls):
    """
    Run the crawler for multiple URLs in a single browser session.
    """
    results = {}
    async with AsyncWebCrawler(verbose=True) as crawler:
        # We can't actually "batch" parallel in one call easily with this lib 
        # unless we use gather, but reusing the crawler instance context manager 
        # keeps the browser open!
        for url in urls:
            try:
                result = await crawler.arun(
                    url=url,
                    bypass_cache=True,
                    magic=True,
                    word_count_threshold=10
                )
                results[url] = result.markdown
            except Exception as e:
                print(f"⚠️ Error scraping {url}: {e}")
                results[url] = None
    return results

def scrape_with_god_mode(url):
    """
    Legacy single URL scraper.
    """
    try:
        return asyncio.run(run_crawler(url))
    except Exception as e:
        print(f"❌ Searcher Crash: {e}")
        return None

def scrape_multiple_with_god_mode(urls):
    """
    Batch scraper wrapper.
    """
    try:
        return asyncio.run(run_crawler_batch(urls))
    except Exception as e:
        print(f"❌ Searcher Batch Crash: {e}")
        return {}
