from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from backend.database import Category, FollowedClient, Job, Notification, User, UserCategory
from backend.services import notifier
from backend.services.text import normalize, passes_word_filters, split_words


def test_normalize_ignores_case_diacritics_tatweel_and_letter_variants():
    assert normalize("إِعْلَانٌ") == normalize("اعلان") == "اعلان"
    assert normalize("آلة أداة إدارة") == "اله اداه اداره"
    assert normalize("مَدْرَسَة") == normalize("مدرسه")
    assert normalize("عـــلى") == normalize("علي") == "علي"
    assert normalize("Python DJANGO") == "python django"


def test_a_word_list_takes_newlines_commas_and_arabic_commas():
    assert split_words("بايثون، Django\nreact,  ,\n") == ["بايثون", "django", "react"]
    assert split_words("ـــ, ") == []  # nothing left after normalising: it must not match everything
    assert split_words(None) == [] and split_words("") == []


@pytest.mark.parametrize(
    ("title", "include", "exclude", "expected"),
    [
        ("مطلوب مبرمج Python", None, None, True),
        ("مطلوب مبرمج Python", "python", None, True),
        ("مطلوب مبرمج Python", "java, php", None, False),
        ("مطلوب مبرمج Python", "java\npython", None, True),
        ("ترجمة إعلانات", "اعلان", None, True),       # أ/إ/آ -> ا
        ("مدرسة", "مدرسه", None, True),               # ة -> ه
        ("حروف على", "علي", None, True),              # ى -> ي
        ("مَدْرَسَة", "مدرسة", None, True),             # tashkeel
        ("مطلوب مبرمج Python", None, "python", False),
        ("مطلوب مبرمج Python", None, "java, php", True),
        ("مطلوب مبرمج Python", "مبرمج", "python", False),  # exclude wins
        ("مطلوب مبرمج Python", "ـــ", "ـ,", True),      # empty lists after normalising: no filter
    ],
)
def test_include_and_exclude_words(title, include, exclude, expected):
    assert passes_word_filters(title, include, exclude) is expected


def test_users_without_words_see_everything():
    user = SimpleNamespace(
        min_hiring_rate=None,
        require_projects_in_progress=False,
        require_ongoing_communications=False,
        min_budget_usd=None,
        require_verified_client=False,
        max_project_age_minutes=None,
        keywords_include=None,
        keywords_exclude=None,
    )
    job = SimpleNamespace(title="أي مشروع", published_at=datetime(2026, 1, 1))

    assert notifier._job_matches_user(user, job)
    user.keywords_exclude = "أي"
    assert not notifier._job_matches_user(user, job)


@pytest.fixture
def recorded(db_session, monkeypatch):
    """Record matches for one user; returns the titles that got a pending row."""
    monkeypatch.setattr(notifier, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

    def run(titles_by_client, **user_fields):
        db_session.add(Category(id=1, name="برمجة", mostaql_url="https://mostaql.com/projects"))
        db_session.add(
            User(
                id=1, email="me@example.com", token="tok", verified=True, unsubscribed=False,
                receive_email=True, receive_telegram=False, **user_fields,
            )
        )
        db_session.add_all([
            UserCategory(user_id=1, category_id=1),
            FollowedClient(user_id=1, profile_url="https://mostaql.com/u/client"),
        ])
        jobs = [
            Job(
                id=n, title=title, url=f"https://mostaql.com/project/{n}", content_hash=str(n),
                category_id=1, client_profile_url="https://mostaql.com/u/client" if followed else None,
            )
            for n, (title, followed) in enumerate(titles_by_client, start=1)
        ]
        db_session.add_all(jobs)
        db_session.commit()
        notifier.process_new_jobs(jobs, 1)
        return {row.job.title for row in db_session.query(Notification).all()}

    return run


def test_alerts_follow_the_include_and_exclude_words(recorded):
    titles = recorded(
        [("مطلوب مبرمج Python", False), ("مطلوب مصمم", False), ("مبرمج Python إعلان", False)],
        keywords_include="python, مبرمج",
        keywords_exclude="اعلان",
    )

    assert titles == {"مطلوب مبرمج Python"}


def test_past_client_projects_skip_the_word_filters(recorded):
    titles = recorded(
        [("مشروع ممنوع", True), ("مشروع ممنوع آخر", False)],
        keywords_exclude="ممنوع",
    )

    assert titles == {"مشروع ممنوع"}
