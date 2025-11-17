"""
Abstract base class defining the EmailService contract.
All email providers must implement this interface.
"""
import smtplib
from abc import ABC, abstractmethod
from email.header import Header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from typing import List, Dict
from loguru import logger
from backend.config import settings


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
        category_name: str,
        jobs: List[Dict[str, str]],
        unsubscribe_token: str
    ) -> bool:
        """
        Send job notification email with list of new jobs.
        
        Args:
            email: Recipient email address
            category_name: Name of the category
            jobs: List of job dictionaries with 'title' and 'url' keys
            unsubscribe_token: Token for unsubscribe link
            
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
    
    def _send_email(self, to_email: str, subject: str, html_body: str) -> bool:
        """
        Send email via SMTP (shared implementation).
        """
        if not self._credentials_valid():
            logger.error(f"{self._get_provider_name()} credentials are missing; cannot send email")
            return False

        try:
            host, port, username, password = self._get_smtp_config()
            
            logger.info(f"Connecting to {self._get_provider_name()} SMTP: {host}:{port}")
            logger.info(f"Using login: {username}")
            
            sender_email = getattr(self, '_get_sender_email', lambda: username)()
            logger.info(f"Using sender email: {sender_email}")
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = str(Header(subject, 'utf-8'))
            msg['From'] = formataddr(
                (str(Header("خدمة إشعارات مستقل", 'utf-8')), sender_email)
            )
            msg['To'] = to_email
            
            html_part = MIMEText(html_body, 'html', 'utf-8')
            msg.attach(html_part)
            
            logger.info("Creating SMTP connection...")
            server = smtplib.SMTP(host, port, timeout=settings.smtp_timeout)
            try:
                logger.info("Starting TLS...")
                server.starttls()
                
                logger.info(f"Logging in as {username}...")
                server.login(username, password)
                logger.info("✓ Login successful")
                
                logger.info(f"Sending message to {to_email}...")
                
                try:
                    msg_str = msg.as_string()
                    logger.info(f"Message size: {len(msg_str)} bytes")
                    
                    refused = server.sendmail(sender_email, [to_email], msg_str)
                    
                    if refused:
                        logger.error(f"✗ SMTP sendmail returned refused recipients: {refused}")
                        for email_addr, (code, error_msg) in refused.items():
                            logger.error(f"Refused recipient {email_addr}: {code} - {error_msg}")
                        return False
                    
                    logger.info(f"✓ Email sent via {self._get_provider_name()} to {to_email}")
                    logger.info(f"SMTP sendmail returned: {refused} (empty dict = success)")
                    return True
                except smtplib.SMTPRecipientsRefused as e:
                    logger.error(f"✗ SMTP recipients refused: {e}")
                    return False
                except smtplib.SMTPDataError as e:
                    logger.error(f"✗ SMTP data error (server rejected message): {e}")
                    return False
            finally:
                server.quit()
                logger.info("SMTP connection closed")
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"✗ {self._get_provider_name()} authentication failed: {e}")
            logger.error(f"  Check your credentials - username: {username[:20]}..., password length: {len(password)}")
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
    
    def send_verification_email(self, email: str, token: str) -> bool:
        """Send verification email (shared implementation)"""
        from backend.config import settings
        from backend.services.email.templates import get_verification_email_html
        
        try:
            verify_url = f"{settings.base_url}/api/verify/{token}"
            html_content = get_verification_email_html(verify_url)
            
            return self._send_email(
                to_email=email,
                subject="تأكيد البريد الإلكتروني - خدمة إشعارات مستقل",
                html_body=html_content
            )
            
        except Exception as e:
            logger.error(f"Failed to send verification email to {email}: {e}")
            return False
    
    def send_job_notifications(
        self,
        email: str,
        category_name: str,
        jobs: List[Dict[str, str]],
        unsubscribe_token: str
    ) -> bool:
        """Send job notification email (shared implementation)"""
        from backend.config import settings
        from backend.services.email.templates import get_job_notifications_html
        
        try:
            if not jobs:
                return False
            
            unsubscribe_url = f"{settings.base_url}/api/unsubscribe/{unsubscribe_token}"
            html_content = get_job_notifications_html(category_name, jobs, unsubscribe_url)
            
            return self._send_email(
                to_email=email,
                subject=f"مشاريع جديدة في {category_name} - خدمة إشعارات مستقل",
                html_body=html_content
            )
            
        except Exception as e:
            logger.error(f"Failed to send job notification to {email}: {e}")
            return False

