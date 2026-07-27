"""
Shared email templates used by all email providers.
"""
from html import escape
from typing import Any, List, Dict


def get_verification_email_html(verify_url: str) -> str:
    """Generate verification email HTML"""
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
</head>
<body style="direction: rtl; text-align: right; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; padding: 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
        <h2 style="color: #2c3e50; margin-bottom: 20px;">مرحباً بك في خدمة إشعارات مستقل</h2>
        <p style="color: #555; line-height: 1.6; font-size: 16px;">شكراً لاشتراكك في خدمة الإشعارات الخاصة بنا. للمتابعة، يرجى تأكيد بريدك الإلكتروني بالنقر على الزر أدناه:</p>
        <a href="{verify_url}" style="display: inline-block; background-color: #3498db; color: #ffffff !important; padding: 12px 30px; text-decoration: none; border-radius: 5px; margin: 20px 0; font-weight: bold;">تأكيد البريد الإلكتروني</a>
        <p style="color: #e74c3c; font-weight: bold;">هذا الرابط صالح لمدة 24 ساعة فقط</p>
    </div>
</body>
</html>"""


def _job_signals_html(job: Dict[str, Any]) -> str:
    signals = []
    labels = (
        ("budget", "الميزانية"),
        ("hiring_rate", "معدل التوظيف"),
        ("projects_in_progress", "مشاريع قيد التنفيذ"),
        ("ongoing_communications", "التواصلات الجارية"),
        ("verification", "التوثيق"),
    )
    for key, label in labels:
        value = job.get(key)
        if value is not None:
            signals.append(
                f'<span style="display: inline-block; margin: 3px 0 3px 8px; '
                f'padding: 4px 8px; border-radius: 999px; background: #eef8fc; '
                f'color: #24566b; font-size: 13px;">{label}: {escape(str(value))}</span>'
            )
    if job.get("project_age_minutes") is not None:
        signals.append(
            f'<span style="display: inline-block; margin: 3px 0 3px 8px; '
            f'padding: 4px 8px; border-radius: 999px; background: #fff6e6; '
            f'color: #7a4b00; font-size: 13px;">عمر المشروع: '
            f'{escape(str(job["project_age_minutes"]))} دقيقة</span>'
        )
    return "".join(signals)


def get_job_notifications_html(category_name: str, jobs: List[Dict[str, Any]], unsubscribe_url: str) -> str:
    """Generate job notifications email HTML"""
    jobs_html = "\n".join([
        f'<div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin-bottom: 15px; direction: rtl; text-align: right;">'
        f'<h3 style="margin: 0 0 10px 0; color: #2c3e50;">{escape(str(job["title"]))}</h3>'
        f'<div style="margin-bottom: 14px;">{_job_signals_html(job)}</div>'
        f'<a href="{escape(str(job["url"]), quote=True)}" style="display: inline-block; background: #2cabe3; color: #fff !important; padding: 9px 16px; text-decoration: none; border-radius: 6px; font-weight: bold;">راجع المشروع وتقدّم الآن</a>'
        f'</div>'
        for job in jobs
    ])
    
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head><meta charset="UTF-8"></head>
<body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px; direction: rtl; text-align: right;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; padding: 40px; direction: rtl;">
        <h2 style="color: #2c3e50;">مشاريع جديدة في {escape(category_name)}</h2>
        <p style="color: #555; line-height: 1.6; font-size: 16px;">تم العثور على {len(jobs)} مشروع جديد</p>
        {jobs_html}
        <hr>
        <small><a href="{escape(unsubscribe_url, quote=True)}" style="color: #3498db; font-weight: 600; text-decoration: none;">إلغاء الاشتراك</a></small>
    </div>
</body>
</html>"""


def get_announcement_html(message: str, unsubscribe_url: str) -> str:
    safe_message = escape(message).replace("\n", "<br>")
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head><meta charset="UTF-8"></head>
<body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px; direction: rtl; text-align: right;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; padding: 40px; direction: rtl; box-shadow: 0 2px 10px rgba(0,0,0,0.08);">
        <h2 style="color: #2c3e50; margin: 0 0 24px 0;">إعلان من خدمة تنبيهات مستقل</h2>
        <div style="color: #444; line-height: 2; font-size: 16px;">{safe_message}</div>
        <hr style="margin: 32px 0 16px 0; border: none; border-top: 1px solid #eee;">
        <small><a href="{escape(unsubscribe_url, quote=True)}" style="color: #999; text-decoration: none;">إلغاء الاشتراك من الإعلانات</a></small>
    </div>
</body>
</html>"""


def get_unsubscribe_email_html(unsubscribe_url: str) -> str:
    """Generate unsubscribe email HTML"""
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
</head>
<body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px; direction: rtl; text-align: right;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; padding: 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
        <h2 style="color: #2c3e50; margin-bottom: 20px;">إلغاء الاشتراك</h2>
        <p style="color: #555; line-height: 1.6; font-size: 16px;">لقد طلبت إلغاء الاشتراك من خدمة إشعارات مستقل. لإتمام العملية، يرجى النقر على الزر أدناه:</p>
        <a href="{unsubscribe_url}" style="display: inline-block; background-color: #e74c3c; color: #ffffff !important; padding: 12px 30px; text-decoration: none; border-radius: 5px; margin: 20px 0; font-weight: bold;">تأكيد إلغاء الاشتراك</a>
        <p style="color: #555; line-height: 1.6; font-size: 16px;">إذا لم تطلب هذا الإجراء، يمكنك تجاهل هذه الرسالة.</p>
    </div>
</body>
</html>"""

