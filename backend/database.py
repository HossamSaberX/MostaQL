"""
Database models and connection setup for Mostaql Job Notifier
"""
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, 
    Float, Text, TIMESTAMP, ForeignKey, Index, event
)
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from datetime import datetime
from typing import Generator
import os

from backend.config import settings
from backend.enums import NotificationChannel, NotificationStatus
from backend.utils.logger import app_logger

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    verified = Column(Boolean, default=False)
    token = Column(String(255), unique=True, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    token_issued_at = Column(TIMESTAMP, default=datetime.utcnow)
    unsubscribed = Column(Boolean, default=False)
    receive_email = Column(Boolean, default=True)
    receive_telegram = Column(Boolean, default=True)
    telegram_chat_id = Column(String(64), nullable=True, unique=True)
    min_hiring_rate = Column(Float, nullable=True)
    last_notified_at = Column(TIMESTAMP, nullable=True)
    
    categories = relationship("UserCategory", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_users_verified', 'verified', 'unsubscribed'),
        Index('idx_users_token', 'token'),
    )


class Category(Base):
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    mostaql_url = Column(Text, nullable=False)
    last_scraped_at = Column(TIMESTAMP, nullable=True)
    scrape_failures = Column(Integer, default=0)
    
    user_categories = relationship("UserCategory", back_populates="category", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="category")
    scraper_logs = relationship("ScraperLog", back_populates="category")


class UserCategory(Base):
    __tablename__ = "user_categories"
    
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    user = relationship("User", back_populates="categories")
    category = relationship("Category", back_populates="user_categories")
    
    __table_args__ = (
        Index('idx_user_categories_user', 'user_id'),
    )


class Job(Base):
    __tablename__ = "jobs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    url = Column(Text, unique=True, nullable=False)
    content_hash = Column(String(64), nullable=False)
    hiring_rate = Column(Float, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    scraped_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    category = relationship("Category", back_populates="jobs")
    notifications = relationship("Notification", back_populates="job", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_jobs_category', 'category_id', 'scraped_at'),
        Index('idx_jobs_hash', 'content_hash'),
        Index('idx_jobs_hiring_rate', 'hiring_rate'),
    )


class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(20), nullable=True)
    sent_at = Column(TIMESTAMP, default=datetime.utcnow)
    status = Column(String(20), default=NotificationStatus.PENDING.value)
    error_message = Column(Text, nullable=True)
    
    user = relationship("User", back_populates="notifications")
    job = relationship("Job", back_populates="notifications")
    
    __table_args__ = (
        Index('idx_notifications_status', 'status', 'sent_at'),
        Index('idx_notifications_channel', 'channel'),
    )


class ScraperLog(Base):
    __tablename__ = "scraper_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    status = Column(String(20), nullable=False)
    jobs_found = Column(Integer, default=0)
    duration_seconds = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    scraped_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    category = relationship("Category", back_populates="scraper_logs")


DATABASE_URL = settings.database_url

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL mode and other optimizations for SQLite"""
    if "sqlite" in DATABASE_URL:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA busy_timeout=5000")  # 5 second timeout
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator:
    """Dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database and create tables"""
    os.makedirs("data", exist_ok=True)
    
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        from backend.config import settings
        base_url = settings.mostaql_base_url
        desired_categories = [
            ("برمجة، تطوير المواقع والتطبيقات", "development"),
            ("تصميم، فيديو وصوتيات", "design"),
            ("كتابة، تحرير، ترجمة ولغات", "writing-translation"),
            ("تسويق إلكتروني ومبيعات", "marketing"),
            ("أعمال وخدمات استشارية", "business"),
            ("هندسة، عمارة وتصميم داخلي", "engineering-architecture"),
            ("تدريب وتعليم عن بعد", "training"),
            ("دعم، مساعدة وإدخال بيانات", "support"),
        ]

        existing = {category.name for category in db.query(Category).all()}

        to_create = []
        for name, slug in desired_categories:
            if name not in existing:
                to_create.append(
                    Category(
                        name=name,
                        mostaql_url=f"{base_url}/projects?category={slug}&sort=latest",
                    )
                )

        if to_create:
            db.add_all(to_create)
            db.commit()
            app_logger.info(f"✓ Added {len(to_create)} missing categories")
        else:
            app_logger.info(f"✓ All {len(desired_categories)} categories already exist")
    except Exception as e:
        app_logger.error(f"✗ Error initializing database: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    app_logger.info("Initializing database...")
    init_db()
    app_logger.info("✓ Database initialization complete")

