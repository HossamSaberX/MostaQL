"""Recording matches (process_new_jobs) and sending what is due (flush_notifications)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from backend.database import (
    Category,
    FollowedClient,
    Job,
    Notification,
    User,
    UserCategory,
)
from backend.services import notifier

NOW = datetime(2026, 11, 15, 10, 0)  # 12:00 in Cairo (UTC+2 in November)
CLIENT = "https://mostaql.com/u/client"


@pytest.fixture
def env(db_session, monkeypatch):
    """A database with two categories and the two send queues replaced by lists."""
    monkeypatch.setattr(notifier, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    emails, telegram = [], []
    monkeypatch.setattr(notifier.email_task_queue, "enqueue", emails.append)
    monkeypatch.setattr(notifier.telegram_task_queue, "enqueue", telegram.append)
    db_session.add_all(
        [
            Category(id=1, name="برمجة", mostaql_url="https://mostaql.com/projects?category=development"),
            Category(id=2, name="تصميم", mostaql_url="https://mostaql.com/projects?category=design"),
        ]
    )
    db_session.commit()
    return SimpleNamespace(db=db_session, emails=emails, telegram=telegram)


def add_user(db, user_id=1, *, email=True, telegram=False, delivery="instant", categories=(1,), **fields):
    db.add(
        User(
            id=user_id,
            email=f"user{user_id}@example.com",
            token=f"token{user_id}",
            verified=True,
            unsubscribed=False,
            receive_email=email,
            receive_telegram=telegram,
            telegram_chat_id=f"chat{user_id}" if telegram else None,
            email_delivery=delivery,
            **fields,
        )
    )
    db.add_all([UserCategory(user_id=user_id, category_id=c) for c in categories])
    db.commit()


def add_job(db, job_id, *, category_id=1, title=None, client=None, published_at=None):
    job = Job(
        id=job_id,
        title=title or f"مشروع {job_id}",
        url=f"https://mostaql.com/project/{job_id}",
        content_hash=f"hash{job_id}",
        category_id=category_id,
        client_profile_url=client,
        published_at=published_at or NOW - timedelta(minutes=5),
    )
    db.add(job)
    db.commit()
    return job


def add_row(db, user_id, job_id, channel="email", *, age=timedelta(0), status="pending", **fields):
    fields.setdefault("sent_at", NOW - timedelta(days=1))  # never the real clock: tests must not depend on it
    row = Notification(
        user_id=user_id,
        job_id=job_id,
        channel=channel,
        status=status,
        created_at=NOW - age,
        **fields,
    )
    db.add(row)
    db.commit()
    return row


def statuses(db):
    db.expire_all()
    return {row.id: row.status for row in db.query(Notification).all()}


# ---- recording ----------------------------------------------------------------------------


def test_process_new_jobs_only_records_pending_rows(env):
    add_user(env.db, telegram=True)
    job = add_job(env.db, 1)

    result = notifier.process_new_jobs([job], 1)

    assert result == {"queued_emails": 1, "notifications": 2, "queued_telegram": 1}
    rows = env.db.query(Notification).all()
    assert {(row.channel, row.status) for row in rows} == {("email", "pending"), ("telegram", "pending")}
    assert all(row.created_at is not None for row in rows)
    assert env.emails == [] and env.telegram == []


def test_a_project_matched_twice_or_polled_twice_is_recorded_once(env):
    add_user(env.db, telegram=True)
    env.db.add(FollowedClient(user_id=1, profile_url=CLIENT, label="العميل"))
    job = add_job(env.db, 1, client=CLIENT)  # matches by category AND by followed client

    notifier.process_new_jobs([job], 1)
    again = notifier.process_new_jobs([job], 1)

    assert env.db.query(Notification).count() == 2  # one email row, one Telegram row
    assert again["notifications"] == 0


def test_flush_shows_a_twice_matched_project_once_with_the_past_client_reason(env):
    add_user(env.db)
    env.db.add(FollowedClient(user_id=1, profile_url=CLIENT, label="العميل"))
    job = add_job(env.db, 1, client=CLIENT)
    notifier.process_new_jobs([job], 1)

    notifier.flush_notifications()  # process_new_jobs stamps rows with the real clock

    (task,) = env.emails
    assert len(task.jobs) == 1
    assert task.jobs[0]["followed_client"] == "العميل"
    assert task.jobs[0]["category"] == "برمجة"


def test_old_rows_of_a_project_do_not_block_other_users_or_channels(env):
    add_user(env.db, 1)
    add_user(env.db, 2, telegram=True)
    job = add_job(env.db, 1)
    add_row(env.db, 1, 1, "email", status="expired")

    result = notifier.process_new_jobs([job], 1)

    assert result["notifications"] == 2  # user 2's email and Telegram rows; user 1 already has one


# ---- Telegram -------------------------------------------------------------------------------


def test_telegram_combines_categories_into_one_message_per_cycle(env):
    add_user(env.db, email=False, telegram=True, categories=(1, 2))
    notifier.process_new_jobs([add_job(env.db, 1, category_id=1, title="أول")], 1)
    notifier.process_new_jobs([add_job(env.db, 2, category_id=2, title="ثاني")], 2)

    result = notifier.flush_notifications()

    assert result["queued_telegram"] == 1 and result["notifications"] == 2
    (task,) = env.telegram
    assert task.chat_id == "chat1"
    assert len(task.texts) == 1
    assert "أول" in task.texts[0] and "ثاني" in task.texts[0]
    assert task.buttons == [("⚙️ إدارة التنبيهات", "https://alerts.test/api/unsubscribe/token1")]
    assert set(statuses(env.db).values()) == {"queued"}


def test_a_flood_becomes_three_messages_and_a_more_line_but_every_row_is_accounted_for(env):
    add_user(env.db, email=False, telegram=True)
    for n in range(1, 36):
        add_job(env.db, n)
        add_row(env.db, 1, n, "telegram")

    notifier.flush_notifications(NOW)

    (task,) = env.telegram
    assert len(task.notification_ids) == 35
    assert len(task.texts) == 3
    assert [text.count("mostaql.com/project/") for text in task.texts] == [10, 10, 10]
    assert task.texts[-1].endswith('<a href="https://mostaql.com/projects">و5 مشاريع أخرى</a>')
    assert set(statuses(env.db).values()) == {"queued"}


def test_telegram_lists_past_clients_first_then_the_newest(env):
    add_user(env.db, email=False, telegram=True)
    env.db.add(FollowedClient(user_id=1, profile_url=CLIENT))
    add_job(env.db, 1, title="الأقدم", published_at=NOW - timedelta(hours=2))
    add_job(env.db, 2, title="الأحدث", published_at=NOW - timedelta(minutes=1))
    add_job(env.db, 3, title="من عميل سابق", client=CLIENT, published_at=NOW - timedelta(hours=3))
    for n in (1, 2, 3):
        add_row(env.db, 1, n, "telegram")

    notifier.flush_notifications(NOW)

    text = env.telegram[0].texts[0]
    assert text.index("من عميل سابق") < text.index("الأحدث") < text.index("الأقدم")


def test_a_person_on_both_channels_gets_one_task_per_channel(env):
    add_user(env.db, telegram=True)
    for n in (1, 2):
        add_job(env.db, n)
        add_row(env.db, 1, n, "email")
        add_row(env.db, 1, n, "telegram")
    add_row(env.db, 1, 1, "email")  # a stray duplicate row never shows a project twice

    result = notifier.flush_notifications(NOW)

    assert (result["queued_emails"], result["queued_telegram"]) == (1, 1)
    assert [job["title"] for job in env.emails[0].jobs] == ["مشروع 2", "مشروع 1"]
    assert len(env.emails[0].notification_ids) == 3


# ---- email: within minutes -----------------------------------------------------------------


def test_the_first_email_goes_right_away_then_one_every_ten_minutes(env):
    add_user(env.db)
    add_job(env.db, 1)
    add_job(env.db, 2)
    first = add_row(env.db, 1, 1)

    notifier.flush_notifications(NOW)
    assert len(env.emails) == 1
    # The worker reports the send.
    first.status, first.sent_at = "sent", NOW
    env.db.commit()

    second = add_row(env.db, 1, 2, age=timedelta(0))
    notifier.flush_notifications(NOW + timedelta(minutes=5))
    assert len(env.emails) == 1  # held: an email went out 5 minutes ago
    assert statuses(env.db)[second.id] == "pending"

    notifier.flush_notifications(NOW + timedelta(minutes=10, seconds=1))
    assert len(env.emails) == 2
    assert env.emails[1].notification_ids == [second.id]


def test_a_past_client_project_skips_the_ten_minute_wait(env):
    add_user(env.db)
    env.db.add(FollowedClient(user_id=1, profile_url=CLIENT))
    add_job(env.db, 1)
    add_job(env.db, 2, client=CLIENT)
    add_row(env.db, 1, 1, status="sent", sent_at=NOW)
    ordinary = add_row(env.db, 1, 1)
    past_client = add_row(env.db, 1, 2)

    notifier.flush_notifications(NOW + timedelta(minutes=1))

    (task,) = env.emails
    assert sorted(task.notification_ids) == sorted([ordinary.id, past_client.id])


def test_an_email_with_only_past_client_projects_does_not_hold_ordinary_alerts(env):
    # A busy followed client must not keep renewing the wait for everything else.
    add_user(env.db)
    env.db.add(FollowedClient(user_id=1, profile_url=CLIENT))
    add_job(env.db, 1, client=CLIENT)
    add_job(env.db, 2)
    add_row(env.db, 1, 1, status="sent", sent_at=NOW)
    ordinary = add_row(env.db, 1, 2)

    notifier.flush_notifications(NOW + timedelta(minutes=1))

    (task,) = env.emails
    assert task.notification_ids == [ordinary.id]


def test_a_new_row_has_no_sent_time_until_it_is_sent(env):
    add_user(env.db)
    add_job(env.db, 1)
    env.db.add(Notification(user_id=1, job_id=1, channel="email", status="pending"))
    env.db.commit()

    row = env.db.query(Notification).one()
    assert row.sent_at is None
    assert row.created_at is not None


def test_an_email_still_being_sent_holds_the_next_one(env):
    add_user(env.db)
    add_job(env.db, 1)
    add_job(env.db, 2)
    add_row(env.db, 1, 1, status="queued")
    waiting = add_row(env.db, 1, 2)

    notifier.flush_notifications(NOW)

    assert env.emails == []
    assert statuses(env.db)[waiting.id] == "pending"


def test_another_persons_recent_email_does_not_hold_you(env):
    add_user(env.db, 1)
    add_user(env.db, 2)
    add_job(env.db, 1)
    add_row(env.db, 1, 1, status="sent", sent_at=NOW)
    add_row(env.db, 2, 1)

    notifier.flush_notifications(NOW)

    assert [task.user_ids for task in env.emails] == [[2]]


# ---- email: hourly and daily digests ----------------------------------------------------------


def test_hourly_digest_waits_for_the_oldest_project_to_be_an_hour_old(env):
    add_user(env.db, delivery="hourly")
    add_job(env.db, 1)
    add_job(env.db, 2)
    add_row(env.db, 1, 1, age=timedelta(minutes=59))
    add_row(env.db, 1, 2, age=timedelta(minutes=1))

    notifier.flush_notifications(NOW)
    assert env.emails == []

    notifier.flush_notifications(NOW + timedelta(minutes=1))
    (task,) = env.emails
    assert len(task.notification_ids) == 2  # one digest with everything gathered


def test_hourly_digest_still_rushes_a_past_client_project(env):
    add_user(env.db, delivery="hourly")
    env.db.add(FollowedClient(user_id=1, profile_url=CLIENT))
    add_job(env.db, 1, client=CLIENT)
    add_row(env.db, 1, 1)

    notifier.flush_notifications(NOW)

    assert len(env.emails) == 1


@pytest.mark.parametrize("delivery", ["hourly", "daily"])
def test_a_past_client_project_goes_alone_and_the_digest_keeps_waiting(env, delivery):
    add_user(env.db, delivery=delivery)
    env.db.add(FollowedClient(user_id=1, profile_url=CLIENT))
    add_job(env.db, 1)
    add_job(env.db, 2, client=CLIENT)
    waiting = add_row(env.db, 1, 1, age=timedelta(minutes=10))
    rushed = add_row(env.db, 1, 2)
    # 07:00 in Cairo: neither the hourly nor the daily digest is due yet.
    before_digest = NOW - timedelta(hours=5)
    waiting.created_at = before_digest - timedelta(minutes=10)
    rushed.created_at = before_digest
    env.db.commit()

    notifier.flush_notifications(before_digest)

    (task,) = env.emails
    assert task.notification_ids == [rushed.id]
    assert statuses(env.db)[waiting.id] == "pending"

    # At the digest's normal time the rest goes out.
    notifier.flush_notifications(NOW + timedelta(hours=1))
    assert len(env.emails) == 2
    assert env.emails[1].notification_ids == [waiting.id]


@pytest.mark.parametrize(
    ("today", "digest_utc_hour"),
    [
        (datetime(2026, 11, 15), 7),  # Cairo is UTC+2 in winter
        (datetime(2026, 7, 15), 6),   # and UTC+3 in summer
    ],
)
def test_daily_digest_goes_once_after_nine_in_cairo(env, today, digest_utc_hour):
    digest = today.replace(hour=digest_utc_hour)
    add_user(env.db, delivery="daily")
    add_job(env.db, 1)
    add_job(env.db, 2)
    # Created at 20:00 UTC the evening before (a day-old project).
    evening = add_row(env.db, 1, 1)
    evening.created_at = digest - timedelta(hours=11)
    env.db.commit()

    notifier.flush_notifications(digest - timedelta(minutes=1))
    assert env.emails == []  # 08:59 in Cairo

    notifier.flush_notifications(digest)
    assert len(env.emails) == 1  # 09:00 in Cairo
    assert env.emails[0].notification_ids == [evening.id]

    # A project found after the digest waits for tomorrow's.
    later = add_row(env.db, 1, 2)
    later.created_at = digest + timedelta(minutes=30)
    env.db.commit()
    notifier.flush_notifications(digest + timedelta(hours=5))
    assert len(env.emails) == 1
    notifier.flush_notifications(digest + timedelta(hours=23, minutes=59))
    assert len(env.emails) == 1
    notifier.flush_notifications(digest + timedelta(hours=24))
    assert len(env.emails) == 2
    assert env.emails[1].notification_ids == [later.id]


def test_daily_digest_still_rushes_a_past_client_project(env):
    add_user(env.db, delivery="daily")
    env.db.add(FollowedClient(user_id=1, profile_url=CLIENT))
    add_job(env.db, 1, client=CLIENT)
    add_row(env.db, 1, 1)

    notifier.flush_notifications(NOW - timedelta(hours=5))  # 07:00 in Cairo, before the digest

    assert len(env.emails) == 1


# ---- expiry -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delivery", "channel", "window_hours"),
    [
        ("instant", "email", 6),
        ("instant", "telegram", 6),
        ("hourly", "email", 7),
        ("daily", "email", 30),
    ],
)
def test_rows_older_than_their_window_plus_six_hours_expire_and_are_never_sent(
    env, delivery, channel, window_hours
):
    add_user(env.db, telegram=channel == "telegram", delivery=delivery, email=channel == "email")
    for n in (1, 2):
        add_job(env.db, n)
    stale = add_row(env.db, 1, 1, channel, age=timedelta(hours=window_hours, minutes=1))
    kept = add_row(env.db, 1, 2, channel, age=timedelta(hours=window_hours) - timedelta(minutes=1))

    result = notifier.flush_notifications(NOW)

    assert result["expired"] == 1
    assert statuses(env.db)[stale.id] == "expired"
    tasks = env.emails + env.telegram
    # Only the fresh row is considered: hourly and daily digests may keep waiting for their time.
    assert all(stale.id not in task.notification_ids for task in tasks)
    if delivery == "instant":
        assert [task.notification_ids for task in tasks] == [[kept.id]]


def test_rows_with_no_created_at_use_sent_at(env):
    add_user(env.db)
    add_job(env.db, 1)
    old = add_row(env.db, 1, 1, sent_at=NOW - timedelta(days=30))
    old.created_at = None
    env.db.commit()

    notifier.flush_notifications(NOW)

    assert statuses(env.db)[old.id] == "expired"
    assert env.emails == []


@pytest.mark.parametrize(
    "fields",
    [
        {"unsubscribed": True},
        {"verified": False},
        {"receive_email": False},
    ],
)
def test_rows_of_people_who_can_no_longer_receive_them_expire(env, fields):
    add_user(env.db, 1)
    add_job(env.db, 1)
    row = add_row(env.db, 1, 1)
    env.db.query(User).filter(User.id == 1).update(fields)
    env.db.commit()

    notifier.flush_notifications(NOW)

    assert statuses(env.db)[row.id] == "expired"
    assert env.emails == []


def test_telegram_rows_expire_when_the_chat_is_unlinked(env):
    add_user(env.db, email=False, telegram=True)
    add_job(env.db, 1)
    row = add_row(env.db, 1, 1, "telegram")
    env.db.query(User).filter(User.id == 1).update({"telegram_chat_id": None})
    env.db.commit()

    notifier.flush_notifications(NOW)

    assert statuses(env.db)[row.id] == "expired"
    assert env.telegram == []


def test_the_flush_reads_only_pending_rows_of_real_channels(env):
    add_user(env.db, telegram=True)
    add_job(env.db, 1)
    no_channel = add_row(env.db, 1, 1, channel=None)
    others = [add_row(env.db, 1, 1, status=status) for status in ("sent", "failed", "expired", "queued")]

    result = notifier.flush_notifications(NOW)

    assert result["notifications"] == 0 and result["expired"] == 0
    assert env.emails == [] and env.telegram == []
    state = statuses(env.db)
    assert state[no_channel.id] == "pending"  # left alone, not even expired by the flush
    assert [state[row.id] for row in others] == ["sent", "failed", "expired", "queued"]


# ---- never twice, and recovery -----------------------------------------------------------------


def test_queued_rows_are_not_sent_again_by_the_next_flush(env):
    add_user(env.db, email=False, telegram=True)
    add_job(env.db, 1)
    add_row(env.db, 1, 1, "telegram")

    notifier.flush_notifications(NOW)
    again = notifier.flush_notifications(NOW + timedelta(minutes=1))

    assert len(env.telegram) == 1
    assert again["notifications"] == 0


def test_rows_claimed_by_someone_else_meanwhile_are_not_sent(env, monkeypatch):
    add_user(env.db, email=False, telegram=True)
    add_job(env.db, 1)
    row = add_row(env.db, 1, 1, "telegram")
    original = notifier._build_task

    def build_then_lose_the_row(*args, **kwargs):
        task = original(*args, **kwargs)
        env.db.query(Notification).filter(Notification.id == row.id).update({"status": "sent"})
        env.db.commit()
        return task

    monkeypatch.setattr(notifier, "_build_task", build_then_lose_the_row)

    result = notifier.flush_notifications(NOW)

    assert env.telegram == [] and result["queued_telegram"] == 0
    assert statuses(env.db)[row.id] == "sent"


def test_one_broken_person_does_not_stop_the_others(env, monkeypatch):
    add_user(env.db, 1, email=False, telegram=True)
    add_user(env.db, 2, email=False, telegram=True)
    add_job(env.db, 1)
    broken = add_row(env.db, 1, 1, "telegram")
    add_row(env.db, 2, 1, "telegram")
    original = notifier._build_task

    def fail_for_first(user, *args, **kwargs):
        if user.id == 1:
            raise RuntimeError("boom")
        return original(user, *args, **kwargs)

    monkeypatch.setattr(notifier, "_build_task", fail_for_first)

    notifier.flush_notifications(NOW)

    assert [task.user_ids for task in env.telegram] == [[2]]
    assert statuses(env.db)[broken.id] == "pending"  # still there for the next cycle


def test_startup_returns_unsent_queued_rows_to_pending(env):
    add_user(env.db)
    add_job(env.db, 1)
    queued = add_row(env.db, 1, 1, status="queued")
    sent = add_row(env.db, 1, 1, status="sent")
    expired = add_row(env.db, 1, 1, status="expired")

    assert notifier.reset_queued_notifications() == 1

    state = statuses(env.db)
    assert (state[queued.id], state[sent.id], state[expired.id]) == ("pending", "sent", "expired")
    notifier.flush_notifications(NOW)
    assert [task.notification_ids for task in env.emails] == [[queued.id]]
