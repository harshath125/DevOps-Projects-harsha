LEAD: CoverDesk is the Azure project, and it is also the DevSecOps project: a Node insurance platform with a quoting campaign that multiplies traffic 8×, an approval workflow where a claim must not be self-approved, and a monthly board report that must be provably complete. You build the delivery path on Azure DevOps with environments and approvals, deploy to AKS, and put the security gates where they can actually stop something.

::: figure
assets/img/azure-devops-loop.png
The loop you are about to build: Developer → Azure Repos → Azure Pipelines → AKS → Application Insights → Azure Monitor → back to the developer. Every arrow is an artefact you will create (a commit, a YAML stage, a manifest, a telemetry line, an alert) — which is why this diagram belongs on the wall of an Azure shop.
:::

# PART A — GET THE APPLICATION

## Overview
A pnpm monorepo with three deployables: `gateway` (Fastify proxy with JWT, rate limits and headers), `policy-service` (quote → buy → cancel, with a pricing table and pro-rata maths) and `claims-service` (a `LODGED → ASSESSED → APPROVED|DECLINED → SETTLED` state machine with an append-only event log and fraud flags). MongoDB 7 underneath, Redis for limits, `/metrics` and `/internal/ready` on each.

## Business scenario
Insurance is a documents-and-deadlines business. A quote expires in 24 h, a claim without a decision in 10 working days is a regulatory complaint, and the monthly reserve report is signed. So: TTL indexes are *business logic*, a state transition cannot be skipped, and the nightly `reserve` job must be provably complete — which means a count reconciliation and an alarm, not a hope.

## Tech stack (received)
| Layer | Inherited | What it forces you to operate |
| :-- | :-- | :-- |
| Node 20 + Fastify 4 | `PORT`, `TERM`-based drain, `unhandledRejection` → exit 1 | K8s `terminationGracePeriodSeconds` ≥ 25 s; exit-1-on-rejection is a *feature* (a restart beats a wedged process) |
| MongoDB 7 | Cosmos/Mongo choices: we use self-managed on AKS with Atlas as the honest alternative, decided in an ADR | backup/restore, index creation as a deploy step, TTL caveat on replicasets |
| Redis 7 (Azure Cache) | per-instance in-memory limiter documented as per-instance | decide fall-open vs distributed; a `429 storm` on scale-in is a real incident |
| Two services + gateway | independent builds, one repo | per-service images from a monorepo (`pnpm deploy --filter`), one release train, one `versions.yaml` |

## Architecture
::: flow
customers/agents → Front Door/WAF → App Gateway → ingress-nginx on AKS → gateway → policy-service / claims-service → MongoDB (replica set) + Redis ; events → reserve job (CronJob) → accounting summary rows → monthly report endpoint
:::
Two gateways in front (Front Door + App Gateway) is not decoration in Azure: Front Door gives you global WAF + caching for the quoting campaign, App Gateway gives you the private ingress and a path to Key Vault-backed TLS. If that feels heavy, write the ADR that says “App Gateway only, Front Door when we open a second region” — a *chosen* simplification reads far better than an accidental one.

## What the developer will build
Two state machines with `409` on illegal transitions, pricing as a data file, pro-rata short-rate maths with a case table in tests, an idempotent re-runnable reserve job keyed by `(policyId, runDate)`, `X-Request-Id` idempotency on policy creation, fraud rules as a pure module, per-package build graph so CI can run `pnpm --filter policy-service test`, and `docs/OPERATIONS.md` with the index-creation commands and what to do when Mongo is slow versus down.

## Copyable Developer Agent Prompt
::: prompt coverdesk-insurance
:::

## Developer handoff
```bash
git clone https://github.com/harshath125/coverdesk.git && cd coverdesk
pnpm install --frozen-lockfile           # the *frozen* flag is the gate; ask for the lockfile if it's missing
pnpm -r test && pnpm -r lint
grep -RIn "process.exit(1)" packages/*/src/*.ts | head       # crash-on-bug design, in the wrong place?
pnpm --filter policy-service test          # exactly what each pipeline job will run
```

# PART B — THE DEVOPS JOURNEY

## Clone, inspect, plan the images
```bash
find . -name package.json -not -path "*/node_modules/*" | sed 's|/package.json||'
cat pnpm-workspace.yaml ; du -sh node_modules           # how big is the pruned prod install?
pnpm deploy --filter=policy-service --prod /tmp/policy-prod && du -sh /tmp/policy-prod/node_modules
```
That `pnpm deploy` experiment *is* the Dockerfile design decision: copy the pruned prod directory into the runtime image, don't `npm ci` the whole workspace (which ships the claims service's dependencies inside the policy image — an image-size and CVE-triage problem you will hear about in the security review).

## Dockerize (one pattern, three images)
```dockerfile
FROM node:20.12.2-alpine3.19 AS build
WORKDIR /repo
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY packages/policy-service/package.json packages/policy-service/
RUN corepack enable && pnpm fetch --prod=false
COPY . .
RUN pnpm install --frozen-lockfile --offline && pnpm --filter policy-service build \
 && pnpm deploy --filter=policy-service --prod /prod/policy-service

FROM node:20.12.2-alpine3.19
RUN addgroup -g 10001 app && adduser -u 10001 -G app -S appuser
WORKDIR /app
COPY --from=build --chown=10001:10001 /prod/policy-service ./
ENV NODE_ENV=production NODE_OPTIONS=--max-old-space-size=384 PORT=8081 TERM=linux
USER 10001
EXPOSE 8081
HEALTHCHECK --interval=10s --timeout=2s --start-period=12s --retries=5 \
  CMD wget -qO- http://127.0.0.1:8081/internal/ready || exit 1
ENTRYPOINT ["node","dist/main.js"]
```
`pnpm fetch` + `--offline` is the trick that makes a monorepo build fast and reproducible (it populates the store from the lockfile before source is copied, so a code change doesn't re-resolve 900 packages). `NODE_OPTIONS=--max-old-space-size` must be **below** the container limit — with 512 Mi and a 480 heap you get no headroom for buffers and the OOM killer wins; 384 is the deliberate number, and it belongs in a comment in the values file so nobody “optimises” it later.

## Registry, scan, sign — the Azure way
```bash
az acr create -g rg-coverdesk -n coverdeskacr --sku Standard
az acr update -n coverdeskacr --policies-azure-ad true --policies-trust-policy-enabled true
az acr build -r coverdeskacr -t coverdeskacr.azurecr.io/policy-service:$(git rev-parse --short HEAD) \
             -f Dockerfile --platform linux/amd64 .
az acr check-health -n coverdeskacr -o table      # the “is my ACR even usable?” command nobody knows
```
`--policies-trust-policy-enabled` (ACR Tasks + quarantine on `az acr import`) is Azure's native answer to “can an unscanned image be pushed?”; it's weaker than an admission controller, so keep Kyverno in AKS as the enforcing layer and treat the trust policy as the *first* no.

## Terraform for the Azure landing zone
```
envs/prod/main.tf  →  module "hub" (vnet, 3 subnets: nodes/pods, ingress, data + private endpoints)
                     module "aks" (2 × Standard_D4ds_v5 node pool, azure CNI with --max-pods 30,
                                   --enable-oidc-issuer, --enable-workload-identity, --os-sku AzureLinux,
                                   --auto-upgrade-channel patch, --upgrade-settings surge,
                                   --attach-acr coverdeskacr, --network-policy azure, --dns-profile private-zone)
                     module "data" (cosmosdb/mongo-compatible OR a VM-based replica set + a written risk note,
                                    redis: Azure Cache Premium with zones, keyvault with RBAC + purge protection)
                     module "observability" (law, appinsights, diagnostic settings on AKS/AGW/keyvault,
                                             alert processing rule for the on-call PowerBi email... no: action group → PagerDuty)
                     module "edge"   (app gateway v2 with a Key Vault-backed cert + managed identity, waf policy linked,
                                      frontdoor standard with caching rules for /static, private endpoints where needed)
```
Three Azure-specific traps worth naming in the plan review: (1) **azure CNI + a small subnet**: `--max-pods 30` on a `/25` node subnet means 14 nodes before you run out of IPs, and “pending: 0/3 nodes are available: InvalidSubnet” is a miserable 45 minutes; (2) **App Gateway v2 needs its own dedicated subnet with no other service and a `/24`** — a `/28` “for tidiness” gives you a `SubnetNotBigEnough` error; (3) **AKS `--os-sku AzureLinux`** is the default for new clusters but many tutorials bake Ubuntu assumptions into node-identity labels and `kubectl debug` images, so decide deliberately.

## Networking
`sg-nodes` allows what the control plane needs (and Azure adds that rule for you — check it wasn't deleted by a “hardening” PR); `ingress subnet` gets the WAF policy in `Prevention` mode with `Microsoft_BuiltinManager` and **exclusion for the `/webhooks/**` path that a partner signs** (a signed body + a rewriting rule is a classic false positive, so write the exclusion with a ticket number); `data subnet` with a private endpoint for Redis and Cosmos, and no `0.0.0.0/0` at any layer; DNS: a private zone for `*.internal.coverdesk.io` plus the public A record for `www`; TLS from Key Vault with a managed identity on the App Gateway (so renewal is `az keyvault certificate-contact` + a Let's Encrypt or ACM-mirrored automation, not a human).

## Database and the backup you can prove
If you self-host Mongo on AKS (the teaching choice): a 3-node replica set on `ReadWriteOnce` SSDs with `podAntiAffinity`, `mongod` with `--replSet rs0 --keyFile`, a `MongoDBBackup` CronJob doing `mongodump --oplog` to a **separate** storage account with immutability + a 7-day legal-hold on the report bucket, `az storage account blob-service-properties update --enable-immutability` for the retention the auditors ask about, and — the part that makes it real — a **restore drill in a scratch RG, timed and logged, once a month**. If you use Atlas or Cosmos: the drill is the same and the config is `PITR`. Either way, a restore that has never been run is a file you hope is a backup.

## Deploy: AKS manifests + Helm, and the identity story
```bash
az aks get-credentials -g rg-coverdesk -n aks-coverdesk --admin=false --overwrite-existing
helm upgrade --install coverdesk ./chart -n coverdesk-prod --values values/azure-prod.yaml \
  --set-string policy.image=coverdeskacr.azurecr.io/policy-service@sha256:$DIGEST \
  --atomic --wait-for-jobs --timeout 7m
kubectl -n coverdesk-prod rollout status deploy/policy-service
```
```yaml
# values/azure-prod.yaml — the two Azure-specific lines a reviewer looks for
workloadIdentity: { enabled: true, clientID: 00000000-… }   # the user-assigned MI, not the node's
serviceAccount: { annotations: { "azure.workload.identity/use": "true" } }
```
`--attach-acr` grants the *node* identity pull rights; Workload Identity grants the *pod* its own identity for Key Vault. Mixing those two up is the #1 Azure AKS security review comment. Note also the ingress annotation `appgw.ingress.kubernetes.io/ssl-redirect: "true"` **or** the App Gateway listener doing TLS — pick one and delete the other, because doing both creates a redirect loop that only appears for one HTTP method (HEAD) and wastes an afternoon.

## CI/CD on Azure DevOps (the pipeline that matches the diagram)
```yaml
# azure-pipelines.yml — multi-stage YAML with templates and environments
trigger: { branches: { include: [main] }, paths: { exclude: ['docs/**','**/*.md'] } }
pool: { vmImage: ubuntu-22.04 }
variables: { ACR: coverdeskacr.azurecr.io, CLUSTER: aks-coverdesk }
stages:
- stage: Build
  jobs:
  - ${{ each svc in [ 'gateway', 'policy-service', 'claims-service' ] }}:
    - job: ${{ svc }}
      steps:
      - script: pnpm install --frozen-lockfile && pnpm --filter ${{ svc }} test && pnpm --filter ${{ svc }} lint
      - task: Docker@2
        inputs: { command: buildAndPush, repository: 'coverdesk/${{ svc }}', dockerfile: Dockerfile,
                  buildContext: ., containerRegistry: svc-acr-push,
                  tags: ['${{ svc }}:$(Build.SourceVersion)'] }
      - script: trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed $ACR/coverdesk/${{ svc }}:$(Build.SourceVersion)
      - script: syft $ACR/coverdesk/${{ svc }}:$(Build.SourceVersion) -o cyclonedx-json > sbom-${{ svc }}.json
      - publish: sbom-${{ svc }}.json
        artifact: sbom-${{ svc }}
- stage: Security
  dependsOn: Build
  jobs:
  - job: Scans
    steps:
    - script: |
        gitleaks detect --source $(Build.SourcesDirectory) --redact --exit-code 1
        checkov -d infra/ --soft-fail-on LOW --framework terraform -o junit.xml > /dev/null
        npx madge --circular packages/ --extensions ts
      # + Dependabot/Renovate PRs, and the OWASP ZAP API scan runs against staging later
- stage: DeployStaging
  dependsOn: Security
    # uses service connection sc-aks-staging + environment env-staging (no approval)
  jobs:
  - deployment: staging
    environment: env-staging
    strategy:
      runOnce:
        deploy:
          steps:
          - checkout: self
          - task: HelmDeploy@0
            inputs: { command: upgrade, chartName: ./chart, namespace: coverdesk-staging,
                      valueFile: values/azure-staging.yaml, arguments: '--atomic --wait-for-jobs' }
          - script: ./scripts/smoke.sh https://staging.coverdesk.io && ./scripts/zap-baseline.sh
- stage: DeployProd
  dependsOn: DeployStaging
  condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))
  jobs:
  - deployment: prod
    environment: env-prod        # ← approvals, VM tags for deploy windows, and *separate* variable groups live here
    strategy:
      runOnce:
        deploy:
          preDeploy:
            steps: [ script: 'az aks show -n $(CLUSTER) -g rg-coverdesk --query addonProfiles.ingressApplicationGateway -o json' ]
          deploy:
            steps:
            - task: HelmDeploy@0
              inputs: { command: upgrade, chartName: ./chart, namespace: coverdesk-prod,
                        valueFile: values/azure-prod.yaml, arguments: '--atomic --wait-for-jobs --timeout 10m' }
            - script: |
                ./scripts/promote-tags.sh coverdesk/policy-service $(Build.SourceVersion) prod-$(Build.BuildNumber)
                az monitor metrics list --resource $APPINSIGHTS --metric requestsPerSecond --interval PT5M
          routeTraffic:
            steps: [ script: 'argocd? no — weight 100 via app gateway path map' ]
          abort:
            steps: [ script: 'helm -n coverdesk-prod rollback coverdesk && ./scripts/notify.sh "#incidents" "prod aborted"' ]
```
**Why `deployment` jobs and `environment:` rather than a bash step:** an Azure DevOps *environment* is the object that carries approvals, deploy-window checks, k8s service-connection scoping, and a visible deployment history per environment — which is what “we can prove who approved what” means in an audit. That's the same idea as GitHub's `environment:` and Jenkins' input step; learn the concept once, translate it forever.

## DevSecOps — the gates on this project specifically
| Gate | Tool | Enforcement point | Notes |
| :-- | :-- | :-- | :-- |
| Repo hygiene | branch protection + signed commits + `CODEOWNERS` for `infra/` and `chart/` | PR | `policy-service` changes need a claims-owner review if `shared/` moved |
| Secrets | gitleaks + Azure DevOps secret scanning + Key Vault (never a variable group with a plain secret; use **Azure Key Vault links** in variable groups) | pre-commit + PR | the *variable group → Key Vault* link is the single most-loved Azure feature; use it and stop arguing about masked secrets |
| SAST | SonarCloud/SonarQube with the pipeline analyzing all three packages | PR quality gate | `coverage` 80 %, and a **security hotspots reviewed** requirement (not “fixed” — a hotspot that's a false positive needs a human decision recorded) |
| SCA | Trivy + `pnpm audit --audit-level=high` in the same job | PR | with an expiring-ignore file, owned, in Git |
| Container | Trivy image + `dockle`/`hadolint` on the Dockerfile | build | plus `az policy` `allowedContainerImages` on the RG so `docker.io` random images are refused at deploy time |
| IaC | Checkov + `az apim`… no: **Azure Policy** (`kubernetes-require-labels`, `no-public-ip`, `aks-https-only`) | plan + deploy | Terraform scans the *code*, Azure Policy enforces the *platform*; you need both because drift exists |
| DAST | OWASP ZAP API scan against staging with a test customer token | staging gate | specifically to cover the `/claims/**` authorisation matrix — the self-approval rule is exactly what a naive scan misses, so the scan **must** be authenticated |
| K8s | Kyverno + `kubectl auth can-i --list` review + Falco | admission + runtime | deny `:latest`, deny privileged, require `runAsNonRoot`, require the `checksum/config` annotation on ConfigMap consumers |
| Cloud | Entra Conditional Access for the pipeline's SP? (no — federate: workload identity federation for GitHub; for Azure DevOps use a service connection with workload identity federation, not a client secret) | platform | a client secret on a service connection is a rotation liability with the same blast radius as a committed key |

## Monitoring (Azure Monitor + App Insights, plus the things only you add)
`requests`/`dependencies`/`exceptions`/`performanceCounters`/`customMetrics` in Log Analytics with KQL on the wall: the three queries that answer most pages —
```kusto
requests | where timestamp > ago(15m) | summarize r = count(), err = sumif(success==false,1), p95 = percentile(duration,95) by cloud_RoleInstance | order by err desc
exceptions | where timestamp > ago(1h) | summarize count() by outerMessage | order by count_ desc
dependencies | where timestamp > ago(30m) and name has 'mongodb' | summarize p95=percentile(duration,95) by target | order by p95 desc
```
plus availability tests (multi-region web test on `/health`), an alert on `requests/success` < 99 %, an alert on the **reserve job's** last run (`customMetrics` with a `run_succeeded` measurement — a CronJob that never ran is invisible without it), AKS control-plane diagnostics to the same LAW, and one Grafana panel comparing App Gateway p95 vs App Insights p95 to catch the “slow at the edge” class. Application Insights sampling at 20 % for the campaign week with `broken alert on the excluded portion` noted — sampling changes error-rate math, and knowing that is senior.

## Failure scenarios
| Break | What you see | The fix and the guardrail |
| :-- | :-- | :-- |
| Quoting campaign, 8× traffic | App Gateway `FrontendHealth` fine, pods at CPU limit, p95 1.2 s | HPA max from 6 → 14 *before* the campaign (scheduled via `cron` on a KEDA ScaledObject), App Gateway autoscale 0→10 “for now” costs more than the traffic — decide and write it; the guardrail is a load test at 10× in staging two weeks out |
| Mongo primary steps down | 15 s of `Topology is closed`, then recovery; a 429 on claims | driver `retryWrites`, `readPreference: secondaryPreferred` on report queries only, and a liveness probe that does **not** depend on Mongo, or every pod restarts and the failover becomes an outage |
| Self-approved claim (a real bug from a bad merge) | `NEEDS_REVIEW` never set on 3 claims | the *authorisation matrix test* the prompt demanded; the gate is an authenticated ZAP scan (a test with a claims handler's token doing the self-approve) — that's the “a naive scan misses it” lesson |
| Missing monthly reserve rows | `count(policies)` vs `count(reserve_rows)` differ | the reconciliation query is a *pipeline verification step* and an alarm, not a report someone opens |
| Key Vault soft-delete on the TLS cert | App Gateway 502 on all traffic | purge protection on, `az keyvault update --enable-purge-protection`, and a cert-expiry alarm at 30/14/7 days; the WAF managed-identity cert fetch is the other Azure-specific 502 cause |
| Node pool upgrade with a `ReadWriteOnce` Mongo pod | pod stuck `Pending` after drain | `PDB maxUnavailable: 1`, `topologySpreadConstraints`, a StorageClass with `volumeBindingMode: WaitForFirstConsumer` (otherwise the PVC binds to the wrong AZ and the pod can never schedule) — that one line is the difference between a working and a broken stateful upgrade |

## Troubleshooting — “staging works, prod 500s on `POST /policies`”
Same image digest, same config (verified with `helm get values`). Difference: `MONGO_URL` in prod has `?replicaSet=rs0` and a `readPreference=secondary`, so a *read-after-write* check in the code (`insert`, then `findOne` to confirm) hits a lagging secondary. That is not “the network”; it's a consistency assumption in the application that the environment exposed. Fix forward: the check uses `readPreference=primary` (a 2-line code change the developer makes in an hour) and the *guardrail* is a staging parity rule — staging gets the same topology (`replicaSet` with 3 members) because a standalone Mongo in staging can never surface a read-preference bug. The generalisable lesson for any Azure-vs-AWS project: **parity of shape, not just of version** — single-node databases are where “works in staging” lives.

## Scaling
HPA on `p95 latency` via KEDA Prometheus scalper (target 400 ms) with min 3/max 20 for policy-service; gateway on CPU min 3; claims-service on CPU min 2 with `scaleDown.stabilizationWindow 600` (long-running claim workflows must not be cut mid-transition); Redis: Premium with clustering enabled only when `used_memory > 60 %` or `evicted_keys > 0` for 5 minutes; App Gateway autoscale 2→20; AKS: azure CNI + VPA-in-audit on the node pool with `--max-pods` recalculated and the subnet CIDR plan updated **in the same PR** (a `--max-pods` change with a small subnet is a landmine).

## Rollback
`helm -n coverdesk-prod rollback coverdesk 7` — or `git revert` the values PR (preferred, because Git is truth for Argo users and for the audit trail), plus an `az deployment group what-if` for the infra half if an App Gateway or WAF change is implicated. Data: no rollback needed because the only migrations were index additions (`collmod`/`createIndex` in a pre-deploy Job with `background: true` semantics — which is exactly the *expand-only* rule, translated to Mongo). And a `docs/ROLLBACK.md` with the exact commands and the two known “you cannot roll back” cases (a released fraud rule that already auto-declined claims → compensating re-open script).

## Final architecture
::: flow
internet → Front Door (WAF Prevention, cache for /static, 24×7 campaign headroom) → App Gateway v2 (private, Key Vault cert via managed identity, path map → AKS ingress) → AKS (2 node pools: system×2 D2as_v5, app×3-20 D4ds_v5, Azure Linux, patch channel, workload identity) → coverdesk pods (non-root, readOnlyRootFS, PDB, HPA/KEDA, NetworkPolicy default-deny) → Mongo RS (zonal SSD, WaitForFirstConsumer, PITR backups to an immutable storage account) + Redis Premium (zones, private endpoint) ; CI/CD: Azure DevOps YAML (Build matrix → Security → staging auto → env-approved prod) with ACR trust policy, Trivy, SBOM, Kyverno ; signals: App Insights + LAW + KQL boards + availability tests + alert processing rule to PagerDuty ; Terraform: hub/aks/data/edge/observability modules with a remote state in a locked storage account
:::

## What I learned
Azure rewards you for using its native identity and gate mechanisms (managed identities, workload identity, environments with approvals, Key Vault variable groups, Azure Policy) instead of porting AWS habits and CLI `for` loops; and App Insights' KQL is a first-class debugging tool, not a log viewer. Second lesson, the universal one: staging's *shape* matters as much as its version — a single-node database in staging is a bug generator you'll pay for in prod.

## Interview questions
::: grid2
**“AKS vs Container Apps vs App Service?”** → Container Apps when the team doesn't want cluster ops (KEDA scaling, Dapr, no ingress/nginx to own), App Service when it's a single app and you want patching gone, AKS when you need multi-service topology, NetworkPolicies, custom node config, or you already run EKS/GKE. I'd say which of those we actually have.
**“How do pods get secrets on Azure?”** → Workload Identity: federated token → pod identity → Key Vault, with the vault in RBAC permission model, `Key Vault Secrets User` scoped to the specific secret. No `env` from a variable group holding the value, no `kubectl describe secret` in the incident channel.
**“App Gateway vs Nginx ingress?”** → AGIC programs the App Gateway from your Ingress objects and gives you the WAF and the managed cert; ingress-nginx is faster to change, cheaper, and portable across clouds, but you own the updates and the WAF becomes a separate thing. Here we used AGIC because of the WAF + Key Vault integration, and I can name the two outages it caused (a path-map conflict on a rewrite, and a subnet too small).
**“What does an Azure DevOps environment buy you over a bash step?”** → approvers, deploy windows, an isolated variable/secret scope, the deployment history an auditor asks for, and `abort:` hooks for automatic rollback.
:::

## Résumé bullets
::: callout note ONLY WHAT YOU ACTUALLY BUILT
- Built the delivery path for a three-package Node monorepo on Azure DevOps: multi-stage YAML with per-service build/test/scan jobs, ACR with a trust policy, Trivy and Checkov gates, SBOM artefacts, staging auto-deploy and production behind an approval-gated environment with an automatic `abort` rollback.
- Deployed to AKS with Terraform (hub/aks/data/edge/observability modules, remote state with locking) — Azure CNI IP planning with `--max-pods`, Azure Linux node pools with patch-channel auto-upgrades and surge max-surge, Workload Identity federation, and Key Vault-backed TLS on App Gateway v2.
- Enforced pod and platform security: Kyverno admission policies (no `:latest`, no privileged, required non-root and read-only rootfs), Azure Policy for the resource group (no public IPs, HTTPS-only AKS, allowed registries), default-deny NetworkPolicies, and WAF Prevention mode with a documented, ticketed exclusion for the signed partner webhook.
- Made background work and correctness observable: Application Insights KQL dashboards for per-instance errors and Mongo dependency latency, an availability test at the edge, a reconciliation alarm for the nightly reserve job, and a monthly timed restore drill of `mongodump --oplog` backups into an immutable storage account.
:::

::: revision QUICK REVISION — 5 minutes
**Concepts:** Front Door vs App Gateway vs ingress-nginx · azure CNI `--max-pods` and subnet math · managed identity vs node identity vs workload identity · ACR trust policy + Kyverno (defence in depth) · environments with approvals and `abort:` rollback · Key Vault variable groups and soft-delete/purge protection · diagnostic settings (no data without them) · App Insights sampling and its effect on error math · `WaitForFirstConsumer` for stateful upgrades · `pnpm deploy` pruned images · read-preference/secondary consistency bugs · staging shape parity · Azure Policy vs Checkov (drift vs code) · immutable storage for compliance backups.
**Commands:** `az acr build -r coverdeskacr -t … .` · `az aks get-credentials -g rg -n aks` · `az aks check-security --name aks-coverdesk --resource-group rg-coverdesk` · `az policy state list --resource $ID -o table` · `helm -n coverdesk-prod rollback coverdesk 7` · `kubectl -n coverdesk-prod get events --sort-by=.lastTimestamp | tail` · `az monitor metrics list --resource $AI --metric requestsPerSecond --interval PT5M` · `az keyvault secret show --name TLS-CERT --id …` · `trivy image --exit-code 1 --severity HIGH,CRITICAL IMG` · `az acr check-health`.
**Architecture:** Front Door → App Gateway (WAF) → AKS ingress → gateway → two services → Mongo RS + Redis (both private-endpointed) ; Azure DevOps YAML with Build/Security/staging/prod stages ; App Insights + LAW for signals ; Terraform modules for the whole thing.
**Common mistakes:** node identity pull rights mistaken for pod identity · a `/28` App Gateway subnet · both AGIC and nginx doing TLS · variable-group secrets instead of Key Vault links · unauthenticated DAST on an authorisation-dependent API · `--max-pods` bumped without touching the subnet · no alarm for a CronJob that never ran · purge protection left off on Key Vault.
**Troubleshooting checklist:** which layer is red (Front Door → AGW → ingress → pod) → `az aks check-security` / `kubectl describe` events → App Insights per-instance → `helm get values` diff vs the expected digest → is the *database topology* the same as prod (read preferences, lag) → did a diagnostic setting stop writing (no data ≠ no problem) → which approval/approvers ran, and at what time (`az devops` audit log or the environment history) → for a rollback, does the data layer need the same step (it shouldn't).
:::

Next: [Project 08 — Runline (AWS + Kubernetes + observability) →](proj-08-saas.html)
