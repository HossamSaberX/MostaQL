"""
Email service module with Strategy Pattern implementation.
Provides a unified interface for multiple email providers.
"""
from typing import List, Dict

from backend.config import settings
from backend.services.email.gmail import GmailEmailService
from backend.services.email.brevo import BrevoEmailService

# Export the base class for type hints
from backend.services.email.base import EmailService


def get_email_service() -> EmailService:
    """
    Factory function to get the appropriate email service based on config.
    Returns an instance of the configured email provider.
    """
    provider = getattr(settings, 'email_provider', 'gmail').lower()
    
    if provider == 'brevo':
        return BrevoEmailService()
    elif provider == 'gmail':
        return GmailEmailService()
    else:
        # Default to Gmail if unknown provider
        from backend.utils.logger import app_logger
        app_logger.warning(f"Unknown email provider '{provider}', defaulting to Gmail")
        return GmailEmailService()


# Convenience functions that use the configured provider
def send_verification_email(email: str, token: str) -> bool:
    """Send verification email using the configured provider"""
    service = get_email_service()
    return service.send_verification_email(email, token)


def send_job_notifications(
    email: str, 
    category_name: str, 
    jobs: List[Dict[str, str]], 
    unsubscribe_token: str
) -> bool:
    """Send job notifications using the configured provider"""
    service = get_email_service()
    return service.send_job_notifications(email, category_name, jobs, unsubscribe_token)

