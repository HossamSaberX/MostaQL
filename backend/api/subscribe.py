"""
Subscribe endpoint for user registration
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address
from loguru import logger

from backend.database import get_db, User, Category, UserCategory
from backend.models import SubscribeRequest, SubscribeResponse
from backend.utils.security import generate_token, validate_email
# Using Gmail SMTP - FREE, works without custom domain, 500 emails/day!
from backend.services.email_service_gmail import send_verification_email_gmail as send_verification_email

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post("/subscribe", response_model=SubscribeResponse)
@limiter.limit("5/hour")
async def subscribe(
    request: Request,
    data: SubscribeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Subscribe a user to job notifications
    
    Rate limit: 5 requests per hour per IP
    """
    try:
        # Validate email format
        if not validate_email(data.email):
            raise HTTPException(status_code=400, detail="Invalid email format")
        
        # Check if categories exist
        categories = db.query(Category).filter(Category.id.in_(data.category_ids)).all()
        if len(categories) != len(data.category_ids):
            raise HTTPException(status_code=400, detail="One or more invalid category IDs")
        
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == data.email).first()
        
        if existing_user:
            if existing_user.verified:
                # User exists and verified - update categories
                # Remove old categories
                db.query(UserCategory).filter(UserCategory.user_id == existing_user.id).delete()
                
                # Add new categories
                for category_id in data.category_ids:
                    user_category = UserCategory(
                        user_id=existing_user.id,
                        category_id=category_id
                    )
                    db.add(user_category)
                
                db.commit()
                logger.info(f"Updated categories for existing user: {data.email}")
                
                return SubscribeResponse(
                    message="Your subscription preferences have been updated",
                    email=data.email
                )
            else:
                # User exists but not verified - resend verification
                # Update categories
                db.query(UserCategory).filter(UserCategory.user_id == existing_user.id).delete()
                
                for category_id in data.category_ids:
                    user_category = UserCategory(
                        user_id=existing_user.id,
                        category_id=category_id
                    )
                    db.add(user_category)
                
                # Generate new token
                existing_user.token = generate_token()
                db.commit()
                
                # Send verification email in background
                background_tasks.add_task(
                    send_verification_email,
                    data.email,
                    existing_user.token
                )
                
                logger.info(f"Resent verification email to: {data.email}")
                
                return SubscribeResponse(
                    message="Verification email sent. Please check your inbox.",
                    email=data.email
                )
        
        # Create new user
        token = generate_token()
        user = User(
            email=data.email,
            token=token,
            verified=False,
            unsubscribed=False
        )
        db.add(user)
        db.flush()  # Get user ID
        
        # Add user categories
        for category_id in data.category_ids:
            user_category = UserCategory(
                user_id=user.id,
                category_id=category_id
            )
            db.add(user_category)
        
        db.commit()
        
        # Send verification email in background
        background_tasks.add_task(
            send_verification_email,
            data.email,
            token
        )
        
        logger.info(f"New user subscribed: {data.email}, categories: {data.category_ids}")
        
        return SubscribeResponse(
            message="Verification email sent. Please check your inbox.",
            email=data.email
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Subscribe error for {data.email}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")

