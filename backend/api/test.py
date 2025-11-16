"""
Test endpoints for manual scraper triggering and debugging
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from loguru import logger
import requests
from bs4 import BeautifulSoup

from backend.scheduler import run_scraper_job
from backend.database import get_db, Category, Job
from backend.services.scraper import quick_check_category, scrape_category_with_logging


class TestEmailRequest(BaseModel):
    email: EmailStr
    subject: str
    body: str
    provider: str = "gmail"

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
        
        # Run quick check
        first_job_url = quick_check_category(category_id, category.mostaql_url, check_count=5)
        
        if not first_job_url:
            return {
                "status": "success",
                "category_id": category_id,
                "category_name": category.name,
                "first_job_url": None,
                "exists_in_db": False,
                "message": "No jobs found in quick check"
            }
        
        # Check if exists in DB
        existing_job = db.query(Job).filter(Job.url == first_job_url).first()
        
        return {
            "status": "success",
            "category_id": category_id,
            "category_name": category.name,
            "first_job_url": first_job_url,
            "exists_in_db": existing_job is not None,
            "job_id_in_db": existing_job.id if existing_job else None,
            "message": "First job unchanged, would skip full scrape" if existing_job else "New job detected, would trigger full scrape"
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
        
        # Run quick check first
        first_job_url = quick_check_category(category_id, category.mostaql_url, check_count=5)
        
        if not first_job_url:
            return {
                "status": "success",
                "category_id": category_id,
                "category_name": category.name,
                "quick_check_result": "no_jobs_found",
                "full_scrape_triggered": False,
                "new_jobs_count": 0,
                "message": "No jobs found, skipped"
            }
        
        # Check if exists in DB
        existing_job = db.query(Job).filter(Job.url == first_job_url).first()
        
        if existing_job:
            return {
                "status": "success",
                "category_id": category_id,
                "category_name": category.name,
                "quick_check_result": "unchanged",
                "first_job_url": first_job_url,
                "full_scrape_triggered": False,
                "new_jobs_count": 0,
                "message": "First job unchanged, skipped full scrape"
            }
        
        # New job detected, do full scrape
        logger.info(f"🧪 Test: New job detected for category {category.name}, triggering full scrape")
        new_jobs = scrape_category_with_logging(category_id)
        
        return {
            "status": "success",
            "category_id": category_id,
            "category_name": category.name,
            "quick_check_result": "new_job_detected",
            "first_job_url": first_job_url,
            "full_scrape_triggered": True,
            "new_jobs_count": len(new_jobs),
            "new_jobs": [
                {
                    "id": job.id,
                    "title": job.title,
                    "url": job.url
                }
                for job in new_jobs[:10]  # Limit to first 10
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
                # Quick check
                first_job_url = quick_check_category(category.id, category.mostaql_url, check_count=5)
                
                if not first_job_url:
                    results.append({
                        "category_id": category.id,
                        "category_name": category.name,
                        "action": "skipped",
                        "reason": "no_jobs_found"
                    })
                    skipped += 1
                    continue
                
                # Check if exists
                existing_job = db.query(Job).filter(Job.url == first_job_url).first()
                
                if existing_job:
                    results.append({
                        "category_id": category.id,
                        "category_name": category.name,
                        "action": "skipped",
                        "reason": "unchanged",
                        "first_job_url": first_job_url
                    })
                    skipped += 1
                else:
                    # Would trigger full scrape
                    results.append({
                        "category_id": category.id,
                        "category_name": category.name,
                        "action": "would_scrape",
                        "reason": "new_job_detected",
                        "first_job_url": first_job_url
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
        
        # Get the specified provider service
        if provider == "brevo":
            service = BrevoEmailService()
        elif provider == "gmail":
            service = GmailEmailService()
        else:
            return {
                "status": "error",
                "message": f"Unknown provider '{provider}'. Use 'gmail' or 'brevo'"
            }
        
        # Send test email using the service's internal _send_email method
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

