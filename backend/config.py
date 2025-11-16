"""
Configuration management using environment variables.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:///./data/mostaql.db"

    # Email providers
    email_provider: str = "gmail"  # Options: "gmail" or "brevo"
    
    # Gmail settings
    gmail_user: str = ""
    gmail_app_password: str = ""
    
    # Brevo settings
    brevo_smtp_key: str = ""
    brevo_smtp_login: str = ""
    brevo_sender_email: str = ""  # Verified sender email (optional, defaults to login)
    
    # Legacy Resend settings (kept for backward compatibility)
    resend_api_key: str = ""
    resend_from_email: str = "noreply@yourdomain.com"

    # Application
    base_url: str = "http://localhost"
    secret_key: str = "CHANGE_ME"
    environment: str = "development"

    # Scraper
    scraper_interval_minutes: int = 30  # Legacy: kept for backward compatibility
    scraper_poll_interval_minutes: int = 2  # Polling interval (quick checks)
    max_categories_per_user: int = 10

    # Rate limiting
    rate_limit_per_hour: int = 5

    # Logging
    log_level: str = "INFO"

    # Token expiry
    verification_token_expiry_hours: int = 24

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()


