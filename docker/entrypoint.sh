#!/usr/bin/env bash
# =============================================================================
# 群钓鱼插件镜像的入口脚本
#
# 做三件事，然后原样交还给 AstrBot 官方启动命令（CMD: python main.py）：
#   1. 把镜像里预置的插件同步到数据卷 data/plugins/<插件名>
#   2. 把 .env 里的机器人凭据注入 AstrBot 主配置 data/cmd_config.json
#      （AstrBot 本身不读 .env，所以这一步是必要的桥接；已存在的值不会被覆盖）
#   3. exec "$@" 启动 AstrBot
# =============================================================================
set -euo pipefail

ASTRBOT_HOME="${ASTRBOT_HOME:-/AstrBot}"
DATA_DIR="${ASTRBOT_HOME}/data"
PLUGIN_NAME="${FISHING_PLUGIN_NAME:-astrbot_plugin_fishing_mini}"
SRC_DIR="${FISHING_PLUGIN_SRC:-/opt/fishing-plugin}"
DST_DIR="${DATA_DIR}/plugins/${PLUGIN_NAME}"
CONFIG_FILE="${DATA_DIR}/cmd_config.json"
INJECTOR="/opt/fishing-docker/inject_cmd_config.py"

log() { echo "[fishing] $*"; }

# -----------------------------------------------------------------------------
# 1. 同步插件到数据卷
#    只有「还没装」或「镜像里的版本更新」时才覆盖，避免抹掉容器里的手动改动。
#    想强制覆盖：FISHING_FORCE_SYNC=1
# -----------------------------------------------------------------------------
mkdir -p "${DATA_DIR}/plugins"

sync_plugin() {
    log "安装插件 -> ${DST_DIR}"
    mkdir -p "${DST_DIR}"
    cp -a "${SRC_DIR}/." "${DST_DIR}/"
    # 清掉只在开发时才需要的东西，别塞进运行目录
    rm -rf "${DST_DIR}/.git" "${DST_DIR}/.github" "${DST_DIR}/docker" \
           "${DST_DIR}/backups" "${DST_DIR}/.gitignore" "${DST_DIR}/.gitattributes" \
           "${DST_DIR}/.dockerignore" "${DST_DIR}/Dockerfile" \
           "${DST_DIR}/docker-compose.yml" "${DST_DIR}/.env" "${DST_DIR}/.env.example"
    find "${DST_DIR}" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
}

if [ "${FISHING_FORCE_SYNC:-0}" = "1" ] \
   || [ ! -f "${DST_DIR}/metadata.yaml" ] \
   || ! cmp -s "${SRC_DIR}/metadata.yaml" "${DST_DIR}/metadata.yaml"; then
    sync_plugin
else
    log "插件已是当前版本，跳过同步（要强制覆盖请设 FISHING_FORCE_SYNC=1）"
fi

# -----------------------------------------------------------------------------
# 2. 注入机器人凭据
#    首次启动时 cmd_config.json 还不存在（AstrBot 自己会生成），
#    所以这里先试一次，再挂一个后台等待任务：等文件出现后补一次注入。
# -----------------------------------------------------------------------------
inject_once() {
    if [ ! -f "${CONFIG_FILE}" ]; then
        return 1
    fi
    python "${INJECTOR}" || log "注入脚本出错（不影响 AstrBot 启动，请看上面的报错）"
    return 0
}

if ! inject_once; then
    log "暂无 ${CONFIG_FILE}（AstrBot 首次启动会生成），已安排后台等待注入"
    (
        for _ in $(seq 1 90); do
            if [ -f "${CONFIG_FILE}" ]; then
                sleep 2
                if python "${INJECTOR}"; then
                    log "凭据已写入配置；如需立即生效请执行：docker compose restart"
                fi
                break
            fi
            sleep 2
        done
    ) &
fi

# -----------------------------------------------------------------------------
# 3. 交还给官方启动命令（默认是 python main.py，工作目录 /AstrBot）
# -----------------------------------------------------------------------------
log "启动 AstrBot：$*"
exec "$@"
