from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.services import scraper
from backend.services.scraper import (
    RateLimitError,
    VerificationDetails,
    _get_cached_client_verification,
    parse_client_verification,
    parse_project_details,
)


PROJECT_HTML = """
<html>
  <div class="meta-row">
    <div class="meta-label">الميزانية</div>
    <div class="meta-value"><span>$١٠٠٫٠٠ - $٢٥٠٫٠٠</span></div>
  </div>
  <div class="meta-row">
    <div class="meta-label">تاريخ النشر</div>
    <div class="meta-value">
      <time itemprop="datePublished" datetime="2026-07-27 08:30:00">منذ ٣٠ دقيقة</time>
    </div>
  </div>
  <div data-type="employer_widget">
    <a href="/u/trusted-client">العميل</a>
    <table>
      <tr><td>التواصلات الجارية</td><td>١</td></tr>
      <tr><td>معدل التوظيف</td><td><label>٤٨٫٤٨%</label></td></tr>
      <tr><td>مشاريع قيد التنفيذ</td><td>٠</td></tr>
    </table>
    <span title="هوية موثقة"></span>
  </div>
</html>
"""


def test_parse_project_details_is_label_driven_and_supports_arabic_digits():
    details = parse_project_details(PROJECT_HTML)

    assert details.hiring_rate == pytest.approx(48.48)
    assert details.budget_min_usd == 100
    assert details.budget_max_usd == 250
    assert details.published_at == datetime(2026, 7, 27, 8, 30)
    assert details.projects_in_progress == 0
    assert details.ongoing_communications == 1
    assert details.client_profile_url == "https://mostaql.com/u/trusted-client"
    assert details.client_identity_verified is True


def test_parse_relative_publication_time_when_datetime_attribute_is_missing():
    now = datetime(2026, 7, 27, 12, 0)
    html = """
    <div class="meta-row">
      <div class="meta-label">تاريخ النشر</div>
      <div class="meta-value"><time itemprop="datePublished">منذ ساعتين</time></div>
    </div>
    """

    details = parse_project_details(html, now=now)

    assert details.published_at == now - timedelta(hours=2)


def test_missing_widgets_and_uncalculated_rate_remain_unknown():
    details = parse_project_details("""
    <div data-type="employer_widget">
      <table>
        <tr><td>معدل التوظيف</td><td>لم يحسب بعد</td></tr>
        <tr><td>مشاريع قيد التنفيذ</td><td>3</td></tr>
        <tr><td>التواصلات الجارية</td><td>2</td></tr>
      </table>
    </div>
    """)

    assert details.hiring_rate is None
    assert details.projects_in_progress == 3
    assert details.ongoing_communications == 2
    assert details.budget_max_usd is None

    missing_widget = parse_project_details("<div>لا توجد بيانات عميل</div>")
    assert missing_widget.hiring_rate is None
    assert missing_widget.projects_in_progress is None
    assert missing_widget.ongoing_communications is None


def test_parse_client_identity_and_payment_verification():
    html = """
    <div class="panel">
      <h4>توثيقات</h4>
      <table>
        <tr>
          <td><i class="fa text-success fa-check"></i> الهوية الشخصية</td>
          <td><i class="fa text-success fa-check"></i> وسيلة الدفع</td>
        </tr>
      </table>
    </div>
    """

    verification = parse_client_verification(html)

    assert verification.identity_verified is True
    assert verification.payment_verified is True


def test_client_verification_cache_reuses_fresh_profile_result():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    calls = []

    def fetcher(url):
        calls.append(url)
        return VerificationDetails(identity_verified=True, payment_verified=False)

    now = datetime(2026, 7, 27, 10, 0)
    first = _get_cached_client_verification(
        session, "https://mostaql.com/u/client", now, fetcher=fetcher
    )
    second = _get_cached_client_verification(
        session,
        "https://mostaql.com/u/client",
        now + timedelta(hours=1),
        fetcher=fetcher,
    )

    assert first[:2] == (True, False)
    assert second[:2] == (True, False)
    assert calls == ["https://mostaql.com/u/client"]


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code
        self.content = b""
        self.text = ""
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


def test_rate_limit_is_preserved_for_retry_layer(monkeypatch):
    monkeypatch.setattr(scraper.requests, "get", lambda *args, **kwargs: _Response(429))

    with pytest.raises(RateLimitError):
        scraper.extract_project_details("https://mostaql.com/project/1")


def test_non_success_response_returns_unknown_details(monkeypatch):
    monkeypatch.setattr(scraper.requests, "get", lambda *args, **kwargs: _Response(500))

    details = scraper.extract_project_details("https://mostaql.com/project/1")

    assert details.hiring_rate is None
    assert details.budget_max_usd is None
