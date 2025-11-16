"""
Background scheduler for periodic job scraping
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.utils.logger import app_logger
from backend.database import SessionLocal, Category
from backend.services.scraper import poll_category
from backend.services.notifier import process_new_jobs
from backend.config import settings


def run_scraper_job():
    """
    Run polling scraper and process new jobs
    Uses quick checks first, then full scrape only if needed
    This function runs in a separate thread
    """
    try:
        app_logger.info("🔍 Starting polling run...")
        
        db = SessionLocal()
        try:
            category_ids = [category.id for category in db.query(Category.id).all()]
        finally:
            db.close()

        total_new_jobs = 0
        skipped_count = 0
        
        for category_id in category_ids:
            try:
                # Poll: quick check first, full scrape only if needed
                new_jobs = poll_category(category_id)
                
                if not new_jobs:
                    skipped_count += 1
                    continue

                total_new_jobs += len(new_jobs)
                process_new_jobs(new_jobs, category_id)
            except Exception as category_error:
                app_logger.error(
                    f"Error polling or notifying for category {category_id}: {category_error}"
                )
        
        if total_new_jobs == 0:
            app_logger.info(f"No new jobs found (skipped {skipped_count} categories)")
        else:
            app_logger.info(f"✓ Polling run finished: {total_new_jobs} new jobs from {len(category_ids) - skipped_count} categories")
            
    except Exception as e:
        app_logger.error(f"Error in scraper job: {e}")


def start_scheduler():
    """
    Start the background scheduler with polling
    """
    # Use polling interval (default 2 minutes) instead of legacy interval
    interval_minutes = getattr(settings, 'scraper_poll_interval_minutes', 2)
    
    scheduler = BackgroundScheduler()
    
    # Add scraper job with polling interval
    scheduler.add_job(
        func=run_scraper_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="scraper_job",
        name="Poll Mostaql jobs",
        replace_existing=True
    )
    
    scheduler.start()
    
    app_logger.info(f"✓ Polling scheduler started (interval: {interval_minutes} minutes)")
    
    return scheduler


def shutdown_scheduler(scheduler):
    """
    Gracefully shutdown the scheduler
    """
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=True)
        app_logger.info("✓ Scheduler shut down")

