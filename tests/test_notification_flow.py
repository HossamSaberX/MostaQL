from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base, Category, Job, Notification, User, UserCategory
from backend.services import notifier


def build_database(min_budget_usd):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    category = Category(id=1, name="برمجة", mostaql_url="https://mostaql.com/projects")
    user = User(
        id=1,
        email="notify@example.com",
        token="token",
        verified=True,
        unsubscribed=False,
        receive_email=True,
        receive_telegram=False,
        min_budget_usd=min_budget_usd,
    )
    job = Job(
        id=1,
        title="مشروع موثوق",
        url="https://mostaql.com/project/1",
        content_hash="hash",
        category_id=1,
        hiring_rate=75,
        budget_min_usd=100,
        budget_max_usd=250,
        published_at=datetime.utcnow(),
        projects_in_progress=1,
        ongoing_communications=2,
        client_identity_verified=True,
        client_payment_verified=False,
    )
    session.add_all([category, user, UserCategory(user_id=1, category_id=1), job])
    session.commit()
    return Session, session, job


def test_rejected_job_creates_no_notification_rows(monkeypatch):
    Session, session, job = build_database(min_budget_usd=500)
    emails = []
    monkeypatch.setattr(notifier, "SessionLocal", Session)
    monkeypatch.setattr(notifier.email_task_queue, "enqueue", emails.append)

    result = notifier.process_new_jobs([job], 1)

    assert result["notifications"] == 0
    assert session.query(Notification).count() == 0
    assert emails == []


def test_accepted_job_creates_row_and_queues_explanatory_payload(monkeypatch):
    Session, session, job = build_database(min_budget_usd=250)
    emails = []
    monkeypatch.setattr(notifier, "SessionLocal", Session)
    monkeypatch.setattr(notifier.email_task_queue, "enqueue", emails.append)

    result = notifier.process_new_jobs([job], 1)

    assert result["notifications"] == 1
    assert session.query(Notification).count() == 1
    assert len(emails) == 1
    assert emails[0].jobs[0]["budget"] == "$100 - $250"
    assert emails[0].jobs[0]["ongoing_communications"] == 2


def test_every_subscriber_gets_their_own_email_with_their_own_token(monkeypatch):
    Session, session, job = build_database(min_budget_usd=None)
    session.add(
        User(
            id=2,
            email="second@example.com",
            token="token-2",
            verified=True,
            unsubscribed=False,
            receive_email=True,
            receive_telegram=False,
        )
    )
    session.add(UserCategory(user_id=2, category_id=1))
    session.commit()
    emails = []
    monkeypatch.setattr(notifier, "SessionLocal", Session)
    monkeypatch.setattr(notifier.email_task_queue, "enqueue", emails.append)

    result = notifier.process_new_jobs([job], 1)

    assert result["queued_emails"] == 2
    assert {(task.email, task.unsubscribe_token) for task in emails} == {
        ("notify@example.com", "token"),
        ("second@example.com", "token-2"),
    }
    assert all(len(task.user_ids) == 1 and len(task.notification_ids) == 1 for task in emails)
    assert emails[0].jobs[0]["category"] == "برمجة"


def test_telegram_gets_one_task_with_a_manage_button(monkeypatch):
    Session, session, job = build_database(min_budget_usd=None)
    user = session.get(User, 1)
    user.receive_email = False
    user.receive_telegram = True
    user.telegram_chat_id = "42"
    session.commit()
    tasks = []
    monkeypatch.setattr(notifier, "SessionLocal", Session)
    monkeypatch.setattr(notifier.telegram_task_queue, "enqueue", tasks.append)

    result = notifier.process_new_jobs([job], 1)

    assert result["queued_telegram"] == 1
    (task,) = tasks
    assert task.chat_id == "42"
    assert task.buttons == [("⚙️ إدارة التنبيهات", "https://alerts.test/api/unsubscribe/token")]
    assert "مشروع موثوق" in task.texts[0]
    assert "تصنيف تتابعه: برمجة" in task.texts[0]
