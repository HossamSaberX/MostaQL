"""
Test endpoints for manual scraper triggering
"""
from fastapi import APIRouter
from loguru import logger

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

