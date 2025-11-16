"""
Brevo (formerly Sendinblue) email service implementation using SMTP.
"""
from loguru import logger
from backend.config import settings
from backend.services.email.base import SMTPEmailService


class BrevoEmailService(SMTPEmailService):
    """Brevo email service implementation"""
    
    def _get_smtp_config(self) -> tuple[str, int, str, str]:
        """Get Brevo SMTP configuration"""
        return (
            "smtp-relay.brevo.com",
            587,
            settings.brevo_smtp_login,
            settings.brevo_smtp_key
        )
    
    def _get_provider_name(self) -> str:
        """Get provider name for logging"""
        return "Brevo"
    
    def _get_sender_email(self) -> str:
        """
        Get sender email - MUST be a verified sender email.
        The SMTP login (9bc163001@smtp-brevo.com) is NOT a valid sender.
        You need to verify a sender email in Brevo dashboard first.
        """
        if settings.brevo_sender_email:
            return settings.brevo_sender_email
        
        # If no sender email is set, log a warning
        logger.warning(
            "BREVO_SENDER_EMAIL not set! "
            "The SMTP login cannot be used as sender. "
            "You must verify a sender email in Brevo dashboard and set BREVO_SENDER_EMAIL in .env"
        )
        # Still return login as fallback, but it will fail
        return settings.brevo_smtp_login
    
    def _credentials_valid(self) -> bool:
        """Check if Brevo credentials are configured"""
        has_key = bool(settings.brevo_smtp_key)
        has_login = bool(settings.brevo_smtp_login)
        
        if not has_key or not has_login:
            logger.debug(
                f"Brevo credentials check: key={has_key} (length={len(settings.brevo_smtp_key) if settings.brevo_smtp_key else 0}), "
                f"login={has_login} (value={settings.brevo_smtp_login[:20] if settings.brevo_smtp_login else 'None'}...)"
            )
        
        return has_key and has_login

