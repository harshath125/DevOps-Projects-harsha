LEAD: Nine out of ten “my application is broken in the cloud” tickets are really “I don't understand how a packet gets from a browser to my process”. This page gives you the smallest set of networking facts that makes every cloud tutorial readable, plus the shell habits that turn your manual work into a script.

## The mental model — five layers, four questions

::: flow
Browser types https://api.medbook.io
1. DNS: what IP is that name?
2. Routing: does a path exist to that IP? (subnet → route table → IGW/NAT/peering)
3. Firewall: is the port open to that source? (security group / NSG / firewall rule)
4. Listener: is a process bound to 0.0.0.0:port?
5. Application: does it answer with the status you expected, in time?
:::

Every network problem is one of those five. Your job in an incident is to find which one — in that order — with a command instead of a guess.

## The facts you must know cold

| Concept | Plain English | Command that proves it |
| :-- | :-- | :-- |
| IP address | the street number of a machine on a network | `ip -4 addr show` |
| CIDR `10.0.1.0/24` | a block of addresses; `/24` = 256, `/16` = 65 536 | read the subnet list |
| Private ranges | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` — not routable on the internet; this is why subnets are private and the balancer is public | `ip route` |
| Port | which *process* on that machine. 22 SSH · 80 HTTP · 443 HTTPS · 3306 MySQL · 5432 Postgres · 27017 Mongo · 6379 Redis · 8080 app · 9000 SonarQube · 9090 Prometheus | `ss -lntp` |
| TCP handshake | SYN → SYN/ACK → ACK. No ACK = nothing is listening or something is dropping you | `nc -zv host 5432` |
| DNS A vs CNAME | A → an IP; CNAME → another name; an apex A record cannot be a CNAME (hence AWS “Alias” records) | `dig +short api.x.io` |
| TTL | how long resolvers cache the answer. Lower it *before* a migration, raise it after | `dig +noall +answer x.io` |
| HTTP status | 2xx fine · 3xx redirect · **4xx your request is wrong** · **5xx the server failed** | `curl -i` |
| TLS | the certificate proves identity; the private key never leaves the terminator; traffic is decrypted at the proxy and re-encrypted or not onward | `openssl s_client -connect host:443 -servername host` |
| Latency vs bandwidth | 40 ms round trip vs 500 Mbps. Slow *first byte* is latency or backend work; slow *whole file* is bandwidth | `ping`, `curl -w` |

::: callout why WHY A LOAD BALANCER CHANGES THE MENTAL MODEL
Without a balancer: DNS → box. With one: DNS → balancer (it terminates TCP and TLS) → target group → instance:port. So a **502 from the balancer is the balancer telling you it could not get a valid response from a target.** The certificate lives on the balancer. Health checks run from the balancer. “The balancer's security group” and “the instance's security group” are two rules, and the instance one must allow the *balancer's group*, not the internet. That paragraph is worth an hour of blog reading.
:::

## The status codes a DevOps engineer is judged on

| Code | Operations meaning | Where to look first |
| :-- | :-- | :-- |
| `301/302/308` | redirect — is it a loop (http→https→http)? | listener rules, the app's base-url config |
| `401/403` | your request is fine, your *identity* isn't | token, IAM policy, bucket policy, WAF rule |
| `404` | path unknown — often a missing `/api` prefix behind a proxy | ingress/ALB path rules |
| `408` | client took too long to send | idle timeouts, mobile clients |
| `429` | rate limited (or your WAF decided so) | API gateway throttling, app limiter |
| `499` | **client hung up before you answered** (Nginx) | latency, upstream timeouts vs client patience |
| `500` | application exception | app log at that second, first error + deepest `Caused by` |
| `502` | bad gateway: proxy got no valid response | is the app up? `ss -lntp` · app log · keepalive vs `proxy_read_timeout` |
| `503` | no healthy target / drained | balancer health state, target registration, readiness probe |
| `504` | gateway timeout: the app didn't answer in time | slow SQL, external API, timeout ladder (see below) |

::: step STEP 1 — The five-command reachability ladder
Run them in this order. Each one cuts the world in half.

```bash
# 1 · does the name resolve, and to what?
dig +short api.medbook.io
10.0.2.15          # a private IP → you are testing the internal path; a public one → external

# 2 · is the TCP port open at all?
nc -zv 10.0.2.15 443
Connection to 10.0.2.15 443 port [tcp/https] succeeded!
#  succeeded → firewall and listener are OK, go to 3
#  timed out  → silently dropped: security group / NACL / no route
#  refused    → you reached the host and nothing is listening

# 3 · does TLS terminate and is the certificate believable?
curl -vI https://api.medbook.io/health 2>&1 | sed -n '1,25p'
* SSL certificate verify ok.
< HTTP/2 200

# 4 · what does the app say, and where did the time go?
curl -sS -o /dev/null -w 'code=%{http_code} dns=%{time_namelookup} connect=%{time_connect} ttfb=%{time_starttransfer} total=%{time_total}\n' https://api.medbook.io/health
code=200 dns=0.004 connect=0.021 ttfb=0.186 total=0.187

# 5 · on the box itself: who is listening where?
ss -lntp | grep -E '443|80|8080'
LISTEN 0 511 0.0.0.0:443  users:(("nginx",pid=1190,fd=6))
LISTEN 0 100 *:8080       users:(("java",pid=24631,fd=48))
```

**WHAT JUST HAPPENED.** You separated DNS from routing from firewall from listener from application. If step 2 times out you never need to read the app log. If step 5 shows `127.0.0.1:8080` while the proxy expects `0.0.0.0`, no firewall change will ever fix it.
:::

::: callout fix TIMEOUT vs REFUSED — the most useful 30 seconds of your incident
- **Connection refused** = the packet reached the machine and the kernel said “nobody home”. Fix: start the process, bind the right interface/port (`server.address=0.0.0.0`, FastAPI `--host 0.0.0.0`, `bind 0.0.0.0`), or correct the target port in the target group.
- **Connection timed out** = the packet was swallowed. Fix: security group inbound, NACL, route to IGW/NAT, or the instance sits in a private subnet with no path. Also check on-box: `sudo iptables -L -n`, `nft list ruleset`.
:::

## Shell scripting — the habits, then a script you can copy

A deployment script is not programming. It is: check inputs → do a thing → verify → tell me.

```bash
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

APP_NAME="${APP_NAME:?APP_NAME is required}"          # fail loudly, now, not at 03:00
IMAGE="${IMAGE:?IMAGE is required}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8080/health}"
LOG() { printf '%s [%s] %s\n' "$(date -u +%H:%M:%S)" "$1" "$2"; }

command -v docker >/dev/null || { LOG ERROR "docker not installed"; exit 1; }
[[ "$ENVIRONMENT" =~ ^(dev|stage|prod)$ ]] || { LOG ERROR "bad ENVIRONMENT=$ENVIRONMENT"; exit 1; }

docker rm -f "$APP_NAME" 2>/dev/null || true          # idempotent: safe to re-run
LOG INFO "starting $IMAGE in $ENVIRONMENT"
docker run -d --name "$APP_NAME" --restart unless-stopped \
  -p 8080:8080 --env-file "/etc/$APP_NAME/$ENVIRONMENT.env" \
  "$IMAGE"

for i in {1..30}; do
  curl -fsS "$HEALTH_URL" >/dev/null 2>&1 && { LOG OK "healthy after ${i}s"; exit 0; }
  sleep 1
done
LOG ERROR "not healthy after 30s — last logs:"
docker logs --tail 60 "$APP_NAME"
exit 1                                                # a failing pipeline is a useful pipeline
```

| Piece | Why it is there |
| :-- | :-- |
| `set -euo pipefail` | Without it, a script that fails on line 3 happily runs to line 40 and reports success. Non-negotiable first line. |
| `${VAR:?msg}` | Fail immediately when the environment forgot something, instead of deploying with `DB_HOST=""`. |
| `command -v docker` | Prove the tool exists before using it; the message tells the next person what to install. |
| `|| true` on `docker rm` | Idempotency. Re-running a deploy must be boring. |
| the 30-second curl loop | Deploy and **wait for healthy**. `sleep 5 && hope` is not a check. |
| `docker logs --tail 60` | Put the evidence in the pipeline output. This one line saves the most time. |
| `exit 1` | Without a non-zero exit, Jenkins marks a broken deploy green. |

**Quoting in one table.** `"$x"` keeps spaces and empties safe · bare `$x` splits and globs · `'...'` is literal · `$'...'` interprets escapes · use `[[ ]]` in bash and `[ ]` only for POSIX sh. If you remember one line: **quote every expansion.**

## Production scenario — “the pod can reach the internet but not our Postgres”

1. In the pod: `nc -zv pg-primary.internal 5432` → **timed out**. So it is network/firewall, not credentials.
2. `getent hosts pg-primary.internal` → `10.0.4.21`, a private address in another subnet.
3. The database's security group allows 5432 from `sg-app-legacy`, but after a cluster upgrade the pods now come from the *node* group `sg-eks-nodes`.
4. Fix now: `aws ec2 authorize-security-group-ingress --group-id sg-0db --protocol tcp --port 5432 --source-group sg-eks-nodes`.
5. Fix properly: the same rule in Terraform, or your next apply deletes it and the ticket comes back on Friday.
6. Verify from a pod; delete the stale rule; write the runbook line — “if a pod can't reach a managed DB, check the SG **source group**, not the app”.

`connection refused` on a healthy database means nothing is bound on that port; **timeout** means the firewall is pointing at an old security group. Most of real operations is knowing which of those two you are looking at.

## Common mistakes

::: checklist
- [ ] Testing with `ping` and declaring “network is fine”. A box answers ICMP with every TCP port closed. Test the **port**.
- [ ] Blaming the cloud firewall before checking `ss -lntp` for an actual listener.
- [ ] `curl http://localhost` *inside* the pod to test a Service, then calling it verified — you tested your own container, not the ingress path.
- [ ] Hard-coding private IPs in scripts or configs. IPs change on rebuild; use names (private zone, Service DNS, managed DB endpoint).
- [ ] Forgetting that a **security group is stateful** (return traffic automatic) while a **NACL is stateless** (you must open both directions). Classic “SSH works out but not in”.
- [ ] Raising DNS TTL from 300 s to 48 h *during* a migration instead of the day before.
- [ ] No timeouts anywhere: `curl` without `--max-time`, `apt` without `Acquire::http::Timeout` → hung deploys nobody kills.
- [ ] Treating `0.0.0.0` and `127.0.0.1` as the same word. `0.0.0.0` = reachable from anywhere the routes allow; `127.0.0.1` = this box only.
:::

## Best practices

::: grid2
**Names before IPs, everywhere.** Private hosted zones, Service DNS, CNAMEs. Names survive rebuilds.
**One health endpoint, honestly implemented.** `/health` = process up; `/ready` = dependencies reachable. Wire readiness to the balancer, liveness to the restart logic. Returning `UP` while the DB is down is how you get a green dashboard and a dead site.
**Timeouts get longer as you go inward**: client > balancer > proxy > app > DB driver. A 30 s proxy in front of a 60 s app produces 504s that look like application bugs.
**Document the port map in the repo.** Six lines — component, port, source, why open — answers most review questions before they're asked.
:::

## Interview answer

::: callout aha “A user reports HTTP 502 from the load balancer. Walk me through it.”
“First, scope and time: one user or all, since when, and did anything deploy. 502 means the proxy couldn't get a valid response from a target, so I go to the target, not the browser. Is the service running (`systemctl status`), and listening on the expected port and interface (`ss -lntp`)? If nothing is listening, the app log at the incident timestamp explains it — usually a start-up exception or an OOM kill (`dmesg | grep -i kill`). If something *is* listening, I curl the health endpoint from the host itself. Local success plus balancer failure means the path between them: is the target registered and healthy, does the instance security group allow the *balancer's* group on that port, and is the health-check path and port correct? If it works locally but intermittently, I compare timeouts — a 40 s endpoint behind a 30 s proxy produces exactly this. Then I fix, verify with repeated curls and the balancer's metrics, and add the prevention: a readiness probe, the corrected health-check path in Terraform, or matched timeouts, plus the runbook entry.”
:::

::: grid2
**Follow-up: “Security group vs NACL?”** → SG: stateful, attached to the instance/ENI, allow-only, all rules evaluated together. NACL: stateless, attached to the subnet, allow *and* deny, evaluated in numbered order — so you must open ephemeral return ports yourself.
**Follow-up: “What does a NAT gateway do?”** → instances in a private subnet have no route to the internet gateway; the subnet's route table sends `0.0.0.0/0` to the NAT gateway in a public subnet, which masquerades the traffic out. Without it, `apt-get`, `docker pull` and package installs from private subnets simply hang.
**Follow-up: “Why does DNS matter for zero-downtime failover?”** → TTL is how long clients keep using the old answer. Lower it before the change, keep the old path alive through the window, and let health-checked records (Route 53 Alias with `EvaluateTargetHealth`, Traffic Manager, Cloud DNS) move traffic automatically.
:::

## Virtual lab — network diagnosis (simulated terminal)

::: lab net-1
:::

## Commands to remember

::: grid2
```bash
dig +short host.io
getent hosts host.io
nc -zv host 5432
ss -lntp ; ss -tnp
ip a ; ip r ; traceroute -T -p 443 host
```
```bash
curl -i https://host/health
curl -fsS --max-time 5 -o /dev/null \
  -w '%{http_code} %{time_total}\n' URL
openssl s_client -connect host:443 \
  -servername host </dev/null
tail -f /var/log/nginx/error.log
```
```bash
# shell hygiene
set -euo pipefail
: "${VAR:?need it}"
for f in *.log; do :; done
while IFS=, read -r a b; do :; done < in.csv
awk '{print $7}' access.log | sort | uniq -c | sort -rn | head
```
:::

::: callout note WHAT I MUST REMEMBER
1. DNS → route → firewall → listener → application. Walk the ladder; never jump to “it's the cloud”.
2. `refused` = nothing listening; `timed out` = blocked on the way.
3. Every script starts with `set -euo pipefail` and ends with a real verification, not a `sleep`.
:::

::: revision REVISION — 5 minutes
**Concepts:** CIDR and private ranges · TCP handshake · stateful SG vs stateless NACL · A vs CNAME vs Alias · TTL · listener bind address · health vs readiness · 4xx vs 5xx and the 499/502/503/504 specifics · TLS termination at the balancer · latency vs bandwidth · `0.0.0.0` vs `127.0.0.1`.
**Commands:** `dig`, `nc -zv`, `ss -lntp`, `curl -w`, `openssl s_client`, `iptables -L -n`, `set -euo pipefail`, `command -v`, `for i in {1..30}`, `awk|sort|uniq -c|sort -rn|head`.
**Architecture to remember:** browser → DNS → balancer (TLS terminates, health checks) → target group → instance:port → app → DB in a private subnet with an SG source of *app SG*.
**Common mistakes:** trusting `ping` · testing only from inside · hard-coded IPs · TTL raised mid-migration · proxy timeout shorter than app timeout · unquoted `$var` · `sleep 5` instead of a health loop.
**Troubleshooting checklist:** who can reach it? which port? is it listening? does it answer locally? does the balancer think it is healthy? what did the log say at that second? — then fix in IaC, verify twice, document once.
:::

Next: [Git + GitHub →](f-git-github.html)
