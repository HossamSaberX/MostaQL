"""
Abstract base class defining the EmailService contract.
All email providers must implement this interface.
"""
import smtplib
from abc import ABC, abstractmethod
from email.header import Header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from typing import Any, List, Dict, Optional, Tuple
from loguru import logger
from backend.config import settings
from backend.services.messages import SERVICE_NAME, email_unsubscribe_url
from backend.services.email.templates import (
    render_alert_email,
    render_announcement_email,
    render_unsubscribe_email,
    render_verification_email,
)


def build_message(
    *,
    sender_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
    unsubscribe_url: Optional[str] = None,
) -> MIMEMultipart:
    """
    Build the MIME message: plain text first, then HTML (mail clients show the last part they
    understand). With `unsubscribe_url` the message carries the one-click unsubscribe headers
    (RFC 8058) that Gmail and Yahoo require of bulk senders.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((SERVICE_NAME, sender_email), "utf-8")
    msg["To"] = to_email
    msg["Date"] = formatdate(usegmt=True)
    msg["Message-ID"] = make_msgid(domain=sender_email.rpartition("@")[2] or None)
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


class EmailService(ABC):
    """
    Abstract base class for email service providers.
    Defines the contract that all email providers must follow.
    """
    
    @abstractmethod
    def send_verification_email(self, email: str, token: str) -> bool:
        """
        Send a verification email to the user.
        
        Args:
            email: Recipient email address
            token: Verification token to include in the email
            
        Returns:
            True if email was sent successfully, False otherwise
        """
        pass
    
    @abstractmethod
    def send_job_notifications(
        self,
        email: str,
        jobs: List[Dict[str, Any]],
        unsubscribe_token: Optional[str] = None,
        more_count: int = 0,
        more_url: Optional[str] = None,
    ) -> bool:
        """
        Send one person one alert email.
        
        Args:
            email: Recipient email address
            jobs: Project payloads (see notifier._build_job_payload)
            unsubscribe_token: The recipient's token, for the manage and unsubscribe links
            more_count: Projects the caller left out, counted as "and N more"
            more_url: Where "and N more" leads
            
        Returns:
            True if email was sent successfully, False otherwise
        """
        pass
    
    @abstractmethod
    def send_unsubscribe_email(self, email: str, token: str) -> bool:
        """
        Send unsubscribe confirmation email.
        
        Args:
            email: Recipient email address
            token: Unsubscribe token
            
        Returns:
            True if email was sent successfully, False otherwise
        """
        pass


class SMTPEmailService(EmailService):
    """
    Base class for SMTP-based email services.
    Handles common SMTP logic, subclasses only need to provide config.
    """
    
    @abstractmethod
    def _get_smtp_config(self) -> tuple[str, int, str, str]:
        """
        Get SMTP configuration for this provider.
        
        Returns:
            Tuple of (host, port, username, password)
        """
        pass
    
    @abstractmethod
    def _get_provider_name(self) -> str:
        """Get the name of this email provider (for logging)"""
        pass
    
    @abstractmethod
    def _credentials_valid(self) -> bool:
        """Check if credentials are configured"""
        pass
    
    def _send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        *,
        unsubscribe_url: Optional[str] = None,
    ) -> bool:
        """
        Send one email via SMTP (shared implementation).
        """
        if not self._credentials_valid():
            logger.error(f"{self._get_provider_name()} credentials are missing; cannot send email")
            return False

        try:
            host, port, username, password = self._get_smtp_config()
            sender_email = getattr(self, '_get_sender_email', lambda: username)()

            msg = build_message(
                sender_email=sender_email,
                to_email=to_email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
                unsubscribe_url=unsubscribe_url,
            )

            server = smtplib.SMTP(host, port, timeout=settings.smtp_timeout)
            try:
                server.starttls()
                server.login(username, password)
                refused = server.sendmail(sender_email, [to_email], msg.as_string())
                if refused:
                    for email_addr, (code, error_msg) in refused.items():
                        logger.error(f"Refused recipient {email_addr}: {code} - {error_msg}")
                    return False
                logger.info(f"✓ Email sent via {self._get_provider_name()} to {to_email}")
                return True
            finally:
                server.quit()

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"✗ {self._get_provider_name()} authentication failed: {e}")
            return False
        except smtplib.SMTPRecipientsRefused as e:
            logger.error(f"✗ {self._get_provider_name()} recipient refused: {e}")
            return False
        except smtplib.SMTPSenderRefused as e:
            logger.error(f"✗ {self._get_provider_name()} sender refused: {e}")
            return False
        except smtplib.SMTPDataError as e:
            logger.error(f"✗ {self._get_provider_name()} data error: {e}")
            return False
        except Exception as e:
            logger.error(f"✗ {self._get_provider_name()} email failed to {to_email}: {e}")
            logger.exception("Full error traceback:")
            return False

    def _send_rendered(
        self,
        email: str,
        rendered: Tuple[str, str, str],
        unsubscribe_token: Optional[str] = None,
    ) -> bool:
        """Send a (subject, html, text) triple; with a token it also carries one-click unsubscribe."""
        subject, html_body, text_body = rendered
        return self._send_email(
            to_email=email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            unsubscribe_url=email_unsubscribe_url(unsubscribe_token) if unsubscribe_token else None,
        )

    def send_verification_email(self, email: str, token: str) -> bool:
        """Send verification email (shared implementation)"""
        try:
            verify_url = f"{settings.base_url}/api/verify/{token}"
            return self._send_rendered(email, render_verification_email(verify_url))
        except Exception as e:
            logger.error(f"Failed to send verification email to {email}: {e}")
            return False

    def send_job_notifications(
        self,
        email: str,
        jobs: List[Dict[str, Any]],
        unsubscribe_token: Optional[str] = None,
        more_count: int = 0,
        more_url: Optional[str] = None,
    ) -> bool:
        """Send one alert email (shared implementation)"""
        try:
            if not jobs:
                return False
            rendered = render_alert_email(
                jobs, token=unsubscribe_token, more_count=more_count, more_url=more_url
            )
            return self._send_rendered(email, rendered, unsubscribe_token)
        except Exception as e:
            logger.error(f"Failed to send job notification to {email}: {e}")
            return False

    def send_unsubscribe_email(self, email: str, token: str) -> bool:
        """Send the manage-or-unsubscribe link (shared implementation)"""
        try:
            return self._send_rendered(email, render_unsubscribe_email(token))
        except Exception as e:
            logger.error(f"Failed to send unsubscribe email to {email}: {e}")
            return False

    def send_announcement(self, email: str, message: str, unsubscribe_token: str = None) -> bool:
        """Send an announcement/update email (shared implementation)"""
        try:
            rendered = render_announcement_email(message, token=unsubscribe_token)
            return self._send_rendered(email, rendered, unsubscribe_token)
        except Exception as e:
            logger.error(f"Failed to send announcement to {email}: {e}")
            return False
