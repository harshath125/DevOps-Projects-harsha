# ROLE
You are a senior Java microservices engineer. Build a three-service FinTech backend. You are the DEVELOPER only; a DevOps engineer owns containers, Kubernetes, cloud, CI/CD, security tooling and monitoring. Do not touch those.

# PRODUCT: TallyWorks
A payments/transactions and reporting platform for a small lender: one gateway, one transaction service, one reporting service. Monolith-to-microservices migration scenario: the transaction service is the extracted core; reporting is new.

# SERVICES (three independent Maven modules, each its own runnable Spring Boot app)
Shared: Java 17, Spring Boot 3.2.x, Maven wrapper in each module, `spring-boot-starter-{web,actuator,validation,data-jpa}`, Flyway, Resilience4j, `micrometer-registry-prometheus`, structured JSON logging to stdout, correlation id propagation, no `System.out`.

## 1 · `gateway` (port 8080) — Spring Cloud Gateway
- Routes `/api/transactions/**` → transaction service, `/api/reports/**` → reporting service (upstream URLs from env: `TRANSACTION_URI`, `REPORTING_URI`).
- JWT validation (RS256, `JWKS_URI`), role claims `CUSTOMER|OPS|ANALYST`; 401/403 with the shared error envelope.
- Rate limit per `sub` (Bucket4j + Redis, 60/min, 429 + `Retry-After`), request/response size caps (2 MB), `X-Correlation-Id` mint-or-passthrough, timeouts (connect 2 s, response 10 s), circuit breaker on upstream failure (503 with `RETRY_AFTER` when open), and a `/health`, `/ready`, `/metrics` triple.
- No business logic. If you find yourself writing it, stop: the gateway must be replaceable by an API gateway product.

## 2 · `transaction-service` (port 8081) — the core
Endpoints: `POST /api/transactions` (idempotent via `Idempotency-Key`, 24 h), `GET /api/transactions?customerId=&from=&to=&status=&page=` (keyset pagination, max 500), `GET /api/transactions/{id}`, `POST /api/transactions/{id}/reverse` (compensating entry, `reason` required, 24 h window unless `OPS`), `POST /api/transactions/{id}/settle`, `GET /api/accounts/{id}/balance`, `GET /api/accounts/{id}/entries?from=&to=` (running balance), `POST /internal/transactions/{id}/retry-settlement` (role `OPS`).
Rules: double-entry ledger with a hard invariant `sum(debit)=sum(credit)` per transaction; an account balance may not go below its `overdraftLimit`; state machine `AUTHORIZED → CAPTURED → SETTLED` with `REVERSED|FAILED` branches and 409 on illegal transitions; `@Version` optimistic locking on accounts; settlement calls an external processor (interface + stub, retries with backoff, circuit breaker) via the **outbox** so no synchronous dependency on the request path.
Data (PostgreSQL 16): `Account(id, customerId, number, currency, balance, overdraftLimit, status, rowVersion)`, `Transaction(id, type, amount, currency, status, idempotencyKey unique, correlationId, createdAt, settledAt)`, `LedgerEntry(id, transactionId, accountId, direction, amount, balanceAfter, seq)` unique `(accountId, seq)`, `OutboxEvent(aggregateId, type, payload, status, attempts, nextAttemptAt)`. Constraints, FKs, and the indexes your queries need, all in Flyway migrations (`V1__init.sql` … `V3__seed.sql`).
Also: a `SETTLEMENT_BATCH` scheduled task (5 s interval, `@Scheduled` with a Postgres advisory lock so multiple instances are safe) that drains the outbox, plus `GET /internal/metrics/invariants` returning `{"balanceImbalance":0,"unsettledAgeSeconds":N,"outboxLagSeconds":N}` for alerting.

## 3 · `reporting-service` (port 8082)
Consumes `TransactionSettled`/`TransactionReversed` events from the outbox (poll `GET /internal/outbox?since=` if you prefer HTTP to a broker — document the choice) into a local projection `daily_summary(date, customerId, products…, debitTotal, creditTotal, count)`.
Endpoints: `GET /api/reports/daily?customerId=&from=&to=`, `GET /api/reports/top-movers?date=&limit=`, `POST /api/reports/rebuild?from=&to=` (idempotent, chunked at 5 000 rows per transaction with a commit between chunks — never one giant transaction), `GET /api/reports/jobs/{id}`.
Rules: rebuild must be safe to run while traffic flows (read from a shadow table, swap by view rename); stale-data age exposed as a metric; 202 + job id for long requests.

# CROSS-CUTTING REQUIREMENTS
- **Config**: environment variables only, with a `ConfigCheck` that fails startup naming the missing key: `DB_URL`, `DB_USER`, `DB_PASSWORD`, `JWKS_URI` or `JWT_PUBLIC_KEY`, `REDIS_URI` (gateway), `OUTBOX_*` tuning, `RESILIENCE4J_*` overrides, `SERVER_PORT`, `LOG_LEVEL`, `APP_TIMEZONE`. No `application-prod.yml` with real credentials in Git; a `.env.example` documents every key.
- **Contract**: OpenAPI per service in `src/main/resources/openapi.yaml`, committed, and a `contract-tests` module with Spring Cloud Contract *or* Pact tests (pick one; state why) that the pipeline can run standalone.
- **Graceful shutdown**: `server.shutdown=graceful`, `spring.lifecycle.timeout-per-shutdown-phase=20s`, and outbox draining must survive a restart (an in-flight batch is re-picked, never lost).
- **Logging**: JSON to stdout via Logback's encoder, fields `ts, level, logger, msg, app, correlation_id, customer_id_masked, txn_id`; account numbers masked to last 4; never log token, PAN or password fields; a `logging.pattern` that keeps stack traces single-line for Loki/CloudWatch.
- **Tests**: each service has ≥ 15 tests: unit (state machine, idempotency, invariant math), `@WebMvcTest` slices, and one Testcontainers-based integration test per service (Postgres, Redis). Plus one cross-service test in `contract-tests/`. `./mvnw verify` green from a clean checkout.
- **Observability**: `GET /actuator/{health,health/liveness,health/readiness,metrics,prometheus}` enabled and *secured* (health groups only on the internal port or path); each service publishes its RED metrics plus the domain counters above; a `docs/OBSERVABILITY.md` per service listing metric names + labels (the DevOps engineer will alert on them).

# DELIVERY
Layout: `gateway/`, `transaction-service/`, `reporting-service/`, `contract-tests/`, `common/` (shared error envelope + masking + correlation filter, as a Maven module with its own tests), `db/`, `docs/`, `README.md`, `.editorconfig`, `.gitignore` (target/, .idea, *.iml, .env), `LICENSE`.
`README.md` per service: purpose, endpoints, build/run command, env table, test command.
`docs/OPERATIONS.md`: per service — start command, port, health/readiness semantics, migration commands, outbox mechanics and what "safe to run twice" means here, the advisory-lock design and what happens if Postgres restarts mid-batch, expected cold start per service, how to replay the outbox for one aggregate, and how to rebuild reports safely.
`docs/DECISIONS.md`: three ADRs (why three services, why outbox over a broker, why optimistic locking).

# HARD CONSTRAINTS — DEVELOPER ONLY
No Dockerfile or `.dockerignore`, no container registry instructions, no docker-compose for deployment (a `docker-compose.dev.yml` for Postgres+Redis is allowed and should exist), no Kubernetes/Helm/Kustomize/Argo, no Terraform/CloudFormation/Bicep, no AWS/Azure/GCP SDK or CLI usage, no IAM or cloud resources, no Jenkinsfile or any CI YAML, no Sonar/Trivy/Checkov/gitleaks/ZAP configuration, no SBOM or signing scripts, no Prometheus/Grafana/Alertmanager config, no reverse-proxy or TLS configuration, no `deploy.sh`, no `git init`/push/branch/tag operations. Deliver files only. If a requirement appears to need one of these, implement the app-side hook and record the decision for the DevOps engineer in `docs/OPERATIONS.md`.

# OUTPUT FORMAT
1. Plan: file list per service (max 20 lines).
2. `common/` first, then each service file by file with `// path/to/File.java` headers, then migrations, then `contract-tests`.
3. `README.md`s and the three docs files.
4. Final `DEVOPS HANDOFF NOTES`: one section per service (build, package, run, port, env, health), plus a shared section: dependency order (does gateway need the others up? no — say so), how to roll back safely mid-settlement, and every assumption.

Target 60–90 files across the four modules; the whole point is a *realistic* multi-service repository, so keep each service small rather than making three monoliths.
