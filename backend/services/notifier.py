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
from backend.config import settings


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


def _build_email_tasks(
    users: List[User],
    category_name: str,
    jobs: List[Job],
    notification_rows: Dict[int, List[int]],
    payloads_by_user: Optional[Dict[int, List[Dict[str, Any]]]] = None,
    category_names_by_user: Optional[Dict[int, str]] = None,
) -> List[EmailTask]:
    common_payloads = [_build_job_payload(job) for job in jobs]
    tasks: List[EmailTask] = []
    
    active_users = [user for user in users if user.id in notification_rows]
    total_active = len(active_users)
    if total_active == 0:
        return tasks

    # A followed-client match can add a per-user explanation to the payload.
    # Group only users with identical payloads so BCC delivery never exposes a
    # different client's label to another recipient.
    groups: Dict[Tuple[str, Tuple[Tuple[str, Optional[str]], ...]], List[User]] = {}
    for user in active_users:
        user_payloads = (payloads_by_user or {}).get(user.id, common_payloads)
        user_category_name = (category_names_by_user or {}).get(user.id, category_name)
        signature = tuple(
            (str(payload.get("url", "")), payload.get("followed_client"))
            for payload in user_payloads
        )
        groups.setdefault((user_category_name, signature), []).append(user)

    configured_batch = getattr(settings, "email_bcc_batch_size", 0)
    for (user_category_name, _signature), grouped_users in groups.items():
        batch_size = len(grouped_users) if configured_batch <= 0 else min(configured_batch, len(grouped_users))
        user_payloads = (payloads_by_user or {}).get(grouped_users[0].id, common_payloads)

        for start in range(0, len(grouped_users), batch_size):
            batch_users = grouped_users[start:start + batch_size]
            bcc_emails = [user.email for user in batch_users]
            batch_notification_ids = []
            batch_user_ids = [user.id for user in batch_users]
            for user in batch_users:
                batch_notification_ids.extend(notification_rows.get(user.id, []))

            tasks.append(
                EmailTask(
                    notification_ids=batch_notification_ids,
                    user_ids=batch_user_ids,
                    email="undisclosed-recipients:;",
                    category_name=user_category_name,
                    jobs=user_payloads,
                    unsubscribe_token=None,
                    bcc=bcc_emails,
                )
            )
        
    return tasks


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
    return payload


def _telegram_job_html(payload: Dict[str, Any]) -> str:
    signal_lines = []
    if payload.get("followed_client"):
        signal_lines.append(
            f"تنبيه عميل تتابعه: {escape(str(payload['followed_client']))}"
        )
    if payload.get("budget"):
        signal_lines.append(f"الميزانية: {escape(str(payload['budget']))}")
    if payload.get("hiring_rate"):
        signal_lines.append(f"معدل التوظيف: {escape(str(payload['hiring_rate']))}")
    if payload.get("projects_in_progress") is not None:
        signal_lines.append(f"مشاريع قيد التنفيذ: {payload['projects_in_progress']}")
    if payload.get("ongoing_communications") is not None:
        signal_lines.append(f"التواصلات الجارية: {payload['ongoing_communications']}")
    signal_lines.append(f"التوثيق: {escape(str(payload['verification']))}")
    if payload.get("project_age_minutes") is not None:
        signal_lines.append(f"عمر المشروع: {payload['project_age_minutes']} دقيقة")

    signals = "\n".join(f"\u200F  {line}" for line in signal_lines)
    link = escape(str(payload["url"]), quote=True)
    title = escape(str(payload["title"]))
    return f"\u200F• <b>{title}</b>\n{signals}\n\u200F<a href=\"{link}\">راجع المشروع وتقدّم الآن</a>"


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
            if notif.channel == NotificationChannel.EMAIL.value:
                email_notification_rows.setdefault(user_id, []).append(notif.id)
            else:
                telegram_notification_rows.setdefault(user_id, []).append(notif.id)
        
        queued_notifications = len(pending_notifications)
        db.commit()

        category_name_escaped = escape(category.name)
        for user in users:
            if user.id not in user_job_map:
                continue
            
            if user.receive_telegram and user.telegram_chat_id:
                user_jobs = user_job_map[user.id]
                reasons = user_job_reasons.get(user.id, {})
                job_payloads = [
                    _build_job_payload(job, followed_client=reasons.get(job.id))
                    for job in user_jobs
                ]

                msg_content = "\n\n".join(
                    _telegram_job_html(payload) for payload in job_payloads
                )
                has_followed_client = any(
                    reasons.get(job.id) for job in user_jobs
                )
                title = (
                    "\u200Fمشاريع جديدة من عميل تتابعه"
                    if has_followed_client
                    else f"\u200Fوظائف جديدة في {category_name_escaped}"
                )
                
                user_notification_ids = telegram_notification_rows.get(user.id, [])
                if user_notification_ids:
                    telegram_task_queue.enqueue(
                        TelegramTask(
                            notification_ids=user_notification_ids,
                            user_ids=[user.id],
                            chat_id=user.telegram_chat_id,
                            title=title,
                            content=msg_content,
                        )
                    )
                    queued_telegram += 1

        tasks = []
        job_set_users: Dict[Tuple[int, ...], List[User]] = {}
        
        for user in users:
            if user.id not in user_job_map:
                continue
            if not user.receive_email:
                continue
            if not user.verified:
                continue
                
            job_ids = tuple(sorted(j.id for j in user_job_map[user.id]))
            job_set_users.setdefault(job_ids, []).append(user)
            
        job_map = {j.id: j for j in new_jobs}
        
        for job_ids, batch_users in job_set_users.items():
            batch_jobs = [job_map[jid] for jid in job_ids]
            payloads_by_user = {
                user.id: [
                    _build_job_payload(
                        job,
                        followed_client=user_job_reasons.get(user.id, {}).get(job.id),
                    )
                    for job in user_job_map[user.id]
                ]
                for user in batch_users
            }
            category_names_by_user = {
                user.id: (
                    f"{category.name} — عميل تتابعه"
                    if any(user_job_reasons.get(user.id, {}).values())
                    else category.name
                )
                for user in batch_users
            }
            batch_tasks = _build_email_tasks(
                batch_users,
                category.name,
                batch_jobs,
                email_notification_rows,
                payloads_by_user=payloads_by_user,
                category_names_by_user=category_names_by_user,
            )
            tasks.extend(batch_tasks)

        for task in tasks:
            email_task_queue.enqueue(task)

        logger.info(
            f"Evaluated {len(new_jobs)} new jobs for {len(users)} eligible users: "
            f"{matched_user_jobs} matching user-project pairs. "
            f"Queued {len(tasks)} emails, {queued_telegram} Telegram messages "
            f"({queued_notifications} notifications) for category {category.name}"
        )
        return {"queued_emails": len(tasks), "notifications": queued_notifications, "queued_telegram": queued_telegram}

    except Exception as exc:
        db.rollback()
        logger.error(f"Error queueing notifications for category {category_id}: {exc}")
        return {"queued_emails": 0, "notifications": 0, "queued_telegram": 0}
    finally:
        db.close()


