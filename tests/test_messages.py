from backend.services import messages
from backend.services.messages import (
    count_projects,
    job_facts,
    job_reason,
    manage_button,
    manage_url,
    more_projects,
    order_jobs,
    valid_for_hours,
)


def test_count_projects_has_correct_arabic_agreement():
    assert count_projects(1) == "مشروع جديد واحد"
    assert count_projects(2) == "مشروعان جديدان"
    assert count_projects(3) == "3 مشاريع جديدة"
    assert count_projects(10) == "10 مشاريع جديدة"
    assert count_projects(11) == "11 مشروعًا جديدًا"
    assert count_projects(20) == "20 مشروعًا جديدًا"
    assert count_projects(0) == "لا مشاريع جديدة"


def test_counting_follows_the_hundreds_rule():
    expected = {
        1: "مشروع جديد واحد",
        2: "مشروعان جديدان",
        3: "3 مشاريع جديدة",
        10: "10 مشاريع جديدة",
        11: "11 مشروعًا جديدًا",
        99: "99 مشروعًا جديدًا",
        100: "100 مشروع جديد",
        101: "101 مشروع جديد",
        102: "102 مشروع جديد",
        103: "103 مشاريع جديدة",
        111: "111 مشروعًا جديدًا",
    }
    assert {n: count_projects(n) for n in expected} == expected
    assert more_projects(101) == "و101 مشروع آخر"
    assert valid_for_hours(100) == "هذا الرابط صالح لمدة 100 ساعة فقط"


def test_more_projects_has_correct_arabic_agreement():
    assert more_projects(1) == "ومشروع آخر"
    assert more_projects(2) == "ومشروعان آخران"
    assert more_projects(3) == "و3 مشاريع أخرى"
    assert more_projects(10) == "و10 مشاريع أخرى"
    assert more_projects(11) == "و11 مشروعًا آخر"
    assert more_projects(150) == "و150 مشروعًا آخر"  # 150 % 100 = 50: "many"


def test_validity_sentence_uses_the_given_hours():
    assert valid_for_hours(24) == "هذا الرابط صالح لمدة 24 ساعة فقط"
    assert valid_for_hours(2) == "هذا الرابط صالح لمدة ساعتين فقط"
    assert valid_for_hours(1) == "هذا الرابط صالح لمدة ساعة واحدة فقط"
    assert valid_for_hours(6) == "هذا الرابط صالح لمدة 6 ساعات فقط"


def test_job_facts_are_ordered_skip_missing_values_and_keep_zero():
    facts = job_facts(
        {
            "title": "x",
            "budget": "$100",
            "hiring_rate": None,
            "projects_in_progress": 0,
            "ongoing_communications": 3,
            "verification": "غير معروف",
            "project_age_minutes": 2,
        }
    )

    assert facts == [
        ("الميزانية", "$100"),
        ("مشاريع قيد التنفيذ", "0"),
        ("التواصلات الجارية", "3"),
        ("التوثيق", "غير معروف"),
        ("عمر المشروع", "دقيقتان"),
    ]


def test_project_age_is_counted_in_minutes_then_hours():
    def age(minutes):
        return dict(job_facts({"project_age_minutes": minutes}))["عمر المشروع"]

    assert age(0) == "أقل من دقيقة"
    assert age(1) == "دقيقة واحدة"
    assert age(5) == "5 دقائق"
    assert age(45) == "45 دقيقة"
    assert age(60) == "ساعة واحدة"
    assert age(190) == "3 ساعات"


def test_reason_prefers_the_past_client_then_the_category():
    assert job_reason({"followed_client": "أحمد", "category": "برمجة"}) == "عميل سابق تتابعه: أحمد"
    assert job_reason({"category": "برمجة"}) == "تصنيف تتابعه: برمجة"
    assert job_reason({}) is None


def test_past_client_projects_come_first_and_order_is_otherwise_kept():
    jobs = [{"id": 1}, {"id": 2, "followed_client": "x"}, {"id": 3}, {"id": 4, "followed_client": "y"}]

    assert [job["id"] for job in order_jobs(jobs)] == [2, 4, 1, 3]


def test_links_use_the_configured_site_address():
    assert manage_url("abc") == "https://alerts.test/api/unsubscribe/abc"
    assert messages.email_unsubscribe_url("abc") == "https://alerts.test/api/unsubscribe/abc/email"
    assert manage_button("abc") == ("⚙️ إدارة التنبيهات", "https://alerts.test/api/unsubscribe/abc")


def test_both_verifications_are_joined_the_arabic_way():
    from types import SimpleNamespace

    from backend.services.notifier import _verification_label

    job = SimpleNamespace(client_identity_verified=True, client_payment_verified=True)
    assert _verification_label(job) == "موثق (الهوية والدفع)"
