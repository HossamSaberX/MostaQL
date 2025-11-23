"""
Job notification service.
"""
from typing import List, Dict, Tuple
from html import escape
from datetime import datetime
from loguru import logger

from backend.database import (
    SessionLocal,
    User,
    Job,
    Notification,
    Category,
    UserCategory,
)
from backend.services.notification_queue import EmailTask, email_task_queue
from backend.services.channels import TelegramChannel
from backend.config import settings


def _get_users_for_category(category_id: int, db) -> List[User]:
    return (
        db.query(User)
            .join(UserCategory)
            .filter(
                UserCategory.category_id == category_id,
                User.unsubscribed.is_(False),
                User.verified.is_(True),
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
        for user in batch_users:
            batch_notification_ids.extend(notification_rows.get(user.id, []))
            
        tasks.append(
            EmailTask(
                notification_ids=batch_notification_ids,
                email="undisclosed-recipients:;",
                category_name=category_name,
                jobs=job_payloads,
                unsubscribe_token=None,
                bcc=bcc_emails,
            )
        )
        
    return tasks


def process_new_jobs(new_jobs: List[Job], category_id: int) -> Dict[str, int]:
    """
    Persist notification rows for new jobs in a category and dispatch via notification channels.
    """
    if not new_jobs:
        return {"queued_emails": 0, "notifications": 0, "sent_telegram": 0}

    db = SessionLocal()
    queued_notifications = 0
    sent_telegram = 0
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.warning(f"Category {category_id} not found while notifying users")
            return {"queued_emails": 0, "notifications": 0, "sent_telegram": 0}

        users = _get_users_for_category(category_id, db)
        if not users:
            logger.info(f"No verified subscribers for category {category.name}")
            return {"queued_emails": 0, "notifications": 0, "sent_telegram": 0}

        # 1. Filter jobs per user and create notifications
        user_job_map: Dict[int, List[Job]] = {}
        notification_rows: Dict[int, List[int]] = {}
        
        for user in users:
            filtered_jobs = []
            for job in new_jobs:
                # Filter by hiring rate
                if user.min_hiring_rate is not None:
                    if job.hiring_rate is None or job.hiring_rate < user.min_hiring_rate:
                        continue
                filtered_jobs.append(job)
            
            if not filtered_jobs:
                continue
                
            user_job_map[user.id] = filtered_jobs
            
            for job in filtered_jobs:
                notification = Notification(
                    user_id=user.id,
                    job_id=job.id,
                    status="pending",
                )
                db.add(notification)
                db.flush()
                notification_rows.setdefault(user.id, []).append(notification.id)
                queued_notifications += 1

        db.commit()

        # 2. Send Telegram
        telegram_channel = TelegramChannel()
        
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
                title = f"\u200Fوظائف جديدة في {escape(category.name)}"
                
                success = telegram_channel.send(user.telegram_chat_id, title, msg_content)
                if success:
                    sent_telegram += 1
                    if not user.receive_email:
                        user_notification_ids = notification_rows.get(user.id, [])
                        if user_notification_ids:
                            db.query(Notification).filter(
                                Notification.id.in_(user_notification_ids)
                            ).update(
                                {
                                    "status": "sent", 
                                    "sent_at": datetime.utcnow()
                                },
                                synchronize_session=False
                            )
                            db.commit()

        # 3. Send Emails (Grouped by job set for efficient BCC)
        tasks = []
        job_set_users: Dict[Tuple[int, ...], List[User]] = {}
        
        for user in users:
            if user.id not in user_job_map:
                continue
            if not user.receive_email:
                continue
                
            job_ids = tuple(sorted(j.id for j in user_job_map[user.id]))
            job_set_users.setdefault(job_ids, []).append(user)
            
        job_map = {j.id: j for j in new_jobs}
        
        for job_ids, batch_users in job_set_users.items():
            batch_jobs = [job_map[jid] for jid in job_ids]
            batch_tasks = _build_email_tasks(batch_users, category.name, batch_jobs, notification_rows)
            tasks.extend(batch_tasks)

        for task in tasks:
            email_task_queue.enqueue(task)

        logger.info(
            f"Queued {len(tasks)} emails, sent {sent_telegram} Telegram messages "
            f"({queued_notifications} notifications) for category {category.name}"
        )
        return {"queued_emails": len(tasks), "notifications": queued_notifications, "sent_telegram": sent_telegram}

    except Exception as exc:
        db.rollback()
        logger.error(f"Error queueing notifications for category {category_id}: {exc}")
        return {"queued_emails": 0, "notifications": 0, "sent_telegram": 0}
    finally:
        db.close()


