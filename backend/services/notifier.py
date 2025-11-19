"""
Job notification service.
"""
from typing import List, Dict
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
from backend.config import settings


def _get_users_for_category(category_id: int, db) -> List[User]:
    return (
        db.query(User)
            .join(UserCategory)
            .filter(
                UserCategory.category_id == category_id,
                User.verified.is_(True),
                User.unsubscribed.is_(False),
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
    Persist notification rows for new jobs in a category and enqueue emails.
    """
    if not new_jobs:
        return {"queued_emails": 0, "notifications": 0}

    db = SessionLocal()
    queued_notifications = 0
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.warning(f"Category {category_id} not found while notifying users")
            return {"queued_emails": 0, "notifications": 0}

        users = _get_users_for_category(category_id, db)
        if not users:
            logger.info(f"No verified subscribers for category {category.name}")
            return {"queued_emails": 0, "notifications": 0}

        notification_rows: Dict[int, List[int]] = {}
        for job in new_jobs:
            for user in users:
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

        tasks = _build_email_tasks(users, category.name, new_jobs, notification_rows)
        for task in tasks:
            email_task_queue.enqueue(task)

        logger.info(
            f"Queued {len(tasks)} emails ({queued_notifications} notifications) for category {category.name}"
        )
        return {"queued_emails": len(tasks), "notifications": queued_notifications}

    except Exception as exc:
        db.rollback()
        logger.error(f"Error queueing notifications for category {category_id}: {exc}")
        return {"queued_emails": 0, "notifications": 0}
    finally:
        db.close()


