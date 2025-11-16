"""
Pydantic models for API request/response validation
"""
from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional
from datetime import datetime


class SubscribeRequest(BaseModel):
    """Request model for user subscription"""
    email: EmailStr = Field(..., description="User's email address")
    category_ids: List[int] = Field(
        ..., 
        min_items=1, 
        description="List of category IDs to subscribe to"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "category_ids": [1, 2]
            }
        }


class SubscribeResponse(BaseModel):
    """Response model for subscription"""
    message: str
    email: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "message": "تم الاشتراك بنجاح! يرجى التحقق من بريدك الإلكتروني لتأكيد الاشتراك",
                "email": "user@example.com"
            }
        }


class CategoryResponse(BaseModel):
    """Response model for category"""
    id: int
    name: str
    url: Optional[str] = None
    jobs_count: Optional[int] = 0
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": 1,
                "name": "برمجة وتطوير",
                "url": "https://mostaql.com/projects?category=1",
                "jobs_count": 42
            }
        }


class ScraperMetrics(BaseModel):
    """Scraper health metrics"""
    last_run: Optional[datetime] = None
    success_rate_24h: float = 0.0
    categories_active: int = 0
    
    class Config:
        from_attributes = True


class EmailMetrics(BaseModel):
    """Email service metrics"""
    pending: int = 0
    sent_today: int = 0
    failed_today: int = 0
    
    class Config:
        from_attributes = True


class DatabaseMetrics(BaseModel):
    """Database metrics"""
    users_verified: int = 0
    jobs_total: int = 0
    
    class Config:
        from_attributes = True


class DetailedHealthResponse(BaseModel):
    """Detailed health check response"""
    status: str
    timestamp: datetime
    database: str
    last_scrape: Optional[datetime] = None
    pending_notifications: int = 0
    scraper: ScraperMetrics
    email: EmailMetrics
    database_stats: DatabaseMetrics
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "timestamp": "2024-01-01T00:00:00",
                "database": "connected",
                "last_scrape": "2024-01-01T00:00:00",
                "pending_notifications": 5,
                "scraper": {
                    "last_run": "2024-01-01T00:00:00",
                    "success_rate_24h": 0.95,
                    "categories_active": 7
                },
                "email": {
                    "pending": 5,
                    "sent_today": 100,
                    "failed_today": 2
                },
                "database_stats": {
                    "users_verified": 50,
                    "jobs_total": 1000
                }
            }
        }

