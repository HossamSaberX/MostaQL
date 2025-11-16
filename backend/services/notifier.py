"""
Job notification service - matches new jobs with user preferences and sends notifications
"""
from typing import List, Dict
from sqlalchemy.orm import Session
from loguru import logger
from datetime import datetime

from backend.database import (
    SessionLocal, User, Job, Notification, 
    Category, UserCategory
)
# Using Gmail SMTP - FREE, works without custom domain, 500 emails/day!
from backend.services.email_service_gmail import send_job_notifications_gmail as send_job_notifications


def get_users_for_category(category_id: int, db: Session) -> List[User]:
    """Get all verified, subscribed users interested in a category"""
    users = db.query(User).join(UserCategory).filter(
        UserCategory.category_id == category_id,
        User.verified == True,
        User.unsubscribed == False
    ).all()
    
    return users


def create_pending_notifications(job_id: int, user_ids: List[int], db: Session):
    """Create pending notification records for a job and list of users"""
    for user_id in user_ids:
        notification = Notification(
            user_id=user_id,
            job_id=job_id,
            status="pending"
        )
        db.add(notification)
    
    db.commit()


def queue_job_notifications(new_jobs: List[Job], category_id: int):
    """
    Queue notifications for new jobs
    Creates pending notification records in database
    """
    if not new_jobs:
        return
    
    db = SessionLocal()
    try:
        # Get users interested in this category
        users = get_users_for_category(category_id, db)
        
        if not users:
            logger.info(f"No users subscribed to category {category_id}")
            return
        
        user_ids = [user.id for user in users]
        
        # Create notification records for each job
        for job in new_jobs:
            create_pending_notifications(job.id, user_ids, db)
        
        total_notifications = len(new_jobs) * len(users)
        logger.info(f"Queued {total_notifications} notifications for {len(new_jobs)} jobs, {len(users)} users")
        
    except Exception as e:
        logger.error(f"Error queueing notifications: {e}")
        db.rollback()
    finally:
        db.close()


def get_pending_notifications_by_user() -> Dict[int, List[Dict]]:
    """
    Get all pending notifications grouped by user
    Returns dict: {user_id: [notification_data, ...]}
    """
    db = SessionLocal()
    try:
        # Get all pending notifications with related data
        pending = db.query(Notification).filter(
            Notification.status == "pending"
        ).join(User).join(Job).join(Category).all()
        
        # Group by user
        user_notifications = {}
        for notif in pending:
            if notif.user_id not in user_notifications:
                user_notifications[notif.user_id] = []
            
            user_notifications[notif.user_id].append({
                'notification_id': notif.id,
                'user_email': notif.user.email,
                'user_token': notif.user.token,
                'job_id': notif.job_id,
                'job_title': notif.job.title,
                'job_url': notif.job.url,
                'category_id': notif.job.category_id,
                'category_name': notif.job.category.name
            })
        
        return user_notifications
        
    finally:
        db.close()


def group_jobs_by_category(notifications: List[Dict]) -> Dict[int, List[Dict]]:
    """
    Group notifications by category for a single user
    Returns: {category_id: [job_data, ...]}
    """
    categories = {}
    for notif in notifications:
        cat_id = notif['category_id']
        if cat_id not in categories:
            categories[cat_id] = {
                'category_name': notif['category_name'],
                'jobs': []
            }
        
        categories[cat_id]['jobs'].append({
            'title': notif['job_title'],
            'url': notif['job_url']
        })
    
    return categories


async def send_pending_notifications() -> Dict[str, int]:
    """
    Process and send all pending notifications
    Groups jobs by category for each user to minimize emails
    Returns stats: {sent: int, failed: int}
    """
    stats = {"sent": 0, "failed": 0, "emails_sent": 0}
    db = SessionLocal()
    
    try:
        # Get pending notifications grouped by user
        user_notifications = get_pending_notifications_by_user()
        
        if not user_notifications:
            logger.info("No pending notifications to send")
            return stats
        
        logger.info(f"Processing notifications for {len(user_notifications)} users")
        
        # Process each user
        for user_id, notifications in user_notifications.items():
            try:
                # Group jobs by category
                categories = group_jobs_by_category(notifications)
                
                # Get user details from first notification
                user_email = notifications[0]['user_email']
                user_token = notifications[0]['user_token']
                
                # Send one email per category
                for category_id, data in categories.items():
                    try:
                        success = await send_job_notifications(
                            email=user_email,
                            category_name=data['category_name'],
                            jobs=data['jobs'],
                            unsubscribe_token=user_token
                        )
                        
                        if success:
                            stats["emails_sent"] += 1
                        
                    except Exception as e:
                        logger.error(f"Failed to send email to {user_email} for category {category_id}: {e}")
                
                # Mark all notifications for this user as sent/failed
                notification_ids = [n['notification_id'] for n in notifications]
                db.query(Notification).filter(
                    Notification.id.in_(notification_ids)
                ).update({
                    'status': 'sent',
                    'sent_at': datetime.utcnow()
                }, synchronize_session=False)
                
                # Update user's last notified time
                db.query(User).filter(User.id == user_id).update({
                    'last_notified_at': datetime.utcnow()
                }, synchronize_session=False)
                
                db.commit()
                
                stats["sent"] += len(notifications)
                logger.info(f"✓ Sent notifications to {user_email}: {len(notifications)} jobs")
                
            except Exception as e:
                logger.error(f"Error processing notifications for user {user_id}: {e}")
                
                # Mark as failed
                notification_ids = [n['notification_id'] for n in notifications]
                db.query(Notification).filter(
                    Notification.id.in_(notification_ids)
                ).update({
                    'status': 'failed',
                    'error_message': str(e)
                }, synchronize_session=False)
                
                db.commit()
                stats["failed"] += len(notifications)
        
        logger.info(f"Notification batch complete: {stats}")
        return stats
        
    except Exception as e:
        logger.error(f"Error in send_pending_notifications: {e}")
        db.rollback()
        return stats
    finally:
        db.close()


async def process_new_jobs(new_jobs: List[Job], category_id: int):
    """
    Complete workflow: queue notifications and send them
    Called after scraper finds new jobs
    """
    if not new_jobs:
        return
    
    logger.info(f"Processing {len(new_jobs)} new jobs for category {category_id}")
    
    # Queue notifications
    queue_job_notifications(new_jobs, category_id)
    
    # Send immediately
    stats = await send_pending_notifications()
    
    logger.info(f"Processed notifications: {stats}")

