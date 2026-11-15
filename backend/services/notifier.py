"""
Job notification service.

Alerts go out in two steps. `process_new_jobs` only records who matches what, as `pending`
rows. `flush_notifications` runs once after every poll cycle, decides per person and channel
whether a message is due, and hands one combined message to the send workers.
"""
from datetime import datetime, timedelta, timezone
from html import escape
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload

from backend.database import (
    SessionLocal,
    User,
    Job,
    Notification,
    Category,
    UserCategory,
    FollowedClient,
)
from backend.enums import EmailDelivery, NotificationChannel, NotificationStatus
from backend.services.notification_queue import (
    EmailTask,
    TelegramTask,
    can_receive,
    email_task_queue,
    id_chunks,
    telegram_task_queue,
)
from backend.services.client_tracking import client_profile_key
from backend.services.messages import (
    count_projects,
    job_facts,
    job_reason,
    manage_button,
    more_projects,
    order_jobs,
    projects_url,
)
from backend.services.text import passes_word_filters

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_TITLE_LENGTH = 300
TELEGRAM_PROJECTS_PER_MESSAGE = 10
TELEGRAM_MAX_MESSAGES = 3  # per person per cycle; the rest becomes "and N more"

EMAIL_COOLDOWN = timedelta(minutes=10)  # "within minutes": at most one email per person in this time
EXPIRY_GRACE = timedelta(hours=6)       # a pending row older than its window plus this is never sent
DIGEST_WINDOWS = {
    EmailDelivery.HOURLY.value: timedelta(hours=1),
    EmailDelivery.DAILY.value: timedelta(hours=24),
}
DAILY_DIGEST_HOUR = 9
DIGEST_TIMEZONE = "Africa/Cairo"

_flush_lock = Lock()


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

    return passes_word_filters(job.title, user.keywords_include, user.keywords_exclude)


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


def _followed_label(followed_client: FollowedClient) -> str:
    return followed_client.label or followed_client.profile_url


def _jobs_for_user(user: User, jobs: List[Job], category_id: int) -> List[Job]:
    """Category jobs and followed-client jobs this user should get, each job once."""

    category_subscriber = _is_category_subscriber(user, category_id)
    matching_jobs: List[Job] = []

    for job in jobs:
        # A followed-client alert intentionally ignores category and smart filters (word
        # filters included): the user's explicit request is to see every new project from
        # that client, in every category.
        if _followed_client_for_job(user, job) is not None:
            matching_jobs.append(job)
        elif category_subscriber and _job_matches_user(user, job):
            matching_jobs.append(job)

    return matching_jobs


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


def _telegram_more_line(count: int) -> str:
    link = escape(projects_url(), quote=True)
    return f'<a href="{link}">{escape(more_projects(count))}</a>'


def render_telegram_alert(
    jobs: List[Dict[str, Any]],
    *,
    per_message: int = TELEGRAM_PROJECTS_PER_MESSAGE,
    max_messages: int = TELEGRAM_MAX_MESSAGES,
) -> List[str]:
    """
    One person's alert as Telegram HTML messages.

    Projects from past clients come first and a project is never split between messages.
    A message holds at most `per_message` projects and stays under 4096 characters. After
    `max_messages` messages the rest becomes one "and N more" link at the end of the last one.
    """
    ordered = order_jobs(jobs)
    if not ordered:
        return []

    pages: List[List[str]] = [[f"<b>{count_projects(len(ordered))}</b>"]]
    projects = [0]  # projects on each page (the first page also holds the heading)
    left_out = 0
    for payload in ordered:
        if left_out:
            left_out += 1
            continue
        block = _telegram_project(payload)
        page_is_full = (
            projects[-1] >= per_message
            or len("\n\n".join(pages[-1])) + 2 + len(block) > TELEGRAM_MESSAGE_LIMIT
        )
        if projects[-1] and page_is_full:
            if len(pages) == max_messages:
                left_out += 1
                continue
            pages.append([])
            projects.append(0)
        pages[-1].append(block)
        projects[-1] += 1

    if left_out:
        # Make room for the "and N more" line by moving projects from the end of the last message.
        while projects[-1] > 1 and len("\n\n".join(pages[-1])) + 2 + len(_telegram_more_line(left_out)) > TELEGRAM_MESSAGE_LIMIT:
            pages[-1].pop()
            projects[-1] -= 1
            left_out += 1
        pages[-1].append(_telegram_more_line(left_out))

    return ["\n\n".join(page) for page in pages]


# ---- recording matches ------------------------------------------------------------------


def process_new_jobs(new_jobs: List[Job], category_id: int) -> Dict[str, int]:
    """
    Record who should hear about these new projects: one `pending` row per user, project and
    channel. Nothing is sent here; `flush_notifications` does that after the poll cycle.

    A project that a category and a followed client both match, or that two polls both
    report, is recorded once. `queued_emails` and `queued_telegram` count the people with
    newly recorded rows.
    """
    nothing = {"queued_emails": 0, "notifications": 0, "queued_telegram": 0}
    if not new_jobs:
        return nothing

    db = SessionLocal()
    try:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            logger.warning(f"Category {category_id} not found while notifying users")
            return nothing

        users = _get_users_for_category(category_id, db)
        if not users:
            logger.info(f"No active subscribers or followed clients for category {category.name}")
            return nothing

        recorded: Set[Tuple[int, int, Optional[str]]] = {
            (user_id, job_id, channel)
            for user_id, job_id, channel in db.query(
                Notification.user_id, Notification.job_id, Notification.channel
            ).filter(Notification.job_id.in_([job.id for job in new_jobs]))
        }
        recipients: Dict[NotificationChannel, Set[int]] = {channel: set() for channel in NotificationChannel}
        rows = 0
        for user in users:
            channels = [channel for channel in NotificationChannel if can_receive(user, channel.value)]
            if not channels:
                continue
            for job in _jobs_for_user(user, new_jobs, category_id):
                for channel in channels:
                    key = (user.id, job.id, channel.value)
                    if key in recorded:
                        continue
                    recorded.add(key)
                    db.add(Notification(
                        user_id=user.id,
                        job_id=job.id,
                        channel=channel.value,
                        status=NotificationStatus.PENDING.value,
                    ))
                    recipients[channel].add(user.id)
                    rows += 1
        db.commit()

        logger.info(
            f"Recorded {rows} pending notifications for {len(new_jobs)} new jobs "
            f"in category {category.name}"
        )
        return {
            "queued_emails": len(recipients[NotificationChannel.EMAIL]),
            "notifications": rows,
            "queued_telegram": len(recipients[NotificationChannel.TELEGRAM]),
        }

    except Exception as exc:
        db.rollback()
        logger.error(f"Error recording notifications for category {category_id}: {exc}")
        return nothing
    finally:
        db.close()


# ---- sending what is due ----------------------------------------------------------------


def _set_status(db, ids: List[int], status: NotificationStatus, *, only_if: NotificationStatus) -> int:
    """Move rows from `only_if` to `status`; rows someone else already moved are left alone."""
    changed = 0
    for chunk in id_chunks(ids):
        changed += (
            db.query(Notification)
            .filter(Notification.id.in_(chunk), Notification.status == only_if.value)
            .update({"status": status.value}, synchronize_session=False)
        )
    return changed


def reset_queued_notifications() -> int:
    """
    Put `queued` rows back to `pending`. The send queues live in memory and are lost when the
    app restarts, so anything still `queued` at startup was never sent.
    """
    db = SessionLocal()
    try:
        count = db.query(Notification).filter(
            Notification.status == NotificationStatus.QUEUED.value
        ).update({"status": NotificationStatus.PENDING.value}, synchronize_session=False)
        db.commit()
        if count:
            logger.info(f"Returned {count} unsent queued notifications to pending")
        return count
    except Exception as exc:
        db.rollback()
        logger.error(f"Could not reset queued notifications: {exc}")
        return 0
    finally:
        db.close()


def _created_at(row: Notification, now: datetime) -> datetime:
    return row.created_at or row.sent_at or now  # old rows have no created_at


def _delivery_window(user: User, channel: str) -> timedelta:
    """How long a person's alerts are gathered: none for Telegram and "within minutes" email."""
    if channel != NotificationChannel.EMAIL.value:
        return timedelta(0)
    return DIGEST_WINDOWS.get(user.email_delivery, timedelta(0))


def _todays_digest_time(now: datetime) -> datetime:
    """Today's 09:00 in Cairo as a naive UTC time (Cairo moves its clocks in summer)."""
    local = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(DIGEST_TIMEZONE))
    nine = local.replace(hour=DAILY_DIGEST_HOUR, minute=0, second=0, microsecond=0)
    return nine.astimezone(timezone.utc).replace(tzinfo=None)


def _email_rows_due(user: User, rows: List[Notification], *, held: bool, now: datetime) -> List[Notification]:
    """
    The rows to email now (possibly none). "Within minutes" waits only while an email went
    out in the last 10 minutes (or is still being sent), "hourly" waits for the oldest project
    to be an hour old, and "daily" sends once after 09:00 Cairo time. Past-client projects
    never wait: with "within minutes" they take the whole batch along; with a digest they go
    alone and the rest keep waiting for the digest.
    """
    past_client = [row for row in rows if _followed_client_for_job(user, row.job)]
    oldest = min(_created_at(row, now) for row in rows)
    if user.email_delivery == EmailDelivery.HOURLY.value:
        due = now - oldest >= DIGEST_WINDOWS[EmailDelivery.HOURLY.value]
    elif user.email_delivery == EmailDelivery.DAILY.value:
        digest_time = _todays_digest_time(now)
        due = now >= digest_time and oldest < digest_time
    else:
        return rows if past_client or not held else []
    return rows if due else past_client


def _held_email_users(db, now: datetime) -> Set[int]:
    """
    People whose last ordinary email went out in the cooldown or is still waiting to be sent.
    Past-client projects never wait, so an email that carried only those does not start a wait.
    """
    rows = (
        db.query(Notification)
        .options(joinedload(Notification.user), joinedload(Notification.job))
        .filter(
            Notification.channel == NotificationChannel.EMAIL.value,
            or_(
                Notification.status == NotificationStatus.QUEUED.value,
                and_(
                    Notification.status == NotificationStatus.SENT.value,
                    Notification.sent_at >= now - EMAIL_COOLDOWN,
                ),
            ),
        )
        .all()
    )
    return {row.user_id for row in rows if not _followed_client_for_job(row.user, row.job)}


def _newest_first(jobs: Iterable[Job]) -> List[Job]:
    return sorted(
        jobs,
        key=lambda job: (job.published_at or job.scraped_at or datetime.min, job.id),
        reverse=True,
    )


def _alert_payloads(user: User, rows: List[Notification], now: datetime) -> List[Dict[str, Any]]:
    """One payload per project, newest first, each with the reason this person got it."""
    jobs = _newest_first({row.job_id: row.job for row in rows}.values())
    payloads = []
    for job in jobs:
        followed = _followed_client_for_job(user, job)
        payloads.append(
            _build_job_payload(
                job,
                now=now,
                followed_client=_followed_label(followed) if followed else None,
                category_name=job.category.name if job.category else None,
            )
        )
    return payloads


def _build_task(user: User, channel: str, rows: List[Notification], now: datetime):
    """The one message (as a send task) that carries all these rows to this person."""
    payloads = _alert_payloads(user, rows, now)
    ids = [row.id for row in rows]
    if channel == NotificationChannel.EMAIL.value:
        return EmailTask(
            notification_ids=ids,
            user_ids=[user.id],
            email=user.email,
            jobs=payloads,
            unsubscribe_token=user.token,
        )
    return TelegramTask(
        notification_ids=ids,
        user_ids=[user.id],
        chat_id=user.telegram_chat_id,
        texts=render_telegram_alert(payloads),
        buttons=[manage_button(user.token)],
    )


def _flush_group(
    db,
    user: User,
    channel: str,
    rows: List[Notification],
    *,
    held_email_users: Set[int],
    now: datetime,
):
    """Expire what is stale and, if a message is due, claim the rest. Returns (task, expired count)."""
    allowed = can_receive(user, channel)
    cutoff = now - _delivery_window(user, channel) - EXPIRY_GRACE
    stale = [row for row in rows if not allowed or _created_at(row, now) < cutoff]
    stale_ids = {row.id for row in stale}
    fresh = [row for row in rows if row.id not in stale_ids]

    if fresh and channel == NotificationChannel.EMAIL.value:
        fresh = _email_rows_due(user, fresh, held=user.id in held_email_users, now=now)
    task = _build_task(user, channel, fresh, now) if fresh else None

    expired = _set_status(
        db, [row.id for row in stale], NotificationStatus.EXPIRED, only_if=NotificationStatus.PENDING
    )
    if task:
        claimed = _set_status(
            db, task.notification_ids, NotificationStatus.QUEUED, only_if=NotificationStatus.PENDING
        )
        if claimed != len(task.notification_ids):
            logger.warning(f"Notifications of user {user.id} changed while flushing; not sending")
            db.rollback()
            return None, 0
    db.commit()
    return task, expired


def flush_notifications(now: Optional[datetime] = None) -> Dict[str, int]:
    """
    Send what is due. Call it once after every poll cycle, also when nothing new was found,
    so hourly and daily digests go out on time.

    Reads only `pending` rows of the email and Telegram channels. Per person and channel it
    sends ONE combined message: Telegram every cycle, email according to the person's
    delivery choice. Rows are marked `queued` and committed before the message is queued, so
    one flush sends a row at most once; the send worker then marks them sent or failed. If
    the app stops between a send and that mark, the restart sends that one message again.
    Rows too old for their window, or of people who can no longer receive them, are `expired`.
    """
    now = now or datetime.utcnow()
    result = {"queued_emails": 0, "queued_telegram": 0, "notifications": 0, "expired": 0}
    with _flush_lock:
        db = SessionLocal(expire_on_commit=False)
        try:
            rows = (
                db.query(Notification)
                .options(joinedload(Notification.user), joinedload(Notification.job).joinedload(Job.category))
                .filter(
                    Notification.status == NotificationStatus.PENDING.value,
                    Notification.channel.in_([channel.value for channel in NotificationChannel]),
                )
                .order_by(Notification.id)
                .all()
            )
            groups: Dict[Tuple[int, str], List[Notification]] = {}
            for row in rows:
                groups.setdefault((row.user_id, row.channel), []).append(row)

            held_email_users = _held_email_users(db, now) if groups else set()
            for (user_id, channel), group in groups.items():
                try:
                    task, expired = _flush_group(
                        db, group[0].user, channel, group, held_email_users=held_email_users, now=now
                    )
                except Exception as exc:
                    db.rollback()
                    logger.error(f"Could not flush {channel} notifications of user {user_id}: {exc}")
                    continue
                result["expired"] += expired
                if task is None:
                    continue
                result["notifications"] += len(task.notification_ids)
                if channel == NotificationChannel.EMAIL.value:
                    result["queued_emails"] += 1
                    email_task_queue.enqueue(task)
                else:
                    result["queued_telegram"] += 1
                    telegram_task_queue.enqueue(task)
        except Exception as exc:
            db.rollback()
            logger.error(f"Error flushing notifications: {exc}")
        finally:
            db.close()

    if result["notifications"] or result["expired"]:
        logger.info(
            f"Flushed notifications: {result['queued_emails']} emails and "
            f"{result['queued_telegram']} Telegram messages queued "
            f"({result['notifications']} projects), {result['expired']} expired"
        )
    return result
