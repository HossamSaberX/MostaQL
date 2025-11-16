"""
Configuration management using environment variables
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:///./data/mostaql.db"
    
    # Resend Email API
    resend_api_key: str = ""
    resend_from_email: str = "noreply@yourdomain.com"
    
    # Gmail SMTP (alternative to Resend)
    gmail_user: str = ""
    gmail_app_password: str = ""
    
    # Application
    base_url: str = "http://localhost"
    secret_key: str
    environment: str = "development"
    
    # Scraper
    scraper_interval_minutes: int = 30
    max_categories_per_user: int = 10
    
    # Rate Limiting
    rate_limit_per_hour: int = 5
    
    # Logging
    log_level: str = "INFO"
    
    # Token expiry (hours)
    verification_token_expiry_hours: int = 24
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()

