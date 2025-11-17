"""
Email service module with Strategy Pattern implementation.
Provides a unified interface for multiple email providers.
"""
from typing import List, Dict
import threading

from backend.config import settings
from backend.services.email.gmail import GmailEmailService
from backend.services.email.brevo import BrevoEmailService

# Export the base class for type hints
from backend.services.email.base import EmailService

# Thread-safe counter for round-robin alternating
_alternate_lock = threading.Lock()
_alternate_counter = 0


def get_email_service() -> EmailService:
    """
    Factory function to get the appropriate email service based on config.
    
    Supports:
    - "gmail": Always use Gmail
    - "brevo": Always use Brevo
    - "alternate": Round-robin between Gmail and Brevo (alternates on each call)
    
    Returns an instance of the configured email provider.
    """
    provider = getattr(settings, 'email_provider', 'gmail').lower()
    
    if provider == 'alternate':
        # Round-robin: alternate between Gmail and Brevo
        global _alternate_counter
        with _alternate_lock:
            _alternate_counter += 1
            use_gmail = (_alternate_counter % 2) == 1
            return GmailEmailService() if use_gmail else BrevoEmailService()
    elif provider == 'brevo':
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
    """
    Send verification email using the configured provider.
    If EMAIL_PROVIDER=alternate, will try the other provider if first fails.
    """
    service = get_email_service()
    success = service.send_verification_email(email, token)
    
    # If alternating and first attempt failed, try the other provider
    if not success and getattr(settings, 'email_provider', '').lower() == 'alternate':
        from backend.utils.logger import app_logger
        app_logger.info("First provider failed for verification email, trying alternate...")
        alternate_service = BrevoEmailService() if isinstance(service, GmailEmailService) else GmailEmailService()
        return alternate_service.send_verification_email(email, token)
    
    return success


def send_job_notifications(
    email: str, 
    category_name: str, 
    jobs: List[Dict[str, str]], 
    unsubscribe_token: str
) -> bool:
    """
    Send job notifications using the configured provider.
    If EMAIL_PROVIDER=alternate, will try the other provider if first fails.
    """
    service = get_email_service()
    success = service.send_job_notifications(email, category_name, jobs, unsubscribe_token)
    
    # If alternating and first attempt failed, try the other provider
    if not success and getattr(settings, 'email_provider', '').lower() == 'alternate':
        from backend.utils.logger import app_logger
        app_logger.info("First provider failed for job notifications, trying alternate...")
        alternate_service = BrevoEmailService() if isinstance(service, GmailEmailService) else GmailEmailService()
        return alternate_service.send_job_notifications(email, category_name, jobs, unsubscribe_token)
    
    return success

