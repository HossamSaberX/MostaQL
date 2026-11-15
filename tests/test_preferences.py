import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api.verify import get_preferences, update_preferences
from backend.database import Base, Category, User
from backend.models import PreferencesRequest
from backend.services.subscription_service import SubscriptionService


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Category(id=1, name="برمجة", mostaql_url="https://mostaql.com/projects"))
    session.commit()
    return session


def response_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_subscription_persists_smart_preferences_and_a_second_signup_changes_nothing():
    session = make_session()
    service = SubscriptionService(session)

    created = service.subscribe(
        "smart@example.com",
        [1],
        min_hiring_rate=40,
        require_projects_in_progress=True,
        require_ongoing_communications=True,
        min_budget_usd=250,
        require_verified_client=True,
        max_project_age_minutes=15,
    )

    assert created.user.require_projects_in_progress is True
    assert created.user.require_ongoing_communications is True
    assert created.user.min_budget_usd == 250
    assert created.user.require_verified_client is True
    assert created.user.max_project_age_minutes == 15

    again = service.subscribe("smart@example.com", [1])

    assert created.created is True and again.created is False
    assert again.user.require_projects_in_progress is True
    assert again.user.min_budget_usd == 250
    assert again.user.max_project_age_minutes == 15


def test_preferences_api_round_trip_includes_new_fields():
    session = make_session()
    user = User(
        email="prefs@example.com",
        token="token",
        verified=True,
        unsubscribed=False,
    )
    session.add(user)
    session.commit()

    request = PreferencesRequest(
        token="token",
        receive_email=True,
        receive_telegram=False,
        min_hiring_rate=55,
        require_projects_in_progress=True,
        require_ongoing_communications=True,
        min_budget_usd=500,
        require_verified_client=True,
        max_project_age_minutes=10,
    )

    update_response = asyncio.run(update_preferences(request, db=session))
    get_response = asyncio.run(get_preferences("token", db=session))
    update_data = response_json(update_response)
    get_data = response_json(get_response)

    assert update_data["min_budget_usd"] == 500
    assert get_data["require_projects_in_progress"] is True
    assert get_data["require_ongoing_communications"] is True
    assert get_data["require_verified_client"] is True
    assert get_data["max_project_age_minutes"] == 10
