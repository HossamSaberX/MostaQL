"""
Job notification service.
"""
from typing import Any, List, Dict, Optional, Tuple
from html import escape
from datetime import datetime
from loguru import logger
from sqlalchemy import or_, and_

from backend.database import (
    SessionLocal,
    User,
    Job,
    Notification,
    Category,
    UserCategory,
    FollowedClient,
)
from backend.enums import NotificationChannel, NotificationStatus
from backend.services.notification_queue import EmailTask, TelegramTask, email_task_queue, telegram_task_queue
from backend.services.client_tracking import client_profile_key
from backend.services.messages import (
    count_projects,
    job_facts,
    job_reason,
    manage_button,
    order_jobs,
)

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_TITLE_LENGTH = 300


def _get_users_for_category(category_id: int, db) -> List[User]:
    return (
        db.query(User)
            .outerjoin(UserCategory, UserCategory.user_id == User.id)
            .filter(
                or_(
                    UserCategory.category_id == category_id,
                    User.followed_clients.any(),
                ),
                User.unsubscribed.is_(False),
                or_(
                    User.verified.is_(True),
                    and_(
                        User.telegram_chat_id.isnot(None),
                        User.receive_telegram.is_(True)
                    )
                )
            )
            .distinct()
            .all()
    )


def _project_age_minutes(job: Job, now: Optional[datetime] = None) -> Optional[int]:
    if job.published_at is None:
        return None
    now = now or datetime.utcnow()
    return max(0, int((now - job.published_at).total_seconds() // 60))


def _job_matches_user(user: User, job: Job, now: Optional[datetime] = None) -> bool:
    if user.min_hiring_rate is not None:
        if job.hiring_rate is None or job.hiring_rate < user.min_hiring_rate:
            return False

    if user.require_projects_in_progress:
        if job.projects_in_progress is None or job.projects_in_progress <= 0:
            return False

    if user.require_ongoing_communications:
        if job.ongoing_communications is None or job.ongoing_communications <= 0:
            return False

    if user.min_budget_usd is not None:
        if job.budget_max_usd is None or job.budget_max_usd < user.min_budget_usd:
            return False

    if user.require_verified_client:
        if not (
            job.client_identity_verified is True
            or job.client_payment_verified is True
        ):
            return False

    if user.max_project_age_minutes is not None:
        age_minutes = _project_age_minutes(job, now)
        if age_minutes is None or age_minutes > user.max_project_age_minutes:
            return False

    return True


def _filter_jobs_for_user(
    user: User,
    jobs: List[Job],
    now: Optional[datetime] = None,
) -> List[Job]:
    return [job for job in jobs if _job_matches_user(user, job, now)]


def _is_category_subscriber(user: User, category_id: int) -> bool:
    return any(
        user_category.category_id == category_id
        for user_category in getattr(user, "categories", [])
    )


def _followed_client_for_job(user: User, job: Job) -> Optional[FollowedClient]:
    job_profile_key = client_profile_key(getattr(job, "client_profile_url", None))
    if not job_profile_key:
        return None
    for followed_client in getattr(user, "followed_clients", []):
        if client_profile_key(followed_client.profile_url) == job_profile_key:
            return followed_client
    return None


def _jobs_for_user(
    user: User,
    jobs: List[Job],
    category_id: int,
) -> Tuple[List[Job], Dict[int, Optional[str]]]:
    """Match category jobs and followed-client jobs exactly once per user."""

    category_subscriber = _is_category_subscriber(user, category_id)
    matching_jobs: List[Job] = []
    match_reasons: Dict[int, Optional[str]] = {}

    for job in jobs:
        followed_client = _followed_client_for_job(user, job)
        if followed_client is not None:
            # A followed-client alert intentionally ignores category and smart
            # filters: the user's explicit request is to see every new project
            # from that client, in every category.
            matching_jobs.append(job)
            match_reasons[job.id] = followed_client.label or followed_client.profile_url
        elif category_subscriber and _job_matches_user(user, job):
            matching_jobs.append(job)
            match_reasons[job.id] = None

    return matching_jobs, match_reasons


def _format_amount(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    numeric = float(value)
    return f"{numeric:,.0f}" if numeric.is_integer() else f"{numeric:,.2f}"


def _verification_label(job: Job) -> str:
    if job.client_identity_verified is True or job.client_payment_verified is True:
        verified_parts = []
        if job.client_identity_verified is True:
            verified_parts.append("الهوية")
        if job.client_payment_verified is True:
            verified_parts.append("الدفع")
        return f"موثق ({' و'.join(verified_parts)})"
    if job.client_identity_verified is False and job.client_payment_verified is False:
        return "غير موثق"
    return "غير معروف"


def _build_job_payload(
    job: Job,
    now: Optional[datetime] = None,
    followed_client: Optional[str] = None,
    category_name: Optional[str] = None,
) -> Dict[str, Any]:
    min_budget = _format_amount(job.budget_min_usd)
    max_budget = _format_amount(job.budget_max_usd)
    budget = None
    if min_budget and max_budget:
        budget = f"${min_budget} - ${max_budget}" if min_budget != max_budget else f"${max_budget}"

    payload = {
        "title": job.title,
        "url": job.url,
        "budget": budget,
        "hiring_rate": f"{job.hiring_rate:.2f}%" if job.hiring_rate is not None else None,
        "projects_in_progress": job.projects_in_progress,
        "ongoing_communications": job.ongoing_communications,
        "verification": _verification_label(job),
        "project_age_minutes": _project_age_minutes(job, now),
    }
    if followed_client:
        payload["followed_client"] = followed_client
    if category_name:
        payload["category"] = category_name
    return payload


def _telegram_project(payload: Dict[str, Any]) -> str:
    title = escape(str(payload["title"])[:TELEGRAM_TITLE_LENGTH])
    link = escape(str(payload["url"]), quote=True)
    lines = [f'\u200F<b><a href="{link}">{title}</a></b>']
    lines += [f"{escape(label)}: {escape(value)}" for label, value in job_facts(payload)]
    reason = job_reason(payload)
    if reason:
        lines.append(f"<i>{escape(reason)}</i>")
    return "\n".join(lines)


def render_telegram_alert(jobs: List[Dict[str, Any]]) -> List[str]:
    """
    One person's alert as Telegram HTML messages, each under 4096 characters.

    Projects from past clients come first; a project is never split between messages.
    """
    ordered = order_jobs(jobs)
    if not ordered:
        return []

    messages: List[str] = []
    current = f"<b>{count_projects(len(ordered))}</b>"
    for payload in ordered:
        block = _telegram_project(payload)
        if len(current) + 2 + len(block) > TELEGRAM_MESSAGE_LIMIT:
            messages.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}"
    messages.append(current)
    return messages


def _create_notification(
    db, 
    user_id: int, 
    job_id: int, 
    channel: NotificationChannel
) -> Notification:
    notif = Notification(
        user_id=user_id,
        job_id=job_id,
        status=NotificationStatus.PENDING.value,
        channel=channel.value
    )
    db.add(notif)
    return notif


def process_new_jobs(new_jobs: List[Job], category_id: int) -> Dict[str, int]:
    if not new_jobs:
        return {"queued_emails": 0, "notifications": 0, "queued_telegram": 0}

    db = SessionLocal()
    queued_notifications = 0
    queued_emails = 0
    queued_telegram = 0
    matched_user_jobs = 0
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.warning(f"Category {category_id} not found while notifying users")
            return {"queued_emails": 0, "notifications": 0, "queued_telegram": 0}

        users = _get_users_for_category(category_id, db)
        if not users:
            logger.info(f"No active subscribers or followed clients for category {category.name}")
            return {"queued_emails": 0, "notifications": 0, "queued_telegram": 0}

        user_job_map: Dict[int, List[Job]] = {}
        user_job_reasons: Dict[int, Dict[int, Optional[str]]] = {}
        pending_notifications: List[Tuple[int, Notification]] = []
        
        for user in users:
            filtered_jobs, match_reasons = _jobs_for_user(user, new_jobs, category_id)
            if not filtered_jobs:
                continue
            matched_user_jobs += len(filtered_jobs)
            user_job_map[user.id] = filtered_jobs
            user_job_reasons[user.id] = match_reasons
            
            for job in filtered_jobs:
                if user.receive_email and user.verified:
                    pending_notifications.append((user.id, _create_notification(
                        db, user.id, job.id, NotificationChannel.EMAIL
                    )))
                if user.receive_telegram and user.telegram_chat_id:
                    pending_notifications.append((user.id, _create_notification(
                        db, user.id, job.id, NotificationChannel.TELEGRAM
                    )))

        db.flush()
        
        email_notification_rows: Dict[int, List[int]] = {}
        telegram_notification_rows: Dict[int, List[int]] = {}
        for user_id, notif in pending_notifications:
            rows = (
                email_notification_rows
                if notif.channel == NotificationChannel.EMAIL.value
                else telegram_notification_rows
            )
            rows.setdefault(user_id, []).append(notif.id)
        
        queued_notifications = len(pending_notifications)
        db.commit()

        for user in users:
            if user.id not in user_job_map:
                continue
            payloads = [
                _build_job_payload(
                    job,
                    followed_client=user_job_reasons[user.id].get(job.id),
                    category_name=category.name,
                )
                for job in user_job_map[user.id]
            ]

            telegram_ids = telegram_notification_rows.get(user.id)
            if telegram_ids:
                telegram_task_queue.enqueue(
                    TelegramTask(
                        notification_ids=telegram_ids,
                        user_ids=[user.id],
                        chat_id=user.telegram_chat_id,
                        texts=render_telegram_alert(payloads),
                        buttons=[manage_button(user.token)],
                    )
                )
                queued_telegram += 1

            email_ids = email_notification_rows.get(user.id)
            if email_ids:
                email_task_queue.enqueue(
                    EmailTask(
                        notification_ids=email_ids,
                        user_ids=[user.id],
                        email=user.email,
                        jobs=payloads,
                        unsubscribe_token=user.token,
                    )
                )
                queued_emails += 1

        logger.info(
            f"Evaluated {len(new_jobs)} new jobs for {len(users)} eligible users: "
            f"{matched_user_jobs} matching user-project pairs. "
            f"Queued {queued_emails} emails, {queued_telegram} Telegram messages "
            f"({queued_notifications} notifications) for category {category.name}"
        )
        return {"queued_emails": queued_emails, "notifications": queued_notifications, "queued_telegram": queued_telegram}

    except Exception as exc:
        db.rollback()
        logger.error(f"Error queueing notifications for category {category_id}: {exc}")
        return {"queued_emails": 0, "notifications": 0, "queued_telegram": 0}
    finally:
        db.close()


