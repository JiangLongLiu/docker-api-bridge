#!/usr/bin/env bash
# docker-api-bridge / bridge.sh
# 远端：detect / apply / verify / rollback socat docker-api 桥接容器
# 由 deploy.py 上传到目标主机 /tmp/dab_bridge.sh 后执行

set -u

# 兼容群晖 / OpenWrt / 老 Linux：非交互 SSH 默认 PATH 通常很窄，主动加上常见 docker 路径
export PATH="${PATH}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/var/packages/Docker/target/usr/bin:/var/packages/ContainerManager/target/usr/bin:/opt/bin:/opt/sbin"

CMD="${1:-detect}"
shift || true

CONTAINER="dockhand-docker-proxy"
PORT="2375"
BIND="0.0.0.0"
IMAGE="alpine/socat:latest"

while [ $# -gt 0 ]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2 ;;
    --port)      PORT="$2";      shift 2 ;;
    --bind)      BIND="$2";      shift 2 ;;
    --image)     IMAGE="$2";     shift 2 ;;
    *) shift ;;
  esac
done

# JSON 字符串安全转义
json_str() {
  local s="${1:-}"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/}"
  s="${s//$'\t'/\\t}"
  printf '"%s"' "$s"
}

bool() { if [ "$1" = "true" ] || [ "$1" = "1" ]; then echo true; else echo false; fi; }

detect() {
  ARCH=$(uname -m 2>/dev/null || echo unknown)
  KERNEL=$(uname -r 2>/dev/null || echo unknown)
  OS_NAME="unknown"; OS_VER="unknown"; OS_PRETTY="unknown"
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_NAME="${ID:-unknown}"
    OS_VER="${VERSION_ID:-unknown}"
    OS_PRETTY="${PRETTY_NAME:-unknown}"
  fi

  DOCKER_VER="$(docker --version 2>/dev/null || echo '')"
  DOCKER_INSTALLED=false
  [ -n "$DOCKER_VER" ] && DOCKER_INSTALLED=true

  SOCK_EXISTS=false
  [ -S /var/run/docker.sock ] && SOCK_EXISTS=true

  PROXY=""
  if [ -r /etc/docker/daemon.json ]; then
    PROXY=$(grep -oE '"http-proxy"[^,}]*' /etc/docker/daemon.json | head -1 || true)
  fi

  IMAGE_PRESENT=false
  if [ "$DOCKER_INSTALLED" = "true" ] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
    IMAGE_PRESENT=true
  fi

  PORT_LISTENER=""
  PORT_BUSY_BY_OTHER=false
  if command -v ss >/dev/null 2>&1; then
    PORT_LISTENER=$(ss -lntp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print $0; exit}')
  fi

  EXISTING_JSON="null"
  if [ "$DOCKER_INSTALLED" = "true" ] && docker inspect "$CONTAINER" >/dev/null 2>&1; then
    C_STATE=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)
    C_IMG=$(docker inspect -f '{{.Config.Image}}'  "$CONTAINER" 2>/dev/null || echo unknown)
    HEALTHY=false
    if [ "$C_STATE" = "running" ]; then
      if curl -sS --max-time 3 "http://127.0.0.1:$PORT/_ping" 2>/dev/null | grep -q OK; then
        HEALTHY=true
      fi
    fi
    EXISTING_JSON=$(printf '{"name":"%s","state":"%s","image":"%s","healthy":%s}' \
      "$CONTAINER" "$C_STATE" "$C_IMG" "$HEALTHY")
  fi

  if [ -n "$PORT_LISTENER" ]; then
    if echo "$PORT_LISTENER" | grep -q docker-proxy; then
      :
    else
      PORT_BUSY_BY_OTHER=true
    fi
  fi

  printf '{"arch":"%s","kernel":"%s","os":"%s","os_version":"%s","os_pretty":%s,"docker_installed":%s,"docker_version":%s,"sock_exists":%s,"image":"%s","image_present":%s,"port":%s,"port_listener":%s,"port_busy_by_other":%s,"existing_container":%s,"daemon_proxy":%s}\n' \
    "$ARCH" "$KERNEL" "$OS_NAME" "$OS_VER" "$(json_str "$OS_PRETTY")" \
    "$DOCKER_INSTALLED" "$(json_str "$DOCKER_VER")" "$SOCK_EXISTS" \
    "$IMAGE" "$IMAGE_PRESENT" \
    "$PORT" "$(json_str "$PORT_LISTENER")" "$PORT_BUSY_BY_OTHER" \
    "$EXISTING_JSON" \
    "$(json_str "$PROXY")"
}

apply() {
  if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "[apply] removing stale container $CONTAINER"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[apply] pulling $IMAGE ..."
    if ! docker pull "$IMAGE"; then
      echo "[apply] pull failed"
      return 11
    fi
  fi
  PUBLISH="$BIND:$PORT:2375"
  echo "[apply] running $CONTAINER -> $PUBLISH"
  if ! docker run -d \
      --name "$CONTAINER" \
      --restart=always \
      -p "$PUBLISH" \
      -v /var/run/docker.sock:/var/run/docker.sock \
      "$IMAGE" \
      -d "TCP-LISTEN:2375,fork,reuseaddr" "UNIX-CONNECT:/var/run/docker.sock" ; then
    echo "[apply] docker run failed"
    return 12
  fi
  sleep 2
  docker ps --filter "name=$CONTAINER" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
}

verify() {
  echo "[verify] LISTEN check"
  ss -lntp 2>/dev/null | grep ":$PORT " || echo "(no LISTEN line on :$PORT)"
  echo "[verify] /_ping"
  if ! curl -sS --max-time 4 "http://127.0.0.1:$PORT/_ping" | grep -q OK; then
    echo "[verify] _ping FAIL"
    return 21
  fi
  echo "OK"
  echo "[verify] /version (head 200)"
  curl -sS --max-time 4 "http://127.0.0.1:$PORT/version" | head -c 200
  echo
}

rollback() {
  if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 && echo "[rollback] removed $CONTAINER"
  else
    echo "[rollback] container absent"
  fi
  echo "[rollback] image kept (manual: docker rmi $IMAGE)"
}

case "$CMD" in
  detect)   detect ;;
  apply)    apply ;;
  verify)   verify ;;
  rollback) rollback ;;
  *)
    echo "usage: $0 {detect|apply|verify|rollback} [--container N] [--port P] [--bind B] [--image I]" >&2
    exit 1
    ;;
esac
