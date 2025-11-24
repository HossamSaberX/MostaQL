# MostaQL

MostaQL is a specialized job scraping and notification system designed to monitor Mostaql.com for new freelance opportunities. It features intelligent polling, advanced filtering based on hiring rates, and a dual-channel notification system.

## Features

### Core Logic & Scraping

*   **Smart Polling**: Optimizes bandwidth by checking the newest job ID first. Full scraping only occurs when new data is detected.
*   **Hiring Rate Enrichment**: Fetches individual job pages to parse hiring rates (budget/success score).
*   **Anti-Ban Strategy**: Implements User-Agent rotation, random delays, and connection validation to maintain access reliability.

### Notification Architecture

*   **Producer-Consumer Queue**: Implements a non-blocking custom Thread and Queue system. The scraper produces tasks while a background worker consumes them.
*   **Dual-Channel Support**: Delivers notifications via Email (SMTP/Brevo) and Telegram (Bot API).
*   **Smart Grouping**: Batches users with identical job sets for efficient processing while respecting individual `min_hiring_rate` filters.
*   **Graceful Lifecycle**: Uses a `Lifespan` context manager to ensure the queue finishes processing and worker threads exit cleanly during shutdown.

### Database & Performance

*   **Optimized SQLite**: Configured for high concurrency using WAL Mode (Write-Ahead Logging), `synchronous=NORMAL`, and increased cache size.
*   **Atomic Writes**: Ensures consistency of user state and notification logs through transaction-based operations.

### Ops & Security

*   **Broadcast System**: Includes a secure API endpoint protected by an Admin Secret to push maintenance alerts to all users.
*   **Infrastructure**: Fully Dockerized application using FastAPI and Caddy.
*   **Reverse Proxy**: Caddy manages automatic HTTPS (Let's Encrypt) and gzip compression.
*   **CI/CD**: Automated deployment pipeline via GitHub Actions.
*   **Security**: Implements rate limiting via SlowAPI on public endpoints and strict environment variable management for secrets.

## Quick Start

### Option A: Docker (Recommended)

1.  **Clone & Configure**
    ```bash
    git clone https://github.com/HossamSaberX/MostaQL.git
    cd MostaQL
    cp .env.example .env
    ```

2.  **Edit Environment**
    Open `.env` and set your secrets (Telegram Token, Email Credentials).
    ```bash
    nano .env
    ```

3.  **Launch**
    ```bash
    docker-compose up -d --build
    ```

### Option B: Local Development

1.  **Install Dependencies** (using `uv` or `pip`)
    ```bash
    ./setup_uv.sh  # or pip install -r requirements.txt
    ```

2.  **Run Database Migrations**
    ```bash
    alembic upgrade head
    ```

3.  **Start Backend**
    ```bash
    python -m backend.main
    ```

## Configuration

The system is configured via environment variables. Copy `.env.example` to `.env`.

| Variable | Description | Default |
| :--- | :--- | :--- |
| `SCRAPER_INTERVAL_MINUTES` | Time between full scrapes | `30` |
| `SCRAPER_POLL_INTERVAL_MINUTES` | Time between "newest ID" checks | `2` |
| `EMAIL_PROVIDER` | `gmail`, `brevo`, or `alternate` | `gmail` |
| `TELEGRAM_BOT_TOKEN` | Your Telegram Bot Token | Required |
| `DATABASE_URL` | SQLite connection string | `sqlite:///./data/mostaql.db` |

