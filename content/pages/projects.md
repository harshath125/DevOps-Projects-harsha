LEAD: Eleven projects, one rule: **you never write the application.** Each project starts by handing a specification to an AI coding agent, and your DevOps work begins when a finished repository appears on GitHub. That is deliberately the shape of a real job: someone else's code arrives, and you are responsible for it running safely, repeatedly, and visibly.

::: callout note WHY THE APPLICATION IS NOT YOURS TO WRITE
Because “build an app, then deploy an app” teaches you the wrong half of the job. In industry, a DevOps engineer joins a codebase they didn't author — a Spring Boot service with a Maven wrapper and a Flyway folder, a Django app with `gunicorn.conf.py`, a `.NET` solution with `appsettings.Production.json`. Your skill is *reading* it: finding the port, the dependency, the config, the health endpoint, the startup command, the secrets it expects — then building the pipeline, the infrastructure and the guardrails around it. Every project here trains exactly that, using your coding agent as the developer so you can do it eleven times without writing a line of application code.
:::

## The two parts of every project page

::: cols
::: col
**PART A — get the application**
1. **The business scenario** — what the software is for, and which non-functional demands follow from it.
2. **STEP 0 · Developer Agent Prompt** — a large, project-specific prompt you copy into Claude Code / Copilot / Cursor / Codex. It tells the agent exactly what to build, and it *forbids* DevOps work: no Terraform, no Docker or CI configuration, no cloud resources, no Jenkinsfile, no Kubernetes manifests.
3. **Developer handoff** — the agent produces the repo; you review it like a DevOps engineer, push to GitHub, and tag `v1.0.0`. The “dev” phase ends here.
:::
::: col
**PART B — the DevOps journey**
Clone → inspect the repo → identify dependencies, port and config → build → test → containerise → build image → push to a registry → create infrastructure (Terraform) → configure the cloud (network, LB, DB) → deploy → CI/CD → DevSecOps gates → monitoring → failure scenarios → troubleshooting → scaling → rollback → final architecture → what I learned → interview questions → résumé bullets → quick revision.
:::

**The handoff, in one line (it's the same line on every page, and it must become boring):**

::: flow
Developer → GitHub → YOU → git clone → inspect → build → test → Docker → Registry → Terraform → AWS / Azure → CI/CD → DevSecOps → Monitoring → Troubleshooting
:::

## Choose your project

::: cards
- [01 · MediBook](proj-01-healthcare.html) :: SPRING BOOT + POSTGRESQL :: Healthcare appointment booking on a Linux VM, then on AWS as a three-tier architecture. Your first full journey: systemd, Nginx, a real security group, RDS and a health-checked load balancer. :: Linux VM → AWS
- [02 · CartFlow](proj-02-ecommerce.html) :: NODE + MONGODB + REDIS :: E-commerce catalogue and orders, packaged with Docker and Compose first, then deployed to AWS with CI/CD. Where “works on my machine” dies. :: Docker → AWS + CI/CD
- [03 · LedgerPay](proj-03-banking.html) :: .NET + SQL SERVER :: Banking account service on an AWS three-tier stack built entirely with Terraform: VPC, RDS, IAM, encryption, drift alarms. :: AWS + Terraform
- [04 · StockPilot](proj-04-retail.html) :: DJANGO + CELERY + POSTGRES :: Retail inventory and reorder engine on ECR + ECS/Fargate — a real multi-stage image, scan-on-push, a task definition and zero long-lived keys. :: AWS ECR + Fargate + CI/CD
- [05 · NetSwitch](proj-05-telecom.html) :: FASTAPI + POSTGRES + REDIS :: Telecom provisioning API on Kubernetes (EKS): probes, PDBs, HPA, an Ingress, a stuck rollout and the rollback that fixes it. :: AWS + Kubernetes
- [06 · TallyWorks](proj-06-fintech.html) :: SPRING BOOT MICROSERVICES :: FinTech transactions and reporting: one monolith becomes a gateway plus two services; per-service pipelines, shared modules, DevSecOps gates on EKS. :: AWS EKS + CI/CD + DevSecOps
- [07 · CoverDesk](proj-07-insurance.html) :: NODE MICROSERVICES + MONGO :: Insurance policies and claims on Azure with Azure DevOps: service connections, environments with approvals, AKS, and the full scanning set. :: Azure + DevSecOps
- [08 · Runline](proj-08-saas.html) :: DJANGO + FASTAPI + POSTGRES :: Multi-tenant SaaS business-ops platform on AWS with Kubernetes and the observability stack: golden signals, SLOs, burn-rate alerts, one Grafana. :: AWS + K8s + Observability
- [09 · Northwind Grid](proj-09-multicloud.html) :: NODE + POSTGRES (TWO CLOUDS) :: One artefact deployed to AWS as primary and Azure as standby: identity translation, health-checked DNS, and a drift alarm that catches the quiet failure. :: AWS primary + Azure secondary
- [10 · OrbitShop](proj-10-capstone.html) :: NODE + REACT + POSTGRES + REDIS :: The capstone: GitOps, canary with Argo Rollouts, security gates, SLOs, cost guardrails, and a written architecture decision record. :: Capstone, AWS EKS
- [11 · Helios Switch](proj-11-mega.html) :: SPRING BOOT + FASTAPI + KAFKA :: The mega project: a platform, not a service — multi-environment, multi-team, Policy-as-Code, self-service golden paths, and the runbook you leave behind. :: Mega platform
:::

Plus two smaller, focused builds in GCP, because modern DevOps roles increasingly ask for them: [**Signal Yard — an AIOps project**](aiops.html) (telemetry → anomalies → labelled incidents on Pub/Sub + BigQuery) and [**FreshCast — a small MLOps project**](mlops.html) (a scikit-learn model treated as an artefact: built, scanned, versioned, promoted, monitored).

## The rules that make these projects worth doing

::: checklist
- [ ] **Do Part A first, and don't shortcut it by writing the app yourself.** Reviewing and *reading* someone else's repository is the muscle we're building; you also keep the “developer handed me this” story truthful in an interview.
- [ ] **One Git repository per project**, containing only your DevOps work: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `infra/`, `jenkins/` or `.github/workflows/`, `k8s/` or Helm charts, `scripts/`, `docs/RUNBOOK.md`. Never commit the application source into it.
- [ ] **A `docs/handoff.md` per project** — what the developer delivered, what you asked them to change, what you inherited and what you'd change next. This file is gold in an interview.
- [ ] **Break it on purpose at least twice per project.** A failed health check, a committed secret, a bad migration, an out-of-disk node, an image tag that doesn't exist. Recovery is the skill; the happy path is the warm-up.
- [ ] **Money first:** a budget alert before you create anything, and a `terraform destroy` at the end of every session. Then delete what the state didn't own: EIPs, NAT gateways, load balancers, snapshots, log groups.
- [ ] **Explain it out loud when you finish.** If you can't narrate the architecture for two minutes without notes, do the project again — faster.
:::

## What each project adds to your CV

| Project | The one sentence it earns you |
| :-- | :-- |
| 01 | “I can take a JAR and run it properly: systemd, Nginx, TLS, logs, alarms, backups.” |
| 02 | “I can containerise an unknown Node app and make Compose mirror production.” |
| 03 | “I build AWS networks from code, and my plans get reviewed rather than trusted.” |
| 04 | “I ship images: multi-stage, scanned, signed, immutable, deployed to Fargate.” |
| 05 | “I can debug a stuck rollout and roll back in under two minutes.” |
| 06 | “I split pipelines for microservices without splitting the standards.” |
| 07 | “I work in Azure and Azure DevOps, with gates and approvals that are enforced.” |
| 08 | “I define SLOs and my alerts page humans only when users are affected.” |
| 09 | “I can keep two clouds consistent, and I know where they diverge.” |
| 10 | “I design the whole path: GitOps, canary, policy, cost, and documentation.” |
| 11 | “I build platforms other teams can use safely without asking me.” |

## A note on what “done” means

A project is done when a stranger could clone your repo and, using only your `README.md`, bring the whole stack up and take it down; when a bad deploy is caught by a gate and not by a user; and when the failure you intentionally caused is now covered by an alarm and a runbook line. Not when the app opens in a browser. That distinction is the difference between a tutorial and an engineering portfolio — and it's the difference most candidates never notice.

Start with [Project 01 — MediBook →](proj-01-healthcare.html)
