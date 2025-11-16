#!/usr/bin/env python3
"""
Quick scraper test script
Run with: python test_scraper.py
"""
import sys
sys.path.insert(0, '/media/officerk/WD/College/Projectz/MostaQL')

from backend.services.scraper import scrape_category

# Test scraping the main projects page
print("🔍 Testing Mostaql scraper...")
print("=" * 60)

# Mostaql main projects page URL
test_url = "https://mostaql.com/projects"

print(f"Scraping: {test_url}")
print("-" * 60)

try:
    jobs = scrape_category(category_id=0, category_url=test_url)
    
    print(f"\n✅ Successfully scraped {len(jobs)} jobs!\n")
    
    if jobs:
        print("Sample jobs:")
        for i, job in enumerate(jobs[:5], 1):
            print(f"\n{i}. {job['title'][:80]}...")
            print(f"   URL: {job['url']}")
    else:
        print("⚠️  No jobs found. This might mean:")
        print("   - Mostaql changed their HTML structure")
        print("   - Your IP got blocked")
        print("   - Network issue")
        
except Exception as e:
    print(f"\n❌ Error: {e}")
    print("\nThis might mean:")
    print("   - Network connection issue")
    print("   - Mostaql is blocking requests")
    print("   - HTML structure changed")

print("\n" + "=" * 60)
print("Test complete!")

