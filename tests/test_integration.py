"""
Integration tests for complete workflow
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.database import Base, User, Category, Job, UserCategory
from backend.utils.security import generate_token, hash_content


TEST_DATABASE_URL = "sqlite:///./test_integration.db"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def db_session():
    """Create test database session"""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    
    # Add test data
    category = Category(
        name="برمجة",
        mostaql_url="https://mostaql.com/projects?category=1"
    )
    session.add(category)
    session.commit()
    
    yield session
    
    session.close()
    Base.metadata.drop_all(bind=engine)


def test_user_subscription_flow(db_session):
    """Test complete user subscription flow"""
    # 1. Create user
    token = generate_token()
    user = User(
        email="integration@example.com",
        token=token,
        verified=False
    )
    db_session.add(user)
    db_session.flush()
    
    # 2. Add category subscription
    category = db_session.query(Category).first()
    user_category = UserCategory(
        user_id=user.id,
        category_id=category.id
    )
    db_session.add(user_category)
    db_session.commit()
    
    # 3. Verify user
    user.verified = True
    db_session.commit()
    
    # Assertions
    assert user.id is not None
    assert user.verified is True
    assert len(user.categories) == 1
    
    db_session.delete(user)
    db_session.commit()


def test_job_creation_and_notification_flow(db_session):
    """Test job scraping and notification matching"""
    # 1. Create verified user
    token = generate_token()
    user = User(
        email="job_test@example.com",
        token=token,
        verified=True
    )
    db_session.add(user)
    db_session.flush()
    
    category = db_session.query(Category).first()
    user_category = UserCategory(
        user_id=user.id,
        category_id=category.id
    )
    db_session.add(user_category)
    db_session.commit()
    
    # 2. Create new job
    job = Job(
        title="Test Project",
        url="https://mostaql.com/projects/test",
        content_hash=hash_content("Test Project"),
        category_id=category.id
    )
    db_session.add(job)
    db_session.commit()
    
    # 3. Match users to job
    users_for_category = db_session.query(User).join(UserCategory).filter(
        UserCategory.category_id == category.id,
        User.verified == True,
        User.unsubscribed == False
    ).all()
    
    # Assertions
    assert len(users_for_category) == 1
    assert users_for_category[0].email == "job_test@example.com"
    
    # Cleanup
    db_session.delete(job)
    db_session.delete(user)
    db_session.commit()


def test_user_unsubscribe_flow(db_session):
    """Test user unsubscribe workflow"""
    # 1. Create verified user
    token = generate_token()
    user = User(
        email="unsub_test@example.com",
        token=token,
        verified=True,
        unsubscribed=False
    )
    db_session.add(user)
    db_session.commit()
    
    # 2. Unsubscribe
    user.unsubscribed = True
    db_session.commit()
    
    # 3. Verify unsubscribed users are excluded
    active_users = db_session.query(User).filter(
        User.verified == True,
        User.unsubscribed == False
    ).all()
    
    assert user not in active_users
    
    # Cleanup
    db_session.delete(user)
    db_session.commit()


def test_token_uniqueness(db_session):
    """Test that tokens are unique"""
    token1 = generate_token()
    token2 = generate_token()
    
    assert token1 != token2
    assert len(token1) > 0
    assert len(token2) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

