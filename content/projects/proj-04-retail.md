LEAD: StockPilot is three processes from one repository — web, Celery worker, Celery beat — on Fargate. That single fact makes it the best project for learning the thing beginners actually fumble: a *deployable unit* is not “the app”, it's a set of roles that must be started, stopped, scaled, scanned and rolled back independently while sharing one image and one migration.

# PART A — GET THE APPLICATION

## Overview
A Django 5 inventory and reorder platform for a 14-store retail chain: DRF API, PostgreSQL, Redis broker, Celery worker and beat, pip-tools with hashes, Gunicorn behind whatever proxy you choose.

## Business scenario
Empty shelves cost money, over-ordering costs cash, and both are invisible until a store manager complains. The nightly/15-minute reorder job *is* the product. So the operational goal is precise: the engine must run on time, run once, and be loudly broken when it doesn't. That maps to three things you'll build: `celery_queue_depth` alarms, an idempotency guarantee, and a runbook for a stuck queue.

## Tech stack (received)
| Layer | Inherited | Consequence for you |
| :-- | :-- | :-- |
| Django 5, Gunicorn | `--bind 0.0.0.0:8080`, `--max-requests 2000` | Worker recycling means brief connection churn; your LB health check must survive it (`--graceful-timeout 25` is why) |
| PostgreSQL 16 + Alembic/Django migrations | no autocommit on boot | Migration is a **pipeline step**, run by a one-off task |
| Celery worker + beat | two services, one image | Beat must be `replicas: 1` or you get duplicate purchase orders — the classic. Two instances are safe *only* if the task itself is idempotent (here it is by design; prove it) |
| Redis 7 as broker | `acks_late=True`, `task_time_limit=300` | `acks_late` + a hard-killed worker = a task redelivered; that's the point, but it needs idempotency |
| pip-tools `base.txt` with hashes | `--require-hashes` | Deterministic build; `pip install` without hashes in CI is a supply-chain hole you can close once, globally |

## Architecture (as delivered)
::: flow
stores / portal → ALB → web tasks (Gunicorn) → Postgres ; web → Redis (queue) → worker tasks ×N → Postgres ; beat (1 replica) → schedules → worker ; reports table refreshed at 00:10
:::

## What the developer will build
Nine endpoint groups, immutable `StockMovement` rows, a reorder engine with `select_for_update(skip_locked=True)`, batch sales import with idempotency, a materialised daily summary, 45+ tests including a concurrency test, `/health`, `/ready` (with migration-version check) and `/metrics` with `celery_queue_depth`. No Dockerfile, no CI, no K8s.

## Copyable Developer Agent Prompt
::: prompt stockpilot-retail
:::

## Developer handoff
```bash
git clone https://github.com/harshath125/stockpilot.git && cd stockpilot
grep -n "web:\|worker:\|beat:" docs/OPERATIONS.md     # the three entrypoints, in their words
pip install -r requirements/dev.txt && pytest -q --cov=. --cov-fail-under=80
python manage.py migrate --check 2>/dev/null || echo "no --check; we'll diff instead"
grep -RIn "RotatingFileHandler\|DEBUG = True" config/ || echo "clean"
```
The `--check` line matters: Django has no portable “are migrations unapplied?” command in older versions, so the pipeline uses `python manage.py showmigrations | grep -c '\[ \]'` and fails if > 0 *after* the migration job runs. Ask the developer to add a management command if they want a cleaner gate — that's a legitimate handoff request, and one an interviewer would like to hear you made.

# PART B — THE DEVOPS JOURNEY

## Clone, inspect, identify
```bash
git log --oneline -6 ; ls requirements/ ; test -f requirements/base.txt && grep -c "--hash=" requirements/base.txt
find . -name "Dockerfile*" ; ls k8s 2>/dev/null ; echo "expect: nothing (developer only)"
grep -RIn "env(" config/settings/base.py | sed -n '1,25p'      # the env contract, from source
cat Procfile 2>/dev/null ; cat pyproject.toml | sed -n '1,40p'
```
**Dependency map you can draw in a minute:** Postgres (hard), Redis (hard for 2 of 3 roles), no outbound internet (which means no NAT for the app subnets in *this* project — a cost line you can honestly remove and a great design-review sentence).

## Build and test
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements/base.txt --require-hashes
DJANGO_SETTINGS_MODULE=config.settings.dev python manage.py check
pytest -q -p no:randomly                     # deterministic order for CI debugging
ruff check . && mypy src                     # lint/type as a *build* step, not a review comment
python manage.py makemigrations --check --dry-run && echo "uncommitted migrations!" && exit 1
```
That last line is the highest-value 20 seconds in this pipeline: a model change without a migration file is a *silent* production bug (new deploys don't get the column), and `makemigrations --check --dry-run` exits non-zero when it happens.

## Dockerize — one image, three roles
```dockerfile
FROM python:3.12.3-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements/base.txt ./
RUN pip install --require-hashes -r base.txt
COPY . .
RUN python -m compileall -q src && adduser --system --uid 10001 --group app

FROM python:3.12.3-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DJANGO_SETTINGS_MODULE=config.settings.prod
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/* \
    && adduser --system --uid 10001 --group app
COPY --from=build --chown=10001 /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build --chown=10001 /usr/local/bin /usr/local/bin
COPY --from=build --chown=10001 /app /app
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
  CMD curl -fsS http://127.0.0.1:8080/health/ready || exit 1
# no ENTRYPOINT: the task definition supplies the command per role
```
**Why no `ENTRYPOINT`:** three ECS services share this image and differ only in `command`. One image = one scan, one SBOM, one sign, one digest to promote — that's the whole argument, and it's also why your pipeline asserts “all three task definitions reference digest X”. **Why `compileall` in the build stage and `PYTHONUNBUFFERED=1`:** the first removes a `.pyc`-write on a read-only filesystem at request time, the second stops logs arriving 30 seconds late (a buffering artefact that has wasted hours of many people's lives).

## Build image, push, scan
```bash
IMG=$R/stockpilot:sha-$SHORT
docker build -t $IMG . ; trivy image --exit-code 1 --severity HIGH,CRITICAL $IMG
syft $IMG -o spdx-json > sbom.json                     # an artefact of the build, not an afterthought
docker push $IMG && cosign sign --yes $IMG
aws ecr describe-images --repository-name stockpilot --image-ids imageTag=sha-$SHORT --query 'imageDetails[0].imageDigest'
```
That `describe-images` line is the “does the tag actually exist?” check that prevents tomorrow's `ImagePullBackOff`; put it right after push in every pipeline you write.

## Run the trio locally (before the cloud)
```bash
docker compose -f docker-compose.yml up -d --wait postgres redis
docker run --rm --env-file .env.local -e DATABASE_URL=postgres://… $IMG python manage.py migrate
docker run --rm --env-file .env.local -p 8080:8080 $IMG gunicorn config.wsgi:application \
  --bind 0.0.0.0:8080 --workers 3 --timeout 60 --graceful-timeout 25 --access-logfile - --error-logfile -
docker run --rm --env-file .env.local $IMG celery -A config worker -l info -Q default,reports -c 4
docker run --rm --env-file .env.local $IMG celery -A config beat -l info
curl -s localhost:8080/ready | jq '.migrations, .broker'
```
Do this *before* Fargate. Five minutes here saves an hour of “why is my task not starting”, and the same commands are literally the three task definitions' `command` fields.

## Create infrastructure with Terraform
`network` (public×2 for ALB, private×2 for tasks, VPC endpoints for ECR/S3/Logs/SSM/Secrets and **no NAT** since nothing needs the internet) · `data` (RDS Postgres + `redis` ElastiCache with `automatic_failover_enabled` and a parameter group with `notify-keyspace-events` if you use keyspace notifications) · `app` (3 ECS services, one task defn template each, ALB + target group on `/health/ready`, an SQS dead-letter for a future event fan-out, `aws_ecs_capacity_provider` if you prefer EC2 to cut Fargate cost per request).

```hcl
resource "aws_ecs_service" "beat" {
  name            = "stockpilot-beat"
  task_definition = aws_ecs_task_definition.beat.arn
  desired_count   = 1                       # deliberately 1, with a comment saying why
  deployment_maximum_percent         = 100  # no two beats overlapping
  deployment_minimum_healthy_percent = 0    # accept a short gap, prefer no duplicate scheduling
  lifecycle { ignore_changes = [desired_count] }   # autoscaling won't touch it; note this in the runbook
}
```
Beating around `deployment_maximum_percent` like that is exactly the kind of line that shows you've thought about **overlap** — a blue/green of two beats double-fires every schedule for 90 seconds.

## Networking, and one honest simplification
`sg-alb` 443 from `0.0.0.0/0` → `sg-task` 8080 from `sg-alb` only; `sg-db` 5432 from `sg-task`; `sg-redis` 6379 from `sg-task`. Then the simplification: web/tasks in private subnets with **no NAT**, because there is no egress requirement (documented in `docs/networking.md`, since the *next* person will ask). If a dependency later needs the internet, the note tells them where to add a NAT — and what it will cost.

## Database
RDS Postgres, `db.t4g.medium` for a “real” load test and `micro` for the lab, `performance_insights`, `pg_stat_statements` in the parameter group, 7-day retention, copy tags to snapshots, and `rds.force_ssl = 1` (an audit favorite; test the connection string with `sslmode=require` before you enable it in prod — a task that fails to start because of SSL is a 20-minute outage you can have in staging instead). Backups restored **into a scratch environment** once per quarter, with the restore timed in the runbook: an untested backup is a hope, not a backup.

## Deploy (and the migration job, which is its own service)
```bash
aws ecs run-task --cluster stockpilot-prod --task-definition stockpilot-migrate \
  --overrides '{"containerOverrides":[{"name":"app","command":["python","manage.py","migrate","--noinput"]}]}' \
  --enable-execute-command --query 'tasks[0].taskArn' --output text
# wait, check exit code, THEN deploy web/worker/beat
```
The one-off `migrate` task with an exit code checked in CI is the clean answer to “who runs `migrate`” (never the web container: with 6 tasks starting at once you get 6 racing `ALTER TABLE`s and, on some DDL, a lock storm that takes the API with it).

## CI/CD (GitHub Actions + a Jenkins parity note)
```
pr: ruff + mypy + pytest(cov 80) + gitleaks + trivy fs + makemigrations --check
merge: build → trivy image → syft sbom → cosign → push (immutable tag) → migrate job (dev) → deploy dev →
       verify (/ready shows migration version == the artefact's version, one POST /api/sales/bulk smoke) →
       deploy staging → soak 10 min on queue depth + p95 → [prod: approval] → migrate prod → deploy web+worker → beat → verify
```
The order in prod is **migrate → web → worker → beat**, and the reason is worth stating in the PR template: the worker must not consume tasks whose schema the web tier hasn't yet migrated, and beat must not schedule a task that the new worker can't handle. Every “the deploy half-applied” incident I've read about in Django/Celery shops is that ordering, ignored.

## DevSecOps
`pip-audit` + Trivy on `base.txt` (pip's own hash-checking already blocks a swapped wheel — a good talking point), gitleaks over history, a `SECURITY.md` and a dependency-update bot with a **weekly** schedule so CVE PRs don't arrive as an emergency, Checkov on the Terraform (it will catch a `redis` without `at_rest_encryption_enabled` — a real finding for a retail company with supplier pricing), Kyverno image-signature verification on the cluster if you use EKS later, `aws ecr` lifecycle policy so 300 builds don't become a $60/month habit, and an IAM policy where `s3:PutObject` is scoped to one prefix.

## Monitoring
`celery_queue_depth{queue="default"}` > 20 for 5 min (warning) / > 100 for 5 min (page: the engine is behind, so purchase orders are late), `celery_task_latency_seconds` p95, `reorder_run_duration_seconds` vs the 15-minute schedule (if the run takes > 14 min you are one bad day from overlapping), `stockout_events_total` (a **business** metric on the ops dashboard, and the one the retail manager will thank you for), `sales_batch_rejected_total{reason}` (a spike = an upstream POS integration change, not your bug), and Postgres saturation (`pg_stat_activity` count, `pg_locks` waits). Beat staleness — `time() - max(celery_task_last_success_timestamp{task="evaluate_reorder_points"}) > 900` — is the alarm that catches “the schedule silently vanished”, and it is the single most valuable line in this section.

## Failure scenarios
| Break | Symptom | Fix |
| :-- | :-- | :-- |
| Kill the worker | Queue depth climbs, API fine, orders “pending” | autoscale workers on `queue_depth` (custom metric → target tracking), alert before the user notices |
| Two beat replicas (someone scales “for reliability”) | Duplicate POs, `UNIQUE` violations in logs | scale to 1, add a `Polaris`-style check or a Terraform rule that `desired_count == 1` for beat, and make the task idempotent so this row becomes a shrug |
| Redis evicted (maxmemory) | Tasks vanish silently, `Queue is empty` in worker logs | `allkeys-lru` was wrong for a broker → `noeviction` + `notify-keyspace-events`, memory alarm at 80 %, and a queue-depth *drop* alarm that catches “tasks disappeared” |
| Migration ran, old code still serving | `column does not exist` on the web tier only during the rollout | expand-only migrations (again), and the readiness gate on the migration version you now expose in `/ready` |
| `acks_late` + a task that takes 30 min | Task redelivered on every deploy → duplicates | `acks_late` with `visibility_timeout` matched, idempotency keys, and a task duration SLO (`reorder_run_duration_seconds`) |
| Log volume exploded (a `print` loop) | Container disk / CloudWatch bill | `--log-opt max-size`, a `logging.Filter` for noisy paths, and a per-service log-volume alarm |
| Postgres connection exhaustion | `FATAL: too many connections`, tasks restarting | `DB_CONN_MAX_AGE`, pool sizing arithmetic (3 workers × 2 threads × N tasks vs `max_connections-3`), PgBouncer (`--pool-mode transaction`) |

## Troubleshooting — “the reorder engine stopped 40 minutes ago”
`celery inspect active` (empty) → `celery inspect ping` (no reply) → the workers are **up** but wedged. `kubectl`-equivalent: `aws ecs execute-command` → `py-spy record`/`py-spy dump` on the worker PID → 8 greenlets stuck in `psycopg2.connect`. Postgres `max_connections`? No — `pg_stat_activity` shows 96 idle-in-transaction. Cause: `ATOMIC_REQUESTS` was left on in a settings refactor, so web transactions held locks while workers waited, and the beat schedule backed up. Fix: `idle_in_transaction_session_timeout = 60s` (a *guardrail*, not just a fix), the settings check in CI (`grep ATOMIC_REQUESTS` must be absent in prod), and a new alarm on `pg_stat_activity_count{state="idle in transaction"} > 20`.

## Scaling
Web: `cpu 65 %` target tracking, `min 2 max 12`, and **requests-based** scaling via `TargetTrackingScalingPolicyConfiguration{predefined_metric_specification{predefined_metric_type="ResourceManager:RequestCountPerTarget", target_value=900}}` — because Gunicorn's 6 (3 workers × 2 threads) capacity per task is what saturates, not CPU. Workers: scale on queue depth, not CPU — a queue of 40 with idle workers means the tasks are long, and more workers help; a queue of 40 with 100 % CPU also means more workers help; so it's the one metric that's right in both cases. Beat: never. DB: read replica for `/api/reports/*` only.

## Rollback
One command per service, all to the previous task definition revision (which pins a *digest*), run in the order `beat → worker → web` — the reverse of deploy. `aws ecs update-service --force-new-deployment` restarts with the same definition if the image was re-pushed. Migrations: expand-only, so no DB rollback; if you ever *must* contract, it's a separate release with its own soak. Rollback rehearsal in staging, timed, with the invariant check (`purchase orders created during the last 15 minutes == expected`) as the verification.

## Final architecture
::: flow
route53 → ALB (TLS/ACM, /health/ready) → ECS web×N (private, sg from ALB, no NAT, VPC endpoints) → RDS Postgres (multi-AZ, KMS, 7d, force_ssl) + ElastiCache Redis (noeviction, 2 replicas) ← ECS workers×M (scaled on queue depth) ← beat×1 (1 replica by policy) ; migrate = one-off task, gated in CI ; logs→CloudWatch (30d), metrics→Prometheus, alarms→SNS ; Terraform modules + locked state, applied only by the pipeline
:::

## What I learned
Roles, not apps: a deployment unit is a process with a lifecycle, and sharing an image while splitting commands is what makes the trio maintainable. Ordering is architecture (migrate → web → worker → beat), and “silently not running” is the failure class that observability exists for — the staleness alarm is worth more than ten dashboards.

## Interview questions
::: grid2
**“Three services, one image — how do you keep them consistent?”** → the pipeline writes the digest into all three task definitions in one job and a post-deploy check compares them; plus a CI assertion that the number of `containerOverrides` commands equals the number of roles.
**“Why is scaling Celery workers on CPU wrong?”** → queue depth is the demand signal; CPU tells you the workers are busy, not whether work is waiting. With long tasks CPU is 100 % at 1 worker and the queue is the only thing that shows the pain.
**“How do you run `migrate` safely in a rolling deploy?”** → a gated one-off task after the image push and before the web deploy, expand-only DDL, and readiness that reports the applied version so a rollout can't mark a pod ready against an old schema.
**“Beat at 1 replica: what's your availability plan?”** → accept a short gap (a 90-second restart window loses nothing because the engine is *state-driven*, not event-loss-sensitive), alarm on staleness, and — if the business needs better — a Postgres advisory-lock leader election instead of running two.
:::

## Résumé bullets
::: callout note ONLY WHAT YOU ACTUALLY BUILT
- Deployed a Django + Celery platform (web, worker, beat) as three ECS Fargate services from a single scanned, signed image with immutable digests, with a gated one-off migration task and a deploy order (migrate → web → worker → beat) that eliminated duplicate-schema errors during rollouts.
- Built a GitHub Actions pipeline with `pip --require-hashes` deterministic builds, `ruff`/`mypy`, `pytest --cov-fail-under=80`, a `makemigrations --check` gate, Gitleaks, Trivy fs + image, SBOM generation and cosign signing; production behind an environment approval with a 10-minute staging soak on queue depth and p95 latency.
- Cut the NAT line to zero by proving no egress requirement and using VPC endpoints for ECR, S3, CloudWatch Logs and SSM; kept Postgres on `rds.force_ssl` with a rehearsal of the connection-string change in staging.
- Made background work observable: queue-depth and task-latency alarms, a beat-staleness alarm that caught a silently vanished schedule, `pg_stat_activity` idle-in-transaction alarm, and autoscaling workers on queue depth — reducing late-purchase-order reports to zero over the following sprint.
:::

::: revision QUICK REVISION — 5 minutes
**Concepts:** image vs task definition vs service · one image, many roles · `acks_late` + idempotency · `visibility_timeout` · beat = 1 replica by policy (and why overlap is the danger) · `--max-requests` worker recycling and health-check tolerance · expand-only migrations + a version-gated readiness · queue-depth autoscaling · no-NAT designs and their documentation · `--require-hashes` and `--locked-mode` as supply-chain gates · log volume as a first-class metric.
**Commands:** `pytest -q --cov=. --cov-fail-under=80` · `python manage.py makemigrations --check --dry-run` · `docker build -t $R/app:sha-$SHORT .` · `trivy image --exit-code 1 --severity HIGH,CRITICAL $IMG` · `aws ecs run-task --task-definition app-migrate --overrides '{"containerOverrides":[{"name":"app","command":[…]}]}'` · `celery -A config inspect active|ping|reserved` · `aws ecs update-service --service x --task-definition y:12 --force-new-deployment` · `py-spy dump --pid 1`.
**Architecture:** ALB → web tasks → Postgres + Redis ← workers ← beat(1); migration one-off before the fleet; no egress, endpoints instead; alarms on staleness, depth and duration.
**Common mistakes:** `migrate` at container start · two beats “for HA” · CPU-based worker autoscaling · `allkeys-lru` on a broker · unhashed `pip install` in the image build · buffered logs in production · a settings-file `DEBUG` reachable by an env typo.
**Troubleshooting checklist:** `/ready` JSON (migration version + broker) → queue depth trend → `inspect active/ping` → worker log around the last successful task → DB locks and `idle in transaction` → Redis memory and evictions → task duration vs schedule → did a deploy half-apply (compare digests) → was beat even scheduled (staleness metric).
:::

Next: [Project 05 — NetSwitch on Kubernetes →](proj-05-telecom.html)
