"""
Background scheduler for periodic job scraping
"""
import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.utils.logger import app_logger
from backend.database import SessionLocal
from backend.services.scraper import scrape_all_categories
from backend.services.notifier import process_new_jobs


def run_scraper_job():
    """
    Run scraper and process new jobs
    This function runs in a separate thread
    """
    try:
        app_logger.info("🔍 Starting scheduled scraper run...")
        
        # Scrape all categories (creates its own db session)
        stats = scrape_all_categories()
        new_jobs = stats.get("new_jobs", 0)
        
        if new_jobs > 0:
            app_logger.info(f"✓ Found {new_jobs} new jobs")
            
            # Process and send notifications
            db = SessionLocal()
            try:
                notifications_sent = process_new_jobs(db)
                app_logger.info(f"✓ Sent {notifications_sent} notifications")
            finally:
                db.close()
        else:
            app_logger.info("No new jobs found")
            
    except Exception as e:
        app_logger.error(f"Error in scraper job: {e}")


def start_scheduler():
    """
    Start the background scheduler
    """
    interval_minutes = int(os.getenv("SCRAPER_INTERVAL_MINUTES", "30"))
    
    scheduler = BackgroundScheduler()
    
    # Add scraper job with interval from env
    scheduler.add_job(
        func=run_scraper_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="scraper_job",
        name="Scrape Mostaql jobs",
        replace_existing=True
    )
    
    scheduler.start()
    
    app_logger.info(f"✓ Scheduler started (interval: {interval_minutes} minutes)")
    
    return scheduler


def shutdown_scheduler(scheduler):
    """
    Gracefully shutdown the scheduler
    """
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=True)
        app_logger.info("✓ Scheduler shut down")

