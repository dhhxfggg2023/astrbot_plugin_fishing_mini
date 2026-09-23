# =============================================================================
# 群钓鱼插件 · 一体化镜像
#
# 基于 AstrBot 官方镜像（soulter/astrbot:latest）预装本插件：
#   docker build -t astrbot-fishing .
#   docker compose up -d          （推荐，配合 .env 使用）
#
# 官方镜像事实（已核对 AstrBot 仓库 Dockerfile）：
#   * 基础镜像 python:3.12-slim，WORKDIR 是 /AstrBot
#   * 启动命令是 CMD ["python", "main.py"]，**没有** ENTRYPOINT
#   * 暴露 6185（管理面板），数据目录 /AstrBot/data
# 因此这里可以安全地挂一个自己的 ENTRYPOINT：官方 CMD 会作为参数传进来，
# 脚本末尾 exec "$@" 原样交还给 AstrBot 启动流程（见 docker/entrypoint.sh）。
# =============================================================================
FROM soulter/astrbot:latest

LABEL org.opencontainers.image.title="AstrBot + 群钓鱼插件" \
      org.opencontainers.image.description="AstrBot 官方镜像预装 astrbot_plugin_fishing_mini（QQ 群钓鱼小游戏）" \
      org.opencontainers.image.licenses="MIT"

# 插件源码：整个仓库就是插件本体，暂存到 /opt（不直接写 data，
# 因为 data 通常是挂载卷，镜像里的内容会被卷覆盖）
COPY . /opt/fishing-plugin/

# 部署脚本：启动时把插件同步进 data/plugins，并把 .env 里的机器人凭据注入 AstrBot 配置
COPY docker/ /opt/fishing-docker/
RUN chmod +x /opt/fishing-docker/entrypoint.sh

ENV FISHING_PLUGIN_SRC=/opt/fishing-plugin \
    FISHING_PLUGIN_NAME=astrbot_plugin_fishing_mini

# 官方 CMD ["python", "main.py"] 会作为参数传给本脚本
ENTRYPOINT ["/bin/bash", "/opt/fishing-docker/entrypoint.sh"]
