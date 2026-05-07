---
name: docker-api-bridge
description: 无人值守在远程主机上部署 socat 旁路桥接容器，把宿主 /var/run/docker.sock 暴露为 TCP 2375，避免修改 systemd / docker.service / daemon.json，对 fnOS 等会重置系统单元的发行版完全免疫。当用户提及"暴露 docker api"、"远程 docker 2375"、"socat 桥接"、"dockhand 连不上 docker"、"修复 docker tcp 监听"、"批量配置 docker 远程访问"时使用。输入是包含一台或多台主机的 password.csv，工具自动 SSH 探测 CPU 架构 / OS / Docker 状态 / 端口占用 / 现有容器，生成计划并执行，最后做端到端验证。
---

# Docker API Bridge（socat 旁路桥接）

## 适用场景

- 多台远程 Docker 主机，需要把本地 unix socket 以 TCP 形式暴露给统一控制面（如 dockhand / Portainer / 自研运维平台）
- 宿主是 fnOS / 群晖 / 飞牛等会被系统升级覆盖 systemd 单元的发行版，禁止改 `docker.service` 与 `daemon.json`
- 已有 daemon.json 配置了 `http-proxy` / `registry-mirrors`，必须保留

## 一句话原理

在远端跑 `alpine/socat` 容器，监听宿主 `0.0.0.0:2375`，把流量转发到挂进容器的 `/var/run/docker.sock`。零系统改动，`docker rm -f` 即完整回滚。

## 调用方式

```bash
python "C:\Users\liujianglong\.qoder\skills\docker-api-bridge\scripts\deploy.py" --csv "<path/to/password.csv>"
```

### 常用参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--csv` | 必填 | 主机清单 CSV，格式同 ssh-unattended（IP地址,用户名,密码,SSH端口,备注） |
| `--container` | `dockhand-docker-proxy` | 桥接容器名 |
| `--port` | `2375` | 对外监听端口 |
| `--bind` | `0.0.0.0` | 监听网卡，可改成 `192.168.123.52` 仅绑内网 |
| `--image` | `alpine/socat:latest` | 桥接镜像 |
| `--dry-run` | off | 只 detect + plan，不 apply |
| `--rollback` | off | 移除桥接容器，恢复原状 |

### 示例

```bash
# 单机或多机一键部署
python "C:\Users\liujianglong\.qoder\skills\docker-api-bridge\scripts\deploy.py" --csv "e:/Qoder_workspace/dockhand/password_52.csv"

# 仅探测，不动手
python "C:\Users\liujianglong\.qoder\skills\docker-api-bridge\scripts\deploy.py" --csv "e:/Qoder_workspace/dockhand/password_52.csv" --dry-run

# 仅绑内网网卡
python "C:\Users\liujianglong\.qoder\skills\docker-api-bridge\scripts\deploy.py" --csv "hosts.csv" --bind 192.168.123.52

# 一键回滚
python "C:\Users\liujianglong\.qoder\skills\docker-api-bridge\scripts\deploy.py" --csv "hosts.csv" --rollback
```

## 工作流（每台主机）

`deploy.py` 通过 paramiko 连接每台主机，把 `bridge.sh` 上传到 `/tmp/dab_bridge.sh`，依次执行：

1. **detect** → 输出 JSON：`arch / kernel / os / docker_version / sock_exists / image_present / port_listener / port_busy_by_other / existing_container / daemon_proxy`
2. **plan** → 本地根据 detect 结果决定动作：
   - 已有同名容器且 `_ping` 通 → SKIP，仅 verify
   - 同名容器异常 → 先 REMOVE
   - 端口被非 docker-proxy 占用 → ABORT
   - 镜像缺失 → PULL
   - 否则 RUN
3. **apply** → `docker run -d --name <C> --restart=always -p <BIND>:<PORT>:2375 -v /var/run/docker.sock:/var/run/docker.sock <IMAGE> -d TCP-LISTEN:<PORT>,fork,reuseaddr UNIX-CONNECT:/var/run/docker.sock`
4. **verify** → 在远端做 `ss -lntp | grep PORT`、`curl /_ping`、`curl /version`，全部通过才算 OK

每台主机执行完打印一段 DETECT/PLAN/APPLY/VERIFY，最后在末尾打印 SUMMARY 表格。

## 退出码

- `0` 全部主机 OK / DRY_RUN_OK / ROLLBACK_OK
- `1` 至少一台失败（SSH_FAIL / ABORTED / APPLY_FAIL / VERIFY_FAIL）
- `2` CSV 解析失败或为空

## 安全说明

- 2375 默认无认证 + 明文，**仅用于受信任内网**
- 桥接容器挂载 docker.sock 等同 root；如需收紧可：
  1. `--bind <内网IP>` 仅绑单网卡
  2. 后续叠加 stunnel / TLS 代理

## 不做的事

- 不修改 `/etc/systemd/system/docker.service`
- 不写 systemd drop-in 文件
- 不修改 `/etc/docker/daemon.json`（保护既有 http-proxy / registry-mirrors / 容器运行时代理）
- 不动防火墙、不重启目标主机其他容器

## 依赖

- 本地：Python 3.x、`pip install paramiko`
- 远端：bash、curl、ss、docker（脚本会自动检测，缺失则 ABORT）

## 详细脚本位置

- 本地入口：`C:\Users\liujianglong\.qoder\skills\docker-api-bridge\scripts\deploy.py`
- 远端脚本：`C:\Users\liujianglong\.qoder\skills\docker-api-bridge\scripts\bridge.sh`（自动上传到目标主机 `/tmp/dab_bridge.sh`）
