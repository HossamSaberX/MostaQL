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
    Shows first 5 project rows found with their titles
    """
    try:
        url = "https://mostaql.com/projects"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find tbody with projects
        tbody = soup.find('tbody', attrs={'data-filter': 'collection'})
        
        if not tbody:
            return {
                "status": "error",
                "message": "No tbody with data-filter='collection' found"
            }
        
        # Find project rows
        project_rows = tbody.find_all('tr', class_='project-row')
        samples = []
        
        for row in project_rows[:5]:
            try:
                title_link = row.find('h2').find('a') if row.find('h2') else None
                
                if title_link:
                    title = title_link.get_text(strip=True)
                    url = title_link.get('href', '')
                    full_url = url if url.startswith('http') else f"https://mostaql.com{url}"
                    
                    samples.append({
                        'title': title,
                        'url': full_url,
                        'title_length': len(title)
                    })
            except Exception as e:
                continue
        
        return {
            "status": "success",
            "total_project_rows": len(project_rows),
            "samples": samples
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

