import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api.verify import (
    add_previously_completed_client,
    get_preferences,
    remove_previously_completed_client,
)
from backend.database import Base, Category, FollowedClient, Job, Notification, User, UserCategory
from backend.models import FollowedClientRequest
from backend.services import notifier
from backend.services.client_tracking import (
    FollowedClientError,
    add_followed_client,
    client_profile_key,
    normalize_client_profile_url,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all(
        [
            Category(id=1, name="برمجة", mostaql_url="https://mostaql.com/projects?category=development"),
            Category(id=2, name="تصميم", mostaql_url="https://mostaql.com/projects?category=design"),
        ]
    )
    session.commit()
    return session


def response_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_profile_urls_are_canonical_and_restricted_to_mostaql():
    assert normalize_client_profile_url("https://www.mostaql.com/u/client/") == "https://mostaql.com/u/client"
    assert normalize_client_profile_url("https://mostaql.com/u/client/reviews") == "https://mostaql.com/u/client"
    assert normalize_client_profile_url("/u/client/projects") == "https://mostaql.com/u/client"
    assert client_profile_key("https://www.mostaql.com/u/Client/notes") == "client"

    with pytest.raises(FollowedClientError):
        normalize_client_profile_url("https://evil.example/u/client")


def test_followed_client_api_round_trip_and_delete():
    session = make_session()
    user = User(
        id=1,
        email="past-client@example.com",
        token="token",
        verified=True,
        unsubscribed=False,
    )
    session.add(user)
    session.commit()

    request = FollowedClientRequest(
        token="token",
        profile_url="https://www.mostaql.com/u/client/",
        label="عميل سابق",
    )
    added = asyncio.run(add_previously_completed_client(request, db=session))
    data = response_json(added)
    assert data["client"]["profile_url"] == "https://mostaql.com/u/client"

    preferences = response_json(asyncio.run(get_preferences("token", db=session)))
    assert preferences["followed_clients"] == [
        {
            "id": data["client"]["id"],
            "profile_url": "https://mostaql.com/u/client",
            "label": "عميل سابق",
        }
    ]

    deleted = asyncio.run(
        remove_previously_completed_client(data["client"]["id"], "token", db=session)
    )
    assert "حذف" in response_json(deleted)["message"]
    assert session.query(FollowedClient).count() == 0


def test_followed_client_matches_any_category_and_bypasses_category_filters(monkeypatch):
    session = make_session()
    user = User(
        id=1,
        email="past-client@example.com",
        token="token",
        verified=True,
        unsubscribed=False,
        receive_email=True,
        receive_telegram=False,
        min_budget_usd=1000,
    )
    followed = FollowedClient(
        user_id=1,
        profile_url="https://mostaql.com/u/client",
        label="العميل السابق",
    )
    job = Job(
        id=1,
        title="مشروع تصميم جديد",
        url="https://mostaql.com/project/200",
        content_hash="hash",
        category_id=2,
        budget_min_usd=50,
        budget_max_usd=100,
        client_profile_url="https://mostaql.com/u/client",
    )
    session.add_all([user, followed, job, UserCategory(user_id=1, category_id=2)])
    session.commit()

    emails = []
    monkeypatch.setattr(notifier, "SessionLocal", sessionmaker(bind=session.get_bind()))
    monkeypatch.setattr(notifier.email_task_queue, "enqueue", emails.append)

    result = notifier.process_new_jobs([job], category_id=2)

    assert result["notifications"] == 1
    assert session.query(Notification).count() == 1
    assert len(emails) == 1
    assert emails[0].category_name.endswith("عميل تتابعه")
    assert emails[0].jobs[0]["followed_client"] == "العميل السابق"


def test_followed_client_does_not_match_a_different_profile(monkeypatch):
    session = make_session()
    user = User(
        id=1,
        email="past-client@example.com",
        token="token",
        verified=True,
        unsubscribed=False,
        receive_email=True,
        receive_telegram=False,
    )
    session.add_all(
        [
            user,
            FollowedClient(user_id=1, profile_url="https://mostaql.com/u/client"),
            Job(
                id=1,
                title="مشروع آخر",
                url="https://mostaql.com/project/201",
                content_hash="hash",
                category_id=1,
                client_profile_url="https://mostaql.com/u/other-client",
            ),
        ]
    )
    session.commit()

    emails = []
    monkeypatch.setattr(notifier, "SessionLocal", sessionmaker(bind=session.get_bind()))
    monkeypatch.setattr(notifier.email_task_queue, "enqueue", emails.append)

    result = notifier.process_new_jobs([session.query(Job).first()], category_id=1)

    assert result["notifications"] == 0
    assert emails == []


def test_followed_client_matching_ignores_profile_subpages_and_host_casing(monkeypatch):
    session = make_session()
    user = User(
        id=1,
        email="past-client@example.com",
        token="token",
        verified=True,
        unsubscribed=False,
        receive_email=True,
        receive_telegram=False,
    )
    session.add_all(
        [
            user,
            FollowedClient(user_id=1, profile_url="https://mostaql.com/u/Client"),
            Job(
                id=1,
                title="مشروع جديد من ملف خاص",
                url="https://mostaql.com/project/202",
                content_hash="hash",
                category_id=1,
                # This is the shape Mostaql can expose from a project page;
                # it may return 403 when opened directly but still identifies
                # the owner unambiguously.
                client_profile_url="https://www.mostaql.com/u/client/notes",
            ),
        ]
    )
    session.commit()

    emails = []
    monkeypatch.setattr(notifier, "SessionLocal", sessionmaker(bind=session.get_bind()))
    monkeypatch.setattr(notifier.email_task_queue, "enqueue", emails.append)

    result = notifier.process_new_jobs([session.query(Job).first()], category_id=1)

    assert result["notifications"] == 1
    assert len(emails) == 1
    assert emails[0].jobs[0]["followed_client"] == "https://mostaql.com/u/Client"
