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
)
from backend.enums import NotificationChannel, NotificationStatus
from backend.services.notification_queue import EmailTask, TelegramTask, email_task_queue, telegram_task_queue
from backend.config import settings


def _get_users_for_category(category_id: int, db) -> List[User]:
    return (
        db.query(User)
            .join(UserCategory)
            .filter(
                UserCategory.category_id == category_id,
                User.unsubscribed.is_(False),
                or_(
                    User.verified.is_(True),
                    and_(
                        User.telegram_chat_id.isnot(None),
                        User.receive_telegram.is_(True)
                    )
                )
            )
            .all()
    )


def _build_email_tasks(
    users: List[User],
    category_name: str,
    jobs: List[Job],
    notification_rows: Dict[int, List[int]],
) -> List[EmailTask]:
    job_payloads = [_build_job_payload(job) for job in jobs]
    tasks: List[EmailTask] = []
    
    active_users = [user for user in users if user.id in notification_rows]
    total_active = len(active_users)
    if total_active == 0:
        return tasks

    configured_batch = getattr(settings, "email_bcc_batch_size", 0)
    batch_size = total_active if configured_batch <= 0 else min(configured_batch, total_active)

    for start in range(0, total_active, batch_size):
        batch_users = active_users[start:start + batch_size]
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
                category_name=category_name,
                jobs=job_payloads,
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


def _build_job_payload(job: Job, now: Optional[datetime] = None) -> Dict[str, Any]:
    min_budget = _format_amount(job.budget_min_usd)
    max_budget = _format_amount(job.budget_max_usd)
    budget = None
    if min_budget and max_budget:
        budget = f"${min_budget} - ${max_budget}" if min_budget != max_budget else f"${max_budget}"

    return {
        "title": job.title,
        "url": job.url,
        "budget": budget,
        "hiring_rate": f"{job.hiring_rate:.2f}%" if job.hiring_rate is not None else None,
        "projects_in_progress": job.projects_in_progress,
        "ongoing_communications": job.ongoing_communications,
        "verification": _verification_label(job),
        "project_age_minutes": _project_age_minutes(job, now),
    }


def _telegram_job_html(payload: Dict[str, Any]) -> str:
    signal_lines = []
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
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.warning(f"Category {category_id} not found while notifying users")
            return {"queued_emails": 0, "notifications": 0, "queued_telegram": 0}

        users = _get_users_for_category(category_id, db)
        if not users:
            logger.info(f"No verified subscribers for category {category.name}")
            return {"queued_emails": 0, "notifications": 0, "queued_telegram": 0}

        user_job_map: Dict[int, List[Job]] = {}
        pending_notifications: List[Tuple[int, Notification]] = []
        
        for user in users:
            filtered_jobs = _filter_jobs_for_user(user, new_jobs)
            if not filtered_jobs:
                continue
            user_job_map[user.id] = filtered_jobs
            
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
                job_payloads = [_build_job_payload(job) for job in user_jobs]

                msg_content = "\n\n".join(
                    _telegram_job_html(payload) for payload in job_payloads
                )
                title = f"\u200Fوظائف جديدة في {category_name_escaped}"
                
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
            batch_tasks = _build_email_tasks(batch_users, category.name, batch_jobs, email_notification_rows)
            tasks.extend(batch_tasks)

        for task in tasks:
            email_task_queue.enqueue(task)

        logger.info(
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


