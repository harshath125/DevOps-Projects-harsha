LEAD: TallyWorks is the microservices project, and it is deliberately a *migration*: a gateway plus two small services carved out of what used to be one Spring Boot app. Your job is not to celebrate microservices; it's to make three deployable units behave like one system — separate pipelines, one standard, one release train, and no cross-service coupling that turns a small change into an outage.

## Before you start: monolith → microservices, in one honest page
::: cols
::: col
**What the monolith gave us for free**
one process, one transaction boundary, one deploy, one log stream, function calls instead of network calls, “it just works” consistency.
:::
::: col
**What it cost**
every release is a 600-test release; one hot endpoint starves the rest; a `@Transactional` on the reporting path holds locks; scaling is all-or-nothing; and the on-call rotation now includes a codebase nobody fully understands.
:::

We split *one* bounded context (reporting, the read-heavy child) out of the transaction core, and put a gateway in front so clients don't learn two hostnames. That is the correct size for a first microservices project. The two rules we will enforce in review, because they're what actually saves teams: **no cross-service foreign keys** (reporting owns a projection, and gets events, never `JOIN`s the ledger) and **no shared database** (one Postgres cluster, separate schemas, separate roles, separate migration folders).

# PART A — GET THE APPLICATION

## Overview
Three Maven modules — `gateway` (Spring Cloud Gateway), `transaction-service` (the ledger core with an outbox), `reporting-service` (event-driven projection) — plus `common` and `contract-tests`. Java 17, Spring Boot 3.2, Flyway, Resilience4j, Actuator + Prometheus, Testcontainers, 45+ tests.

## Business scenario
A lender's settlement engine. Money must balance (`sum(debit)=sum(credit)` per transaction), settlement must never be lost across a restart (the outbox), and reports must be rebuildable without a maintenance window (shadow table + view rename). Three sentences, three operational designs — and all three are testable in a pipeline.

## Tech stack (received)
| Service | Port | Its operational personality |
| :-- | :-- | :-- |
| `gateway` | 8080 | Stateless, cheap, scales with traffic, **no business logic** (the review rule that keeps it replaceable) |
| `transaction-service` | 8081 | Write-hot, optimistic locking, an outbox drain on a 5-second schedule with an advisory lock, and one invariant metric |
| `reporting-service` | 8082 | Read-hot, event-driven, tolerates being behind (it has a *staleness gauge*), and can be killed without hurting customers |
| `common` | — | Correlation filter + masking + error envelope; a shared library is a **coupling decision** — a version bump there means three services rebuild |

## Architecture
::: flow
clients → gateway → transaction-service → (ledger, outbox) ; transaction-service →(events)→ reporting-service → projections ; both → PostgreSQL (separate schemas/roles) ; gateway → Redis for limits
:::
Draw the failure arrows too, in your `docs/handoff.md`: if reporting is down, transactions continue (good); if the outbox stops draining, reports go stale silently (that's the alarm); if Redis dies, the gateway's limits fall open (documented decision, not a surprise).

## What the developer will build
OpenAPI per service with a `contract-tests` module the pipeline can run standalone; idempotent `POST /api/transactions` on `Idempotency-Key`; a state machine with `409` on illegal transitions; `/internal/metrics/invariants` returning `balanceImbalance`, `outboxLagSeconds`, `unsettledAgeSeconds`; a chunked `rebuild` that commits every 5 000 rows; three ADRs; and `docs/OPERATIONS.md` that tells you whether the gateway needs the others up before it starts. No Dockerfile, no K8s, no CI.

## Copyable Developer Agent Prompt
::: prompt tallyworks-fintech
:::

## Developer handoff
```bash
git clone https://github.com/harshath125/tallyworks.git && cd tallyworks
ls -d */ && for s in gateway transaction-service reporting-service; do echo "== $s"; grep -n "server.port\|management.endpoint" $s/src/main/resources/application.yml; done
./mvnw -q verify                                # all four modules; 6-9 minutes, and that's a pipeline input
grep -RIn "outbox\|advisory" transaction-service/src/main/java | head
grep -RIn "System.out\|printStackTrace" */src/main/java | head    # must be empty
```

# PART B — THE DEVOPS JOURNEY

## Clone, inspect, decide the boundaries
Three `Dockerfile`s or one multi-job build? **Three images from one repo**, built by one pipeline with a matrix, because a shared image would force `transaction-service` releases to ship a reporting change (and vice versa) — that coupling is exactly what the split was meant to remove. Version them independently (`0.14.0`, `0.9.3`, `1.2.1`) and record the *tested combination* per environment in a `versions.yaml` the pipeline writes. That file is your answer to “which three images are known to work together?”

## Build, test, contract
```bash
./mvnw -B -pl common install -DskipTests              # the shared lib, once per build
./mvnw -B -pl transaction-service -am verify          # per service, with the reactor resolving common
./mvnw -pl contract-tests verify                      # consumer-driven contracts: run standalone in CI
./mvnw -B dependency-check:aggregate -DfailBuildOnCVSS=7   # SCA as a *build* goal, not just Trivy
```
**What just happened, and why the order matters:** `-am` (also make) builds `common` from source so the local build matches CI; the contract job runs *without* either service deployed, so a breaking producer change fails on the producer's PR rather than in integration — which is the one microservices practice that measurably reduces “whose build broke?” arguments.

## Dockerize ×3 (identical pattern, different artefacts)
```dockerfile
ARG BUILDER=eclipse-temurin:17.0.10_7-jdk-jammy
ARG RUNTIME=eclipse-temurin:17.0.10_7-jre-jammy
FROM ${BUILDER} AS build
WORKDIR /workspace
COPY pom.xml ./ ; COPY common common ; COPY transaction-service transaction-service
RUN mvn -B -pl transaction-service -am -DskipTests package
FROM ${RUNTIME}
RUN useradd -r -u 10001 -s /usr/sbin/nologin app && apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build --chown=10001 /workspace/transaction-service/target/*.jar app.jar
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=70 -XX:+ExitOnOutOfMemoryError -Djava.security.egd=file:/dev/./urandom"
USER 10001
EXPOSE 8081
ENTRYPOINT ["java","-jar","/app/app.jar"]
```
Three notes a reviewer will ask about: **`ARG BUILDER/RUNTIME`** so a base-image bump is a pipeline variable, not three edited files (and Pin by digest in the values file, so “the base moved under us” is impossible) · **`-am` inside the image** builds `common` in the same layer set — reproducible, at the cost of a longer build; the alternative (publish `common` to a Maven repo) is the mature choice at >3 services and is recorded as such in the ADR · **`JAVA_TOOL_OPTIONS` not `JAVA_OPTS`** because Spring Boot's script-based images don't read the latter, and a silently-ignored flag is worse than a smaller heap.

## Push, scan, sign, and the supply-chain line that matters here
```bash
for s in gateway transaction-service reporting-service; do
  trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed $R/tw-$s:sha-$SHA
  cosign sign --yes $R/tw-$s@$(digest $R/tw-$s:sha-$SHA)
  syft $R/tw-$s:sha-$SHA -o cyclonedx-json > sbom-$s.json   # 3 SBOMs, attached to the release
done
```
With a `common` library, “which services contain CVE-2026-1234?” is a question three registries can answer only if each image has its own SBOM. That's the argument for per-service artefacts stated in cost terms, and it's the sentence that gets you budget for a registry with indexing.

## Terraform: the platform both services sit on
EKS (2 managed node groups, `m6i.large` general + `c6i.xlarge` for reporting's rebuild bursts), an IRSA role per service, RDS Postgres with **three schemas and three roles**, PgBouncer (`transaction` mode; note the Hikari `prepareThreshold=0`/`ApplicationName` requirement in the ConfigMap), ElastiCache Redis for gateway limits, one shared ALB for the gateway only, `aws_eks_access_entry` for the CI role, KMS-encrypted EBS by default, and an ECR lifecycle policy keeping 30 images per repo.

## Networking and east-west traffic
North-south: `Route 53 → ALB → gateway` (the only LoadBalancer/Ingress in the stack). East-west: **Kubernetes Services by name** (`http://tallyworks-transaction.tallyworks.svc.cluster.local:8081`) — no Ingress per microservice, no public CLBs per hop, and one reason: the gateway already owns routing, auth and limits; adding a second proxy layer means two places to fix a timeout. A NetworkPolicy default-deny in `tallyworks-prod` allows `gateway→transaction`, `gateway→reporting`, `transaction→db`, `reporting→db`, `*→redis`, and monitoring-namespace scrape. Pod-level security: non-root, `readOnlyRootFilesystem` with an `emptyDir` for `/tmp`, `drop ALL`, and a `seccompProfile: RuntimeDefault`.

## Database, and the coupling you must refuse
One RDS cluster is a pragmatic start (three schemas, three roles, `transaction_rw`, `reporting_ro`, `gateway_rl` with grants that make the boundary real). The line to hold in review: `reporting`'s role must not have `SELECT` on `ledger_entry` — otherwise someone writes a `JOIN` “just for the campaign”, and the extraction silently failed. That check belongs in `checkov`-style policy (a SQL lint on the reporting migrations) rather than in a code comment.

## Deploy: one release train, three rollouts
```bash
helm upgrade --install tallyworks ./chart -n tallyworks-prod \
  --values values/prod.yaml --set gateway.image.digest=$G --set transaction.image.digest=$T \
  --set reporting.image.digest=$RP --atomic --wait-for-jobs --timeout 8m
kubectl -n tallyworks-prod rollout status deploy/tallyworks-transaction --timeout=6m
./scripts/check-invariants.sh https://prod.internal/actuator/invariants   # balanceImbalance == 0
```
`--atomic` matters more with three services than with one: without it you get a half-applied release (gateway new, transaction old) that no test covers. Rollout **order** per release: `transaction → reporting → gateway` (producers before consumers, so an unknown event never arrives at a service that can't parse it), and reverse for rollback. Write it in `docs/RELEASE-ORDER.md` — this is precisely the sort of tribal knowledge that a documented project turns into a repeatable one.

## CI/CD: matrix pipelines, shared standards
```yaml
# .github/workflows/services.yml (abridged)
on: { push: { branches: [main], paths: ['gateway/**','transaction-service/**','reporting-service/**','common/**','pom.xml'] } }
permissions: { id-token: write, contents: read }
jobs:
  build:
    strategy: { fail-fast: false, matrix: { svc: [gateway, transaction-service, reporting-service] } }
    steps:
      - uses: dorny/paths-filter@v3
        id: f
        with: { filters: ".github/filters.yaml" }     # common/** affects all three
      - if: steps.f.outputs.changed-${{ matrix.svc }} == 'true'
        run: ./mvnw -B -pl ${{ matrix.svc }} -am verify
      - if: steps.f.outputs.changed-${{ matrix.svc }} == 'true'
        uses: docker/build-push-action@v6
        with: { build-args: |
                  BUILDER=eclipse-temurin:17.0.10_7-jdk-jammy@sha256:…,
                  RUNTIME=eclipse-temurin:17.0.10_7-jre-jammy@sha256:… }
```
`filters.yaml` maps each service to its paths *plus* `common/**` and `pom.xml`; a change to the shared library rebuilds all three. `fail-fast: false` so one service's red build doesn't hide another's. One pipeline, per-service artefacts, one env-values PR per promotion — the pattern that scales to eleven services instead of becoming eleven copies of a Jenkinsfile.

## DevSecOps (this is the Phase 2 showcase)
| Gate | Where | Threshold, and the reason |
| :-- | :-- | :-- |
| SAST (SonarQube) | per service PR | new-code bugs=0, security hotspots reviewed; **per-service quality profiles**, because a gateway and a ledger have different “critical” |
| SCA (Trivy fs + OWASP dep-check) | after `verify` | HIGH/CRITICAL with a fix = block; without a fix = 14-day expiry via the ignore file + a ticket |
| Secret scan (gitleaks + GH push protection) | pre-commit and PR | any verified finding = block, always, and the *rotation* runbook opens automatically |
| Image scan + SBOM + cosign | build job | block on CRITICAL, verify signature at admission (Kyverno) |
| IaC (Checkov/tfsec + `tfsec`-generated policies) | plan job | P0/P1 fail; “no public DB”, “KMS required”, “no 0.0.0.0/0” are errors, not warnings |
| DAST (ZAP API scan against the OpenAPI spec) | staging deploy | medium+ findings on `/api/**` open tickets; **auth'd scan** with a test `CUSTOMER` token so the whole surface is covered |
| Runtime (Falco or CloudWatch + alarms) | prod | `exec` into a pod, new privileged container, `kubectl attach` → page |
| Dependency review | PR | `dependency-review-action` on any `pom.xml` change (typosquat + licence) |

Plus the two rules that keep a security programme honest: a **documented, expiring override** (a `security-ignore.yaml` with `expires: 2026-10-01` and a Jira key — an ignore file without an expiry is a lie) and **one owner per gate** named in the file header, so “who decided this was fine?” has a real answer.

## Monitoring
Four dashboards, one per concern: *service* (RED per service, gateway p95 broken out), *transaction core* (the invariant gauge — page on non-zero, always, and the runbook's first line is “stop writes”), *pipeline health* (`outbox_lag_seconds`, `settlement_batch_duration_seconds`, `events_consumed_lag`), and *cluster* (node pressure, PDB blocks, restarts). Alerts: p99 by route, 5xx ratio > 1 %, `outboxLagSeconds > 300`, `balanceImbalance != 0`, `reporting_staleness_seconds > 3600`, and a **gateway-vs-service latency diff** panel — the fastest way to see “the proxy is the problem”. Exemplars on the histograms so a spike is one click from a trace.

## Failure scenarios
| Break | Symptom | Root cause and the permanent fix |
| :-- | :-- | :-- |
| Reporting can't parse a new event | 400s in its log, lag climbs, transactions unaffected | producer changed the schema without a contract update → add a `schema-registry`-ish check: the contract job runs against the producer's *published* OpenAPI/AsyncAPI, so the producer's PR fails |
| Advisory lock lost on a DB failover | two `SETTLEMENT_BATCH` runs overlap for 8 s | fine, because the batch is idempotent — *prove* it in staging and write “safe by idempotency” in the runbook; that's the answer to “what if the lock is lost” |
| `common` bump rebuilds all three, prod deploy half-succeeds | gateway new, transaction old | `--atomic` + a rollout-order check in CI + a `versions.yaml` gate that refuses to promote a combination never run in staging |
| PgBouncer transaction mode + a `@Transactional` + `SELECT nextval` pattern | `cannot prepare statement inside a transaction in transaction pooling mode` | use statement mode for the transaction service, or set `prepareThreshold=0`; the fix that survives: a CI smoke test against PgBouncer, not just Postgres |
| Redis flaps | 429 storm then none | gateway's limit store is per-instance (documented!) — decide consciously: fall open, or move to a distributed limiter and accept the latency; either way it's in `docs/DECISIONS.md` |
| One pod OOMKilled during `rebuild` | restart loop when the job re-runs | request 1.5 Gi, limit 2 Gi, and chunk size from a ConfigMap so ops can tune it without a rebuild |

## Troubleshooting — “5xx on `/api/transactions` at 10:02, gateway and transaction both ‘healthy’”
`/ready` green everywhere → gateway metrics show `resilience4j_breaker_sessions{breaker="transaction"}` OPEN → transaction's RED is *fine* → so the gateway's circuit opened on a 2-second timeout while the service was answering in 2.4 s under a batch spike. Two fixes, both shipped: the timeout to 5 s with `retry=0` on POST (a retry here would double-charge: idempotency covers it, but the retry budget still wastes capacity), and a `rebuild` schedule moved off the top of the hour plus `resources.requests` on reporting so its CPU burst can't starve the transaction pod on a shared node (a burstable-node `cpu` contention story people misdiagnose as “the JVM is slow”). The lesson you take: **a timeout is a contract between services and must be written down** — a `docs/LATENCY-BUDGETS.md` with p99 per hop and the rule “gateway timeout > sum of downstream budgets”.

## Scaling
Gateway on CPU + `requests_per_target`; transaction on **p95 and DB connections** (a CPU-only policy is how you get 20 pods and one saturated PgBouncer); reporting on `events_consumed_lag` with a floor that lets it scale to 1 at night. `HPA` `behavior.scaleUp.stabilizationWindowSeconds: 0` (be eager) and `scaleDown: 300` (be lazy), plus `topologySpreadConstraints` so a scale-out doesn't stack four pods on the node that just caught fire. Karpenter consolidates at 03:00 (not at 10:00).

## Rollback
Per service, one `helm` values change; `transaction → gateway → reporting` (consumers first when rolling back, so the consumer never sees a producer's *new* event format). Migrations expand-only, with the reporting projection rebuilt by `POST /api/reports/rebuild` (idempotent, chunked) instead of a DB rollback. And the release gate includes a **rollback rehearsal on staging for every release**, with the number posted in the release thread — the 90 seconds is the artefact as much as the chart.

## Final architecture
::: flow
clients → Route53 → ALB → tallyworks-gateway×N (IRSA, limits in Redis, circuit breakers) → transaction×3-12 (outbox+advisory lock) → Postgres(schema: tw_tx) ; → reporting×1-6 (projection, rebuild) → Postgres(schema: tw_rep, role: ro) ; GitOps (Argo CD, sync waves: migrations → transaction → reporting → gateway) ; one pipeline, matrix builds, three signed images, three SBOMs ; Falco + Prometheus + Grafana + one pager ; Terraform: EKS, RDS, ElastiCache, endpoints, KMS, node groups
:::

## What I learned
Microservices are a *coupling* decision, not an architecture badge: the boundary survives only if the DB roles, the contract tests and the release order all enforce it. And three services make three things unavoidable — a shared standard (base images, probes, policies, dashboard template), an explicit release train, and per-service artefacts — which is why the “platform” in Platform Engineering is mostly these three files.

## Interview questions
::: grid2
**“Why three services and not five?”** → bounded contexts and a scaling/ownership difference, not org-chart fashion; reporting is read-heavy and can be stale, transactions cannot, and the gateway exists so clients don't learn topology. A fifth service (`payments`) waits until its own scaling or team pressure justifies it.
**“How do you avoid distributed transactions?”** → don't: outbox + local transaction on the write side, idempotent consumers, compensating entries for reversals; sagas only when there's a real multi-step workflow.
**“What breaks first when you split a monolith?”** → the timeouts and the connection pool: the gateway's timeout budget, and one Postgres' connection ceiling multiplied by N services.
**“One repo or many?”** → monorepo for this size, with per-service build/deploy; many repos when ownership and release cadence truly diverge. The cost I paid here is rebuild fan-out on `common` changes, and the fix is path filters plus publishing `common` as a versioned artefact.
:::

## Résumé bullets
::: callout note ONLY WHAT YOU ACTUALLY BUILT
- Split a Spring Boot monolith into a gateway and two services with contract tests and an event-driven projection, then built one matrix pipeline (per-service images from a shared base, path-filtered rebuilds, three SBOMs, cosign signatures) and a release train with a documented rollout order (producer → consumer → gateway).
- Enforced service boundaries in the platform, not in comments: separate Postgres schemas and roles (reporting cannot read the ledger), a SQL-lint check in CI, and default-deny NetworkPolicies for east-west traffic.
- Deployed to EKS via Argo CD with sync waves and `--atomic` Helm releases, per-service IRSA roles, PodDisruptionBudgets and topology spread; canary promotion on error rate and p99 with automatic abort.
- Wired a ledger invariant (`balanceImbalance`) and outbox-lag metrics into paging alerts, halving MTTR on settlement-delay incidents and catching a silently-stale reporting projection the same week it was added.
:::

::: revision QUICK REVISION — 5 minutes
**Concepts:** bounded contexts and coupling · no shared DB, no cross-service FK · outbox + idempotent consumers vs 2PC · contract testing as a producer PR gate · release order producer→consumer, reverse on rollback · shared library = rebuild fan-out (path filters) · latency budgets across hops and the circuit-breaker/timeout interaction · PgBouncer pooling modes and prepared statements · per-service images + one tested-combination manifest · security gates with expiring ignores and named owners · advisory locks and “safe because idempotent”.
**Commands:** `./mvnw -pl X -am verify` · `./mvnw -pl contract-tests verify` · `docker build --build-arg RUNTIME=… -t tw-x:sha-$S .` · `trivy image --exit-code 1 --severity HIGH,CRITICAL IMG` · `syft IMG -o cyclonedx-json` · `cosign sign --yes IMG@sha256:…` · `helm upgrade --install --atomic --wait-for-jobs --timeout 8m` · `kubectl -n tw-prod rollout status deploy/…` · `argocd app wait --health` · `aws eks update-kubeconfig --name tw-prod --region eu-west-1` · `psql -c 'select role, schema from …'` for the grants audit.
**Architecture:** client → ALB → gateway → tx (outbox) → DB schema A ; consumer → DB schema B (read-only role) ; one pipeline, three artefacts, one values file per env, one invariant that pages.
**Common mistakes:** one Dockerfile for all services · per-service public load balancers · `reporting` selecting from `ledger_*` · retries on non-idempotent POSTs · gateway timeout smaller than the sum of budgets · an ignore file without an expiry · “microservices” without contract tests.
**Troubleshooting checklist:** which service is red (per-service RED) → is the circuit open (gateway) → is the DB/pool saturated → is the outbox lagging → did a `common` bump half-deploy (`versions.yaml`) → did a schema/role boundary get crossed → trace one `correlation_id` end-to-end → and which gate should have caught it.
:::

Next: [Project 07 — CoverDesk on Azure DevOps →](proj-07-insurance.html)
