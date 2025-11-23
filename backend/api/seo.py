from fastapi import APIRouter, Request, Response, Depends
from sqlalchemy.orm import Session
from backend.database import get_db, Job
from backend.config import settings
from datetime import datetime

router = APIRouter()

@router.get("/robots.txt", response_class=Response)
def robots_txt():
    """
    Generate robots.txt file.
    """
    base_url = settings.base_url.rstrip('/')
    
    content = f"""User-agent: *
Allow: /
Disallow: /api/
Disallow: /unsubscribe.html
Disallow: /unsubscribe-request.html

Sitemap: {base_url}/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")

@router.get("/sitemap.xml", response_class=Response)
def sitemap_xml(request: Request, db: Session = Depends(get_db)):
    base_url = settings.base_url.rstrip('/')
    
    # Static routes
    urls = [
        {"loc": f"{base_url}/", "changefreq": "daily", "priority": "1.0"},
        # Add other public static pages here
    ]
    
    xml_content = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_content.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    
    for url in urls:
        xml_content.append('  <url>')
        xml_content.append(f'    <loc>{url["loc"]}</loc>')
        if "lastmod" in url:
            xml_content.append(f'    <lastmod>{url["lastmod"]}</lastmod>')
        xml_content.append(f'    <changefreq>{url["changefreq"]}</changefreq>')
        xml_content.append(f'    <priority>{url["priority"]}</priority>')
        xml_content.append('  </url>')
        
    xml_content.append('</urlset>')
    
    return Response(content="\n".join(xml_content), media_type="application/xml")
