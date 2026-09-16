LEAD: Here is the whole course in one page: what to study, in which order, how long each part takes, and — most importantly — how you decide you are ready for the next thing. Print it, tick it in your browser, and keep it next to the eleven projects.

## The two phases, and why the split exists

::: cols
::: col
**PHASE 1 — Core DevOps (3–5 weeks)**
Build the machine and make the change reach it, repeatably.

Linux · networking + shell · Git/GitHub · cloud concepts · AWS · Azure + GCP · Docker + Compose · Terraform · Jenkins + GitHub Actions · Kubernetes · observability · multicloud patterns

**Exit test:** you can take an unknown repository, containerise it, deploy it to your own VPC with Terraform, expose it behind a load balancer with a health check, watch it in a dashboard, and roll it back.
:::
::: col
**PHASE 2 — DevSecOps + platform (2–4 weeks)**
Make the unsafe path impossible, and make failure visible before users do.

Git security · secrets + least privilege · SAST · SCA · secret scanning · container + IaC scanning · DAST · runtime/K8s security · security gates (SonarQube, Trivy, Checkov, OWASP ZAP, GitHub security) · one AIOps project · one MLOps project

**Exit test:** your pipeline refuses to ship a critical vulnerability, a secret, or an open S3 bucket — and you can explain *why each gate exists* to a developer who is annoyed about it.
:::

## The order, with time and the gate you must pass

| # | Study | Pages | Hours | Move on when you can… |
| :-- | :-- | :-- | :-- | :-- |
| 1 | Linux + shell | [Linux](f-linux.html), [Networking + shell](f-networking.html) | 6 | diagnose a service that will not start using only `systemctl`, `journalctl`, `ls -l`, `ss`, `df` |
| 2 | Git + GitHub | [Git + GitHub](f-git-github.html) | 2.5 | clone, inspect, cut a release branch, tag it, and clean a secret out of history |
| 3 | Containers | [Docker + Compose](f-docker.html) | 3.5 | write a multi-stage Dockerfile for a repo you did not write, and explain its layer cache |
| 4 | **Project 01** | [MediBook on a Linux VM](proj-01-healthcare.html) | 6 | take a developer's repo from clone → running service on a VM, with Nginx in front |
| 5 | Cloud core + AWS | [Cloud core](p1-cloud-core.html), [AWS](p1-aws.html) | 8 | draw and defend a three-tier VPC: subnets, routes, SG sources, health checks |
| 6 | **Projects 02–03** | [CartFlow in Docker](proj-02-ecommerce.html), [LedgerPay + Terraform](proj-03-banking.html) | 14 | build, scan, push, deploy an image; make the same infra from code twice, then destroy it |
| 7 | Terraform | [Terraform](p1-terraform.html) | 4 | read a plan and spot the `# forces replacement` before it hurts anyone |
| 8 | Kubernetes | [Kubernetes](p1-kubernetes.html), [Project 05](proj-05-telecom.html) | 10 | deploy, expose, scale, roll back and debug `CrashLoopBackOff` without a search engine |
| 9 | CI/CD | [CI/CD](p1-cicd.html), [Project 04](proj-04-retail.html) | 9 | write a pipeline with build, test, scan, push, deploy, **verify** — and debug one that lies |
| 10 | Observability | [Observability](p1-observability.html), [Project 08](proj-08-saas.html) | 6 | turn “it's slow” into a PromQL query, an SLO and one useful alert |
| 11 | Azure + GCP | [Azure + GCP](p1-azure-gcp.html), [Multicloud](p1-multicloud.html) | 4 | explain the five structural differences and where each one bites |
| 12 | **Projects 06–07** | [TallyWorks on EKS](proj-06-fintech.html), [CoverDesk on Azure DevOps](proj-07-insurance.html) | 17 | microservices with per-service pipelines, security gates in both clouds |
| 13 | DevSecOps | [core](p2-devsecops-core.html), [secrets + IAM](p2-secrets-iam.html), [scanning + gates](p2-scanning.html), [runtime](p2-runtime-sec.html) | 12 | place every tool in the right stage and justify the threshold |
| 14 | **Projects 09–11** | [Multicloud](proj-09-multicloud.html), [Capstone](proj-10-capstone.html), [Mega](proj-11-mega.html) | 24 | design, secure, observe, document a production-shaped platform on two clouds |
| 15 | Career | [Interview](interview.html), [Scenarios](interview-scenarios.html), [Troubleshooting](troubleshoot.html), [Resume](resume.html) | 8 | answer 8 of 10 questions in your own words and defend a design on a whiteboard |

::: callout note Why the projects are interleaved, not left for the end
A tool learned in isolation is a tool you cannot use. Every module here is followed within one step by a project that *needs* it: Docker immediately followed by packaging a repository, Terraform immediately followed by rebuilding Project 03 from code, Kubernetes immediately followed by rolling back a bad deploy on a real cluster shape. If you read all the theory first, you will understand less and retain a third of it.
:::

## The comprehension gate (four questions, every single page)

Do not turn the page until you can answer these out loud, in simple English, without notes:

1. **What is it** and what problem does it remove? (one sentence)
2. **What command(s)** do I actually run, and what does the output prove?
3. **What goes wrong** in production, and what is my first check?
4. **Why** does it work that way? (the sentence a senior would accept)

If any of the four fails, re-read that section and re-do the lab step. This is the whole study method of this site — it is also precisely how interviews work.

## A weekly rhythm that works with a job or college

::: grid2
**Mon–Thu · 90 minutes.** 40 min reading one section, 40 min in the simulated terminal or on the real machine, 10 min writing the page's revision block in your own words.
**Fri · 45 minutes.** Break something on purpose in the lab (troubleshooting mode), then fix it. Write the symptom → cause → fix in your notes.
**Sat · 3 hours.** Project work only: the real cloud, the real repo. This is where the learning becomes yours.
**Sun · 30 minutes.** Tick the revision checklist, take the Test-Me quiz for the week, and plan Saturday's project step.
:::

::: callout why Why the weekend slot is project-only
Weekday slots build vocabulary in a safe environment; the weekend forces you to use it against reality, where the error messages are not scripted. People who skip the weekend part can explain Terraform for twenty minutes and then freeze when an apply fails.
:::

## Three shortcuts, depending on where you start

::: cards
- [Total beginner](f-linux.html) :: 0 HRS DEVOPS :: start at the top of the table; do not skip Linux — every later minute is cheaper if you can read a log, a permission string and a socket :: 6 weeks
- [I know Linux + Docker, new to cloud](p1-cloud-core.html) :: SKIP PHASE 1 PARTLY :: do Cloud core, AWS, Terraform, then straight into Project 03; skim the Linux and Docker pages only for their “production scenario” and “common mistakes” sections :: 3 weeks
- [Interview in 3 weeks](troubleshoot.html) :: REVISION-FIRST :: roadmap shortcut: [Revision sheets](revision.html) → [Troubleshooting playbook](troubleshoot.html) → [Interview bank](interview.html) → [Scenario rounds](interview-scenarios.html) → and one project end-to-end (03 or 05) so you have a real story to tell :: 3 weeks
:::

## What to write down as you go (the portfolio, not notes)

::: checklist
- [ ] One Git repository per project, containing the infra code, the Dockerfile, the pipeline file and a `docs/RUNBOOK.md` — even when you followed a page step by step.
- [ ] A `notes/differences.md` for every cloud-specific gotcha you hit. Six months of that is the best document in your portfolio.
- [ ] A screenshot or exported graph per project: dashboard, ALB healthy targets, `terraform plan` output, a passed pipeline. Evidence of a working system beats a list of tools.
- [ ] The “WHAT I LEARNED” block of each project page rewritten in your own words. That text becomes your résumé bullets and your interview answers.
- [ ] Time spent and what blocked you for more than 30 minutes. This is your real strength map — it beats any self-assessment.
:::

## Money, safety and the habit that protects you

::: callout warn Two rules that never change
1. **A budget alert before the first resource**, in every account you ever touch. Set $16 and let it fire once, so you know the alarm path works.
2. **`terraform destroy` at the end of every session** — and then look at the cost report to prove it was complete. Load balancers, NAT gateways, unattached Elastic IPs and long-retention log groups are the four things that bill you after “destroy”.
Break these twice and the course pays for itself in a way no certificate does.
:::

## Where each project lands

| Project | Environment | Stack (built by the coding agent, received by you) |
| :-- | :-- | :-- |
| [01 MediBook](proj-01-healthcare.html) | Linux VM → AWS | Spring Boot + PostgreSQL |
| [02 CartFlow](proj-02-ecommerce.html) | Docker + Compose | Node.js + MongoDB + Redis |
| [03 LedgerPay](proj-03-banking.html) | AWS three-tier + Terraform | .NET + SQL Server |
| [04 StockPilot](proj-04-retail.html) | AWS ECR + ECS/Fargate | Django + Celery + PostgreSQL |
| [05 NetSwitch](proj-05-telecom.html) | Kubernetes (EKS) | FastAPI + PostgreSQL + Redis |
| [06 TallyWorks](proj-06-fintech.html) | AWS EKS + CI/CD + DevSecOps | Spring Boot microservices + PostgreSQL |
| [07 CoverDesk](proj-07-insurance.html) | Azure + Azure DevOps + DevSecOps | Node.js microservices + MongoDB |
| [08 Runline](proj-08-saas.html) | AWS + Kubernetes + full observability | Django + FastAPI + PostgreSQL (multi-tenant) |
| [09 Northwind Grid](proj-09-multicloud.html) | AWS primary + Azure secondary | Node.js + PostgreSQL (two clouds, one artefact) |
| [10 OrbitShop](proj-10-capstone.html) | Capstone: AWS EKS, GitOps, security | Node.js + React + PostgreSQL + Redis |
| [11 Helios Switch](proj-11-mega.html) | Mega: full platform + GitOps + SLOs | Spring Boot + FastAPI + PostgreSQL + Redis |
| [AIOps](aiops.html) | GCP | telemetry + labelled incidents on Pub/Sub + BigQuery |
| [MLOps](mlops.html) | GCP | a scikit-learn model, packaged and promoted like any artefact |

::: callout note The one-sentence version of this roadmap
Learn Linux and Docker until they are boring, learn one cloud until it is yours, learn Kubernetes and a pipeline until you can debug both at speed, then make the whole thing secure and observable — and prove it eleven times on projects you did not have to write yourself.
:::

::: revision REVISION — the whole plan on one card
**Order:** Linux → shell/networking → Git → Docker → **Project 01** → cloud core → AWS → **Projects 02–03** → Terraform → Kubernetes → CI/CD → observability → **Projects 04–08** → Azure/GCP → DevSecOps → **Projects 09–11** → AIOps/MLOps → interview + resume.
**Gate:** four questions per page, out loud, no notes.
**Rhythm:** 90 min on weekdays (read · do · write), Friday break-it-on-purpose, Saturday project, Sunday revision + quiz.
**Shortcuts:** beginner = full path; Docker/Linux person = cloud + Terraform + 03; interview in 3 weeks = revision → troubleshoot → interview → scenarios → one project.
**Always on:** budget alert before creating, destroy at the end, evidence in the repo, notes in your own words.
:::

Start here: [Foundation — Linux →](f-linux.html)
