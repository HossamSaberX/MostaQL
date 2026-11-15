import smtplib

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api import subscribe, verify, webhook
from backend.config import settings
from backend.database import Base, get_db
from backend.utils.limiter import limiter


@pytest.fixture(autouse=True)
def safe_settings_and_no_network(monkeypatch):
    """The local .env may hold real credentials: fix the settings and block real sends."""
    monkeypatch.setattr(settings, "base_url", "https://alerts.test")
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_webhook_secret", None)

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to use the network")

    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(smtplib, "SMTP", refuse)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    """The subscribe, verify and webhook routers on a small app, backed by the test database."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(subscribe.router, prefix="/api")
    app.include_router(verify.router, prefix="/api")
    app.include_router(webhook.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    limiter.reset()
    yield TestClient(app)
    limiter.reset()
