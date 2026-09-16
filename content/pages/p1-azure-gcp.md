LEAD: One module, two clouds, because after AWS the right way to learn them is by translation. Everything here answers one question: “what is the same, what is different, and what different thing will bite me if I assume AWS?”

## Azure first: the shape of the place

| Job | Azure service | The one thing AWS people get wrong |
| :-- | :-- | :-- |
| Identity | **Microsoft Entra ID** + **Azure RBAC** (role assignments at MG/sub/RG/scope) | Entra is a *directory*, Azure RBAC is *authorization*. There's no “assume role”: you grant a **managed identity** to a resource and the platform fetches tokens for it. |
| Compute | VM / **VMSS** / App Service / **Container Apps** / AKS | App Service is PaaS — you don't own the OS, so “patching” becomes “pick a runtime version”. Container Apps = serverless containers with KEDA scaling, no cluster to run. |
| Network | **VNet** + subnets + **NSG** + Azure Firewall + **Private Link** | Subnets are safer than AWS: an NSG is *bound* to the subnet or NIC, so “no rule” = default-allow inside a VNet. Don't forget the implicit `AllowVNetInBound` when you `az network nsg rule delete` things. |
| Load balancing | **Azure Load Balancer** (L4) + **Application Gateway** (L7, WAF) + Front Door (global) | L7 needs App Gateway; ALB-like features are *not* in the L4 LB. A WAF policy attaches to App Gateway/Front Door, not to a VM. |
| Storage | **Blob Storage** (containers, access tiers, lifecycle) + managed disks | `Account` + `container`, and the *account* has the network rules — so “403” is often the storage account firewall, not the container. |
| Registry | **ACR** | `az acr build` runs the build in Azure (no local Docker, and the source never leaves your subscription); `az acr login` gives a 3-hour token; tasks rebuild on base-image updates. |
| Database | Azure Database for PostgreSQL Flexible Server / SQL Database | “HA” is a same-region standby; a *readable* replica is a different SKU than plain zone-redundant HA. |
| CI/CD | **Azure Pipelines** (YAML + classic) and GitHub Actions | YAML pipelines + **service connections** (workload identity federation recommended) + **environments** for approvals; classic release pipelines are being retired. |
| Observability | **Azure Monitor** (metrics, logs, alerts, dashboards) + **Application Insights** | Everything is Log Analytics **KQL**, not SQL-flavoured Insights query — that's a 30-minute relearn with big payoff. |
| Secrets | **Key Vault** | RBAC-enabled vs vault-access policies: pick RBAC mode *at creation*; switching later is a migration. |
| IaC | **Bicep** / ARM / Terraform | Bicep is a nicer ARM template; ARM is what actually gets deployed, and *what-resources-change* semantics differ from Terraform's plan. |

### The five structural differences that cause the incidents

1. **Resource groups** are a lifecycle boundary: `az group delete` deletes *everything* inside, whether or not your IaC created it. Importing existing resources into Terraform before you move them is how you avoid a 3 a.m. surprise.
2. **Management groups + Azure Policy** replace AWS Organizations + SCPs. Landing zones put subscriptions under `corp/online/sandbox`; policies enforce “no public IPs”, “allowed regions”, “tags required”.
3. **Managed identity** replaces instance profiles *and* most secrets: system-assigned (dies with the resource) vs **user-assigned** (reusable, survives a rebuild, and is what teams should use so an AKS node replacement doesn't orphan permissions).
4. **NSGs are per-subnet-or-NIC and default-allow within a VNet**, so Azure Firewall/Private Link are load-bearing where AWS would have used SGs alone. Also: `ApplicationGateway` needs its own subnet; NAT Gateway replaces the load-balancer-backend outbound SNAT — and a *standard SKU* LB is required for that.
5. **Diagnostic settings** are how anything reaches Log Analytics; without them there is simply no data. “Metrics exist but logs are empty” is nearly always a missing diagnostic setting + a managed identity with `Monitoring Metrics Publisher`.

### The Azure CLI you'll live in

```bash
az login --use-device-code
az account set --subscription "DevOps-Lab"
az group create -n rg-medbook-dev -l westeurope \
  -t 'app=medbook env=dev owner=you costcenter=lab'
az vm create -g rg-medbook-dev -n medbook-01 \
  --image Ubuntu2204 --size Standard_B2s \
  --assign-identity --role "Reader" --scope $(az group show -g rg-medbook-dev --query id -o tsv) \
  --nsg-rule SSH --admin-username azureuser --generate-ssh-keys
az aks create -g rg-medbook-dev -n aks-medbook \
  --node-count 2 --node-vm-size Standard_D4ds_v5 \
  --enable-oidc-issuer --enable-workload-identity \
  --attach-acr medbookacr --network-policy azure \
  --query 'oidcIssuerProfile.issuerURL' -o tsv
az keyvault create -n kv-medbook-dev -g rg-medbook-dev --enable-rbac-authorization
az deployment group what-if --resource-group rg-medbook-dev -f main.bicep   # Bicep's plan
```

`--assign-identity` + `--role` + `--scope` in one command is Azure's best idea: identity and permission granted in the same breath as creation, so nobody hand-edits a vault policy at 2 a.m.

### ACR + App Service or AKS, the two-minute deploy that makes Azure feel real

```bash
az acr create -g rg-medbook-dev -n medbookacr --sku Basic --admin-user-enabled false
az acr update -n medbookacr --policies-pull-through-source none   # then enable quarantine-on-scan for prod
az acr build -r medbookacr -t medbookacr.azurecr.io/medbook:main-a1f9c2d .   # build in Azure
az acr task create -r medbookacr -n rebuild-base \
  --image medbookacr.azurecr.io/medbook:latest \
  --base-image-trigger-enable true --base-image-trigger-type All
# deploy to a container app (simplest path with a managed identity pulling the image)
az containerapp create -g rg-medbook-dev -n medbook \
  --environment cae-medbook-dev --image medbookacr.azurecr.io/medbook:main-a1f9c2d \
  --registry-server medbookacr.azurecr.io --registry-identity /subscriptions/.../userAssignedIdentities/acr-pull \
  --target-port 8080 --ingress external \
  --secrets "db-connection-string=@Microsoft.KeyVault(VaultName=kv-medbook-dev;SecretName=db-cs)" \
  --min-replicas 2 --max-replicas 5
```

## GCP next: three ideas that reorganise your mental model

| Job | GCP service | What's different (deliberately) |
| :-- | :-- | :-- |
| Hierarchy | Org → Folder → **Project** → resources | Projects are *the* unit of IAM, quota, billing and API enablement. A “resource group” equivalent barely matters because isolation is per project. |
| Network | **VPC = global**; **subnets = regional**; firewall rules are *tag- and service-account-based*, priority-numbered, and stateful-inbound + stateful-by-default-outbound | One VPC spans regions; `0.0.0.0/0` deny rules are how you implement “no internet”; VPC-SC perimeters bound *data* not just network. |
| Compute | Compute Engine + MIG / **Cloud Run** / GKE (Autopilot) | MIG is the ASG equivalent, with `--update-policy=restart` for rolling changes and autoscaling on custom metrics out of the box. Cloud Run scales to zero, and containers must honour `K_SERVICE` + a 60s-or-configurable request timeout. |
| Registry | **Artifact Registry** | One repo type for docker, helm, npm, python — and `gcloud auth configure-docker` writes 60-minute credentials, so CI logs in per job. |
| CI/CD | **Cloud Build** + Deploy + **Skaffold** | `cloudbuild.yaml` with `steps:` (each step is a container!), `secrets:` from Secret Manager via `availableSecrets`, and triggers from GitHub/GitLab. |
| Database | Cloud SQL / AlloyDB / Spanner / Firestore | Cloud SQL: private IP needs a **VPC peering + services range**, and the `cloudsql-auth`/`Cloud SQL Admin API` step is one many tutorials skip — hence “connection timed out” with a correct config. |
| Observability | **Cloud Monitoring** (metrics + alerting policies), **Cloud Logging** (log-based metrics!), Trace, Error Reporting | Log-based metrics are the killer feature: `logs.query` becomes a gauge with no code change — a Grafana panel over a log field is a one-step job. |
| Secrets | **Secret Manager** with automatic rotation, workload identity for access | `secretVersion` + labels; never `gcloud secrets versions access` in a build log — that secret is now in Cloud Storage forever. |

```bash
gcloud config set project medbook-dev && gcloud config set compute/region europe-west1
gcloud services enable container.googleapis.com artifactregistry.googleapis.com sqladmin.googleapis.com
gcloud compute networks create medbook-vpc --subnet-mode=custom
gcloud compute networks subnets create pub-a --network medbook-vpc --region europe-west1 \
  --range 10.10.0.0/24 --enable-private-ip-google-access
gcloud compute instances create medbook-01 --zone=europe-west1-b \
  --network=medbook-vpc --subnet=pub-a --no-address \
  --service-account=medbook-sa@medbook-dev.iam.gserviceaccount.com \
  --scopes=cloud-platform
gcloud artifact-registry repositories create containers --location=europe-west1 \
  --format=DOCKER --description="app images"
gcloud run deploy medbook --image europe-west1-docker.pkg.dev/medbook-dev/containers/medbook:v1.4.0 \
  --region europe-west1 --allow-unauthenticated --min-instances=2 --concurrency=80 \
  --set-env-vars SPRING_PROFILES_ACTIVE=prod --update-secret DB_URL=medbook-db-url
```

**The three things I'd warn you about on day one:** `terraform apply` fails with `api not enabled` until you run `gcloud services enable …` (or let Terraform do it); every `gcloud` command needs **project + region/zone** in the config or the flags, and errors from the wrong region read like “not found”; and firewall rules are **priority-numbered** (lower wins) with a default `allow-implicit-*` set that surprises people coming from SGs.

## The AWS → Azure → GCP translation table (the asset you take away)

| Task | AWS | Azure | GCP |
| :-- | :-- | :-- | :-- |
| Scoped isolation | Account | Management group → Subscription → Resource group | Org → Folder → Project |
| Machine identity | Instance profile / IRSA | Managed identity + workload identity federation | Service account + Workload Identity Federation |
| Human auth | IAM Identity Center (SSO) | Entra ID + PIM | Cloud Identity + IAP/OIDC |
| Private network | VPC, subnets regional | VNet, subnets in a region | VPC **global**, subnets regional |
| Firewall | SG (stateful, per ENI) + NACL | NSG (per subnet/NIC, default-allow in VNet) | Tag-based rules, priority number, egress default |
| LB | ALB (L7) / NLB (L4) | App Gateway (L7) / LB (L4) / Front Door (global) | HTTPS LB / Network LB / (Global) External LB |
| Object store | S3 bucket | Storage account + container | Cloud Storage bucket |
| Registry | ECR | ACR (can build there) | Artifact Registry |
| Serverless containers | Fargate | Container Apps | Cloud Run |
| Kubernetes | EKS (vpc-cni, node roles) | AKS (azure CNI or overlay, kubenet legacy, ACR attach) | GKE (alias IPs, no NAT for pods!, Autopilot) |
| Managed Postgres | RDS + Proxy | Flexible Server | Cloud SQL (+ private services access) |
| IaC | CloudFormation (drift, stack sets) | Bicep/ARM (what-if) | Terraform (Deployment Manager is legacy) |
| CI/CD | CodePipeline/CodeBuild + GitHub | Azure Pipelines (YAML, environments) | Cloud Build + Deploy |
| Metrics | CloudWatch | Azure Monitor + App Insights | Cloud Monitoring + log-based metrics |
| Logs | CloudWatch Logs (Insights) | Log Analytics (**KQL**) | Cloud Logging (Logs Explorer) |
| Tracing | X-Ray | App Insights snapshots | Cloud Trace |
| Secrets | Secrets Manager + Parameter Store | Key Vault | Secret Manager |
| Events | EventBridge | Event Grid | Eventarc / Pub-Sub |
| Cost governance | Budgets + Cost Explorer + Anomaly | Cost Management + Policy | Budgets + alerts + Recommender |

## Production scenario — the same app on AWS and Azure, and where it breaks

We deployed the retail app twice in a week: AWS on ECS/Fargate, Azure on Container Apps. Same image, same Terraform style. Three differences cost real time:

1. **Outbound traffic.** AWS: Fargate task in a private subnet with a NAT — fine. Azure: Container Apps *without* a NAT gateway and without delegated subnet = no egress, and the app's “fetch exchange rates” call hung with no error at all (a timeout at 30 s, not a refusal). Fix: a NAT gateway on the managed environment's VNet integration. Lesson: on Azure, egress for PaaS is a *feature you enable*, not a route table you inherit.
2. **Health probes.** ALB used `/actuator/health`; Container Apps expects `/healthz` returning 200 within 2 s — and, unlike ALB, a failing readiness probe removes the replica from the *ingress map* without any event you'd notice. We lost 15 minutes because the app's context path was `/api`: `/healthz` 404'd, so the readiness never turned green and the “scale-out” did nothing.
3. **Secrets at start.** On AWS the task role read Secrets Manager directly. On Azure, Managed Service Identity works, but the app needed the `Azure.Identity` SDK — a *code* dependency. So we injected the secret as an env var from Key Vault via the `secrets:` field instead (which the platform resolves at deploy time). Different mechanism, same outcome, and the review note “don't assume cloud SDKs are available in every runtime” is now in our onboarding doc.
4. **Bonus difference:** on GCP we later moved the same container to Cloud Run, where the third difference was `--concurrency` and CPU-off during request gaps: a scheduled batch job inside the container silently stopped running between requests. Nothing “failed” — it just didn't happen. That's the class of bug PaaS gives you.

## Common mistakes (the cross-cloud kind)

::: checklist
- [ ] **Porting, not translating.** Terraform from a tutorial on AWS applied to Azure as if `azurerm_subnet` had a route table — or worse, `aws_vpc` renamed to `google_compute_network` with subnets still assumed regional.
- [ ] **Assuming the security defaults match.** AWS SGs default-deny inbound; Azure NSGs default-allow inside the VNet; GCP has an `allow-all-ssh`-shaped legacy rule in auto-created networks and a default *allow* for egress.
- [ ] **Assuming egress exists.** NAT on AWS, NAT or Private Link on Azure, `--no-address` + Private Google Access on GCP. Three different knobs, one bug (“no outbound”).
- [ ] **Region vs location vs zone, used interchangeably.** Azure locations are not zones; GCP regions have no equivalent of AWS “AZ-attached” resources except zones inside the region; `az vm list` across regions needs `-o tsv` and a loop, not a single `--location`.
- [ ] **Not using each cloud's native identity federation.** Putting a `AZURE_APP_SECRET` or an AWS access key in a GitHub Actions secret when OIDC is free and expiring.
- [ ] **One IaC style for all three.** Bicep for a Microsoft shop's team review, Terraform where you're crossing clouds (and never `az` CLI scripts that only humans can read).
:::

## Best practices

::: grid2
**Learn one cloud's networking deeply; the others become vocabulary.** If you can draw a VPC with three subnet tiers and explain the route tables, you already know 80 % of a VNet and a GCP VPC.
**Use each cloud's “one command” PaaS to see a result early:** `aws ecs run-task`/App Runner, `az containerapp create`, `gcloud run deploy`. Then replace it with an explicit, reviewable path.
**Keep a translation journal.** A page per confusing difference. After three months it's the most senior thing in your notes.
**Never manage a multi-cloud artefact twice.** The image is pushed **once** (a canonical registry) and each cloud references it by digest or copies it via a replication rule. Two builds, two truths.
:::

## Interview answer

::: callout aha “Have you worked in more than one cloud?”
“Yes — I built the same application's delivery path on AWS and Azure, and ran it on GCP Cloud Run afterwards, so I can talk about the differences concretely. On AWS I own the VPC: public subnets for the ALB, private for the app tier with a NAT and gateway endpoints, chained security groups, an IAM role for the nodes, ECR with immutable tags and scan-on-push, CloudWatch for logs and alarms. On Azure, the shape was the same but the controls sit differently — resource groups as the lifecycle boundary, NSGs bound to subnets where AWS uses SGs on ENIs, managed identities instead of instance roles, ACR built server-side with `az acr build`, Key Vault with RBAC mode, Application Insights instead of hand-built metrics, and Azure DevOps YAML with environments and approvals for the gate. On GCP, subnets are regional inside a global VPC, IAM and quota hang off projects, firewall rules are tag-based with priorities, and Cloud Run meant no nodes and no cron-at-all — which is why I now check egress and CPU-off behaviour whenever a serverless service 'silently doesn't do something'. The thing I take from multi-cloud is not flag knowledge, it's a design habit: name the primitive first (identity, network, registry, gate, signals), then bind it to each cloud's implementation in code review.”
:::

::: grid2
**Follow-up: “What surprised you moving between clouds?”** → egress defaults, identity model, and where logs *must* be explicitly enabled (Azure diagnostic settings) vs automatic (CloudWatch, Cloud Logging).
**Follow-up: “Would you use Bicep or Terraform?”** → Terraform for multi-cloud and module reuse; Bicep if the team is Microsoft-only and wants `what-if` + Azure Policy integration out of the box.
**Follow-up: “Cloud Run or GKE?”** → Cloud Run until you need static IPs, gRPC streaming, cron-in-container, or long CPU-bound work; then GKE, and Autopilot if the team doesn't want to own node config.
**Follow-up: “How do you keep the two clouds from drifting apart?”** → one artefact and one policy set: same Dockerfile, same Trivy gate, same IaC modules with a thin per-cloud wrapper, and a compliance report per environment instead of per cloud.
:::

## Virtual lab — Azure and GCP side by side (simulated terminal)

::: lab az-gcp-1
:::

## Commands to remember

::: grid2
```bash
az account set -s "DevOps-Lab"
az group create -n rg-app-dev -l westeurope -t 'app=medbook env=dev'
az vm list -d -o table
az acr build -r myacr -t myacr.azurecr.io/app:v1 .
az role assignment list --assignee $(az identity show -n id-app --query clientId -o tsv)
az monitor metrics list --resource $ID --metric "Percentage CPU"
```
```bash
gcloud config set project p-dev
gcloud compute instances create app-01 --zone europe-west1-b \
  --network lab-vpc --no-address --scopes=cloud-platform
gcloud artifact-registry repos describe containers
gcloud run services describe app --region europe-west1 --format 'value(status.url)'
gcloud logging read 'resource.type="cloud_run_revision" AND severity>=ERROR' --limit 20
```
:::

::: callout note WHAT I MUST REMEMBER
1. Azure = Entra + RBAC scope chains, resource groups as lifecycle, managed identities instead of instance profiles, ACR/Pipelines/App Insights as the PaaS spine.
2. GCP = global VPC with regional subnets, tag/priority firewall, projects as the IAM+quota+billing unit, Artifact Registry + Cloud Build + Cloud Run/GKE.
3. Every difference above reduces to one sentence: **the primitives are the same; where they attach, and what the defaults are, is not.**
:::

::: revision REVISION — 5 minutes
**Concepts:** Entra/RBAC vs IAM · resource group lifecycle vs region · NSG vs SG vs GCP firewall · App Gateway vs Azure LB vs Front Door · ACR build-in-cloud · managed identity system vs user-assigned · diagnostic settings → Log Analytics/KQL · GCP project boundaries · global VPC/regional subnets · Private Google Access · Cloud Run concurrency + CPU-off + `K_SERVICE` · log-based metrics · Secret Manager + workload identity · Bicep `what-if`.
**Architecture to remember (Azure):** Front Door/App Gateway → subnet with delegation → Container Apps/AKS + managed identity → Key Vault + Private Endpoint → Azure Monitor → Azure DevOps environments with approvals.
**Common mistakes:** porting instead of translating · forgetting egress · assuming default-allow/default-deny matches AWS · long-lived storage keys · two CI systems building the same image.
**Troubleshooting checklist:** `az account show` / `gcloud config list` → region/location → NSG/ASG/firewall path → managed identity assignment + token → diagnostic settings enabled? → probe path and context root → cost line in the right subscription/project.
:::

Next: [Terraform — infrastructure as code →](p1-terraform.html)
