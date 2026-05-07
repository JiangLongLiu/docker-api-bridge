#!/usr/bin/env python3
"""docker-api-bridge / deploy.py

无人值守在多台远程主机上部署 socat 旁路桥接容器，
把宿主 /var/run/docker.sock 暴露为 TCP <PORT>。

不修改 systemd / docker.service / daemon.json。
对 fnOS / 群晖 等会重置系统单元的发行版完全免疫。

输入 CSV 列（与 ssh-unattended 同款）：
  IP地址, 用户名, 密码, SSH端口, 备注
英文表头也兼容：ip/host, user/username, password/pwd, port, note
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import shlex
import sys
from pathlib import Path

try:
    import paramiko  # noqa: F401
except ImportError:
    print("[FATAL] missing dependency: pip install paramiko", file=sys.stderr)
    sys.exit(2)

import paramiko  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
BRIDGE_SH = SCRIPT_DIR / "bridge.sh"
REMOTE_PATH = "/tmp/dab_bridge.sh"

DEFAULT_IMAGE = "alpine/socat:latest"
LOCAL_BUILD_IMAGE = "local/socat:latest"

CSV_KEYS = {
    "ip":       ["IP地址", "ip", "IP", "host", "Host", "HOST"],
    "user":     ["用户名", "user", "User", "username", "Username"],
    "password": ["密码",   "password", "Password", "pwd", "Pwd"],
    "port":     ["SSH端口", "port", "Port", "ssh_port"],
    "note":     ["备注",   "note", "Note", "comment"],
}


def pick(row: dict, key: str, default=None):
    for k in CSV_KEYS[key]:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return default


def parse_csv(path: str):
    hosts = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ip = pick(row, "ip")
            pwd = pick(row, "password")
            if not ip or not pwd:
                continue
            hosts.append({
                "ip":   str(ip).strip(),
                "user": str(pick(row, "user", "root")).strip(),
                "pwd":  str(pwd),
                "port": int(str(pick(row, "port", 22)).strip() or 22),
                "note": str(pick(row, "note", "")).strip(),
            })
    return hosts


def ssh_connect(host, timeout=10):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host["ip"], port=host["port"], username=host["user"],
        password=host["pwd"], timeout=timeout,
        allow_agent=False, look_for_keys=False,
    )
    return client


def run(client, cmd, timeout=120):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def upload_bridge(client):
    """优先 SFTP；失败则用 base64 + exec_command 兑底。
    兼容默认未开 SFTP 子系统的设备（群晖、老 OpenWrt 等）。
    """
    try:
        sftp = client.open_sftp()
        try:
            sftp.put(str(BRIDGE_SH), REMOTE_PATH)
            sftp.chmod(REMOTE_PATH, 0o755)
        finally:
            sftp.close()
        return "sftp"
    except Exception as sftp_err:
        with open(BRIDGE_SH, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        # bridge.sh < 10KB，base64 后 ≤14KB，单行 sshd 指令足够
        cmd = (
            f"umask 022 && printf '%s' '{b64}' | base64 -d > {REMOTE_PATH} "
            f"&& chmod 755 {REMOTE_PATH} && echo UPLOAD_OK"
        )
        rc, out, err = run(client, cmd, timeout=30)
        if rc != 0 or "UPLOAD_OK" not in out:
            raise RuntimeError(
                f"upload failed (sftp_err={sftp_err}; base64_fallback rc={rc} "
                f"stderr={err.strip()[:200]} stdout={out.strip()[:200]})"
            )
        return "base64"


def remote_args(args) -> str:
    parts = [
        f"--container {shlex.quote(args.container)}",
        f"--port {args.port}",
        f"--bind {shlex.quote(args.bind)}",
        f"--image {shlex.quote(args.image)}",
        f"--build-base {shlex.quote(args.build_base)}",
    ]
    if args.build_proxy:
        parts.append(f"--build-proxy {shlex.quote(args.build_proxy)}")
    return " ".join(parts)


def detect(client, args):
    rc, out, err = run(client, f"bash {REMOTE_PATH} detect {remote_args(args)}", timeout=30)
    out = out.strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_error": f"detect parse failed rc={rc}", "_stdout": out[:400], "_stderr": err[:200]}


def make_plan(info: dict, args) -> list[str]:
    plan: list[str] = []
    if "_error" in info:
        return ["ABORT: " + info["_error"]]
    if not info.get("docker_installed"):
        return [f"ABORT: docker not installed (version='{info.get('docker_version','')}')"]
    if not info.get("sock_exists"):
        return ["ABORT: /var/run/docker.sock missing"]

    existing = info.get("existing_container")
    if existing:
        if existing.get("healthy"):
            return [f"SKIP: {args.container} already healthy (state={existing.get('state')})", "VERIFY"]
        plan.append(f"REMOVE: stale container {args.container} (state={existing.get('state')})")
    elif info.get("port_busy_by_other"):
        return [f"ABORT: port {args.port} occupied by non-docker-proxy: {info.get('port_listener')}"]

    if not info.get("image_present"):
        if args.build_proxy:
            # Local build path: skip docker pull entirely.
            if not info.get("base_image_present"):
                return [
                    f"ABORT: --build-proxy set but base image '{info.get('base_image')}' "
                    f"missing on host; pull it first or pass --build-base <existing-image>"
                ]
            plan.append(
                f"BUILD: {args.image} <- {info.get('base_image')} "
                f"(apk add socat via proxy {args.build_proxy})"
            )
        else:
            plan.append(f"PULL: {args.image}")
    plan.append(f"RUN: socat -p {args.bind}:{args.port}:2375")
    plan.append("VERIFY")
    return plan


def deploy_one(host: dict, args) -> dict:
    label = f"{host['ip']}" + (f" ({host['note']})" if host['note'] else "")
    print(f"\n========== {label} ==========")
    try:
        client = ssh_connect(host)
    except Exception as e:
        print(f"[ERR] SSH connect failed: {e}")
        return {"host": host["ip"], "status": "SSH_FAIL", "detail": str(e)}

    try:
        upload_method = upload_bridge(client)
        if upload_method != "sftp":
            print(f"[INFO] bridge.sh uploaded via {upload_method} (SFTP unavailable)")
        info = detect(client, args)
        print("--- DETECT ---")
        print(json.dumps(info, indent=2, ensure_ascii=False))

        plan = make_plan(info, args)
        print("--- PLAN ---")
        for step in plan:
            print(f"  - {step}")

        if args.rollback:
            print("--- ROLLBACK ---")
            rc, out, err = run(client, f"bash {REMOTE_PATH} rollback {remote_args(args)}", timeout=60)
            print(out)
            if err.strip():
                print("STDERR:", err.strip())
            return {
                "host": host["ip"],
                "status": "ROLLBACK_OK" if rc == 0 else "ROLLBACK_FAIL",
                "arch": info.get("arch"), "os": info.get("os_pretty"),
            }

        if any(p.startswith("ABORT") for p in plan):
            return {"host": host["ip"], "status": "ABORTED", "plan": plan,
                    "arch": info.get("arch"), "os": info.get("os_pretty")}

        if args.dry_run:
            return {"host": host["ip"], "status": "DRY_RUN_OK", "plan": plan,
                    "arch": info.get("arch"), "os": info.get("os_pretty")}

        if not any(p.startswith("SKIP") for p in plan):
            if any(p.startswith("BUILD:") for p in plan):
                print("--- BUILD ---")
                rc, out, err = run(
                    client,
                    f"bash {REMOTE_PATH} build {remote_args(args)}",
                    timeout=300,
                )
                sys.stdout.write(out[-3000:])
                if rc != 0:
                    if err.strip():
                        print("STDERR:", err.strip()[-800:])
                    return {"host": host["ip"], "status": "BUILD_FAIL", "rc": rc,
                            "arch": info.get("arch"), "os": info.get("os_pretty")}
            print("--- APPLY ---")
            rc, out, err = run(client, f"bash {REMOTE_PATH} apply {remote_args(args)}", timeout=180)
            sys.stdout.write(out[-3000:])
            if rc != 0:
                if err.strip():
                    print("STDERR:", err.strip()[-800:])
                return {"host": host["ip"], "status": "APPLY_FAIL", "rc": rc,
                        "arch": info.get("arch"), "os": info.get("os_pretty")}
        else:
            print("--- APPLY skipped (already healthy) ---")

        print("--- VERIFY ---")
        rc, out, err = run(client, f"bash {REMOTE_PATH} verify {remote_args(args)}", timeout=20)
        sys.stdout.write(out)
        if rc != 0:
            if err.strip():
                print("STDERR:", err.strip())
            return {"host": host["ip"], "status": "VERIFY_FAIL", "rc": rc,
                    "arch": info.get("arch"), "os": info.get("os_pretty")}

        return {"host": host["ip"], "status": "OK",
                "arch": info.get("arch"), "os": info.get("os_pretty"),
                "endpoint": f"tcp://{host['ip']}:{args.port}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="Deploy socat docker-api bridge to remote hosts")
    ap.add_argument("--csv", required=True, help="path to password.csv")
    ap.add_argument("--container", default="dockhand-docker-proxy")
    ap.add_argument("--port", type=int, default=2375)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--image", default=DEFAULT_IMAGE,
                    help=f"socat image to run (default: {DEFAULT_IMAGE}; "
                         f"auto-switched to {LOCAL_BUILD_IMAGE} when --build-proxy is set")
    ap.add_argument("--build-proxy", default="",
                    help="HTTP proxy URL for local build path (e.g. http://10.0.0.1:7890). "
                         "When set, the host builds the socat image locally from --build-base "
                         "instead of running 'docker pull'. Use this for hosts with no internet "
                         "egress / broken dockerd proxy. dockerd is NOT restarted.")
    ap.add_argument("--build-base", default="alpine:3.18.2",
                    help="base image for local build (must already exist on the host)")
    ap.add_argument("--dry-run", action="store_true", help="detect + plan only")
    ap.add_argument("--rollback", action="store_true", help="remove bridge container instead of deploying")
    args = ap.parse_args()

    # When --build-proxy is set and the user did not override --image,
    # switch to the local-build tag so the produced image matches what apply runs.
    if args.build_proxy and args.image == DEFAULT_IMAGE:
        args.image = LOCAL_BUILD_IMAGE
        print(f"[INFO] --build-proxy set; image auto-switched to {LOCAL_BUILD_IMAGE}")

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[FATAL] csv not found: {csv_path}", file=sys.stderr)
        sys.exit(2)

    hosts = parse_csv(str(csv_path))
    if not hosts:
        print("[FATAL] no valid hosts in csv", file=sys.stderr)
        sys.exit(2)

    print(f"[INFO] {len(hosts)} host(s) loaded from {csv_path}")
    for h in hosts:
        print(f"  - {h['ip']}:{h['port']} as {h['user']}" + (f"  # {h['note']}" if h['note'] else ""))

    results = [deploy_one(h, args) for h in hosts]

    print("\n========== SUMMARY ==========")
    print(f"{'HOST':<18} {'STATUS':<14} {'ARCH':<8} OS")
    for r in results:
        print(f"{r['host']:<18} {r['status']:<14} {(r.get('arch') or '-'):<8} {r.get('os') or '-'}")

    bad = [r for r in results if r["status"] not in ("OK", "DRY_RUN_OK", "ROLLBACK_OK")]
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
