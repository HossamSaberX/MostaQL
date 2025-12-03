import random
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from bs4 import BeautifulSoup
from loguru import logger

from backend.database import SessionLocal, Job, Category, ScraperLog
from backend.utils.security import hash_content
from backend.config import settings


USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0',
]


def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def get_headers() -> Dict[str, str]:
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
    try:
        if not link_element or link_element.name != 'a':
            return None
        
        url = link_element.get('href', '')
        if not url:
            return None
        
        if '/project/' not in url:
            return None
        
        if not url.startswith('http'):
            url = f"https://mostaql.com{url}"
        
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
    try:
        headers = get_headers()
        response = requests.get(
            category_url,
            headers=headers,
            timeout=settings.http_request_timeout,
            allow_redirects=True
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'lxml')
        tbody = soup.find('tbody', attrs={'data-filter': 'collection'})
        
        if not tbody:
            return None
        
        project_rows = tbody.find_all('tr', class_='project-row', limit=1)
        
        if not project_rows:
            return None
        
        first_row = project_rows[0]
        title_link = first_row.find('h2').find('a') if first_row.find('h2') else None
        
        if not title_link:
            return None
        
        title = title_link.get_text(strip=True)
        url = title_link.get('href', '')
        
        if not title or not url:
            return None
        
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
    jobs = []
    
    try:
        headers = get_headers()
        logger.info(f"Scraping {category_url}")
        
        response = requests.get(
            category_url,
            headers=headers,
            timeout=settings.http_request_timeout,
            allow_redirects=True
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'lxml')
        
        tbody = soup.find('tbody', attrs={'data-filter': 'collection'})
        
        if not tbody:
            logger.warning("No tbody with data-filter='collection' found on page")
            return jobs
        
        project_rows = tbody.find_all('tr', class_='project-row')
        logger.info(f"Found {len(project_rows)} project rows")
        
        for row in project_rows:
            try:
                title_link = row.find('h2').find('a') if row.find('h2') else None
                
                if not title_link:
                    continue
                
                title = title_link.get_text(strip=True)
                url = title_link.get('href', '')
                
                if not title or not url:
                    continue
                
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
    content_hash = hash_content(job_data['title'])
    existing = db.query(Job).filter(
        (Job.content_hash == content_hash) | (Job.url == job_data['url'])
    ).first()
    return existing is not None


def save_new_jobs(category_id: int, jobs: List[Dict[str, str]]) -> List[Job]:
    db = SessionLocal()
    new_jobs = []
    
    try:
        for job_data in jobs:
            if _job_exists_in_db(db, job_data):
                logger.debug(f"Job already exists: {job_data['title'][:50]}")
                continue
            
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
    db = SessionLocal()
    
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.error(f"Category {category_id} not found")
            return []
        
        first_job = quick_check_category(category_id, category.mostaql_url)
        
        if not first_job:
            logger.debug(f"Category {category.name} (ID {category_id}): No jobs found in quick check")
            return []
        
        if _job_exists_in_db(db, first_job):
            logger.debug(f"Category {category.name} (ID {category_id}): First job unchanged, skipping full scrape")
            return []
        
        logger.info(f"Category {category.name} (ID {category_id}): New job detected, doing full scrape")
        return scrape_category_with_logging(category_id)
        
    except Exception as e:
        logger.error(f"Error polling category {category_id}: {e}")
        return []
    finally:
        db.close()


class RateLimitError(Exception):
    pass


def extract_hiring_rate(job_url: str) -> Optional[float]:
    try:
        headers = get_headers()
        response = requests.get(
            job_url,
            headers=headers,
            timeout=settings.http_request_timeout
        )
        
        if response.status_code == 429:
            raise RateLimitError(f"Rate limited (429) for {job_url}")
        
        if response.status_code != 200:
            return None
            
        soup = BeautifulSoup(response.content, 'lxml')
        
        widget = soup.find('div', attrs={'data-type': 'employer_widget'})
        if not widget:
            return None
            
        target_row = None
        for row in widget.find_all('tr'):
            if "معدل التوظيف" in row.get_text():
                target_row = row
                break
        
        if not target_row:
            return None
            
        cells = target_row.find_all('td')
        if len(cells) < 2:
            return None
            
        rate_text = cells[1].get_text(strip=True)
        
        if "%" in rate_text:
            return float(rate_text.replace('%', ''))
        
        return None
        
    except RateLimitError:
        raise
    except Exception as e:
        logger.warning(f"Failed to extract hiring rate for {job_url}: {e}")
        return None


def enrich_jobs_with_hiring_rates(
    job_ids: List[int], 
    max_workers: int = settings.scraper_max_workers, 
    rate_limit_delay: float = settings.scraper_rate_limit_delay
) -> None:
    if not job_ids:
        return

    db = SessionLocal()
    try:
        jobs = db.query(Job).filter(Job.id.in_(job_ids)).all()
        job_urls = [(job.id, job.url) for job in jobs]
        
        request_lock = time.time()
        lock = threading.Lock()
        
        def fetch_rate_with_backoff(job_id: int, url: str, attempts: int = 3) -> Tuple[int, Optional[float]]:
            nonlocal request_lock
            
            for attempt in range(attempts):
                try:
                    wait_time = 0
                    with lock:
                        now = time.time()
                        wait_time = rate_limit_delay - (now - request_lock)
                        if wait_time > 0:
                            request_lock = now + wait_time
                        else:
                            request_lock = now
                    
                    if wait_time > 0:
                        time.sleep(wait_time)
                    
                    rate = extract_hiring_rate(url)
                    return (job_id, rate)
                    
                except RateLimitError:
                    if attempt < attempts - 1:
                        backoff = 2 ** (attempt + 1)
                        logger.warning(f"Rate limited for job {job_id}, waiting {backoff}s (attempt {attempt + 1}/{attempts})")
                        time.sleep(backoff)
                    else:
                        logger.error(f"Rate limit exceeded for job {job_id} after {attempts} attempts")
                        return (job_id, None)
                        
                except Exception as e:
                    if attempt < attempts - 1:
                        backoff = 2 ** attempt
                        logger.debug(f"Attempt {attempt + 1}/{attempts} failed for job {job_id}, retrying in {backoff}s: {e}")
                        time.sleep(backoff)
                    else:
                        logger.warning(f"Failed to fetch hiring rate for job {job_id} after {attempts} attempts: {e}")
                        return (job_id, None)
            
            return (job_id, None)
        
        results: Dict[int, Optional[float]] = {}
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_job = {
                executor.submit(fetch_rate_with_backoff, job_id, url): job_id 
                for job_id, url in job_urls
            }
            
            for future in as_completed(future_to_job):
                job_id = future_to_job[future]
                try:
                    job_id, rate = future.result()
                    results[job_id] = rate
                except Exception as e:
                    logger.warning(f"Unexpected error for job {job_id}: {e}")
                    results[job_id] = None
        
        success_count = 0
        for job in jobs:
            rate = results.get(job.id)
            if rate is not None:
                job.hiring_rate = rate
                success_count += 1
        
        db.commit()
        
        duration = time.time() - start_time
        jobs_per_sec = len(job_ids) / duration if duration > 0 else 0
        logger.info(f"✓ Enriched {success_count}/{len(job_ids)} jobs with hiring rates in {duration:.2f}s "
                   f"({jobs_per_sec:.1f} jobs/s)")
        
    except Exception as e:
        logger.error(f"Error enriching jobs: {e}")
        db.rollback()
    finally:
        db.close()


def scrape_category_with_logging(category_id: int) -> List[Job]:
    start_time = time.time()
    db = SessionLocal()
    
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.error(f"Category {category_id} not found")
            return []
        
        logger.info(f"Starting full scrape for category: {category.name}")
        
        jobs_data = scrape_category(category_id, category.mostaql_url)
        
        new_jobs = save_new_jobs(category_id, jobs_data)
        
        if new_jobs:
            try:
                job_ids = [j.id for j in new_jobs]
                enrich_jobs_with_hiring_rates(job_ids)
                
                db_refresh = SessionLocal()
                for i, job in enumerate(new_jobs):
                    refreshed_job = db_refresh.query(Job).filter(Job.id == job.id).first()
                    if refreshed_job:
                        new_jobs[i] = refreshed_job
                        db_refresh.expunge(refreshed_job)
                db_refresh.close()
                
            except Exception as e:
                logger.error(f"Enrichment failed: {e}")
        
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

