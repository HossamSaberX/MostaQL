"""
Background scheduler for periodic job scraping
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.config import settings
from backend.utils.logger import app_logger
from backend.database import SessionLocal
from backend.services.scraper import scrape_all_categories
from backend.services.notifier import process_new_jobs


def run_scraper_job():
    """
    Run scraper and process new jobs
    This function runs in a separate thread
    """
    db = SessionLocal()
    try:
        app_logger.info("🔍 Starting scheduled scraper run...")
        
        # Scrape all categories
        new_jobs_count = scrape_all_categories(db)
        
        if new_jobs_count > 0:
            app_logger.info(f"✓ Found {new_jobs_count} new jobs")
            
            # Process and send notifications
            notifications_sent = process_new_jobs(db)
            app_logger.info(f"✓ Sent {notifications_sent} notifications")
        else:
            app_logger.info("No new jobs found")
            
    except Exception as e:
        app_logger.error(f"Error in scraper job: {e}")
    finally:
        db.close()


def start_scheduler():
    """
    Start the background scheduler
    """
    scheduler = BackgroundScheduler()
    
    # Add scraper job with interval from settings
    scheduler.add_job(
        func=run_scraper_job,
        trigger=IntervalTrigger(minutes=settings.scraper_interval_minutes),
        id="scraper_job",
        name="Scrape Mostaql jobs",
        replace_existing=True
    )
    
    scheduler.start()
    
    app_logger.info(
        f"✓ Scheduler started (interval: {settings.scraper_interval_minutes} minutes)"
    )
    
    return scheduler


def shutdown_scheduler(scheduler):
    """
    Gracefully shutdown the scheduler
    """
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=True)
        app_logger.info("✓ Scheduler shut down")

