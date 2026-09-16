LEAD: NetSwitch is the Kubernetes project. A telecom provisioning service with a workflow engine, delivered as source by your coding agent, and by the end of this page it runs on EKS with probes that reflect its real dependency semantics, a PodDisruptionBudget, an HPA that scales on queue depth, a canary behind an ALB, and a rollback you have rehearsed. No cloud console for the deploy: it's all declarative and reviewed.

# PART A — GET THE APPLICATION

## Overview
FastAPI + Postgres + Redis, two entrypoints (`api`, `engine`), an idempotent step machine (`VALIDATE_SITE → RESERVE_PORT → CONFIGURE_CPE → TEST_LINK → ACTIVATE → NOTIFY_CUSTOMER`), cursor pagination, HMAC webhooks, and a `/ready` that reports the applied migration version.

## Business scenario
An order stuck for three days is a truck roll, a customer complaint and a penalty clause. Kubernetes here is not a badge: it's justified because (a) the engine and the API have opposite scaling shapes, (b) orders are long-running so restarts must be *safe*, not just fast, and (c) the deploy must be gradual (a bad provisioning step config can send engineers to the wrong address).

## Tech stack (received)
| Layer | Inherited | Operational meaning on K8s |
| :-- | :-- | :-- |
| FastAPI + uvicorn, 1 worker per container | `PORT=8080` | Single process per pod is fine: the horizontal axis is the cluster's job |
| async SQLAlchemy + asyncpg | pool of 20 | 20 × replicas must stay under Postgres' `max_connections-3` — a number you'll compute, and an HPA max replica limit that follows from it |
| Alembic, no auto-migrate | `/ready` exposes the version | A `Job` (Helm hook `pre-upgrade`) gates the rollout; readiness **refuses** to pass on a version mismatch |
| Engine as a separate entrypoint | graceful TERM, 25 s drain | `terminationGracePeriodSeconds: 35` and a `preStop` sleep; the drain window is a *contract*, not a suggestion |
| `SKIP LOCKED` queue | safe with concurrent engines | so the engine can have 3 replicas without double-driving — the DB does the coordination, which is exactly what you want from a platform |
| Prometheus metrics | `route` label as template | cardinality safety built in by the developer, and one reason your recording rules stay cheap |

## Architecture (as delivered)
::: flow
portal/partner systems → HTTP → API pods → Postgres (orders, steps, ports, reservations) ↔ engine pods (drive QUEUED/RETRY_WAIT) → external OSS/BSS stubs ; webhooks in ← signature-verified, idempotent
:::

## What the developer will build
Seven endpoint groups plus the engine, a state machine with per-step timeouts and retry budgets, circuit breakers (open for writes, fail-open for reads), a reservation sweeper, JSON logs with `order_id`, PII masking, 40+ tests including “two concurrent engines never double-drive one order”, and `docs/OPERATIONS.md` telling you exactly how to replay one order and how to clear a stuck one safely.

## Copyable Developer Agent Prompt
::: prompt netswitch-telecom
:::

## Developer handoff
```bash
git clone https://github.com/harshath125/netswitch.git && cd netswitch
grep -n "TERM_GRACE_SECONDS\|run_in_threadpool" -r src/ | head
sed -n '1,60p' docs/OPERATIONS.md          # the two entrypoints, drain semantics, replay commands
pytest -q                                   # 40 tests, against a local pg/redis via docker
grep -RIn "max_connections\|pool_size" src/netswitch/db/ | head    # the number your HPA depends on
```

# PART B — THE DEVOPS JOURNEY

## Clone, inspect, cluster plan
```bash
ls docs/ ; git ls-files | grep -icE "helm|k8s|dockerfile|terraform"   # 0 expected — the developer did none
kubectl config current-context ; kubectl version --short 2>/dev/null || kubectl version
```
**Cluster shape for this project:** EKS 1.30, 2 node groups (`m6i.large`×3 general, `c6i.large`×2 for the engine's CPU-bound address normalisation), `metrics-server`, `aws-load-balancer-controller`, `karpenter` (or the cluster autoscaler — pick one and say why in the decision log), `external-secrets` or the Secrets Store CSI driver with the AWS provider, `kube-prometheus-stack`, `ingress-nginx` only if you need path rewriting (otherwise the ALB controller alone, for fewer moving parts), `kyverno` for admission policy, and a `monitoring` namespace that is *not* in the app team's write scope.

## Identify dependencies, then write the manifest set
| Question | Where it's answered | Manifest consequence |
| :-- | :-- | :-- |
| Startup time? | `docs/OPERATIONS.md` (≈6 s) | `startupProbe` with 12 × 5 s, and no `initialDelaySeconds` theatre |
| Readiness semantics? | `/ready` returns DB + migration version | readiness = `/ready`; liveness = `/health` (no external deps) — the developer gave you both, so use them |
| Does it need to run once or many? | engine: many, via `SKIP LOCKED` | Deployment with `replicas: 3`, **not** a Job |
| What must run before? | migrations | Helm `pre-upgrade` hook Job with `hook-delete-policy: before-hook-creation` |
| Any shared filesystem? | no (all state in Postgres; exports go to S3 via a presigned URL) | zero `ReadWriteOnce` volumes → pods can move between AZs freely. That's a *big* K8s win, so note it in the handoff |
| Secrets? | 5 of them | external-secret CRs referencing the same names the app already expects; no `Secret` ever committed |

```yaml
# helm/templates/deployment-api.yaml (abridged; the engine differs only in command and probes)
apiVersion: apps/v1
kind: Deployment
metadata: { name: {{ .Release.Name }}-api, labels: { app: netswitch, role: api } }
spec:
  replicas: {{ .Values.api.replicas }}
  strategy: { type: RollingUpdate, rollingUpdate: { maxSurge: 1, maxUnavailable: 0 } }
  revisionHistoryLimit: 5
  selector: { matchLabels: { app: netswitch, role: api } }
  template:
    metadata:
      labels: { app: netswitch, role: api }
      annotations: { prometheus.io/scrape: "true", prometheus.io/port: "8080",
                     prometheus.io/path: "/metrics" }
    spec:
      serviceAccountName: netswitch-api                  # IRSA: one role, three statements
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: 35
      securityContext: { runAsNonRoot: true, runAsUser: 10001, fsGroup: 10001, seccompProfile: { type: RuntimeDefault } }
      topologySpreadConstraints:
        - { maxSkew: 1, topologyKey: topology.kubernetes.io/zone, whenUnsatisfiable: ScheduleAnyway,
            labelSelector: { matchLabels: { app: netswitch } } }
      containers:
        - name: api
          image: "{{ .Values.image.registry }}/netswitch@{{ .Values.image.digest }}"
          args: ["uvicorn","netswitch.main:app","--host","0.0.0.0","--port","8080"]
          ports: [{ name: http, containerPort: 8080 }]
          envFrom:
            - configMapRef: { name: netswitch-config }
          env:
            - name: DATABASE_URL
              valueFrom: { secretKeyRef: { name: netswitch-secrets, key: database-url } }
          resources: { requests: { cpu: 300m, memory: 512Mi }, limits: { cpu: "1", memory: 768Mi } }
          startupProbe:  { httpGet: { path: /health, port: http }, failureThreshold: 12, periodSeconds: 5 }
          readinessProbe:{ httpGet: { path: /ready, port: http }, periodSeconds: 10, failureThreshold: 3, timeoutSeconds: 3 }
          livenessProbe: { httpGet: { path: /health, port: http }, periodSeconds: 20, failureThreshold: 3, timeoutSeconds: 3 }
          lifecycle: { preStop: { exec: { command: ["sh","-c","sleep 8"] } } }
          securityContext: { allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] } }
          volumeMounts: [{ name: tmp, mountPath: /tmp }]
      volumes: [{ name: tmp, emptyDir: { sizeLimit: 128Mi } }]
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: { name: netswitch-api }
spec: { maxUnavailable: 1, selector: { matchLabels: { app: netswitch, role: api } } }
```
**Two lines to be able to justify:** the image is referenced by `@digest` because Argo CD must reconcile to a *known* image, not a mutable tag (with a tag, “sync” can mean five different things); and `preStop sleep 8` exists because the ALB deregistration call and the kube-proxy endpoint removal race, and the 8 seconds is what makes the window overlap instead of gap — measure it rather than trusting folklore (`for i in $(seq 50); do curl -s -o /dev/null -w '%{http_code} ' …; done` during a rollout is a fine way to find your number).

## Dockerize, build, push (same discipline as 04, shorter because it is)
```dockerfile
FROM python:3.12.3-slim-bookworm AS build
RUN pip install --no-cache-dir uv && uv pip install --system -r requirements.lock
FROM python:3.12.3-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl tini && rm -rf /var/lib/apt/lists/* \
 && adduser --system --uid 10001 --group app
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
WORKDIR /app ; COPY --chown=10001 src alembic.ini ./
USER 10001
ENV PYTHONUNBUFFERED=1 PORT=8080
ENTRYPOINT ["tini","--"]        # PID 1: forwards SIGTERM, reaps children — the difference between a clean drain and SIGKILL at 30s
CMD ["sh","-c","exec uvicorn netswitch.main:app --host 0.0.0.0 --port ${PORT}"]
```
`tini` is the one thing you should remember from this page if you remember nothing else: uvicorn is not a signal-forwarding init, and without a proper PID 1 your `terminationGracePeriodSeconds` is fiction.

## Deploy, and the GitOps step that replaces `kubectl apply`
```bash
# dev: argo cd app of app (source: infra repo, path: envs/dev, selfHeal on)
argocd app sync netswitch-api --prune --timeout 180
kubectl -n netswitch rollout status deploy/netswitch-api --timeout=180s
kubectl -n netswitch get pods -l app=netswitch -o wide
argocd app get netswitch-api -o json | jq '.status.health, .status.sync.status'
# the pipeline's gate — and the reason nobody deploys with kubectl anymore:
argocd app wait netswitch-prod --health --timeout 240
```
**What just happened:** Git is the source of truth; Argo CD diffs live vs Git; the pipeline updates *one field* (`image.digest`) in the env values file and opens a PR or pushes to `main` for dev, then **waits for the app's health** rather than for an exit code. The failure mode of `kubectl apply` in CI — “applied, so green, but the rollout never finished” — is structurally impossible.

## Networking: ALB ingress with target-group health checks, and one trap
`ingressClassName: alb`, `alb.ingress.kubernetes.io/target-type: ip` (not `instance` — NodePort adds a hop and hides the pod IP from the target group), `healthcheck-path: /ready`, `success-codes: http_200`, `healthcheck-interval-seconds: 15`, `healthy-threshold-count: 2`, `unhealthy-threshold-count: 3`, `stickiness.enabled: false`, `idle_timeout: 60` (≥ the API's slowest request), and `algorithm: least_oudest`… no: `least_outstanding_requests`, which is the right choice when request durations vary by 10× as they do here.
**The trap:** with `target-type: ip` the health checker must reach the **pod** network, so a NetworkPolicy in the namespace has to allow `ingress from the ALB's subnets/security group on 8080`. The classic symptom is “pods Ready in `kubectl`, targets unhealthy in the console, and no events anywhere”. The fix is one line in the policy: `ipBlock: 10.0.0.0/16` (the VPC) on the probe port — and a *test* for it: `kubectl run -n netswitch curl … -- -sv http://<pod-ip>:8080/ready`.

## Database
RDS Postgres in the same VPC, PgBouncer (`transaction` pooling) as a Deployment with its own Service because 3 × 20 API connections + 3 × 20 engine + report queries exceeds `max_connections=200` on a `micro`, and `pgbouncer`'s `server_reset_query` documented so asyncpg's prepared statements don't break it (set `statement_cache_size=0` or `prepare_threshold=None` on the driver — a real, specific, .NET-in-Java-land kind of gotcha that interviewers love when it's *yours*). Migrations as a Helm hook Job with `backoffLimit: 1` and a `ttlSecondsAfterFinished: 3600` so the namespace doesn't accumulate job pods.

## CI/CD: the promotion path, not the YAML
```
PR → lint(ruff) + type(mypy) + test + gitleaks + trivy fs + `alembic check` (autogen must be empty)
merge → build (SBOM + provenance) → scan → sign → push digest → update dev values → Argo syncs dev →
        verify: /ready version + a smoke order driven to ACTIVATE in the dev cluster →
        promote: PR bumping staging values with the same digest → [prod PR with 2 approvals] → Argo syncs →
        Argo Rollouts canary 5 % → 25 % → 50 % → 100 % with analysis on error rate + p99 + step_failure_total
        → any analysis failure: automatic abort + rollback, and the PR is labelled `rollback` so the record is legible
```
The `alembic check` gate is the Django `makemigrations --check` equivalent and costs nothing; the analysis templates (Argo Rollouts) are the part that makes canaries real rather than decorative: `successRate >= 0.99`, `criticalAlertsCount == 0`, and a pause requiring human confirmation before 100 %.

## DevSecOps
Non-root with a read-only root filesystem and `drop ALL` (already in the manifest — and the test that proves it is “does the pod stay up”, which it does, because the developer put nothing on disk); `automountServiceAccountToken: false` on pods that don't need the API; a Kyverno `deny` policy for `:latest`, privileged, `hostPath`, and images not from our ECR; NetworkPolicy default-deny in the namespace with only the four allowed paths (api→db, api→redis, engine→db, alb→pods); Trivy on the image with a *time-boxed* allowlist file so a HIGH with no fix expires in 14 days instead of living forever in `.trivyignore`; `secretsmanager` access via IRSA so no pod can read the app's DB password from another namespace's role; and a `kubeaudit`/`polaris` report as a PR comment.

## Monitoring
Per-role RED from the pod scrape (`sum by (role)(rate(http_requests_total[5m]))`), engine-specific: `netswitch_engine_last_poll_age_seconds > 30` (page), `step_failures_total{code="TIMEOUT"}` rate (ticket) vs `{code="CONFIG_REJECTED"}` (a *product* signal that goes to the partner team — writing that alert in the ticket queue rather than the pager is a maturity signal worth claiming), `order_age_seconds{status="QUEUED"} > 900` (the business page: “an order is stuck”), plus `container_oom_killed`, `kube_pod_container_status_restarts_total`, and the PDB-relevant `node:pod_pending` for `Pending` > 2 min. One dashboard, three rows (service, engine, cluster), deploy annotations from Argo's webhook so a vertical line explains every step change.

## Failure scenarios — rehearse all seven
| Break | What the platform does | What you do next, and what you change |
| :-- | :-- | :---|
| Delete a node with the engine on it | PDB blocks if it would drop below 2; else drain + reschedule | add a 4th replica to the HPA min in prod, and an alarm on `engine_last_poll_age` so a 90-second gap is visible |
| `CrashLoopBackOff` on the new ReplicaSet | old pods keep serving (`maxUnavailable: 0`) | `logs --previous` → `asyncpg.exceptions.InvalidPasswordError` → the secret was rotated in AWS but the ExternalSecret hadn't synced: `kubectl -n netswitch describe externalsecret` shows the refresh error; fix: a sync-interval alarm |
| Pod `Pending` after a config change | scheduler can't satisfy the new 1 CPU request on any node | `describe pod` events (`0/5 nodes are available: 3 Insufficient cpu`) → reduce the request or bump the node group — and *this* is where the requests-vs-limits interview answer lives |
| `OOMKilled` on the engine | exit 137, restart, backlog grows | working-set graph vs limit; a 12 000-row batch was loaded at once; fix code (page it) **and** raise the limit, then keep the alarm so we notice the next regression |
| ALB targets unhealthy, pods Ready | the NetworkPolicy trap above | `describe ingress`, `TargetHealthReason: "unused"`; add the ipBlock rule and a CI check that probes `/ready` **from inside the cluster's pod network** |
| Rollout stuck at `1 old replicas pending termination` | a preStop hook or finalizer hanging | `kubectl get pods -o jsonpath` for `deletionTimestamp`; the `aws-load-balancer-controller` finalizer failed to deregister because the target group was hand-edited → drift check |
| Migration hook Job fails | the release aborts; nothing rolls | `logs` the hook, `alembic history` vs DB, and this is the good outcome — say it out loud in review: “the deploy stopped itself” |

## Troubleshooting — the pattern for “the order is stuck”
`GET /v1/events?order_id=` shows `CONFIGURE_CPE` in `RETRY_WAIT` with `error_code=OSS_TIMEOUT`, `attempts=5`. So: not our code, an external system. But `step_failures_total{code="OSS_TIMEOUT"}` shows the same for **every** order since 11:40, and the API p99 is fine, and the engine's `last_poll_age` is 2 s. So the engine is healthy and the dependency is down. Action: mark orders `NEEDS_HUMAN` via the documented bulk script (the runbook the developer wrote), page the partner team, add an alarm on `{code="OSS_TIMEOUT"}` rate > 20/min with a `runbook_url` annotation, and *afterwards*: a circuit breaker that stops retrying the same order 500 times a minute (retry budget exhausted) — a fix whose absence was costing us partner API spend.

## Scaling
HPA on a **custom metric**: `kube_custom_metrics_netswitch_queued_orders` (from the exported gauge via prometheus-adapter) with `target: 50` per pod, `min 3 max 10`; the API on `cpu 60 %` + a second HPA on `p95 latency` (KEDA ScaledObject if you prefer, and the *reason* to prefer it: scaling to zero overnight in staging saves $90/month). Scale-down behaviour: `stabilizationWindowSeconds: 300` (or the engine flaps), and `behavior.scaleDown.policies: [{type: Pods, value: 1, periodSeconds: 180}]` because each scaled-down engine must finish its in-flight steps and a 25-second drain × 5 pods at once is how you lose work. Vertical: `in-place` VPA for the engine in *audit mode* only — `updatePolicy: {updateMode: "Initial"}` — because an autoscaling VPA plus an HPA on CPU is a fight with no winner.

## Rollback
`argocd app rollback netswitch-prod <revision>` **or** better, `git revert` the values PR (which keeps Git true and produces the audit trail) — with a decision rule written in the runbook: *revert in Git unless the cluster is on fire, in which case roll back and revert within the hour*. Migrations: expand-only, so nothing to unwind; a contract migration ships ≥ 1 release after its expand, with a `checkov` custom rule failing PRs that contain `DROP COLUMN`/`RENAME` alongside a feature change. Rehearse: break staging on purpose every Friday, time the rollback, post the number in the channel. That ritual is worth more than the manifest.

## Final architecture
::: flow
DNS → ALB (TLS/ACM, least-outstanding-requests, /ready health) → ingress→netswitch-api×3-10 (HPA cpu, IRSA role, read-only fs, PDB maxUnavailable 1) → netswitch-engine×3-10 (HPA on queued-orders, tini, 35 s drain) → PgBouncer → RDS Postgres (multi-AZ, KMS, 7d) + Redis (AZ-redundant) ; Argo CD (GitOps, selfHeal, no sync windows during business hours) + Argo Rollouts (canary 5/25/50/100 with analysis) ; Kyverno admission + Trivy+cosign + NetworkPolicy default-deny ; kube-prometheus-stack → Grafana/Alertmanager → PagerDuty ; all manifests and policies from the infra repo, reviewed by PR
:::

## What I learned
Kubernetes rewards the questions you answer *before* the YAML: what must run first (migrations as a hook), what must not run twice (nothing — the engine coordinates in the DB), what a restart costs (25 s of drain, so the grace period is 35), and what “ready” really means to *this* app (the migration version, not an HTTP 200). And the health-check NetworkPolicy bug is the clearest lesson that clusters are a *network*, not a container runtime.

## Interview questions
::: grid2
**“How do you do a zero-downtime deploy on K8s?”** → readiness gating + `maxUnavailable: 0` + preStop sleep so LB deregistration completes + `terminationGracePeriodSeconds` longer than the drain + a PDB + `rollout status --timeout` in CI, and for risky releases a Rollouts canary with analysis that aborts on error-rate regression. All of those are on my page; the number I can add is the measured overlap window.
**“Your app uses Postgres connections. What breaks when you scale?”** → the connection budget: replicas × pool ≤ `max_connections-3`; the fix ladder is reduce pool → PgBouncer transaction pooling → RDS Proxy, with the asyncpg prepared-statement caveat for PgBouncer.
**“How do you run migrations with GitOps?”** → a Helm/Kustomize pre-upgrade Job (or Argo Workflows as a sync wave) with a version reported in readiness, expand-only DDL, and a rollback story that doesn't require unwinding schema.
**“Pod `Pending`: your steps?”** → `describe pod` events first (insufficient cpu/memory, unschedulable taint, PVC binding), then `kubectl top node` / `kubectl get pv,pvc` for a stuck `Pending` volume, then whether the *requests* are wrong rather than the cluster.
:::

## Résumé bullets
::: callout note ONLY WHAT YOU ACTUALLY BUILT
- Deployed a FastAPI service and its workflow engine on EKS with Helm: rolling updates with `maxUnavailable: 0`, three-tier probes (`startup`/`readiness` on the migration version/`liveness` dependency-free), PodDisruptionBudgets, zone topology spread, read-only root filesystems with non-root UIDs and `drop ALL` capabilities.
- Replaced imperative CI deploys with GitOps: Argo CD syncing reviewed manifests and Argo Rollouts canary promotion (5/25/50/100) with automated analysis on error rate and p99, aborting and rolling back releases in under 2 minutes; rollback is a reverted values PR, giving a Git-visible audit trail.
- Diagnosed and fixed ALB target health for `target-type: ip` (namespace NetworkPolicy for the health-checker path), verified with an in-cluster probe added to CI so the class of bug can't return.
- Autoscaled the engine on queued-orders (custom metric via prometheus-adapter) with a 5-minute stabilisation window and bounded scale-down to respect the 25-second drain, cutting stuck-order incidents while removing idle capacity overnight.
:::

::: revision QUICK REVISION — 5 minutes
**Concepts:** reconcile loop as the mental model · probes ×3 and their semantics by dependency · `maxSurge/maxUnavailable` arithmetic on 2-4 replicas · preStop vs grace period vs LB deregistration · PDB vs rollout strategy · requests = scheduling, limits = ceiling (memory is execution) · HPA on custom metrics and the stabilisation window · VPA modes and the HPA conflict · GitOps sync/prune/selfHeal and `app wait` · canary analysis as a promotion gate · Helm hook Jobs for migrations · IRSA and no-secret pods · NetworkPolicy + `target-type: ip` health checks · PgBouncer/asyncpg prepared-statement caveat · `SKIP LOCKED` as the reason N engine replicas are safe · Argo `rollback` vs `git revert` as a policy choice.
**Commands:** `kubectl get pods -o wide` / `describe pod | sed -n '/Events:/,$p'` / `logs --previous` / `top pod --containers` · `kubectl rollout status|history|undo` · `kubectl get endpointslice -l app=netswitch` · `kubectl auth can-i --list --as=system:serviceaccount:netswitch:netswitch-api` · `argocd app get -o json | jq .status` / `app wait --health` / `app rollback` · `kubectl -n argo rollout status` + `kubectl argo rollouts get rollout netswitch-api --watch` · `aws elbv2 describe-target-health` · `helm get manifest netswitch -n prod | grep -A3 image:`.
**Architecture:** Git → Argo CD → (hook Job: migrate) → api + engine Deployments → Service → ALB ingress with pod-IP targets → Postgres behind PgBouncer + Redis; policy from Kyverno; signals to Prometheus; canary by weight with analysis.
**Common mistakes:** `:latest` in a manifest · liveness on `/ready` · `initialDelaySeconds` instead of a startup probe · no PDB then a surprise node drain · HPA and VPA both on CPU · `terminationGracePeriodSeconds` shorter than the app's drain · editing live YAML “just for now” and losing it at the next sync · an ingress class with no controller.
**Troubleshooting checklist:** `get pods -o wide` → status and node → `describe` events + Last State → `logs --previous` → `endpointslice` (is the Service even wired?) → in-cluster `curl` to the pod IP (is it network or app?) → `argocd app get` health vs sync (which is *different* information) → `auth can-i` → `top node` → and the deploy marker on the graph.
:::

Next: [Project 06 — TallyWorks on EKS with DevSecOps →](proj-06-fintech.html)
