LEAD: For a DevOps engineer, Git is not “version control for code” — it is the **handoff contract**. The developer pushes; you clone. Everything later (build, release, rollback, audit, blame) hangs on these dozen commands. You do not need to become a Git expert; you need to never lose anybody's work and always know what is deployed.

## What it is, and why it matters to *you*

::: callout why WHY A DEVOPS ENGINEER CARES ABOUT GIT AT ALL
Three jobs depend on it. **Traceability** — “what version is running in production?” must have a one-line answer. **Rollback** — going back is a Git operation plus a redeploy, not a prayer. **Automation surface** — webhooks trigger pipelines, branch names select environments, tags mark releases, `CODEOWNERS` and protected branches enforce review. If you treat Git as “the developer's tool”, you cannot answer the first question an interviewer asks about an incident.
:::

::: flow
Developer branches from main, opens a PR
Review + CI checks (build, tests, scans)
Merge to main → pipeline builds image main-<sha>
Tag v1.4.0 for a release → pipeline promotes that image to prod
Rollback = redeploy the previous tag's image
:::

## The 12 commands you need

| Job | Command | Note |
| :-- | :-- | :-- |
| receive the app | `git clone https://github.com/acme/medbook.git` | HTTPS or SSH, either works |
| where am I | `git status -sb` | branch + ahead/behind counts |
| what happened here | `git log --oneline --decorate -n 20` | the story of the repo |
| who broke it | `git blame -L 40,60 src/App.java` | line-level authorship |
| what changed | `git diff --stat main..release/1.4` | file summary before reading a diff |
| work separately | `git switch -c feature/tls-support` | one branch per change, never on `main` |
| publish | `git push -u origin HEAD` | `-u` links upstream for next time |
| get latest safely | `git fetch && git rebase origin/main` | or `git pull --rebase`; no merge-commit soup |
| mark a release | `git tag -a v1.4.0 -m "release 1.4.0"` + `git push --follow-tags` | CI builds from tags |
| inspect a version | `git switch --detach v1.3.2` | read the exact code that is running |
| undo, published | `git revert <sha>` | a new commit; history stays honest |
| undo, local only | `git reset --soft HEAD~1` | never `reset --hard` on a shared branch |

## STEP 1 — Receive a repository and *understand* it (the real skill)

::: step STEP 1 — Clone, then read like an engineer
**COMMAND**

```bash
git clone https://github.com/acme/medbook-api.git && cd medbook-api
git log --oneline --decorate -n 10
git shortlog -sn --no-merges | head        # who commits, how much
ls -la
grep -RniE "health|actuator|/readyz" src | head
```

**EXPECTED OUTPUT**

::: term
devops@lab:~/medbook-api$ git log --oneline --decorate -n 6
a1f9c2d (HEAD -> main, origin/main) chore: add /health and /ready endpoints
77b0e41 feat: appointment booking with slot overlap check
3c21aa9 fix: return 404 for unknown patient id
9d0b1f2 build: pin maven wrapper, add springdoc-openapi
2f44a0c (tag: v1.3.2) release: v1.3.2 - last release before the DB pool change
devops@lab:~/medbook-api$ ls -la
total 44
-rw-r--r-- 1 devops devops  118 Mar  4 09:41 .gitignore
-rw-r--r-- 1 devops devops 1486 Mar  4 09:41 README.md
-rw-r--r-- 1 devops devops 5.2K Mar  4 09:41 pom.xml
drwxr-xr-x 4 devops devops 4096 Mar  4 09:41 src
-rwxr-xr-x 1 devops devops  10K Mar  4 09:41 mvnw
-rw-r--r-- 1 devops devops  181 Mar  4 09:41 docker-compose.yml
:::

**WHAT.** Clone, read the recent history with ref decorations, the contributors, the top-level files, then grep for the health endpoint.

**WHY.** `--decorate` instantly tells you whether tags sit on `main` (releases exist) and whether what you cloned is what production runs. `README` + `pom.xml` + `docker-compose.yml` tell you how it builds, what runtime it wants and what it needs (a database). That is your whole deployment brief.

**WHAT JUST HAPPENED.** A Maven wrapper exists (so JDK 17 only, no system Maven), `v1.3.2` was the last tagged release, and four untagged commits sit on top — one touching the DB pool. If anyone asks “why is prod different from the repo?”, you have the answer before they finish the sentence.
:::

::: callout note THE 90-SECOND REPOSITORY INSPECTION (do this on every repo, forever)
- [ ] `ls -la` — is there a `Dockerfile`, `docker-compose.yml`, `Jenkinsfile`, `.github/workflows`, `helm/`, `terraform/`?
- [ ] `cat README.md` — run instructions, ports, env vars
- [ ] Build descriptor — `pom.xml` / `build.gradle` / `package.json` / `*.csproj` / `pyproject.toml` / `requirements.txt`
- [ ] Runtime version — `java.version`, `engines.node`, `<TargetFramework>`, `python_requires`
- [ ] Config surface — `src/main/resources/application*.yml`, `appsettings.json`, `settings.py`, `.env.example`
- [ ] Port — `server.port`, `PORT`, `EXPOSE`
- [ ] Health endpoint — the exact path (`/health`? `/actuator/health/readiness`?)
- [ ] Secrets smell — `grep -RniE "password|secret|AKIA|BEGIN PRIVATE" . | head`
- [ ] Tests — `src/test`, `*.test.js`, `pytest` config, `dotnet test` project
- [ ] LICENSE / NOTICE — someone in your legal review will ask
:::

## STEP 2 — Branches, tags and the release flow you will automate

```bash
git switch -c release/1.4.0 main          # cut from an explicit base, not from wherever HEAD is
git log --oneline main..release/1.4.0     # what does this release contain?
git diff --stat v1.3.2..release/1.4.0     # size of the change
git tag -a v1.4.0 -m "release 1.4.0
- slot overlap check
- /ready endpoint
- hikari pool sized for 2 vCPU"
git push --follow-tags
```

**Command explanation.** `switch -c name base` avoids the classic “branched off my stale feature branch by accident”. Annotated tags (`-a`) carry a message and their own object, which `git describe` and your release notes need. `--follow-tags` pushes commits and tags in one operation so CI sees both.

The ref convention used by every project on this site:

| Ref | Image tag | Environment | Rule |
| :-- | :-- | :-- | :-- |
| `feature/*` | `pr-<n>-<sha>` | ephemeral preview | build + test only; never deploy to shared envs |
| `main` | `main-<shortsha>` | dev | auto-deploy; warnings allowed |
| `release/*` | `vX.Y.Z-rc<n>` | stage | gates must pass |
| tag `v*` | `vX.Y.Z` (+ digest) | prod | **promote the tested image**, never rebuild |

::: callout aha THE ONE RULE THAT PREVENTS “IT PASSED IN STAGING”
**The artefact that passed staging is the artefact that goes to production** — promoted by *digest*, not rebuilt from a tag. `docker pull repo/app@sha256:abc… && docker tag … prod/app:v1.4.0`. If you rebuild for prod, you have tested something different from what you shipped. This is the highest-value sentence on this page.
:::

## STEP 3 — Emergencies: a secret in history, and lost work

::: step STEP 3 — A committed secret (this will happen to you)
**CONTEXT.** Your inspection grep found `DB_PASSWORD=Pr0d!medbook` in `src/main/resources/application-dev.yml`, committed three commits back. The file was deleted afterwards. **Deleting the file does nothing** — the secret still lives in history and in every clone.

**THE ORDER OF OPERATIONS**

1. **Rotate the credential first.** It is compromised the moment it is pushed; scrubbing history does not un-leak it.
2. **Find every copy:** `git log --all --oneline -S 'Pr0d!medbook'` — the pickaxe finds commits that added or removed that string.
3. **Rewrite history** (needs the team; SHAs change, everyone re-clones):

```bash
git filter-repo --invert-paths --path src/main/resources/application-dev.yml
# or remove a literal value everywhere:
git filter-repo --replace-text <(echo 'Pr0d!medbook==>***REMOVED***')
git push --force --tags
```

4. **Add the guard so it cannot repeat:**

```bash
pip install gitleaks
gitleaks protect --staged -v                    # local pre-commit
gitleaks detect --source . -v --report-format sarif --report-out gitleaks.sarif
```

**HOW WE VERIFY.** `git log --all -S 'Pr0d!medbook'` returns nothing; the old credential now fails to authenticate (because it was rotated); the scan runs on every PR as a required check.

**HOW WE PREVENT.** `.env` in `.gitignore`, a committed `.env.example` with no values, secrets injected at runtime by the platform (Secrets Manager / Key Vault / Kubernetes Secret), and push protection on the host.
:::

::: callout fix “I lost a commit” / “I did `git reset --hard`” — recoverable, relax
```bash
git reflog                       # every position HEAD has held, ~90 days
git reset --hard HEAD@{4}        # or, safer: git switch -c rescue HEAD@{4}
git fsck --lost-found            # dangling objects, the last resort
git cherry-pick <sha>            # bring one commit back onto a new branch
```
`reflog` is why “Git never really loses it”. Tell the juniors once; they will fix their own mistakes instead of hiding them.
:::

## STEP 4 — GitHub: what the platform adds on top of Git

| Feature | Why you, as a DevOps engineer, care |
| :-- | :-- |
| Branch protection | required reviews and checks, no force-push to `main`, linear history — your release policy in UI form |
| `CODEOWNERS` | who must approve infra: `terraform/** @platform-team` |
| Actions (`on: push/pull_request`) | the CI runner; secrets live in *environments*, not in the YAML |
| Environments + protection rules | an approval button in front of “prod deployment” — cheap, real change control |
| OIDC to the cloud | short-lived role assumption in CI instead of long-lived keys (see [secrets & IAM](p2-secrets-iam.html)) |
| Concurrency groups | `concurrency: {group: prod, cancel-in-progress: false}` stops two overlapping prod deploys |
| Dependabot / Renovate | opens the PRs for vulnerable or stale dependencies — your SCA input |
| Secret scanning + push protection | the platform-side copy of gitleaks; enable it on every repo you touch |
| Releases + tags | the human-readable changelog; attach `CHANGELOG.md` and the image digest |
| `gh` CLI | `gh run watch`, `gh run view --log-failed`, `gh pr checks --watch` — pipeline debugging without a browser |

```bash
gh repo clone acme/medbook
gh run list --workflow=ci.yml --limit 5
gh run watch 4123456
gh run view 4123456 --log-failed
gh pr checks 128 --watch
gh api repos/acme/medbook/branches/main/protection     # what does our policy actually say?
```

## Production scenario — the release that was not the tested one

Monday 08:15. Prod runs an image tagged `v1.4.0`. Staging passed at 23:40. Users get 500s on the booking form.

1. `gh release view v1.4.0` → the release notes record digest `sha256:9f2c…`.
2. On the node: `docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' <running>` → commit `a1f9c2d`.
3. `git rev-parse v1.4.0^{commit}` → `77b0e41`. **They differ.** Someone re-pointed `v1.4.0` after the staging test “to include one more fix”, and the pipeline rebuilt on the tag push.
4. Mitigate: promote the *tested* digest back — `kubectl set image deploy/medbook medbook=repo/medbook@sha256:8ad1…`. Verify `/ready`, watch the error rate for 5 minutes.
5. Fix forever: protect the tag pattern `v*` (no force-push, no delete), have the pipeline deploy the digest it produced rather than a name, and add a check that the registry digest for `v1.4.0` equals the recorded one.

Git did not cause the outage. An unenforced release convention did. This is why “immutable tags, promoted digests” is on every senior's list.

## Common mistakes

::: checklist
- [ ] Committing straight to `main` “because it's only a config change” — no review, no gate, no rollback story.
- [ ] `git push --force` on a shared branch: you rewrote history others built on. Use `--force-with-lease`, never on `main`.
- [ ] `:latest` in production: you cannot tell what runs and cannot roll back.
- [ ] `git pull` on a dirty tree → conflicts in half-edited files. `git stash -u` (or a WIP commit), then `git pull --rebase`.
- [ ] Merge-commit soup: history unreadable. Squash-merge PRs, rebase long-lived branches.
- [ ] No `.gitignore` for build output → 300 MB of `target/` in the repo and a slow CI clone.
- [ ] Committing `.env` “temporarily”. See STEP 3.
- [ ] Tags that move. If `v1.4.0` can be re-pointed, your audit trail is fiction.
:::

## Best practices

::: grid2
**Conventional commits.** `feat:` `fix:` `build:` `chore:` `ci:` `perf:` `revert:`, and a `CHANGELOG.md` generated from them. Release notes become free.
**Branch = intent.** `feature/`, `fix/`, `release/`, `hotfix/` — then CI can choose the environment from the ref name, with no human configuration.
**Sign release tags** (`git config commit.gpgsign true`, `tag.gpgsign`) so “who approved this release?” is cryptographic rather than tribal.
**One repo per deployable, plus one infra repo.** Application code and infrastructure change on different clocks with different reviewers; separation keeps `terraform apply` out of app PRs and vice versa.
**`git worktree` for release days.** `main` and `release/1.4` in two directories; no stash-juggling while a hotfix waits.
:::

## Interview answer

::: callout aha “How do you manage a release with Git in a CI/CD setup?”
“I use branch intent and immutable tags. Work happens on short-lived branches merged to `main` by squash, so history stays linear and one commit equals one PR. `main` builds a candidate image tagged with the short SHA and deploys to dev. For a release I cut `release/x.y.z`; when staging is green I create an annotated tag `vX.Y.Z`, the pipeline builds once, and then **promotes the same image digest** to production — never a rebuild, because the tested artefact must be the shipped artefact. Rollback is redeploying the previous tag's digest, which is why tags are protected and immutable. Traceability works both ways: from a running container's `org.opencontainers.image.revision` label I reach the exact commit, and from `git blame` on a config line I reach the PR and the pipeline run that deployed it. On GitHub I'd add required checks, an environment approval for prod, and push protection for secrets, so the process is enforced by the platform rather than remembered by people.”
:::

::: grid2
**Follow-up: “Merge vs rebase?”** → merge preserves true history and is safe on shared branches; rebase gives a clean linear history but rewrites SHAs, so only for local work. Pick one team rule and enforce it in branch protection.
**Follow-up: “A PR was merged by mistake — now what?”** → `git revert <merge-sha> -m 1` (a new commit; history honest), PR it, let CI run, push. Never rewrite a shared branch to undo one commit.
**Follow-up: “How do you keep secrets out of the repo?”** → runtime injection by the platform, `.env` ignored with a committed `.env.example`, pre-commit gitleaks, host push protection, a required CI scan — and rotation anyway if anything ever lands in history.
:::

## Virtual lab — receive, inspect, release, recover (simulated terminal)

::: lab git-1
:::

## Commands to remember

::: grid2
```bash
git clone URL && git status -sb
git log --oneline --decorate -n 20
git shortlog -sn --no-merges
git diff --stat v1.3.2..HEAD
git log --oneline -- path/to/file
```
```bash
git switch -c release/1.5 main
git fetch --all --prune
git pull --rebase
git tag -a v1.5.0 -m "release 1.5.0"
git push --follow-tags
```
```bash
git revert <sha> -m 1      # undo, published
git reset --soft HEAD~1    # undo, local
git reflog ; git stash -u
git filter-repo --invert-paths --path secret.txt
gitleaks protect --staged -v
```
:::

::: callout note WHAT I MUST REMEMBER
1. Clone, then read `log --decorate`, `README`, the build descriptor and the config surface. The repo tells you how to deploy it.
2. Deploy by immutable tag *or* digest; promote the tested artefact; roll back by redeploying the previous one.
3. A committed secret is rotated first, scrubbed second. `reflog` saves your work; `revert` saves everybody's history.
:::

::: revision REVISION — 5 minutes
**Concepts:** working dir / index / HEAD · SHA as content hash · fast-forward vs merge vs rebase · annotated vs lightweight tag · upstream tracking · `--force-with-lease` · protected branches · pickaxe `-S` · reflog window · OIDC vs stored keys · CODEOWNERS as policy · release flow (feature → main → release → tag).
**Architecture to remember:** the flow diagram at the top: PR → checks → main (dev) → release branch (stage) → tag (promote digest to prod) → rollback = previous tag.
**Common mistakes:** `:latest` · moving tags · committing to main · `reset --hard` on shared branches · committed `.env` · assuming HEAD equals production.
**Troubleshooting checklist:** lost commit → `reflog` · wrong code shipped → compare container label with `git rev-parse <tag>` · secret in history → rotate → `filter-repo` → coordinated force-push · blocked push → protection or non-fast-forward → `fetch` + `rebase`.
:::

Next: [Docker + Compose →](f-docker.html)
