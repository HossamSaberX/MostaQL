"""
Email service using Gmail SMTP (no domain required!)
Limitation: 500 emails/day, requires App Password
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict
from loguru import logger
from backend.config import settings

# Gmail SMTP Configuration
GMAIL_USER = settings.gmail_user
GMAIL_PASSWORD = settings.gmail_app_password
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # STARTTLS


VERIFICATION_TEMPLATE = """<!DOCTYPE html>
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


def send_email_gmail(to_email: str, subject: str, html_body: str) -> bool:
    """
    Send email via Gmail SMTP
    Requires: Gmail App Password (not regular password!)
    """
    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"خدمة مستقل <{GMAIL_USER}>"
        msg['To'] = to_email
        
        # Attach HTML body
        html_part = MIMEText(html_body, 'html', 'utf-8')
        msg.attach(html_part)
        
        # Connect and send via Gmail SMTP
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"✓ Email sent via Gmail to {to_email}")
        return True
        
    except Exception as e:
        logger.error(f"✗ Gmail email failed to {to_email}: {e}")
        return False


async def send_verification_email_gmail(email: str, token: str) -> bool:
    """Send verification email via Gmail"""
    from backend.config import settings
    
    try:
        verify_url = f"{settings.base_url}/api/verify/{token}"
        html_content = VERIFICATION_TEMPLATE.format(verify_url=verify_url)
        
        return send_email_gmail(
            to_email=email,
            subject="تأكيد البريد الإلكتروني - خدمة إشعارات مستقل",
            html_body=html_content
        )
        
    except Exception as e:
        logger.error(f"Failed to send verification email to {email}: {e}")
        return False


async def send_job_notifications_gmail(email: str, category_name: str, jobs: List[Dict[str, str]], unsubscribe_token: str) -> bool:
    """Send job notification email via Gmail"""
    from backend.config import settings
    
    try:
        if not jobs:
            return False
        
        jobs_html = "\n".join([
            f'<div style="border: 1px solid #e0e0e0; border-radius: 5px; padding: 20px; margin-bottom: 15px;"><h3 style="margin: 0 0 10px 0; color: #2c3e50;"><a href="{job["url"]}" style="color: #3498db; text-decoration: none; font-weight: bold;">{job["title"]}</a></h3></div>'
            for job in jobs
        ])
        
        unsubscribe_url = f"{settings.base_url}/api/unsubscribe/{unsubscribe_token}"
        
        html_content = f"""<!DOCTYPE html>
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
        
        return send_email_gmail(
            to_email=email,
            subject=f"مشاريع جديدة في {category_name} - مستقل",
            html_body=html_content
        )
        
    except Exception as e:
        logger.error(f"Failed to send job notification to {email}: {e}")
        return False

