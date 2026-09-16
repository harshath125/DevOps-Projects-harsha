LEAD: You will live in this shell for your whole DevOps career. Not to become a Linux administrator — you are not here to manage 500 servers by hand — but because every application you receive, every container you build and every cloud VM you provision sits on Linux, and when something breaks at 2 a.m. the only thing between you and the answer is `ssh` plus these commands.

You need roughly 35 Linux commands. Not 300. This page is those 35, in the order you will actually use them, with the failure patterns that beginners lose whole days to.

## What a Linux server is, from a DevOps point of view

::: callout why WHAT IT IS, IN ONE PARAGRAPH
A Linux server is just another person's computer that you control through a text door (SSH). It has a filesystem, users, running processes, listening ports, disks and log files. That is your entire mental model. Everything else — systemd, SELinux, cgroups, namespaces — is detail you learn when it blocks you.
:::

::: flow
You (laptop) → ssh → cloud VM (Linux)
inside the VM: a process listens on a port
a firewall / security group decides who may reach that port
the app writes logs to /var/log (or journald)
disk, CPU and memory are finite → 70% of “mysterious” failures live here
:::

**Mental model to keep.** An application on a server does exactly three observable things:

1. it runs as a **process** (`ps`, `top`, `systemctl status`),
2. it listens on a **port** (`ss -lntp`, `curl`),
3. it writes to **files and logs** (`ls`, `cat`, `tail`, `journalctl`).

When something is “down”, one of those three is wrong. Always.

## The 35 commands, grouped by job

| Job | Commands | You will use them |
| :-- | :-- | :-- |
| Where am I? | `pwd` `ls -la` `cd` `find . -maxdepth 2 -type f` `du -sh *` | every day |
| Read files | `cat` `less` `head` `tail -f` `grep -R` `wc -l` `sort` `uniq -c` `cut` | every day |
| Edit / create | `nano` `touch` `cp` `mv` `mkdir -p` `sed -i` | daily |
| Who may do what | `id` `whoami` `ls -l` `chmod` `chown` `sudo -l` | whenever “Permission denied” appears |
| Processes | `ps aux` `top` `htop` `pgrep -af` `kill` `kill -9` `pkill -f` | every incident |
| Services | `systemctl status/start/stop/restart/enable` `journalctl -u` `systemctl cat` | every deploy |
| Networking | `ip a` `ip r` `ss -lntp` `ping` `curl -v` `dig` `nc -zv` | every “unreachable” |
| Disks | `df -h` `df -i` `du -sh` `lsblk` `lsof +L1` `mount` | the silent killer |
| Users/groups | `useradd` `usermod -aG` `getent group` `passwd -l` | hardening, CI agents |
| Archives | `tar -czvf` `tar -xzvf` `gzip` `scp` `rsync -avz` | backups, moving builds |
| Packages | `apt-get install -y` `dnf/yum install` `apt list --installed` | setup scripts |

::: callout note You do not need more than this to start
If you can read a directory listing, follow a log, check what is listening and restart a service, you are already more useful than a large fraction of the “Linux experienced” lines on CVs. Depth comes from doing; the lab below is the doing.
:::

## STEP 1 — Look around before you touch anything

::: step STEP 1 — Orientation
::: cols
::: col
**CONTEXT.** You logged into a VM somebody else created, running an application somebody else wrote. Do not type `rm`, do not “just restart it”. Look first.

**COMMAND**

```bash
pwd
ls -la
find . -maxdepth 2 -type f | head -40
du -sh * 2>/dev/null | sort -h | tail -10
```
:::
::: col
**EXPECTED OUTPUT**

::: term
devops@ip-10-0-1-23:~$ pwd
/home/devops
devops@ip-10-0-1-23:~$ ls -la
total 28
drwxr-x---  4 devops devops 4096 Mar  4 09:12 .
drwxr-xr-x  5 root   root   4096 Mar  4 08:55 ..
drwxr-xr-x  3 devops devops 4096 Mar  4 09:12 apps
-rw-r--r--  1 devops devops  220 Mar  4 08:55 .bashrc
-rw-r--r--  1 devops devops 1486 Mar  4 09:12 deploy-notes.md
devops@ip-10-0-1-23:~$ du -sh *
4.0K	deploy-notes.md
612M	apps
:::

**WHAT.** Print the working directory, list everything including hidden files, peek two levels deep, then find what is eating disk.

**WHY.** Because most “the server is broken” reports turn out to be “the disk is full” or “the app is not where I thought it was”. `du` answers the disk question in five seconds.

**WHAT JUST HAPPENED.** The app lives in `~/apps/medbook`, someone left a note, and 612 MB sits in the app directory — usually logs or build artefacts.

**WHAT CAN GO WRONG.** `Permission denied` on some paths. You are not root; that is normal and correct.
:::

**How to read `ls -l` without memorising a chart**

::: term
devops@lab:~$ ls -l medbook.jar
-rw-r--r-- 1 devops devops 61M Mar  4 09:12 medbook.jar
└┬┘└┬┘└┬┘ └─┬─┘ └─┬─┘        └┬┘ └──┬──┘
type perms  owner  group      size  date
:::

- `d` at the start = directory, `-` = file, `l` = symlink.
- `rwx` = read, write, execute, in three groups: owner, group, everyone else.
- Execute on a *directory* means “I may enter it”. That is why `700` on a home directory still lets you `cd` while `600` does not.

| Mode | Meaning | Typical use |
| :-- | :-- | :-- |
| `600` | owner read/write only | SSH private key, secret file |
| `640` | owner rw, group r, others none | config file with a credential in it |
| `644` | owner writes, others read | jar, HTML, docs |
| `700` | owner only, can enter/execute | `~/.ssh` |
| `755` | owner full, others read+execute | web dir, most scripts |
| `777` | everyone can do anything | **never in production** — flag it when you see it |

**Command explanation**, line by line:

| Command | What each part does |
| :-- | :-- |
| `ls -la` | `-a` include hidden (dot) files, `-l` long form |
| `find . -maxdepth 2 -type f` | start here, two levels, files only |
| `du -sh *` | summarise one total per argument, human units |
| `sort -h` | sort sizes correctly (1K < 1M < 1G) — plain `sort` gets this wrong |
| `tail -f app.log` | follow as it grows; `Ctrl+C` stops |
| `grep -Rin "error" .` | recurse, ignore case, line numbers; `-A 5` = 5 lines after |
| `journalctl -u medbook -f` | follow one systemd unit's log |

## STEP 2 — Users, permissions, and the classic “it works as root”

::: step STEP 2 — Fix a service that cannot read its own file
**CONTEXT.** The developer's app is installed but the service starts and dies immediately. Log line: `java.nio.file.AccessDeniedException: /opt/medbook/config/application.yml`.

**HOW TO INVESTIGATE (this order, always)**

```bash
systemctl status medbook --no-pager                # what did the unit say?
journalctl -u medbook -n 50 --no-pager             # what did the app say?
ls -l /opt/medbook/config/                         # owner + bits
systemctl cat medbook | grep -E 'User|ExecStart'   # which user does systemd run it as?
sudo -u medbook cat /opt/medbook/config/application.yml   # can that user read it?
```

**EXPECTED OUTPUT**

::: term
devops@lab:~$ ls -l /opt/medbook/config/application.yml
-rw------- 1 root root 1486 Mar  4 09:12 /opt/medbook/config/application.yml
devops@lab:~$ systemctl cat medbook | grep -E 'User|ExecStart'
User=medbook
ExecStart=/usr/bin/java -jar /opt/medbook/app/medbook.jar
:::

**WHAT HAPPENED.** The file is readable only by `root`, but systemd runs the app as `medbook`. Nothing is broken — permissions are doing their job. Somebody copied the config in with `sudo`.

**THE FIX**

```bash
sudo chown medbook:medbook /opt/medbook/config/application.yml
sudo chmod 640 /opt/medbook/config/application.yml
sudo systemctl restart medbook
systemctl is-active medbook                 # active
curl -fsS http://localhost:8080/health      # {"status":"UP"}
```

**WHY `chown` and not `chmod 666`.** Widening permissions for everybody is how a config file with a database password ends up readable by any process on the box. Change the *owner* so the right user can read it, then keep the bits tight.
:::

::: callout warn PRODUCTION SCENARIO — the story you will repeat for years
A developer says “it works on my machine”. On the server the service exits at boot. Root cause, 8 times in 10: (1) files owned by root because they were copied with `sudo`; (2) the wrong port, because the previous process still holds it; (3) a missing environment variable, because the developer's shell exports it but systemd knows nothing. Check `ls -l`, `ss -lntp` and `systemctl show -p Environment` in that order — before you touch the application.
:::

## STEP 3 — systemd: how an application survives a reboot

::: step STEP 3 — The unit file that turns “run it in a terminal” into a deployment
Running `java -jar app.jar` in an SSH session is a tutorial: close the terminal and the process dies. `systemd` is the supervisor Linux already has — it starts the app at boot, restarts it when it crashes, and keeps its logs.

```ini
# /etc/systemd/system/medbook.service
[Unit]
Description=MedBook appointment API
After=network-online.target
Wants=network-online.target

[Service]
User=medbook
Group=medbook
WorkingDirectory=/opt/medbook
EnvironmentFile=/etc/medbook/medbook.env
ExecStart=/usr/bin/java -jar /opt/medbook/app/medbook.jar --server.port=${APP_PORT}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload            # REQUIRED after editing any unit file
sudo systemctl enable --now medbook     # start now + start on every boot
systemctl status medbook --no-pager
journalctl -u medbook --since "-10 min" --no-pager
```

**Command explanation.** `enable` = start on boot; `--now` = also start now; `daemon-reload` = tell systemd the file changed (forgetting this is the #1 beginner trap); `status` = state, PID and the last log lines; `Restart=always` + `RestartSec=5` = self-healing with a pause so a crash loop doesn't spin the CPU.
:::

## STEP 4 — Processes, ports, disk: the three checks of any incident

::: cols
::: col
### Processes
```bash
ps aux | grep -i medbook | grep -v grep
top            # then P (CPU), M (memory), q
pgrep -af java
sudo kill 2451          # polite: SIGTERM
sudo kill -9 2451       # last resort: SIGKILL
pkill -f "medbook.jar"
```
Columns that matter in `ps aux`: `USER` (who), `%CPU`/`%MEM` (pressure), `RSS` (real RAM), `START`, `COMMAND` (with which args). A state of `Z` is a zombie: already dead, waiting for its parent; harmless alone, suspicious in hundreds.
:::
::: col
### Ports
```bash
ss -lntp                    # all listening sockets + PID
ss -lntp | grep 8080        # who owns the port I want?
curl -v http://localhost:8080/health
nc -zv db.internal 5432     # is the TCP path open?
ip a ; ip r                 # addresses, routes
```
If the port is not in `ss -lntp`, *no firewall rule on earth will help you.* Fix the process first. That one sentence saves hours.
:::
::: col
### Disk
```bash
df -h                    # filesystem usage — check / and /var
df -i                    # inodes: "no space left" with free GB?
du -sh /var/log/* | sort -h | tail
sudo truncate -s 0 /var/log/app/app.log
lsof +L1                 # deleted files still held open
sudo systemctl restart rsyslog
```
:::

::: callout fix HOW TO FIX THE FOUR OUTPUTS YOU WILL SEE MOST
- **`Address already in use`** → `ss -lntp | grep <port>` → kill the old process or change `APP_PORT`.
- **`Permission denied (publickey)`** over SSH → wrong key or wrong user → `ssh -i ~/.ssh/app.pem ubuntu@host -v`; on AWS it is usually `ec2-user` or `ubuntu`, never `root`.
- **`No space left on device` but `df -h` shows room** → inodes (`df -i`), a huge deleted-but-open log (`lsof +L1`), or a mount hiding data beneath it.
- **Service “active” but site returns 502** → the app is up, the *proxy* cannot reach it → `curl localhost:8080` on the box, then the proxy's `error.log` for `connect() failed (111: Connection refused)`.
:::

## STEP 5 — Read logs like a professional (three commands)

```bash
journalctl -u medbook --since "10 min ago" --no-pager
journalctl -f -u medbook
grep -nE "ERROR|Exception|Caused by" /var/log/medbook/app.log | tail -30
```

Then the two lines you actually want: the **first** error in the burst and the **deepest `Caused by:`** under it. The first stack trace is usually the symptom; the last cause is the cause. `tail -f` plus scrolling is how beginners lose whole mornings; `grep -A 20 "Caused by"` finds it in seconds.

::: step STEP 5b — Log rotation, so this never happens again
```bash
cat /etc/logrotate.d/medbook
/var/log/medbook/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```
`copytruncate` lets the app keep its file handle; without it you must signal the app after rotation. `rotate 14` = two weeks of history. `du -sh /var/log` after a month tells you whether you chose well.
:::

## STEP 6 — A 15-line shell script that is actually production-shaped

```bash
#!/usr/bin/env bash
set -euo pipefail                 # exit on error / unset var / failure inside a pipe
LOG() { printf '%s [%s] %s\n' "$(date -u +%H:%M:%S)" "$1" "$2"; }

APP="${APP:-medbook}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8080/health}"
command -v systemctl >/dev/null || { LOG ERROR "systemctl missing"; exit 1; }

LOG INFO "restarting $APP"
systemctl is-active "$APP" >/dev/null && LOG INFO "was active" || LOG WARN "was NOT active"
sudo systemctl restart "$APP"
for i in {1..30}; do
  if curl -fsS "$HEALTH_URL" >/dev/null; then LOG OK "healthy after ${i}s"; exit 0; fi
  sleep 1
done
LOG ERROR "still unhealthy after 30s — last 40 log lines:"
journalctl -u "$APP" -n 40 --no-pager
exit 1                            # a failing script that tells the truth beats a green lie
```

**Command explanation.** `set -euo pipefail` is the single most valuable line in any script you will ever write. `${VAR:-default}` gives a default, `${VAR:?msg}` fails loudly if the environment forgot it. `curl -fsS` returns non-zero on HTTP errors — that is what makes the retry loop a real check. `exit 1` at the end is what turns this into a Jenkins/GitHub Actions step that can fail.

## Production scenario — “the appointment search is slow every morning at 9”

1. `top`: CPU pegged, but mostly in `sys` time → not application logic, it is kernel/IO.
2. `iostat -x 1 5`: `%util` near 100% on the EBS volume, `await` 45 ms → disk-bound.
3. `df -h`: `/` at 96% because a debug log was left enabled — the app is thrashing the same volume the OS needs.
4. Now: `sudo truncate -s 0 /var/log/medbook/debug.log`, `sed -i 's/^logging.level=DEBUG/logging.level=INFO/' /etc/medbook/medbook.env`, restart the service.
5. Forever: log rotation, app logs on a separate volume, a disk alert at 80%, and debug logging gated behind an env var.
6. Verify: `iostat` normal, `/health` p95 back from 4.2 s to 180 ms.

The DevOps answer was not “add more CPU”. Look, then measure, then act. That sequence **is** the job.

## Common mistakes

::: checklist
- [ ] Running the application as `root` “because permissions” — then every permission problem gets worse. Create a service user: `sudo useradd -r -s /usr/sbin/nologin medbook`.
- [ ] `chmod 777` to make an error go away. The error is gone; the security finding is now yours.
- [ ] Editing a unit file without `systemctl daemon-reload` → “I changed it but nothing happened”.
- [ ] Starting the app in a terminal and walking away — it dies on logout without `nohup`/systemd.
- [ ] `kill -9` as the first move: no cleanup, no graceful drain, orphaned connections. SIGTERM, wait 10 s, then escalate.
- [ ] Reading the last line of a stack trace instead of the deepest `Caused by`.
- [ ] `sudo` reflexively on every command → root-owned files that then break your service user (STEP 2).
- [ ] Forgetting `/var/log` grows → disk full → everything on the box fails at once, including the monitoring agent.
:::

## Best practices you can adopt from day one

::: grid2
**One service user per app.** `nologin` shell, no password, home under `/opt/<app>`.
**Config outside the artefact.** `/etc/<app>/`, owned `root:<appgroup>` mode `640`; secrets in a file with `600` or, better, a secret store.
**Logs to stdout in containers, journald on VMs.** Never to a file inside the image.
**Name things identically everywhere.** App dir = service name = repo name = container name. `medbook`, not `medbook-final-new2`.
**A 15-line runbook per server, in the repo.** How to start, stop, find logs, find config. It is your future self's lifeline.
:::

## Interview answer (60 seconds, out loud)

::: callout aha “What Linux commands do you use daily as a DevOps engineer?”
“Mostly diagnosis. `systemctl status` and `journalctl -u` to see whether the service is even alive; `ps aux` and `top` for processes and resource pressure; `ss -lntp` to see what is actually listening on which port — before I touch any firewall; `df -h` and `du -sh`, because a full disk looks exactly like an application bug; `grep`, `tail`, `less` on logs to find the first error and the deepest `Caused by`; and `chown`/`chmod` to fix the service-user problems that appear whenever somebody installed as root. For example, on my healthcare project the API kept exiting at boot: `journalctl` showed an `AccessDeniedException` on the config file, `ls -l` showed root ownership, and `chown medbook:medbook` plus `chmod 640` fixed it. I didn't restart the server hoping — I read the log, checked the owner, and verified with `is-active` and a health curl.”
:::

::: grid2
**Follow-up: “How do you find what is using port 8080?”** → `ss -lntp | grep 8080`, then `ps -fp <pid>` for the full command, then decide kill vs move the port.
**Follow-up: “Service is running but the site is down. First three commands?”** → `curl localhost:8080/health` (is the app OK), `ss -lntp` (bound to `127.0.0.1` or `0.0.0.0`?), and the proxy error log (is Nginx returning 502 because it can't connect?).
**Follow-up: “What does `chmod 750` mean?”** → owner rwx, group r-x, others nothing — right for a directory the deploy user writes and the app user reads.
**Follow-up: “Soft vs hard link?”** → a symlink is its own inode, can cross filesystems and point at directories; a hard link is another name for the same inode, so deleting the original leaves the data. For config swaps: symlink a versioned file and atomically repoint it.
:::

## Virtual lab — Linux drill (simulated terminal)

Run the lab twice: once with hints, once from memory. Do not move on until the second pass is clean.

::: lab linux-1
:::

## Commands to remember

::: grid2
```bash
ls -la            # everything, including hidden
find . -maxdepth 2 -type f
du -sh *          # who is eating disk
df -h ; df -i     # space ; inodes
```
```bash
ps aux | grep app
ss -lntp          # ports + PID
systemctl status app
journalctl -u app -f
```
```bash
chmod 640 f ; chown u:g f
tail -F app.log   # follow across rotation
grep -nE "ERROR|Caused by" app.log | tail
scp -i key.pem file user@host:/tmp
```
:::

::: callout note WHAT I MUST REMEMBER (three lines — that is the exam)
1. An app on a server = a process, a port, some files, and logs. Check those four before you guess.
2. `chown` the right owner instead of `chmod 777` for everybody.
3. The fix is not done until you **verify** it (`is-active`, `curl /health`) and **prevent** it (rotation, alert, runbook).
:::

::: revision REVISION — 5 minutes
**Concepts:** service user and group · permission bits (owner/group/other, `rwx`) · execute-on-directory · systemd unit + `daemon-reload` · journald vs log files · listening socket vs firewall rule · inode exhaustion · log rotation with `copytruncate` · SIGTERM vs SIGKILL · load average · `set -euo pipefail`.
**Architecture to remember:** client → security group/firewall → listener (port) → process → files (config, logs) → disk. A failure sits at one of those boundaries, and each has a command above that proves it is or is not the culprit.
**Common mistakes:** running as root · `777` · forgetting `daemon-reload` · `kill -9` first · reading the wrong line of a stack trace · ignoring `df -h` when a service “randomly” fails.
**Troubleshooting checklist:** is it running? (`systemctl status`) is it listening? (`ss -lntp`) can I reach it locally? (`curl -v`) are the logs clean? (`journalctl -u`) is there disk? (`df -h`) is the owner right? (`ls -l`) — six commands, and 80% of tickets end there.
:::

Next: [Networking + shell scripting →](f-networking.html)
