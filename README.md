# docker-api-bridge

🔧 Unattended deployment of a `socat` sidecar container that exposes a remote host's `/var/run/docker.sock` as TCP `2375`, without touching `systemd`, `docker.service`, or `daemon.json`.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Qoder Skill](https://img.shields.io/badge/Qoder-Skill-blue)](https://github.com/JiangLongLiu/docker-api-bridge)

[[English](README.md)] [[中文](README_zh.md)]

## Overview

A Qoder Skill that takes a CSV of remote hosts and, for each one, SSHs in, detects the environment (CPU arch / OS / Docker / port / existing container / daemon proxy), generates a plan, and deploys an `alpine/socat` container that forwards `tcp://0.0.0.0:2375` to `unix:///var/run/docker.sock`.

This is the safe alternative to "edit `docker.service` and add `-H tcp://0.0.0.0:2375`" — that approach gets reset by every fnOS / Synology / vendor system update. The `socat` sidecar lives entirely inside Docker, survives upgrades, and rolls back with a single `docker rm -f`.

Designed for unified control planes such as **dockhand**, Portainer, or any custom ops dashboard that needs uniform TCP access across a fleet.

## Features

- ✅ **Zero system mutation** — never touches `systemd`, `docker.service`, `daemon.json`, or firewall rules
- 🛡️ **Survives vendor OS updates** — fnOS / Synology DSM upgrade-proof by design
- 🔍 **Auto environment detection** — arch, kernel, OS, Docker version, socket, port, existing container, daemon proxy
- 🚦 **Smart planning** — SKIP if already healthy, REMOVE+RUN if stale, ABORT if port hijacked, PULL only when missing
- 🌐 **Multi-host batch** — feed any number of hosts via one CSV
- 🔁 **One-command rollback** — `--rollback` flag completely removes the bridge
- 🐢 **Wide compatibility** — auto base64 fallback when SFTP is disabled (Synology DSM 6.2 default), expanded `PATH` covering Synology, OpenWrt, Entware
- 🌏 **Proxy-friendly** — keeps your existing `daemon.json` `http-proxy` / `registry-mirrors` 100% intact

## Quick Start

### Prerequisites

- Local: Python 3.x, `pip install paramiko`
- Remote: bash, curl, ss, docker (auto-checked; aborts gracefully if missing)

### Installation

```bash
# Clone into your Qoder personal skills directory
git clone https://github.com/JiangLongLiu/docker-api-bridge.git \
  ~/.qoder/skills/docker-api-bridge

# (Windows) clone into %USERPROFILE%\.qoder\skills\
git clone https://github.com/JiangLongLiu/docker-api-bridge.git \
  "%USERPROFILE%\.qoder\skills\docker-api-bridge"
```

Once placed in `~/.qoder/skills/`, Qoder will auto-discover the skill and trigger it on relevant prompts.

### CSV Format

UTF-8 (no BOM required), header row, one host per line. Both Chinese and English headers are accepted:

```csv
IP地址,用户名,密码,SSH端口,备注
192.168.1.10,root,YourPassword,22,fnOS-node1
192.168.1.11,root,YourPassword,22,Synology-DS220+
```

```csv
ip,user,password,port,note
192.168.1.10,root,YourPassword,22,fnOS-node1
```

## Usage

### Examples

**1. One-shot deploy across the fleet**

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv
```

**2. Detect only, no changes**

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv --dry-run
```

**3. Bind only to internal NIC**

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv --bind 192.168.1.10
```

**4. Full rollback**

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv --rollback
```

**5. Offline host — local build (dockerd is NOT restarted)**

Use this when the target host has no internet egress, or `dockerd` was configured with an `http-proxy` that's now offline and you can't restart `dockerd` (e.g. a Synology running 100+ containers). Requires that the target host already has a small base image locally (default `alpine:3.18.2`) and can reach the build-time proxy.

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py \
    --csv hosts.csv \
    --build-proxy http://10.0.0.1:7890
```

This switches the path from `docker pull` to `docker build` on the host: a 5-line `Dockerfile` (`FROM alpine:3.18.2 + apk add socat + ENTRYPOINT ["socat"]`) is written to `/tmp/dab-socat-build/`, built with `--build-arg http_proxy=...`, then run as `local/socat:latest`. **`dockerd` is not restarted, `daemon.json` is not modified.**

### Sample Output

```
[INFO] 1 host(s) loaded from hosts.csv
  - 192.168.1.10:22 as root  # fnOS-node1

========== 192.168.1.10 (fnOS-node1) ==========
--- DETECT ---
{
  "arch": "aarch64",
  "os_pretty": "Debian GNU/Linux 12 (bookworm)",
  "docker_version": "Docker version 28.2.2, build e6534b4",
  "sock_exists": true,
  "image_present": false,
  "port_busy_by_other": false,
  "existing_container": null,
  "daemon_proxy": "\"http-proxy\":\"http://192.168.1.1:7890\""
}
--- PLAN ---
  - PULL: alpine/socat:latest
  - RUN: socat -p 0.0.0.0:2375:2375
  - VERIFY
--- APPLY ---
[apply] pulling alpine/socat:latest ...
[apply] running dockhand-docker-proxy -> 0.0.0.0:2375:2375
NAMES                   STATUS         PORTS
dockhand-docker-proxy   Up 2 seconds   0.0.0.0:2375->2375/tcp
--- VERIFY ---
[verify] /_ping
OK

========== SUMMARY ==========
HOST               STATUS         ARCH     OS
192.168.1.10       OK             aarch64  Debian GNU/Linux 12 (bookworm)
```

## CLI Reference

| Flag | Default | Description |
|---|---|---|
| `--csv` | required | Path to a UTF-8 CSV with host credentials |
| `--container` | `dockhand-docker-proxy` | Bridge container name |
| `--port` | `2375` | TCP port to publish on the host |
| `--bind` | `0.0.0.0` | Host NIC to bind on; set to an internal IP to lock down |
| `--image` | `alpine/socat:latest` | socat image to use; auto-switched to `local/socat:latest` when `--build-proxy` is set |
| `--build-proxy` | empty | Triggers **local build mode**: skip `docker pull`, build a socat image on the host from `--build-base`, with `apk add socat` going through this HTTP proxy. `dockerd` is NOT restarted, `daemon.json` is NOT touched. |
| `--build-base` | `alpine:3.18.2` | Base image for local build mode; must already exist on the target host |
| `--dry-run` | off | Detect + plan only; no `docker run` |
| `--rollback` | off | Remove bridge container instead of deploying |

### Exit Codes

- `0` — all hosts OK / DRY_RUN_OK / ROLLBACK_OK
- `1` — at least one host failed (SSH_FAIL / ABORTED / BUILD_FAIL / APPLY_FAIL / VERIFY_FAIL)
- `2` — CSV missing or empty

## How It Works

For each host in the CSV, `deploy.py` executes 4 phases via paramiko:

1. **upload** — copies `bridge.sh` to `/tmp/dab_bridge.sh` (SFTP, with base64 + `exec_command` fallback)
2. **detect** — runs `bash bridge.sh detect` which prints a single-line JSON of arch / kernel / OS / docker version / socket / port / existing container / daemon proxy / base image presence
3. **plan** — Python decides locally:
   - existing container healthy → `SKIP`
   - existing container stale → `REMOVE` + `RUN`
   - port held by non-`docker-proxy` → `ABORT`
   - image missing + no `--build-proxy` → `PULL`
   - image missing + `--build-proxy` set → `BUILD` (aborts if base image is missing)
4. **build** (local-build mode only) — writes `/tmp/dab-socat-build/Dockerfile`, runs `docker build --build-arg http_proxy=... --build-arg https_proxy=... -t local/socat:latest .`, verifies `ENTRYPOINT` is the JSON array `[socat]`
5. **apply + verify** — runs `docker run -d --restart=always -p <BIND>:<PORT>:2375 -v /var/run/docker.sock:/var/run/docker.sock <IMAGE> -d TCP-LISTEN:2375,fork,reuseaddr UNIX-CONNECT:/var/run/docker.sock`, then validates `ss`, `_ping`, `/version`.

### Local Build Mode (Offline Hosts)

When `--build-proxy` is given, the bridge image is built on the target host instead of pulled. This is the only safe path on hosts that:

- Have no internet egress
- Have a `dockerd.json` `http-proxy` that's now offline, **and** dockerd cannot be restarted (e.g. Synology running 100+ containers)
- Can't reach any registry mirror

**Requirements:**

1. Target host has the `--build-base` image locally (default `alpine:3.18.2`; any alpine tag that supports `apk add socat` works)
2. Target host can reach the proxy URL passed via `--build-proxy` (only during build, ~1 MB traffic for `apk add socat`)

**Safety guarantees** (verified on Synology DSM 6.2 with 113 containers running, 34 of them active):

- `dockerd` is **not** restarted
- `daemon.json` is **not** modified
- No other container on the host is touched

The built image lives as `local/socat:latest`; rollback with `--rollback` removes the bridge container, and you can `docker rmi local/socat:latest` manually if you want it gone too.

## Compatibility Matrix (verified)

| OS | Arch | Docker | Image source | Notes |
|---|---|---|---|---|
| Synology DSM 6.2 | x86_64 | 20.10.3 | `docker pull` | SFTP off → base64 fallback used; PATH expanded to `/var/packages/Docker/target/usr/bin` |
| Synology DSM 6.2 (offline) | x86_64 | 20.10.3 | `--build-proxy` (local build) | Validated against a live host with 113 containers; dockerd NOT restarted |
| fnOS (Debian 12) | aarch64 | 28.2.2 | `docker pull` | Standard path; survives fnOS updates |
| fnOS (Debian 12) | x86_64 | 28.2.2+ | `docker pull` | Standard path |

The `bridge.sh` PATH already includes Synology Container Manager, OpenWrt, and Entware (`/opt/bin`) prefixes, so most NAS / embedded distros work out of the box.

## What This Tool Will NOT Do

- ❌ Modify `/etc/systemd/system/docker.service`
- ❌ Write `systemd` drop-in files
- ❌ Modify `/etc/docker/daemon.json` (preserves your `http-proxy`, `registry-mirrors`, container runtime proxy)
- ❌ Touch firewall rules
- ❌ Restart any other container on the target host

## Rollback

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv --rollback
```

This removes the bridge container; the image is kept (run `docker rmi alpine/socat:latest` manually if you want it gone too). Post-rollback the host returns exactly to its pre-deploy state — zero residual config.

## Security Notes

- TCP `2375` is **plaintext + unauthenticated**. Only use on a trusted internal network.
- The bridge container mounts `docker.sock`, which is root-equivalent. Tighten by:
  1. `--bind <internal-ip>` to restrict to one NIC
  2. Layering stunnel / TLS in front for cross-zone traffic

## File Layout

```
docker-api-bridge/
├── SKILL.md              # Qoder skill manifest (frontmatter + trigger keywords)
├── README.md             # English docs (this file)
├── README_zh.md          # Chinese docs
├── LICENSE               # MIT
├── .gitignore
└── scripts/
    ├── deploy.py         # Local entrypoint (paramiko, multi-host loop)
    └── bridge.sh         # Remote script (detect / apply / verify / rollback)
```

## Troubleshooting

**Issue: `paramiko.ssh_exception.SSHException: EOF during negotiation`**
The remote SSH server has SFTP disabled (typical Synology DSM 6.2). The skill auto-falls back to a base64 + `exec_command` upload channel — the next run should succeed. If it still fails, ensure `base64` is available on the remote.

**Issue: `docker_installed=false` while `sock_exists=true`**
`docker` binary is not in the non-interactive SSH `PATH`. Already mitigated in `bridge.sh` (covers Synology, OpenWrt, Entware). Add your distro's path to the `export PATH=...` line if needed.

**Issue: `ABORT: port 2375 occupied by non-docker-proxy`**
Something else (likely `dockerd -H tcp://...` from a previous manual fix) is holding `:2375`. Either pick a different `--port`, or stop the old listener first.

**Issue: `docker pull` fails with `proxyconnect tcp: ... no route to host`**
Dockerd is holding a stale `http-proxy` from `daemon.json`. Two options:
1. Restart dockerd after fixing `daemon.json` (risky if many containers are running)
2. **Use `--build-proxy <reachable-proxy-url>`** — builds the bridge image locally from a base image already on the host. dockerd is not restarted, `daemon.json` is not touched. See [Local Build Mode](#local-build-mode-offline-hosts).

## License

This project is licensed under the [MIT License](LICENSE).

## Feedback

Issues are intentionally **disabled** on this repository. For bug reports or feature requests:

- 📧 Reach out via [JiangLongLiu's GitHub profile](https://github.com/JiangLongLiu)
- 🍴 Fork the repo and send a Pull Request

## ⭐ Show Your Support

If this skill saved you from yet another `docker.service` rollback, please give it a star! 🌟

---

Made with ❤️ by [JiangLongLiu](https://github.com/JiangLongLiu)
