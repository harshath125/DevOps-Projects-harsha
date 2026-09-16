LEAD: Docker is the hinge of this course. It is the moment a developer's “it runs on my machine” becomes something a pipeline can build, scan, sign, ship and start anywhere with the same result. You are not learning to write applications in containers — you are learning to *receive* an application and turn it into an artefact that never lies about what is inside.

## What it is / why we need it

| Question | Answer |
| :-- | :-- |
| **What?** | A way to package an app with its runtime into an **image**: a read-only, layered filesystem plus a start command. A **container** is that image running as ordinary processes on the host kernel, inside namespaces (isolation) with cgroup limits. |
| **Why?** | “Works on my machine” is a deployment bug; the image removes the machine from the equation. You also get a scan target, an immutable artefact, rollback by tag, and one format every registry, orchestrator and CI runner understands. |
| **What it is NOT?** | Not a VM (no guest kernel), not a config manager, not a database host for production data, and not a substitute for infrastructure as code. |
| **Why do interviewers ask?** | If you cannot read a Dockerfile and explain why the image is 1.4 GB and how you would shrink it, you cannot own a pipeline. |

::: flow
source (developer's repo) → Dockerfile (the recipe) → docker build (cached layers) → image in a registry (immutable, tagged, signed, scanned) → docker run (a writable top layer + namespaces + cgroups) → container (exec into it, tail it, stop it, delete it)
:::

**Mental model that fixes most confusion:** *the image is the deployment artefact; the container is a disposable instance of it.* You never “fix” a container — you fix the image and start a new container. Say it until it is automatic; it is the cultural difference between VM-era and container-era operations.

## Read *any* Dockerfile — the eight lines that decide everything

```dockerfile
# 1. base: small, patched, pinned
FROM eclipse-temurin:17.0.10_7-jdk-jammy AS builder

# 2. build-only inputs, ordered so the cache survives code edits
WORKDIR /workspace
COPY pom.xml mvnw ./
COPY .mvn .mvn
RUN ./mvnw -B dependency:go-offline          # cached until pom.xml changes

# 3. now source; a code change busts only this layer and below
COPY src src
RUN ./mvnw -B -DskipTests package            # -> target/*.jar

# 4. multi-stage: ship the jar, not the toolchain
FROM eclipse-temurin:17.0.10_7-jre-jammy
RUN useradd -r -u 10001 -s /usr/sbin/nologin appuser
WORKDIR /app
COPY --from=builder --chown=10001 /workspace/target/medbook-0.0.1-SNAPSHOT.jar app.jar
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75.0" \
    SPRING_CONFIG_ADDITIONAL_LOCATION="file:/config/"
EXPOSE 8080
USER 10001
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/actuator/health || exit 1
ENTRYPOINT ["sh","-c","exec java $JAVA_OPTS -jar /app/app.jar"]
```

| Line | What a DevOps engineer takes from it |
| :-- | :-- |
| `FROM …:17.0.10_7-jre-jammy` | A pinned base = reproducible builds and a known CVE baseline. `FROM openjdk` alone is a maintenance problem. |
| `AS builder` + second `FROM` | Multi-stage: the toolchain never ships. Typically 900 MB → 210 MB, and ~40 fewer CVEs to triage. |
| `COPY pom.xml` before `COPY src` | Layer-cache order. The other way round, every commit re-downloads every dependency — about 6 minutes lost per pipeline run. |
| `useradd` + `USER 10001` | Blast radius for escapes and for file permissions. Note the coupling: mounted config must be *readable by 10001*, or you set `securityContext.runAsUser` in the cluster. Pick one and document it. |
| `ENV` defaults, no secrets | Defaults are fine; secrets are not — they persist in `docker history` and in the image config. Inject at runtime. |
| `EXPOSE` | **Documentation only.** Publishing is `-p host:container`. A classic interview trap. |
| `HEALTHCHECK` | Makes `docker ps` honest and lets Compose's `depends_on: condition: service_healthy` wait for a real start. |
| `exec java …` in the entrypoint | Without `exec`, `sh` is PID 1 and SIGTERM dies with the shell: `docker stop` waits 10 s and SIGKILLs your app mid-request. Use `exec` or `--init`. |

## Build, run, push — with the flags you will actually need

```bash
docker build \
  -t 123456789012.dkr.ecr.eu-west-1.amazonaws.com/medbook:v1.4.0 \
  -t 123456789012.dkr.ecr.eu-west-1.amazonaws.com/medbook:main-a1f9c2d \
  --label org.opencontainers.image.revision=a1f9c2d \
  --label org.opencontainers.image.source=https://github.com/acme/medbook \
  .

docker run -d --name medbook \
  -p 8080:8080 \
  --env-file /etc/medbook/dev.env \
  -v /etc/medbook/config:/config:ro \
  --memory 768m --cpus 1.5 \
  --restart unless-stopped \
  --read-only --tmpfs /tmp \
  --log-opt max-size=10m --log-opt max-file=3 \
  123456789012.dkr.ecr.eu-west-1.amazonaws.com/medbook:v1.4.0
```

::: cols
::: col
**Build flags**
- `.` = the **build context**: this whole directory is archived and sent to the daemon. Hence `.dockerignore`.
- two `-t` = two tags, one image: an immutable release tag plus a `main-<sha>` tag. Push both.
- `--label org.opencontainers.image.revision` — now `docker inspect` answers “which commit is this?”: a free audit trail.
- `--progress=plain` when a layer fails; the fancy UI hides the real error.
:::
::: col
**Run flags**
- `-p 8080:8080` is `host:container`. `-p 127.0.0.1:8080:8080` if only a local proxy may reach it. `-p 80` alone picks a random host port (tests, not prod).
- `--env-file` keeps secrets out of `docker inspect` args and shell history.
- `-v host:container:ro` for config; named volume for anything that must survive; **never** DB data in the container's writable layer.
- `--read-only --tmpfs /tmp` = the process writes only to declared paths. Some apps break — that is the point; find out here, not at 2 a.m.
- `--memory`/`--cpus` are cgroup limits. Without them one leak takes the box.
:::

```bash
# the debug five — you will use these daily
docker logs -f --tail 100 medbook
docker exec -it medbook sh              # distroless images have no shell: use kubectl debug / a sidecar
docker inspect medbook --format '{{json .State.Health}}' | jq
docker stats --no-stream medbook
docker history --no-trunc medbook:v1.4.0 | head -20
```

## Push it somewhere: the registry is a handoff point

```bash
# AWS ECR — the pattern used by DevOps-Project-04 in the reference repository
aws ecr create-repository \
    --repository-name medbook \
    --image-scanning-configuration scanOnPush=true \
    --image-tag-mutability IMMUTABLE \
    --region eu-west-1
aws ecr get-login-password --region eu-west-1 | \
  docker login --username AWS --password-stdin 123456789012.dkr.ecr.eu-west-1.amazonaws.com
docker push 123456789012.dkr.ecr.eu-west-1.amazonaws.com/medbook:v1.4.0
```

| Cloud | Registry | Two things worth knowing |
| :-- | :-- | :-- |
| AWS | ECR | `scanOnPush=true` + `IMMUTABLE` save you a policy argument later. Add a lifecycle policy: 40 000 images is a real bill. |
| Azure | ACR | `az acr build` builds *in the cloud* (no local Docker needed); tasks rebuild on base-image updates. |
| GCP | Artifact Registry | Replaces Container Registry; holds Helm charts too; `gcloud auth configure-docker` once per machine. |
| Any | Harbor / Artifactory | Harbor: projects, cosign signing, robot accounts. Artifactory: remote caches, so a registry outage doesn't stop builds. |

**Tag policy for every project here:** `vX.Y.Z` for releases, `main-<shortsha>` for dev, and **never** `:latest` in a deployment manifest — with `:latest` nobody can tell what is running, and `rollout undo` may restart the very image that broke.

## Docker Compose — the two-service harness you need before Kubernetes

```yaml
# docker-compose.yml — local/dev parity for app + database
services:
  api:
    build: { context: ., dockerfile: Dockerfile }
    image: medbook:dev
    ports: ["8080:8080"]
    env_file: .env.dev
    environment:
      SPRING_DATASOURCE_URL: jdbc:postgresql://db:5432/medbook
      SPRING_DATASOURCE_PASSWORD: ${DB_PASS:?set DB_PASS in .env.dev}
    depends_on:
      db: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:8080/actuator/health || exit 1"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 25s
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "1.5", memory: 768M } } }

  db:
    image: postgres:16.2-alpine3.19
    environment:
      POSTGRES_DB: medbook
      POSTGRES_USER: medbook
      POSTGRES_PASSWORD: ${DB_PASS}
    volumes:
      - dbdata:/var/lib/postgresql/data          # survives `down`; `down -v` deletes it
      - ./db/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U medbook -d medbook"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  dbdata:
```

```bash
docker compose config -q          # resolve + interpolate FIRST: catches missing env in 0.2s
docker compose up -d --build
docker compose ps
docker compose logs -f api
docker compose exec db psql -U medbook -d medbook -c 'select count(*) from appointment;'
docker compose down               # keep data ·  `down -v` deletes volumes (say it out loud first)
```

**What just happened, in order:** `config` fails fast on a missing `${DB_PASS}` instead of leaving a container in a restart loop; `depends_on: service_healthy` waits for `pg_isready` so the app's first connection succeeds; the named volume means `down`/`up` doesn't lose rows; `up -d --build` is the loop you will repeat forty times per project.

## “Why is my image 1.4 GB?” — and the shrinking order

```bash
docker history medbook:v1.4.0 | head -25
docker buildx build --progress=plain -t medbook:v1.4.0 . 2>&1 | tail -40
```

1. **Multi-stage** — drop the build toolchain. ~-60 %.
2. **Smaller base** — `-jre` → alpine → distroless/Chainguard.
3. `apt-get install -y --no-install-recommends && rm -rf /var/lib/apt/lists/*`.
4. One `RUN` per logical step with `&& \` continuations (fewer layers, each self-cleaning).
5. `.dockerignore` (below).
6. `COPY --link --chown=10001:10001` so you don't need a `chown -R` layer that duplicates every file.

```gitignore
# .dockerignore — the file that fixes slow, fat and leaky builds at once
.git
.gitignore
target/
**/node_modules
**/*.log
.dockerignore
Dockerfile
docker-compose.yml
.env
.env.*
!.env.example
secrets/
*.pem
id_rsa
```

::: callout warn THE SECRET-IN-LAYERS TRAP (asked in nearly every security review)
`COPY . .` from a directory containing `.env` or `~/.aws/credentials` puts those files **in a layer**. A later `RUN rm .env` does not remove them — `docker history`, `docker save`, or anyone with pull access can read that layer forever. Fixes: `.dockerignore` (prevention), BuildKit secrets `RUN --mount=type=secret,id=dbpass …` (runtime only, never a layer), and no `AWS_ACCESS_KEY_ID` baked into a task definition — use an IAM role.
:::

## Production scenario — `ImagePullBackOff` right after a deploy

21:04, `kubectl get pods` shows `ImagePullBackOff` on the new deployment.

1. `kubectl describe pod medbook-… | sed -n '/Events/,$p'` → `pull access denied for 123…dkr.ecr.eu-west-1.amazonaws.com/medbook, repository does not exist or may require 'docker login'`.
2. That message means one of three things: **the tag was never pushed**, **auth** (node role lost `ecr:GetAuthorizationToken`/`BatchGetImage`, or the 12-hour token expired), or **network** (private subnet with no route to ECR → needs a VPC endpoint or NAT).
3. Disambiguate on the node: log in and `docker pull` the same reference → *works*. So creds and path are fine → **the tag doesn't exist**: the pipeline pushed `medbook:v1.4.0` while the manifest, after a repo rename, referenced `medbook-api:v1.4.0`.
4. Fix forward: correct the image reference in Helm values, redeploy, `kubectl rollout status deployment/medbook`.
5. Prevent: the pipeline computes the reference from the *same variable* it pushes to; a post-push `aws ecr describe-images --repository-name medbook --image-ids imageTag=v1.4.0` step so the **build** fails, not the cluster; ECR VPC endpoint for private subnets.
6. Runbook line: “ImagePullBackOff → describe pod → does the tag exist in the registry? → node auth → subnet route.”

## Common mistakes

::: checklist
- [ ] `COPY . .` with no `.dockerignore`: fat images, leaked `.env`, 900 MB context per build.
- [ ] `RUN npm ci` **after** `COPY . .` — cache busted on every commit. Copy the lockfile, install, then copy source.
- [ ] App data in the container layer → gone at the next `docker rm`.
- [ ] `docker stop` “hanging 10 seconds”: PID 1 isn't forwarding SIGTERM. Use `exec`/`--init` and handle it in the app (same reason K8s has `terminationGracePeriodSeconds`).
- [ ] `:latest` in a deployment manifest → no rollback, no traceability.
- [ ] Publishing a port to `0.0.0.0` on a VM “for testing” and forgetting. That is how a database ends up on the internet.
- [ ] No memory limit next to other containers → one leak and the kernel OOM-kills something random.
- [ ] Logging to a file inside the container → log rotation can't reach it. Log to stdout; let the platform own the file.
- [ ] `sudo docker` on the laptop and `USER root` in the image. Rootless Docker or your user in the `docker` group; always a non-root `USER`.
:::

## Best practices

::: grid2
**Pin everything.** Base image by digest (`FROM …jammy@sha256:…`) plus a scheduled Dependabot/Renovate PR to move it: reproducible *and* patchable.
**One concern per image.** The app image contains the app. Config comes from env/files; the database is a managed service in every project here.
**Healthcheck in the image AND a probe in the platform.** Compose/`docker ps` honour `HEALTHCHECK`; Kubernetes ignores it and needs its own readiness/liveness probes.
**Build in CI only.** Iterate locally with `docker compose up --build`; the artefact anyone may deploy is the one CI pushed, tagged and labelled.
**Scan and sign at build time**: Trivy in the same stage as the build, cosign sign before push. An unscanned, unsigned image should be *undeployable by policy*, not by discipline.
:::

## Interview answer

::: callout aha “What do you actually do with Docker on a project?”
“I take the developer's source and turn it into a deployable artefact. First I read the repo for the build system and runtime version, then I write or fix the Dockerfile: a pinned small base image, dependencies installed before the source copy so the layer cache works, a multi-stage build so the toolchain never ships, a non-root `USER`, a real `HEALTHCHECK`, and an entrypoint that forwards SIGTERM. I add a `.dockerignore` so the context stays small and no `.env` ends up in a layer. I build with two tags — release and `main-<sha>` — and put the git revision into image labels so `docker inspect` answers “what commit is running”. Before pushing I scan with Trivy and sign with cosign; the registry enforces immutable tags. To run it I publish only the ports that must be reachable, use `--env-file` for config, mount config read-only, set memory and CPU limits, and cap the log driver's size. State never goes in the container. Compose is my local parity harness for app + dependency, and the same image then goes to ECS, AKS or EKS unchanged.”
:::

::: grid2
**Follow-up: “Image vs container?”** → image = immutable layered filesystem + config; container = that image with a writable top layer plus its own namespaces and cgroup limits.
**Follow-up: “Why is my build slow, and how do you fix it?”** → cache order and context size: install dependencies from the lockfile before copying source, `.dockerignore`, BuildKit cache mounts or a registry cache, and multi-stage so heavy layers never appear in the final image.
**Follow-up: “A container exits immediately. Your steps?”** → `docker logs`, then `docker inspect --format '{{.State.ExitCode}} {{.State.OOMKilled}}'`; 137 + OOMKilled = memory limit, 1 with a stack trace = config/app, and *empty* logs means it wrote to a file instead of stdout. Reproduce interactively with `--entrypoint sh`.
:::

## Virtual lab — package a stranger's application (simulated terminal)

::: lab docker-1
:::

## Commands to remember

::: grid2
```bash
docker build -t repo/app:v1.0.0 \
  --label org.opencontainers.image.revision=$SHA .
docker history --no-trunc repo/app:v1.0.0
docker image ls --format '{{.Repository}}:{{.Tag}} {{.Size}}'
docker builder prune -f
```
```bash
docker run -d --name app -p 8080:8080 \
  --env-file .env --memory 768m --cpus 1.5 \
  --read-only --tmpfs /tmp \
  --log-opt max-size=10m --restart unless-stopped \
  repo/app:v1.0.0
docker exec -it app sh ; docker logs -f app
```
```bash
docker compose config -q
docker compose up -d --build
docker compose ps ; docker compose logs -f api
docker compose exec db pg_isready -U app
docker compose down      # down -v deletes volumes
```
:::

::: callout note WHAT I MUST REMEMBER
1. Image = the artefact you deploy; container = a disposable instance. Fix the image, never the container.
2. Cache order and `.dockerignore` are 80 % of build speed and half of image security.
3. Config comes from env/secret stores, data from volumes or managed services, and deployments reference an immutable tag or digest — never `:latest`.
:::

::: revision REVISION — 5 minutes
**Concepts:** layers and caching · build context vs `.dockerignore` · multi-stage · `EXPOSE` ≠ publish · namespaces/cgroups vs a VM · PID 1 signal handling · digest vs tag · registry auth (IAM/token/`docker login`) · image `HEALTHCHECK` vs platform probes · named volume vs bind mount vs tmpfs · log drivers · `depends_on: service_healthy` · rootless and non-root.
**Architecture to remember:** repo → Dockerfile → build (cached layers) → scan/sign → registry → orchestrator pulls by digest → container with limits and read-only FS → logs to stdout → health endpoint gates traffic.
**Common mistakes:** secrets in layers · `:latest` in prod · state in the writable layer · `sleep` instead of a healthcheck · unquoted `${VAR}` in compose · shipping the build toolchain.
**Troubleshooting checklist:** `docker logs` → `docker inspect` (exit code, OOMKilled, health, mounts) → `docker history` (size/layers) → `docker exec`/override entrypoint → `docker build --progress=plain` → registry: does the tag exist? auth? route?
:::

Next: [Cloud core concepts →](p1-cloud-core.html)
