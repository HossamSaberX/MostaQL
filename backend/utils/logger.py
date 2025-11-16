"""
Structured logging setup using loguru
"""
from loguru import logger
import sys
from backend.config import settings


def setup_logger():
    """Configure loguru logger with structured output"""
    
    # Remove default handler
    logger.remove()
    
    # Get log level from settings
    log_level = settings.log_level.upper()
    
    # Console handler with colored output
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True
    )
    
    # Single file handler
    logger.add(
        "logs/app.log",
        rotation=settings.log_rotation_size,
        retention=f"{settings.log_retention_days} days",
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        enqueue=True  # Thread-safe
    )
    
    logger.info("Logger initialized")
    return logger


# Initialize logger
app_logger = setup_logger()

