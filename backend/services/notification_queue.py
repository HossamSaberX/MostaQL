"""
Simple background email task queue for notification delivery.
"""
from __future__ import annotations

from dataclasses import dataclass
from queue import Queue
from threading import Event, Thread
from datetime import datetime
from typing import List, Dict, Optional

from backend.database import SessionLocal, Notification, User
from backend.services.email import send_job_notifications
from backend.utils.logger import app_logger


@dataclass
class EmailTask:
    notification_ids: List[int]
    user_id: int
    email: str
    category_name: str
    jobs: List[Dict[str, str]]
    unsubscribe_token: str


class EmailTaskQueue:
    """
    Extremely lightweight task queue backed by a single worker thread.
    Keeps the API/scheduler responsive while Gmail SMTP runs synchronously.
    """

    def __init__(self) -> None:
        self._queue: Queue[Optional[EmailTask]] = Queue()
        self._worker: Optional[Thread] = None
        self._stop_event = Event()

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = Thread(target=self._run, name="email-task-worker", daemon=True)
        self._worker.start()
        app_logger.info("✓ Email task queue worker started")

    def stop(self) -> None:
        if not self._worker:
            return
        self._stop_event.set()
        self._queue.put(None)
        self._worker.join(timeout=5)
        self._worker = None
        app_logger.info("✓ Email task queue worker stopped")

    def enqueue(self, task: EmailTask) -> None:
        self._queue.put(task)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                continue

            try:
                success = send_job_notifications(
                    email=task.email,
                    category_name=task.category_name,
                    jobs=task.jobs,
                    unsubscribe_token=task.unsubscribe_token,
                )
                self._update_notification_status(task, success, None)
            except Exception as exc:
                app_logger.error(f"Email task failed for {task.email}: {exc}")
                self._update_notification_status(task, False, str(exc))
            finally:
                self._queue.task_done()

    def _update_notification_status(
        self,
        task: EmailTask,
        success: bool,
        error_message: Optional[str],
    ) -> None:
        status = "sent" if success else "failed"
        db = SessionLocal()
        try:
            db.query(Notification).filter(
                Notification.id.in_(task.notification_ids)
            ).update(
                {
                    "status": status,
                    "sent_at": datetime.utcnow() if success else None,
                    "error_message": error_message,
                },
                synchronize_session=False,
            )

            if success:
                db.query(User).filter(User.id == task.user_id).update(
                    {"last_notified_at": datetime.utcnow()},
                    synchronize_session=False,
                )

            db.commit()
        except Exception as exc:
            db.rollback()
            app_logger.error(
                f"Failed to update notification status for user {task.user_id}: {exc}"
            )
        finally:
            db.close()


email_task_queue = EmailTaskQueue()


