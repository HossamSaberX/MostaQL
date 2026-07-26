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
