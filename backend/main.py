"""
FastAPI application entry point
"""
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import (
    request_validation_exception_handler,
    http_exception_handler,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.database import init_db
from backend.utils.logger import app_logger
from backend.scheduler import start_scheduler, shutdown_scheduler
from backend.api import subscribe, verify, health, test
from backend.utils.limiter import limiter
from backend.services.notification_queue import email_task_queue
from backend.config import settings

scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown events
    """
    app_logger.info("Starting Mostaql Job Notifier...")
    
    os.makedirs("logs", exist_ok=True)
    
    app_logger.info("Starting email task queue...")
    email_task_queue.start()
    
    app_logger.info("Initializing database...")
    init_db()
    
    app_logger.info("Starting scheduler...")
    global scheduler
    scheduler = start_scheduler()
    
    app_logger.info("✓ Application started successfully")
    
    yield
    
    app_logger.info("Shutting down application...")
    
    if scheduler:
        shutdown_scheduler(scheduler)
    
    email_task_queue.stop()
    
    app_logger.info("✓ Application shut down gracefully")


app = FastAPI(
    title="Mostaql Job Notifier",
    description="Automated job notification service for Mostaql freelancers",
    version="1.0.0",
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(subscribe.router, prefix="/api", tags=["subscribe"])
app.include_router(verify.router, prefix="/api", tags=["verify"])
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(test.router, prefix="/api/test", tags=["testing"])

frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    app_logger.info(f"✓ Serving frontend from: {frontend_dir}")


@app.get("/api")
async def root():
    """Root endpoint"""
    return {
        "service": "Mostaql Job Notifier",
        "status": "running",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Return consistent JSON for request validation errors"""
    app_logger.warning(f"Validation error on {request.url.path}: {exc.errors()}")
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Unified handler for HTTP exceptions raised by FastAPI/Starlette"""
    app_logger.warning(f"HTTP {exc.status_code} on {request.url.path}: {exc.detail}")
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all handler to prevent leaking internal errors"""
    app_logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "حدث خطأ غير متوقع، يرجى المحاولة لاحقاً"}
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=settings.environment == "development",
        log_level=settings.log_level.lower()
    )

