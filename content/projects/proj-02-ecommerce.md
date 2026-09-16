LEAD: CartFlow is where packaging stops being optional. A Node service with two stateful dependencies, a cache, a worker and a webhook — and one rule: the same image runs in Compose on your laptop, on a staging box, and in ECS. You never write the app; you make it reproducible, observable and safe to restart.

# PART A — GET THE APPLICATION

## Overview
An e-commerce catalogue + cart + orders API: TypeScript on Node 20, MongoDB for records, Redis for cache and short stock reservations, an outbox for events, and a mock payment provider that misbehaves on purpose (20 % declines, 5 % timeouts).

## Business scenario
300 concurrent shoppers, 40 orders/minute peak, and one unforgivable outcome: selling an item you don't have. So the engineering requirements are not “fast”, they are *atomic stock decrement*, *idempotent webhook handling*, and *graceful degradation when Redis dies*. A DevOps engineer who understands the business rule writes better health checks and better alarms.

## Tech stack (received)
| Layer | Inherited | What you must operate |
| :-- | :-- | :-- |
| Runtime | Node 20, `npm ci` | Lockfile in Git → deterministic installs; `NODE_ENV`, memory flags |
| API | Express or Fastify | Which one changes nothing for you except `server.keepAliveTimeout` advice |
| Data | MongoDB 7 (replica set for transactions) | Connection strings, timeouts, and whether transactions are even available in a standalone instance |
| Cache/locks | Redis 7 | TTLs, eviction policy, what happens on eviction |
| Async | Outbox polling worker | A **second process** → a second container, or a second deployment |
| Ops surface | `/health`, `/health/ready`, `/metrics` | The three endpoints your whole platform hangs on |

## Architecture (as delivered)
::: flow
shoppers → CDN/LB → API pods (stateless) → MongoDB replica set (data) + Redis (cache, short locks) ← outbox worker → payment provider webhook (HMAC) → order events
:::
The API is horizontally scalable *because* nothing is in process memory — the cart lives in Mongo, the rate limiter in Redis. Verify that claim yourself: two containers, one request each, same user, and confirm the cart is shared.

## What the developer will build
Eight endpoint groups, atomic order creation with a real concurrency test (20 parallel requests for the last item, exactly one wins), webhook idempotency via a unique `eventId`, structured JSON logs with correlation ids, rate limits, graceful shutdown, 30+ tests, `docs/OPERATIONS.md` telling you the start command and the “what happens when Redis is down” behaviour. No Dockerfile, no CI, no infra — forbidden by the prompt.

## Copyable Developer Agent Prompt
::: prompt cartflow-ecommerce
:::

## Developer handoff
```bash
git clone https://github.com/harshath125/cartflow.git && cd cartflow
cat docs/OPERATIONS.md | head -40
npm ci && npm test                       # the handoff is only real if tests pass
grep -RIn "process.env" src/config* src/*.ts | head   # the full env contract, from the source
npm run seed && npm run start:prod &      # local smoke, then kill it
curl -s localhost:3000/health/ready | jq
```
Ask the developer (or re-prompt the agent) the five handoff questions, and write `docs/handoff.md`: run command, port, required env, what runs before traffic, disk usage, and “what is the one thing that must never happen” (overselling).

# PART B — THE DEVOPS JOURNEY

## Clone and inspect
```bash
git ls-files | wc -l ; git ls-files | grep -iE "env|pem|key|secret"
du -sh node_modules 2>/dev/null ; test -f package-lock.json && echo "lockfile: OK"
grep -n '"engines"' -A3 package.json ; node -v
find . -name "*.ts" -path "*test*" | wc -l          # how much of this is actually verified?
grep -RIn "MONGO_URL\|REDIS_URL" src/ | head
```
Two inspection habits that save hours later: confirm the **lockfile exists** (without it, `npm install` gives a different image every build), and confirm there's **no `.env` in history** (`git log --all --name-only | grep .env`).

## Identify dependencies, config, failure surface
| Question | Answer, and why it changes your design |
| :-- | :-- | :-- |
| Which ports? | `PORT` (3000) — internal; publish nothing until the LB is in front |
| Which external services must exist *before* the app is ready? | Mongo (hard), Redis (soft) → so `/health/ready` should fail on Mongo, and *succeed with a degraded flag* on Redis. Ask the developer to confirm; if readiness fails on Redis, your readiness will restart-loop pods whenever a node bounces |
| What must run first? | Index creation (`npm run migrate:indexes`) — an idempotent pre-deploy job |
| What writes to disk? | Nothing, and that's why we can set `readOnlyRootFilesystem` in 3 lines |
| Secrets? | `JWT_SECRET`, `WEBHOOK_SECRET` → runtime injection only; the webhook secret is also a *partner* contract, so rotating it is a two-step deploy |
| Long-lived connections? | Mongo driver pool 50 → with 20 pods that's 1 000 connections: check your server limit *before* autoscaling |

## Build and test as CI would
```bash
npm ci                 # not install: the lockfile or nothing
npm run lint && npm test
npm run build && node -e "require('./dist/main.js')" --check 2>/dev/null || node --check dist/main.js
npm audit --omit=dev ; npm ls mongoose            # who pulls what, and is it duplicated?
```
**What just happened:** `npm ci` gives you a reproducible `node_modules`; `--check` proves the compiled entry at least parses (cheap, and it catches a bad `tsconfig` output path); `npm audit` is a *report*, not a gate — Trivy is the gate, because `npm audit` uses the npm advisory DB, which ands its own rules and never sees transitive binaries. Note that `npm ls` showing **two copies of mongoose** is an image-size and behaviour bug, and exactly the kind of thing you can catch once in review and then enforce forever.

## Dockerize
```dockerfile
FROM node:20.12.2-alpine3.19 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM node:20.12.2-alpine3.19 AS build
WORKDIR /app
COPY package.json package-lock.json tsconfig.json ./
RUN npm ci
COPY src src
RUN npm run build && npm prune --omit=dev

FROM node:20.12.2-alpine3.19
ENV NODE_ENV=production
RUN addgroup -g 10001 app && adduser -u 10001 -G app -S -s /sbin/nologin appuser
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/dist ./dist
COPY --from=deps  --chown=10001:10001 /app/node_modules ./node_modules
COPY package.json ./
USER 10001
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=2s --start-period=15s --retries=5 \
  CMD wget -qO- http://127.0.0.1:3000/health/ready || exit 1
ENTRYPOINT ["node","dist/main.js"]
```
Three deliberate decisions to be able to defend: **`npm ci` twice** (dev deps for the build, prod-only for the run — the second stage is why the image is 145 MB instead of 620 MB); **`wget` not `curl`** (alpine's busybox has wget; a missing binary in a health check is a *silent* failure: the check always fails, and in Compose a failing health check makes `depends_on: service_healthy` wait forever); and **COPY the lockfile before installing** so a source-only change reuses the dependency layer.

## Build image, push, run the pair
```bash
docker build -t cartflow:sha-a1f9c2d .
docker run --rm cartflow:sha-a1f9c2d node -e "console.log(process.version)"   # 3-second sanity check
# compose harness: app + worker + mongo (replica set) + redis + seeded data
docker compose -f docker-compose.test.yml up -d --wait
docker compose ps
curl -s localhost:3000/health/ready | jq '.checks'
```
`--wait` honours every service's health check, so “up” means *ready*, not *started*. That flag alone removes the flakiest step in most CI pipelines.

## The Compose file that mirrors production
```yaml
services:
  api:
    image: registry.local/cartflow:sha-a1f9c2d
    command: ["node","dist/main.js"]
    environment:
      MONGO_URL: mongodb://mongo:27017/cartflow?replicaSet=rs0
      REDIS_URL: redis://redis:6379
      JWT_SECRET: ${JWT_SECRET:?required}
    ports: ["127.0.0.1:3000:3000"]
    depends_on: { mongo: { condition: service_healthy }, redis: { condition: service_started } }
    healthcheck: { test: ["CMD","wget","-qO-","http://127.0.0.1:3000/health/ready"], interval: 10s, retries: 5 }
    deploy: { resources: { limits: { cpus: "1.0", memory: 512M } } }
    read_only: true
    tmpfs: ["/tmp"]
    logging: { driver: json-file, options: { max-size: 10m, max-file: "3" } }
  worker:
    image: registry.local/cartflow:sha-a1f9c2d
    command: ["node","dist/worker/outbox.js"]
    depends_on: { api: { condition: service_started } }
  mongo:
    image: mongo:7.0.6
    command: ["--replSet","rs0","--bind_ip_all"]
    healthcheck: { test: ["CMD","mongosh","--quiet","--eval","db.adminCommand('ping').ok"], interval: 5s, retries: 20 }
  redis:
    image: redis:7.2.4-alpine
    command: ["redis-server","--maxmemory","256mb","--maxmemory-policy","allkeys-lru","--appendonly","no"]
volumes: { mongodata: {} }
```
Read the last two lines of `redis`: `allkeys-lru` means Redis *evicts* under pressure (a cache), and `appendonly no` means it loses everything on restart — which is **correct** here because a lost lock or a lost rate-limit counter is tolerable and a stalled eviction is not. Every “why is Redis not durable?” answer is a design decision you can now make out loud.

## Registry, then one ECS service
```bash
aws ecs create-cluster --cluster-name cartflow --capacity-providers FARGATE_SPOT,FARGATE \
  --default-capacity-provider-strategy strategy=FARGATE_SPOT,weight=1
aws ecs register-task-definition --cli-input-json file://task.json    # image by DIGEST, not tag
aws ecs run-task --cluster cartflow --task-definition cartflow:12 --network-configuration \
  "awsvpcConfiguration={subnets=[subnet-a,subnet-b],securityGroups=[sg-app],assignPublicIp=DISABLED}"
aws ecs wait tasks-stopped --tasks $(aws ecs list-tasks --cluster cartflow --query taskArns[0] --output text)
```
`awsvpc` network mode gives the task its own ENI and IP — so **the security group is on the task**, which is better than EC2-mode where every task on a host shares the host's SG. Log that difference in your notes; it's an AKS/GKE/ECS-comparison question in disguise.

## Create infrastructure with Terraform
`modules/network` (public subnets for the ALB, private for tasks, one NAT for dev / per-AZ NAT for prod, S3 + ECR + Logs endpoints), `modules/data` (DocumentDB or a self-managed Mongo replica *only* if the business accepts it — the honest default here is Atlas or EC2 + your own backup story, and a written risk note), `modules/app-service` (ECS service, ALB, target group on `/health/ready`, autoscaling on `cpu 70 %` and `rc:requests 900` on the ALB metric, an SQS+DLQ for the outbox fan-out). Read the plan line by line; `terraform plan` on a Redis `parameter_group_name` change should not be a surprise.

## Networking
ALB on 443 with an ACM cert, tasks in private subnets, `sg-alb → 443 from 0.0.0.0/0`, `sg-task → 3000 from sg-alb`, `sg-mongo → 27017 from sg-task` (and **only** VPC peering or a private link to Atlas; never `0.0.0.0/0`, which has happened to real companies). Redis: same pattern, 6379 from `sg-task`, `auth_token` on or a subnet so tight it doesn't need one. Add `aws elbv2 describe-listeners` to your docs so the next engineer sees the health path immediately.

## CI/CD (GitHub Actions, because the app is on GitHub and OIDC is free)
```
lint + test (ubuntu, node 20, cache key = hashFiles('package-lock.json'))
→ trivy fs (HIGH,CRITICAL) + gitleaks
→ docker build (buildx, gha cache) → trivy image → cosign sign → push immutable tag + sha tag
→ deploy dev (auto) → verify: /health/ready + 12 smoke requests → deploy staging (auto) → [prod: environment approval]
→ deploy prod with aws ecs deploy --service cartflow --task-definition … (or CodeDeploy blue/green) → verify + 5-min soak on 5xx rate → done
```
One thing to notice: the worker is deployed **in the same release** as the API, and the pipeline asserts the two images have the *same digest*. Two digests is how you ship a worker that writes events the API can't read.

## DevSecOps
Trivy fs on `package-lock.json` (SBOM from it with `syft`), `gitleaks` over full history (`.env` in a commit is a rotation event, not a code fix), npm provenance check (`npm audit signatures`) to catch a typosquatted dependency, image scan + cosign + a Kyverno policy in the cluster that refuses unsigned images, `hadolint`/`dockle` on the Dockerfile, and Checkov on the Terraform (it will find the Redis `security_group` with `0.0.0.0/0` if you leave one). Order matters: secret scan before build so a leaked token never reaches a public registry log.

## Monitoring
Prometheus scrapes `/metrics`: request rate, p95/p99, 5xx ratio, `cart_to_order_conversion`, `stock_reservation_failures_total`, `event_loop_lag`, `mongo_up`/`redis_up` gauges. **Two of those are business signals**, and a DevOps engineer who graphs them is the person in the room: `stock_reservation_failures_total` rising *before* 5xx moves is a whole 20 minutes of lead time. Alert set: p99 > 500 ms 5m, 5xx > 1 %, `redis_up == 0` for 2m (warning: degraded, not page — that's the design decision from the readiness discussion), `event_loop_lag > 0.3` (the “Node is blocked” alarm people never set), Mongo connection pool saturation, and an `outbox_lag_seconds > 30` alarm that catches a stopped worker — which otherwise produces *no* error anywhere.

## Failure scenarios — break these on purpose
| Break | Symptom | Diagnosis → fix |
| :-- | :-- | :-- |
| Redis stopped | Latency up 4×, no 5xx | `redis_up == 0`, `GET /health/ready` shows `cache: degraded`; correct: warning + autoscale off. Wrong: restarting pods, which changes nothing |
| Mongo primary steps down | 5 s of `NodeSelectionTimeoutError`, then self-heal | driver `retryWrites=true`, `maxPoolSize`, and a readiness probe that *tolerates 3 failures* — an aggressive liveness here restarts everything during a failover |
| Oversell attempt (20 parallel for 1 item) | 19 × `409 OUT_OF_STOCK` | good; if you see 2 successes, the transaction isn't atomic — that's a **developer** bug you found with a DevOps test, and the conversation you have with them is the job |
| Webhook replayed 5× | `200` + `x-idempotent-replay: true` | verify `paymentEvents` unique index exists; if `E11000 duplicate key error` bubbles up as 500, the handler isn't idempotent |
| Bad `WEBHOOK_SECRET` on one replica | ~50 % of webhooks 401 | rolling deploy left an old task with the old secret → `deploymentController` rollout verified with `aws ecs describe-services`; never patch a secret onto one task |
| Image tag deleted | `CannotPullContainerError: request access denied for ...` | `aws ecr describe-images --image-ids imageTag=…`; re-push; add a post-push existence check in CI |
| Disk full on a host (logs) | containers fine, host `Evicted`/`DiskPressure` | `df -h`, `du -x -h /var/lib/docker/containers | sort -h | tail -5`, `log_opt` was forgotten — the fix is a policy, not a `cron` cleanup |

## Troubleshooting (the 5xx spike you can only see in traces)
`describe-services` → tasks healthy, so the platform is fine. `/health/ready` fine. Traces show 4.1 s in `POST /orders`, all of it in `stock:reserve` waiting. `redis-cli --latency` fine; `SLOWLOG GET 10` shows `EVALSHA … 380ms` — a Lua script doing `KEYS *`. Cause: a developer changed the reservation script to scan; under 4 000 products it became O(n). **Fix forward:** the script uses a prefix index, `redis-cli --bigkeys` becomes a CI check on staging, and an alert on `stock_reserve_duration_seconds_p99 > 50ms` so next time you page before users notice. This is the pattern for every “everything is green but users are unhappy” incident: **look one layer below your dashboard, in the dependency.**

## Scaling
Requests per task from the load test (a single 0.5 vCPU/1 GB task holds ~120 rps at p95 < 150 ms); so 400 rps → 4 tasks with headroom → target-tracking `cpu 60 %` + `requests_per_task 100`, `min=2` (never 1 in two AZs), `max=12`, plus **scheduled scale** for the 20:00 drop sale, and `scale-in` protection during a deploy (`--enable-execute-command` off, `deploymentMaximumPercent 25`). DB side: read preference `secondaryPreferred` for `GET /products`, and if `DatabaseConnections` approaches Mongo's `maxIncomingConnections`, that's a PgBouncer-equivalent (mongos/router or a shorter idle timeout) — a conversation to have *before* Black Friday.

## Rollback
Rollback = point the ECS service at the previous task-definition revision, because the *task definition* is the versioned deployment object (`aws ecs update-service --task-definition cartflow:11`) and image digests make it exact. Mongo is the constraint: the index migration was additive and backward-compatible, so no data rollback is needed — which was the *rule* you enforced in review (“migrations must not break the previous release for one deploy window”), not luck. And the rollback path is exercised in staging with the same command, timed: “under 60 seconds, no dropped requests beyond in-flight”.

## Final architecture
::: flow
internet → Route 53 → ALB (TLS from ACM, /health/ready target checks) → ECS Fargate service (api, awsvpc, sg from ALB only, 2 AZ, requests/limits, log driver awslogs 30d, read-only fs) → DocumentDB/Mongo RS (private subnet, KMS, snapshots, no public IP) + ElastiCache Redis (cluster-mode off, 2 replicas) → outbox worker (same image, different command) → SNS → order email lambda → Grafana (RED + business panels) → all of it from Terraform with a locked backend, applied only by the pipeline
:::

## What I learned
The image is the unit of deployment (worker and API share it, or you shipped two truths); health checks must match the app's real dependency semantics or autoscaling fights your dependency; and a business metric on the dashboard shortens incidents more than another log pipeline does.

## Interview questions
::: grid2
**“Why two `npm ci` in a Dockerfile?”** → the build needs devDependencies (typescript, test runner), the runtime must not ship them: the multi-stage split is where the 4× size and CVE reduction comes from, plus a smaller attack surface.
**“How do you keep a Node app alive when Redis dies?”** → readiness distinguishes hard vs soft dependencies, cache calls are wrapped with a short timeout + fallback to Mongo, `redis_up` drives a warning and a pause on autoscaling, and a load test in staging that simply stops Redis proves the behaviour instead of assuming it.
**“Your ECS tasks are restarting. Steps?”** → `describe-tasks --include-events` for the stop reason and exit code, `stoppedReason` (`OutOfMemoryError` vs a health check vs a scale-in), the task's log stream around the restart, and whether a deploy or an instance drained underneath them.
**“What's the difference between `depends_on` and a health check?”** → `depends_on` orders *start*, `service_healthy` gates on *readiness*, and neither is a substitute for the app's own retry logic when the dependency dies later.
:::

## Résumé bullets
::: callout note ONLY WHAT YOU ACTUALLY BUILT
- Containerised a TypeScript order-management API (Node 20, MongoDB, Redis) with multi-stage Alpine images: 145 MB, non-root, read-only filesystem, health-checked; enforced `npm ci` from the lockfile and BuildKit layer caching to cut builds from 9 to 3 minutes.
- Built a GitHub Actions pipeline (lint, test, gitleaks, Trivy fs + image, cosign sign, immutable ECR tags) with auto-deploy to dev and staging, environment-approved production behind `aws ecs deploy`, smoke verification, and a 5-minute error-rate soak before a release is marked successful.
- Ran CartFlow on ECS Fargate in `awsvpc` mode behind an ALB with readiness-based target health, target-tracking autoscaling on CPU and requests, and scheduled scaling for sales events; rollback to the previous task-definition revision in under 60 seconds, verified in staging.
- Defined SLOs with Prometheus/Grafana (p95 < 150 ms, 99.5 % availability) including business signals (cart→order conversion, stock-reservation failures, outbox lag), and turned a 4× latency regression into a `redis` slowlog finding before users filed tickets.
:::

::: revision QUICK REVISION — 5 minutes
**Concepts:** lockfile-driven builds · two-stage `npm ci` · distroless/alpine trade-offs (wget vs curl, no shell, `USER 10001` needs `--chown`) · hard vs soft dependencies in readiness · `awsvpc` task ENIs and per-task SGs · immutable tags + digest promotion · outbox worker = second deployment, same image · scale-in protection during deploys · task-definition revision as the rollback unit · Redis eviction policy as a design decision.
**Commands:** `npm ci && npm test` · `docker buildx build --cache-from type=gha` · `docker compose -f … up -d --wait` · `aws ecs register-task-definition --cli-input-json file://task.json` · `aws ecs update-service --service cartflow --task-definition cartflow:11` · `aws ecs describe-tasks --tasks $T --include-events` · `redis-cli --latency` / `SLOWLOG GET 10` · `mongosh --eval 'db.currentOp()'`.
**Architecture:** LB → API tasks (stateless, shared state in Mongo/Redis) → outbox worker → events; two dependencies with *different* failure semantics, and the probes/alarms reflect that.
**Common mistakes:** `npm install` in Docker (no lockfile respect) · health check on a shell tool that isn't in the image · readiness that fails on a soft dependency · deploying API and worker from different digests · `0.0.0.0/0` on Mongo “for the pipeline” · log rotation forgotten on the host.
**Troubleshooting checklist:** `describe-services`/`describe-tasks` → events + exit code → log stream → `/health/ready` components → dependency latency (`redis-cli --latency`, Mongo `currentOp`) → traces for the slow span → is the *cache* cold after a deploy (a 10-minute p95 hump is normal; a 10-hour one is not).
:::

Next: [Project 03 — LedgerPay (AWS + Terraform) →](proj-03-banking.html)
