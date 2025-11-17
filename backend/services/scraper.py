"""
Web scraper for Mostaql jobs with resilience and anti-detection
"""
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
import random
import time
from loguru import logger
from datetime import datetime

from backend.database import SessionLocal, Job, Category, ScraperLog
from backend.utils.security import hash_content
from backend.config import settings


# User agent rotation list
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0',
]


def get_random_user_agent() -> str:
    """Get a random user agent for anti-detection"""
    return random.choice(USER_AGENTS)


def get_headers() -> Dict[str, str]:
    """Generate request headers with random user agent"""
    return {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
    }


def parse_job_listing(link_element) -> Optional[Dict[str, str]]:
    """
    Parse a single job listing from a link element
    Returns dict with title, url, or None if parsing fails
    
    Based on actual Mostaql HTML structure from https://mostaql.com/projects
    """
    try:
        # The link element IS the <a> tag with href to project
        if not link_element or link_element.name != 'a':
            return None
        
        url = link_element.get('href', '')
        if not url:
            return None
        
        # Check if it's a project link (format: /project/ID-slug or full URL)
        if '/project/' not in url:
            return None
        
        # Get full URL
        if not url.startswith('http'):
            url = f"https://mostaql.com{url}"
        
        # Get title text
        title = link_element.get_text(strip=True)
        if not title:
            return None
        
        return {
            'title': title,
            'url': url,
        }
        
    except Exception as e:
        logger.debug(f"Error parsing job link: {e}")
        return None


def quick_check_category(category_id: int, category_url: str) -> Optional[Dict[str, str]]:
    """
    Quick check: Get the first job to see if anything changed.
    Returns dict with 'title' and 'url' of first job, or None if no jobs found.
    This is much faster than full scrape.
    """
    try:
        headers = get_headers()
        response = requests.get(
            category_url,
            headers=headers,
            timeout=settings.http_request_timeout,
            allow_redirects=True
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        tbody = soup.find('tbody', attrs={'data-filter': 'collection'})
        
        if not tbody:
            return None
        
        project_rows = tbody.find_all('tr', class_='project-row', limit=1)
        
        if not project_rows:
            return None
        
        # Get first job only
        first_row = project_rows[0]
        title_link = first_row.find('h2').find('a') if first_row.find('h2') else None
        
        if not title_link:
            return None
        
        title = title_link.get_text(strip=True)
        url = title_link.get('href', '')
        
        if not title or not url:
            return None
        
        # Make URL absolute
        if not url.startswith('http'):
            url = f"{settings.mostaql_base_url}{url}"
        
        return {
            'title': title,
            'url': url
        }
        
    except Exception as e:
        logger.debug(f"Quick check failed for category {category_id}: {e}")
        return None


def scrape_category(category_id: int, category_url: str) -> List[Dict[str, str]]:
    """
    Scrape jobs from a category URL
    Returns list of job dicts
    """
    jobs = []
    
    try:
        # Make request with timeout
        headers = get_headers()
        logger.info(f"Scraping {category_url}")
        
        response = requests.get(
            category_url,
            headers=headers,
            timeout=settings.http_request_timeout,
            allow_redirects=True
        )
        response.raise_for_status()
        
        # Parse HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find project rows in the table structure
        # <tbody data-filter="collection"> contains all projects
        tbody = soup.find('tbody', attrs={'data-filter': 'collection'})
        
        if not tbody:
            logger.warning("No tbody with data-filter='collection' found on page")
            return jobs
        
        # Find all project rows
        project_rows = tbody.find_all('tr', class_='project-row')
        logger.info(f"Found {len(project_rows)} project rows")
        
        # Parse each project row
        for row in project_rows:
            try:
                # Title and URL are in <h2><a>
                title_link = row.find('h2').find('a') if row.find('h2') else None
                
                if not title_link:
                    continue
                
                title = title_link.get_text(strip=True)
                url = title_link.get('href', '')
                
                if not title or not url:
                    continue
                
                # Make URL absolute
                if not url.startswith('http'):
                    url = f"{settings.mostaql_base_url}{url}"
                
                jobs.append({
                    'title': title,
                    'url': url
                })
                
            except Exception as e:
                logger.debug(f"Error parsing project row: {e}")
                continue
        
        logger.info(f"Successfully parsed {len(jobs)} jobs from category {category_id}")
        return jobs
        
    except requests.Timeout:
        logger.error(f"Timeout scraping category {category_id}")
        raise
    except requests.HTTPError as e:
        logger.error(f"HTTP error scraping category {category_id}: {e.response.status_code}")
        raise
    except Exception as e:
        logger.error(f"Error scraping category {category_id}: {e}")
        raise


def _job_exists_in_db(db, job_data: Dict[str, str]) -> bool:
    """
    Check if a job already exists in DB by content_hash OR url.
    Shared logic used by quick check and save_new_jobs.
    """
    content_hash = hash_content(job_data['title'])
    existing = db.query(Job).filter(
        (Job.content_hash == content_hash) | (Job.url == job_data['url'])
    ).first()
    return existing is not None


def save_new_jobs(category_id: int, jobs: List[Dict[str, str]]) -> List[Job]:
    """
    Save new jobs to database (deduplicated by content hash)
    Returns list of newly saved Job objects
    """
    db = SessionLocal()
    new_jobs = []
    
    try:
        for job_data in jobs:
            # Check if job already exists (by hash or URL)
            if _job_exists_in_db(db, job_data):
                logger.debug(f"Job already exists: {job_data['title'][:50]}")
                continue
            
            # Create new job
            content_hash = hash_content(job_data['title'])
            job = Job(
                title=job_data['title'],
                url=job_data['url'],
                content_hash=content_hash,
                category_id=category_id,
                scraped_at=datetime.utcnow()
            )
            db.add(job)
            new_jobs.append(job)
        
        db.commit()
        
        # Refresh to get IDs
        for job in new_jobs:
            db.refresh(job)
        
        logger.info(f"Saved {len(new_jobs)} new jobs for category {category_id}")
        return new_jobs
        
    except Exception as e:
        logger.error(f"Error saving jobs: {e}")
        db.rollback()
        return []
    finally:
        db.close()


def log_scrape_result(category_id: int, status: str, jobs_found: int, duration: float, error_msg: Optional[str] = None):
    """Log scraper execution to database"""
    db = SessionLocal()
    try:
        log_entry = ScraperLog(
            category_id=category_id,
            status=status,
            jobs_found=jobs_found,
            duration_seconds=duration,
            error_message=error_msg,
            scraped_at=datetime.utcnow()
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        logger.error(f"Error logging scrape result: {e}")
        db.rollback()
    finally:
        db.close()


def update_category_scrape_status(category_id: int, success: bool):
    """Update category last scraped time and failure counter"""
    db = SessionLocal()
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if category:
            category.last_scraped_at = datetime.utcnow()
            
            if success:
                category.scrape_failures = 0
            else:
                category.scrape_failures = (category.scrape_failures or 0) + 1
            
            db.commit()
    except Exception as e:
        logger.error(f"Error updating category status: {e}")
        db.rollback()
    finally:
        db.close()


def poll_category(category_id: int) -> List[Job]:
    """
    Polling function: Quick check first, then full scrape if needed.
    Returns list of new Job objects
    """
    db = SessionLocal()
    
    try:
        # Get category
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.error(f"Category {category_id} not found")
            return []
        
        # Quick check: get first job only
        first_job = quick_check_category(category_id, category.mostaql_url)
        
        if not first_job:
            # No jobs found, skip
            logger.debug(f"Category {category.name} (ID {category_id}): No jobs found in quick check")
            return []
        
        # Check if first job already exists in DB (using same logic as save_new_jobs)
        if _job_exists_in_db(db, first_job):
            # First job unchanged, skip full scrape
            logger.debug(f"Category {category.name} (ID {category_id}): First job unchanged, skipping full scrape")
            return []
        
        # First job is new, do full scrape
        logger.info(f"Category {category.name} (ID {category_id}): New job detected, doing full scrape")
        return scrape_category_with_logging(category_id)
        
    except Exception as e:
        logger.error(f"Error polling category {category_id}: {e}")
        return []
    finally:
        db.close()


def scrape_category_with_logging(category_id: int) -> List[Job]:
    """
    Main scraper function with full logging and error handling
    Returns list of new Job objects
    """
    start_time = time.time()
    db = SessionLocal()
    
    try:
        # Get category
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.error(f"Category {category_id} not found")
            return []
        
        logger.info(f"Starting full scrape for category: {category.name}")
        
        # Scrape jobs
        jobs_data = scrape_category(category_id, category.mostaql_url)
        
        # Save new jobs
        new_jobs = save_new_jobs(category_id, jobs_data)
        
        # Log success
        duration = time.time() - start_time
        log_scrape_result(category_id, "success", len(new_jobs), duration)
        update_category_scrape_status(category_id, success=True)
        
        logger.info(f"✓ Scraped {len(new_jobs)} new jobs from {category.name} in {duration:.2f}s")
        return new_jobs
        
    except requests.HTTPError as e:
        duration = time.time() - start_time
        status = "blocked" if e.response.status_code == 429 else "error"
        log_scrape_result(category_id, status, 0, duration, str(e))
        update_category_scrape_status(category_id, success=False)
        
        logger.error(f"✗ HTTP error scraping category {category_id}: {e}")
        return []
        
    except Exception as e:
        duration = time.time() - start_time
        log_scrape_result(category_id, "error", 0, duration, str(e))
        update_category_scrape_status(category_id, success=False)
        
        logger.error(f"✗ Error scraping category {category_id}: {e}")
        return []
        
    finally:
        db.close()


def scrape_all_categories() -> Dict[str, int]:
    """
    Scrape all active categories
    Returns dict with stats
    """
    db = SessionLocal()
    stats = {
        "total_categories": 0,
        "successful": 0,
        "failed": 0,
        "new_jobs": 0
    }
    
    try:
        categories = db.query(Category).all()
        stats["total_categories"] = len(categories)
        
        logger.info(f"Starting scrape for {len(categories)} categories")
        
        for category in categories:
            try:
                new_jobs = scrape_category_with_logging(category.id)
                stats["successful"] += 1
                stats["new_jobs"] += len(new_jobs)
            except Exception as e:
                stats["failed"] += 1
                logger.error(f"Failed to scrape category {category.id}: {e}")
        
        logger.info(f"Scrape complete: {stats}")
        return stats
        
    finally:
        db.close()

