# ROLE
You are a senior Node.js/TypeScript engineer. Build a small insurance platform as two services behind one gateway, for a fictional broker. You are the DEVELOPER only; a DevOps engineer owns containers, cloud, Kubernetes, pipelines, security tooling and monitoring.

# PRODUCT: CoverDesk
Sells simple policies (home, travel, gadget) and handles claims. 3 000 policies, 60 claims/day, a monthly quoting campaign that makes traffic 8× baseline for a week.

# SERVICES (one pnpm workspace, three packages, each independently deployable)
Shared stack: Node 20 LTS, TypeScript strict, Fastify 4, Zod validation, `pino` JSON logging to stdout, `@opentelemetry/api` for spans, no ORM magic that hides N+1s (use the driver or Prisma — choose one and justify in a comment).

## 1 · `gateway` (port 8080)
Reverse proxy + auth for `/policies/**` and `/claims/**` (upstreams from `POLICY_UPSTREAM`, `CLAIMS_UPSTREAM`), JWT verification (RS256, `JWKS_URI`), role claims `CUSTOMER|AGENT|CLAIMS_HANDLER`, request-id and correlation propagation, gzip off for JSON streaming, per-IP rate limit (in-memory token bucket, documented as a per-instance limit — not a distributed one) returning 429 with `Retry-After`, `helmet`-style headers, 2 MB body cap, timeouts (connect 2 s, response 15 s, no automatic retries on POST), health triple, `/metrics` Prometheus, and OpenAPI served at `/openapi.json`.

## 2 · `policy-service` (port 8081)
- `POST /quote {product, coverFrom, coverTo, inputs}` → priced quote with a `quoteId`, valid 24 h, `409 QUOTE_EXPIRED` afterwards; pricing is a pure function `base * productFactor * ageFactor * regionFactor * discountCode` with the factors from `config/pricing.json` (a data file, not code).
- `POST /policies` — consumes a quote, runs KYC-lite rules, issues a policy number, charges nothing (mock payment via an interface with a `PAYMENT_MODE=mock|fail|slow` env), sends an email through an injected `Mailer` (log-only by default), returns `201` with the schedule (monthly instalments) and an `idempotency` guarantee on `X-Request-Id`.
- `GET /policies?customerId=`, `GET /policies/{id}`, `POST /policies/{id}/cancel` (pro-rata refund calculation, status transitions `ACTIVE→CANCELLED` or `PENDING→VOID`), `PATCH /policies/{id}/address` (re-price, may create a premium change entry), `GET /policies/{id}/documents` → presigned-URL-like stubs.
- Rules: a policy cannot start in the past; cancellation after 30 days is `short-rate` (implement the calculation with tests, since a reviewer will check it); every premium change is an immutable row.
- Data (MongoDB 7): `policies`, `quotes(quoteId, expiresAt TTL index)`, `premiumChanges`, `documents`, `paymentEvents(idempotencyKey unique)`.

## 3 · `claims-service` (port 8082)
- `POST /claims {policyId, type, occurredAt, lossAmount, description, attachments[]}` — validates the policy is `ACTIVE` and the date in-period; returns `201` with `claimNumber` and `status=LODGED`.
- Workflow: `LODGED → ASSESSED → APPROVED|DECLINED → SETTLED` with a per-transition validator, an `assessorId` requirement, and an append-only `claimEvents` array with actor, timestamp, note, and the state before/after. Illegal transitions → `409`.
- `POST /claims/{id}/documents` (metadata only; storage is the platform's problem — document what you expect), `POST /claims/{id}/settle {amount}`, `GET /claims?status=&policyId=&handler=`, `GET /claims/{id}`, `PATCH /claims/{id}/assign {handlerId}`.
- Fraud rules as a *pure module* with tests: same bank details on >1 policy, loss amount > 90 % of sum insured, more than 2 claims in 12 months → `flags[]` on the claim and `status=NEEDS_REVIEW` instead of `APPROVED`.
- A nightly `reserve` job recomputes outstanding reserves per policy and writes an accounting summary row to Mongo (safe to re-run; idempotent by `(policyId, runDate)`).

# CROSS-CUTTING
- **Config**: env only, parsed with `envalid`/Zod and failing startup with the key name; `PORT`, `MONGO_URL`, `REDIS_URL` (rate limit + cache), `JWT_ISSUER`, `JWKS_URI`, `PAYMENT_MODE`, `MAIL_TRANSPORT=log|smtp`, `SMTP_URL`, `LOG_LEVEL`, `CORSA/ORIGINS`, `FEATURE_FLAGS` (a JSON blob in env, documented), `TERM_GRACE_MS=20000`.
- **Resilience**: no unhandled promise rejections (`process.on('unhandledRejection')` logs and exits 1 so the platform restarts cleanly), MongoDB driver `retryWrites=true`, `maxPoolSize` from env, `server.maxRequests` back-pressure, circuit breaker on the payment stub (open → `503 PAYMENT_UNAVAILABLE` + `Retry-After`), and *never* retry a non-idempotent POST.
- **Graceful shutdown**: stop accepting, finish in-flight, close Mongo/Redis, exit within 25 s; a `/internal/ready` that fails during shutdown so the LB drains first.
- **AuthN/Z**: JWT with `scope`/`role` checks per route; agent-created policies require `agentId` in the token; `CLAIMS_HANDLER` cannot self-approve their own claim (a real control that will make the tests interesting).
- **Validation & data hygiene**: every input through Zod; no `_id` echoed to clients; PII minimised in logs (email masked, claimant name hashed); attachments are metadata only with a `sha256`; no secrets or connection strings in any response body.
- **Tests**: Vitest, ≥ 20 per service; include: quote expiry, pricing table boundaries, cancel-pro-rata (a table of 8 cases), illegal state transitions, the fraud rules, idempotent re-runs of the reserve job, and one end-to-end test that quotes → buys → claims → settles against real Mongo via `mongodb-memory-server` or docker (documented).
- **Docs**: per-package `README.md`, root `README.md` with the run matrix (which process on which port, which env), `docs/OPERATIONS.md` (start/build/test commands, ports, health semantics, Mongo indexes needed and the command to create them, TTL index caveat, what happens if Mongo is slow vs down, back-pressure behaviour, the two flags worth toggling in an incident), `docs/DOMAIN.md` (state machine diagrams in text).

# DELIVERY
Layout: `packages/gateway`, `packages/policy-service`, `packages/claims-service`, `packages/shared`, `tests/`, `pnpm-workspace.yaml`, `turbo.json` (or equivalent task runner) — a build graph the pipeline can call per package (`pnpm --filter policy-service test`), `.env.example`, `.gitignore`, `docs/`, `eslint`/`prettier` configs. **The per-package build must be independently runnable** — the DevOps engineer builds one image per service from one repo, and needs `pnpm deploy --filter <pkg> --prod` (or an equivalent) to produce a pruned dependency set.

# HARD CONSTRAINTS — DEVELOPER ONLY
No Dockerfile or `.dockerignore`, no container/registry work, no compose for deployment, no Kubernetes/Helm/Kustomize/Argo/Flux manifests, no Terraform/Bicep/CloudFormation, no cloud SDK/CLI or resources or IAM, no `azure-pipelines.yml` or any CI YAML of any kind, no SonarQube/Trivy/Checkov/gitleaks/ZAP config, no SBOM/signing, no Application Insights or monitoring setup (expose `/metrics`; the platform collects), no nginx/ingress config, no TLS material, no deploy scripts, no `git init`/push/tag. Deliver files only, and record any operational decision you would otherwise have made in `docs/OPERATIONS.md`.

# OUTPUT FORMAT
1. Plan: file list per package (max 18 lines).
2. `shared`, then each package, file by file with `// path/to/file.ts` headers.
3. Docs, then `DEVOPS HANDOFF NOTES`: per-service build/test/start commands and the exact prune command for a single-service image, ports, env table, health endpoints, index creation command, cold start, assumptions, and a list of “things I expect the DevOps engineer to decide” (image base, non-root user, secret injection, LB timeouts, backup cadence).

Target 45–70 files. Keep each service small and boring; the value is in the boundaries, not the feature count.
