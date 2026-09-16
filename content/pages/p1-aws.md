LEAD: AWS in the order you will actually need it: identity, network, a server, an image, a load balancer, a database, and the pipes that keep them talking. This is the deepest module in Phase 1 because most of the projects here are AWS, and because AWS forces you to build the plumbing yourself — that pain is what makes the other clouds legible later.

::: flow
YOU + a laptop → `aws sso login` → IAM role → EC2 in a VPC → Dockerfile → ECR → ALB → Route 53 → RDS in private subnets → CloudWatch → (then Terraform builds all of it, and pipelines keep it true)
:::

## AWS accounts, IAM, and the CLI you'll live in (day 0)

| Object | What it really is | How to use it correctly |
| :-- | :-- | :-- |
| Root user | One per account, has every permission | MFA it, and generate **no access key, ever**. All other permissions come from IAM. |
| IAM user | A long-lived identity, usually a person or a legacy CI job | Prefer SSO or an OIDC role; a static access key in CI is a breach waiting to happen. |
| IAM role | Temporary credentials you assume; attached to EC2/ECS/Lambda/EKS pods | Default choice. `MaxSessionDuration` + MFA for humans. |
| Trust policy | *Who may* assume the role (`Principal`, `Condition`) | Scope it: `sts:AssumeRole` only to the pipeline role, and to `arn:aws:iam::ACC:role/cicd`. |
| Permission policy | *What the identity may do* | Start from an AWS-managed policy, then trim to one ARN; `Action` lists only used verbs. |
| Policy variables | `${aws:PrincipalOrgID}`, `aws:RequestedRegion` | Add a region deny so a misconfigured CLI profile can't deploy to another region. |
| Service-linked role | Auto-created (e.g. `AWSServiceRoleForElasticLoadBalancing`) | Don't hand-edit; know they exist so you stop hunting for them. |
| Organizations + SCP | Account tree; guardrails no one can remove | `ec2:CreateVpc` in member accounts only, deny root user actions, restrict regions. |

::: cols
::: col
**The day-0 CLI setup (repeat this every time you touch AWS)**
```bash
aws configure sso
#  sso_start_url  https://mycompany.awsapps.com/start
#  sso_region     eu-west-1
#  sso_account_id 123456789012
#  sso_role_name  LabAdmin
aws sso login --profile lab
aws sts get-caller-identity     # reflex, before any apply
```
:::
::: col
**Why every tool then works:** the CLI resolves `profile → env vars → ~/.aws/config → (instance profile on a box)`, then signs requests with SigV4. `AWS_PROFILE=prod aws s3 ls`, `AWS_REGION=eu-west-1`, and `--profile` per call are all you need in practice — the rest of “AWS CLI knowledge” is flag archaeology.
:::

## VPC: the network everyone asks about, built once with `aws` and then with Terraform

Target shape (memorise the *reasons*, not the numbers):

- VPC `10.0.0.0/16`; public `10.0.0.0/24` (az a), `10.0.1.0/24` (az b) · private-app `10.0.10.0/24`, `10.0.11.0/24` · private-data `10.0.20.0/24`, `10.0.21.0/24`.
- Public subnets get a route `0.0.0.0/0 → igw-…` and `map_public_ip_on_launch = true`. Private subnets get `0.0.0.0/0 → nat-…` (one NAT per AZ for real prod, one shared NAT for labs — the price difference is a lesson, not an accident).
- **One IGW** per VPC. A route table with an IGW target is “public” as a *property of the table*, not of the subnet. That's why “public subnet” confuses people: it's about the route.
- DNS: `enable_dns_support = true`, `enable_dns_hostnames = true`. Forget hostnames and your ALB targets get IPs nobody can name — “unhealthy” with no explanation.
- Flow logs to CloudWatch Logs or S3, retention 14 days for a lab, 90 for prod. They are the only way to prove a packet *wasn't* dropped by the app.
- `prefix list` endpoints for S3/DynamoDB and interface endpoints for ECR/Secrets Manager/SQS so image pulls never transit NAT.

```bash
# by hand, once, so Terraform means something later
VPC=$(aws ec2 create-vpc --cidr-block 10.0.0.0/16 \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=lab-vpc}]' \
  --query Vpc.VpcId --output text)
aws ec2 modify-vpc-attribute --vpc-id $VPC --enable-dns-support
aws ec2 modify-vpc-attribute --vpc-id $VPC --enable-dns-hostnames '{"Value":true}'
IGW=$(aws ec2 create-internet-gateway --query InternetGateway.InternetGatewayId --output text)
aws ec2 attach-internet-gateway --vpc-id $VPC --internet-gateway-id $IGW
RTB=$(aws ec2 create-route-table --vpc-id $VPC --query RouteTable.RouteTableId --output text)
aws ec2 create-route --route-table-id $RTB --destination-cidr-block 0.0.0.0/0 --gateway-id $IGW
SUB=$(aws ec2 create-subnet --vpc-id $VPC --cidr-block 10.0.0.0/24 \
  --availability-zone eu-west-1a --query Subnet.SubnetId --output text)
aws ec2 associate-route-table --route-table-id $RTB --subnet-id $SUB
```

### Subnets, route tables, IGW, NAT — in one paragraph each

- **Subnet** = a contiguous CIDR slice of the VPC in *one* AZ. AWS reserves 5 addresses (network, +1 router, +2 DNS/future, +1 reserved, broadcast), so a `/24` gives 251 usable — plan for that when you pick tiny sizes, and never overlap VPCs you might later peer.
- **Route table** = where traffic *leaves*. One per tier: public → IGW; private → NAT; isolated data → no internet route at all, only VPC endpoints. A subnet can't have two main tables, so “which table do I edit?” is nearly always the real bug.
- **IGW** = the VPC's door to the internet for *inbound-and-outbound* on instances that have public IPs.
- **NAT** = outbound-only for private subnets. Two per AZ in prod (it's $32/month per AZ in eu-west-1 — bill me for accuracy), one for a lab. NAT is a per-GB tollbooth, so heavy S3/ECR traffic goes through **endpoints** instead.
- **Egress-only IGW** = the IPv6 equivalent of a NAT gateway.

### Security groups (stateful) vs NACLs (stateless) — the pair that trips everyone

| | Security group | Network ACL |
| :-- | :-- | :-- |
| Attaches to | ENI/instance | Subnet |
| State | **Stateful** — reply traffic returns automatically | Stateless — you must open ephemeral return ports |
| Rules | Allow only | Allow **and** deny, lowest-number wins |
| Typical use | The main tool: app SG, db SG, lb SG, chained by reference | Rarely touched; a subnet-wide hard deny or a per-subnet WAF-ish rule |
| Failure you get | “I opened port 80 from anywhere” but the app SG still references the wrong source | A missing ephemeral range breaks outbound-initiated traffic in ways nobody can explain |

::: callout note THE SG-CHAINING PATTERN (worth knowing because it appears in every project)
Three SGs, each referencing the previous one: `sg-lb` allows 443 from `0.0.0.0/0`; `sg-app` allows 8080 **only from `sg-lb`** and 22 from `sg-bastion`; `sg-db` allows 5432 **only from `sg-app`**. Because the rules reference *source SG ids* rather than CIDRs, moving an instance or changing a subnet never weakens the chain — and nothing on the internet can reach the app or the database directly. In Terraform this is `aws_security_group_rule` with `source_security_group_id`, and it is the single clearest “this person has built production” detail on a résumé project.
:::

## EC2: right-size, then never SSH to fix what a pipeline should fix

- **AMI vs instance**: an AMI is an immutable image; an instance is a running copy. You bake changes into the AMI or install them at boot with a **launch template + user data**, not by hand on 6 boxes.
- **Instance families worth knowing**: `t3.micro`/`t3.small` (burstable, lab default, watch the CPU credits), `m7i`/`m6i` (general), `c7i` (compute, build agents), `r7g`/`r6g` (memory, JVMs), `t4g`/`m7g` (Graviton, ~20 % cheaper at equal size — always test the ARM image), `i4i` (local NVMe for DB/cache).
- **IMDSv2 mandatory** — v1 is SSRF-harvestable. `aws ec2 modify-instance-metadata-options --http-tokens required --http-endpoint enabled`.
- **EBS**: gp3 is the default choice. IOPS and throughput are provisioned *independently of size* (unlike gp2, where you bought capacity to buy IOPS), so a 30 GB gp3 volume has 3 000 IOPS. Snapshot before you resize.
- **Placement group** (cluster) for ultra-low latency between nodes; spread across AZs with `aws resourcegroupstaggingapi` for ownership. Skip unless you have a real reason.
- **Auto Scaling**: launch template (not the deprecated launch config) + ASG + target group attachment. Tune `DefaultCooldown`/`HealthCheckGracePeriod`, use `InstanceRefresh` with warm pools for patching, and a lifecycle hook to drain long requests. `terminate-instance-in-auto-scaling-group` is the way to *replace* an instance — killing it directly triggers an unwanted replace.
- **Systems Manager**: Session Manager for shell access with no port 22 open, Patch Manager for baselines, State Manager for desired config, Parameter Store for non-secret config (SecureString for secrets).
- **EFS** for shared POSIX config/assets across ASG instances; S3 for artefacts; FSx for Windows/NetApp needs.

**user data, the way production images do it** (cloud-init runs as root on first boot only — put secrets *nowhere* in it):

```bash
#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/user-data.log) 2>&1
dnf -y update-minimal --security
dnf -y install docker git
systemctl enable --now docker
usermod -aG docker ec2-user
printf '%s\n' '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' > /etc/docker/daemon.json
systemctl restart docker
aws s3 cp s3://config-bucket/medbook/prod.env /etc/medbook.env
docker run -d --name medbook --restart unless-stopped \
  --env-file /etc/medbook.env -p 8080:8080 \
  --memory 1g 123456789012.dkr.ecr.eu-west-1.amazonaws.com/medbook:v1.4.0
```
`set -euxo pipefail` + a log file is the entire difference between “it failed” and “it failed *at line 7, apt returned 100*”. And that's why we still put this in a pipeline instead: replace it with an AMI bake in Image Builder.

## S3 in anger: buckets, not “cloud folders”

| Concept | What to internalise |
| :-- | :-- |
| Keys and prefixes | There are **no folders**; `a/b/c.txt` is a key. “List” uses a delimiter, and 5 000-object paging needs continuation tokens. |
| Block Public Access | Account-, bucket- and object-level. Turn it **on** at the account; a bucket policy is not a fix if BPA blocks it. |
| Versioning + MFA delete | Turns “deleted” into “made invisible”; enables recovery and is the reason backups-on-S3 beat tape. |
| Lifecycle | `INTELLIGENT_TIERING` or `STANDARD_IA` after 30 days, `GLACIER_IR` for compliance, expire non-current after N days, abort multipart uploads after 7 (those fragments are billed and invisible in the console). |
| Encryption | SSE-S3 by default now; SSE-KMS with a customer-managed key when policy/audit demands, with `s3:x-amz-server-side-encryption-aws-kms-key-id` conditions. |
| Event notifications | `s3:ObjectCreated:*` → Lambda/SNS/SQS; needs a resource policy allowing `s3.amazonaws.com`, or events silently don't fire. |
| Presigned URLs | Client-side upload/download with an expiring signature; the right answer for a browser uploading an avatar without giving the browser AWS creds. |
| Transfer Acceleration / VPC endpoint | Acceleration for long-haul uploads; a gateway endpoint for free, private access from inside the VPC. |

```bash
aws s3api put-bucket-policy --bucket config-bucket --policy file://policy.json   # deny non-TLS, deny unencrypted puts
aws s3api get-public-access-block --bucket config-bucket
aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=config-bucket --max-results 5
```

## ECR, CodeBuild, CodePipeline — AWS's own CI/CD, briefly and honestly

- **ECR**: `create-repository --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE`, a lifecycle policy to keep the last 20 images, and an **endpoint policy** (`pl-xxxx`) if the registry is private. Pull auth = `aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com` (valid 12 hours). For EKS, the node's IAM role must have `ecr:GetAuthorizationToken` **and** `BatchCheckLayerAvailability/GetDownloadUrlForLayer/BatchGetImage` on that repo; otherwise pods say `no basic auth credentials`.
- **CodeBuild**: a `buildspec.yml` with `phases{install,pre_build,build,post_build}`; compute type `BUILD_GENERAL_MEDIUM` (4 CPU/8 GB) for anything Docker-in-Docker; `env.variables` for non-secrets and `env.privateRegistryCredentials`/Parameter Store for the rest; cache `type: LOCAL, modes: LOCAL_CACHE_DOCKER_LAYERS` for 12-minute Java builds cut to 3.
- **CodePipeline**: sources (CodeCommit/GitHub/S3), stages of actions, `artifactStore` in S3, and **deploy provider CodeDeploy** for EC2/on-prem (blue/green with an ALB target group swap) or ECS for containers. It does its own source polling; webhooks are a CodeConnection (for GitHub) or EventBridge rule. It has no built-in gate — the gate is a `ChangeManager`/manual approval action.

::: callout note WHY THIS SITE STILL TEACHES JENKINS AND GITHUB ACTIONS (one paragraph)
Because the job market does. You will spend more time writing a Jenkinsfile or `.github/workflows/ci.yml` than a `buildspec.yml`. Knowing CodeBuild/CodePipeline earns you the ability to read a pipeline someone else built, and gives you a **working deploy path with zero infrastructure to maintain** — which is exactly why Project 04's “simplest possible deploy” exists before Kubernetes appears.
:::

## ECR push/pull with real errors decoded

| Message you will see | Actual cause | Fix |
| :-- | :-- | :-- |
| `denied: Repository with name "medbook" does not exist` | Repo in another region/account, or the push role lacks `ecr:BatchCheckLayerAvailability` | Create it; grant the 4 read+push actions; check `AWS_REGION`. |
| `no basic auth credentials` (kubelet) | Node role missing ECR read perms, or token expired | Add `AmazonEC2ContainerRegistryReadOnly`; use EKS managed nodegroups or the ECR credential helper. |
| `Image does not exist` on `docker pull` | Tag typo / immutable-tag push rejected | Push, then describe-images to confirm; `IMMUTABLE` means CI must push a *new* tag, not overwrite. |
| `RequestError sending request: timeout` | Private registry in a private subnet with no VPC endpoint | Add ECR/Docker endpoint, or route via NAT. |
| Scan status `Failed` | Image > 4 GB, or scan-on-push quota exceeded | Enable enhanced scanning (Inspector) instead of basic scan-on-push. |

## ELB / ALB / NLB + Route 53 — where TLS lives and where health checks decide

**NLB** = L4, TCP/TLS passthrough, per-flow affinity, ultra-low latency, static IPs, great for gRPC or a DB proxy. **ALB** = L7, HTTP/HTTPS, path/host rules, per-request, target groups with health checks, WAF attachable, and the one that can do blue/green by shifting target-group weights.
**Route 53** = the DNS in front of both. Public → an A/AAAA **alias** record to the ALB's DNS name (never a CNAME at the zone apex — you can't alias to an A record and a CNAME can't coexist with the SOA/NS that every apex has). Internal → a private hosted zone bound to the VPC.

```bash
# create an ALB with a real health check (the part that gets faked in tutorials)
aws elbv2 create-load-balancer --name medbook-alb --type application \
  --subnets subnet-pub-a subnet-pub-b \
  --security-groups sg-lb \
  --load-balancer-attributes access_logs.enabled=false
TG=$(aws elbv2 create-target-group --name medbook-tg --protocol HTTP --port 8080 \
  --vpc-id $VPC --health-check-protocol HTTP --health-check-path /actuator/health \
  --health-check-interval-seconds 15 --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 --unhealthy-threshold-count 3 \
  --target-group-attributes 'stickiness.enabled=false' --query TargetGroups[0].TargetGroupArn --output text)
aws elbv2 create-listener --load-balancer-arn $LB --protocol HTTPS --port 443 \
  --certificates Arn=arn:aws:acm:eu-west-1:123456789012:certificate/xxxx \
  --default-actions Type=forward,TargetGroupArn=$TG
aws elbv2 modify-load-balancer-attributes --load-balancer-arn $LB \
  --attributes Key=idle_timeout.timeout_seconds,Value=60 Key=deletion_protection.enabled,Value=true
```

::: callout fix THE FIVE MOST COMMON “UNHEALTHY TARGET” CAUSES (in order of how often I've seen them)
1. **SG chain**: `sg-app` allows 8080 from `sg-lb` — but the health check is sent *from the ALB's ENIs in the target's AZ*; if the target is in a subnet the ALB isn't in, or you allowed the wrong SG id, you get timeouts, not refusals.
2. **Path returns 401/403/500** — the health check needs an unauthenticated endpoint (`/actuator/health` with `management.endpoint.health.probes.enabled=true` → `/actuator/health/liveness`, `/readiness`) or a matcher like `http_200-399`.
3. **Timeout too short**: cold JVM starts in 45 s; give `health-check-interval-seconds 15`, `healthy-threshold-count 2` and a **grace period on the ASG** of 120–180 s so the instance isn't replaced while still booting.
4. **4xx on HTTPS probe / HTTP target**: the ALB talks HTTP:8080 to targets while `server.servlet.context-path` is `/api`, so the probe hits `/health` not `/api/health`.
5. **Target group has no targets** — the ASG never attached, because you attached the *old* target group ARN. `describe-orphaned-target-groups` finds these.
:::

## RDS/Aurora and ElastiCache: what “managed” does not do for you

```bash
aws rds create-db-subnet-group --db-subnet-group-name medbook-data \
  --db-subnet-group-description "private data tier" \
  --subnet-ids subnet-priv-data-a subnet-priv-data-b
aws rds create-parameter-group --db-parameter-group-name medbook-pg16 \
  --db-parameter-group-family postgres16 --description "tuned"
aws rds modify-db-parameter-group --db-parameter-group-name medbook-pg16 --parameters \
  ParameterName=log_min_duration_statement,ParameterValue=500,ApplyMethod=pending-reboot \
  ParameterName=idle_in_transaction_session_timeout,ParameterValue=60000,ApplyMethod=pending-reboot
```

- **Multi-AZ** = a synchronous standby in another AZ with the *same DNS name*; failover takes 60–120 s of reconnect pain and no manual re-pointing. Multi-AZ **readable** costs 2× for a replica you can query; plain Multi-AZ standby is not readable.
- **Read replicas** are asynchronous: fine for reports, wrong for “read my write immediately”. Promoting a replica is a *few-minute* failover, not zero.
- **Backups**: `BackupRetentionPeriod` (1–35), automated daily snapshots + WAL for point-in-time recovery to any second in that window; snapshot *before* major changes; `aws rds restore-db-instance-to-point-in-time --restore-time …` is a drill to run once, in a lab, so you know the syntax when you need it at 3 a.m.
- **Maintenance window**: patching happens there; `--apply-immediately` on a parameter change that needs a reboot means a planned outage *now* instead of at 04:00 — that's a change-advisory decision, not a flag.
- **Secrets**: RDS proxy + Secrets Manager rotation (`rds` managed rotation runs every 30 days, double-AWS-password-length rules apply), or IAM database auth. Never `master_username` in a Terraform string, and never a snapshot shared publicly.
- **Connection budget**: a `db.t4g.medium` has ~1 200 max connections at 2 GB; Postgres forks a process per connection, so an app pool of 50 × 20 pods = 1 000 → you are at the wall. PgBouncer/RDS Proxy in front is *architecture*, not tuning.
- **ElastiCache**: Redis for cache-aside with a TTL; `--engine redis` with `AutomaticFailoverEnabled=true` needs ≥2 replicas; eviction policy `allkeys-lru` for a cache, `noeviction` if you store state; and 0.0.0.0-free by using a private subnet group + `redis-auth-token` or IAM auth.

## CloudWatch: the logs, metrics and alarms that make the box explainable

| Primitive | What it is | Gotcha you'll hit |
| :-- | :-- | :-- |
| **Logs** | Log groups → streams; JSON search (`fields @timestamp,@message | filter @message like /ERROR/`) | Ingestion is billed; retention **never expires by default** — set 30 days. |
| **Metrics** | Namespaces: `AWS/EC2`, `AWS/ApplicationELB`, `AWS/RDS`, custom via `aws cloudwatch put-metric-data` | `CPUUtilization` is 5-min (1-min with detailed monitoring, paid). A 60 s alarm on 5-min data never fires on the spike you care about. |
| **Alarms** | Threshold or anomaly; `OK/ALARM/INSUFFICIENT_DATA` | **INSUFFICIENT_DATA means “no data”** — usually a typo in a dimension. And `treat-missing-data` decides whether that means “bad”. |
| **Dashboards** | `cw put-dashboard --dashboard-file file://db.json` (widgets as JSON) | Build it from JSON in Git so the team can review it. |
| **EMF / Container Insights** | Structured logs that emit metrics for free | The right way to get per-endpoint latency without a code change from the developer. |
| **Contributor Insights** | Finds the busiest target/trace on an ALB | Turn on for prod ALBs; it's the fastest “which pod?” answer available. |
| **EventBridge** | Rules → targets (Lambda, SNS, SQS, Step Functions); schedules | `aws ec2 describe-instances` events need the right detail-type; schedules replace crons on boxes. |
| **SNS** | Topics + subscriptions (email, SMS, Lambda, SQS) | Alarms can't go to email; they go to SNS which goes to email. And alarms only fire on *transitions* — a re-arm on an active condition isn't a new alert. |

::: callout note THE ALARM SET THAT COVERS 90 % OF AN APP (from Project 01's own list, generalised)
`CPUUtilization > 85` for 3× 5 min · `StatusCheckFailed` (system or instance) for 2× 1 min · `MemoryUtilization > 90` (needs the agent) · `DiskSpaceUtilization > 85` · `HTTPCode_Target_5XX_Count > 1 %` of `RequestCount` · `TargetResponseTime p99 > 2 s` · `SurgeQueueLength > 0` (an ALB that can't reach targets — a canary for “everything is broken” that fires before users notice) · RDS `FreeableMemory < 256 MB` and `FreeLocalStorage < 15 %` · `BurstBalance < 20 %` on t-class (CPU credits running out = a cliff) · ASG `GroupDesiredCapacity != GroupInServiceInstances`. Every one of those has an owner and a runbook line, and that's the difference between an alarm and a pager culture.
:::

## Production scenario — the app is slow and the ALB is healthy

`curl` from the bastion is 40 ms; users report 4 s. The ALB metrics are fine.

1. **Split the timeline**: ALB `TargetResponseTime` (app time) vs `ClientTLSNegotiationErrorCount` vs CloudFront/R53. If the ALB is fast and the client is slow, the problem is between the client and the ALB (DNS, TLS, internet path, or an origin in another region) — not the app. Here `TargetResponseTime p99` was 3.6 s: the app is slow.
2. **Is it capacity or code?** `CPUUtilization` per target is 30 %. Not saturation.
3. **RDS** `ReadIOPS` is pinned and `FreeableMemory` is 190 MB. `log_min_duration_statement` shows a 3.2 s query.
4. Cause: a new release added `ORDER BY created_at DESC` on a 12 M-row table with no index; and the app opens a new connection per request, so RDS was at 950/1 200.
5. Fix forward: index (`CREATE INDEX CONCURRENTLY`), pool via RDS Proxy, then a **guardrail**: the CI job runs `pg_stat_statements` top-10 on a seeded staging DB and fails if any query > 500 ms.
6. Prevent: an alarm on p99 latency (not average) plus one on `DatabaseConnections`, and load test in CI with `k6 run --vus 50 --duration 3m` before first deploy.

Interviewers love this story because the fix was “index + pool + alarm” — three layers, only one of which is code.

## Common mistakes

::: checklist
- [ ] Root user access keys; an IAM user with `*:*`; a CI job with admin because “it kept failing”.
- [ ] No VPC endpoints → every image pull and log upload pays for NAT twice (throughput and money).
- [ ] `user data` holding DB passwords; S3 “temp” buckets with logs world-listable.
- [ ] Public subnet for the database, or a data subnet with `0.0.0.0/0` — because “the app needed to install packages” (that's what a NAT or endpoint is for; the DB needs neither).
- [ ] An ASG with no ELB attached (health checks from EC2 only) so unhealthy instances never rotate; `min=max=desired` with no headroom; grace period shorter than app boot.
- [ ] RDS in one AZ for a “production” app, backups disabled to save $0.095/GB-month.
- [ ] `deletion protection` off on ALB and RDS; `FinalDBSnapshotIdentifier` missing so `terraform destroy` deletes the database without a snapshot.
- [ ] CloudWatch retention `Never expire`, and zero alarms because “we have a dashboard”.
- [ ] Hard-coded AMI ids in Terraform, so the image ages out and the ASG can't launch a replacement; or SSM parameter `aws/service/ami-amazon-linux-latest/al2023- kernel-x86_64-hvm` — which is the fix.
:::

## Best practices

::: grid2
**Least privilege, expressed as role count.** Three roles per project: `ci` (can push images and plan/apply), `app` (read its own Parameters/Secrets, write its own log group), `human` (read-mostly, break-glass via MFA).
**Use SSM everywhere humans touch.** Session Manager instead of port 22; Parameter Store/Secrets Manager instead of files; Patch Manager so baselines are visible; `ssm send-command` for a fleet change instead of a `for` loop of SSH.
**Alarms on symptoms, not causes.** Page on latency, error rate and saturation (the user-visible triad); dashboards for CPU, queue depth and memory.
**Drift is an enemy you can schedule.** `terraform plan` in CI nightly, with a Slack message when it's non-empty; Config rules for “no public S3”, “RDS encrypted”, “no SG open to world”.
:::

## Interview answer

::: callout aha “Walk me through how your AWS deployment works.”
“I start at the edge and go inward. DNS is a Route 53 alias for `app.example.com` pointing at an ALB in two public subnets, TLS terminated on the ALB from an ACM certificate. The ALB forwards to a target group whose health check is `/actuator/health/liveness` every 15 s with 2 healthy / 3 unhealthy thresholds. Targets are an Auto Scaling group, 2–4 instances across two AZs, launched from a launch template with a pinned SSM-resolved AMI, IMDSv2 required, and an instance role that can pull from ECR and write to its log group — that's the only way the app gets credentials; there are no access keys anywhere. Config comes from Parameter Store and Secrets Manager, not from files. App code and image are built only in the pipeline: the image is scanned on push and tags are immutable, so a deploy is a tag change. The database is RDS Postgres Multi-AZ in private subnets with a security group that only allows 5432 from the app SG, backups to S3 retained 7 days with KMS, and RDS Proxy in front because our pools were larger than the connection budget. Observability is CloudWatch: structured logs with 30-day retention, p99 latency and 5xx-ratio alarms into SNS, and one dashboard in the repo. Rollback is retagging the previous image and letting a rollout replace pods — I rehearse it in staging before I claim it works.”
:::

::: grid2
**Follow-up: “How do you debug a 502 from the ALB?”** → correlate by timestamp: ALB access log (status 502 + target), target health history, app logs at that second, and whether the app closed the connection (idle timeout shorter than the ALB's 60 s), or a deploy restarted the process mid-request.
**Follow-up: “Why immutable tags and a new tag per build?”** → a rollback must be an unambiguous pointer to a known-good artefact; overwriting a tag makes “what is running?” unanswerable.
**Follow-up: “How do you patch 40 instances with zero downtime?”** → bake a new AMI, ASG instance refresh (min healthy 80 %, 5 min between batches), or CodeDeploy blue/green on a second target group and move ALB weights.
**Follow-up: “Your S3 bucket leaked. Now what?”** → rotate every credential that touched it, delete the public policy, enable BPA at the account, add a Config rule + SCP, and turn on access logs/CloudTrail data events so the next incident has evidence.
:::

## Virtual lab — deploy on AWS without touching the console (simulated terminal)

::: lab aws-1
:::

## Commands to remember

::: grid2
```bash
aws sts get-caller-identity
aws ec2 describe-vpcs --filters Name=isDefaultVolume,Values=true
aws ec2 describe-instances --filters Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].[InstanceId,PrivateIpAddress,Tags[?Key==`Name`].Value|[0]]'
```
```bash
aws elbv2 describe-target-health --target-group-arn $TG
aws cloudwatch get-metric-data --metric-data-queries file://q.json \
  --start-time -PT2H --end-time now
aws s3api get-bucket-policy-status --bucket config-bucket
aws rds describe-db-instances --db-instance-identifier medbook \
  --query 'DBInstances[0].[MultiAZ,BackupRetentionPeriod,PubliclyAccessible]'
```
:::

::: callout note WHAT I MUST REMEMBER
1. SGs are stateful and per-instance; NACLs are stateless and per-subnet.
2. A public subnet is a property of its route table (the IGW route), not of a checkbox.
3. No long-lived credentials: IAM roles for compute, OIDC for CI, SSO for humans.
4. Health check → the only thing that makes a load balancer useful; an alarm → the only thing that makes a dashboard useful.
5. Destroy, and delete the five things Terraform may not: EIPs, NAT gateways, LBs, snapshots, log groups.
:::

::: revision REVISION — 5 minutes
**Concepts:** SSO/SigV4/profiles · role vs user · trust vs permission policies · VPC/subnet/route-table/IGW/NAT/endpoint model · SG chaining · AMI + launch template + ASG + instance refresh · IMDSv2 · EBS gp3 IOPS vs size · S3 keys/prefixes/versioning/lifecycle/BPA/presigned · ECR tokens 12 h + immutable tags + scan on push · ALB vs NLB and target-group health checks · RDS Multi-AZ vs replicas, backups/PITR, maintenance window, connection budget · CloudWatch logs/metrics/alarms/INSUFFICIENT_DATA · EventBridge rules and schedules · ACM + DNS validation.
**Architecture to remember:** users → Route 53 → ALB in public subnets → target group → ASG in private-app subnets → app SG → RDS SG in private-data subnets, with NAT/VPC endpoints for egress and CloudWatch/parameter stores as the sidecar plane.
**Common mistakes:** admin for CI · keys in user data · no endpoints → NAT bill · RDS public or backup-less · grace period shorter than boot · retention never expire · hand-edited prod.
**Troubleshooting checklist:** `get-caller-identity` → region → `describe-target-health` → target SG sources → health path and matcher → `SurgeQueueLength` → RDS connections and IOPS → `log-opts` on the ASG → `aws cloudtrail lookup-events` for who changed what.
:::

Next: [Azure + GCP →](p1-azure-gcp.html)
