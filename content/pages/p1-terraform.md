LEAD: Terraform is not “cloud in a text file”. It is a **diff engine with a safety pin**: it remembers what exists, you describe what should exist, and it prints the gap. Everything good about infrastructure as code — review, reuse, rollback, drift detection — comes from that one mechanism. Learn to read the plan and the state and you can work in any cloud.

::: flow
your .tf files (desired state) + terraform.tfstate (what exists, from the last apply) → `terraform plan` (the diff) → review → `apply` → provider API calls, one dependency edge at a time → refreshed state file → next run diffs against reality again
:::

## The mental model, in six sentences

Terraform builds a graph of your **resources**; each node knows its provider, its type and its config. On `plan` it **refreshes** (reads live API state into memory), compares with what's recorded, then computes an ordered set of create / update / **replace** actions. Because that diff needs a reliable record of “what I own”, state is not optional — it's the product Terraform actually maintains, and it's why concurrent runs must be serialised with a lock. Providers are plugins with their own version numbers, which is why lock files are committed. Once you accept “Terraform is a diff tool that owns its inventory”, everything else — modules, workspaces, remote backends, import blocks, `moved` blocks, `terraform plan -out` — is bookkeeping around that one idea.

## Every command you need, in the order you'll meet them

```bash
terraform init                    # download providers into .terraform/, honour .terraform.lock.hcl
terraform validate                # syntax + types, offline; runs in CI before anything touches the cloud
terraform fmt -recursive -check   # the diff a reviewer should never have to comment on
terraform plan -out=tfplan        # the artefact you show a human
terraform show tfplan | less
terraform apply tfplan            # apply EXACTLY what was reviewed — this pairing is the whole point
terraform state list
terraform import 'aws_instance.app' i-0abc123     # adopt one existing resource
terraform refresh                 # state vs reality, no changes
terraform destroy -target=aws_cloudfront_distribution.cdn   # targeted teardown, double-check the plan
```

::: callout note THE TWO-STEP `plan` → `apply tfplan` RULE
`terraform plan && terraform apply` looks safe and isn't: someone (or an autoscaler, or the console) changes the world between those two runs, and the second plan — the one nobody read — is applied. In CI you always do `plan -out=tfplan` (artefact it, comment the diff on the PR) and `apply tfplan` in the next job, so what gets created is literally what was reviewed. Add `-detailed-exitcode`: exit 2 means “changes pending”, which lets a pipeline fail if nobody approved the plan.
:::

## The HCL you will actually write

```hcl
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
  }
  backend "s3" {                      # static values only: no variables here
    bucket         = "acme-tf-state"
    key            = "medbook/prod.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "acme-tf-locks"  # the lock that prevents two applies at once
    encrypt        = true
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { App = "medbook", Env = terraform.workspace, ManagedBy = "terraform" } }
}

variable "db_instance_class" {
  type        = string
  description = "RDS instance class; keep small in dev, see AWS pricing for prod"
  default     = "db.t4g.micro"
  validation {
    condition     = can(regex("^db\\.(t4g|m7g|r7g)\\.", var.db_instance_class))
    error_message = "Use a burstable or Graviton class so costs stay predictable."
  }
}

variable "cidr_public" { type = list(string) }   # no default: force the caller to decide

locals {
  name_prefix = "${var.project}-${terraform.workspace}"
  all_subnets = zipmap(range(length(var.azs)), [for i, az in var.azs : cidrsubnet(var.vpc_cidr, 4, i)])
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_security_group" "app" {
  name_prefix = "${local.name_prefix}-app-"
  vpc_id      = aws_vpc.main.id
  description = "App tier"
  ingress { from_port = 8080, to_port = 8080, protocol = "tcp", description = "only from the ALB SG",
            security_groups = [aws_security_group.alb.id] }
  egress { from_port = 0, to_port = 0, protocol = "-1", cidr_blocks = ["0.0.0.0/0"], description = "egress for image pulls and APIs" }
  lifecycle { create_before_destroy = true }        # required with name_prefix
}

resource "aws_instance" "app" {
  for_each                    = { for i, az in var.azs : az => { az = az, subnet = values(local.all_subnets)[i] } }
  ami                         = data.aws_ssm_parameter.al2023.non_encoded_value
  instance_type               = var.instance_type
  subnet_id                   = each.value.subnet
  key_name                    = var.key_name
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.app.name
  metadata_options { http_tokens = "required" }    # IMDSv2
  root_block_device { volume_size = 30, volume_type = "gp3", encrypted = true }
  tags = { Name = "${local.name_prefix}-app-${each.key}" }
  depends_on = [aws_security_group.app]            # almost never needed; usually a smell
}

output "ssh_command" {
  value = "aws ssm start-session --target ${values(aws_instance.app)[0].id}"
}
```

::: cols
::: col
**The six constructs, and the trap in each**
- `variable` — typed, `nullable`, `validation`, `sensitive = true` (only hides it in output; it's still in state).
- `locals` — DRY without a module. `for`/`for_each`/`[for …]`/`zipmap`/`try()`.
- `resource` — args = desired config; exported attributes = what you can reference.
- `data` — read-only lookup from the provider; the clean way to avoid hard-coded ids.
- `output` — the module's contract; `sensitive`, `description`, and only what the caller needs.
- `module` — composition with an explicit interface; a version pinned in the source address.
:::
::: col
**`count` vs `for_each` — the rule that prevents an outage**
`count = 3` → resources keyed by index. Delete list item 1 and Terraform *destroys and recreates* items 2 and 3 to keep indexes contiguous: on a database or an ENI, that is a production incident.
`for_each = toset(var.subnets)` → keyed by value; removing an entry destroys **only that one**.
Use `for_each` whenever the collection can change order or lose members; use `count` only for a fixed number, and only with `count.index`-stable semantics. If you must switch, use a `moved` block — don't destroy.
:::

## `terraform.tfstate`: the file you must respect

State holds **every attribute of every managed resource** — including DB passwords, private keys and tokens — in plain JSON, locally or in the backend. That single fact explains all the rules:

| Rule | Why |
| :-- | :-- |
| Never commit state | Secrets + drift + two people fighting over one file. `.gitignore` it; commit `.terraform.lock.hcl` instead. |
| Remote backend, encrypted, versioned | S3 + `encrypt = true` + versioning + DynamoDB lock (or GCS + object versioning + a lock object; or Terraform Cloud/Enterprise, which also gives run UIs, policy-as-code and ephemeral runners). |
| No `terraform.tfvars` with secrets | `-var`, `-var-file` outside Git, SSM/env injection, or TF Cloud variable sets marked sensitive. |
| `terraform state` subcommands for surgery | `mv`, `rm`, `replace-provider`, `pull`/`push` (last two only in emergencies, with a backup copy). |
| Read it when debugging | `terraform state show aws_instance.app`, `terraform show -json` for policy tooling. |

::: callout warn IF YOU USE S3 AS A BACKEND, DO THE BORING THING RIGHT
Versioned bucket + SSE-KMS + public access blocked + a bucket policy that denies anything but TLS and the CI role + `dynamodb_table` for locking + a lifecycle rule to expire old non-current versions + **state files are also your blast radius**: `terraform destroy` on a 4 MB JSON in a wrong account can delete everything, so lock writes to humans (a `apply` runs from a pipeline, not a laptop) and enable CloudTrail data events for the state key so `who touched prod state` has an answer. Also: never `terraform force-unlock` while a run may still be live. And if the DynamoDB table disappears, the lock silently degrades to nothing — which is how “state was overwritten” stories start.
:::

### `terraform import` and `import` blocks (the two ways to adopt reality)

```hcl
# Terraform 1.5+: generate code from the live object, no hand-written skeleton
import {
  id     = "i-0abc123"
  to     = aws_instance.app
}
resource "aws_instance" "app" {
  # config copied from `terraform plan -generate-config-out=gen.tf`
}
```

```bash
terraform plan -generate-config-out=gen.tf   # 1 · produce config from the live resource
terraform plan                                # 2 · read gen.tf: the diff must now be EMPTY or small
terraform state list                          # 3 · confirm the address
```
Order matters: **write the resource block to match reality first**, then import; otherwise the import “succeeds” and the next plan destroys the resource because your empty config disagrees with the world. And for modules, the address includes the module path: `aws_kms_key.this[0]` inside `module.kms` becomes `module.kms["prod"].aws_kms_key.this[0]` — get that wrong and `import` says “resource not found”.

## Drift, lock files and versions: the three that make a team safe

```bash
# nightly, in CI — the alert that saves the 2 a.m. “who changed the SG?” call
terraform plan -detailed-exitcode
#   0 = no changes · 2 = drift (or a real change waiting) · 1 = error
terraform providers lock -platform=linux_amd64 -platform=darwin_arm64   # reproducible across laptops/CI
terraform init -upgrade   # only when you *decide* to move a provider version
```

::: grid2
**Why `.terraform.lock.hcl` is committed:** it pins provider versions + checksums per platform. Without it, a provider release with a renamed attribute or a new default (`aws_vpc`'s `assign_generate_mac` change, `azurerm_subnet`'s enforcement toggles) silently changes semantics for everyone at once.
**Why `required_version` matters:** state format and function behaviour change across minor versions (1.0 → 1.9 added `moved`, changed `import` blocks and how `optional()` works), and your backend may be written by a newer Terraform than your CI runs — a downgrade is how you “lose” resources from state.
**Why drift matters more than “clean plans”:** the goal is not to *prevent* manual change (you can't), it's to make it *visible within one night* so it gets folded back into code — or reverted deliberately.
:::

## Modules: reusable, versioned, and worth the ceremony

```
infra/
├── modules/
│   ├── vpc/            # network only: vpc, subnets, route tables, IGW/NAT, flow logs, endpoints
│   │   ├── main.tf  variables.tf  outputs.tf  README.md  versions.tf  examples/
│   ├── iam-role/       # assume-role policy + tags, one opinion, small surface
│   └── app-service/    # ALB + target group + listener + ASG + SG + CloudWatch log group
└── envs/
    ├── dev/     # vpc + iam-role + app-service, 2 nodes, t3.small
    └── prod/    # same three modules, 4 nodes, m6i.large, RDS Proxy on
```

```bash
# how a consumer pins a module — the part most tutorials leave out
module "vpc" {
  source     = "git::ssh://git@github.com/acme/infra.git//modules/vpc?ref=v1.4.2"
  cidr       = "10.0.0.0/16"
  az_count   = 2
}
```

**Rules that come from pain:** a module needs an output for everything a caller reasonably needs (ids, ARNs, the security-group id, the DNS name); inputs get sensible defaults but **no** credentials; anything a caller may want to override (`instance_type`, `min_size`, `enable_cross_zone`) is an input, and anything that must never be overridden (`encrypted = true`) is not; `versions.tf` declares the provider range so a module doesn't explode on a version mismatch; and a module's `README.md` says which cloud assumptions are baked in, because “VPC module” with an AWS-only `data aws_availability_zones` is a lie when Azure comes.

**When NOT to write a module:** one use, or two uses of something you're still figuring out. Copy twice, abstract on the third — premature modules become a `var.flag_for_everything` monster that no one dares change. Also: don't module-wrap every resource 1:1 (a “`aws_s3_bucket` module” with 30 pass-through variables adds no information).

## Workspaces vs directories vs stacks

| Approach | Isolation | State | CI | Verdict |
| :-- | :-- | :-- | :-- | :-- |
| `terraform workspace new prod` | same config, different state key | `prod.tfstate` in the same backend prefix | needs `terraform workspace select` in the pipeline; one bad `apply -workspace=prod` from a laptop is a prod change | fine for dev/qa/stage; avoid for prod vs dev |
| Directory per environment (`envs/prod`) | separate root modules, separate backends, often separate accounts | own `key` | one pipeline per directory, approval on prod only | **the default for real teams** |
| Terraform Cloud/Enterprise stacks | explicit VCS-driven runs, policy, ephemeral runs | managed | plan/apply split with UI approval and `remote-state` data sources between stacks | best when you have the budget |

**Cross-environment references** should use the `remote_state_data_sources`/`terraform_remote_state` read of a *non-sensitive* output (VPC id, SG id), never a hand-copied string. If you find yourself reading `prod.tfstate` to get an ARN, you've created a coupling that will break during a state migration; the clean version is an SSM parameter or an export written by the prod stack.

::: callout fix DEPENDENCY / REMOTE-STATE PATTERNS THAT DON'T BIT BACK
`terraform_remote_state` reads only **outputs** of another root module's state — so the network team must export `vpc_id`, `private_subnet_ids`, `alb_sg_id` as outputs and versions them with the module. Do **not** read `state show` from a script or parse JSON with `jq` — that turns an internal file format into an interface. If the state lives in S3, restrict the read to the CI role, and if you have Terraform Cloud, use `tfe_outputs` instead; it doesn't hand out the whole state document.
:::

## CI: plan on PRs, apply on merge — the safe pattern

```yaml
# .github/workflows/terraform.yml (abridged — the full file is in Project 03 and 09)
name: terraform
on:
  pull_request: { paths: ['infra/**', '.github/workflows/terraform.yml'] }
  push: { branches: [main], paths: ['infra/**'] }
permissions: { id-token: write, contents: read, pull-requests: write }   # OIDC, no stored AWS keys
jobs:
  plan-apply:
    runs-on: ubuntu-latest
    environment: ${{ github.event_name == 'push' && 'prod-infra' || '' }}
    defaults: { run: { working-directory: infra/envs/prod } }
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with: { terraform_wrapper: false }        # needed for -detailed-exitcode parsing
      - run: terraform init -input=false -lock=true
      - run: terraform fmt -check -recursive && terraform validate
      - name: Plan
        run: terraform plan -input=false -out=tfplan -detailed-exitcode
        id: plan
        continue-on-error: true                   # exit 2 = changes, and we want to comment
      - uses: actions/upload-artifact@v4
        with: { name: tfplan, path: tfplan, retention-days: 1 }
      - name: Comment diff on PR
        if: github.event_name == 'pull_request'
        run: terraform show -no-color tfplan > plan.txt; gh pr comment "$PR" --body-file plan.txt
      - name: Security gates
        run: |
          tfsec ./ --tfvars-file terraform.tfvars   # or: checkov -d . -o json > checkov.json
          tfsec-check() { test "$(jq -r '.results | length' <(tfsec --format json .))" = "0"; }
      - name: Apply
        if: github.event_name == 'push' && steps.plan.outcome == '2'
        run: |
          gh run download -n tfplan .            # the EXACT plan humans reviewed
          terraform apply -input=false tfplan
```

**What this buys:** no cloud credentials at plan time for humans; a reviewed diff as the unit of change; formatting and validation as a *build* failure not a comment; and a security scanner whose findings block a merge. The three pipeline mistakes that ruin it: applying without the plan file, `terraform apply -auto-approve` on a push to `main` (so any merge can destroy prod), and running the same state from two workflows (locks collide and one run fails with `Error acquiring the state lock`, which people “fix” by removing the lock table).

## Production scenario — `destroy` ran because of a rename, and the fix is a 3-line file

A developer “tidied” `aws_db_instance.main` into `aws_db_instance.core` and the plan said:

```
  # aws_db_instance.main must be replaced
-/+ resource "aws_db_instance" "main" { ... }   # forces replacement
Plan: 1 to add, 0 to change, 1 to destroy.
```

Because a rename is, to Terraform, a new resource plus a removed one, and the removed one is **destroyed**. The right fix is not “don't rename” (real projects refactor constantly) — it's `moved`:

```hcl
# refactors.tf — read by the next plan, applied to state, no API call
moved {
  from = aws_db_instance.main
  to   = aws_db_instance.core
}
moved {
  from = module.app.aws_security_group.this
  to   = aws_security_group.app            # un-nesting a module
}
import { id = "prod-db"  to = aws_db_instance.core }   # and adopt the one created by hand last quarter
```

Plan output after a correct `moved` block: `~ 1 to move` and **`0 to add, 0 to change, 0 to destroy`**. Reviewers should be trained to hunt for exactly that line.

Other war stories, each with its one-line fix:

| Failure | The plan/apply said | Fix |
| :-- | :-- | :-- |
| Provider upgrade deleted a resource | `# forces replacement` on a subnet because `map_public_ip_on_launch` changed meaning | Pin provider versions; upgrade in a PR whose *only* job is the upgrade; read the changelog before merging. |
| “Lost” resource, apply errors | `Error: Cannot import non-existent remote object` / `InvalidGroup.NotFound` | State drifted from reality (someone deleted in the console). `terraform refresh`, decide whether reality or code wins, fold into code. |
| Apply half-completed, state locked | `Error acquiring the state lock ... ConditionalRequestFailed` | Someone's run is still live or crashed. Check the lock owner in DynamoDB; only then `terraform force-unlock <ID>`. Never in the middle of an unknown run. |
| Two applies in parallel corrupted inventory | `CodeSnapshotCreateTime` mismatch, phantom diffs forever | One apply per environment at a time: locking + a pipeline concurrency group. Terraform has no multi-writer safety. |
| `for_each` over a computed value | `Invalid for_each argument ... depends on resource attributes that cannot be determined until apply` | Break the cycle: use a fixed set of AZ names, or a second `apply` (the value is computed by a prior step whose output you pass as a variable). |
| “Nothing changed” but the app is broken | `No changes. Your infrastructure matches the configuration.` | Terraform only knows what it manages. Manual config, app-level changes and PaaS UI toggles are invisible — hence Config rules / Azure Policy / `aws configservice` + drift alarms. |

## Common mistakes

::: checklist
- [ ] `-auto-apply` / `-auto-approve` on every branch. Auto-approve is for a *human-reviewed plan artefact* in a controlled job, not for “the pipeline decides”.
- [ ] State in a Git repo, in an S3 bucket without versioning, or without a lock table.
- [ ] `count` on a list that can reorder, then a surprise database replacement.
- [ ] Hard-coded AMI/account ids/`eu-west-1a` — use data sources and variables, and never AZ letters in reusable modules.
- [ ] Secrets in `default_tags`, `user_data`, or RDS `password` (they're in state). Use a secret manager + a data source marked sensitive, and accept that the *reference* is in state, not the value.
- [ ] One giant root module: every plan takes 6 minutes, every apply touches 800 resources, and nobody can review it. One root module per blast radius.
- [ ] `terraform apply` from a laptop on prod, with local variables nobody can see. Reproducibility dies with the laptop.
- [ ] Ignoring `terraform fmt`/`validate`, so a 900-line diff hides a `depends_on` typo someone added at 23:50.
- [ ] No `prevent_destroy`/`deletion protection` on the database, bucket or state bucket, so one bad `destroy -target` is final.
- [ ] Version-less modules (`source = "../modules/vpc"`) so a module change silently affects every environment on their next run.
:::

## Best practices you can start today

::: grid2
**Small, reviewable changes with `-replace` on purpose.** When you *must* replace a resource, `terraform plan -replace=aws_instance.app` shows the blast radius before anyone merges.
**`prevent_destroy` on anything stateful** (`terraform { lifecycle { prevent_destroy = true } }`) plus cloud-side deletion protection. Belt, braces, and a written exception process.
**One plan file, everywhere.** CI, local, and any “emergency” run uses `apply <plan>`. Never re-plan at apply time.
**Name and tag by convention.** `identifier = "${var.project}-${terraform.workspace}"` used in every resource name, so `terraform state list | grep medbook-prod` is a query you can run at 2 a.m.
:::

## Interview answer

::: callout aha “How do you use Terraform on a real project?”
“Modules first: I keep a small library — network, iam-role, app-service, database — each with an explicit set of variables and outputs, versioned in Git, and one root module per environment so the state file and the blast radius match the team's approval boundary. State lives in an encrypted, versioned S3 bucket with a DynamoDB lock, so two applies can't interleave and I can read the previous state after a bad run. Nothing is applied from a laptop: a PR runs `fmt -check`, `validate`, `plan -out` and security scans, posts the diff as a comment, and on merge a separate job downloads *that exact plan* and applies it. I deliberately avoid `-auto-approve` on pushes and instead require a reviewed plan artefact plus a manual approval gate for prod. For refactors I use `moved` blocks so a rename is a state move, not a destroy — and I've seen a rename destroy a database on a project, so I now check every plan's `to destroy:` line before I let anything apply. I pin provider versions with a committed lock file and do provider upgrades in their own PR. And because Terraform only manages what it knows, I run a nightly `plan -detailed-exitcode` as a drift alarm plus cloud-side guardrails: SCP/Azure Policy for the things I refuse to allow at all.”
:::

::: grid2
**Follow-up: “What happens if you delete a resource from your config?”** → plan says `1 to destroy`, apply removes it from the cloud *and* from state. That's exactly why code review reads that line, and why stateful resources carry `prevent_destroy`.
**Follow-up: “Terraform vs CloudFormation/Pulumi?”** → CFN: native, IAM-native, no state file, but weaker module/DRY story and slower cross-cloud; Pulumi: real languages and real tests, with a state file of its own to run; Terraform: the widest provider coverage and the ecosystem everyone can read.
**Follow-up: “How do you manage secrets?”** → don't: pass references. Secrets Manager/Key Vault data sources + `sensitive = true` outputs, and the state itself protected like a database.
**Follow-up: “How do you handle a tainted resource?”** → `terraform state rm` if you intentionally adopt it manually, or `-replace` if it's half-created; and never edit state JSON by hand on a shared backend.
:::

## Virtual lab — build and destroy a network from code (simulated terminal)

::: lab terraform-1
:::

## Commands to remember

::: grid2
```bash
terraform init -upgrade=false
terraform fmt -recursive -check && terraform validate
terraform plan  -input=false -out=tfplan -detailed-exitcode
terraform apply -input=false tfplan
```
```bash
terraform state list
terraform state show aws_instance.app
terraform import aws_instance.app i-0abc123
terraform force-unlock 4a1f-...      # only after confirming no live run
terraform graph | dot -Tsvg > g.svg
```
```bash
# recovery trio — in this order, always
cp prod.tfstate prod.tfstate.bak
terraform refresh && terraform plan   # see what reality says now
terraform apply -replace=aws_instance.app
```
:::

::: callout note WHAT I MUST REMEMBER
1. Terraform's output is a **diff**; “read the plan” is the skill, not “write the HCL”.
2. State is the product: remote, encrypted, versioned, locked, and never in Git.
3. `for_each` over `count`; `moved` over “don't refactor”; `plan` file over re-planning at apply.
4. IaC governs what Terraform owns. Everything else needs drift alarms and policies.
:::

::: revision REVISION — 5 minutes
**Concepts:** providers, resources, data sources, locals, variables, outputs; state + locking; plan = diff; `count` vs `for_each`; `depends_on` as a smell; `lifecycle` (`create_before_destroy`, `ignore_changes`, `prevent_destroy`); drift; lock file; module versioning; workspaces vs directories; `moved`/`import` blocks; `-detailed-exitcode`; tfsec/Checkov/`tf plan json` policy gates.
**Architecture to remember:** modules (network · security · app · data) + `envs/{dev,prod}` roots + S3 state with a DynamoDB lock + CI that plans on PR and applies the reviewed plan on merge, gated by an environment approval.
**Common mistakes:** auto-approve on main · state in Git · `count` on an orderable list · secrets in user data/vars · one giant root module · no `prevent_destroy` on stateful things · laptop applies.
**Troubleshooting checklist:** `fmt/validate` first → `refresh` and re-plan → read the `forces replacement` lines → is the lock held (check the owner) → is the resource real (`describe-*`) → is the address right (`state list`) → `-replace` targeted → restore from state version if you must.
:::

Next: [Kubernetes — the deployment layer →](p1-kubernetes.html)
