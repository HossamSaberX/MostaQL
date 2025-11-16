"""
Test endpoints for manual scraper triggering and debugging
"""
from fastapi import APIRouter
from loguru import logger
import requests
from bs4 import BeautifulSoup

from backend.scheduler import run_scraper_job

router = APIRouter()


@router.post("/trigger-scraper")
async def trigger_scraper():
    """
    Manually trigger the scraper job (for testing)
    Runs immediately instead of waiting for scheduled interval
    """
    try:
        logger.info("🧪 Manual scraper trigger requested")
        
        # Run scraper job synchronously
        run_scraper_job()
        
        return {
            "status": "success",
            "message": "Scraper job triggered successfully. Check logs for results."
        }
        
    except Exception as e:
        logger.error(f"Manual scraper trigger failed: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.get("/debug-scrape")
async def debug_scrape():
    """
    Debug endpoint to see what's actually being scraped
    Shows first 5 project links found with their titles
    """
    try:
        url = "https://mostaql.com/projects?category=3"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find all links
        all_links = soup.find_all('a', href=True)
        project_links = []
        
        for link in all_links[:100]:  # Check first 100 links
            href = link.get('href', '')
            if '/project/' in href:
                title = link.get_text(strip=True)
                full_url = href if href.startswith('http') else f"https://mostaql.com{href}"
                
                project_links.append({
                    'href': href,
                    'full_url': full_url,
                    'title': title,
                    'title_length': len(title),
                    'has_title': bool(title)
                })
                
                if len(project_links) >= 5:
                    break
        
        return {
            "status": "success",
            "total_links_on_page": len(all_links),
            "project_links_found": len(project_links),
            "samples": project_links
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

