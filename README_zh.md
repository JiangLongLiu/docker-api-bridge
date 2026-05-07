# docker-api-bridge

🔧 无人值守在远程主机上部署 `socat` 旁路桥接容器，把宿主 `/var/run/docker.sock` 暴露为 TCP `2375`，全程不碰 `systemd` / `docker.service` / `daemon.json`。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Qoder Skill](https://img.shields.io/badge/Qoder-Skill-blue)](https://github.com/JiangLongLiu/docker-api-bridge)

[[English](README.md)] [[中文](README_zh.md)]

## 概述

一个 Qoder Skill：吃一张包含若干远程主机的 CSV，对每台机器自动 SSH 进去探测环境（CPU 架构 / 操作系统 / Docker / 端口占用 / 已有容器 / daemon 代理），生成执行计划，然后部署一个 `alpine/socat` 容器，把 `tcp://0.0.0.0:2375` 转发到 `unix:///var/run/docker.sock`。

这是"修改 `docker.service` 加 `-H tcp://0.0.0.0:2375`"的安全替代方案 —— 后者会被 fnOS / 群晖 / 厂商系统更新无情擦除。socat 旁路容器全部跑在 Docker 内，升级免疫，一键 `docker rm -f` 即可彻底回滚。

适用于需要把 Docker API 统一暴露给 **dockhand** / Portainer / 自研运维平台的场景。

## 核心特性

- ✅ **零系统污染** —— 不碰 `systemd`、`docker.service`、`daemon.json`、防火墙
- 🛡️ **抗系统升级** —— 对 fnOS / 群晖 DSM 系统更新天生免疫
- 🔍 **环境自动探测** —— arch / kernel / OS / Docker 版本 / socket / 端口 / 现有容器 / daemon 代理
- 🚦 **智能计划** —— 已健康 SKIP、状态异常 REMOVE+RUN、端口被占 ABORT、缺镜像 PULL
- 🌐 **多主机批量** —— 一张 CSV 支持任意数量主机
- 🔁 **一键回滚** —— `--rollback` 即可完整撤除桥接
- 🐢 **广泛兼容** —— SFTP 不可用时自动 base64 兜底（兼容群晖 DSM 6.2 默认禁 SFTP），PATH 预置覆盖群晖 / OpenWrt / Entware
- 🌏 **代理友好** —— 完整保留 `daemon.json` 里既有的 `http-proxy` / `registry-mirrors`

## 快速开始

### 前置条件

- 本地：Python 3.x，`pip install paramiko`
- 远端：bash、curl、ss、docker（脚本自动检测，缺失则 ABORT）

### 安装

```bash
# Linux / macOS
git clone https://github.com/JiangLongLiu/docker-api-bridge.git \
  ~/.qoder/skills/docker-api-bridge

# Windows (PowerShell)
git clone https://github.com/JiangLongLiu/docker-api-bridge.git `
  "$env:USERPROFILE\.qoder\skills\docker-api-bridge"
```

放入 `~/.qoder/skills/` 后，Qoder 会自动发现并在相关提示词触发时调用。

### CSV 格式

UTF-8 编码（不强求 BOM），表头一行、一行一台主机。中英文表头都支持：

```csv
IP地址,用户名,密码,SSH端口,备注
192.168.1.10,root,YourPassword,22,fnOS-node1
192.168.1.11,root,YourPassword,22,Synology-DS220+
```

```csv
ip,user,password,port,note
192.168.1.10,root,YourPassword,22,fnOS-node1
```

## 使用方法

### 示例

**1. 一键批量部署**

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv
```

**2. 仅探测，不动手**

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv --dry-run
```

**3. 仅绑内网网卡**

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv --bind 192.168.1.10
```

**4. 一键回滚**

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv --rollback
```

### 输出示例

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

## 命令行参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--csv` | 必填 | 主机清单 CSV，UTF-8 编码 |
| `--container` | `dockhand-docker-proxy` | 桥接容器名 |
| `--port` | `2375` | 对外监听端口 |
| `--bind` | `0.0.0.0` | 监听网卡，可改成内网 IP 收紧 |
| `--image` | `alpine/socat:latest` | 桥接镜像 |
| `--dry-run` | off | 只 detect + plan，不 apply |
| `--rollback` | off | 移除桥接容器，恢复原状 |

### 退出码

- `0` 全部主机 OK / DRY_RUN_OK / ROLLBACK_OK
- `1` 至少一台失败（SSH_FAIL / ABORTED / APPLY_FAIL / VERIFY_FAIL）
- `2` CSV 缺失或为空

## 工作原理

每台主机 4 个阶段（paramiko 串行执行）：

1. **upload** —— 把 `bridge.sh` 上传到 `/tmp/dab_bridge.sh`（SFTP 优先，失败则用 base64 + `exec_command` 兜底）
2. **detect** —— 远端执行 `bash bridge.sh detect`，输出单行 JSON：arch / kernel / OS / Docker 版本 / socket / 端口 / 已有容器 / daemon 代理
3. **plan** —— Python 本地决策：
   - 已有同名容器且健康 → `SKIP`
   - 已有容器但异常 → `REMOVE` + `RUN`
   - 端口被非 docker-proxy 占用 → `ABORT`
   - 镜像缺失 → `PULL`
4. **apply + verify** —— 执行 `docker run -d --restart=always -p <BIND>:<PORT>:2375 -v /var/run/docker.sock:/var/run/docker.sock alpine/socat -d TCP-LISTEN:2375,fork,reuseaddr UNIX-CONNECT:/var/run/docker.sock`，再用 `ss`、`_ping`、`/version` 三连验证

## 兼容性矩阵（已验证）

| 系统 | 架构 | Docker | 备注 |
|---|---|---|---|
| 群晖 DSM 6.2 | x86_64 | 20.10.3 | SFTP 默认禁用 → 自动 base64 兜底；PATH 已扩展到 `/var/packages/Docker/target/usr/bin` |
| fnOS (Debian 12) | aarch64 | 28.2.2 | 标准路径；fnOS 升级免疫 |
| fnOS (Debian 12) | x86_64 | 28.2.2+ | 标准路径 |

`bridge.sh` 顶部 PATH 已预置：群晖 Docker / 群晖 Container Manager / OpenWrt / Entware (`/opt/bin`)，绝大多数 NAS / 嵌入式发行版开箱即用。

## 不做的事

- ❌ 不修改 `/etc/systemd/system/docker.service`
- ❌ 不写 systemd drop-in 文件
- ❌ 不修改 `/etc/docker/daemon.json`（保护既有 `http-proxy` / `registry-mirrors` / 容器运行时代理）
- ❌ 不动防火墙
- ❌ 不重启目标主机其他任何容器

## 回滚方式

```bash
python ~/.qoder/skills/docker-api-bridge/scripts/deploy.py --csv hosts.csv --rollback
```

仅移除桥接容器；镜像保留（如果想彻底清掉，远端手动 `docker rmi alpine/socat:latest`）。回滚后宿主与部署前完全一致，零残留配置。

## 安全说明

- TCP `2375` **明文 + 无认证**，仅适用于受信任内网
- 桥接容器挂载了 `docker.sock`，等同 root 权限。如需收紧：
  1. `--bind <内网IP>` 仅绑单网卡
  2. 跨区域流量叠加 stunnel / TLS

## 文件结构

```
docker-api-bridge/
├── SKILL.md              # Qoder skill 元数据（frontmatter + 触发关键词）
├── README.md             # 英文文档
├── README_zh.md          # 中文文档（本文件）
├── LICENSE               # MIT
├── .gitignore
└── scripts/
    ├── deploy.py         # 本地入口（paramiko、多主机循环）
    └── bridge.sh         # 远端脚本（detect / apply / verify / rollback）
```

## 故障排除

**问题：`paramiko.ssh_exception.SSHException: EOF during negotiation`**
远端 SSH 服务未启用 SFTP 子系统（典型如群晖 DSM 6.2）。skill 已自动降级到 base64 + `exec_command` 上传通道，重跑一次即可。仍不行则确认远端有 `base64` 命令。

**问题：`docker_installed=false` 但 `sock_exists=true`**
非交互 SSH 的 `PATH` 不含 `docker` 二进制目录。`bridge.sh` 已预置常见路径（群晖、OpenWrt、Entware），如果你的发行版不在内，把对应路径加到 `export PATH=...` 那一行即可。

**问题：`ABORT: port 2375 occupied by non-docker-proxy`**
端口 2375 被其他进程持有（多半是早先手动加 `dockerd -H tcp://...` 的残留）。换 `--port`，或先停掉旧监听。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。

## 支持

- 🐛 [报告问题](https://github.com/JiangLongLiu/docker-api-bridge/issues)
- 💡 [功能请求](https://github.com/JiangLongLiu/docker-api-bridge/issues)

## ⭐ 支持作者

如果这个 skill 帮你免去了又一次 `docker.service` 回滚，请给它一个 star！🌟

---

由 [JiangLongLiu](https://github.com/JiangLongLiu) 用 ❤️ 制作
