"""
Tests for scraper functionality
"""
import pytest
from bs4 import BeautifulSoup
from backend.services.scraper import (
    parse_job_listing, get_random_user_agent, hash_content
)


def test_get_random_user_agent():
    """Test user agent randomization"""
    ua1 = get_random_user_agent()
    ua2 = get_random_user_agent()
    
    assert isinstance(ua1, str)
    assert len(ua1) > 0
    assert "Mozilla" in ua1


def test_parse_job_listing_valid():
    """Test parsing valid job HTML"""
    html = """
    <div class="project-listing">
        <h2 class="mrg--bt-reset">
            <a href="/projects/12345">Test Job Title</a>
        </h2>
    </div>
    """
    soup = BeautifulSoup(html, 'html.parser')
    job_element = soup.find('div', class_='project-listing')
    
    result = parse_job_listing(job_element)
    
    assert result is not None
    assert result['title'] == "Test Job Title"
    assert "mostaql.com" in result['url']


def test_parse_job_listing_invalid():
    """Test parsing invalid job HTML"""
    html = """
    <div class="project-listing">
        <p>No job here</p>
    </div>
    """
    soup = BeautifulSoup(html, 'html.parser')
    job_element = soup.find('div', class_='project-listing')
    
    result = parse_job_listing(job_element)
    
    assert result is None


def test_hash_content():
    """Test content hashing for deduplication"""
    from backend.utils.security import hash_content
    
    content1 = "Test job title"
    content2 = "Test job title"
    content3 = "Different title"
    
    hash1 = hash_content(content1)
    hash2 = hash_content(content2)
    hash3 = hash_content(content3)
    
    assert hash1 == hash2
    assert hash1 != hash3
    assert len(hash1) == 64


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

