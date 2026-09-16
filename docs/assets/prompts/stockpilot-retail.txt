# ROLE
You are a senior Python/Django engineer. Build the backend for a retail inventory and reorder platform. You are the DEVELOPER only — a DevOps engineer owns containers, cloud, CI/CD, security and monitoring. Do not touch any of those.

# PRODUCT: StockPilot
Tracks stock across a small retail chain (14 stores, ~9 000 SKUs), generates purchase orders when stock drops below reorder points, and exposes daily reports. ~600 requests/minute peak, plus nightly batch jobs.

# FUNCTIONAL REQUIREMENTS (Django 5 REST API + Celery)
1. Auth: session or JWT (`SIMPLE_JWT`), roles `STAFF`, `STORE_MANAGER`, `CENTRAL_BUYER`; `POST /api/token`, refresh flow, lockout after 10 failed logins for 15 min.
2. `GET /api/products?search=&category=&store=&page=` (paginated, DRF `PageNumberPagination`, max page size 200), `GET /api/products/{id}`, `POST/PUT/PATCH` restricted to `CENTRAL_BUYER`.
3. `GET /api/stock?store=&low=true` — current quantity per SKU per store with `is_below_reorder_point` boolean; `POST /api/stock/adjust {product, store, delta, reason}` writing an immutable `StockMovement` row (never mutate a quantity without a movement).
4. `POST /api/sales/bulk` — ingests a batch of sale lines (list of dicts, up to 5 000 per request), validates, writes movements, and returns per-line results with a status (`accepted|rejected:UNKNOWN_SKU|rejected:NEGATIVE`). Idempotent per batch via `Idempotency-Key` header.
5. Reorder engine: `POST /api/reorder/run` (or the celery beat task `evaluate_reorder_points`) — for each (store,product) below reorder point, create/append to a draft `PurchaseOrder` with quantity = `max(0, reorder_qty - on_hand)`; must not duplicate an open PO for the same pair; must be safe to run twice (`transaction.atomic` + `select_for_update(skip_locked=True)`).
6. `GET /api/purchase-orders?status=&store=`, `POST /api/purchase-orders/{id}/approve`, `POST /api/purchase-orders/{id}/receive` (increments stock, closes PO, writes movements), `POST /api/purchase-orders/{id}/cancel`.
7. Reports: `GET /api/reports/daily?date=&store=` (sales value, units, stock-outs, POs raised, top 10 SKUs), `GET /api/reports/stockouts?from=&to=` — served from a `daily_sales_summary` table updated by a Celery task, not from a live `JOIN` across millions of rows.
8. Health: `GET /health` (process), `GET /health/ready` (DB + broker), `GET /metrics` (Prometheus client, exposing `django_http_*`, `celery_queue_depth{queue=}`, `celery_task_latency_seconds`, `stockout_events_total`, `reorder_run_duration_seconds`, `sales_batch_rejected_total{reason=}`).

# DATA MODEL (PostgreSQL 16, managed by Django migrations only — no `migrate` at web start-up)
`Store`, `Product(sku unique, name, category, unit_cost, reorder_point, reorder_qty, active)`, `StockLevel(store, product, on_hand)` with unique `(store, product)`, `StockMovement(store, product, delta, reason, source SALE|ADJUSTMENT|RECEIPT|COUNT, actor, created_at, immutable)`, `PurchaseOrder(store, status DRAFT|APPROVED|RECEIVED|CANCELLED, created_by, approved_by, totals)`, `PurchaseOrderLine`, `SalesBatchImport(idempotency_key unique, status, counts)`, `DailySalesSummary(date, store, units, value, stockouts)`.
Add indexes for every query above and a CHECK constraint preventing negative `on_hand`.

# CELERY / WORKERS
Broker: Redis 7 (`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`), `acks_late=True`, `reject_on_worker_lost=True`, one retry with exponential backoff for transient DB errors only, `task_time_limit=300`, `soft_time_limit=280`, beat schedule `evaluate_reorder_points` every 15 min and `refresh_daily_summary` at 00:10 in `APP_TIMEZONE`. Every task records its own duration metric and stores failures with full context in a `TaskFailure` row for the runbook.

# NON-FUNCTIONAL REQUIREMENTS
- Configuration from env vars only, read in `config/settings/base.py` with a `env()` helper that raises a clear error when a required var is missing and never prints its value: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `APP_TIMEZONE`, `LOG_LEVEL`, `DB_CONN_MAX_AGE`, `GUNICORN_WORKERS`, `SENTRY_DSN` (optional; if unset, no error — the DevOps engineer decides about APM).
- WSGI via Gunicorn: `web: gunicorn config.wsgi:application --bind 0.0.0.0:8080 --workers 3 --threads 2 --timeout 60 --graceful-timeout 25 --max-requests 2000 --max-requests-jitter 200 --access-logfile - --error-logfile -` (documented in the README; `--access-logfile -` and `--error-logfile -` are mandatory, no file handlers).
- Logging: structured JSON to stdout via a `logging` dict config (no `RotatingFileHandler`); fields `ts, level, logger, msg, request_id, user_id_hash, path, status, dur_ms`; `request_id` middleware honouring `X-Request-ID`; never log `X-CSRFToken`, cookies or passwords; `Django-Request` logs suppressed for `/health`.
- Reliability: `ATOMIC_REQUESTS` off, explicit `transaction.atomic()` in writes; `select_for_update` on stock rows; all external calls (none required here) would be time-boxed; graceful shutdown drains Celery with `worker_soft_shutdown` semantics; `SECURE_PROXY_SSL_HEADER` documented for when a load balancer terminates TLS.
- Performance: p95 < 200 ms for `GET /api/stock?low=true` over 9 000 SKUs × 14 stores; `select_related`/`prefetch_related` used deliberately; the daily summary is a materialised table, and the query plans for the report endpoints go in `docs/`.
- Tests: pytest-django + factory-boy, 45+ tests, covering: negative-stock prevention, reorder-run idempotency (running twice creates nothing new), `skip_locked` behaviour under two concurrent runs, sales-batch partial rejection, PO approval/receipt flow, JWT lockout, and one test that the health/ready endpoint reports broker-down correctly (fake the broker). `pytest -q --cov=. --cov-fail-under=80`.
- Docs: `docs/OPERATIONS.md` (start commands for web/worker/beat, migration commands, the Celery queues and what each consumes, how to safely restart beat, what happens if two beat instances run, queue-depth thresholds, disk/temp behaviour, known limits); `docs/RUNBOOK-DEV.md` for common 500s and how to reproduce them locally.

# DELIVERY
Repository layout: `manage.py`, `config/` (settings `base/dev/prod`), one Django app per domain (`products`, `stock`, `ordering`, `reporting`, `accounts`), `requirements/` (`base.in`, `base.txt`, `dev.txt` — pip-tools layout with hashes: `--generate-hashes`), `.env.example`, `pyproject.toml` (black/ruff/mypy config), `.gitignore`, `README.md`, `docs/`. No TODOs, no stubs, no `DEBUG=True` paths that can be reached in production, `python-dotenv` for local only.

# HARD CONSTRAINTS — DEVELOPER ONLY (violations = rejected handoff)
- No Dockerfile, `.dockerignore`, compose files for deployment, no image build/registry/push instructions.
- No Terraform/CloudFormation/Bicep/Pulumi, no cloud resources, no AWS/Azure/GCP SDK or CLI usage, no IAM, no RDS/EKS/ECS/Redis creation scripts.
- No Jenkinsfile, GitHub Actions, GitLab CI, Azure DevOps YAML, Helm charts, Kubernetes manifests, Argo/Flux.
- No SonarQube/Trivy/Checkov/gitleaks/ZAP configuration, no SBOM, signing, or gate scripts.
- No Prometheus scrape config, Grafana dashboards, Alertmanager or cloud monitoring setup (exposing `/metrics` is required; collecting is not).
- No Nginx/uwsgi/supervisor/systemd configuration, no TLS certificates, no deploy scripts, no shell scripts that provision or reconfigure servers (`scripts/` may contain app-level helpers like `make migration-check`).
- No `git init`, no remote repository creation, no push/branch/tag operations.

If something seems to need one of the above, implement only the application-side hook and record the decision the DevOps engineer must make in `docs/OPERATIONS.md`.

# OUTPUT FORMAT
1. Plan: file list (max 15 lines).
2. Code, file by file, each with a `# path/to/file.py` header.
3. Django migrations for the full schema (plus the SQL for the indexes and the CHECK constraint).
4. `README.md`, `docs/OPERATIONS.md`, `docs/RUNBOOK-DEV.md`.
5. Final `DEVOPS HANDOFF NOTES`: exact web/worker/beat start commands, port, every env var (required vs optional, defaults), health endpoints, migration command + why it must not run at web start, celery queues, expected cold start, external dependencies (PostgreSQL 16, Redis 7), and every assumption.

Target 40–60 files. Standard Django patterns; boring over clever.
