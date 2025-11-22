"""
Test endpoints for manual scraper triggering and debugging
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import List
from loguru import logger
import requests
from bs4 import BeautifulSoup

from backend.scheduler import run_scraper_job
from backend.database import get_db, Job, Category
from backend.services.scraper import quick_check_category, scrape_category_with_logging, _job_exists_in_db
from backend.services.notifier import process_new_jobs

router = APIRouter()


class TestEmailRequest(BaseModel):
    email: EmailStr
    subject: str
    body: str
    provider: str = "gmail"


class TestNotificationRequest(BaseModel):
    category_id: int
    jobs: List[dict]


class TestJobWithRateRequest(BaseModel):
    category_id: int
    title: str
    url: str
    hiring_rate: float = None


@router.post("/simulate-job")
async def simulate_job(data: TestJobWithRateRequest, db: Session = Depends(get_db)):
    """
    Simulate a new job with a specific hiring rate to test filtering logic.
    
    Example:
    POST /api/test/simulate-job
    {
        "category_id": 1,
        "title": "Test Job with 80% Hiring Rate",
        "url": "https://mostaql.com/project/test-80",
        "hiring_rate": 80.0
    }
    """
    category = db.query(Category).filter(Category.id == data.category_id).first()
    if not category:
        raise HTTPException(404, "Category not found")
    
    # Create fake job
    job = Job(
        title=data.title,
        url=data.url,
        content_hash=f"test_{data.url}",
        category_id=data.category_id,
        hiring_rate=data.hiring_rate
    )
    
    # Check if exists to avoid unique constraint error
    existing = db.query(Job).filter(Job.url == data.url).first()
    if existing:
        db.delete(existing)
        db.flush()
        
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Trigger notification logic
    result = process_new_jobs([job], data.category_id)
    
    return {
        "status": "success",
        "job_id": job.id,
        "hiring_rate": job.hiring_rate,
        "notification_result": result
    }


@router.post("/notification")
async def test_notification(data: TestNotificationRequest, db: Session = Depends(get_db)):
    """
    Test notification system by creating fake jobs.
    
    Example:
    POST /api/test/notification
    {
        "category_id": 1,
        "jobs": [
            {"title": "مطلوب مبرمج Python", "url": "https://mostaql.com/project/test1"},
            {"title": "تطوير موقع", "url": "https://mostaql.com/project/test2"}
        ]
    }
    """
    category = db.query(Category).filter(Category.id == data.category_id).first()
    if not category:
        raise HTTPException(404, "Category not found")
    
    test_jobs = []
    for job_data in data.jobs:
        job = Job(
            title=job_data["title"],
            url=job_data["url"],
            content_hash=f"test_{job_data['url']}",
            category_id=data.category_id
        )
        db.add(job)
        db.flush()
        test_jobs.append(job)
    
    db.commit()
    result = process_new_jobs(test_jobs, data.category_id)
    
    return {
        "status": "success",
        "category": category.name,
        "jobs_created": len(test_jobs),
        **result
    }


@router.post("/trigger-scraper")
async def trigger_scraper():
    """
    Manually trigger the scraper job (for testing)
    Runs immediately instead of waiting for scheduled interval
    """
    try:
        logger.info("🧪 Manual scraper trigger requested")
        
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
        from backend.config import settings
        url = f"{settings.mostaql_base_url}/projects"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=settings.http_request_timeout)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        tbody = soup.find('tbody', attrs={'data-filter': 'collection'})
        
        if not tbody:
            return {
                "status": "error",
                "message": "No tbody with data-filter='collection' found"
            }
        
        project_rows = tbody.find_all('tr', class_='project-row')
        samples = []
        
        for row in project_rows[:5]:
            try:
                title_link = row.find('h2').find('a') if row.find('h2') else None
                
                if title_link:
                    title = title_link.get_text(strip=True)
                    url = title_link.get('href', '')
                    full_url = url if url.startswith('http') else f"{settings.mostaql_base_url}{url}"
                    
                    samples.append({
                        'title': title,
                        'url': full_url,
                        'title_length': len(title)
                    })
            except Exception:
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


@router.get("/test-quick-check/{category_id}")
async def test_quick_check(category_id: int, db: Session = Depends(get_db)):
    """
    Test the quick check function for a specific category
    Returns the first job URL found and whether it exists in DB
    """
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            return {
                "status": "error",
                "message": f"Category {category_id} not found"
            }
        
        first_job = quick_check_category(category_id, category.mostaql_url)
        
        if not first_job:
            return {
                "status": "success",
                "category_id": category_id,
                "category_name": category.name,
                "first_job": None,
                "exists_in_db": False,
                "message": "No jobs found in quick check"
            }
        
        exists = _job_exists_in_db(db, first_job)
        
        return {
            "status": "success",
            "category_id": category_id,
            "category_name": category.name,
            "first_job": {
                "title": first_job['title'][:50],
                "url": first_job['url'][:80]
            },
            "exists_in_db": exists,
            "message": "First job unchanged, would skip full scrape" if exists else "New job detected, would trigger full scrape"
        }
        
    except Exception as e:
        logger.error(f"Quick check test failed: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/test-poll/{category_id}")
async def test_poll_category(category_id: int, db: Session = Depends(get_db)):
    """
    Test polling for a specific category
    Runs quick check + full scrape if needed, returns results
    """
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            return {
                "status": "error",
                "message": f"Category {category_id} not found"
            }
        
        first_job = quick_check_category(category_id, category.mostaql_url)
        
        if not first_job:
            return {
                "status": "success",
                "category_id": category_id,
                "category_name": category.name,
                "quick_check_result": "no_jobs_found",
                "full_scrape_triggered": False,
                "new_jobs_count": 0,
                "message": "No jobs found, skipped"
            }
        
        if _job_exists_in_db(db, first_job):
            return {
                "status": "success",
                "category_id": category_id,
                "category_name": category.name,
                "quick_check_result": "unchanged",
                "full_scrape_triggered": False,
                "new_jobs_count": 0,
                "message": "First job unchanged, skipped full scrape"
            }
        
        logger.info(f"🧪 Test: New job detected for category {category.name}, triggering full scrape")
        new_jobs = scrape_category_with_logging(category_id)
        
        return {
            "status": "success",
            "category_id": category_id,
            "category_name": category.name,
            "quick_check_result": "new_job_detected",
            "first_job": {
                "title": first_job['title'][:50],
                "url": first_job['url'][:80]
            },
            "full_scrape_triggered": True,
            "new_jobs_count": len(new_jobs),
            "new_jobs": [
                {
                    "id": job.id,
                    "title": job.title,
                    "url": job.url
                }
                for job in new_jobs[:10]
            ],
            "message": f"Found {len(new_jobs)} new jobs"
        }
        
    except Exception as e:
        logger.error(f"Poll test failed: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.get("/test-poll-all")
async def test_poll_all(db: Session = Depends(get_db)):
    """
    Test polling for all categories (like the scheduler does)
    Shows which categories would be skipped vs scraped
    """
    try:
        categories = db.query(Category).all()
        
        if not categories:
            return {
                "status": "error",
                "message": "No categories found"
            }
        
        results = []
        skipped = 0
        scraped = 0
        
        for category in categories:
            try:
                first_job = quick_check_category(category.id, category.mostaql_url)
                
                if not first_job:
                    results.append({
                        "category_id": category.id,
                        "category_name": category.name,
                        "action": "skipped",
                        "reason": "no_jobs_found"
                    })
                    skipped += 1
                    continue
                
                if _job_exists_in_db(db, first_job):
                    results.append({
                        "category_id": category.id,
                        "category_name": category.name,
                        "action": "skipped",
                        "reason": "unchanged"
                    })
                    skipped += 1
                else:
                    results.append({
                        "category_id": category.id,
                        "category_name": category.name,
                        "action": "would_scrape",
                        "reason": "new_job_detected"
                    })
                    scraped += 1
                    
            except Exception as e:
                results.append({
                    "category_id": category.id,
                    "category_name": category.name,
                    "action": "error",
                    "reason": str(e)
                })
        
        return {
            "status": "success",
            "total_categories": len(categories),
            "skipped": skipped,
            "would_scrape": scraped,
            "results": results,
            "message": f"Would skip {skipped} categories, scrape {scraped} categories"
        }
        
    except Exception as e:
        logger.error(f"Poll all test failed: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/test-send-email")
async def test_send_email(request: TestEmailRequest, db: Session = Depends(get_db)):
    """
    Test endpoint to send an email with specified provider.
    
    Request body:
        email: Recipient email address
        subject: Email subject
        body: Email body (HTML supported)
        provider: Email provider to use ("gmail" or "brevo", default: "gmail")
    """
    try:
        from backend.services.email.gmail import GmailEmailService
        from backend.services.email.brevo import BrevoEmailService
        
        provider = request.provider.lower()
        
        if provider == "brevo":
            service = BrevoEmailService()
        elif provider == "gmail":
            service = GmailEmailService()
        else:
            return {
                "status": "error",
                "message": f"Unknown provider '{provider}'. Use 'gmail' or 'brevo'"
            }
        
        success = service._send_email(
            to_email=request.email,
            subject=request.subject,
            html_body=request.body
        )
        
        if success:
            return {
                "status": "success",
                "message": f"Test email sent successfully via {provider}",
                "provider": provider,
                "recipient": request.email,
                "subject": request.subject
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to send email via {provider}. Check logs for details.",
                "provider": provider
            }
            
    except Exception as e:
        logger.error(f"Test email send failed: {e}")
        return {
            "status": "error",
            "message": str(e)
        }

