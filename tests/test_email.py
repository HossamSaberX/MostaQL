"""
Tests for email service
"""
import pytest
from unittest.mock import patch, MagicMock
from backend.services.email_service import (
    render_job_html, send_verification_email, send_job_notifications
)


def test_render_job_html():
    """Test job HTML rendering"""
    job = {
        'title': 'Test Job',
        'url': 'https://mostaql.com/projects/12345'
    }
    
    html = render_job_html(job)
    
    assert 'Test Job' in html
    assert 'mostaql.com/projects/12345' in html
    assert '<div class="job">' in html


@pytest.mark.asyncio
@patch('backend.services.email_service.resend.Emails.send')
async def test_send_verification_email_success(mock_send):
    """Test sending verification email"""
    mock_send.return_value = {'id': 'test_email_id'}
    
    result = await send_verification_email('test@example.com', 'test_token_123')
    
    assert result is True
    mock_send.assert_called_once()


@pytest.mark.asyncio
@patch('backend.services.email_service.resend.Emails.send')
async def test_send_verification_email_failure(mock_send):
    """Test verification email failure handling"""
    mock_send.side_effect = Exception("Email service error")
    
    result = await send_verification_email('test@example.com', 'test_token_123')
    
    assert result is False


@pytest.mark.asyncio
@patch('backend.services.email_service.resend.Emails.send')
async def test_send_job_notifications_success(mock_send):
    """Test sending job notifications"""
    mock_send.return_value = {'id': 'test_email_id'}
    
    jobs = [
        {'title': 'Job 1', 'url': 'https://mostaql.com/projects/1'},
        {'title': 'Job 2', 'url': 'https://mostaql.com/projects/2'}
    ]
    
    result = await send_job_notifications(
        email='test@example.com',
        category_name='برمجة',
        jobs=jobs,
        unsubscribe_token='test_token'
    )
    
    assert result is True
    mock_send.assert_called_once()


@pytest.mark.asyncio
@patch('backend.services.email_service.resend.Emails.send')
async def test_send_job_notifications_empty_jobs(mock_send):
    """Test notification with empty jobs list"""
    result = await send_job_notifications(
        email='test@example.com',
        category_name='برمجة',
        jobs=[],
        unsubscribe_token='test_token'
    )
    
    assert result is False
    mock_send.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

