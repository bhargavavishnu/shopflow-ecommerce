# ShopFlow — Full-Stack E-Commerce Platform with Order & Inventory Management

A full-stack e-commerce platform featuring catalog search, persistent cart, transactional checkout, Stripe test-mode payments, asynchronous Celery order confirmation/inventory alerts via Redis, and a React storefront.

## Architecture

```
+--------------------+     REST JSON      +----------------------------------+
| React Storefront   | -----------------> | Django REST Framework API (:8000)|
| Search, Cart, Track|                    | Catalog, Cart, Checkout, Orders  |
+--------------------+                    +----------------+-----------------+
                                                           |
               +-------------------------------------------+--------------------+
               |                                                                |
               v                                                                v
+------------------------------+                                +------------------------------+
| PostgreSQL 17                |                                | Redis + Celery Worker        |
| - GIN SearchVector Index     |                                | - Async Confirmation Emails  |
| - SELECT FOR UPDATE Locking  |                                | - Low-Stock Inventory Audit  |
| - Stripe Test Intent IDs     |                                +------------------------------+
+------------------------------+
```

## Key Features

- **Transactional Inventory Locking (`backend/store/services/checkout.py`):** Wraps checkout in `transaction.atomic()` with deterministically ordered `Inventory.objects.select_for_update().order_by("product_id")` locks to eliminate race conditions and prevent overselling under concurrent requests.
- **Stripe Test-Mode Payment Integration (`backend/store/services/payments.py`):** Charges totals through the official `stripe` Python SDK (`PaymentIntent.create`) when configured with a `sk_test_...` API key, or through an explicit test-mode simulator (`tok_visa` / `tok_declined`) when running without live credentials. Declined payments trigger full SQL transaction rollback.
- **Celery + Redis Asynchronous Pipeline (`backend/store/tasks.py`):** Schedules `send_order_confirmation_email` and `audit_low_stock_inventory` via `transaction.on_commit(...)` after paid orders commit.
- **PostgreSQL Full-Text Search Across 1,000 Seeded Products:** Uses a dedicated indexed `SearchVectorField` + `GinIndex` (`product_search_gin_idx`) weighted across SKU, title, and description with `SearchQuery(..., search_type="websearch")` and `SearchRank`.

## PostgreSQL Full-Text Search Benchmark (1,000 Seeded Products)

Measured across 50 search queries against PostgreSQL 17 (`backend/scripts/benchmark_search.py`):

| Query Strategy | Mean (ms) | p50 (ms) | p95 (ms) |
| :--- | :---: | :---: | :---: |
| Naive Multi-Term `icontains` (`LIKE`) | `6.301` | `4.947` | `13.342` |
| PostgreSQL `SearchVector` + `GinIndex` | `1.785` | `1.570` | `3.509` |

- **Measured Improvement:** **71.7% latency reduction** (**3.53x speedup**) over naive substring `icontains` queries.

## Test Coverage & Verification (`pytest-cov`)

- **5 / 5 test suites passing** including a **multi-threaded `ThreadPoolExecutor` concurrency race test** (8 simultaneous threads competing for 3 remaining units -> exactly 3 orders committed, 5 blocked with `InsufficientStockError`, `stock_on_hand == 0`).
- **96.44% overall test coverage** on the `store` application (`93%` on `store/services/checkout.py`, `91%` on `store/services/payments.py`, `90%` on `store/tasks.py`, `98%` on `store/views.py`).

## Running Locally

> Note: `Dockerfile`s and `docker-compose.yml` are included for containerized execution (`docker compose up --build`). Local macOS development without Docker runs natively against PostgreSQL + Redis as shown below.

```bash
# 1. Sync backend Python dependencies & apply migrations
cd backend
uv sync
uv run python manage.py migrate

# 2. Seed ~1,000 catalog products & run full-text search benchmark
uv run python manage.py seed_products --count 1000
uv run python scripts/benchmark_search.py

# 3. Run pytest integration & concurrency suite with coverage
uv run pytest --cov=store --cov-report=term-missing --cov-fail-under=80

# 4. Start backend API & Celery worker
uv run python manage.py runserver 8000
# in another terminal:
uv run celery -A shopflow worker -l info

# 5. Build & run React storefront
cd ../frontend
npm install
npm run build
npm run dev
```
