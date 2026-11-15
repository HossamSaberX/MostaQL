import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.api import verify
from backend.config import settings
from backend.database import User, get_db

ONE_CLICK = {"List-Unsubscribe": "One-Click"}


def add_user(session, **overrides):
    values = {"id": 1, "email": "me@example.com", "token": "tok", "verified": True}
    values.update(overrides)
    session.add(User(**values))
    session.commit()


def reload_user(session):
    session.expire_all()
    return session.get(User, 1)


# ---- one-click email unsubscribe (RFC 8058) ----------------------------------------------


def test_one_click_post_stops_email_and_the_subscription_when_nothing_else_is_left(client, db_session):
    add_user(db_session)

    response = client.post("/api/unsubscribe/tok/email", data=ONE_CLICK)

    assert response.status_code == 200
    assert response.json()["unsubscribed"] is True
    user = reload_user(db_session)
    assert user.receive_email is False
    assert user.unsubscribed is True


def test_one_click_keeps_the_subscription_alive_for_linked_telegram(client, db_session):
    add_user(db_session, telegram_chat_id="42", receive_telegram=True)

    response = client.post("/api/unsubscribe/tok/email", data=ONE_CLICK)

    assert response.status_code == 200
    assert "تيليجرام" in response.json()["message"]
    user = reload_user(db_session)
    assert user.receive_email is False
    assert user.unsubscribed is False


def test_one_click_ends_the_subscription_when_telegram_is_linked_but_off(client, db_session):
    add_user(db_session, telegram_chat_id="42", receive_telegram=False)

    client.post("/api/unsubscribe/tok/email", data=ONE_CLICK)

    assert reload_user(db_session).unsubscribed is True


def test_one_click_can_be_repeated_and_needs_no_body(client, db_session):
    add_user(db_session)

    first = client.post("/api/unsubscribe/tok/email", data=ONE_CLICK)
    second = client.post("/api/unsubscribe/tok/email")

    assert first.status_code == second.status_code == 200
    assert reload_user(db_session).unsubscribed is True


def test_one_click_with_an_unknown_token_is_a_404(client, db_session):
    add_user(db_session)

    response = client.post("/api/unsubscribe/nope/email", data=ONE_CLICK)

    assert response.status_code == 404
    assert reload_user(db_session).receive_email is True


def test_opening_the_link_changes_nothing_and_shows_a_confirm_button(client, db_session):
    add_user(db_session)

    response = client.get("/api/unsubscribe/tok/email")

    assert response.status_code == 200
    assert 'method="post"' in response.text
    assert "إلغاء الاشتراك من البريد" in response.text
    user = reload_user(db_session)
    assert user.receive_email is True and user.unsubscribed is False


def test_browser_form_post_gets_a_page_instead_of_json(client, db_session):
    add_user(db_session)

    response = client.post(
        "/api/unsubscribe/tok/email", data=ONE_CLICK, headers={"Accept": "text/html"}
    )

    assert response.headers["content-type"].startswith("text/html")
    assert "تم الإلغاء" in response.text
    assert reload_user(db_session).unsubscribed is True


# ---- the unsubscribe-request form does not reveal who is registered --------------------------


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(verify, "send_unsubscribe_email", lambda email, token: sent.append((email, token)))
    return sent


def test_known_unknown_and_unsubscribed_addresses_get_the_same_answer(client, db_session, mails):
    add_user(db_session, id=1, email="known@example.com", token="a")
    add_user(db_session, id=2, email="gone@example.com", token="b", unsubscribed=True)

    answers = [
        client.post("/api/unsubscribe-request", json={"email": email})
        for email in ("known@example.com", "nobody@example.com", "gone@example.com")
    ]

    assert {response.status_code for response in answers} == {200}
    assert len({response.text for response in answers}) == 1
    assert answers[0].json()["message"] == verify.UNSUBSCRIBE_REQUEST_REPLY


def test_the_link_is_mailed_to_every_registered_address_paused_ones_too(client, db_session, mails):
    add_user(db_session, id=1, email="known@example.com", token="a")
    add_user(db_session, id=2, email="gone@example.com", token="b", unsubscribed=True)

    replies = {
        client.post("/api/unsubscribe-request", json={"email": email}).text
        for email in ("known@example.com", "nobody@example.com", "gone@example.com")
    }

    assert mails == [("known@example.com", "a"), ("gone@example.com", "b")]
    assert len(replies) == 1  # the same words for every address


def test_unsubscribe_request_is_rate_limited(client, db_session, mails):
    for _ in range(settings.rate_limit_per_hour):
        assert client.post("/api/unsubscribe-request", json={"email": "a@example.com"}).status_code == 200

    assert client.post("/api/unsubscribe-request", json={"email": "a@example.com"}).status_code == 429


# ---- Arabic validation errors ---------------------------------------------------------------


def test_validation_errors_are_arabic_and_keep_the_field_details(db_session):
    main.app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(main.app).post("/api/subscribe", json={"email": "not-an-email", "category_ids": []})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "تحقق من البيانات المدخلة"
    assert {"email", "category_ids"} <= {error["loc"][-1] for error in body["errors"]}
