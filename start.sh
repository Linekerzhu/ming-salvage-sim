#!/usr/bin/env bash
# 前端 build + 启 web_app。
# 用法：./start.sh [--port 8010] [--host 127.0.0.1] [--no-build] [--python /path/to/python]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="127.0.0.1"
PORT="8010"
DO_BUILD=1
PY_OVERRIDE="${MING_PYTHON:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --python) PY_OVERRIDE="$2"; shift 2 ;;
    --no-build) DO_BUILD=0; shift ;;
    -h|--help)
      echo "Usage: $0 [--host HOST] [--port PORT] [--no-build] [--python /path/to/python]"
      exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

# 1. 注入 .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "[warn] 未找到 .env，LLM 相关变量未注入" >&2
fi

# 1b. LLM dump 会把 system/user/assistant 提示词落盘，服务器模式默认关闭。
#     只有排查问题时才在 .env 里显式设 MING_SIM_DUMP_LLM=1。
export MING_SIM_DUMP_LLM="${MING_SIM_DUMP_LLM:-0}"

# 2. 选 python：先做启动自检，避免坏 .venv 卡在 uvicorn import 前无日志。
python_works() {
  local candidate="$1"
  local log_file
  log_file="$(mktemp -t ming-python-check.XXXXXX)"
  "$candidate" -u - <<'PY' >"$log_file" 2>&1 &
import fastapi  # noqa: F401
import uvicorn  # noqa: F401
import web_app  # noqa: F401
print("ok")
PY
  local pid=$!
  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if [[ "$waited" -ge 6 ]]; then
      kill "$pid" 2>/dev/null || true
      sleep 0.2
      kill -9 "$pid" 2>/dev/null || true
      echo "[warn] Python 自检超时，跳过：$candidate" >&2
      rm -f "$log_file"
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  if wait "$pid"; then
    rm -f "$log_file"
    return 0
  fi
  echo "[warn] Python 自检失败，跳过：$candidate" >&2
  sed 's/^/[warn]   /' "$log_file" >&2 || true
  rm -f "$log_file"
  return 1
}

PY=""
declare -a PY_CANDIDATES=()
if [[ -n "$PY_OVERRIDE" ]]; then
  PY_CANDIDATES+=("$PY_OVERRIDE")
else
  if command -v python3 >/dev/null 2>&1; then
    PY_CANDIDATES+=("python3")
  fi
  if [[ -x ".venv/bin/python" ]]; then
    PY_CANDIDATES+=(".venv/bin/python")
  fi
fi

for candidate in "${PY_CANDIDATES[@]}"; do
  if command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]]; then
    if python_works "$candidate"; then
      PY="$candidate"
      break
    fi
  fi
done

if [[ -z "$PY" ]]; then
  echo "[error] 未找到可启动 web_app 的 Python。可尝试：MING_PYTHON=/path/to/python ./start.sh --port ${PORT}" >&2
  exit 1
fi
echo "[start] 使用 Python: $("$PY" -c 'import sys; print(sys.executable)')"

# 3. 前端 build
if [[ "$DO_BUILD" -eq 1 ]]; then
  if [[ ! -d web/node_modules ]]; then
    echo "[start] 安装前端依赖"
    (cd web && npm install)
  fi
  echo "[start] 构建前端"
  (cd web && npm run build)
else
  if [[ ! -f web/dist/index.html ]]; then
    echo "[error] --no-build 但 web/dist/index.html 不存在，无法服务前端页面。" >&2
    echo "[hint] 先运行：./start.sh --port ${PORT}" >&2
    exit 1
  fi
  echo "[start] 跳过前端 build (--no-build)"
fi

# 4. 启 uvicorn
echo "[start] 启动 web_app at http://${HOST}:${PORT}"
exec "$PY" -u -m uvicorn web_app:app --host "$HOST" --port "$PORT"
