import os
import subprocess
import sys

from sqlalchemy import create_engine, inspect


def test_upgrade_head_adds_smart_alert_schema(tmp_path):
    database_path = tmp_path / "migration.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from alembic.config import Config; from alembic import command; "
            "command.upgrade(Config('alembic.ini'), 'head')",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    inspector = inspect(create_engine(env["DATABASE_URL"]))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}

    assert {
        "require_projects_in_progress",
        "require_ongoing_communications",
        "min_budget_usd",
        "require_verified_client",
        "max_project_age_minutes",
    } <= user_columns
    assert {
        "budget_min_usd",
        "budget_max_usd",
        "published_at",
        "projects_in_progress",
        "ongoing_communications",
    } <= job_columns
    assert "client_verification_cache" in inspector.get_table_names()
    assert "followed_clients" in inspector.get_table_names()
    followed_columns = {column["name"] for column in inspector.get_columns("followed_clients")}
    assert {"user_id", "profile_url", "label", "created_at"} <= followed_columns


def test_upgrade_preserves_existing_users_with_disabled_filter_defaults(tmp_path):
    database_path = tmp_path / "existing-user.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    config_command = (
        "from alembic.config import Config; from alembic import command; "
        "command.upgrade(Config('alembic.ini'), '{}')"
    )

    previous = subprocess.run(
        [sys.executable, "-c", config_command.format("cffc3bde7727")],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert previous.returncode == 0, previous.stdout + previous.stderr

    engine = create_engine(env["DATABASE_URL"])
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users (email, token, verified, unsubscribed) "
            "VALUES ('existing@example.com', 'existing-token', 1, 0)"
        )

    head = subprocess.run(
        [sys.executable, "-c", config_command.format("head")],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert head.returncode == 0, head.stdout + head.stderr

    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT require_projects_in_progress, require_ongoing_communications, "
            "require_verified_client FROM users WHERE email = 'existing@example.com'"
        ).one()

    assert tuple(row) == (0, 0, 0)


PREVIOUS_HEAD = "b7c8d9e0f1a2"


def _upgrade(env, revision):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from alembic.config import Config; from alembic import command; "
            f"command.upgrade(Config('alembic.ini'), '{revision}')",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_upgrade_expires_the_whole_old_pending_backlog_and_a_flush_sends_nothing(tmp_path, monkeypatch):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'backlog.db').as_posix()}"
    _upgrade(env, PREVIOUS_HEAD)

    engine = create_engine(env["DATABASE_URL"])
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO categories (id, name, mostaql_url) VALUES (1, 'برمجة', 'https://mostaql.com/projects')"
        )
        # Fully eligible for both channels, so only the expiry stops a send.
        connection.exec_driver_sql(
            "INSERT INTO users (id, email, token, verified, unsubscribed, receive_email, "
            "receive_telegram, telegram_chat_id) VALUES (1, 'old@example.com', 'tok', 1, 0, 1, 1, '42')"
        )
        connection.exec_driver_sql(
            "INSERT INTO jobs (id, title, url, content_hash, category_id) "
            "VALUES (1, 'مشروع قديم', 'https://mostaql.com/project/1', 'hash', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO notifications (id, user_id, job_id, status, channel, sent_at) VALUES "
            "(1, 1, 1, 'pending', NULL, datetime('now')), "
            "(2, 1, 1, 'pending', 'email', datetime('now')), "
            "(3, 1, 1, 'pending', 'telegram', datetime('now')), "
            "(4, 1, 1, 'sent', NULL, datetime('now')), "
            "(5, 1, 1, 'sent', 'email', datetime('now'))"
        )

    _upgrade(env, "head")

    inspector = inspect(engine)
    assert "created_at" in {column["name"] for column in inspector.get_columns("notifications")}
    assert {"email_delivery", "keywords_include", "keywords_exclude", "telegram_link_nonce", "verify_token"} <= {
        column["name"] for column in inspector.get_columns("users")
    }
    assert any(
        index["column_names"] == ["verify_token"] and index["unique"] for index in inspector.get_indexes("users")
    )
    with engine.connect() as connection:
        statuses = dict(connection.exec_driver_sql("SELECT id, status FROM notifications").all())
        user = connection.exec_driver_sql(
            "SELECT email_delivery, keywords_include, keywords_exclude, telegram_link_nonce, verify_token FROM users"
        ).one()
    assert statuses == {1: "expired", 2: "expired", 3: "expired", 4: "sent", 5: "sent"}
    assert tuple(user) == ("instant", None, None, None, None)

    from sqlalchemy.orm import sessionmaker

    from backend.services import notifier

    sent = []
    monkeypatch.setattr(notifier, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(notifier.email_task_queue, "enqueue", sent.append)
    monkeypatch.setattr(notifier.telegram_task_queue, "enqueue", sent.append)

    # Even a no-channel row that is pending again is never read by the flush.
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE notifications SET status = 'pending' WHERE id = 1")
    result = notifier.flush_notifications()
    assert sent == []
    assert (result["queued_emails"], result["queued_telegram"], result["expired"]) == (0, 0, 0)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT status FROM notifications WHERE id = 1").scalar() == "pending"

    # Control: a row recorded after the upgrade does go out (Telegram: the email above is in its
    # ten-minute wait), so the check above is not vacuous.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO notifications (user_id, job_id, status, channel, created_at) "
            "VALUES (1, 1, 'pending', 'telegram', datetime('now'))"
        )
    notifier.flush_notifications()
    assert len(sent) == 1 and sent[0].chat_id == "42"


def test_downgrade_removes_the_new_columns(tmp_path):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'downgrade.db').as_posix()}"
    _upgrade(env, "head")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from alembic.config import Config; from alembic import command; "
            f"command.downgrade(Config('alembic.ini'), '{PREVIOUS_HEAD}')",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    inspector = inspect(create_engine(env["DATABASE_URL"]))
    assert "created_at" not in {column["name"] for column in inspector.get_columns("notifications")}
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert not {"email_delivery", "keywords_include", "keywords_exclude", "telegram_link_nonce", "verify_token"} & user_columns
