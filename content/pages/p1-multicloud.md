LEAD: Multicloud is rarely “the same app in two clouds for fun”. It is usually an acquisition, a data-residency rule, a vendor negotiation, a GPU supply problem, or a platform team's risk posture. Whatever the reason, the engineering answer is always the same: **one artefact, one control plane, portable primitives, and a deliberate list of the things you will not port.**

## Why organisations end up with two clouds (the real reasons)

| Driver | What it means for you as the engineer |
| :-- | :-- |
| M&A / team autonomy | Two stacks, two account structures, one product. Your job: converge the *delivery path*, not the infrastructure. |
| Data residency / regulation | “EU data in the EU”, a government enclave, a payment-scope network. Drives region choice first, cloud second. |
| Service-specific advantage | GCP for BigQuery/Vertex, Azure for AD-bound .NET, AWS for breadth (WAF, Outposts, Marketplace). |
| Negotiating leverage and concentration risk | A credible exit path changes contract terms. It also caps blast radius: an account-wide quota incident, a region outage, or a bad policy push. |
| Capacity / price arbitrage | GPUs in one region, spot in another, egress cheaper there. Real teams do this and it must be automated or it becomes a rumour. |
| “We tried and it stayed” | Legacy. Plan for **migration out of** it as much as into the target. |

::: callout note THE UNCOMFORTABLE SENTENCE YOU SHOULD BE ABLE TO SAY
Active-active across clouds is usually a bad trade: cross-cloud latency is 30–90 ms between regions, egress is $0.09–$0.12/GB, and you must reconcile two IAM models, two DNS failover semantics and two sets of managed-database replication. **One cloud for the stateful core, another for a bounded function** — that's the version that survives contact with a finance team. This course builds that version.
:::

## The portability ladder — pick your rung before you start

| Rung | What's portable | Cost |
| :-- | :-- | :-- |
| 1 · Container + config | The image and env contract work everywhere; the platform differs | The honest default. Cheap, and covers 90 % of the value. |
| 2 · IaC modules with per-cloud wrappers | `module/app/{aws,azure,gcp}/main.tf` + a shared interface | One module set, three backends; the pattern used in Project 09. |
| 3 · Kubernetes API as the interface | Deployments, Services, ingress/Gateway, Helm charts | Requires a managed cluster on both sides; identity/registry/LB still per-cloud. |
| 4 · A distribution layer | KubeVela/Crossplane/Backstage, Flux + a GitOps cluster hub | Platform-team territory. Only if you have several services and a real need. |
| 5 · Abstraction SDKs (the ones people regret) | Wrapping S3 behind a library, or Pulumi CDK-for-Cloud | You inherit the least-common-denominator tax and the debugging cost. Reach for it only for storage/queues. |

## What actually happens when you run in two clouds

**Identity — nothing is common, so model it first.**
AWS roles ↔ Azure managed identities ↔ GCP service accounts, and the one genuinely shared idea is *workload identity federation*: a CI job or a pod exchanges an OIDC token for short-lived cloud credentials, so no keys live anywhere. Practical rule: every workload gets a named identity per cloud, with the *same* semantic name (`medbook-ci`, `medbook-app`) so a policy diff between clouds is meaningful. Document the mapping in one table in the repo; it saves more time than any abstraction layer.

**Networking — the CIDR conversation is a one-time decision with permanent consequences.**
Overlapping ranges across clouds make peering/VNet-connections and hybrid routing impossible; 10/8 wastes nothing and is a cliché for a reason (`10.40.0.0/16` AWS, `10.50.0.0/16` Azure, `10.60.0.0/16` GCP leaves room for acquisitions). Then three hard differences to respect: AWS subnets are zonal, GCP subnets are regional inside a global VPC, Azure NSGs attach to subnets *or* NICs; egress must be bought (NAT/VPC-NAT/Azure NAT) or routed (private endpoints/Private Link/ Private Google Access) per cloud; and cross-cloud paths need a transit choice — VPN to a hub, a private interconnect (AWS Direct Connect + Azure ExpressRoute via a provider, or Partner Interconnect on GCP), or plain public internet with an IPsec tunnel and a health-checked static route.

**Data — the only part that should never be “multi-active” by accident.**
- Object storage: S3 ↔ Blob ↔ GCS have different consistency and listing semantics. All three are read-after-write for new objects today; deletes and list-after-delete still differ enough that a batch job designed on S3 listing 100 k keys can behave differently on Blob (prefix enumeration is per-container, and the “folder” idea is more misleading there).
- Databases: one primary. Replicas that lag, or an application writing to two masters, is how “high availability” becomes “two versions of the truth”. If you must keep data in two clouds, keep a *derived* copy (analytics, cache, search index) and treat it as eventually consistent by design.
- Secrets/config: replicate *references*, not values. AWS Secrets Manager/Parameter Store, Azure Key Vault, GCP Secret Manager, each read by the local workload with its local identity — and an external-secrets/Infisical/vault sidecar only if you truly need one source of truth.

**Observability — deliberately cloud-agnostic, and this is where multicloud pays off.**
One OTel collector config, exported to *your* Prometheus/Loki/Tempo (hosted on one cloud or a vendor), means one Grafana for both clouds and one alert vocabulary. Cloud-native metrics stay where they are for platform detail; SLOs and pages come from the shared plane. This is the single strongest argument for OpenTelemetry in a multicloud shop — and note the boring corollary: your log shipping now has a cross-cloud network path, so plan egress and a local buffer (a disk-backed queue in the DaemonSet).

**CI/CD — one pipeline, three deploy steps.** GitHub Actions builds, scans, signs and pushes **one** image; then `deploy-aws`, `deploy-azure` and `deploy-gcp` are small jobs that each take the digest and run the local CLI (`helm upgrade`, `az containerapp update`, `gcloud run deploy --image=…@sha256:…`). Never let a cloud-specific build happen per cloud: two builds = two truths.

## The failure modes, and the one that is always the answer

| Symptom | Most likely multicloud cause |
| :-- | :-- |
| “Works on AWS, 502s on Azure” | App Gateway probes `/health` while the ALB probed `/actuator/health` — health paths, context roots, and `trailingSlash` semantics differ per ingress controller. |
| Pods pending only in AKS | Azure CNI + a small subnet → no IPs left for pods. The AWS equivalent is IP exhaustion; GKE doesn't have this failure mode at all. |
| Image pull fails in one cloud only | ACR admin user disabled without a pull identity, ECR token cached in a long-lived runner, or Artifact Registry key file expired in CI. |
| Terraform destroys the wrong environment | Two clouds in one state + a mis-set `ARM_SUBSCRIPTION_ID`/`GOOGLE_PROJECT` env. Keep one state and one env per cloud, and set `prevent_destroy`. |
| DNS fails over to the standby and stays there | Route 53 health checks can't reach the Azure endpoint (firewall didn't open the health-checker source ranges); health check down → record removed → someone re-adds it manually and it never fails back. |
| “Two versions of a config value” | Hand-edited key vaults on both sides. One config source in Git, promoted to both, plus a scheduled diff job. |
| Latency fine, cost terrible | Egress. Cross-cloud request chatty by design (an app in Azure calling a service in AWS for every row) — fix with a read replica, batching, or moving the call site, not with a bigger NAT. |

## Three architectures, chosen by what the business actually needs

::: cols
::: col
**1 · Standby / DR (the most defensible)**
Primary everything in AWS; a *cold* or *warm* copy in Azure: IaC module with different provider block, images in both registries (replication, not rebuild), data via nightly snapshots + log shipping to a *read-only* replica, DNS with health-checked failover and a 30-second TTL.
**RTO hours, RPO minutes-to-hours** depending on the data path. Test it quarterly, on purpose.
:::
::: col
**2 · Bounded-function split**
Stateful core in AWS; one clearly bounded capability in the other cloud because it's genuinely better — GCP for the ML/analytics slice (BigQuery + Vertex), Azure for a .NET/AD-bound partner integration, or S3 as the archive and Blob for the partner-facing share.
Latency is *designed around*: async + queue + eventual, never a synchronous call per request. This is what Projects 09 and 10 do.
:::
::: col
**3 · Regulated partition**
Workload A must live in an EU enclave; workload B is global; a thin API gateway in front routes by tenant, and both clouds share one GitOps repo, one policy set and one observability plane. Security/compliance teams approve this shape because the boundary is *architectural*, not a tagging convention.
:::

## Production scenario — “the Azure copy was silently stale for 11 days”

Project 09's DR drill: `az group deployment` had run fine, the app was deployed, and the failover test found **schema drift**: migration 47 and 48 had never been applied to the Azure copy because the migration step lived in a *Jenkins job with a hardcoded AWS-only host*.

1. Detection was luck: someone compared `select max(version) from flyway_schema_history` on both sides during the drill. It should have been an alarm: a scheduled job that diffs schema version between environments and pages on drift.
2. Root cause class: **two deploy paths, not one artefact twice.** The image was promoted by digest, but the *migration* was executed by a script that only knew one endpoint.
3. Fix: migrations run in the same pipeline, once per environment, from the image (`flyway migrate -url=$DB_URL` with the URL from the local secret store), and the pipeline fails if the target's schema version ≠ the artefact's expected version.
4. Prevention: a `portability.md` in the repo — a two-column list of “AWS-specific things we assume” (instance roles, Secrets Manager paths, CloudWatch agent, ALB health check paths, EFS). Anything on that list must have a named Azure equivalent or a written reason. Eleven days of stale standby became a documentation file.

The generalisable lesson: **the failure mode of multicloud isn't an outage; it's the quiet divergence you only discover in a drill.** So schedule drills, and diff everything you can: schema, config, policy versions, IAM grants, dashboard contents, and retention settings.

## Common mistakes

::: checklist
- [ ] Starting with an abstraction layer instead of a portability decision. Decide the *rung* first; then write code.
- [ ] Two pipelines that each build the image (from slightly different Dockerfiles, in their defence, honestly). One build, N deploys.
- [ ] Replicating writes to two databases and calling it HA.
- [ ] Copy-pasting secrets between clouds instead of referencing local stores.
- [ ] Identical Terraform with `count = var.provider == "aws" ? 1 : 0` all over it — conditional-module soup nobody can read. Use thin per-cloud roots with a shared module interface.
- [ ] Forgetting that **quotas, regions and support plans are per cloud**, and being surprised that eu-west-1 has the service but westeurope doesn't (or costs 40 % more).
- [ ] No cross-cloud network path design, then discovering it during a DR test at 22:00 on a Saturday.
- [ ] One cost report with two currencies and three billing periods. Two reports, one normalised view per service tag, monthly.
- [ ] “Multicloud” as a résumé word: if you can't name the workload that justifies it and the person who pays for it, say *portable by design* instead.
:::

## Best practices

::: grid2
**Write the exit plan, not the lock-in story.** If a service can't be moved in a quarter, it shouldn't be load-bearing on day one. Document for each dependency how it's replaced (managed Postgres → managed Postgres, Easy DB → a migration runbook).
**Portable core, cloud-native edges.** Container + HTTP + object storage + queues are portable; Lambda/DynamoDB/Event Grid/App Service plans are accelerators you use *where they pay*. The line is a written decision, not a vibe.
**One policy set, generated twice.** OPA/Kyverno for the clusters, an SCP/Azure Policy pair generated from a common YAML for the accounts, and one Trivy/Checkov gate for both. Same rules, two enforcers.
**Drills as a calendar item.** Quarterly: fail over on purpose, restore a backup into the other cloud, and time it. A DR plan that has never been exercised is an essay.
:::

## Interview answer

::: callout aha “Have you worked with more than one cloud, and how would you design for it?”
“Yes — I built the same service's delivery path on AWS and Azure, with a GCP analytics slice, so I've lived the differences rather than only read them. My default design is: one artefact and one control plane. The image is built, scanned, signed and pushed once by a single CI job; every environment consumes it by digest, and the only per-cloud differences are identity, ingress, the registry and the secret store — which I express as small per-cloud Terraform roots over shared modules, not as conditionals sprinkled through one stack. Data stays single-writer in the primary cloud, with a read-only or derived copy elsewhere; that decision is explicit in the repo's `portability.md` along with the AWS-specific assumptions each service is allowed to make. Observability is deliberately cloud-agnostic — OpenTelemetry into Prometheus/Loki/Tempo and one Grafana — because two dashboards in two clouds is how outages become archaeology. And I treat failover as a test: health-checked DNS with a 30-second TTL, a documented schema-drift diff between environments, and a quarterly drill with measured RTO/RPO, because my most instructive incident in that setup was a standby that had been silently stale for eleven days from a migration script that only knew one endpoint. So I'd ask you first *why* two clouds — regulation, an acquisition, a GPU constraint — because the reason decides the architecture, and 'avoid vendor lock-in' by itself usually buys complexity.”
:::

::: grid2
**Follow-up: “How do you handle IAM across clouds?”** → semantic names per workload, short-lived credentials via OIDC federation everywhere, one table mapping role → permissions → cloud, and a scheduled lint that flags any grant that drifted from the module's intent.
**Follow-up: “What about cost?”** → tag-first with a policy that blocks untagged creates, per-cloud reports normalised by service tag monthly, and egress as a named line item in the design review, not an apology later.
**Follow-up: “Would you use Crossplane/Terragrunt/Backstage?”** → Crossplane when I need cloud resources driven from the Kubernetes API for many teams; Terragrunt for state/module hygiene across environments; Backstage once there are >10 services and onboarding is the bottleneck. Not as a first move.
**Follow-up: “Failover: DNS or something better?”** → DNS is the floor (30–60 s TTL, health-checked, and *test the fallback to the fallback*); for HTTP, a global load balancer or a CDN with origin-health + a session-drain plan; for stateful systems, you need a data path, not a switch.
:::

## Virtual lab — keep two clouds consistent (simulated terminal)

::: lab multi-1
:::

## Commands to remember

::: grid2
```bash
# same shape, three dialects — pin them in your notes until they're boring
aws ec2 describe-instances --filters Name=tag:App,Values=medbook --query 'Reservations[].Instances[].Tags'
az vm list -d --query '[].{n:name,zone:availabilityZones}' -o table
gcloud compute instances list --filter="labels.app=medbook" --format='table(name,zone)'
```
```bash
# the consistency checks I'd script and run nightly
terraform -chdir=envs/aws plan -detailed-exitcode ; terraform -chdir=envs/azure plan -detailed-exitcode
helm template app ./chart -f values/aws.yaml | kubeconform -strict -summary -
helm template app ./chart -f values/azure.yaml | tr '[:upper:]' '[:lower:]' > /tmp/az.yaml  # diff intent
psql "$DB_AWS"  -c 'select max(version) from flyway_schema_history'
psql "$DB_AZURE" -c 'select max(version) from flyway_schema_history'
```
:::

::: callout note WHAT I MUST REMEMBER
1. Pick the rung on the portability ladder on purpose, and write it down.
2. One artefact, one pipeline; per-cloud roots over shared modules; no conditionals in place of design.
3. Single-writer data, local identities, cloud-agnostic signals.
4. Drift is the enemy, and only scheduled diffs and drills find it.
:::

::: revision REVISION — 5 minutes
**Concepts:** reasons vs résumé words · portability ladder · account/subscription/project isolation carried across clouds · CIDR planning for peering and acquisitions · egress economics and the private-endpoint trio (gateway endpoints, Private Link, Private Google Access) · health-checked DNS failover with TTL and a fallback plan · schema/config/policy drift diffing · RTO/RPO and why the drill defines both · one-artefact-many-deploys · external secrets vs replicated values · cross-cloud interconnect vs VPN.
**Architecture to remember (Project 09 shape):** users → Route 53/Traffic Manager with health checks → AWS three-tier (ALB → EKS → RDS) *primary*; Azure (App Gateway → AKS → Flexible Server) *standby with read-only replica and its own Key Vault/ACR*; both fed by one GitHub Actions pipeline publishing one image to both registries; both watched by one Grafana; both deployed from one Helm chart with three values files.
**Common mistakes:** active-active writes · two build paths · abstraction-first · copying secrets · one state for two clouds · no written portability decisions · no egress line in the design.
**Troubleshooting checklist:** which cloud is answering (DNS, TTL, resolver) → is the artefact the same digest → is identity valid in *this* cloud → is the network path open for the *health checker* specifically → is data/schema in sync → which line of which bill moved → when did we last drill, and did anyone time it?
:::

Next: [Projects — the eleven builds →](projects.html)
