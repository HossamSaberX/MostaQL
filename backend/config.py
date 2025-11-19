"""
Configuration management using environment variables.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/mostaql.db"

    email_provider: str = "gmail"
    email_bcc_batch_size: int = 0
    
    gmail_user: str = ""
    gmail_app_password: str = ""
    
    brevo_smtp_key: str = ""
    brevo_smtp_login: str = ""
    brevo_sender_email: str = ""
    
    resend_api_key: str = ""
    resend_from_email: str = "noreply@yourdomain.com"

    base_url: str = "http://localhost"
    secret_key: str = "CHANGE_ME"
    environment: str = "development"

    scraper_interval_minutes: int = 30
    scraper_poll_interval_minutes: int = 2
    scraper_quick_check_count: int = 5
    mostaql_base_url: str = "https://mostaql.com"
    http_request_timeout: int = 10
    max_categories_per_user: int = 10

    rate_limit_per_hour: int = 5

    log_level: str = "INFO"
    log_rotation_size: str = "50 MB"
    log_retention_days: int = 7

    smtp_timeout: int = 30

    api_port: int = 8000

    verification_token_expiry_hours: int = 24

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()


