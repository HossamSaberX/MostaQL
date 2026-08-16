# MostaQL

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

MostaQL is a specialized job scraping and notification system designed to monitor Mostaql.com for new freelance opportunities. It features intelligent polling, advanced filtering based on hiring rates, and a dual-channel notification system.

## Features

### Core Logic & Scraping

*   **Complete Polling**: Scans each category listing (including bounded pagination) every poll so multiple projects published in the same minute are not hidden behind an unchanged first row. Normal polls stop after the first already-known page to limit traffic.
*   **Near-Real-Time Alerts**: Polling starts at application startup and repeats every two minutes by default. Deployments can set `SCRAPER_POLL_INTERVAL_SECONDS` (minimum five seconds) for a faster cycle; Mostaql does not provide a push webhook, so delivery is not zero-latency.
*   **Stable Project Identity**: Deduplicates by the canonical project URL, so different projects with the same title are retained.
*   **Hiring Rate Enrichment**: Fetches individual job pages to parse hiring rates (budget/success score).
*   **Precise Rate Filters**: Minimum hiring rates accept hundredths (for example, `50.01%`) and match inclusively (`rate >= minimum`).
*   **Past-Client Alerts**: Save Mostaql profile URLs for clients you previously completed work for. Every new project from a saved client is matched across all categories, regardless of the category subscriptions or smart filters. Matching uses the stable `/u/<username>` identity from the project page, so a private/deleted profile (HTTP 403) does not disable alerts.
*   **Anti-Ban Strategy**: Implements User-Agent rotation, random delays, and connection validation to maintain access reliability.

### Notification Architecture

*   **Producer-Consumer Queue**: Implements a non-blocking custom Thread and Queue system. The scraper produces tasks while a background worker consumes them.
*   **Dual-Channel Support**: Delivers notifications via Email (SMTP/Brevo) and Telegram (Bot API).
*   **Smart Grouping**: Batches users with identical job sets for efficient processing while respecting individual `min_hiring_rate` filters.
*   **Duplicate-Safe Client Matching**: A project matching both a category subscription and a followed client creates one notification per channel, with the followed-client reason shown in the alert.
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

### Follow clients from past work

After confirming your email, click **إدارة التفضيلات والعملاء السابقين** on the confirmation page. You can also use the same button in any notification email. Then expand **عملائي السابقون** and add the client's Mostaql profile URL (for example, `https://mostaql.com/u/client-name`). The watcher uses the canonical client profile URL, so trailing slashes and profile sub-pages are handled consistently. The next project published by that client will notify you even when it belongs to a category you did not select.

The alert worker fetches Mostaql pages itself with a public HTTP request; it does not use the cookies or login state of your Chrome/in-app browser. Logging in can help you open a private client page manually, but signing in or out will not turn server-side alerts on or off.

## Configuration

The system is configured via environment variables. Copy `.env.example` to `.env`.

| Variable | Description | Default |
| :--- | :--- | :--- |
| `SCRAPER_INTERVAL_MINUTES` | Time between full scrapes | `30` |
| `SCRAPER_POLL_INTERVAL_MINUTES` | Time between complete category listing polls (the first poll starts immediately) | `2` |
| `SCRAPER_POLL_INTERVAL_SECONDS` | Optional fast-alert override for the poll interval; takes precedence when set | unset |
| `SCRAPER_MAX_PAGES` | Maximum listing pages scanned per category and poll | `10` |
| `EMAIL_PROVIDER` | `gmail`, `brevo`, or `alternate` | `gmail` |
| `TELEGRAM_BOT_TOKEN` | Your Telegram Bot Token | Required |
| `DATABASE_URL` | SQLite connection string | `sqlite:///./data/mostaql.db` |

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

