LEAD: There is only one cloud; it appears three times with different names. This page teaches the concept once — identity, network, compute, storage, registry, database, orchestrator, observability, secrets, cost — then maps it to AWS, Azure and GCP, so switching employer costs you a week instead of six months.

::: callout note HOW TO USE THIS PAGE (and why the depth is uneven)
Go **deep on AWS**: it has the most job listings and the most explicit primitives — if you can build an AWS VPC by hand, everything else is translation. **Azure** second: enterprises that already pay for Office/Entra buy Azure first, and their pipelines are Azure DevOps. **GCP** third but not optional: cleanest managed Kubernetes, nicest CLI, and it hosts this course's AIOps and MLOps projects. Learning all three in parallel gives you three mediocre mental models instead of one good one.
:::

## The ten things every cloud gives you (in different clothes)

| Job to do | AWS | Azure | GCP | The concept you must actually understand |
| :-- | :-- | :-- | :-- | :-- |
| Run a server | EC2 | Azure VM | Compute Engine | VM lifecycle, images, key-pair auth, IMDSv2/metadata hardening |
| Containers on servers | ECS/Fargate | Container Apps | Cloud Run | task vs pod, scheduling, placement |
| Orchestrate containers | **EKS** | **AKS** | **GKE** | managed control plane vs data plane, node pools, upgrade cadence |
| Private network | VPC + subnets + route tables | VNet + subnets + NSG | VPC + subnets + firewall rules | CIDR planning, public vs private, NAT, peering |
| Load balancing | ALB / NLB | Load Balancer + App Gateway | HTTP(S) LB + Network LB | L4 vs L7, health checks, TLS termination, target groups |
| Object storage | S3 | Blob Storage | Cloud Storage | buckets/containers, prefix ≠ folder, tiers, encryption, bucket policy |
| Relational database | RDS / Aurora | Azure Database for PostgreSQL / SQL DB | Cloud SQL / AlloyDB | managed endpoint, failover, maintenance window, backups, pooling |
| Cache | ElastiCache | Azure Cache for Redis | Memorystore | cache-aside, eviction policy, TTL |
| Image registry | ECR | ACR | Artifact Registry | OCI artefacts, token auth, immutable tags, scan-on-push |
| Relational key/value | DynamoDB | Cosmos DB | Datastore/Bigtable | partition key design, throughput model |
| Secret store | Secrets Manager | Key Vault | Secret Manager | rotation, IAM-scoped reads, audit of access |
| Identity | IAM users/roles/policies | Entra ID + RBAC | Cloud IAM | role assumption, service accounts, workload identity federation, least privilege |
| CI/CD | CodePipeline | Azure DevOps / GH Actions | Cloud Build | stages, agents, artefact stores, gates |
| Metrics & logs | CloudWatch + X-Ray | Azure Monitor + App Insights | Cloud Monitoring + Logging + Trace | metrics vs logs vs traces, retention, alert routing |
| DNS | Route 53 | Azure DNS + Traffic Manager | Cloud DNS + Cloud Armor | records, health-checked failover, private zones, TTL |
| IaC | CloudFormation / Terraform | Bicep / Terraform | Terraform / Deployment Manager | declarative desired state, plan, state, drift |
| Cost guardrails | Budgets + Cost Anomaly | Cost Management + Policy | Budgets + Org Policy | tagging, rightsizing, idle cleanup, egress |

::: callout why WHY THIS TABLE IS THE EXAM
Nobody asks “what is an EC2 instance”. They ask “how would you put an API in a private subnet and still let it reach the internet for package installs?” You answer with the concept (private subnet, no IGW route, default route to a NAT gateway, egress 443 allowed) and then name it in *their* cloud. Console screenshots memorised; concept understood — only one of those transfers.
:::

## Regions, zones, and why placement is an architecture decision

- **Region** = a geographic area (`eu-west-1` Ireland, `centralus`, `europe-west1` Belgium). Data residency, pricing, service availability and quotas are all *per region*. Choose it for latency to your users + compliance + which services exist; then almost never change it.
- **Availability Zone** = an independent data centre with its own power and cooling, linked to its siblings. Multi-AZ is the entire reason one instance is never “production-grade”.
- **Edge / POP** = where CDN and DNS responses come from: CloudFront, Front Door, Cloud CDN; Route 53 answers DNS from the nearest POP.
- **Global vs zonal**: IAM, Route 53, S3 bucket names and Cloud IAM are global-ish; EC2, subnets, RDS instances and EBS volumes are regional or zonal. This is why “the AMI is in us-east-1 but the instance is in eu-west-1” is a real beginner error, and why cross-AZ transfer is billed while cross-region is billed much more.

::: flow
Region
└── AZ a: public subnet (ALB, NAT, bastion) · private-app subnet · private-data subnet
└── AZ b: public subnet (ALB, NAT) · private-app subnet · private-data subnet (standby replica)
└── AZ c: headroom for an ASG during a storm
:::

Rule of thumb you can repeat in any design review: **everything that serves traffic exists in ≥2 AZs; everything that stores state has a managed failover; nothing that matters lives on one disk in one zone.**

## Accounts, subscriptions, projects — one idea, three names

| Cloud | Top | Middle | Bottom (the isolation boundary) | Typical split |
| :-- | :-- | :-- | :-- | :-- |
| AWS | Organisation | OU | **Account** (blast radius + bill) | `prod`, `nonprod`, `security`, `shared-services` |
| Azure | Management group | Subscription | **Resource group** (lifecycle boundary) | `prod-eastus`, `dev`, `network-hub`, `security` |
| GCP | Organisation | Folder | **Project** (IAM + quota + billing attach here) | `prod-app`, `dev-sandbox`, `logs-archive` |

::: callout aha THE SENTENCE THAT MAKES YOU SOUND SENIOR
“Isolate by account/subscription/project, not by naming convention.” A script with bad variables or an attacker with stolen keys cannot touch what it has no credentials for. A tag `env=prod` is a hint; a separate account is a boundary. Enforced separation is the only control that survives a mistake in one pipeline.
:::

## The shared-responsibility line, in the four models you will meet

::: cols
::: col
**VM in the cloud (EC2 / Azure VM / Compute Engine)**
Cloud: hypervisor, hardware, fabric, instance availability.
**You: the OS, patching, firewall, hardening, the app, its deps, the agent, logs, backups.**
“We run on AWS so it's secure” is an immediate red flag in a review — that sentence is the gap.
:::
::: col
**Managed containers (Fargate / Container Apps / Cloud Run)**
Cloud adds: the runtime, the node OS, the scaling plane.
You: the image, dependencies, config, secrets, what you expose, app logs.
:::
::: col
**Kubernetes (EKS / AKS / GKE)**
Cloud adds: control plane, etcd, upgrades, node images.
You: node patching policy, RBAC, NetworkPolicy, requests/limits, the image supply chain, secrets, and the probes.
:::
::: col
**Managed database (RDS / Azure Database / Cloud SQL)**
Cloud adds: DB software, patching window, failover, storage growth, backups.
You: schema, credential rotation, connection limits and pooling, the network path to it, key choice — and *proving* failover works.
:::

## The concepts that hurt beginners most

**1 · Identity is not a login, it is a policy evaluation.** Every API call resolves: principal → attached policies → resource policy → org guardrail (SCP / Azure Policy / GCP constraint) → allow or deny, with explicit deny winning. Symptom to recognise: `User: arn:aws:sts…:assumed-role/ci-role/pipeline is not authorized to perform: ecr:BatchGetImage`. The fix is never “add AdministratorAccess”; it is the one missing action on the one resource.

**2 · Networking is routes, then rules.** Packets follow the route table; the firewall then allows or denies. A missing route and a missing rule look identical from the client (a timeout), so learn to check both and to say which one you eliminated.

**3 · Elasticity is a policy plus a cooldown.** An ASG/VMSS/MIG needs a metric, a threshold, a cooldown and a **drain** behaviour. No cooldown → 2 a.m. scaling storms. No drain → in-flight requests die at scale-in.

**4 · Storage tiers and egress are where the money goes.** AWS NAT is roughly $0.045/hr + $0.045/GB processed: a chatty image pull from a private subnet can be a $150/month line item nobody declared. VPC endpoints (S3, ECR, Secrets Manager, CloudWatch Logs) remove those paths from NAT entirely; the equivalents are Azure service endpoints/private endpoints and GCP Private Google Access.

**5 · Everything is an API, so everything belongs in a pipeline.** `aws`, `az`, `gcloud` wrap the same APIs the console calls. If a step can't be done with the CLI, question whether it should be a step at all.

## The CLI: one pattern, three dialects

::: cols
::: col
```bash
# AWS CLI v2
aws configure sso          # never a long-lived key
aws ec2 describe-instances \
  --filters Name=tag:App,Values=medbook \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,Placement.AvailabilityZone]' \
  --output table
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=i-0abc \
  --start-time -PT1H --end-time now \
  --period 300 --statistics Average
```
:::
::: col
```bash
# Azure CLI
az account set -s 1234abcd-...
az vm list -d -g rg-medbook-prod \
  --query '[].[name,vmSize,provisioningState]' -o tsv
az monitor metrics list \
  --resource $(az vm show -g rg-medbook-prod \
    -n medbook-vm --query id -o tsv) \
  --metric "Percentage CPU" --interval PT5M
```
:::
::: col
```bash
# gcloud
gcloud config set project medbook-prod
gcloud compute instances list \
  --filter="labels.app=medbook" \
  --format='table(name,zone,status)'
gcloud compute firewall-rules list \
  --filter="network=medbook-net" --format=table
gcloud logging read 'severity>=ERROR' \
  --limit 25 --format='table(timestamp,textPayload)'
```
:::

Notice the pattern, because it repeats for everything in every cloud: **scope** (account / subscription / project) → **filter** (tags, resource group, labels) → **query** (project only the columns you want) → **format** (`table` for humans, `json`/`tsv` for scripts). Once that clicks, a fourth cloud is an afternoon.

## Tagging, quotas and cost: the adult part

| Practice | A good organisation | What you say in an interview |
| :-- | :-- | :-- |
| Mandatory tags | SCP / Azure Policy / GCP constraint blocks creation without `Owner`, `Environment`, `App`, `CostCenter`, `ManagedBy` | “I make tagging a policy, not a convention — I've seen a surprise bill traced to one forgotten resource.” |
| Budgets | 80 % alert to a channel, 100 % to a manager, per-tag cost report weekly | “I set a budget before I create anything in a new account.” |
| Quotas | Known per region: EIPs, vCPU, SGs per ENI, RDS storage; raised *before* launch | “The ASG couldn't scale — we were at the default on-demand vCPU limit. I raised the quota and added a service-limit alarm.” |
| Idle cleanup | Scheduled non-prod shutdown at 20:00, snapshot retention, delete unattached volumes | “Non-prod was 35 % of the bill running 24×7. A stop schedule paid for my time.” |
| Rightsizing | Reviewed monthly from utilisation metrics, not vibes | “I moved gp2 → gp3: 40 % cheaper storage with better baseline IOPS.” |

::: callout fix THE LEARNING-COST TRAP (your first real lesson)
Every project here can run in one region for a few rupees a day if you are disciplined, and thousands a month if you are not. So:
- **budget with an alert** before anything exists (and fire it once at $0.01 to prove the alarm path);
- **`terraform destroy` at the end of every session** — then delete what state didn't own: **Elastic IPs, NAT gateways, load balancers, snapshots, CloudWatch log groups**;
- keep RDS small (`db.t4g.micro`, 20 GB gp3, single-AZ) and never leave a Multi-AZ + 100 GB io2 + 35-day backup cluster running because you “might need it”;
- tag everything `ManagedBy=learning`, so a weekend later one query finds it all.
Tearing down is a DevOps skill, not a chore. Production cost control is this exact muscle.
:::

## Free-tier and lab safety, once before any project

```bash
# 1 · an isolated account/project for learning (never your employer's, never the root user)
#     MFA on root, no access key on root — at all, ever

# 2 · CLI via SSO, not keys
aws configure sso && aws sso login --profile lab
aws sts get-caller-identity            # reflex before any apply

# 3 · a budget you have proven
aws budgets create-budget --account-id 123456789012 \
  --budget file://budget.json --notifications-with-subscribers file://notify.json
#    BudgetLimit $20 monthly; ALERT at 80% ACTUAL and 100% FORECASTED

# 4 · guard rails in the shell itself
echo 'export AWS_PAGER=""' >> ~/.bashrc      # no pager inside scripts, ever
```

::: checklist CLOUD READINESS CHECKLIST (all four, or you are not ready for Project 01)
- [ ] Separate account/project/subscription for learning, MFA everywhere, **no access key on the root user**
- [ ] Budget + alert configured and *proven* (set it to $0.01 once and wait for the email)
- [ ] CLI works via SSO/`gcloud auth`/`az login`; `get-caller-identity` is a reflex before destructive commands
- [ ] Terraform installed, and you can say in one sentence why its state file must not live on your laptop
:::

## Production scenario — “the pipeline could pull images on Monday but not Wednesday”

Nothing changed in the code. Two candidate causes, both cross-cloud patterns:

1. **Registry token expiry.** ECR login tokens last 12 hours; ACR refresh tokens ~24 h; `gcloud auth configure-docker` writes short-lived credentials. A long-lived self-hosted runner caches a login and fails mid-shift. Fix: the pipeline logs in *inside the same job*, every run.
2. **A network change you don't own.** A new route, a NACL, or a deleted VPC endpoint for ECR means the *path* is gone. Symptom differences are reliable: `no such host` / timeout = path; `403 unauthorized` = identity.
3. Prove which one it was, then encode a canary: a CI step doing `docker manifest inspect` on the base image at the top of the pipeline — two seconds that cover both auth and the network path.

The lesson generalises: **most “random” cloud failures are an expiry or a change in something you do not own.** Make expiry visible (log in per job, renew in code) and changes loud (drift alarms, endpoint auditing).

## Common mistakes

::: checklist
- [ ] Everything in one account with one user and `AdministratorAccess`. You are training yourself to be careless.
- [ ] The same VPC CIDR in prod and non-prod — then peering is impossible. Plan CIDRs before the second environment exists.
- [ ] `0.0.0.0/0` inbound on 22 or 3306 “for now”. Use SSM Session Manager / a bastion / Azure Bastion / `gcloud compute ssh --tunnel-through-iap`.
- [ ] Learning the console only. Console is for *looking*; code is for building, reviewing and reproducing.
- [ ] Treating IAM as a security afterthought instead of the interface every other service uses.
- [ ] Choosing the region the tutorial used, not the one your users and your compliance map need.
- [ ] No tags and no budget — which is how a learning project gets cancelled over a surprise bill.
:::

## Best practices you can start today

::: grid2
**One concept, three names, one table.** Keep your own copy of the mapping table above and add a row whenever a service confuses you. It is the fastest-growing asset in your kit.
**Learn the failure mode of every service you deploy.** Managed DB → the failover window and how you test it. ALB → what makes a target red. ASG → cooldowns and lifecycle hooks. Registry → token TTLs. That catalogue is what “experience” actually is.
**Never create by hand what you will create twice.** Twice or more → Terraform. Twice a day → a pipeline.
**Write the account map in the README.** Which account/subscription/project, which region, which environment, what the pipeline assumes. It is the first thing a reviewer reads and the last thing a beginner writes.
:::

## Interview answer

::: callout aha “Which cloud do you know, and how transferable is it?”
“I'm strongest on AWS and I build the networking myself, so: VPC design with public and private subnets across AZs, security groups as the instance firewall, NAT or VPC endpoints for egress, ALB with target-group health checks, RDS Multi-AZ in a private subnet group, ECR with immutable tags and scan-on-push, EKS for orchestration, and CloudWatch for logs and alarms. I've run the same architecture on Azure, which mostly meant renaming: VNet and NSG instead of subnet and security group, resource groups as the lifecycle boundary, ACR, AKS, Application Insights instead of CloudWatch, and Managed Identity instead of instance roles — plus Azure DevOps YAML with service connections and environments. On GCP I've used GKE and Cloud Run with Artifact Registry and Cloud Monitoring; the shift there is that IAM, quota and billing hang off *projects*, and subnets are regional. So: I know the primitives — identity, network, compute, storage, registry, orchestrator, observability, cost — and I translate them. If your stack is mainly Azure, I'd need a week in your subscriptions and less in your pipelines, and what would slow me down is your policies, not the technologies.”
:::

::: grid2
**Follow-up: “AWS vs Azure vs GCP for a new workload?”** → existing skills and contracts first (a team that knows Azure out-runs a team that “should” use AWS); then required services (Azure for .NET/AD-bound apps, GCP for data/ML and GKE, AWS for breadth); then region and compliance; then cost model differences on egress and support. If there's no cloud yet, hiring reality decides.
**Follow-up: “Why separate accounts?”** → credentials, quotas, billing, guardrails and audit are all scoped per account, so a mistake stays inside one.
**Follow-up: “How do you control cost as an engineer?”** → tags enforced by policy, budgets with early alerts, lifecycle rules on storage and snapshots, rightsizing from metrics, scheduled non-prod shutdowns, and choosing the cheapest egress path (VPC endpoints, CDN, same-AZ placement).
:::

## Virtual lab — first 24 hours in a cloud account (simulated terminal)

::: lab cloud-1
:::

## Commands to remember

::: grid2
```bash
aws sts get-caller-identity
aws configure list
aws ec2 describe-vpcs --query 'Vpcs[].[VpcId,CidrBlock]'
aws ec2 describe-security-groups --group-ids sg-xxx \
  --query 'SecurityGroups[0].IpPermissions'
```
```bash
az account show ; az group list -o table
az network vnet list -g rg-lab
az role assignment list --assignee <sp> -o table
gcloud config list ; gcloud projects list
gcloud compute networks subnets list
```
```bash
# cost, from anywhere
aws ce get-cost-and-usage \
  --time-period Start=2026-03-01,End=2026-03-31 \
  --granularity MONTHLY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE
```
:::

::: callout note WHAT I MUST REMEMBER
1. Ten concepts, three names. Learn the concept and translate.
2. Isolation is enforced by account/subscription/project, not by tags.
3. Budget and teardown are part of the deployment, not admin chores.
4. If it is not scriptable, it is not understood.
:::

::: revision REVISION — 5 minutes
**Concepts:** region/AZ/edge · the ten shared services · accounts vs OUs vs subscriptions/RGs vs projects/folders · identity = policy evaluation with explicit-deny winning · routes before rules · responsibility lines per model · egress and NAT economics · quotas as launch blockers · IMDSv2, workload identity federation, private endpoints · tagging as policy.
**Architecture to remember:** users → DNS → global LB/CDN → regional VPC/VNet with public+private subnets in ≥2 AZs → app tier (ASG/ECS/AKS/GKE/Cloud Run) → data tier (managed DB in private subnets) → an out-of-band observability plane.
**Common mistakes:** one account for everything · console-only learning · admin role for CI · `0.0.0.0/0` on 22 · identical CIDRs · no tags/budget · infinite log retention.
**Troubleshooting checklist:** who am I → which region → which route → which rule → did a token expire → did we hit a quota → which line on the bill moved. In that order.
:::

Next: [AWS — VPC, EC2, ALB, RDS, IAM →](p1-aws.html)
