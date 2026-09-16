LEAD: A pipeline is a machine that turns a commit into a decision. If it only turns a commit into a deployment, you have an expensive button. Everything on this page — Jenkins and GitHub Actions side by side — is organised around one idea: each stage must either produce evidence or stop the line.

::: flow
commit → build → test → scan → package → publish → deploy staging → verify → **gate** → deploy prod → smoke → auto-rollback on failure
:::

**The stages, in words an interviewer accepts:** build (compile; the artefact name embeds the commit SHA so it's traceable) · test (unit + contract, deterministic, and the *coverage* is trended not demanded) · static analysis and dependency/secret scanning (cheap, early, blocking at severity) · package (image, SBOM, signature, provenance) · deploy to a real environment with a health-check wait · **verify** (`rollout status`, synthetic requests, smoke tests) · promote by *retagging and promoting the same artefact* — never rebuild for prod · gate (a human on prod, an SLO on auto-scaling decisions) · rollback on a red health signal.

## Jenkins: the industry's workhorse (learn it, then love it less than it deserves)

```
Controller (the brain: plugins, credentials, config-as-code, JCasC)
   ├── agent "linux"   ← your build box (labels = scheduling policy)
   ├── agent "k8s"     ← pod templates: one ephemeral pod per build (the modern way)
   └── agent "docker"  ← a runner with the Docker socket (danger: root-equivalent; prefer DinD-less
                          BuildKit with a dedicated service account)
```

**Why it's everywhere:** anything can be a Jenkins job. Legacy .NET on Windows, SSH deploys, Ansible, “the DBA's script”, on-prem VMs, jobs with human input steps, and pipelines that must run inside your firewall. **Why teams leave:** plugin churn, upgrades that break the world, controllers that accumulate snowflakes, and no security boundary between “run my job” and “read every secret”. Knowing both answers honestly is the interview-grade position: “I'd use GitHub Actions for anything GitHub-hosted and Jenkins where on-prem, Windows, or heavy orchestration of long-running manual steps is required.”

::: cols
::: col
**Install it the way the reference project does** (`DevOps-Project-41`): a `t2.large` EC2 with Docker, and a `docker-compose.yml` bringing up Jenkins (8080), an agent, SonarQube (9000) and the Trivy plugin path — port 50000 for agents.

**Then four settings that decide your quality of life:**
1. *Manage Jenkins → Security → Agent-to-controller access control* on.
2. *Threats* configured for Sonar/OWASP-ZAP so “failed to push” is a build failure.
3. Global pipeline library in Git, not copy-pasted `sh` blocks.
4. Build retention: 30 builds max, 14 days — or `/var/lib/jenkins` fills the 30 GB EBS and `df -h` becomes your incident.
:::
::: col
**Credentials, the only correct way:**
```groovy
withCredentials([sshUserPrivateKey(credentialsId:'gh-deploy-key', keyFileVariable:'KEY'),
                 usernamePassword(credentialsId:'ecr', usernameVariable:'U', passwordVariable:'P')]) {
  sh 'aws ecr get-login-password | docker login -u $U --password-stdin ...'
}
```
Never `sh "docker login -u ${env.USER} -p ${env.PASS}"` — it lands in the build log, which is world-readable to your whole org. Store secrets in the Credentials store backed by the HashiCorp Vault or AWS Secrets Manager plugin, so rotation happens in one place; and give each job a *scoped* credential, not the admin's.
:::

### A Jenkinsfile you can defend in review — modelled on `DevOps-Project-41`

```groovy
#!/usr/bin/env groovy
pipeline {
  agent { label 'docker-agent' }

  options {
    timestamps(); disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
    timeout(time: 30, unit: 'MINUTES'); retry(1)
  }

  environment {
    REGISTRY = '123456789012.dkr.ecr.eu-west-1.amazonaws.com'
    IMAGE    = "${REGISTRY}/medbook"
    TAG      = "${env.BRANCH_NAME}-${env.GIT_COMMIT?.take(7)}-${env.BUILD_NUMBER}"
    TRIVY_SEVERITY = 'HIGH,CRITICAL'
  }

  stages {
    stage('Checkout & toolchain') {
      steps {
        checkout scm
        sh 'java -version && mvn -v && docker --version'
      }
    }
    stage('Build + unit tests') {
      steps {
        sh './mvnw -B -Dtest=failing test verify || true'   // pattern below explains the || true
        junit allowEmptyResults: false, testResults: '**/target/surefire-reports/*.xml'
      }
      post { always { recordCoverage(tools: [[parser: 'JACOCO']], id: 'jacoco') } }
    }
    stage('Static analysis (SAST)') {
      steps {
        sh 'mvn -B sonar:sonar -Dsonar.projectKey=medbook -Dsonar.qualitygate.wait=true'
      }
    }
    stage('Dependencies (SCA)') {
      steps { sh "trivy fs . --severity ${TRIVY_SEVERITY} --exit-code 1 --ignore-unfixed=false" }
    }
    stage('Secret scan') {
      steps { sh "gitleaks detect --source . --redact --exit-code 1 || " +
                 "echo 'found; blocking' && exit 1" }
    }
    stage('Image: build, scan, push') {
      steps {
        sh "docker build -t ${IMAGE}:${TAG} ."
        sh "trivy image ${IMAGE}:${TAG} --severity ${TRIVY_SEVERITY} --exit-code 1"
        withCredentials([usernamePassword(credentialsId:'ecr-push', usernameVariable:'U', passwordVariable:'P')]) {
          sh 'aws ecr get-login-password | docker login --username AWS --password-stdin $REGISTRY'
        }
        sh "docker push ${IMAGE}:${TAG} && cosign sign --yes ${IMAGE}:${TAG}"
      }
    }
    stage('Deploy staging') {
      steps {
        sh "kubectl -n staging set image deploy/medbook medbook=${IMAGE}@$(docker buildx imagetools inspect --raw ${IMAGE}:${TAG} | jq -r .digest) " +
           "&& kubectl -n staging rollout status deploy/medbook --timeout=240s"
      }
    }
    stage('Verify') {
      steps { sh './scripts/smoke.sh https://staging.medbook.example.com' }
    }
    stage('Promote to prod') {
      when { expression { env.BRANCH_NAME == 'main' } }
      options { timeout(time: 60, unit: 'MINUTES') }
      input {
        message "Promote ${env.TAG} to production?"
        ok 'Ship it'; submitter 'leads,devops';
        // and a parameter so the approver can attach the change number
        parameters { text(name: 'CHANGE_REF', defaultValue: '', description: 'JIRA change id') }
      }
      steps { sh "./scripts/deploy.sh prod ${IMAGE}:${TAG}" }
    }
  }

  post {
    failure {
      sh "./scripts/notify.sh '#devops-alerts' \"medbook build #${env.BUILD_NUMBER} failed on ${env.BRANCH_NAME}: ${env.BUILD_URL}\""
      catchError { sh './scripts/rollback.sh staging' }     // auto-rollback where it's safe
    }
    success { sh "./scripts/tag-release.sh ${env.TAG}" }
    always  { cleanWs(deleteDirs: true, notFailBuild: true) }
  }
}
```

::: callout fix THE `|| true` TRICK THAT'S WORTH KNOWING (and its trap)
`sh './mvnw test || true'` + `junit allowEmptyResults: false` means *a test failure still publishes the report and then fails the build via the junit step*. Why bother? So the PR comment shows exactly which tests failed instead of “exit code 1”. The trap: if the build fails *before* tests, there are no report files and the job passes silently — hence `allowEmptyResults: false`, which makes the missing report itself a failure. This is the kind of nuance that separates “I have written a Jenkinsfile” from “I have debugged one at 11 p.m.”
:::

**Jenkins terms in one line each:** *pipeline syntax* = scripted (Groovy you can abuse) vs declarative (stages, `post`, `when`, sane defaults — choose this); *agent vs label* = where a build runs, and the label is your queue management; *shared library* = `vars/*.groovy` functions so ten teams share one deploy step; *JCasC* = config as YAML, so a controller rebuild is 5 minutes not 3 days; *jobs vs folders* = organise by team, not by “everything in root”; *webhook* = GitHub App / GitHub plugin so builds start in 3 s not 60 s of polling.

## GitHub Actions: CI as a code review

```yaml
# .github/workflows/ci.yml  — the same pipeline, and note what's different
name: ci
on:
  pull_request: { branches: [main] }
  push: { branches: [main] }
permissions: { contents: read }          # default-deny; every job opts in explicitly (do that!)
concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }   # PRs don't queue behind each other

env: { REGISTRY: 123456789012.dkr.ecr.eu-west-1.amazonaws.com, IMAGE: medbook }

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }              # full history → gitleaks can scan commits, not just tree
      - uses: actions/setup-java@v4
        with: { distribution: temurin, java-version: 17, cache: maven }
      - run: ./mvnw -B verify
      - uses: actions/upload-artifact@v4
        with: { name: reports, path: target/surefire-reports/, retention-days: 5 }
      - uses: gitleaks/gitleaks-action@v2
        env: { GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
      - uses: aquasecurity/trivy-action@0.24.0
        with: { scan-type: fs, severity: HIGH,CRITICAL, exit-code: '1', ignore-unfixed: true }
      - uses: mstksg/get-test-coverage@v1        # or a PR comment from the junit artifact
        continue-on-error: true

  image:
    needs: quality
    runs-on: ubuntu-latest
    permissions: { id-token: write, contents: read, packages: write }   # OIDC for AWS, no stored keys
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::123456789012:role/gha-medbook-ci
          aws-region: eu-west-1
      - uses: aws-actions/amazon-ecr-login@v2
        id: ecr
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.ecr.outputs.registry }}/medbook:sha-${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          provenance: true                    # SLSA attestations + SBOM, for free
      - run: |
          DIGEST=$(docker buildx imagetools inspect ${{ steps.ecr.outputs.registry }}/medbook:sha-${{ github.sha }} --format '{{json .Manifest}}' | jq -r .digest)
          cosign sign --yes "${{ steps.ecr.outputs.registry }}/medbook@$DIGEST"
  deploy-staging:
    needs: image
    uses: ./.github/workflows/deploy.yml      # a reusable workflow: ONE deploy implementation, many callers
    with: { env: staging, image: "..." }
    secrets: inherit

  deploy-prod:
    if: github.ref == 'refs/heads/main'
    needs: deploy-staging
    environment: { name: production, url: https://medbook.example.com }   # ← required reviewers live here
    uses: ./.github/workflows/deploy.yml
    with: { env: prod, image: "..." }
    secrets: inherit
```

::: grid2
**The three features that change team behaviour:** **matrix** (test 3 versions/OSes from 10 lines), **reusable workflows** (`on: workflow_call` — a deploy path shared by 11 repos, changed once), and **environments** (protection rules = required reviewers + a wait timer + a *separate* set of secrets, so `prod` creds literally aren't available to a PR build from a fork).
**`pull_request` vs `pull_request_target`:** the first runs with the fork's restricted token (safe, but no secrets); the second runs in the base repo with secrets — which is fine for *labeling*, catastrophic if you also `checkout` the PR code and run it. That one word has been the root cause of real supply-chain incidents; use a `pull_request` + a comment-free policy, or the trust model of your choice, consciously.
:::

## The pipeline order and why it's that order

| Position | Stage | Why here, not later |
| :-- | :-- | :-- |
| 1 | `fmt` + `validate` + `hadolint`/`yamllint` | 5 seconds, catches 20 % of review noise, teaches the machine to be the pedant |
| 2 | Unit tests + coverage | Feedback must be < 5 min or developers route around the pipeline |
| 3 | SAST (SonarQube) | Cheapest possible place to find a null-pointer or an injection sink — before it's compiled into an artefact |
| 4 | SCA (Trivy fs / OWASP dep-check / `npm audit`) | A dependency CVE doesn't need a built image to be found |
| 5 | Secret scan (`gitleaks`, `trufflehog`, GH's own scanning) | A committed secret is a *rotation* event, not a code event — find it before it's in a public commit |
| 6 | Build + Dockerfile lint | Now the artefact exists; the scans that need it start here |
| 7 | Image scan (Trivy/Grype), SBOM, sign, provenance | Scan the artefact you'll actually run, not a proxy for it; produce attestation so “was this built here?” is answerable |
| 8 | IaC scan (Checkov/tfsec, `kubeconform`, `kubectl diff`) | Fail before `apply`; `terraform plan` output through `tfsec`/`checkov --var-file` catches the open SG in *this* change |
| 9 | Deploy staging → **verify** (rollout status, smoke) | A deploy without verification is a prayer |
| 10 | DAST (OWASP ZAP baseline) on staging | Needs a running app; finds `X-Frame-Options`, cookie flags, IDOR-ish behaviours |
| 11 | Gate (approval, SLO check) | Humans approve *risk*, not *diffs* |
| 12 | Deploy prod with health-gated rollout → auto-rollback | Same as 9, with an alarm attached |

::: callout why “FAIL EARLY” IS A COST ARGUMENT, NOT A PURITY ARGUMENT
The same class of bug costs, per stage it's caught in, roughly: style 1×, unit test 5×, staging 25×, production 200×. That's why SAST and secret scanning sit at the *front*, before a 9-minute build, and why an image scan sits right after the image exists. It's also why a gate you place after deploy is worth less than the same gate placed after build: by then the artefact is in the registry and someone's `docker pull`ing it into a laptop.
:::

## Production scenario — a “successful” pipeline that shipped nothing

Team reported “deploy didn't work” after a green build. Three findings, all worth memorising:

1. The deploy step ran `kubectl apply` and the pipeline counted the *exit code of the shell*, not the rollout: `deployment.apps/medbook configured` was enough for green. Fix: `kubectl rollout status --timeout=180s` as the last command, and `kubectl diff` in the PR.
2. `on: push` + `paths-ignore: ['**.md']` had also been added — a docs PR that touched `Chart.yaml` still skipped CI, and the manifest change rode in on the *next* commit, deployed by someone else's build. This is why the config change “appeared to come from nowhere”. Fix: no path filters on jobs that deploy; path filters only for jobs that can't break anything.
3. Cache: `actions/cache` keyed on `$(date +%Y-%m)` for the Maven repo, so a poisoned `~/.m2` sat there for a month and half the team ran an old dependency. Fix: key on `hashFiles('**/pom.xml')` (plus an OS/arch suffix), and never let a cache be *required* for correctness.

Then prevention: a `verify` job that asserts “the image running in prod has digest X”, reading the deployment back and comparing to the artefact just built. Fifteen lines of YAML, and a whole category of “it said success” incidents dies.

## Common mistakes

::: checklist
- [ ] **Trusting green.** A pipeline that ends in `kubectl apply` without a status check is decoration. Verify the *cluster/VM*, not the shell exit code.
- [ ] **Rebuilding for prod.** Staging and prod must run the same bytes; promotion = retag/approve, not “run the build again on main” (yes, that's why the diff you tested isn't the diff you ship).
- [ ] **Secrets in `environment:` blocks.** They're masked in logs but readable by anything with write access to the repo — hence environments with `required reviewers`, and OIDC for cloud access.
- [ ] **Jenkinsfile committed to `master` only** → every PR branch builds whatever's on master, and “the pipeline changed and I didn't touch it” becomes a mystery. Use multibranch and `when { branch 'main' }` for deploy stages.
- [ ] **Unbounded concurrency** → 40 builds for one PR storm the cluster and the registry; use `disableConcurrentBuilds()` / `concurrency.group`.
- [ ] **Flaky tests “fixed” by `retry()`** — retry hides, quarantine + a ticket fixes.
- [ ] **A 22-minute pipeline with no parallelism.** `needs:`/`parallel:` matrix and cache split it into 7. Speed is a security feature: slow pipelines get bypassed.
- [ ] **One mega job that does test + build + deploy** → you can't retry the deploy, can't see what took the time, and can't reuse it. One concern per job.
- [ ] **DAST and secret scans that never block** → findings no one reads; either gate them or delete them (an unread alarm is worse than none).
- [ ] **Deploying on a Friday at 18:00** from an unattended “auto-promote main” rule. Gate after 16:00, or require the approver to be around for 30 minutes.
:::

## Best practices

::: grid2
**Idempotent deploys, always.** `apply`/`helm upgrade --install`/`terraform apply` are safe to re-run; `for` loops of SSH commands aren't. If the pipeline can be re-run on the same commit and produce the same state, retries and rollbacks stop being scary.
**A green pipeline must mean something specific.** Write the sentence: “green means the artefact is scanned, signed, running in staging and passing smoke tests.” If you can't, the pipeline has a hole.
**Deploy preview environments for PRs** (a namespace per PR, or `--reuse-values` Cloud Run revisions with a PR URL). Reviewers test the *running thing*, not the diff; and “it works on my machine” becomes “it works at https://pr-142.staging.example.com”.
**Version everything in the pipeline itself.** Pinned action SHAs or Dependabot for actions, `options { timeout }`, `RUNNER_DEBUG=1` for diagnosis, and one README section: “how to run this build locally” — because a pipeline you can't run locally is a pipeline you can't debug.
:::

## Interview answer

::: callout aha “Design a CI/CD pipeline for a containerised service.”
“Source is GitHub with branch protection and signed commits; PRs are the only path to main, with required status checks from Actions. The pipeline runs in that order: lint/format and `terraform validate` because they're free, unit tests with coverage trended as a comment, then three scans — SonarQube for SAST with `qualitygate.wait`, Trivy for dependencies, and gitleaks for secrets, all blocking on HIGH/CRITICAL — then the image build with BuildKit and layer caching, so the base image never floats: I pin it by digest and Renovate opens a PR to move it. Right after build, in the same job: Trivy on the image, an SBOM, and cosign signing with the job's OIDC token — no cloud keys in the repo, the job assumes a scoped role that can push to one repository and describe one log group. Deployment is Helm per environment with values in Git, promoted by digest rather than rebuilt, so staging and prod run identical bytes. Staging deploys automatically and *verifies* with `helm status` + `kubectl rollout status` + smoke tests + a ZAP baseline scan; production requires an environment approval from the service owners with a change reference, and the rollout itself is gated: maxSurge 1 / maxUnavailable 0, readiness probes, an Argo Rollouts or ALB weighted canary, and automatic rollback if 5xx or p99 crosses the SLO for 2 minutes. Everything logs to one place, and the pipeline publishes a deployment marker as an annotation so 'what changed at 14:32' is answerable. Jenkins is where I'd put the same stages if we needed on-prem, Windows agents or manual input steps — the shape doesn't change, only the YAML.”
:::

::: grid2
**Follow-up: “Blue/green or canary?”** → blue/green is a fast, reversible whole-system switch (needs 2× capacity, and one clean moment of cutover); canary is gradual and statistically safer for risky changes, but needs traffic splitting and metrics you trust. Most teams want canary on the API tier and blue/green on the database-adjacent bits.
**Follow-up: “How do you handle DB migrations?”** → expand/contract: add nullable column, deploy code that reads/writes both, backfill, then drop. Migration runs as an explicit gated job, never as an app side effect during a rollout.
**Follow-up: “Pipeline is red and prod is broken. Order of operations?”** → stabilise first (rollback or feature-flag off), then reproduce, then fix forward with a test. Never debug a broken prod from the pipeline logs alone — take a snapshot: pod describe, last good deploy time, `git log` since.
**Follow-up: “What's your policy on merge queues?”** → `merge_queue` on Actions or GitHub's queue with `group` locks, because “green on the PR” is not “green on main”; a queue also caps the number of concurrent deploys.
:::

## Virtual lab — build the pipeline, then debug it (simulated terminal)

::: lab cicd-1
:::

## Commands to remember

::: grid2
```bash
# Jenkins: the debugging trio
tail -f /var/log/jenkins/jenkins.log
curl -s -u user:apitoken JENKINS/job/x/lastBuild/consoleText | tail -40
jenkins-cli build job --wait-with-console
```
```bash
# Actions: the debugging trio
gh run list --workflow=ci.yml --limit 10 --json status,conclusion
gh run view 1234567890 --log-failed
act push -j deploy-staging --secret GITHUB_TOKEN=...   # run locally
```
```bash
# the promote-by-digest pattern (both tools)
cosign verify --key cosign.pub $REGISTRY/app@sha256:...
cosign sign --yes $REGISTRY/app:prod-$SHA
helm upgrade --install app ./chart --values values/prod.yaml \
  --set image.digest=sha256:... --atomic --timeout 5m
```
:::

::: callout note WHAT I MUST REMEMBER
1. Every stage either produces evidence (report, artefact, attestation, health check) or it's theatre.
2. Order by cost: cheap static checks first, artefact scans after the artefact exists, runtime scans after deploy.
3. Promote artefacts; never rebuild for prod.
4. Green ≠ deployed. Verify with `rollout status` and a smoke test, or the pipeline will lie at the worst possible moment.
:::

::: revision REVISION — 5 minutes
**Concepts:** build/test/package/deploy/verify/promote/gate · CD vs “continuous deployment” (a gate is a design choice) · declarative Jenkinsfile + shared library + JCasC · Actions events, `permissions`, concurrency, environments, reusable workflows, matrix, OIDC · pin actions by SHA · SBOM + provenance + cosign · preview environments · flaky-test quarantine · cache keys · pipeline security (fork PRs, `pull_request_target`, secrets availability) · rollback as a first-class stage.
**Architecture to remember:** PR → Actions/Jenkins (checks, scans, build, scan, sign, push) → registry with immutable digests → Argo/Helm applies to cluster → LB canary weights → metrics gate → promote by digest → alarms wired to auto-revert.
**Common mistakes:** no `rollout status` · rebuild for prod · no timeouts/`disableConcurrentBuilds` · secrets as plain env in a fork-exposed job · path filters skipping deploy-critical files · 22-minute serial pipelines.
**Troubleshooting checklist:** `gh run view --log-failed` (or consoleText) → which stage, which exit code → is it infra (`no space left`, DNS, runner killed) or code → re-run a single job with debug → is the artefact actually in the registry (`describe-images`) → did the rollout stall at `1 old replicas pending termination` → is the deployment pointing at the digest you pushed → and did a cache/lock make it a one-off.
:::

Next: [Observability →](p1-observability.html)
