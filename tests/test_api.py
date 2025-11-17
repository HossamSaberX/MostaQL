"""
Tests for API endpoints
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.main import app
from backend.database import Base, get_db, Category

TEST_DATABASE_URL = "sqlite:///./test_mostaql.db"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Override database dependency for testing"""
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


@pytest.fixture(scope="module")
def setup_database():
    """Setup test database"""
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    categories = [
        Category(name="برمجة", mostaql_url="https://mostaql.com/projects?category=1"),
        Category(name="تصميم", mostaql_url="https://mostaql.com/projects?category=2"),
    ]
    db.add_all(categories)
    db.commit()
    db.close()
    
    yield
    
    Base.metadata.drop_all(bind=engine)


def test_root_endpoint():
    """Test root endpoint"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert data["status"] == "running"


def test_health_endpoint(setup_database):
    """Test health check endpoint"""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"


def test_categories_endpoint(setup_database):
    """Test categories listing"""
    response = client.get("/api/categories")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_subscribe_valid(setup_database):
    """Test valid subscription"""
    response = client.post(
        "/api/subscribe",
        json={
            "email": "test@example.com",
            "category_ids": [1, 2]
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "email" in data


def test_subscribe_invalid_email():
    """Test subscription with invalid email"""
    response = client.post(
        "/api/subscribe",
        json={
            "email": "invalid-email",
            "category_ids": [1]
        }
    )
    assert response.status_code == 422


def test_subscribe_no_categories(setup_database):
    """Test subscription without categories"""
    response = client.post(
        "/api/subscribe",
        json={
            "email": "test2@example.com",
            "category_ids": []
        }
    )
    assert response.status_code == 422


def test_subscribe_too_many_categories(setup_database):
    """Test subscription with too many categories"""
    response = client.post(
        "/api/subscribe",
        json={
            "email": "test3@example.com",
            "category_ids": list(range(1, 12))
        }
    )
    assert response.status_code == 422


def test_verify_invalid_token():
    """Test verification with invalid token"""
    response = client.get("/api/verify/invalid_token_12345")
    assert response.status_code == 200
    assert "status=invalid" in response.headers.get("location", "")


def test_unsubscribe_invalid_token():
    """Test unsubscribe with invalid token"""
    response = client.get("/api/unsubscribe/invalid_token_12345")
    assert response.status_code == 200
    assert "status=invalid" in response.headers.get("location", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

