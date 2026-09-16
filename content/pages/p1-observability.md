LEAD: Observability is the ability to answer a question you did not predict. Monitoring asks “is CPU high?”; observability answers “why is p99 latency up only for the checkout endpoint on the Tuesday deploy, in one AZ?”. This page builds the three signals, the query language for two of them, and the alert discipline that keeps you employable and rested.

## Metrics, logs, traces — three tools for three shapes of question

| | **Metrics** | **Logs** | **Traces** |
| :-- | :-- | :-- | :-- |
| Shape | Numeric time series, aggregated | Text events, per occurrence | Causal tree of spans across services |
| Perfect for | Trends, thresholds, capacity, SLOs | “what exactly happened, with the ID and the user?” | “where did the 2 seconds go?” |
| Cost curve | Cheap: one datapoint per label-combination per interval | Expensive: storage + ingest scale with traffic | Middle: sample it, keep the interesting ones |
| Cardinality trap | `label{instance="pod-abc"}` per pod per restart → TSDB explodes | — | — |
| Tooling | Prometheus/VictoriaMetrics, CloudWatch, Azure Monitor, Cloud Monitoring | Loki, CloudWatch Logs, Log Analytics (KQL), Cloud Logging, Elasticsearch | Tempo/X-Ray/App Insights/Cloud Trace/OTel |
| Fails you when | The average hides the p99, or you aggregated the answer away | You can't grep 40 GB across 6 pods at 2 a.m. | You didn't propagate a context header, so the trace stops at the queue |

::: callout note THE SENTENCE THAT STRUCTURES AN INCIDENT
*Metrics* tell you **when** and **how much** → *traces* tell you **where** → *logs* tell you **what exactly**. So the workflow is always the same direction: dashboard panel → exemplar trace ID → `trace_id="abc"` in the log query. Set that up once (correlation IDs in logs + `trace_id` in OTel exporters) and MTTR halves — and “we have metrics and logs” stops being a complete answer.
:::

**The USE / RED method, because interviewers say these acronyms:** USE per resource = Utilisation, Saturation, Errors (CPU, memory, disk queue, connection pool). RED per service = Rate, Errors, Duration (the golden signals; requests/min, error %, latency histogram). Four panels per service: RED. One row per node type: USE. That's ~80 % of a useful dashboard, built in an afternoon, and it's what I put on the wall of a war room.

## Prometheus + Grafana on a real project

```yaml
# kube-prometheus-stack (Helm) — the stack I deploy unless told otherwise
helm upgrade --install obs prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set prometheus.prometheusSpec.retention=30d \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=gpi3 \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=100Gi \
  --set grafana.adminSecret.name=grafana-admin \
  --set alertmanager.config.route.group_by=[namespace,alertname] \
  --set alertmanager.config.route.repeat_interval=4h \
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false   # ← else PodMonitors are ignored
```

**Exposition model — the part beginners get wrong:** Prometheus *pulls*. Your app exposes `/metrics` in text format; a `ServiceMonitor`/`PodMonitor` (or EC2's prometheus agent config, or an Azure managed Prometheus scraper) tells Prometheus *what* to scrape and how often. So “no data” is nearly always one of: pod not labelled for the selector, `/metrics` returning 401 (the scrape path must be unauthenticated *or* mTLS/bearer configured), network policy blocking `monitoring` ns → app ns, or the port name not matching (`port: http` vs `targetPort: 8080`).

```text
# what your app should export, with the labels you'll need
# HELP http_requests_total total API requests by route, method, status
# TYPE http_requests_total counter
http_requests_total{route="/api/appointments",method="POST",status="201"} 48213
http_request_duration_seconds_bucket{route="/api/appointments",le="0.25"} 47002
http_request_duration_seconds_bucket{route="/api/appointments",le="0.5"}  48100
http_request_duration_seconds_bucket{route="/api/appointments",le="+Inf"} 48213
```

::: callout why COUNTERS, HISTOGRAMS AND THE p99 ARITHMETIC
A **counter** only goes up (requests, errors); you turn it into a rate with `rate()`/`increase()`, and restarts don't break `rate()` (it handles resets). A **gauge** goes up and down (memory, queue depth, pod count) — never wrap a gauge in `rate()`. A **histogram** is buckets + `_sum` + `_count`, and it's the *only* way to compute a percentile honestly: `histogram_quantile(0.99, sum by (le, route) (rate(..._bucket[5m])))`. The `by (le)` is mandatory — drop it and the quantile is meaningless. And **averages lie**: a mean latency of 120 ms with a p99 of 4 s means a fraction of your users are having a terrible time — alert on the quantile, and prefer a bucket boundary near your SLO because `_le` interpolation assumes uniform spread inside the bucket.
:::

### PromQL: the eight queries that cover 90 % of incidents

```promql
# 1 · request rate per second, by route
sum by (route) (rate(http_requests_total{namespace="prod"}[5m]))

# 2 · error ratio (the alert that matters), with a floor so low traffic can't flap
sum(rate(http_requests_total{namespace="prod",status=~"5.."}[5m]))
  / sum(rate(http_requests_total{namespace="prod"}[5m])) > 0.01
and sum(rate(http_requests_total{namespace="prod"}[5m])) > 1

# 3 · p99 duration by route, across replicas
histogram_quantile(0.99, sum by (le, route) (rate(http_request_duration_seconds_bucket{namespace="prod"}[5m]))) > 2

# 4 · burn rate: fast burn (2% of budget in 5m) — page; slow burn (5% in 6h) — ticket
(sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))) > 14.4 * 0.001
  and (sum(rate(http_requests_total[1h])) > 0.05)

# 5 · Kubernetes: is it the node or the pod?
sum by (node) (1 - avg by (node) (rate(node_cpu_seconds_total{mode="idle"}[5m]))) > 0.85
max by (namespace,pod,container) (container_memory_working_set_bytes{container!="",container!="POD"})
  / max by (namespace,pod,container) (container_spec_memory_limit_bytes{container!=""}) > 0.9

# 6 · the restart / OOM signals every on-call should have
increase(kube_pod_container_status_restarts_total{namespace="prod"}[15m]) > 2
kube_pod_container_status_last_terminated_reason{namespace="prod",reason="OOMKilled"} == 1

# 7 · saturation of a resource you forgot: connection pool + file descriptors
pg_stat_activity_count{datname="medbook"} / pg_settings_max_connections > 0.8
process_open_fds / process_max_fds > 0.8

# 8 · delta over a window, to see "since the deploy" instead of "now"
sum(increase(deployment_restarts_total{app="medbook"}[1d]))
```

Grafana on top: one **variable** per (`datasource`, `namespace`, `app`), a row of RED panels, a row of USE panels, an **annotations** query for deploys (`changeTimeUTC` from your CD datasource — or Loki's annotation data source to draw log events over the graph), and one panel per alert rule so the alert's own query is on the dashboard. `grafana --version`? No: dashboards are **JSON in Git** (`make dashboards` produces them from a generator or you keep hand-written JSON — either way, reviewed, not clicked together at midnight).

## Cloud-native: CloudWatch / Azure Monitor / Cloud Logging — when to use which

| Need | AWS | Azure | GCP |
| :-- | :-- | :-- | :-- |
| Metrics without agents | CloudWatch (namespace `AWS/EC2`, ALB, RDS…) | Azure Monitor metrics per resource | Cloud Monitoring (agent or OTel collector sidecar) |
| Custom app metrics | EMF from structured logs, or `put-metric-data` | `Azure.Monitor.OpenTelemetry` or a custom metric REST call | OTel exporter, or a **logs-based metric** (their superpower) |
| Log query | CloudWatch Logs Insights (`stats pct(latency,99) by service`) | KQL (`requests \| where success == false \| summarize dcount(user_Id) by bin(timestamp,1h)`) | Logs Explorer + KQL-ish `AND jsonPayload.code>=500` |
| APM/traces | X-Ray (or ADOT + Tempo) | **Application Insights** (`dependency`, `request`, `availability` tables) | Cloud Trace + Error Reporting |
| Dashboards | CloudWatch dashboard JSON in Git | Azure dashboard / Workbooks (KQL-backed, good for ops runbooks) | Monitoring dashboards, or Grafana with the Cloud Monitoring datasource |
| Alarm target | SNS → PagerDuty/Slack/lambda | Action group → email/SOAR/logic app | Alert policy → notification channel (pub/sub, webhook, email) |

**The honest recommendation:** if your systems are Kubernetes-heavy or multi-cloud, run **Prometheus + Grafana (or Thanos/Mimir/VictoriaMetrics for HA and long retention) as the primary plane**, and use the cloud-native one for what it does best: service-specific metrics you can't scrape (RDS internals, ALB counters, Lambda concurrency), and platform logs you must keep anyway. That hybrid is the standard in real organisations; the purity arguments are for blogs.

## OpenTelemetry: why it's the answer to “who owns my instrumentation?”

```yaml
# a collector as a Deployment (cluster traffic) or DaemonSet (node traffic): your single knob
receivers:
  otlp: { protocols: { grpc: { endpoint: 0.0.0.0:4317 }, http: { endpoint: 0.0.0.0:4318 } } }
processors:
  memory_limiter: { limit_mib: 512 }
  batch: { timeout: 5s }
  filter/drop_debug: { error_mode: ignore, log_records: [{ severity: DEBUG }] }
  attributes/scrub: { actions: [{ key: http.request.header.authorization, action: delete }] }
exporters:
  otlphttp/tempo: { endpoint: http://tempo.monitoring:4318 }
  prometheusremotewrite: { endpoint: http://prometheus:9090/api/v1/write, external_labels: { cluster: prod } }
  loki: { url: http://loki:3100/loki/api/v1/push }
service:
  pipelines:
    traces:  { receivers: [otlp], processors: [memory_limiter, filter/drop_debug, batch], exporters: [otlphttp/tempo] }
    metrics: { receivers: [otlp], processors: [memory_limiter, batch], exporters: [prometheusremotewrite] }
    logs:    { receivers: [otlp], processors: [memory_limiter, filter/drop_debug, batch], exporters: [loki] }
```

Developers add the OTel SDK once; **you** decide where the data goes by editing a ConfigMap. Vendor swap becomes a redeploy, not a rewrite of 11 services. Bonus points in an interview: mention **context propagation** (W3C `traceparent`) as the thing that makes traces span a queue or a Lambda, and **exemplars** as the thing that makes a Grafana histogram bar clickable into a trace.

## SLOs and error budgets — the discipline that makes alerts trustworthy

```yaml
# slo.yaml — a burn-rate alert in recording rules, so the alert is one expression
groups:
  - name: medbook-slo
    rules:
      - record: service:http_requests:errors5xx_ratio
        expr: |
          sum(rate(http_requests_total{namespace="prod",job="medbook",status=~"5.."}[5m]))
            / sum(rate(http_requests_total{namespace="prod",job="medbook"}[5m]))
      - record: slo:medbook:objective
        expr: 0.001            # 99.9 % availability → 43.8 min/month of “bad” requests
      - alert: MedbookSLOBurnFast
        expr: service:http_requests:errors5xx_ratio > (14.4 * slo:medbook:objective)
        for: 5m
        labels: { severity: page }
        annotations:
          summary: "medbook burning the error budget 14× — page"
          runbook: "https://wiki/medbook#5xx"
          dashboard: "https://grafana/d/medbook?var-app=medbook"
```

**The math you should be able to do on a napkin:** 99.9 % over a 30-day window ≈ 43.8 minutes of unavailability. A **14.4× burn rate** over 5 minutes means you've consumed 2 % of a 30-day budget in 5 minutes → page. A **1× burn rate** over 6 hours means you're exactly on plan → ticket, not page. Multi-window, multi-burn-rate (fast: 5 m + 1 h; slow: 6 h + 3 d) gives you the pages you act on and the tickets you plan around — and it's *Google's SRE book* pattern, so naming it in an interview earns a nod.

**Alert-fatigue rules I'd enforce on day one of any team:** no alert without a runbook link; no alert a human can't act on in 5 minutes; `for:` long enough to survive a pod restart (2–5 min, not 1 min); severity from *user impact*, not from which metric moved; one Slack channel and one escalation path, with a weekly 30-minute **alert review** that deletes or tunes the noise (I've deleted more value in that meeting than in any tuning session).

## Distributed tracing, the small version

```python
# one decorator, and the trace tells you where the time went
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

def book_appointment(req):
    with tracer.start_as_current_span("book_appointment") as span:
        span.set_attribute("patient.id_hash", hash_id(req.patient_id))   # never the raw PII
        with tracer.start_as_current_span("db.insert_appointment"):
            row = repo.insert(req)
        with tracer.start_as_current_span("notify", attributes={"channel": req.channel}):
            notifier.send(row)
```
Read the flame chart left to right: `POST /appointments 2.4 s`, of which `db.insert_appointment 1.9 s`. That single sentence is why traces exist.

## Production scenario — p99 spikes every day at 14:05 for eleven minutes

1. Confirm on the RED row: p99 220 ms → 4.1 s, error ratio unchanged, rate flat. So not traffic, not failure — *slow*.
2. Slice by label: only route `/api/reports`, only pods on node `ip-10-0-21-44`, only AZ `b`. So it's **one node**, not one service — a very different incident.
3. USE row for that node: disk IO latency 45 ms (baseline 2 ms), no CPU or memory pressure.
4. `iostat -x 5` → one device at 100 % `await`; `kubectl get pv -A | grep ip-10-0-21-44` → a `gp2` 100 GB volume, and **CloudWatch `BurstBalance`** on that volume is the smoking gun: it hits zero at 14:05 and refills by 14:16.
5. Cause: a nightly-at-14:00-UTC cron report job (a `CronJob` on that node pool, `ReadWriteOnce`, gp2 with exhausted throughput credits) saturating the shared EBS burst bucket for every pod on the node.
6. Fix: `gp3` (provisioned 3 000 IOPS, no burst bucket), give the report job its own node pool with taint/toleration, and move it off business hours.
7. Prevent: `volume_queue_length` and `BurstBalance` alarms per volume class, a StorageClass that never produces `gp2` again (`volumeType: gp3` + `fsType`), and a panel that breaks p99 **by node** so this class of problem is one click, not one hour.

The moral: “app is slow” was a **storage tier** problem. No log line ever mentioned it. Metrics-by-node answered it in nine minutes.

## Common mistakes

::: checklist
- [ ] **Dashboards with 20 panels and no questions.** A dashboard is an answer to a question; write the question in the panel title (“are users seeing 5xx?”).
- [ ] **Alerting on causes instead of symptoms.** Page on user-facing SLOs (error ratio, p99); *ticket* on causes (CPU, memory, disk).
- [ ] **Cardinality suicide:** putting `pod_name` or `user_id` on a high-rate counter. Prometheus OOMs, Thanos compactions fall behind, and you delete the metric — now nobody can debug.
- [ ] **`rate()` over a 1-minute window** with a 30 s scrape → gaps; use ≥4× the scrape interval (5 m is the default for a reason).
- [ ] **No `for:` on alerts** → every restart pages you. And no `keep_firing_for` → flapping alerts end when data blinks.
- [ ] **1×1 sampling of traces** in a busy service → the one slow request you need is not in your trace store. Head-based sampling plus tail-based “keep all errors and everything > 1 s” is the answer.
- [ ] **Logs without `trace_id`** → you have two disconnected datasets and a manual grep across them during an outage.
- [ ] **Logging PII.** Payment tokens, patient IDs, JWTs in a log line is a breach, and the retention policy makes it permanent. Scrub at the collector, not “in the app, eventually”.
- [ ] **One dashboard for everything.** Service owners own their RED panels; the platform team owns USE and the cluster.
- [ ] **No drill.** An alert nobody has tested at 2 a.m. is a hypothesis. Run a game day: kill a pod mid-request, watch whether the pages and the dashboards do what you claim.
:::

## Best practices

::: grid2
**Structured logs, one JSON shape across services.** `{"ts","lvl","msg","trace_id","span_id","route","status","dur_ms","app","env"}` — then Loki/CloudWatch can extract and Grafana can correlate, and no regex archaeology during an incident.
**Redact at the collector.** The scrub processor is one place; the alternative is 11 codebases and a promise.
**Retention tiers**: 15 min of *everything* (hot logs), 30 days of sampled traces, 13 months of metrics at 5 m resolution with downsampling. Cost is a design input.
**SLOs per service in Git** so an error budget is a *reviewable object* — the same PR flow that reviews code reviews the objective, and a missed SLO becomes a work item, not a blame.
:::

## Interview answer

::: callout aha “How do you monitor a service you just took over?”
“First I make the four golden signals visible per service — rate, errors, duration histogram, saturation — plus USE on the resources it depends on, because that's where most ‘app is slow’ incidents actually are. Then I wire the three signals together so a page is answerable in one path: alert → dashboard panel with a variable for the affected service → exemplar on the histogram bar → trace → `trace_id` in Loki. On Kubernetes I run Prometheus with ServiceMonitors, Alertmanager with an inhibit rule so a node-down alert silences the twelve pod alerts under it, and Grafana dashboards as JSON in Git; on AWS I keep CloudWatch for what only it sees — ALB 5xx and target-response-time, RDS FreeableMemory and connection counts, EBS `BurstBalance` — and ship app logs to CloudWatch Logs with 30-day retention and an Insights query saved for the common shapes. Alerts are burn-rate based: 14.4× over 5 minutes pages, 1× over 6 hours is a ticket, and every alert carries a runbook link and a dashboard link; anything that fires twice without action gets tuned or deleted at the weekly alert review. Instrumentation is OpenTelemetry through a collector, so I control sampling, scrubbing and the destination in one place, and if the team later changes vendor, only the exporter changes. Then I'd add the boring part that makes all of it real: an SLO with an error budget and one game day a quarter, because an untested alert is just a log line with a hook.”
:::

::: grid2
**Follow-up: “Metrics vs logs?”** → metrics are aggregated, cheap and alertable, and hide detail; logs are the detail and are expensive, and can't be alerted on at scale without metrics underneath.
**Follow-up: “How do you find a latency cause across services?”** → traces with propagated context, filtered by slow or errored, with the flame chart pointing at one span; then logs by `trace_id` at that node.
**Follow-up: “What's in a good alert?”** → one symptom, `for:` to survive blips, severity from user impact, a runbook URL, and an owner. If I can't write the first runbook line, it isn't an alert yet.
**Follow-up: “Your Prometheus is down — what now?”** → `kubectl -n monitoring logs prometheus-…`, check the PVC (a full TSDB WAL is the usual), look at scrape-target health (`prometheus_target_scrape_pool_exceeded_target_limit`), and for HA confirm Thanos sidecars and receive; meanwhile, cloud-native metrics + `kubectl top` + `stern`/`kubectl logs` for logs, because those three cover the next hour of most incidents.
:::

## Virtual lab — instrument, graph and alert (simulated terminal)

::: lab obs-1
:::

## Commands to remember

::: grid2
```bash
# Prometheus as a debug API, not just a UI
curl -sG 'http://prometheus:9090/api/v1/query' \
  --data-urlencode 'query=sum by (app)(rate(http_requests_total{namespace="prod"}[5m]))' | jq
curl -s http://prometheus:9090/api/v1/targets | jq '[.data.activeTargets[] | select(.health!="up") | .labels.pod]'
wget -qO- http://localhost:9090/debug/metrics | grep prometheus_tsdb_head
promtool check rules rules/ && promtool test rules tests.yaml
```
```bash
# Loki as a log database, queried like metrics
logcli query '{app="medbook",namespace="prod"} |= "error" | json | __error__=""'
logcli --limit 40 query '{ns="prod"} |~ `(?i)timeout|deadline` |= "trace_id=\"abc123\""'
# cloud equivalents, when Prometheus isn't in the picture
aws logs describe-log-groups --region eu-west-1 | jq '.logGroups[] | {name:.logGroupName, days:.retentionInDays}'
az monitor log-analytics query --workspace $LAID --analytics-query 'requests | where duration > 2000 | count'
gcloud logging metrics create p99_over_2s --filter='httpRequest.latency>=2s' --value-field=latency
```
:::

::: callout note WHAT I MUST REMEMBER
1. Pull metrics, sampled traces, structured logs — three datasets, joined by `trace_id` and by labels.
2. `histogram_quantile` needs `by (le)`, and alerting on an average is how you miss the incident.
3. Symptoms page, causes ticket; every alert has a runbook and an owner.
4. Cardinality and retention are the two budget lines that end observability projects.
:::

::: revision REVISION — 5 minutes
**Concepts:** metrics/logs/traces/profiling; RED and USE; counter vs gauge vs histogram; `rate`/`irate`/`increase`; quantiles and bucket choice; ServiceMonitor vs scrape config; exemplars; OTel SDK vs collector and where sampling happens; SLO/SLI/error budget and burn-rate windows; Alertmanager grouping, inhibition, silences; log levels and cardinality; retention tiers; Grafana variables and annotations; push-based cloud metrics (EMF, App Insights, logs-based metrics).
**Architecture to mention:** app (OTel SDK + `/metrics`) → collector (batch, scrub, sample) → Prometheus/Thanos + Loki + Tempo → Grafana; alerts → Alertmanager → PagerDuty/Slack; deploy markers as annotations; and one runbook URL per alert.
**Common mistakes:** no `for:` on alerts · page on every red panel · logs in plain text · no labels for the deploy · one dashboard for four teams · “we'll instrument later”.
**Troubleshooting checklist:** is it *one* endpoint/pod/AZ? (slice by labels) → RED row for app, USE row for node/DB → did a deploy marker line up? → exemplar → trace → logs by trace id → and if the data is missing: is the target up (`/targets`), is the metric scraped (`/query` on `up{}`), is the retention/collection side healthy?
:::

Next: [Multicloud patterns →](p1-multicloud.html)
