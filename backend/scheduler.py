"""
Background scheduler for periodic job scraping
"""
from datetime import datetime
from math import ceil
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.utils.logger import app_logger
from backend.database import SessionLocal, Category
from backend.services.scraper import poll_category
from backend.services.notifier import flush_notifications, process_new_jobs
from backend.config import settings


_scraper_run_lock = Lock()


def run_scraper_job():
    """Run one polling cycle, avoiding overlapping manual/scheduled runs."""
    if not _scraper_run_lock.acquire(blocking=False):
        app_logger.warning("Skipping polling run because another run is still active")
        return
    try:
        _run_scraper_job()
    finally:
        _scraper_run_lock.release()


def _run_scraper_job():
    """
    Run polling scraper and process new jobs
    Scans each category listing so bursts of projects cannot be hidden by an
    unchanged first row.
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
        
        app_logger.debug(f"Processing {len(category_ids)} categories")
        
        for category_id in category_ids:
            try:
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
        
        # Once per cycle, also when nothing is new: hourly and daily digests are due by the clock.
        try:
            flush_notifications()
        except Exception as flush_error:
            app_logger.error(f"Error sending notifications: {flush_error}")

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
    configured_seconds = getattr(settings, 'scraper_poll_interval_seconds', None)
    if configured_seconds is not None:
        interval_seconds = max(5.0, float(configured_seconds))
    else:
        interval_seconds = max(
            5.0,
            float(getattr(settings, 'scraper_poll_interval_minutes', 2)) * 60,
        )
    
    scheduler = BackgroundScheduler()
    
    scheduler.add_job(
        func=run_scraper_job,
        trigger=IntervalTrigger(seconds=interval_seconds),
        id="scraper_job",
        name="Poll Mostaql jobs",
        replace_existing=True,
        # Poll once at startup instead of waiting for the first interval.
        next_run_time=datetime.now(),
        # A slow multi-page scrape must not create overlapping runs or replay
        # every missed interval; the next scheduled run catches up normally.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(30, ceil(interval_seconds)),
    )
    
    scheduler.start()
    
    app_logger.info(f"✓ Polling scheduler started (interval: {interval_seconds:g} seconds)")
    
    return scheduler


def shutdown_scheduler(scheduler):
    """
    Gracefully shutdown the scheduler
    """
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=True)
        app_logger.info("✓ Scheduler shut down")
