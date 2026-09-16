LEAD: LedgerPay is the Terraform project. A .NET ledger service with double-entry invariants, on an AWS three-tier stack where every byte, every rule and every credential is created by code you can read in a pull request — including the parts a bank's auditor will ask about.

# PART A — GET THE APPLICATION

## Overview
Digital-banking ledger: accounts, double-entry transfers with idempotency keys, reversals that never mutate history, asynchronous statement generation with a disk watermark, and a `/metrics` endpoint whose most important gauge must always read zero. Delivered as a .NET 8 solution with EF Core migrations and an SQL Server schema.

## Business scenario
50 000 accounts, 200 transactions/second peak, and an unforgiving correctness bar: money that doesn't balance is a regulatory incident. So the operational surface is unusually strict — auditors want an append-only trail, on-call wants a *zero-imbalance* alarm, and finance wants statements at 06:00 sharp. Those three sentences become an alert, a backup policy and a scheduled job.

## Tech stack (received)
| Layer | Inherited | Your operational consequence |
| :-- | :-- | :-- |
| Runtime | .NET 8, ASP.NET Core | `DOTNET_gcServer=1` for server GC, container CPU limits *must* be set or `Environment.ProcessorCount` lies to the runtime |
| Data | SQL Server 2022 / RDS SQL Server | `Contained Database Authentication` vs `Windows` — a real decision, and the reason the password policy needs an explicit `CHECK_POLICY` review |
| ORM | EF Core 8, code-first migrations | Migrations are a deploy step with ordering: **expand → migrate → contract**, and `dotnet ef migrations script` is the artefact |
| Async | Outbox + a background `StatementWorker` | A second Windows-service-equivalent: its own container, restart policy and its own alarm |
| Security | JWT, `DataProtection` key ring | The key ring **must be persisted** or every pod decrypts nothing → `IDX10611`/`[PII is hidden]` errors after a scale-out |

## Architecture (as delivered)
::: flow
customer apps → API (stateless, 2 pods) → SQL Server (RDS, multi-AZ) ; API → Outbox table → StatementWorker → CSV/PDF on a writable volume → signed download URL ; every state change → append-only AuditTrail
:::

## What the developer will build
Auth with roles, account and transaction APIs with `UPDLOCK, HOLDLOCK` serialisation, a unique `IdempotencyKey`, a `sum(DR)=sum(CR)` invariant enforced by both code and a trigger, reversal-with-compensating-entry, `202`-then-poll statements, an audit trail, Polly retry policy limited to transient SQL errors, health endpoints, Prometheus metrics, xUnit tests including a property-based balance check, and `docs/OPERATIONS.md` with the publish command and the two entry points. No Dockerfile, no CI, no infra, no monitoring config — the prompt forbids it.

## Copyable Developer Agent Prompt
::: prompt ledgerpay-banking
:::

## Developer handoff
```bash
git clone https://github.com/harshath125/ledgerpay.git && cd ledgerpay
dotnet build -c Release
dotnet test
find . -name "*.csproj" ; ls src/LedgerPay.Api/Properties/launchSettings.json 2>/dev/null || echo "no launchSettings — good for containers"
grep -RIn "DataProtection__KeyRingPath\|ConnectionStrings__Ledger" src/ | head
```
Handoff questions worth asking this developer specifically: “Where does the data-protection key ring live, and what happens on a new pod?” · “Is the worker safe to run twice?” · “Which SQL errors do you retry, and which never?” · “What is the one gauge that must be zero, and what breaks if it isn't?”

# PART B — THE DEVOPS JOURNEY

## Clone and inspect
```bash
git log --oneline -8 ; ls docs/
grep -RIn "integrate" -A3 src/LedgerPay.Api/Program.cs   # the real startup pipeline
find . -path ./out -prune -o -name "Migrations" -print    # how many migrations exist?
du -sh .git ; git ls-files | grep -iE "pfx|crt|key|secret|appsettings.Production"
```
The `appsettings.Production.json` check is not paranoia: the .NET default config order loads it, so a developer (or an agent) putting a connection string there has already committed a secret. If `git ls-files` shows it, the fix is history surgery *and* a credential rotation, in that order.

## Identify dependencies, config, failure surface
| Need | Value | Operational note |
| :-- | :-- | :-- |
| Runtime | `mcr.microsoft.com/dotnet/aspnet:8.0` | The SDK image is only for building; `aspnet` already lacks the compiler |
| Port | `Urls=http://0.0.0.0:8080` | Kestrel binds what `ASPNETCORE_URLS` says; the container port must match it *exactly*, and `0.0.0.0` inside the container is correct (it's not “exposed to the world” — the platform decides that) |
| Required env | `ConnectionStrings__Ledger`, `Jwt__SigningKey`, `DataProtection__KeyRingPath` | The `__` convention is the double-underscore config provider — no app change needed for any of it, which is why you can inject everything from Secrets Manager |
| Health | `/health` (liveness), `/health/ready` (SQL + migration + disk) | Disk watermark in readiness: a *good* design; make sure it's > not >= your alarm threshold |
| Writes | `App_Data/statements/` | The **one** thing needing a volume — EFS or an S3-backed CSI driver; local `emptyDir` loses statements on restart, which finance will notice |
| Cold start | ~4 s | Liveness `initialDelaySeconds: 10`, `startupProbe` not needed; you now know the numbers instead of guessing 30 |

## Build and test as CI would
```bash
dotnet restore --locked-mode            # fails if the lockfile (packages.lock.json) disagrees
dotnet test -c Release --collect:"XPlat Code Coverage"
dotnet publish src/LedgerPay.Api -c Release -o out --self-contained false -r linux-x64
dotnet ef migrations script -p src/LedgerPay.Infrastructure -o out/migrate.sql   # the DB artefact
```
`--locked-mode` is the .NET equivalent of `npm ci`: it refuses a `csproj`/lockfile mismatch, and it is the single cheapest supply-chain check you can add to a .NET pipeline. Add it to every project from now on.

## Dockerize — and the two lines that fix 90 % of .NET container pain
```dockerfile
FROM mcr.microsoft.com/dotnet/sdk:8.0.204-bookworm-slim AS build
WORKDIR /src
COPY Directory.Build.props *.sln ./
COPY src src ; COPY tests tests
RUN dotnet restore --locked-mode
RUN dotnet publish src/LedgerPay.Api -c Release -o /out --self-contained false /p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:8.0.204-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 -m -d /app appuser
WORKDIR /app
COPY --from=build /out ./
RUN mkdir -p /app/App_Data/statements && chown -R 10001 /app/App_Data
ENV ASPNETCORE_URLS=http://0.0.0.0:8080 DOTNET_gcServer=1 DOTNET_EnableDiagnostics=0
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=4 CMD curl -fsS http://127.0.0.1:8080/health/ready || exit 1
ENTRYPOINT ["dotnet","LedgerPay.Api.dll"]
```
**`UseAppHost=false`** removes a 140 MB `LedgerPay.Api` binary you never execute — and on some base images its presence makes the app start under an apphost that mishandles signals. **`DOTNET_EnableDiagnostics=0`** stops the runtime trying to create `/tmp/dotnet-diagnostic-*` sockets, which fails (harmlessly, noisily) on a read-only root filesystem and wastes a startup retry when it doesn't. Both lines belong in every .NET image you will ever review. If you mount `/app/App_Data` read-only for the API, the *worker* needs it writable — one of the few legitimate reasons to give one container a read-write mount and the other a read-only one, and a sentence worth putting in the Helm values comment.

## Build image, push to ECR, scan, sign
```bash
aws ecr create-repository --repository-name ledgerpay/api --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE
aws ecr create-repository --repository-name ledgerpay/worker --image-scanning-configuration scanOnPush=true
docker build -t $R/ledgerpay/api:sha-$SHA .          # the worker is the same image, a different entrypoint → do NOT build twice
trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed $R/ledgerpay/api:sha-$SHA
cosign sign --yes $R/ledgerpay/api@sha256:...
```

## Create infrastructure — the Terraform that a bank's reviewer will read
```
envs/prod/
├── main.tf            # provider, backend, one module call per concern
├── variables.tf       # every knob, typed, validated
├── outputs.tf         # what other stacks may read (no secrets)
└── versions.tf        # required_version + provider pins
modules/
├── network/           # vpc, 6 subnets, routes, NAT, endpoints, flow logs
├── security-baseline/ # sg chain, SCP-shaped tag enforcement, IMDSv2 AMI hardening, kms key
├── data-sql/          # db.sql server, elastic pool, TDE via KMS, long-retention backup, audit to S3
├── app-ecs/           # cluster, service, task defn, ALB, autoscaling, IAM roles
└── observability/     # log groups + retention, metrics, alarms, dashboard JSON, SNS+escalation
```
```hcl
resource "aws_db_instance" "ledger" {
  engine                 = "sqlserver-ee"
  license_model          = "LICENSE_INCLUDED"          # or BRING_YOUR_OWN_LICENSE: a real cost decision
  instance_class         = var.db_instance_class
  allocated_storage      = 100
  storage_type           = "gp3"
  kms_key_id             = module.security_baseline.kms_arn
  backup_retention_period = 14
  backup_window          = "17:00-18:00 UTC"           # after statement generation, before the trading day
  copy_tags_to_snapshots  = true
  multi_az               = true
  performance_insights_enabled = true
  iam_database_authentication_enabled = true
  enabled_cloudwatch_logs_exports = ["AGENT","ERRORLOG"]
  port                   = 1433
  publicly_accessible    = false                        # Checkov's favourite line to find
  parameter_group_name   = aws_db_parameter_group.ledger.name
  db_subnet_group_name   = module.network.data_subnet_group
  vpc_security_group_ids = [module.security_baseline.db_sg_id]
  lifecycle { prevent_destroy = true, ignore_changes = [master_password] }
  final_snapshot_identifier = "ledgerpay-final-${terraform_workspace_raw}"   # destroy WITHOUT losing data
}
```
Six decisions in that block worth defending out loud: `LICENSE_INCLUDED` vs `BYOL` (money), `prevent_destroy` + `final_snapshot_identifier` (a destroy must not be silent), `backup_window` chosen around the business clock, `enabled_cloudwatch_logs_exports` so ERRORLOG is searchable (a “why did SQL restart?” answer), `ignore_changes` on the master password (because Secrets Manager owns it — otherwise every apply fights the rotation), and the parameter group for `user options`/leak-detector settings rather than console edits.

## Networking, and the RDS-specific gotchas
- Private data subnets, no default route; VPC endpoints for SSM, Logs, Secrets, S3. Interface endpoint for ECR.
- `1433` open only from `sg-app`; SQL Server's default port plus `1434` UDP **not** open (it leaks instance names).
- **A classic:** “`terraform apply` succeeded but the instance is `modifying` for 20 minutes” — changing `multi_az` or `instance_class` without `apply_immediately = false` inside the maintenance window. Terraform's `timeouts { create = "60m" }` on an RDS resource is not cosmetic: the default 30-minute create timeout kills the *apply*, leaves the resource, and your state is then not what you think it is.
- Another: `SQLServer instance is not available` after a failover — the driver needed `MultiSubnetFailover=True` in the connection string, or `Connect Timeout` raised; a 30-second app outage during a 90-second failover becomes an SLO miss nobody can explain.

## Database
Elastic pool if you have several small DBs (it is *the* SQL Server cost lever); `contained database authentication = 0` and SQL auth only, so the “Windows auth needs a domain” trap is out of scope for this project — and a note in `docs/decision-log.md` explaining what changes when the org brings Active Directory. `audit` policy to S3 with a 7-year lifecycle (regulatory, not preference). `tempdb` on the local NVMe for the statement aggregation queries.

## Deploy (two services, one release)
```bash
aws ecs deploy --service ledgerpay-api --task-definition $(aws ecs register-task-definition \
  --cli-input-json file://taskdef-api.json --query taskDefinition.taskDefinitionArn --output text) \
  --cluster ledgerpay-prod --wait-for-service-stability
```
The **worker** is the same image with `command: ["dotnet","LedgerPay.StatementWorker.dll"]`, `deployments: { replicas: 1 }` and a `dynamoDB`-style lease so two replicas can't both pick up the same job — or, more honestly, an explicit single-replica service with `aws cloudwatch` alarms on `worker_last_success_timestamp` staleness. A silent worker is the worst failure mode in this project: no errors, just missing statements at 06:01.

## CI/CD — Jenkins here, because banking teams still run Jenkins
```
Checkout → restore (cache ~/.nuget) → build → test + coverage gate → SAST (Sonar, quality gate wait)
→ secret scan (gitleaks) → SCA (trivy fs, .NET advisory data) → publish → image build+scan+sign
→ migrations script published as an artefact + a `migrate` job with an approval
→ deploy api (rolling, 2 → N) → deploy worker (single, restart-after) → verify (/health/ready, one test transfer, balance invariant == 0)
→ [prod gate: two approvers, CHANGE_ID required] → deploy prod → 10-min soak on the imbalance gauge → mark release
```
Two project-specific stages: the **migration job** (not part of the API's startup) and a **post-deploy invariant check** (`SELECT CASE WHEN (SELECT SUM(amount) FROM LedgerEntry WHERE direction='DR') = (SELECT SUM(amount) FROM LedgerEntry WHERE direction='CR') THEN 0 ELSE 1 END`). If it's ever 1, the pipeline fails and the release is marked rolled-back. That single query is worth more than 200 unit tests for this domain — and it's the sort of thing an interviewer remembers.

## DevSecOps
Least privilege as an artefact: three IAM roles (ci, api, worker) with *named* actions and one ARN each; an SCP denying `rds:ModifyDBInstance --PubliclyAccessible`; secrets in Secrets Manager with rotation and `lambda`-based SQL rotation (double-length password rule noted in the runbook, because `ALTER LOGIN` with a short password fails and rotation then looks “flaky”); a `gitleaks` pre-commit and CI scan; `checkov`/`tfsec` with a policy that `0.0.0.0/0` anywhere is a hard fail; cosign + Kyverno verify-images; and an **audit** posture: `AuditTrail` is append-only at the DB level (a trigger blocks DELETE/UPDATE — you can't `terraform` your way out of that, so it's in the migrations review checklist).

## Monitoring
`ledger_balance_imbalance != 0` → page, highest severity, runbook “stop writes, freeze, restore to point in time, call the DBA and the product owner”. `statement_worker_last_success_age_seconds > 3600` → ticket at 06:15, page at 08:00. `sql_connection_pool_wait_ms` p99 → the “pool exhausted, everything slow” signal. `http_request_duration_seconds` by route with p99, `ledger_failed_transactions_total{reason="DEADLOCK"}` rate, `deadlock_count` from SQL's own extended-events metric, `disk_bytes_free` under the watermark, plus `cpu` + `Memory_RSS` (server GC makes working set weird — graph `dotnet_memory_heap_size` if the exporter exposes it, otherwise expect “memory looks high but is fine” tickets).

## Failure scenarios
| Break it | What you see | What you learn |
| :-- | :-- | :-- |
| Freeze the account, then debit | `409 ACCOUNT_FROZEN`, correct | Authorisation *rules* are the app's; your job is the audit log that proves the response |
| Idempotency replay with a new key | Two transactions posted, and the *invariant still holds* | Idempotency is a business rule; a correct ledger can hide a duplicate charge. `ledger_duplicate_suspect_total` is the alarm that catches it |
| Kill one API pod mid-request | Client sees a reset for that connection only | K8s preStop + readiness flip + `server.shutdown` draining; the ECS equivalent is deregistration from the target group + `stopTimeout` |
| RDS failover | 60–90 s of `connection refused`, then self-heal | `MultiSubnetFailover=True`, `Connect RetryCount`, a circuit breaker that must *not* open and stay open — and liveness that ignores SQL so pods aren't killed during a failover |
| DataProtection key ring lost (new node, ephemeral FS) | Users logged out en masse; `IDX10611` | Persist the key ring to S3/`PersistKeysToAzureBlobStorage`-equivalent (`PersistKeysToFileSystem` on EFS, or AWS Secrets/`S3`); this is a *500-storm caused by storage*, the most .NET-specific outage there is |
| Disk watermark reached | readiness fails, pods cycle out, ALB has 0 healthy | `Statements__LowDiskWatermarkMB` set too high for a 30 GB volume, or the S3 lifecycle rule missing — and the reason a `df -h` alarm is on the “boring but page-worthy” list |
| Worker crashed 3 days ago | Nothing. No errors, no alarms | You set the staleness alarm. This row is the entire reason observability is part of deployment, not an afterthought |

## Troubleshooting — “the transfer endpoint is slow at 14:20 every day”
Traces show `db.execute` at 2.1 s while CPU is 30 %. SQL: `sys.dm_exec_requests WHERE blocking_session_id <> 0` → a chain of 40 waiters behind a `SELECT` from the reporting dashboard holding an `S` lock; `sp_WhoIsActive` confirms the same `objectid`. Fix: the dashboard query moves to the read replica with `READ_COMMITTED_SNAPSHOT ON` (row-versioning, so readers stop blocking writers — a *SQL Server-specific* answer worth knowing because interviewers in banking .NET shops ask exactly this), plus an app-side `CommandTimeout` and a Checkov-style review rule: “no unbounded `SUM` over `LedgerEntry` from the app tier”. Lesson generalised: latency that isn't CPU, memory or network is usually **a lock**, and the proof is one DMV query.

## Scaling
Horizontal for the API (stateless, `min=2/max=12`, target tracking on p95 latency via an external metric — the ALB's requests-per-target and `http_request_duration_seconds` p95, since CPU alone lies for I/O-bound .NET). Vertical for SQL (`db.r6g.large` → `.xlarge`, a 3-minute blip, planned in the maintenance window). The worker doesn't scale out — it scales **up** in batch size, and its queue depth (`outbox_lag`) is the real autoscaling input. Connection pool: with 12 tasks × 100 max pool = 1 200 > SQL Server's 32 767 worker limit? No — but the `Max Pool Size` default of 100 × 12 already starves the *reporting* connection; a PgBouncer-less fix is to lower `Max Pool Size` to 30 and raise `Min` thoughtfully. Document the arithmetic: that's the kind of sentence that makes a design review short.

## Rollback
Same image digest, previous task definition. For the DB: **the migration is expand-only** (`ADD` nullable columns, new tables, `CREATE VIEW` for the old shape), so a rollback needs nothing. Contract migrations (`DROP COLUMN`, renames) ship in the *next* release after a full cycle without the old code — a written policy, enforced by a `checkov`-style custom rule and a PR checkbox. And the rollback rehearsal: in staging, deploy v1.4, break it deliberately, roll back to v1.3, and time it against the SLO (target: 5 minutes end-to-end including the verification query).

## Final architecture
::: flow
TLS (ACM) → ALB → ECS Fargate api×N (sg from ALB; secrets+params via VPC endpoints; EFS for App_Data) → RDS SQL Server EE multi-AZ (KMS, 14-day backups, audit→S3 7y, PS enabled) ; statement worker (1 replica, EFS read-write, staleness alarm) ; Terraform modules per concern with a locked S3 state and a nightly drift plan ; Jenkins pipeline with 7 gates and a manual prod approval ; Grafana with the imbalance page at the top
:::

## What I learned
Infrastructure as code is not “cloud in a file”, it is *reviewable* decisions with a diff and an owner — and the moment you added `prevent_destroy` and a `final_snapshot_identifier`, you stopped being a person who runs `terraform apply` and became someone who can be trusted with state. Second lesson: a worker that fails silently is a product failure, and “no alarms for three days” is a finding, not a good week.

## Interview questions
::: grid2
**“How do you deploy .NET to containers and what's specific to .NET?”** → SDK build stage + `aspnet` runtime stage, `UseAppHost=false`, `DOTNET_EnableDiagnostics=0` with a read-only FS, `DOTNET_gcServer=1`, Kestrel `Urls` matching the container port, CPU limits so `ProcessorCount` (and therefore the GC/thread pool) is right, and the DataProtection key ring persisted outside the container.
**“Rolling update or blue/green for a ledger service?”** → blue/green or canary with weight shifting, because a *partially migrated* ledger is the worst state; and no auto-scaling during cutover, with the invariant query as the promotion gate.
**“How do you handle a migration that must run while the app is live?”** → expand/contract, batched backfills (a 5 M-row `UPDATE` in one transaction = a lock storm and a log file that fills the disk), idempotent migration scripts, and a rollback script that is tested on a restored snapshot rather than imagined.
**“RDS is at 95 % CPU. Your steps?”** → PS/`EXCEPTIONS` wait stats first (`cxpacket` vs `PAGEIOLATCH` vs `LCK_M_S` tell you parallelism vs I/O vs locks), `top` queries by `total_worker_time`, missing index DMV, then parameter sniffing (`OPTION (REPARAM)`/`query_store` regression), and only then size.
:::

## Résumé bullets
::: callout note USE THESE WORDS, NOT BIGGER ONES
- Provisioned a three-tier AWS environment for a .NET 8 ledger service with Terraform modules (network, security baseline, data, app, observability) and an encrypted, versioned, locked remote state; enforced least-privilege with three scoped IAM roles and an SCP denying public database endpoints.
- Hardened the delivery path: `dotnet restore --locked-mode`, Gitleaks secret scanning, Trivy dependency and image scanning with immutable ECR tags, cosign signing, Checkov IaC policy gates, and a Jenkins pipeline with a gated migration job plus a post-deploy ledger-balance invariant check.
- Ran the service on ECS Fargate behind an ALB with latency-based autoscaling, a separately deployed background worker with an outbox-lag alarm, and EFS-backed statement storage; reduced a daily 14:20 transfer-latency spike to a documented locking issue solved with row-versioning and a read-replica routing change.
- Cut RDS risk with `prevent_destroy`, final snapshots on destroy, 14-day point-in-time recovery, KMS envelope encryption, SQL ERRORLOG shipped to CloudWatch, and a rehearsed failover drill (60 s reconnect verified) plus a rehearsed rollback (under 5 minutes).
:::

::: revision QUICK REVISION — 5 minutes
**Concepts:** `.NET` container specifics (`UseAppHost`, `EnableDiagnostics`, GC server mode, `Urls`) · DataProtection key-ring persistence · EF migrations as a separate gated job with expand→contract ordering · idempotency keys as an *observable* property · ledger invariants as a pipeline gate · RDS SQL Server: license models, elastic pool, backup window vs business clock, ERRORLOG export, `MultiSubnetFailover` · `prevent_destroy` + `final_snapshot_identifier` · Checkov `0.0.0.0/0` as a hard fail · lock waits vs CPU for “slow SQL”.
**Commands:** `dotnet publish -c Release -o out --self-contained false /p:UseAppHost=false` · `dotnet restore --locked-mode` · `dotnet ef migrations script -o out/migrate.sql` · `trivy image --exit-code 1 --severity HIGH,CRITICAL IMG` · `terraform -chdir=envs/prod plan -out=tfplan && terraform apply tfplan` · `aws ecs deploy --service x --task-definition y --wait-for-service-stability` · `aws secretsmanager rotate-secret --secret-id prod/ledgerpay/db` · `sqlcmd -Q "SELECT DB_NAME(), SUM(...)"` · `kubectl`-free equivalent: `aws ecs describe-tasks --tasks $T --include-events`.
**Architecture:** ALB → API tasks (KMS'd secrets via endpoints) → SQL Server multi-AZ with audit-to-S3; one worker with a staleness alarm; all created by versioned modules with a locked state; every release gated by an invariant.
**Common mistakes:** committing `appsettings.Production.json` · changing `multi_az`/class with `apply_immediately=true` outside a window · trusting a 30-minute Terraform create timeout for RDS · key ring on ephemeral storage · a destructive migration in a rollback-capable release · no alarm for a silent worker · “it's encrypted, therefore it's secure” with the KMS key policy wide open.
**Troubleshooting checklist:** readiness components → `describe-tasks` stop reasons → exit code (137 vs 134 vs a `StackExchange.Redis` exception) → `/health/ready` + `/metrics` imbalance gauge → SQL wait stats and blocking DMV → disk + watermark → outbox lag → who changed the SG/route within the incident window (`cloudtrail lookup-events`) → is it *this* release (compare digests).
:::

::: learn WHAT I LEARNED (your version)
Write the two sentences only you can write: the failure that took you longest, and the design decision you'd now defend in a review. Put them in `docs/handoff.md` of your own repo.
:::

Next: [Project 04 — StockPilot (ECR + Fargate + CI/CD) →](proj-04-retail.html)
