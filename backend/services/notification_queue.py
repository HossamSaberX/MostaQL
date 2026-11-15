"""
Background task queues for notification delivery.
"""
from __future__ import annotations

import html
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from queue import Queue
from threading import Event, Thread
from datetime import datetime
from typing import Any, List, Dict, Optional, Generic, Tuple, TypeVar

import requests

from backend.database import SessionLocal, Notification, User
from backend.enums import NotificationStatus
from backend.config import settings
from backend.services.messages import BOT_COMMANDS
from backend.utils.logger import app_logger


TELEGRAM_TIMEOUT_SECONDS = 10
TELEGRAM_MAX_RETRY_WAIT_SECONDS = 30

Button = Tuple[str, str]  # (label, url)


def _telegram_post(method: str, payload: Dict[str, Any]) -> requests.Response:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    return requests.post(url, json=payload, timeout=TELEGRAM_TIMEOUT_SECONDS)


def _telegram_error(response: requests.Response) -> str:
    """Telegram's own explanation; never the request URL, which contains the bot token."""
    try:
        return str(response.json().get("description", response.status_code))
    except ValueError:
        return str(response.status_code)


def _retry_after_seconds(response: requests.Response) -> float:
    try:
        wait = float(response.json().get("parameters", {}).get("retry_after", 1))
    except (ValueError, TypeError, AttributeError):
        wait = 1.0
    return min(max(wait, 0.0), TELEGRAM_MAX_RETRY_WAIT_SECONDS)


def _plain_text(html_text: str) -> str:
    """HTML message without tags; a link becomes 'text: url' so nothing is lost."""
    text = re.sub(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', r"\2: \1", html_text, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _unlink_telegram_chat(chat_id: str) -> None:
    """The user blocked the bot or deleted the chat (403): stop sending there."""
    db = SessionLocal()
    try:
        unlinked = db.query(User).filter(User.telegram_chat_id == chat_id).update(
            {"telegram_chat_id": None}, synchronize_session=False
        )
        db.commit()
        if unlinked:
            app_logger.info(f"Telegram chat {chat_id} blocked the bot; unlinked it from its user")
    except Exception as exc:
        db.rollback()
        app_logger.error(f"Failed to unlink Telegram chat {chat_id}: {exc}")
    finally:
        db.close()


def send_telegram_message(
    chat_id: str,
    text: str,
    *,
    buttons: Optional[List[Button]] = None,
    silent: bool = False,
) -> bool:
    """
    Send one HTML message. `buttons` are URL buttons under it, one per row; `silent` sends
    without a notification sound.

    429: wait as long as Telegram asks (at most 30 s) and try once more.
    403: the chat blocked the bot, so it is unlinked from its user.
    HTML parse error: try once more as plain text.
    """
    if not chat_id or not settings.telegram_bot_token:
        return False

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if silent:
        payload["disable_notification"] = True
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": label, "url": url}] for label, url in buttons]
        }

    try:
        response = _telegram_post("sendMessage", payload)
        if response.status_code == 429:
            time.sleep(_retry_after_seconds(response))
            response = _telegram_post("sendMessage", payload)
        elif response.status_code == 400 and "parse" in response.text.lower():
            app_logger.warning(f"Telegram could not parse the message for chat {chat_id}; sending plain text")
            payload = {k: v for k, v in payload.items() if k != "parse_mode"}
            payload["text"] = _plain_text(text)
            response = _telegram_post("sendMessage", payload)
    except requests.RequestException as exc:
        app_logger.error(f"✗ Telegram request failed for chat {chat_id}: {type(exc).__name__}")
        return False

    if response.status_code == 200:
        app_logger.info(f"✓ Telegram message sent to chat_id {chat_id}")
        return True
    if response.status_code == 403:
        _unlink_telegram_chat(chat_id)
    app_logger.error(f"✗ Telegram error for chat {chat_id}: {response.status_code} {_telegram_error(response)}")
    return False


def register_bot_commands() -> bool:
    """Show the bot menu (start, help, stop). Needs a bot token; a failure is only logged."""
    if not settings.telegram_bot_token:
        return False
    commands = [{"command": name, "description": description} for name, description in BOT_COMMANDS]
    try:
        response = _telegram_post("setMyCommands", {"commands": commands})
        if response.status_code == 200:
            app_logger.info("✓ Telegram bot commands registered")
            return True
        app_logger.warning(f"Telegram bot commands not registered: {_telegram_error(response)}")
    except requests.RequestException as exc:
        app_logger.warning(f"Telegram bot commands not registered: {type(exc).__name__}")
    return False


@dataclass
class BaseTask:
    notification_ids: List[int]
    user_ids: List[int]


@dataclass
class TelegramTask(BaseTask):
    """One person's Telegram messages (several only when the text is too long for one)."""
    chat_id: str
    texts: List[str]
    buttons: Optional[List[Button]] = None  # shown under the last message


@dataclass
class EmailTask(BaseTask):
    """One alert email for one person."""
    email: str
    jobs: List[Dict[str, Any]]
    unsubscribe_token: Optional[str] = None
    more_count: int = 0
    more_url: Optional[str] = None


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
            jobs=task.jobs,
            unsubscribe_token=task.unsubscribe_token,
            more_count=task.more_count,
            more_url=task.more_url,
        )


class TelegramTaskQueue(BaseTaskQueue[TelegramTask]):
    def __init__(self) -> None:
        super().__init__("telegram-task-worker")

    def _process_task(self, task: TelegramTask) -> bool:
        """Send every part, the first with a sound and the rest silently; stop at the first failure."""
        last = len(task.texts) - 1
        for index, text in enumerate(task.texts):
            sent = send_telegram_message(
                task.chat_id,
                text,
                buttons=task.buttons if index == last else None,
                silent=index > 0,
            )
            if not sent:
                return False
        return True


email_task_queue = EmailTaskQueue()
telegram_task_queue = TelegramTaskQueue()


