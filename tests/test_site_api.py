"""The API behind the home and preferences pages: settings, categories, word lists, Telegram link."""
from datetime import datetime, timedelta

import pytest

from backend.api import subscribe, verify
from backend.config import settings
from backend.database import Category, Job, Notification, User, UserCategory
from backend.services.subscription_service import (
    MAX_WORDS,
    SubscriptionError,
    clean_word_list,
)
from backend.utils.security import telegram_link_code_user_id, verify_telegram_link_code


@pytest.fixture(autouse=True)
def bot_and_no_mail(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_username", "alerts_bot")
    monkeypatch.setattr(settings, "max_categories_per_user", 3)
    sent = []
    monkeypatch.setattr(subscribe, "send_verification_email", lambda email, token: sent.append(email))
    monkeypatch.setattr(subscribe, "send_unsubscribe_email", lambda email, token: sent.append(("manage", email)))
    return sent


@pytest.fixture
def world(db_session):
    for category_id in (1, 2, 3, 4):
        db_session.add(Category(id=category_id, name=f"تصنيف {category_id}", mostaql_url="https://mostaql.com/projects"))
    db_session.add(User(id=1, email="me@example.com", token="tok", verified=True))
    db_session.add(UserCategory(user_id=1, category_id=1))
    db_session.commit()
    return db_session


def reload_user(session):
    session.expire_all()
    return session.get(User, 1)


def save(client, **changes):
    body = {"token": "tok", "receive_email": True, "receive_telegram": False, "category_ids": [1]}
    body.update(changes)
    return client.post("/api/preferences", json=body)


def link_code(url):
    """The account a deep link may connect, or None when its code does not verify."""
    code = url.split("start=", 1)[1]
    user_id = telegram_link_code_user_id(code)
    return user_id if verify_telegram_link_code(code, user_id, None) else None


# ---- preferences: reading ------------------------------------------------------------------


def test_preferences_show_everything_the_page_needs(client, world):
    response = client.get("/api/preferences/tok")

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "me@example.com"
    assert data["category_ids"] == [1]
    assert data["email_delivery"] == "instant"
    assert data["keywords_include"] == "" and data["keywords_exclude"] == ""
    assert data["telegram_linked"] is False
    assert link_code(data["telegram_link"]) == 1
    assert data["sent_this_week"] == 0
    assert data["sent_this_week_text"] == "لم نرسل لك مشاريع هذا الأسبوع بعد."


def test_a_linked_telegram_needs_no_link(client, world):
    reload_user(world).telegram_chat_id = "42"
    world.commit()

    data = client.get("/api/preferences/tok").json()

    assert data["telegram_linked"] is True
    assert data["telegram_link"] is None


def test_sent_this_week_counts_each_sent_project_once(client, world):
    now = datetime.utcnow()
    for job_id in (1, 2, 3):
        world.add(Job(id=job_id, title=f"مشروع {job_id}", url=f"https://mostaql.com/p/{job_id}", content_hash=str(job_id), category_id=1))
    world.add_all([
        Notification(user_id=1, job_id=1, channel="email", status="sent", sent_at=now - timedelta(days=1)),
        Notification(user_id=1, job_id=1, channel="telegram", status="sent", sent_at=now - timedelta(days=1)),
        Notification(user_id=1, job_id=2, channel="email", status="sent", sent_at=now - timedelta(days=2)),
        Notification(user_id=1, job_id=3, channel="email", status="sent", sent_at=now - timedelta(days=8)),
        Notification(user_id=1, job_id=3, channel="telegram", status="failed", sent_at=now),
    ])
    world.commit()

    data = client.get("/api/preferences/tok").json()

    assert data["sent_this_week"] == 2
    assert data["sent_this_week_text"] == "أُرسل لك مشروعان جديدان هذا الأسبوع."


def test_sent_this_week_wording():
    assert verify.sent_this_week_text(1) == "أُرسل لك مشروع جديد واحد هذا الأسبوع."
    assert verify.sent_this_week_text(5) == "أُرسل لك 5 مشاريع جديدة هذا الأسبوع."
    assert verify.sent_this_week_text(12) == "أُرسل لك 12 مشروعًا جديدًا هذا الأسبوع."


def test_an_unknown_token_is_a_404_in_arabic(client, world):
    response = client.get("/api/preferences/nope")

    assert response.status_code == 404
    assert response.json()["detail"] == verify.INVALID_LINK


# ---- preferences: saving -------------------------------------------------------------------


def test_saving_changes_categories_timing_and_word_lists(client, world):
    response = save(
        client,
        category_ids=[3, 2, 2],
        email_delivery="daily",
        keywords_include="  شعار \nلوجو\nشعار\n\nهوية بصرية، ",
        keywords_exclude="ترجمة",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "حُفظت التغييرات."
    assert data["category_ids"] == [2, 3]
    assert data["keywords_include"] == "شعار\nلوجو\nهوية بصرية"
    user = reload_user(world)
    assert user.email_delivery == "daily"
    assert user.keywords_exclude == "ترجمة"
    assert sorted(link.category_id for link in user.categories) == [2, 3]


def test_leaving_out_categories_keeps_them(client, world):
    body = {"token": "tok", "receive_email": True, "receive_telegram": False}

    assert client.post("/api/preferences", json=body).status_code == 200
    assert [link.category_id for link in reload_user(world).categories] == [1]


def test_empty_word_lists_are_stored_as_none(client, world):
    save(client, keywords_include="\n ، \n", keywords_exclude="")

    user = reload_user(world)
    assert user.keywords_include is None and user.keywords_exclude is None


@pytest.mark.parametrize(
    "changes, words",
    [
        ({"category_ids": []}, "اختر تصنيفًا"),
        ({"category_ids": [1, 2, 3, 4]}, "3 تصنيفات على الأكثر"),
        ({"category_ids": [1, 99]}, "حدّث الصفحة"),
        ({"receive_email": False, "receive_telegram": False}, "طريقة واحدة على الأقل"),
        ({"keywords_include": "\n".join(f"كلمة{n}" for n in range(MAX_WORDS + 1))}, f"{MAX_WORDS} كلمة على الأكثر"),
        ({"keywords_exclude": "ا" * 41}, "اختصر"),
    ],
)
def test_bad_choices_get_an_arabic_error_that_says_what_to_fix(client, world, changes, words):
    response = save(client, **changes)

    assert response.status_code == 400
    assert words in response.json()["detail"]
    assert [link.category_id for link in reload_user(world).categories] == [1]  # nothing half-saved


def test_an_unknown_email_timing_is_rejected(client, world):
    assert save(client, email_delivery="weekly").status_code == 422


def test_word_list_dedupe_ignores_letter_variants():
    assert clean_word_list("أحمد\nاحمد\nAPI\napi", "x") == "أحمد\nAPI"
    with pytest.raises(SubscriptionError):
        clean_word_list("\n".join(str(n) for n in range(MAX_WORDS + 1)), "x")


def test_a_paused_account_can_still_save(client, world):
    reload_user(world).unsubscribed = True
    world.commit()

    assert save(client).status_code == 200
    assert reload_user(world).unsubscribed is True


# ---- turning alerts back on ----------------------------------------------------------------


def test_resubscribe_turns_alerts_back_on(client, world):
    user = reload_user(world)
    user.unsubscribed = True
    user.telegram_chat_id = "42"
    user.receive_email = False
    world.commit()

    response = client.post("/api/resubscribe/tok")

    assert response.status_code == 200
    assert response.json()["unsubscribed"] is False
    user = reload_user(world)
    assert user.unsubscribed is False
    assert user.receive_email is False  # Telegram still delivers


def test_resubscribe_leaves_a_channel_that_works(client, world):
    # The email footer's one-click unsubscribe turns email off and stops everything.
    client.post("/api/unsubscribe/tok/email")

    client.post("/api/resubscribe/tok")

    user = reload_user(world)
    assert user.unsubscribed is False
    assert user.receive_email is True


def test_resubscribe_with_an_unknown_token_is_a_404(client, world):
    assert client.post("/api/resubscribe/nope").status_code == 404


def test_stopping_returns_the_new_state(client, world):
    response = client.post("/api/unsubscribe/tok")

    assert response.status_code == 200
    assert response.json()["unsubscribed"] is True


# ---- subscribing ---------------------------------------------------------------------------


def test_a_new_signup_gets_a_telegram_link_and_never_the_account_token(client, world, bot_and_no_mail):
    response = client.post(
        "/api/subscribe",
        json={
            "email": "new@example.com",
            "category_ids": [2],
            "email_delivery": "hourly",
            "keywords_include": "شعار\nشعار",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "created"
    assert "token" not in data
    user = world.query(User).filter_by(email="new@example.com").one()
    assert link_code(data["telegram_link"]) == user.id
    assert user.token not in response.text
    assert user.email_delivery == "hourly"
    assert user.keywords_include == "شعار"
    assert bot_and_no_mail == ["new@example.com"]


def test_no_telegram_link_when_telegram_is_not_chosen(client, world):
    data = client.post(
        "/api/subscribe",
        json={"email": "new@example.com", "category_ids": [2], "receive_telegram": False},
    ).json()

    assert data["telegram_link"] is None


def test_a_confirmed_account_is_not_changed_and_gets_a_manage_link_by_email(client, world, bot_and_no_mail):
    # Anyone can type someone else's address here: nothing changes without the inbox, and
    # the reply reads like a new signup's, so the form cannot tell who has an account.
    world.get(User, 1).unsubscribed = True
    world.commit()
    payload = {"category_ids": [2], "receive_telegram": False, "min_budget_usd": 999}
    known = client.post("/api/subscribe", json={"email": "me@example.com", **payload})
    fresh = client.post("/api/subscribe", json={"email": "new@example.com", **payload})

    assert known.status_code == fresh.status_code == 200
    data = known.json()
    assert data == {**fresh.json(), "email": "me@example.com"}
    assert data["telegram_link"] is None
    assert "tok" not in known.text
    assert bot_and_no_mail == [("manage", "me@example.com"), "new@example.com"]
    world.expire_all()
    user = world.get(User, 1)
    assert [c.category_id for c in user.categories] == [1]
    assert user.unsubscribed is True
    assert user.receive_telegram is True
    assert user.min_budget_usd is None


def test_a_confirmed_account_without_telegram_gets_no_link(client, world):
    response = client.post("/api/subscribe", json={"email": "me@example.com", "category_ids": [2]})

    assert response.json()["telegram_link"] is None


def test_a_telegram_only_account_is_not_changed_either(client, world, bot_and_no_mail):
    client.post("/api/subscribe", json={"email": "new@example.com", "category_ids": [2]})
    user = world.query(User).filter_by(email="new@example.com").one()
    user.telegram_chat_id = "555"
    world.commit()
    token = user.token

    data = client.post("/api/subscribe", json={"email": "new@example.com", "category_ids": [3]}).json()

    world.expire_all()
    user = world.query(User).filter_by(email="new@example.com").one()
    assert user.token == token
    assert [c.category_id for c in user.categories] == [2]
    assert data["telegram_link"] is None
    assert bot_and_no_mail[-1] == ("manage", "new@example.com")


def test_an_unconfirmed_account_gets_a_new_confirmation_link(client, world, bot_and_no_mail):
    client.post("/api/subscribe", json={"email": "new@example.com", "category_ids": [2]})
    first_token = world.query(User).filter_by(email="new@example.com").one().token

    data = client.post("/api/subscribe", json={"email": "new@example.com", "category_ids": [3]}).json()

    world.expire_all()
    user = world.query(User).filter_by(email="new@example.com").one()
    assert user.token != first_token
    assert [c.category_id for c in user.categories] == [3]
    assert link_code(data["telegram_link"]) == user.id
    assert bot_and_no_mail == ["new@example.com", "new@example.com"]


def test_subscribe_errors_are_arabic(client, world):
    response = client.post("/api/subscribe", json={"email": "new@example.com", "category_ids": [1, 2, 3, 4]})

    assert response.status_code == 400
    assert "تصنيفات على الأكثر" in response.json()["detail"]


# ---- confirming the email ------------------------------------------------------------------


def test_confirming_shows_the_result_without_moving_the_token_on(client, world):
    world.add(User(id=2, email="new@example.com", token="fresh", verified=False, token_issued_at=datetime.utcnow()))
    world.commit()

    response = client.get("/api/verify/fresh", follow_redirects=False)

    assert response.status_code == 200
    assert "location" not in response.headers
    assert "تم تأكيد بريدك" in response.text
    assert 'content="no-referrer"' in response.text
    assert "start=fresh" not in response.text
    assert "https://t.me/alerts_bot?start=2_" in response.text
    assert world.get(User, 2).verified is True


def test_an_expired_confirmation_link_offers_a_new_one(client, world):
    world.add(User(id=2, email="old@example.com", token="old", verified=False,
                   token_issued_at=datetime.utcnow() - timedelta(hours=settings.verification_token_expiry_hours + 1)))
    world.commit()

    response = client.get("/api/verify/old")

    assert "انتهت صلاحية الرابط" in response.text
    assert 'href="/#subscribe"' in response.text
    assert world.get(User, 2).verified is False


def test_an_unknown_confirmation_link_says_so(client, world):
    response = client.get("/api/verify/nope")

    assert response.status_code == 200
    assert "الرابط غير صالح" in response.text
