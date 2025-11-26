"""
Job notification service.
"""
from typing import List, Dict, Tuple
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
    job_payloads = [{"title": job.title, "url": job.url} for job in jobs]
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


def _filter_jobs_for_user(user: User, jobs: List[Job]) -> List[Job]:
    if user.min_hiring_rate is None:
        return jobs
    
    filtered = []
    for job in jobs:
        if job.hiring_rate is not None and job.hiring_rate >= user.min_hiring_rate:
            filtered.append(job)
    return filtered


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
                job_payloads = [{"title": job.title, "url": job.url} for job in user_jobs]
                
                msg_content = "\n".join([
                    f"\u200F• <a href=\"{escape(j['url'])}\">{escape(j['title'])}</a>" 
                    for j in job_payloads
                ])
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


