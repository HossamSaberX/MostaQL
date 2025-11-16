"""
Structured logging setup using loguru
"""
from loguru import logger
import sys
import os


def setup_logger():
    """Configure loguru logger with structured output"""
    
    # Remove default handler
    logger.remove()
    
    # Get log level from environment
    log_level = os.getenv("LOG_LEVEL", "INFO")
    
    # Console handler with colored output for development
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True
    )
    
    # File handler for all logs
    logger.add(
        "logs/app.log",
        rotation="100 MB",
        retention="30 days",
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        enqueue=True  # Thread-safe
    )
    
    # Separate file for errors only
    logger.add(
        "logs/errors.log",
        rotation="50 MB",
        retention="90 days",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        enqueue=True
    )
    
    # Scraper-specific logs
    logger.add(
        "logs/scraper.log",
        rotation="50 MB",
        retention="30 days",
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        filter=lambda record: "scraper" in record["extra"].get("context", ""),
        enqueue=True
    )
    
    logger.info("Logger initialized")
    return logger


# Initialize logger
app_logger = setup_logger()

