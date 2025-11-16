"""
Background scheduler for periodic job scraping
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.utils.logger import app_logger
from backend.database import SessionLocal, Category
from backend.services.scraper import scrape_category_with_logging
from backend.services.notifier import process_new_jobs
from backend.config import settings


def run_scraper_job():
    """
    Run scraper and process new jobs
    This function runs in a separate thread
    """
    try:
        app_logger.info("🔍 Starting scheduled scraper run...")
        
        db = SessionLocal()
        try:
            category_ids = [category.id for category in db.query(Category.id).all()]
        finally:
            db.close()

        total_new_jobs = 0
        for category_id in category_ids:
            try:
                new_jobs = scrape_category_with_logging(category_id)
                if not new_jobs:
                    continue

                total_new_jobs += len(new_jobs)
                process_new_jobs(new_jobs, category_id)
            except Exception as category_error:
                app_logger.error(
                    f"Error scraping or notifying for category {category_id}: {category_error}"
                )
        
        if total_new_jobs == 0:
            app_logger.info("No new jobs found in this run")
        else:
            app_logger.info(f"✓ Scrape run finished with {total_new_jobs} new jobs")
            
    except Exception as e:
        app_logger.error(f"Error in scraper job: {e}")


def start_scheduler():
    """
    Start the background scheduler
    """
    interval_minutes = settings.scraper_interval_minutes
    
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

