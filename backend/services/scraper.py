import random
import re
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from backend.database import (
    SessionLocal,
    Job,
    Category,
    ScraperLog,
    User,
    UserCategory,
    ClientVerificationCache,
)
from backend.utils.security import hash_content
from backend.config import settings
from backend.services.client_tracking import normalize_client_profile_url, FollowedClientError


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
        
        url = urljoin(settings.mostaql_base_url, url)
        
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


def _parse_project_rows(tbody) -> List[Dict[str, str]]:
    jobs = []
    if not tbody:
        return jobs

    for row in tbody.find_all('tr', class_='project-row'):
        try:
            title_link = row.select_one('h2 a')
            job = parse_job_listing(title_link)
            if job:
                jobs.append(job)
        except Exception as exc:
            logger.debug(f"Error parsing project row: {exc}")
    return jobs


def _next_page_url(soup: BeautifulSoup, current_url: str) -> Optional[str]:
    """Return the site's next-page link when pagination is present."""
    selectors = (
        'a[rel="next"]',
        'a.next',
        'li.next a',
        'a[aria-label*="التالي"]',
        'a[aria-label*="Next"]',
    )
    for selector in selectors:
        link = soup.select_one(selector)
        if link and link.get('href'):
            return urljoin(current_url, link['href'])

    # Mostaql has used a translated link label in different page templates.
    for link in soup.find_all('a', href=True):
        label = ' '.join(link.get_text(' ', strip=True).split()).lower()
        if label in {'التالي', 'الصفحة التالية', 'next', 'next page'}:
            return urljoin(current_url, link['href'])

    # Some templates expose only numbered links (without rel="next"). Pick
    # the smallest page number greater than the current one.
    current_page_values = parse_qs(urlparse(current_url).query).get('page', ['1'])
    try:
        current_page = int(current_page_values[0])
    except (TypeError, ValueError):
        current_page = 1

    numbered_pages = []
    for link in soup.find_all('a', href=True):
        candidate_url = urljoin(current_url, link['href'])
        page_values = parse_qs(urlparse(candidate_url).query).get('page')
        if not page_values:
            continue
        try:
            page_number = int(page_values[0])
        except (TypeError, ValueError):
            continue
        if page_number > current_page:
            numbered_pages.append((page_number, candidate_url))
    if numbered_pages:
        return min(numbered_pages, key=lambda item: item[0])[1]
    return None


def _scrape_category_page(
    category_id: int,
    category_url: str,
) -> Tuple[List[Dict[str, str]], Optional[str]]:
    headers = get_headers()
    response = requests.get(
        category_url,
        headers=headers,
        timeout=settings.http_request_timeout,
        allow_redirects=True,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.content, 'lxml')
    tbody = soup.find('tbody', attrs={'data-filter': 'collection'})
    if not tbody:
        logger.warning(
            f"No tbody with data-filter='collection' found for category {category_id}"
        )
        return [], None

    jobs = _parse_project_rows(tbody)
    return jobs, _next_page_url(soup, category_url)


def quick_check_category(category_id: int, category_url: str) -> Optional[Dict[str, str]]:
    """Return the first visible project for the diagnostic endpoint.

    Polling no longer relies on this single row; it always scans the listing so
    projects that arrive between checks cannot be hidden behind an unchanged
    first row.
    """
    try:
        jobs, _ = _scrape_category_page(category_id, category_url)
        return jobs[0] if jobs else None
    except Exception as e:
        logger.debug(f"Quick check failed for category {category_id}: {e}")
        return None


def scrape_category(
    category_id: int,
    category_url: str,
    known_urls: Optional[set[str]] = None,
) -> List[Dict[str, str]]:
    jobs: List[Dict[str, str]] = []
    seen_urls = set()
    visited_pages = set()
    max_pages = max(1, int(getattr(settings, 'scraper_max_pages', 10)))
    current_url = category_url

    try:
        logger.info(f"Scraping {category_url} (up to {max_pages} page(s))")
        for page_number in range(1, max_pages + 1):
            if not current_url or current_url in visited_pages:
                break
            visited_pages.add(current_url)

            page_jobs, next_url = _scrape_category_page(category_id, current_url)
            logger.info(
                f"Found {len(page_jobs)} project rows on category {category_id} page {page_number}"
            )
            page_has_unknown = False
            for job in page_jobs:
                if job['url'] not in seen_urls:
                    seen_urls.add(job['url'])
                    jobs.append(job)
                if known_urls is not None and job['url'] not in known_urls:
                    page_has_unknown = True

            if not next_url or next_url in visited_pages:
                break
            # Listings are newest-first. Once a complete page is already in
            # the database, older pages cannot contain a missed new project.
            # This keeps normal polling to one or two requests while still
            # walking through every page of a burst of new projects.
            if known_urls is not None and page_jobs and not page_has_unknown:
                break
            current_url = next_url

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
    # A title is not a stable project identity: clients frequently reuse titles
    # such as "مطلوب مبرمج". The canonical Mostaql project URL is unique and
    # must be the only deduplication key used for new alerts.
    return db.query(Job.id).filter(Job.url == job_data['url']).first() is not None


def save_new_jobs(category_id: int, jobs: List[Dict[str, str]]) -> List[Job]:
    db = SessionLocal()
    new_jobs = []
    
    try:
        seen_urls = set()
        for job_data in jobs:
            if job_data['url'] in seen_urls:
                continue
            seen_urls.add(job_data['url'])
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
        
        # Always scan the listing. Checking only the first row loses projects
        # when several are published between polls or when Mostaql reorders the
        # feed while an earlier project remains at the top.
        logger.info(f"Polling all visible projects for category {category.name} (ID {category_id})")
        return scrape_category_with_logging(category_id)
        
    except Exception as e:
        logger.error(f"Error polling category {category_id}: {e}")
        return []
    finally:
        db.close()


class RateLimitError(Exception):
    pass


@dataclass(frozen=True)
class ProjectDetails:
    hiring_rate: Optional[float] = None
    budget_min_usd: Optional[float] = None
    budget_max_usd: Optional[float] = None
    published_at: Optional[datetime] = None
    projects_in_progress: Optional[int] = None
    ongoing_communications: Optional[int] = None
    client_profile_url: Optional[str] = None
    client_identity_verified: Optional[bool] = None
    client_payment_verified: Optional[bool] = None


@dataclass(frozen=True)
class VerificationDetails:
    identity_verified: Optional[bool] = None
    payment_verified: Optional[bool] = None


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _normalize_text(value: str) -> str:
    return " ".join(value.translate(_ARABIC_DIGITS).replace("\u200f", "").split())


def _parse_decimal(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    normalized = _normalize_text(value).replace("٬", "").replace(",", "").replace("٫", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    return float(match.group(0)) if match else None


def _parse_integer(value: Optional[str]) -> Optional[int]:
    number = _parse_decimal(value)
    return int(number) if number is not None else None


def _extract_table_value(root, label: str) -> Optional[str]:
    if not root:
        return None
    normalized_label = _normalize_text(label)
    for row in root.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue
        if normalized_label in _normalize_text(cells[0].get_text(" ", strip=True)):
            return cells[1].get_text(" ", strip=True)
    return None


def _extract_meta_value(soup: BeautifulSoup, label: str) -> Optional[str]:
    normalized_label = _normalize_text(label)
    for row in soup.select(".meta-row"):
        label_element = row.select_one(".meta-label")
        value_element = row.select_one(".meta-value")
        if not label_element or not value_element:
            continue
        if normalized_label == _normalize_text(label_element.get_text(" ", strip=True)):
            return value_element.get_text(" ", strip=True)
    return None


def _parse_budget(value: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    if not value:
        return None, None
    normalized = _normalize_text(value).replace("٬", "").replace(",", "").replace("٫", ".")
    amounts = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", normalized)]
    if not amounts:
        return None, None
    if len(amounts) == 1:
        return amounts[0], amounts[0]
    return min(amounts[0], amounts[-1]), max(amounts[0], amounts[-1])


def _parse_relative_time(value: Optional[str], now: datetime) -> Optional[datetime]:
    if not value:
        return None
    normalized = _normalize_text(value)
    if "لحظ" in normalized or "الآن" in normalized:
        return now

    match = re.search(r"منذ\s+(?:(\d+)\s*)?([^\s]+)", normalized)
    if not match:
        return None
    unit = match.group(2)
    dual_units = {
        "دقيقتين",
        "ساعتين",
        "يومين",
        "أسبوعين",
        "اسبوعين",
        "شهرين",
        "سنتين",
        "عامين",
    }
    count = int(match.group(1)) if match.group(1) else (2 if unit in dual_units else 1)

    if "دقيق" in unit:
        delta = timedelta(minutes=count)
    elif "ساع" in unit:
        delta = timedelta(hours=count)
    elif "يوم" in unit or "أيام" in unit:
        delta = timedelta(days=count)
    elif "أسبوع" in unit or "اسبوع" in unit:
        delta = timedelta(weeks=count)
    elif "شهر" in unit or "أشهر" in unit:
        delta = timedelta(days=30 * count)
    elif "سن" in unit or "عام" in unit:
        delta = timedelta(days=365 * count)
    else:
        return None
    return now - delta


def _parse_published_at(soup: BeautifulSoup, now: datetime) -> Optional[datetime]:
    time_element = soup.select_one('time[itemprop="datePublished"]')
    if time_element:
        raw_datetime = time_element.get("datetime") or time_element.get("title")
        if raw_datetime:
            try:
                parsed = datetime.fromisoformat(raw_datetime.strip())
                if parsed.tzinfo:
                    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
            except ValueError:
                pass
        relative = _parse_relative_time(time_element.get_text(" ", strip=True), now)
        if relative:
            return relative
    return _parse_relative_time(_extract_meta_value(soup, "تاريخ النشر"), now)


def _verification_value(root, label: str) -> Optional[bool]:
    normalized_label = _normalize_text(label)
    for cell in root.find_all(["td", "li"]):
        text = _normalize_text(cell.get_text(" ", strip=True))
        if normalized_label not in text:
            continue
        if "غير موثق" in text or "لم يتم التوثيق" in text:
            return False
        if cell.select_one("i.text-success.fa-check, i.fa-check.text-success"):
            return True
        return False
    return None


def parse_client_verification(html: str) -> VerificationDetails:
    soup = BeautifulSoup(html, "lxml")
    verification_panel = None
    for heading in soup.find_all(["h2", "h3", "h4", "h5"]):
        if "توثيقات" in _normalize_text(heading.get_text(" ", strip=True)):
            verification_panel = heading.find_parent(class_="panel") or heading.parent
            break
    if not verification_panel:
        return VerificationDetails()
    return VerificationDetails(
        identity_verified=_verification_value(verification_panel, "الهوية الشخصية"),
        payment_verified=_verification_value(verification_panel, "وسيلة الدفع"),
    )


def parse_project_details(html: str, now: Optional[datetime] = None) -> ProjectDetails:
    now = now or datetime.utcnow()
    soup = BeautifulSoup(html, "lxml")
    widget = soup.find("div", attrs={"data-type": "employer_widget"})

    hiring_rate_text = _extract_table_value(widget, "معدل التوظيف")
    hiring_rate = (
        _parse_decimal(hiring_rate_text)
        if hiring_rate_text and re.search(r"[%٪％]", hiring_rate_text)
        else None
    )
    budget_min, budget_max = _parse_budget(_extract_meta_value(soup, "الميزانية"))

    profile_url = None
    identity_verified = None
    payment_verified = None
    if widget:
        profile_link = widget.select_one('a[href*="/u/"]')
        if profile_link and profile_link.get("href"):
            try:
                profile_url = normalize_client_profile_url(
                    urljoin(settings.mostaql_base_url, profile_link["href"])
                )
            except FollowedClientError:
                logger.debug("Ignoring an invalid client profile link in project details")
        if widget.select_one('[title*="هوية موثقة"], [alt*="هوية موثقة"]'):
            identity_verified = True
        identity_verified = _verification_value(widget, "الهوية الشخصية") if identity_verified is None else True
        payment_verified = _verification_value(widget, "وسيلة الدفع")

    return ProjectDetails(
        hiring_rate=hiring_rate,
        budget_min_usd=budget_min,
        budget_max_usd=budget_max,
        published_at=_parse_published_at(soup, now),
        projects_in_progress=_parse_integer(_extract_table_value(widget, "مشاريع قيد التنفيذ")),
        ongoing_communications=_parse_integer(_extract_table_value(widget, "التواصلات الجارية")),
        client_profile_url=profile_url,
        client_identity_verified=identity_verified,
        client_payment_verified=payment_verified,
    )


def _get_page_content(url: str) -> Optional[str]:
    response = requests.get(
        url,
        headers=get_headers(),
        timeout=settings.http_request_timeout,
        allow_redirects=True,
    )
    if response.status_code == 429:
        raise RateLimitError(f"Rate limited (429) for {url}")
    if response.status_code != 200:
        return None
    response.encoding = response.apparent_encoding or response.encoding or "utf-8"
    return response.text


def extract_project_details(job_url: str) -> ProjectDetails:
    try:
        content = _get_page_content(job_url)
        return parse_project_details(content) if content else ProjectDetails()
    except RateLimitError:
        raise
    except Exception as exc:
        logger.warning(f"Failed to extract project details for {job_url}: {exc}")
        return ProjectDetails()


def extract_hiring_rate(job_url: str) -> Optional[float]:
    """Backward-compatible wrapper around the project-details parser."""
    return extract_project_details(job_url).hiring_rate


def extract_client_verification(profile_url: str) -> Optional[VerificationDetails]:
    content = _get_page_content(profile_url)
    return parse_client_verification(content) if content else None


def _fetch_client_verification_with_backoff(
    profile_url: str,
    attempts: int = 3,
) -> Optional[VerificationDetails]:
    for attempt in range(attempts):
        try:
            return extract_client_verification(profile_url)
        except RateLimitError:
            if attempt == attempts - 1:
                raise
            backoff = 2 ** (attempt + 1)
            logger.warning(
                f"Rate limited for client profile, waiting {backoff}s "
                f"(attempt {attempt + 1}/{attempts})"
            )
            time.sleep(backoff)
        except Exception as exc:
            if attempt == attempts - 1:
                logger.warning(f"Failed to fetch client verification for {profile_url}: {exc}")
                return None
            time.sleep(2 ** attempt)
    return None


def _cache_is_fresh(checked_at: Optional[datetime], now: datetime) -> bool:
    if not checked_at:
        return False
    return checked_at >= now - timedelta(hours=settings.client_verification_cache_hours)


def _get_cached_client_verification(
    db,
    profile_url: str,
    now: datetime,
    fetcher=None,
) -> Tuple[Optional[bool], Optional[bool], Optional[datetime]]:
    cache = (
        db.query(ClientVerificationCache)
        .filter(ClientVerificationCache.profile_url == profile_url)
        .first()
    )
    if cache and _cache_is_fresh(cache.checked_at, now):
        return cache.identity_verified, cache.payment_verified, cache.checked_at

    verification = (fetcher or _fetch_client_verification_with_backoff)(profile_url)
    if verification is None:
        return None, None, None

    if cache is None:
        cache = ClientVerificationCache(profile_url=profile_url)
        db.add(cache)
    cache.identity_verified = verification.identity_verified
    cache.payment_verified = verification.payment_verified
    cache.checked_at = now
    db.flush()
    return cache.identity_verified, cache.payment_verified, cache.checked_at


def _categories_requiring_verification(db, jobs: List[Job]) -> set[int]:
    category_ids = {job.category_id for job in jobs}
    if not category_ids:
        return set()
    rows = (
        db.query(UserCategory.category_id)
        .join(User, User.id == UserCategory.user_id)
        .filter(
            UserCategory.category_id.in_(category_ids),
            User.unsubscribed.is_(False),
            User.require_verified_client.is_(True),
        )
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def enrich_jobs_with_project_details(
    job_ids: List[int],
    max_workers: int = settings.scraper_max_workers,
    rate_limit_delay: float = settings.scraper_rate_limit_delay,
) -> None:
    if not job_ids:
        return

    db = SessionLocal()
    try:
        jobs = db.query(Job).filter(Job.id.in_(job_ids)).all()
        job_urls = [(job.id, job.url) for job in jobs]
        verification_categories = _categories_requiring_verification(db, jobs)

        request_lock = time.time()
        lock = threading.Lock()

        def fetch_with_backoff(job_id: int, url: str, attempts: int = 3) -> Tuple[int, ProjectDetails]:
            nonlocal request_lock
            for attempt in range(attempts):
                try:
                    with lock:
                        now_monotonic = time.time()
                        wait_time = rate_limit_delay - (now_monotonic - request_lock)
                        request_lock = now_monotonic + max(wait_time, 0)
                    if wait_time > 0:
                        time.sleep(wait_time)
                    return job_id, extract_project_details(url)
                except RateLimitError:
                    if attempt < attempts - 1:
                        backoff = 2 ** (attempt + 1)
                        logger.warning(
                            f"Rate limited for job {job_id}, waiting {backoff}s "
                            f"(attempt {attempt + 1}/{attempts})"
                        )
                        time.sleep(backoff)
                    else:
                        logger.error(f"Rate limit exceeded for job {job_id} after {attempts} attempts")
                except Exception as exc:
                    if attempt < attempts - 1:
                        backoff = 2 ** attempt
                        logger.debug(
                            f"Attempt {attempt + 1}/{attempts} failed for job {job_id}, "
                            f"retrying in {backoff}s: {exc}"
                        )
                        time.sleep(backoff)
                    else:
                        logger.warning(f"Failed to enrich job {job_id} after {attempts} attempts: {exc}")
            return job_id, ProjectDetails()

        results: Dict[int, ProjectDetails] = {}
        started_at = time.time()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch_with_backoff, job_id, url): job_id
                for job_id, url in job_urls
            }
            for future in as_completed(futures):
                job_id = futures[future]
                try:
                    result_job_id, details = future.result()
                    results[result_job_id] = details
                except Exception as exc:
                    logger.warning(f"Unexpected enrichment error for job {job_id}: {exc}")
                    results[job_id] = ProjectDetails()

        enriched_count = 0
        verification_by_url: Dict[str, Tuple[Optional[bool], Optional[bool], Optional[datetime]]] = {}
        for job in jobs:
            details = results.get(job.id, ProjectDetails())
            job.hiring_rate = details.hiring_rate
            job.budget_min_usd = details.budget_min_usd
            job.budget_max_usd = details.budget_max_usd
            job.published_at = details.published_at
            job.projects_in_progress = details.projects_in_progress
            job.ongoing_communications = details.ongoing_communications
            job.client_profile_url = details.client_profile_url
            job.client_identity_verified = details.client_identity_verified
            job.client_payment_verified = details.client_payment_verified

            if (
                job.category_id in verification_categories
                and details.client_profile_url
                and not (
                    details.client_identity_verified is True
                    or details.client_payment_verified is True
                )
            ):
                if details.client_profile_url not in verification_by_url:
                    try:
                        verification_by_url[details.client_profile_url] = _get_cached_client_verification(
                            db,
                            details.client_profile_url,
                            datetime.utcnow(),
                        )
                    except RateLimitError:
                        logger.warning(f"Rate limited while checking client profile {details.client_profile_url}")
                        verification_by_url[details.client_profile_url] = (None, None, None)
                    except Exception as exc:
                        logger.warning(f"Failed to verify client profile {details.client_profile_url}: {exc}")
                        verification_by_url[details.client_profile_url] = (None, None, None)
                identity, payment, checked_at = verification_by_url[details.client_profile_url]
                job.client_identity_verified = identity
                job.client_payment_verified = payment
                job.client_verification_checked_at = checked_at

            if any(
                value is not None
                for value in (
                    details.hiring_rate,
                    details.budget_max_usd,
                    details.published_at,
                    details.projects_in_progress,
                    details.ongoing_communications,
                )
            ):
                enriched_count += 1

        db.commit()
        duration = time.time() - started_at
        jobs_per_sec = len(job_ids) / duration if duration > 0 else 0
        logger.info(
            f"✓ Enriched {enriched_count}/{len(job_ids)} jobs with smart alert details "
            f"in {duration:.2f}s ({jobs_per_sec:.1f} jobs/s)"
        )
    except Exception as exc:
        logger.error(f"Error enriching jobs: {exc}")
        db.rollback()
    finally:
        db.close()


def enrich_jobs_with_hiring_rates(
    job_ids: List[int],
    max_workers: int = settings.scraper_max_workers,
    rate_limit_delay: float = settings.scraper_rate_limit_delay,
) -> None:
    """Backward-compatible alias for callers that still use the old name."""
    enrich_jobs_with_project_details(job_ids, max_workers, rate_limit_delay)


def scrape_category_with_logging(category_id: int) -> List[Job]:
    start_time = time.time()
    db = SessionLocal()
    
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.error(f"Category {category_id} not found")
            return []
        
        logger.info(f"Starting full scrape for category: {category.name}")
        
        known_urls = {
            row[0]
            for row in db.query(Job.url)
            .filter(Job.category_id == category_id)
            .all()
        }
        jobs_data = scrape_category(
            category_id,
            category.mostaql_url,
            known_urls=known_urls,
        )
        
        new_jobs = save_new_jobs(category_id, jobs_data)
        
        if new_jobs:
            try:
                job_ids = [j.id for j in new_jobs]
                enrich_jobs_with_project_details(job_ids)
                
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
