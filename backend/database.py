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

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    verified = Column(Boolean, default=False)
    token = Column(String(255), unique=True, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    unsubscribed = Column(Boolean, default=False)
    last_notified_at = Column(TIMESTAMP, nullable=True)
    
    # Relationships
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
    
    # Relationships
    user_categories = relationship("UserCategory", back_populates="category", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="category")
    scraper_logs = relationship("ScraperLog", back_populates="category")


class UserCategory(Base):
    __tablename__ = "user_categories"
    
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    # Relationships
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
    content_hash = Column(String(64), nullable=False)  # SHA256 hash
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    scraped_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    # Relationships
    category = relationship("Category", back_populates="jobs")
    notifications = relationship("Notification", back_populates="job", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_jobs_category', 'category_id', 'scraped_at'),
        Index('idx_jobs_hash', 'content_hash'),
    )


class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    sent_at = Column(TIMESTAMP, default=datetime.utcnow)
    status = Column(String(20), default="pending")  # pending, sent, failed
    error_message = Column(Text, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="notifications")
    job = relationship("Job", back_populates="notifications")
    
    __table_args__ = (
        Index('idx_notifications_status', 'status', 'sent_at'),
    )


class ScraperLog(Base):
    __tablename__ = "scraper_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    status = Column(String(20), nullable=False)  # success, blocked, error
    jobs_found = Column(Integer, default=0)
    duration_seconds = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    scraped_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    # Relationships
    category = relationship("Category", back_populates="scraper_logs")


# Database connection setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/mostaql.db")

# Create engine
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
    # Create data directory if it doesn't exist
    os.makedirs("data", exist_ok=True)
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    # Populate initial categories
    db = SessionLocal()
    try:
        # Check if categories already exist
        existing_categories = db.query(Category).count()
        if existing_categories == 0:
            # For MVP: Just scrape main projects page (all categories)
            # Category filtering can be added later
            initial_categories = [
                Category(name="جميع المشاريع", mostaql_url="https://mostaql.com/projects"),
            ]
            db.add_all(initial_categories)
            db.commit()
            print(f"✓ Initialized {len(initial_categories)} categories")
        else:
            print(f"✓ Database already has {existing_categories} categories")
    except Exception as e:
        print(f"✗ Error initializing database: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("✓ Database initialization complete")

