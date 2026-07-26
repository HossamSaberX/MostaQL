from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.models import SubscribeRequest
from backend.services.email.templates import get_job_notifications_html
from backend.services.notifier import (
    _build_job_payload,
    _filter_jobs_for_user,
    _job_matches_user,
    _telegram_job_html,
)


def make_user(**overrides):
    values = {
        "min_hiring_rate": None,
        "require_projects_in_progress": False,
        "require_ongoing_communications": False,
        "min_budget_usd": None,
        "require_verified_client": False,
        "max_project_age_minutes": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_job(**overrides):
    values = {
        "title": "مشروع جديد",
        "url": "https://mostaql.com/project/1",
        "hiring_rate": 60.0,
        "budget_min_usd": 100.0,
        "budget_max_usd": 250.0,
        "published_at": datetime(2026, 7, 27, 11, 55),
        "projects_in_progress": 1,
        "ongoing_communications": 2,
        "client_identity_verified": True,
        "client_payment_verified": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


NOW = datetime(2026, 7, 27, 12, 0)


def test_disabled_filters_preserve_existing_behavior():
    assert _job_matches_user(make_user(), make_job(), NOW)


@pytest.mark.parametrize(
    ("user_overrides", "job_overrides"),
    [
        ({"min_hiring_rate": 70}, {}),
        ({"require_projects_in_progress": True}, {"projects_in_progress": 0}),
        ({"require_ongoing_communications": True}, {"ongoing_communications": 0}),
        ({"min_budget_usd": 251}, {}),
        (
            {"require_verified_client": True},
            {"client_identity_verified": None, "client_payment_verified": None},
        ),
        (
            {"max_project_age_minutes": 5},
            {"published_at": NOW - timedelta(minutes=6)},
        ),
    ],
)
def test_each_enabled_filter_rejects_non_matching_or_unknown_values(
    user_overrides, job_overrides
):
    assert not _job_matches_user(
        make_user(**user_overrides), make_job(**job_overrides), NOW
    )


def test_budget_filter_compares_the_upper_end_and_boundaries_are_inclusive():
    user = make_user(min_budget_usd=250)

    assert _job_matches_user(user, make_job(budget_min_usd=100, budget_max_usd=250), NOW)
    assert not _job_matches_user(user, make_job(budget_min_usd=100, budget_max_usd=249), NOW)


def test_all_enabled_filters_combine_with_and():
    user = make_user(
        min_hiring_rate=50,
        require_projects_in_progress=True,
        require_ongoing_communications=True,
        min_budget_usd=200,
        require_verified_client=True,
        max_project_age_minutes=10,
    )
    matching = make_job()
    rejected = make_job(ongoing_communications=None)

    assert _filter_jobs_for_user(user, [matching, rejected], NOW) == [matching]


def test_request_defaults_are_backward_compatible_and_validation_is_strict():
    request = SubscribeRequest(email="a@example.com", category_ids=[1])

    assert request.require_projects_in_progress is False
    assert request.require_ongoing_communications is False
    assert request.min_budget_usd is None
    assert request.require_verified_client is False
    assert request.max_project_age_minutes is None

    with pytest.raises(ValueError):
        SubscribeRequest(
            email="a@example.com",
            category_ids=[1],
            min_budget_usd=-1,
        )


def test_notification_payload_and_email_explain_why_the_job_matched():
    payload = _build_job_payload(make_job(), NOW)
    html = get_job_notifications_html(
        "برمجة",
        [payload],
        "https://example.com/unsubscribe",
    )

    assert payload["budget"] == "$100 - $250"
    assert payload["project_age_minutes"] == 5
    assert "مشاريع قيد التنفيذ" in html
    assert "التواصلات الجارية" in html
    assert "راجع المشروع وتقدّم الآن" in html

    telegram_html = _telegram_job_html(payload)
    assert "الميزانية" in telegram_html
    assert "التوثيق" in telegram_html
    assert "عمر المشروع" in telegram_html
    assert 'href="https://mostaql.com/project/1"' in telegram_html
