import asyncio
from crawl4ai import AsyncWebCrawler

async def run_crawler(url):
    """
    The asynchronous core that launches the browser (Playwright) via Crawl4AI.
    """
    # ✅ STRATEGY 1: If it's a PDF, try to handle it (Simple version)
    if url.lower().endswith('.pdf'):
        print(f"📄 PDF Detected: {url}")
        # Allow Crawl4AI to TRY reading it by removing the 'return ""' line
        # If Crawl4AI fails, we will catch it in the try/except block below.
        pass 

    async with AsyncWebCrawler(verbose=True) as crawler:
        try:
            result = await crawler.arun(
                url=url,
                bypass_cache=True,
                magic=True,  # This often helps with simple PDFs
                word_count_threshold=10
            )
            return result.markdown
        except Exception as e:
            print(f"⚠️ Failed to scrape {url}: {e}")
            return ""  # Return empty only if it actually fails


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
