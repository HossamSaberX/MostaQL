"""
Background task queues for notification delivery.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from queue import Queue
from threading import Event, Thread
from datetime import datetime
from typing import List, Dict, Optional, Generic, TypeVar

import requests

from backend.database import SessionLocal, Notification, User
from backend.enums import NotificationStatus
from backend.config import settings
from backend.utils.logger import app_logger


def send_telegram_message(chat_id: str, title: str, content: str) -> bool:
    if not chat_id or not settings.telegram_bot_token:
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"<b>{title}</b>\n\n{content}",
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        app_logger.info(f"✓ Telegram message sent to chat_id {chat_id}")
        return True
    except requests.exceptions.HTTPError as e:
        if response.status_code == 400 and "parse" in response.text.lower():
            app_logger.warning(f"Telegram parse error for chat_id {chat_id}, retrying without HTML: {e}")
            payload["parse_mode"] = ""
            try:
                response = requests.post(url, json=payload, timeout=10)
                response.raise_for_status()
                app_logger.info(f"✓ Telegram message sent (plain text) to chat_id {chat_id}")
                return True
            except Exception as retry_exc:
                app_logger.error(f"✗ Telegram retry failed for chat_id {chat_id}: {retry_exc}")
                return False
        app_logger.error(f"✗ Telegram HTTP error for chat_id {chat_id}: {e}")
        return False
    except Exception as e:
        app_logger.error(f"✗ Telegram error for chat_id {chat_id}: {e}")
        return False


@dataclass
class BaseTask:
    notification_ids: List[int]
    user_ids: List[int]


@dataclass
class TelegramTask(BaseTask):
    chat_id: str
    title: str
    content: str


@dataclass
class EmailTask(BaseTask):
    email: str
    category_name: str
    jobs: List[Dict[str, str]]
    unsubscribe_token: Optional[str] = None
    bcc: Optional[List[str]] = None


T = TypeVar("T", bound=BaseTask)


class BaseTaskQueue(ABC, Generic[T]):
    def __init__(self, worker_name: str) -> None:
        self._queue: Queue[Optional[T]] = Queue()
        self._worker: Optional[Thread] = None
        self._stop_event = Event()
        self._worker_name = worker_name

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = Thread(target=self._run, name=self._worker_name, daemon=True)
        self._worker.start()
        app_logger.info(f"✓ {self._worker_name} started")

    def stop(self) -> None:
        if not self._worker:
            return
        self._stop_event.set()
        self._queue.put(None)
        self._worker.join(timeout=5)
        self._worker = None
        app_logger.info(f"✓ {self._worker_name} stopped")

    def enqueue(self, task: T) -> None:
        self._queue.put(task)

    def _run(self) -> None:
        self._on_worker_start()
        while not self._stop_event.is_set():
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                continue
            try:
                success = self._process_task(task)
                if success:
                    # app_logger.info(f"✓ {self._worker_name} task completed successfully")
                    pass  # Individual processors (email/telegram) handle their own success logging
                else:
                    app_logger.warning(f"⚠ {self._worker_name} task returned failure")
                
                self._update_notification_status(task, success, None)
            except Exception as exc:
                app_logger.error(f"✗ {self._worker_name} task failed: {exc}")
                self._update_notification_status(task, False, str(exc))
            finally:
                self._queue.task_done()

    def _on_worker_start(self) -> None:
        pass

    @abstractmethod
    def _process_task(self, task: T) -> bool:
        pass

    def _update_notification_status(
        self,
        task: T,
        success: bool,
        error_message: Optional[str],
    ) -> None:
        status = NotificationStatus.SENT.value if success else NotificationStatus.FAILED.value
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
            if success and task.user_ids:
                db.query(User).filter(User.id.in_(task.user_ids)).update(
                    {"last_notified_at": datetime.utcnow()},
                    synchronize_session=False,
                )
            db.commit()
        except Exception as exc:
            db.rollback()
            app_logger.error(f"Failed to update notification status: {exc}")
        finally:
            db.close()


class EmailTaskQueue(BaseTaskQueue[EmailTask]):
    def __init__(self) -> None:
        super().__init__("email-task-worker")

    def _process_task(self, task: EmailTask) -> bool:
        from backend.services.email import send_job_notifications
        return send_job_notifications(
            email=task.email,
            category_name=task.category_name,
            jobs=task.jobs,
            unsubscribe_token=task.unsubscribe_token,
            bcc=task.bcc,
        )


class TelegramTaskQueue(BaseTaskQueue[TelegramTask]):
    def __init__(self) -> None:
        super().__init__("telegram-task-worker")

    def _process_task(self, task: TelegramTask) -> bool:
        return send_telegram_message(task.chat_id, task.title, task.content)


email_task_queue = EmailTaskQueue()
telegram_task_queue = TelegramTaskQueue()


