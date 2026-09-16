LEAD: Your first complete journey. A clinic's appointment service, delivered by a developer (your coding agent), arrives as a Git repository — and you take a `target/*.jar` on a Linux VM to a health-checked, monitored, three-tier AWS deployment, twice: once by hand so you understand every step, once by pipeline so you never do it by hand again.

::: callout note THE SHAPE OF EVERY PROJECT PAGE (read this once)
**PART A** is the developer's job: you hand a prompt to an AI coding agent and it produces the application. **PART B** is yours: from `git clone` to rollback. You never write application code here, and you never let the agent do DevOps. That separation is the whole training.
:::

# PART A — GET THE APPLICATION

## Overview

MediBook is a Spring Boot 3 REST API for a small clinic: patients, doctors, 30-minute appointment slots, and a daily report. Postgres underneath, JWT auth, Flyway migrations, Actuator health. It is deliberately ordinary — the same shape as a thousand real job ticket-queues — so the only difficulty you meet is *operational*.

## Business scenario

A 12-doctor clinic books about 260 appointments a day. Nobody's phone line should die at 09:00. Double-booking is a compliance and human problem, not a UX inconvenience. Records must not be lost, and a receptionist must be able to answer “did Dr Rao finish her day list?” in one query. Those three sentences are why we want health checks, unique constraints, backups and a report endpoint — not because a tutorial said so.

## Tech stack (received, not chosen by you)

| Layer | What you inherit | What a DevOps engineer must notice |
| :-- | :-- | :-- |
| Language / runtime | Java 17, Spring Boot 3.2 | The JVM's memory model: you'll set `MaxRAMPercentage`, not `-Xmx` guesses |
| Build | Maven wrapper `./mvnw` | It's committed, so CI needs no global Maven install; `target/` is not in Git |
| Data | PostgreSQL 16, Flyway SQL migrations | Migrations are a **deploy step**, not an app side effect; the unique index encodes a business rule |
| API | JSON REST, springdoc OpenAPI | `context-path` matters for health checks behind a proxy |
| Config | env vars + `application.yml` | Good: no `config/prod.properties` in Git to leak |
| Ops surface | Actuator health/liveness/readiness/metrics | The only monitoring surface you get for free; the rest is yours to wire |

## Architecture (before you touch anything)

::: flow
Browser / clinic app → HTTPS → Nginx reverse proxy (TLS, headers, access log) → Spring Boot :8080 → JDBC pool (HikariCP) → PostgreSQL :5432 → WAL + nightly snapshot to S3
:::

Everything on the right of Nginx is inside one Linux box for step 1, then split across a load balancer, an Auto Scaling group and RDS for step 2. Keep that two-frame picture in your head: it's the same system, one is “a machine you own”, the other is “a managed platform you describe in code”.

## What the developer will build

Ten endpoints, three entities with a unique `(doctor_id, scheduled_at)` constraint, JWT + role checks, RFC-7807 error bodies with a `code`, JSON logs to stdout with a correlation id, ~30 tests including a Testcontainers integration test, and `docs/OPERATIONS.md` for you. Nothing else. **No Dockerfile, no Jenkinsfile, no cloud code** — the prompt forbids it, so the operational work stays yours.

## Copyable Developer Agent Prompt

::: prompt medbook-healthcare
:::

Paste that into Claude Code, GitHub Copilot, Cursor or Codex in an empty folder. Review the result as a DevOps engineer would — `README.md`, the env-var table, `docs/OPERATIONS.md`, the migrations — and only then hand it to Git.

## Developer handoff

```bash
git init medibook && cd medibook
# copy the agent's files in; read README.md and docs/OPERATIONS.md first
git add -A && git commit -m "feat: MediBook v1.0.0 (backend, migrations, tests, ops notes)"
git branch -M main
git remote add origin git@github.com:harshath125/medibook.git
git push -u origin main && git tag -a v1.0.0 -m "first handoff" && git push origin v1.0.0
```

**The handoff checklist — five questions, asked of the developer (or of the agent, in a follow-up):**

1. What's the exact run command, and which port does it listen on?
2. Which env vars are required for production, and which fail startup if missing?
3. What must run before the app serves traffic — migrations? seeding?
4. Which endpoint is safe to use as a readiness probe, and which one ignores external dependencies for liveness?
5. Does anything write to disk, and if so, where? (Log files → we'll kill that. Uploads → we'll talk about object storage.)

Write the answers into your repo's `docs/handoff.md`. Ten projects later, that file habit is why you can onboard onto an unknown service in an afternoon.

# PART B — THE DEVOPS JOURNEY

## Clone and inspect

```bash
git clone git@github.com:harshath125/medibook.git && cd medibook
git log --oneline -5
ls -a                                  # pom.xml, mvnw, src/, docs/, .gitignore, .env.example?
find . -name "*.java" | wc -l
cat docs/OPERATIONS.md
grep -RIn "8080\|context-path\|actuator" src/main/resources/ pom.xml | head -20
git ls-files | grep -iE "env|secret|pem|key"          # red flag check, before anything else
du -sh .git && git count-objects -vH                    # did the agent commit a fat repo?
```

`grep -RIn` on the resources folder is how you find the port and context path without reading 25 files. The `git ls-files` line is the check a real engineer does on day one — an application repo with `.env` in history gets cleaned before it's ever public.

## Identify dependencies, config and failure surface

| What you need | Where to find it in the repo |
| :-- | :-- |
| Runtime | `pom.xml` → `<java.version>17` and the Spring Boot parent version |
| Build command | `README.md` → `./mvnw -B -DskipTests package`; verify it |
| Port & context root | `application.yml` → `server.port`, `server.servlet.context-path` |
| External services | `spring.datasource.url` (Postgres), nothing else — a *dependency map* you can draw in 30 seconds |
| Required env vars | `README.md`'s env table + `grep -RIn \${.*:} src/main/resources/` |
| Health endpoints | `/actuator/health`, `/readiness`, `/liveness` — confirm the probe config is enabled |
| Data on disk | none (logs go to stdout) → the app is stateless, so a second instance is legitimate |
| Startup time | cold JVM with 12 migrations ≈ 25–45 s → sets your probe timings later |

## Build and test (as CI would)

```bash
./mvnw -B -DskipTests package            # fast loop: does it compile?
./mvnw -B verify                          # the real gate: unit + Testcontainers integration
java -jar target/medibook-0.0.1-SNAPSHOT.jar   # smoke locally with docker run for Postgres
docker run -d --name pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=medibook -p 5432:5432 postgres:16.2
curl -s localhost:8080/actuator/health | jq
```

**What just happened:** `package` proves the compiler and tests-as-build-dependency are satisfied; `verify` is what the pipeline will run, and Testcontainers needs a Docker socket on the build agent — that single fact will decide your Jenkins agent design later. `curl /actuator/health` returning `{"status":"UP"}` with a `db` component is your first *evidence*, and the same URL becomes the health check at every layer above.

## Dockerize — packaging someone else's application

```dockerfile
FROM eclipse-temurin:17.0.10_7-jdk-jammy AS builder
WORKDIR /workspace
COPY pom.xml mvnw ./ ; COPY .mvn .mvn
RUN ./mvnw -B dependency:go-offline          # cached until pom.xml changes
COPY src src
RUN ./mvnw -B -DskipTests package

FROM eclipse-temurin:17.0.10_7-jre-jammy
RUN useradd -r -u 10001 -s /usr/sbin/nologin appuser
WORKDIR /app
COPY --from=builder --chown=10001 /workspace/target/medibook-*.jar app.jar
ENV JAVA_OPTS="-XX:MaxRAMPercentage=70 -XX:+ExitOnOutOfMemoryError"
EXPOSE 8080
USER 10001
HEALTHCHECK --interval=15s --timeout=3s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/actuator/health/readiness || exit 1
ENTRYPOINT ["sh","-c","exec java $JAVA_OPTS -jar /app/app.jar"]
```

Then `.dockerignore` with `.git`, `target/`, `.env`, `docs/` and `*.log`, and:

```bash
docker build -t medibook:local .
docker history medibook:local | head -8        # see the layers; the jre base is most of it
docker run --rm -e DB_URL=jdbc:postgresql://host.docker.internal:5432/medibook \
  -e DB_USER=postgres -e DB_PASSWORD=pw -e JWT_SECRET=0123456789abcdef0123456789abcdef \
  -p 8080:8080 medibook:local
```

**Common failure here:** `java: command not found` or a `127.0.0.1` health check passing while the pod fails — because `curl` isn't in the JRE base image (add it, or use `wget --spider`, or move the health check to the platform), and inside a container `127.0.0.1` means *this container*, not the host.

## Build image, push to a registry, run it on the VM

```bash
IMG=123456789012.dkr.ecr.eu-west-1.amazonaws.com/medibook:v1.0.0
aws ecr describe-repositories --repository-names medibook || \
  aws ecr create-repository --repository-name medibook \
    --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE
aws ecr get-login-password | docker login --username AWS --password-stdin ${IMG%%/*}
docker tag medibook:local $IMG && docker push $IMG
ssh -i ~/.ssh/lab.pem ec2-user@IP 'sudo docker pull '"$IMG"
```

Registry first, `docker run` second — never `docker save | ssh docker load` as a habit. The registry is where scanning, signing and immutable tags exist; skipping it is how you get a build you can't audit.

## Deploy on the Linux VM (the “one box” version — do this before AWS)

```bash
sudo tee /etc/systemd/system/medibook.service >/dev/null <<'UNIT'
[Unit]
Description=MediBook API
After=network-online.target docker.service
Wants=network-online.target
[Service]
Restart=always
RestartSec=5
ExecStartPre=-/usr/bin/docker rm -f medibook
ExecStart=/usr/bin/docker run --rm --name medibook \
  --env-file /etc/medibook/prod.env -p 127.0.0.1:8080:8080 \
  --memory 768m --cpus 1.5 --log-opt max-size=10m --log-opt max-file=3 \
  --read-only --tmpfs /tmp \
  REGISTRY/medibook:v1.0.0
ExecStop=/usr/bin/docker stop -t 25 medibook
TimeoutStopSec=40
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now medibook
sudo journalctl -u medibook -f
```

Then Nginx in front — TLS, the health path, and timeouts that don't fight the JVM:

```nginx
upstream medibook { server 127.0.0.1:8080 keepalive 32; }
server {
  listen 443 ssl http2;
  server_name app.clinic.example.com;
  ssl_certificate     /etc/letsencrypt/live/app.clinic.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/app.clinic.example.com/privkey.pem;
  add_header Strict-Transport-Security "max-age=31536000" always;
  access_log /var/log/nginx/medibook.access.log combined;
  location /actuator/health { proxy_pass http://medibook; access_log off; }
  location / {
    proxy_pass http://medibook;
    proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_connect_timeout 3s; proxy_read_timeout 60s; proxy_next_upstream off;
  }
}
```

**Why each line earns its place:** `127.0.0.1:8080` publishing means the app is only reachable through Nginx (a `0.0.0.0:8080` binding with a permissive security group is the #1 audit finding on student projects) · `proxy_read_timeout 60s` must exceed the app's slowest legitimate request or you'll “see” 502s that aren't the app's fault · `proxy_next_upstream off` on a single box prevents double-POSTing a booking · `--read-only --tmpfs /tmp` proves the app truly doesn't write to disk · `ExecStop=docker stop -t 25` + `server.shutdown=graceful` means in-flight bookings finish instead of erroring on restart.

## Create the AWS infrastructure (Terraform, one module each)

```hcl
module "network" {
  source = "../modules/network"
  vpc_cidr = "10.0.0.0/16"
  azs      = ["eu-west-1a", "eu-west-1b"]
  public_subnet_cidrs  = ["10.0.0.0/24", "10.0.1.0/24"]
  private_subnet_cidrs = ["10.0.10.0/24", "10.0.11.0/24"]
  data_subnet_cidrs    = ["10.0.20.0/24", "10.0.21.0/24"]
  enable_vpc_endpoints = ["s3", "ecr", "ecr-messages", "ecr-docker", "logs", "ssm", "ssmmessages"]
}

module "app" {
  source = "../modules/app-service"
  name                 = "medibook"
  vpc_id               = module.network.vpc_id
  subnet_ids           = module.network.private_app_subnet_ids
  image                = "${local.registry}/medibook:v1.0.0"
  instance_type        = "t3.small"
  min_size, desired…   = 2, 2, 4
  env_secret_refs        = ["prod/medibook/db-url", "prod/medibook/jwt-secret"]
  health_check_path    = "/actuator/health/readiness"
  graceful_stop_seconds = 30
}

module "data" {
  source = "../modules/postgres"
  engine_version = "16.3"  instance_class = "db.t4g.micro"
  multi_az = true  storage_encrypted = true  backup_retention_days = 7
  deletion_protection = true  db_subnet_group = module.network.data_subnet_group_name
}
```

```bash
terraform -chdir=envs/prod init
terraform -chdir=envs/prod plan  -out=tfplan
terraform -chdir=envs/prod show tfplan | grep -E "will be|forces replacement" # read the plan
terraform -chdir=envs/prod apply tfplan
```

## Networking, and the security-group chain

::: flow
internet → Route 53 → ALB (`sg-lb`: 443 from 0.0.0.0/0) → target group → EC2 (`sg-app`: 8080 from `sg-lb` only, 22 from `sg-bastion` only) → RDS (`sg-db`: 5432 from `sg-app` only)
:::

That chain is the single most reviewed part of any AWS project: source is a **security group id**, never a CIDR, so no instance ever “temporarily” opens 5432 to the world. `sg-app` still needs egress 443 (ECR, SSM, CloudWatch) — which is why “0.0.0.0/0 outbound, 8080 inbound from ALB only” is the correct-looking answer in a design review. No NAT for the *data* tier; VPC endpoints for ECR/S3/Logs so pulls don't pay NAT twice.

## Database

RDS Postgres, `multi_az = true`, subnet group of the two private **data** subnets, `publicly_accessible = false`, KMS CMK, `performance_insights_enabled = true`, retention 7 days, and a parameter group with `log_min_duration_statement = 500` so slow queries are discoverable. Credentials in Secrets Manager with rotation disabled for a lab (enabled in prod), never in `user_data`. Migrations run **from the pipeline as their own step**, not from `spring.flyway.auto-migrate` on boot — otherwise N instances race to migrate and you learn about it during a scale-out.

## Deploy, and prove it

```bash
kubectl-free version: aws elbv2 describe-target-health --target-group-arn $TG --query \
 'TargetHealthDescriptions[].TargetHealth.State'
curl -sS https://app.clinic.example.com/actuator/health | jq '.status, .components.db.status'
curl -sS -X POST https://app.clinic.example.com/api/auth/login -H 'content-type: application/json' \
  -d '{"email":"recep@clinic.test","password":"ChangeMe123!"}' -o /dev/null -w '%{http_code}\n'
ab -n 300 -c 20 https://app.clinic.example.com/api/doctors   # a 30-second load sanity check
```

A deploy “is” these three lines: healthy targets, healthy app-with-DB, and one authenticated request that returns 200. Everything else is detail.

## CI/CD — Jenkins first (because you must be able to read one)

```
Checkout → Build+Test (./mvnw -B verify, junit report) → SonarQube gate → Trivy fs (HIGH,CRITICAL)
→ gitleaks → Docker build (layer cache) → Trivy image → push (immutable tag + sha tag)
→ deploy staging (systemd restart or ECS task update) → verify (health + smoke) → [approval] → deploy prod
```

```groovy
stage('Deploy + verify') {
  steps {
    sh """
      aws ssm send-command --document-name AWS-RunShellScript --instance-ids $INSTANCE_ID \
        --parameters 'commands=["aws ecr pull-per-images >/dev/null 2>&1 || docker pull ${IMAGE} && systemctl restart medibook"]' \
        --query Command.CommandId --output text
    """
    timeout(5) { sh "./scripts/wait-for-health.sh https://staging.clinic.example.com/actuator/health" }
    sh "./scripts/smoke.sh https://staging.clinic.example.com"
  }
  post { failure { sh "./scripts/rollback.sh staging" } }
}
```

The pattern: **deploy, then verify, then auto-rollback on failure.** `wait-for-health.sh` is a `curl --retry-connrefused --retry 30 --retry-delay 2` loop, and it is the reason your pipeline tells the truth. Then rebuild the same flow in GitHub Actions with OIDC for the credentials and nothing else changes — which is the point of learning the stages rather than the tool.

## DevSecOps gates (the same six, every project)

| Gate | Tool | Blocks on | Placed here because |
| :-- | :-- | :-- | :-- |
| SAST | SonarQube quality gate | new code smells, block/critical bugs | before any artefact exists |
| SCA | Trivy fs / OWASP dep-check | HIGH, CRITICAL CVEs | a `pom.xml` change is findable in 4 s |
| Secrets | gitleaks + GitHub push protection | any verified secret | history, not just the tree |
| Container | Trivy image (scan-on-push in ECR too) | CRITICAL, HIGH unfixed | the bytes you'll actually run |
| IaC | Checkov / tfsec | P0/P1 (public DB, unencrypted volume, open 22) | before `apply` creates the hole |
| Runtime | Falco or CloudWatch + alarms (Phase 2) | exec into pod, new privileged container | nothing static can see this |

Plus two non-tool rules that make gates real: **no gate without an owner and a documented override with an expiry**, and **the gate lives in the pipeline, not in a person's discipline** — a `merge check` on the PR for scans, an environment approval for prod.

## Monitoring

CloudWatch agent for memory and disk (`mem_util`, `df -u -x`), `log_opt` on Docker so `/var/lib/docker` can't fill the 30 GB EBS, structured app logs to CloudWatch Logs with 30-day retention, and these alarms (the list is from the reference project's own troubleshooting guide, generalised):

- `CPUUtilization > 85` for 3×5 min · `StatusCheckFailed` (system or instance) 2×1 min · `MemoryUtilization > 90` · `DiskSpaceUtilization > 85`
- ALB: `HTTPCode_Target_5XX_Count` > 1 % of `RequestCount`, `TargetResponseTime p99 > 2 s`, `SurgeQueueLength > 0`
- RDS: `FreeableMemory < 256 MB`, `DatabaseConnections` > 80 % of the pool ceiling, `CPUUtilization > 90`, `BurstBalance < 20` (a burstable volume's last warning before a stall)

One dashboard, three rows: RED for the service, USE for the box, saturation for the DB. And a `logs` insight query saved: `filter @message like /ERROR|Exception/ | stats count() by bin(5m)`.

## Failure scenarios — cause them on purpose

| You break it by | You should see | You should do |
| :-- | :-- | :-- |
| `systemctl stop medibook` | ALB target `draining → unused`, 503 from Nginx | `journalctl -u medibook`, `docker ps`, start, watch the target recover |
| Wrong `DB_PASSWORD` in `/etc/medibook/prod.env` | app exits: `FATAL: password authentication failed`, restarts each time | `docker logs`, compare Secrets Manager value, redeploy — never edit the server by hand |
| Security group 5432 source changed to `sg-lb` | `Connection refused`/timeout, app `DB_CONNECTION_UNAVAILABLE` | `telnet <rds-endpoint> 5432` from the instance, then `aws ec2 authorize-security-group-ingress --group-id sg-db --port 5432 --source-group sg-app` |
| `/tmp` full (a heap dump) | `No space left on device`, health flaps | `df -h`, `du -x -h / | sort -h | tail`, remove, add the alarm *before* it happens twice |
| Image tag deleted from ECR | `ImagePullBackOff`/`pull access denied` | `aws ecr describe-images --repository-name medibook --image-ids imageTag=v1.0.0`, push again or retag |
| Nginx `proxy_read_timeout 5s` | 504 on the report endpoint only | compare ALB `TargetResponseTime` with nginx's `$request_time` — that pair localises the delay in one step |
| Scale to 0 by mistake / ASG min 0 | nothing answers, but no alarm fires | an alarm on `GroupInServiceInstances < 1` — the “missing capacity” alarm people forget |

## Troubleshooting — the 3-tier version of “it's down”

```bash
# 1 · is DNS/TLS the problem?
dig +short app.clinic.example.com ; curl -vI https://app.clinic.example.com/actuator/health 2>&1 | tail -6
# 2 · is the load balancer seeing anything?
aws elbv2 describe-target-health --target-group-arn $TG
aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB \
  --metric-name HTTPCode_Target_5XX_Count --dimensions Name=LoadBalancer,Value=$LB_ARN \
  --start-time $(date -u -d '-30 min' +%FT%TZ) --end-time $(date -u +%FT%TZ) --period 60 --statistics Sum
# 3 · is the box alive, and how?
aws ssm start-session --target i-xxx   # or ssh
sudo systemctl status medibook --no-pager ; sudo journalctl -u medibook --since '-15 min' | tail -40
df -h ; free -m ; top -bn1 | head -15 ; sudo docker inspect medibook --format '{{.State.ExitCode}} {{.State.OOMKilled}}'
# 4 · can the app reach its database?
timeout 5 bash -c '</dev/tcp/medibook-db.cluster-ro-cxy.eu-west-1.rds.amazonaws.com/5432' && echo OPEN || echo BLOCKED
# 5 · can I reproduce a request locally, with the same env?
sudo docker exec -it medibook curl -s localhost:8080/actuator/health/readiness
```

The order is the lesson: **edge → LB → host → process → dependency → request.** Skip a step and you'll “fix” the app when the security group was wrong.

## Scaling

One box doesn't scale; the ASG does. `t3.small` → `t3.medium` is the *first* lever (vertical, cheap, a reboot), then desired 2 → 4 (horizontal, needs the app to be stateless — which you proved when nothing wrote to disk), then scheduled scaling for Monday 08:00 (a 200 % morning spike is predictable, so don't wait for CPU to react), then RDS read replica for `/api/reports/day` only (a report query on the primary is how a busy 09:00 becomes a full outage). Every step justified by a metric, never by a vibe: `CPUUtilization` plus `TargetResponseTime` plus `DatabaseConnections`.

## Rollback

VM/systemd: retag and restart — `/etc/medibook/prod.env` points at `IMAGE_TAG`, so `deploy.sh` takes the tag as an argument and rollback is `deploy.sh v0.9.7`. Two rules you should already feel: **a DB migration must be backward-compatible** (add column, deploy code, then drop; the pipeline rejects `DROP`/`RENAME` in a migration PR without a second release) and **rollback is rehearsed in staging every release**, because a rollback nobody has run is a rumour.

## Final architecture

::: flow
users → Route 53 (alias, 30s TTL) → ACM cert → ALB in public subnets (2 AZs) → target group (readiness probe /actuator/health/readiness) → ASG: t3.small×2 in private-app subnets (sg-app from sg-lb, IMDSv2, docker + cloudwatch agent, --env-file from SSM) → Secrets Manager + Parameter Store (via VPC endpoint) → RDS Postgres 16 Multi-AZ in private-data (sg-db from sg-app), snapshots to S3 with KMS → VPC endpoints: ECR, S3, Logs, SSM → CloudWatch logs+metrics+alarms → SNS → Slack/email → all created by Terraform with a locked remote state, applied only by CI after a reviewed plan
:::

## What I learned (write yours before reading this)

Three sentences I'd expect: the health endpoint is the contract between the app and everything above it; the security-group chain is what makes “no ports open to the world” a fact instead of a hope; and a deploy without verification is a guess. Add your own incident — usually the one where the ALB marked a healthy app unhealthy because of the `/api` context path.

## Interview questions (this project's set)

::: grid2
**“How do you deploy a Spring Boot app without Kubernetes?”** → a container or a JAR, but the important parts are the same: a non-root runtime user, config from env/secret store, graceful shutdown matched by the proxy and LB timeouts, a readiness endpoint that includes the DB, an auto-restart supervisor, logs to stdout, and a rollback path that keeps migrations backward-compatible.
**“A user says the site is down. Your first five commands?”** → `curl -v https://…/actuator/health`, `dig +short`, `aws elbv2 describe-target-health`, `systemctl status`, `journalctl --since -15m | tail -40`. Then explain how each one halves the search space.
**“What's the difference between Nginx here and the ALB?”** → both proxy; the ALB is managed, does TLS from ACM, health-checks and re-registers ASG instances, and you never patch it. Nginx is cheaper and gives you file-level control of headers and caching, but it's a box you own and a thing that can fill its own disk.
**“Why is `0.0.0.0/0` inbound on 8080 a finding?”** → it bypasses TLS, WAF and access logs, and exposes actuator endpoints to the internet; the only inbound path should be the ALB SG on 8080 or the LB on 443.
:::

## Résumé bullets (components you actually built)

::: callout note USE THESE WORDS, NOT BIGGER ONES
- Deployed a Spring Boot appointment API on AWS: Terraform-managed VPC with public/private subnets across two AZs, ALB with readiness-based health checks, and an Auto Scaling group of hardened t3.small instances (IMDSv2, non-root container, read-only filesystem).
- Replaced manual deployment with a Jenkins pipeline: Maven build, JUnit + Testcontainers verification, SonarQube quality gate, Trivy dependency and image scanning, gitleaks secret scan, immutable ECR tags with scan-on-push, automated staging deploy and smoke verification, production behind a manual approval with auto-rollback.
- Added CloudWatch logs, memory/disk metrics and nine alarms covering latency, 5xx rate, saturation and status checks, reducing time-to-detection for a failed deploy from “user report” to under 2 minutes. *(Only include the “2 minutes” if you measured it in a drill.)*
- Designed least-privilege access: chained security groups, IAM roles instead of access keys, secrets in Secrets Manager injected at deploy time, and VPC endpoints that removed all NAT-path traffic for ECR, S3 and CloudWatch.
:::

::: revision QUICK REVISION — 5 minutes
**Concepts:** JAR vs image · `MaxRAMPercentage` vs `-Xmx` · graceful shutdown and `TimeoutStopSec` · Flyway as a deploy step · Nginx vs ALB timeouts · SG chaining · readiness vs liveness · RDS Multi-AZ and backup retention · `log_opt` and the 30 GB EBS · instance profile vs access key · VPC endpoints vs NAT · `describe-target-health` · alarms on symptoms · backward-compatible migrations.
**Commands to remember:** `./mvnw -B verify` · `docker build -t reg/app:v1.0.0 .` · `aws ecr get-login-password | docker login --username AWS --password-stdin REG` · `terraform plan -out=tfplan && terraform apply tfplan` · `aws elbv2 describe-target-health --target-group-arn $TG` · `timeout 5 bash -c '</dev/tcp/host/5432'` · `journalctl -u medibook --since -15m` · `systemctl restart medibook && curl -s .../actuator/health`.
**Architecture:** DNS → ALB → ASG (private) → RDS (private) + secret/param/config sidecars, all from reviewed Terraform plans, all shipping to one dashboard.
**Common mistakes:** `0.0.0.0:8080` published · health check on `/` instead of the readiness endpoint · migrations auto-run at boot on N instances · secrets in user data or Git · no drain on `systemctl stop` · “deployed” decided by `curl -I /` on localhost · ALB idle timeout shorter than the app's longest request · log rotation forgotten.
**Troubleshooting checklist:** edge → LB → host → process → dependency → request. Then the alarm that should have fired, and the runbook line you're adding so it fires next time.
:::

::: learn WHAT I LEARNED (your version)
Write, in this file in your own repo: the one command that surprised you, the one failure that took more than 30 minutes, and the one thing you'd automate first if the clinic paid you. That paragraph becomes your interview story and your résumé bullet. Nobody can write it for you.
:::

Next: [Project 02 — CartFlow (Docker → AWS + CI/CD) →](proj-02-ecommerce.html)
