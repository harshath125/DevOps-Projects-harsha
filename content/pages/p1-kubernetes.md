LEAD: Kubernetes is a reconciliation loop with an API in front. You declare a desired state; controllers keep comparing reality to it and act. Everything that confuses people — rollouts that stall, pods stuck `Pending`, services with no endpoints, “it restarted but nothing changed” — is that loop behaving exactly as designed. Learn the loop and the objects become obvious.

::: flow
kubectl apply → API server validates → etcd stores → controller sees a gap → schedules pod → kubelet on the node pulls the image → container runs → readiness passes → EndpointSlice updated → Service starts sending traffic
:::

## Control plane vs data plane, and what breaks when

| Plane | Pieces | If it dies |
| :-- | :-- | :-- |
| **Control plane** | API server (the only thing that talks to etcd), etcd (the cluster's memory), scheduler (pod → node), controller managers (Deployment/StatefulSet/Namespace logic), cloud-controller-manager (LBs, node lifecycle) | Existing pods keep serving traffic; **you can't change anything.** `kubectl` hangs. In EKS/AKS/GKE it's managed, patched and HA — which is the entire argument for managed Kubernetes. |
| **Data plane** | kubelet (node's agent; owns pods, probes, mounts), kube-proxy or an eBPF dataplane (Service → iptables/IPVS rules), CNI (pod IPs, inter-pod network), container runtime | Pods on that node stop being reconciled; a taint-based eviction removes workloads after ~5 min; node dies with its local `emptyDir`. |

Three consequences to internalise: (1) workloads survive their control plane, so “the cluster is down” usually means “the API is down”; (2) the **kubelet is what restarts your container** — a CrashLoopBackOff is a decision made on a node, not in the cloud console; (3) DNS is a *workload* (CoreDNS), so a cluster can be “healthy” while nothing can resolve a name.

## The objects you'll use daily, in one table

| Object | What it's for | The mistake everyone makes once |
| :-- | :-- | :-- |
| **Pod** | One or more containers sharing a network + volumes | Treating it as a long-lived VM. It's cattle: restart it, replace it, expect it to die. |
| **Deployment** | Stateless pods at N replicas with rollout/rollback | `strategy.maxUnavailable` left at 25 % on a 2-replica app → 25 % of 2 rounds to 0 unavailable but also 0 new capacity if you cap `maxSurge`. |
| **Service** | A stable name + virtual IP in front of pods | `selector` not matching pod labels → `Service` exists, `Endpoints` empty, clients get connection refused. |
| **ConfigMap / Secret** | Config as env or mounted files | Expecting a file-mounted ConfigMap change to reload the app. It doesn't — roll the pods (or checksum the name and use the `reloader` pattern). |
| **Ingress / Gateway API** | HTTP routing and TLS from outside | No `ingressClassName`, or the class controller missing → the resource applies and does nothing at all. |
| **HPA** | Scale on CPU/custom metrics | No `resources.requests.cpu` → the percentage is undefined and the HPA prints `<unknown>`. |
| **PVC / PV / StorageClass** | Claims and provisioned volumes | A `ReadWriteOnce` PVC bound to a zonal EBS/PD volume: pod can never move to another AZ. That's why stateful data belongs to a managed database. |
| **Namespace** | A boundary for quotas, policies and DNS scope | Putting “prod” and “staging” in one namespace “for convenience”. |
| **ServiceAccount + RBAC** | Identity + permission inside the cluster | `automountServiceAccountToken: true` on every pod by default → any pod can read the API. Turn it off where it's not needed. |
| **NetworkPolicy** | Pod-to-pod allow lists | Assuming it does something. Without a CNI that enforces it (Calico, Cilium, Azure NPM, AWS VPC CNI network policy mode), your YAML is a wish. |
| **StatefulSet** | Stable names, ordered, sticky storage | Using it for anything you could run statelessly. Complexity you must earn. |
| **DaemonSet** | One pod per node (log shipper, node exporter) | No tolerations for control-plane taints → “why is the agent missing on 2 nodes”. |
| **Job / CronJob** | Batch, and schedules | Missing `concurrencyPolicy: Forbid` + `startingDeadlineSeconds`, so a job never runs and nobody is told. |

## Reading a pod's life cycle: the five fields that explain everything

```bash
kubectl get pods -o wide
NAME                      READY   STATUS             RESTARTS   AGE   IP          NODE
medbook-6f8d9c7d5-2xkqz   0/1     CrashLoopBackOff   5          7m    10.1.4.88   ip-10-0-12-31
```

| Field | What it proves |
| :-- | :-- |
| `0/1 READY` | Readiness probe is failing. Traffic is *not* going here (good — that's the probe doing its job). |
| `STATUS` | Where it died in the loop: `Pending` = scheduling or volumes; `ContainerCreating` = image pull, CNI, mount; `Running` but `0/x` = app or probe; `CrashLoopBackOff` = it exits after start, exponential backoff up to 5 min; `ImagePullBackOff` = registry/tag/auth; `OOMKilled` = memory limit; `Evicted` = node pressure. |
| `RESTARTS: 5` | It has died 5 times and is in backoff; not “it's starting up”. |
| `describe pod` | Events + `Last State: Terminated, Reason: Error, Exit Code: 1, OOMKilled: false` — the two most useful lines in the whole tool. |
| `logs --previous` | The stack trace from the *dead* container. The one thing that turns 40 minutes into 4. |

```bash
kubectl describe pod medbook-6f8d9c7d5-2xkqz | sed -n '/Events:/,$p'
kubectl logs medbook-6f8d9c7d5-2xkqz --previous --tail=50
kubectl exec -it medbook-6f8d9c7d5-2xkqz -- sh
kubectl top pod --containers ; kubectl get events --sort-by=.lastTimestamp | tail -20
```

## Deployments, services and ingress: the YAML you must be able to write from memory

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: medbook
  labels: { app: medbook, tier: backend }
spec:
  replicas: 3
  revisionHistoryLimit: 5
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }   # never serve fewer than N healthy
  selector: { matchLabels: { app: medbook } }
  template:
    metadata:
      labels: { app: medbook, tier: backend }
    spec:
      serviceAccountName: medbook
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        fsGroup: 10001
        seccompProfile: { type: RuntimeDefault }
      topologySpreadConstraints:                # spread across AZs, don't lose 100 % at once
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: ScheduleAnyway
          labelSelector: { matchLabels: { app: medbook } }
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                topologyKey: kubernetes.io/hostname
                labelSelector: { matchLabels: { app: medbook } }
      containers:
        - name: medbook
          image: 123456789012.dkr.ecr.eu-west-1.amazonaws.com/medbook@sha256:9c1f...   # digest, not tag
          imagePullPolicy: IfNotPresent
          ports: [{ containerPort: 8080, name: http }]
          env:
            - name: DB_URL
              valueFrom: { secretKeyRef: { name: medbook-db, key: url } }
            - name: JAVA_OPTS
              valueFrom: { configMapKeyRef: { name: medbook-config, key: java-opts } }
          resources:
            requests: { cpu: 500m, memory: 768Mi }     # scheduling maths AND an OOM ceiling
            limits:   { cpu: "1",  memory: 1Gi }
          startupProbe:                                  # slow JVM: 5 min of patience, then it's broken
            httpGet: { path: /actuator/health/readiness, port: http }
            failureThreshold: 30
            periodSeconds: 10
          readinessProbe:
            httpGet: { path: /actuator/health/readiness, port: http }
            periodSeconds: 10
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /actuator/health/liveness, port: http }
            periodSeconds: 20
            timeoutSeconds: 3
            failureThreshold: 3                          # deliberately looser than readiness
          lifecycle:
            preStop: { exec: { command: ["sh","-c","sleep 8"] } }  # let LB deregistration propagate
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
            - { name: config, mountPath: /config, readOnly: true }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
      volumes:
        - { name: tmp, emptyDir: { sizeLimit: 256Mi } }
        - name: config
          configMap: { name: medbook-config }
---
apiVersion: v1
kind: Service
metadata: { name: medbook }
spec:
  type: ClusterIP
  selector: { app: medbook }              # must match pod labels
  ports: [{ name: http, port: 80, targetPort: http, protocol: TCP }]
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: medbook
  annotations: { nginx.ingress.kubernetes.io/proxy-body-size: "16m" }
spec:
  ingressClassName: alb                    # or nginx — a class with no controller does nothing
  tls: [{ hosts: [medbook.example.com], secretName: medbook-tls }]
  rules:
    - host: medbook.example.com
      http:
        paths:
          - { path: /, pathType: Prefix, backend: { service: { name: medbook, port: { name: http } } } }
```

::: callout why THE THREE PROBES ARE AN ARCHITECTURE DECISION, NOT A CHECKBOX
`startupProbe` protects slow starts (JVM, migrations) so liveness doesn't kill a booting app. `readinessProbe` controls **traffic** — it should fail when the app can't serve (DB down, thread pool exhausted) and can flap harmlessly. `livenessProbe` controls **life** — it must only fail on conditions a restart can fix (deadlock, wedged event loop); a liveness check that hits the database will, during a DB outage, restart every pod at once and turn an outage into a stampede. Ask for `/actuator/health/liveness` (no external deps) for liveness and `/readiness` (deps included) for readiness; if your app exposes only one endpoint, you know which one to make the readiness.
:::

## Rollouts, scaling, and the two commands that actually save you

```bash
kubectl rollout status deploy/medbook --timeout=180s      # blocks until success or timeout (CI's best friend)
kubectl rollout history deploy/medbook
kubectl rollout undo deploy/medbook --to-revision=3
kubectl rollout pause deploy/medbook   # batch edits without triggering N rollouts
kubectl rollout resume deploy/medbook
kubectl scale deploy/medbook --replicas=6
kubectl patch hpa medbook -p '{"spec":{"minReplicas":3}}'
kubectl get rs -l app=medbook -o wide          # the old ReplicaSet is where rollback points
kubectl diff -f app.yaml                       # what would actually change, before it changes
kubectl rollout restart deploy/medbook         # “restart without changing anything” (use sparingly)
```

**The stuck-rollout reflex:** `kubectl rollout status` says `Waiting for deployment "medbook" rollout to finish: 1 old replicas are pending termination` — so the new pod exists but isn't ready. `get pods` → the new one is `0/1`, so it's the readiness probe. `logs --previous` → `FlywayValidateFailed: migration checksum mismatch`. The rollout isn't broken; **the app is refusing to be ready**, which is exactly what you paid the probe for. `kubectl rollout undo` and you're serving old code in 40 seconds — which is the story to tell in an interview.

## Networking, DNS, and how traffic gets in

::: cols
::: col
**Inside the cluster**
- Pod IP: ephemeral, from the CNI's range (EKS assigns real VPC IPs; AKS Azure CNI likewise; GKE alias IPs; Calico overlay uses a separate pool).
- `Service` = ClusterIP, a virtual IP rewritten by kube-proxy/Cilium to a random ready pod.
- DNS: `<svc>.<ns>.svc.cluster.local`; search domains make `http://medbook` work from the same namespace. `ndots:5` is why external lookups sometimes do four queries first.
- NetworkPolicy: `podSelector` + `policyTypes` + `ingress.from.podSelector/namespaceSelector/ipBlock`. Default-deny = a policy selecting all pods with no rules.
:::
::: col
**Coming in**
- `type: LoadBalancer` → CNI/cloud provisions an NLB/ALB (via the cloud-controller-manager); annotate it for internal, TLS,ProxyProtocol.
- `Ingress` → an ingress controller (nginx/ALB/Gateway API) that reads rules and configures the cloud LB.
- `NodePort` → 30000-32767 on every node; for tests and for on-prem, not prod.
- `externalIPs`/`hostNetwork`/`hostPort` → skip unless you truly need it (`hostNetwork` bypasses the CNI and the NetworkPolicy you thought you had).
- Gateway API (`Gateway` + `HTTPRoute`) → the forward-compatible replacement for Ingress; same mental model, richer filters (header-based routing, mirroring).
:::

```bash
# the “who can talk to whom” three checks
kubectl run curl --rm -it --image=curlimages/curl --restart=Never -- \
  -sSf http://medbook.default.svc.cluster.local/actuator/health
kubectl get endpointslice -l kubernetes.io/service-name=medbook   # empty = no traffic, ever
nslookup medbook.default.svc.cluster.local                        # from inside a pod
```

## Storage, stateful workloads and the honest advice

- `PersistentVolumeClaim` = “I need N GB of class X”; the StorageClass provisions a PV. Reclaim policy `Delete` means deleting the PVC deletes the disk: for anything that isn't disposable, `Retain`.
- `StatefulSet` gives ordinal names (`db-0`, `db-1`), ordered rollout/scale, a `volumeClaimTemplates` per replica, and a headless Service for stable DNS. Use it for the things you'd otherwise hand-config: Kafka, Postgres-in-a-cluster, Solr.
- `ReadWriteOnce` binds to one node; a zonal volume (gp3/EBS, Azure managed disk, GCP PD) pins a pod to that AZ. That is why we run databases as managed services (RDS/Azure Database/Cloud SQL) in every project here, and use S3/Blob/GCS + a cache for anything else.
- `emptyDir` is for scratch (and for `readOnlyRootFilesystem: true` pods that need `/tmp`). `ephemeral-storage` requests exist — set them, or one pod writing a 40 GB heap dump evicts everyone on the node.
- CSI snapshotting and `VolumeSnapshot` are the backup story *if* you put data in the cluster; “we have Velero” is only an answer when someone has restored it in a drill.

## Multi-cloud Kubernetes: EKS vs AKS vs GKE, what actually differs

| Area | EKS | AKS | GKE |
| :-- | :-- | :-- | :-- |
| Upgrades | Manual-ish per control plane, `upgrade-plan`; nodes you replace | `--auto-upgrade-channel patch` + surge node pool; max-surge defaults | Release channels (Rapid/Regular/Stable/Extended), node auto-repair; the nicest story |
| CNI | VPC CNI (pods = VPC IPs: great for ALB/SG-per-pod, burns IP space; prefix mode helps) | Azure CNI (same IP-burn issue; `overlay`/`azure-vnet` modes now) or Calico/Cilium | GKE native (alias IP ranges: no IP exhaustion, pods not in the VPC subnet by default) or Dataplane V2 (Cilium/eBPF) |
| IAM for pods | IRSA (annotation `eks.amazonaws.com/role-arn` + a webhook) → now `eks.amazonaws.com/access-entries`/pod-identity addons | AKS Workload Identity (label `azure.workload.identity/use: "true"` + a federated user-assigned MI) | Workload Identity (annotated K8s SA ↔ GCP SA) |
| Autoscaling | Cluster Autoscaler or **Karpenter** (right-sized, launch templates, consolidation) | Cluster Autoscaler or **VAC/Azure Node Auto-Provisioning** (Karpenter-derived) | Cluster Autoscaler or **Autopilot** (you buy pods, not nodes) |
| Registry pull | ECR + node role or the ECR credential helper | ACR attached with `--attach-acr` (role assignment done for you) | Artifact Registry + node SA scopes; `gke-auth` node role |
| Load balancer | ALB Ingress Controller (target groups, TG health checks) or NLB | App Gateway Ingress Controller (AGIC) or standard LB | Ingress with GCLB (health checks + NEG backends — the best-integrated of the three) |
| Secrets | KMS envelope encryption + CSI Secrets Store driver | Key Vault provider + optional data encryption at rest | Secret Manager via CSI; etcd encryption KMS |
| Cost view | per-node + Fargate | per-node; Spot with `eviction-policy Delete`, and a `--max-pods` IP plan | per-node; Autopilot bills per pod request (often *worse* for chatty requests — tune requests!) |

**Same YAML, four differences to respect:** ingress controllers and their annotations; how pods get cloud identity; CNI/IPAM capacity planning (Azure and AWS both need big subnets for pods); and autoscaler defaults.

## Production scenario — `OOMKilled` on a Java service that “uses 700 MB”

Monitoring shows a container at ~700 MB of a 1 Gi limit, killed every few hours; `kubectl top pod` never catches it (metrics scrape every 60 s, OOM is instant).

1. `kubectl describe pod … | grep -A5 'Last State'` → `Reason: OOMKilled`, exit 137. So the *cgroup limit* was hit, not the app crashing.
2. Why the mismatch? The JVM was given no memory awareness, so `-Xmx` defaulted to 25 % of the **node's** memory on old images, and on recent ones to a percentage of the *container* limit — but the pod also runs a sidecar and does a big heap dump on error, and the RSS of Metaspace + direct buffers + thread stacks isn't in `-Xmx`.
3. Fix: request/limit equal (`1Gi/1Gi` for deterministic scheduling), `MaxRAMPercentage=70` (leaving headroom for non-heap), and a `-XX:+ExitOnOutOfMemoryError` so a heap OOM becomes a clean restart rather than a zombie.
4. Guardrail: `LimitRange` in the namespace requiring requests+limits, an OOMKilled alert (`kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1`), and load-test-per-release so memory regressions fail a pipeline instead of a pagers at night.
5. Lesson generalised: **`limits.cpu` is a throttle, `limits.memory` is an execution.** Cap CPU if you want fairness; on memory, decide *deliberately* whether you'd rather be throttled-then-killed or killable-and-right-sized (GKE Autopilot and HPA-friendly practice: requests = limits for latency-sensitive services).

## Common mistakes

::: checklist
- [ ] **No `resources` at all** → BestEffort pods are the first to be evicted, the last to be scheduled under pressure, and the HPA is `<unknown>`.
- [ ] **Liveness = readiness = “app responds and DB is up”** → a database outage restarts everything, and the restart storm makes recovery slower.
- [ ] **`imagePullPolicy: Always` with `:latest`** → each node may be running a different build; rollbacks become guesswork. Use a digest or an immutable tag.
- [ ] **No `maxUnavailable: 0` on a 2-replica prod service** → during a rollout you briefly serve from one pod, and its retry storm is your incident.
- [ ] **`latest` + no `imagePullPolicy`** → the kubelet caches and “nothing updated” forever.
- [ ] **ConfigMap mounted, edited, “why isn't the app using it”** → it isn't; restart (or checksum-name it).
- [ ] **No PodDisruptionBudget** → a node drain takes the whole app. `minAvailable: 1` or `maxUnavailable: 1` is two lines.
- [ ] **`namespace: default` for everything** → no quota, no network policy scope, no blast radius, no clean `kubectl get all -n prod`.
- [ ] **Ingress with the wrong class** → HTTP 404 from the controller or a hang from nothing at all; `kubectl describe ingress` shows no address, and that's your clue.
- [ ] **A `ClusterRole: admin` bound to `default` SA** — or `runAsUser: 0` “to fix permissions” and leaving it.
- [ ] **`--insecure-skip-tls-verify`** pasted from Stack Overflow, on prod. Fix the CA or the context, not the flag.
:::

## Best practices

::: grid2
**Admission control is cheaper than code review.** Kyverno or Gatekeeper: “images from our registries only”, “no `:latest`”, “requests and limits required”, “drop ALL capabilities”. Fail fast at `apply`, and your team stops shipping the shape of bug you spent months fixing.
**`PodDisruptionBudget` + `topologySpreadConstraints` on anything user-facing.** Two lines each, and they're the difference between a node upgrade and an outage.
**GitOps (Argo CD or Flux) instead of `kubectl apply` from CI.** The cluster converges to what's in Git, drift is a sync diff you can see, and rollback is `git revert` — plus nobody has cluster admin credentials on a laptop.
**Namespaces with ResourceQuota + LimitRange + a default `NetworkPolicy: Deny`.** Then allowlist what's needed. “Sandboxed by default” is a security review's favourite sentence.
:::

## Interview answer

::: callout aha “How do you deploy to Kubernetes, and how do you debug a bad release?”
“Everything is declarative and comes from a pipeline: a Helm chart or Kustomize overlay per environment, an image reference by immutable tag or digest, requests and limits set, three probes — startup for slow JVM boot, readiness against an endpoint that includes dependencies, liveness against one that doesn't — `maxSurge: 1` and `maxUnavailable: 0` so we never drop below three healthy, a `PodDisruptionBudget`, spread across AZs with topology constraints, non-root with a read-only root filesystem and `drop ALL` capabilities, and config injected from ConfigMaps and Secrets via CSI or external-secrets rather than baked in. Traffic enters through an ingress class with TLS at the LB, and Argo CD owns the sync so rollback is a Git revert. When a release goes bad I go in a fixed order: `kubectl rollout status` to see where it's stuck, `get pods -o wide` for status and node, `describe pod` for Events and the Last State/exit code, and `logs --previous` for the stack trace from the dead container — then `get events --sort-by=.lastTimestamp` and `kubectl top` for saturation. `Pending` is scheduling or a volume; `ContainerCreating` is pull, CNI or a mount; `CrashLoopBackOff` is the app or a probe. Yesterday's example: the rollout stalled at `1 old replicas pending termination`, new pods were `0/1`, and `logs --previous` showed a Flyway checksum mismatch from a migration someone edited after review — so I rolled back in about 40 seconds, then the fix was a forward migration plus a CI check that flags edited migrations in review. I've also learned to distrust “it works locally”: I reproduce in a throwaway pod with `kubectl debug` and an ephemeral container rather than `kubectl exec`-ing into prod.”
:::

::: grid2
**Follow-up: “Deployment vs StatefulSet?”** → Deployments treat pods as interchangeable and can replace any of them; StatefulSets give stable names, ordered rollouts and per-replica storage for things that remember who they are.
**Follow-up: “Service vs Ingress?”** → a Service is a cluster-internal virtual IP + load balancing to pods; an Ingress (or Gateway) is HTTP routing and TLS from outside, backed by a controller that programs a cloud LB.
**Follow-up: “What is a CNI?”** → the plugin that gives pods IPs and connectivity; on AWS/Azure pods usually consume VPC IPs (so plan subnet size), on GKE they use alias ranges.
**Follow-up: “How do you do a zero-downtime deploy?”** → readiness gating + `maxUnavailable: 0` + a `preStop` sleep for deregistration lag + connection draining on the LB + graceful shutdown in the app; and an automated `rollout status --timeout` so the pipeline fails loudly if a rollout stalls.
:::

## Virtual lab — deploy, break and fix on a cluster (simulated terminal)

::: lab k8s-1
:::

## Commands to remember

::: grid2
```bash
kubectl get pods -o wide ; kubectl get events --sort-by=.lastTimestamp | tail
kubectl describe pod X | sed -n '/Events:/,$p'
kubectl logs X -c container --previous --tail=100
kubectl exec -it X -- sh ; kubectl cp X:/tmp/dump.hprof .
kubectl get deploy,rs,svc,ep,ing -l app=medbook
```
```bash
kubectl apply -f app.yaml --dry-run=server -o yaml
kubectl rollout status/history/undo deploy/app
kubectl auth can-i --list ; kubectl top pod --containers
kubectl get pvc,pv,sc ; kubectl get netpol,limitrange -n prod
kubetail -n prod -l app=medbook        # or stern, for multi-replica logs
```
```bash
# offline + local, the two commands that make Kubernetes teachable
kubeconform -summary manifests/
kind create cluster --config kind-3node.yaml && kubectl port-forward svc/ingress 8080:80
```
:::

::: callout note WHAT I MUST REMEMBER
1. Requests schedule; limits bound. Memory over the limit = kill; CPU over = throttle.
2. Probes decide traffic and life: readiness for the LB, liveness only for restart-fixable states.
3. Rollout problems are pod problems: `describe` + `logs --previous` explain ~90 %.
4. Deployed ≠ reachable. `Endpoints` empty is a real state, and so is an Ingress with no address.
5. GitOps: the cluster should be a mirror of Git, not a collection of things people did.
:::

::: revision REVISION — 5 minutes
**Concepts:** reconcile loop · control vs data plane · Deployment/Service/ConfigMap/Secret/Ingress/HPA/PVC/NS/RBAC/NetworkPolicy/PDB · rolling strategy and `revisionHistoryLimit` · probes ×3 · requests vs limits and QoS classes · Service types and `externalTrafficPolicy: Local` · DNS search domains and `ndots` · CNI IP planning · IRSA/Workload Identity · Karpenter/cluster-autoscaler/Autopilot · `kubectl diff`/`rollout undo` · admission policy · GitOps sync health.
**Architecture to remember:** client → DNS → cloud LB + ingress controller → Service → pod (app + sidecar) with probes, PDB, limits → ConfigMap/Secret injected → logs to DaemonSet shipper → metrics scraped → alerts → and Argo CD watching Git above it all.
**Common mistakes:** no limits · liveness that depends on the DB · `:latest` · no PDB · `default` namespace · ingressClass typo · editing ConfigMaps expecting a reload · `kubectl delete pod --all` “to fix it” on a Friday.
**Troubleshooting checklist:** `get pods -o wide` → status → `describe` events + last state + exit code → `logs --previous` → `get events` → `top` → `get endpointslice` → `auth can-i` → `rollout history` → nodes (`describe node` for `MemoryPressure`/`DiskPressure`, `kubectl get cs`-style health) → and only then the cluster's platform logs (control-plane logs in CloudWatch/Log Analytics/Cloud Logging).
:::

Next: [CI/CD with Jenkins + GitHub Actions →](p1-cicd.html)
