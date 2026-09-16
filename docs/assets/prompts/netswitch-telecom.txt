# ROLE
You are a senior Python engineer. Build the backend for a telecom provisioning service. You are the DEVELOPER only; a DevOps engineer owns deployment, containers, Kubernetes, cloud, CI/CD, security tooling and monitoring. Do not do any of that work.

# PRODUCT: NetSwitch
Accepts service orders for a small operator (business broadband + SIP trunks), orchestrates provisioning steps against an inventory of network elements, and reports order status to a customer portal. 30 000 active orders, ~200 orders/hour, long-running multi-step workflows with retries and human fallback.

# FUNCTIONAL REQUIREMENTS (FastAPI + Python 3.12)
1. `POST /v1/orders` — create an order `{customer_id, product, site{address, access_type}, desired_date, contacts[]}`; validates address format, product rules (SIP requires at least one site with fibre access), returns `201` with an order id and `status=RECEIVED`.
2. `GET /v1/orders` (filters: `status`, `customer_id`, `created_after`, cursor pagination `limit<=200`), `GET /v1/orders/{id}` (with its step log), `POST /v1/orders/{id}/cancel` (only before the first step has started, else `409`), `POST /v1/orders/{id}/retry` (role `OPS`; re-drives a FAILED order from the failed step, not from the beginning).
3. `POST /v1/orders/{id}/assign` — assigns to a field engineer from the inventory service's roster (in-process stub allowed, but behind an interface so the DevOps engineer can replace it with a real service).
4. Provisioning workflow engine — steps in order: `VALIDATE_SITE → RESERVE_PORT → CONFIGURE_CPE → TEST_LINK → ACTIVATE → NOTIFY_CUSTOMER`. Each step: idempotent, has a timeout, records `{step, status, attempts, started_at, finished_at, external_ref, error_code}` in `order_steps`. A step that fails with a transient code is retried with exponential backoff + jitter (max 5); a permanent code sets `order.status=NEEDS_HUMAN` and emits one event. The engine must be safe to run concurrently for different orders and never double-drive the same order (`SELECT … FOR UPDATE SKIP LOCKED`).
5. `GET /v1/inventory/ports?city=&available=true` and `POST /v1/inventory/ports/{id}/reserve|release` — a tiny port inventory with the same reservation semantics (an expired reservation must be releasable by a sweeper task).
6. `POST /v1/webhooks/provisioner` — HMAC-SHA256 verified (`x-netswitch-signature`), idempotent by `event_id`, upserts step status from the external system, ignores out-of-order events older than the current state (log, don't fail).
7. `GET /health` (liveness: process + event loop lag), `GET /ready` (Postgres, Redis, and that the migration version equals the expected version — expose the version in the response body), `GET /metrics` (Prometheus: http metrics with a `route` label using the template not the path — cardinality!), `netswitch_orders_total{status}`, `netswitch_order_step_duration_seconds{step}`, `netswitch_step_failures_total{step,code}`, `netswitch_reservation_expired_total`, `netswitch_engine_last_poll_age_seconds`, `openapi.json`.
8. `GET /v1/events?order_id=` — the append-only timeline an operator (and the portal) reads when an order is stuck.

# NON-FUNCTIONAL REQUIREMENTS
- **Async, correctly:** `async def` handlers; all DB access via async SQLAlchemy 2.0 + asyncpg; no blocking calls in the event loop (any CPU/legacy sync work goes through `run_in_threadpool`); `uvicorn --workers` documented but the app must also behave as a single worker per container (that is the recommended production shape — say so in the docs).
- Configuration from env only, validated by `pydantic-settings` with `model_config = SettingsConfigDict(env_file=".env")` for local dev: `DATABASE_URL`, `REDIS_URL`, `PORT`, `WORKER_CONCURRENCY`, `STEP_TIMEOUT_SECONDS`, `RETRY_MAX_ATTEMPTS`, `WEBHOOK_SECRET`, `JWT_PUBLIC_KEY` (or `AUTH_DISABLED=true` for a dev-only path that must log a warning), `LOG_LEVEL`, `TZ`. Fail fast with a field name on missing config.
- Long-running engine as a **separate entrypoint** (`python -m netswitch.engine`) with graceful shutdown: stop polling, finish in-flight steps up to `TERM_GRACE_SECONDS=25`, mark nothing as failed merely because we shut down. Document exactly why this matters for an order system.
- Timeouts everywhere: HTTP client with connect 2 s / read 10 s, DB statement timeout `5s`, Redis socket timeout 2 s. Every external call has a retry budget and a circuit breaker that fails *open for reads, closed for writes*.
- Errors: `{"error":{"code":"ADDRESS_UNRESOLVABLE","message":"…","details":{…}}}` with stable codes; correlation id propagated; no stack traces to the client; validation errors never echo submitted PII back.
- Logging: JSON to stdout with `request_id`, `order_id`, `step`, `attempt`; PII masking for phone/email (hash, keep last 2 chars); no logging of full webhook payloads (log a hash + size).
- Tests: pytest + anyio + `httpx.ASGITransport`, 40+ tests including: two concurrent engine instances never double-drive one order; a retry that succeeds on attempt 3; an out-of-order webhook ignored; an expired reservation released by the sweeper; step idempotency (running the same step twice is a no-op); a `/ready` test where migrations are behind. Also a `pytest-benchmark` test asserting the order-creation hot path p95 < 40 ms against a local Postgres.
- Data: PostgreSQL 16 with tables `orders`, `order_steps`, `ports`, `reservations`, `events`, `webhook_events(idempotency key unique)`; proper FKs, `CHECK`s, partial indexes for the engine's queue query (`WHERE status IN ('QUEUED','RETRY_WAIT')`), and `advisory locks` where they beat row locks — documented choices, with migrations as SQL files applied by an external tool (no autocommit-on-boot).

# DELIVERY
Layout: `src/netswitch/{api,engine,db,clients,schemas,workers}`, `tests/`, `alembic/`, `pyproject.toml` (uv or pip-tools with hashes), `scripts/` (app-level only: `seed.py`, `drain.py`), `.env.example`, `README.md`, `docs/{OPERATIONS.md,ARCHITECTURE.md,RUNBOOK.md}`.
`docs/OPERATIONS.md` must state: the two entrypoints and their env differences, required env vars table, port, health/ready semantics (including the migration-version check), how to replay the engine for one order, how to clear a stuck order safely (SQL + why), queue-depth and step-age thresholds, expected cold start, and what to do when the webhook signature key rotates.

# HARD CONSTRAINTS — DEVELOPER ONLY
- No Dockerfile, `.dockerignore`, no container build/registry instructions.
- No Kubernetes manifests, Helm charts, Kustomize, Argo/Flux, `k8s/` directory — the DevOps engineer owns the cluster side and will decide resources, probes and PDBs.
- No Terraform/CloudFormation/Bicep, no cloud SDK/CLI, no IAM, no database provisioning code.
- No CI/CD files of any kind (Jenkinsfile, GitHub Actions, GitLab CI, Azure DevOps).
- No security tooling config (Sonar, Trivy, Checkov, gitleaks, ZAP), no SBOM/signing work.
- No monitoring stack config: no Prometheus scrape files, Grafana dashboards, Alertmanager, Loki, or cloud agents. Exposing `/metrics` is required; scraping it is not.
- No Ingress/Nginx/Traefik configuration, no TLS material, no deploy shell scripts, no `git` operations.

Implement only the application-side hook for anything that appears to need one of those, and record the required decision in `docs/OPERATIONS.md`.

# OUTPUT FORMAT
1. Plan (max 15 lines).
2. Code, file by file, each with a `# path/relative.py` header.
3. Alembic migrations (as code) plus generated SQL.
4. `README.md` + the three docs files.
5. Final `DEVOPS HANDOFF NOTES`: build command, both start commands, port, env table, health endpoints, migration commands, test command, external dependencies, cold start, concurrency model, and every assumption.

Aim for 35–55 files. Favour explicit state machines over clever abstractions.
