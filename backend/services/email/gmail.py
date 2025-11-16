"""
Gmail email service implementation using SMTP.
"""
from backend.config import settings
from backend.services.email.base import SMTPEmailService


class GmailEmailService(SMTPEmailService):
    """Gmail email service implementation"""
    
    def _get_smtp_config(self) -> tuple[str, int, str, str]:
        """Get Gmail SMTP configuration"""
        return (
            "smtp.gmail.com",
            587,
            settings.gmail_user,
            settings.gmail_app_password
        )
    
    def _get_provider_name(self) -> str:
        """Get provider name for logging"""
        return "Gmail"
    
    def _credentials_valid(self) -> bool:
        """Check if Gmail credentials are configured"""
        return bool(settings.gmail_user and settings.gmail_app_password)

