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
