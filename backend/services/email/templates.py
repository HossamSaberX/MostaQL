"""
Shared email templates used by all email providers.
"""
from typing import List, Dict


def get_verification_email_html(verify_url: str) -> str:
    """Generate verification email HTML"""
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px; }}
        .container {{ max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; padding: 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h2 {{ color: #2c3e50; margin-bottom: 20px; }}
        p {{ color: #555; line-height: 1.6; font-size: 16px; }}
        .button {{ display: inline-block; background-color: #3498db; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; margin: 20px 0; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="container">
        <h2>مرحباً بك في خدمة إشعارات مستقل</h2>
        <p>شكراً لاشتراكك في خدمة الإشعارات الخاصة بنا. للمتابعة، يرجى تأكيد بريدك الإلكتروني بالنقر على الزر أدناه:</p>
        <a href="{verify_url}" class="button">تأكيد البريد الإلكتروني</a>
        <p style="color: #e74c3c; font-weight: bold;">هذا الرابط صالح لمدة 24 ساعة فقط</p>
    </div>
</body>
</html>"""


def get_job_notifications_html(category_name: str, jobs: List[Dict[str, str]], unsubscribe_url: str) -> str:
    """Generate job notifications email HTML"""
    jobs_html = "\n".join([
        f'<div style="border: 1px solid #e0e0e0; border-radius: 5px; padding: 20px; margin-bottom: 15px;"><h3 style="margin: 0 0 10px 0; color: #2c3e50;"><a href="{job["url"]}" style="color: #3498db; text-decoration: none; font-weight: bold;">{job["title"]}</a></h3></div>'
        for job in jobs
    ])
    
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head><meta charset="UTF-8"></head>
<body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; padding: 40px;">
        <h2>مشاريع جديدة في {category_name}</h2>
        <p>تم العثور على {len(jobs)} مشروع جديد</p>
        {jobs_html}
        <hr>
        <small><a href="{unsubscribe_url}">إلغاء الاشتراك</a></small>
    </div>
</body>
</html>"""

