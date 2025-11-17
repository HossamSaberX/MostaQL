"""
Health check and categories endpoints
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from datetime import datetime, timedelta
from typing import List
from loguru import logger

from backend.database import (
    get_db, User, Category, Job, Notification, ScraperLog
)
from backend.models import (
    CategoryResponse, DetailedHealthResponse,
    ScraperMetrics, EmailMetrics, DatabaseMetrics
)

router = APIRouter()


@router.get("/categories", response_model=List[CategoryResponse])
async def get_categories(db: Session = Depends(get_db)):
    """
    Get all available categories with job counts
    Public endpoint for frontend dropdown
    """
    try:
        categories = db.query(
            Category.id,
            Category.name,
            func.count(Job.id).label('jobs_count')
        ).outerjoin(Job).group_by(Category.id).all()
        
        return [
            CategoryResponse(
                id=cat.id,
                name=cat.name,
                jobs_count=cat.jobs_count
            )
            for cat in categories
        ]
        
    except Exception as e:
        logger.error(f"Error fetching categories: {e}")
        return []


@router.get("/health", response_model=DetailedHealthResponse)
async def health_check(db: Session = Depends(get_db)):
    """
    Comprehensive health check with metrics
    """
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
        
        last_scrape_log = db.query(ScraperLog).order_by(
            ScraperLog.scraped_at.desc()
        ).first()
        last_scrape = last_scrape_log.scraped_at if last_scrape_log else None
        
        pending_notifications = db.query(Notification).filter(
            Notification.status == "pending"
        ).count()
        
        yesterday = datetime.utcnow() - timedelta(hours=24)
        total_scrapes_24h = db.query(ScraperLog).filter(
            ScraperLog.scraped_at >= yesterday
        ).count()
        
        successful_scrapes_24h = db.query(ScraperLog).filter(
            ScraperLog.scraped_at >= yesterday,
            ScraperLog.status == "success"
        ).count()
        
        success_rate = (
            successful_scrapes_24h / total_scrapes_24h 
            if total_scrapes_24h > 0 else 0.0
        )
        
        active_categories = db.query(Category).filter(
            Category.last_scraped_at.isnot(None)
        ).count()
        
        scraper_metrics = ScraperMetrics(
            last_run=last_scrape,
            success_rate_24h=round(success_rate, 2),
            categories_active=active_categories
        )
        
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        sent_today = db.query(Notification).filter(
            Notification.sent_at >= today_start,
            Notification.status == "sent"
        ).count()
        
        failed_today = db.query(Notification).filter(
            Notification.sent_at >= today_start,
            Notification.status == "failed"
        ).count()
        
        email_metrics = EmailMetrics(
            pending=pending_notifications,
            sent_today=sent_today,
            failed_today=failed_today
        )
        
        users_verified = db.query(User).filter(
            User.verified == True,
            User.unsubscribed == False
        ).count()
        
        jobs_total = db.query(Job).count()
        
        database_metrics = DatabaseMetrics(
            users_verified=users_verified,
            jobs_total=jobs_total
        )
        
        return DetailedHealthResponse(
            status="healthy",
            timestamp=datetime.utcnow(),
            database=db_status,
            last_scrape=last_scrape,
            pending_notifications=pending_notifications,
            scraper=scraper_metrics,
            email=email_metrics,
            database_stats=database_metrics
        )
        
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return DetailedHealthResponse(
            status="unhealthy",
            timestamp=datetime.utcnow(),
            database="error",
            last_scrape=None,
            pending_notifications=0,
            scraper=ScraperMetrics(
                last_run=None,
                success_rate_24h=0.0,
                categories_active=0
            ),
            email=EmailMetrics(pending=0, sent_today=0, failed_today=0),
            database_stats=DatabaseMetrics(users_verified=0, jobs_total=0)
        )

