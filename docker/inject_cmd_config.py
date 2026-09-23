#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .env（容器环境变量）里的机器人凭据注入 AstrBot 主配置。

为什么需要这一步：AstrBot v4.x 不读取 .env 文件（源码里没有 dotenv），
QQ 官方机器人的 appid/secret、OneBot 的 ws_reverse_token 都存在
``data/cmd_config.json`` 的 ``platform`` 数组里。docker compose 会把
``.env`` 变成容器环境变量，本脚本负责把它落进配置文件。

安全约定：
* 只填「平台不存在」或「字段为空」的情况；想强制覆盖设 FISHING_CONFIG_OVERWRITE=1
* 写之前先备份 cmd_config.json
* 日志里绝不打印 secret / token 的值

字段名与 AstrBot v4.28.1 真实配置一致：
* qq_official: id, type, enable, appid, secret, enable_group_c2c,
  enable_guild_direct_message, use_markdown
* aiocqhttp:   id, type, enable, ws_reverse_host, ws_reverse_port, ws_reverse_token
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

#: AstrBot 官方镜像的工作目录是 /AstrBot，桌面版可用 ASTRBOT_ROOT 覆盖
ASTRBOT_HOME = Path(
    os.environ.get("ASTRBOT_HOME") or os.environ.get("ASTRBOT_ROOT") or "/AstrBot"
)
CONFIG_FILE = Path(
    os.environ.get("ASTRBOT_CONFIG_FILE") or ASTRBOT_HOME / "data" / "cmd_config.json"
)

PLATFORM_QQ_OFFICIAL = "qq_official"
PLATFORM_AIOCQHTTP = "aiocqhttp"


def env_str(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on", "y")


def env_flag(name: str) -> bool | None:
    """三态开关：.env 里没写这一项时返回 None（保持 AstrBot 里的现有配置）。

    密钥类字段只在为空时写入，而 ``enable_group_c2c`` 这类开关只要在 .env
    里显式写了就以 .env 为准，否则用户改了 .env 却发现没效果。
    """
    raw = env_str(name)
    if not raw:
        return None
    return raw.lower() in ("1", "true", "yes", "on", "y")


def log(message: str) -> None:
    print(f"[fishing-config] {message}", flush=True)


def load_config() -> dict[str, Any] | None:
    if not CONFIG_FILE.is_file():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"读取 {CONFIG_FILE} 失败：{e}")
        return None
    return data if isinstance(data, dict) else None


def backup(path: Path) -> None:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    try:
        shutil.copy2(path, target)
        log(f"已备份原配置 -> {target.name}")
    except OSError as e:  # pragma: no cover - 只影响备份，不影响主流程
        log(f"备份失败（继续）：{e}")


def find_platform(platforms: list[Any], ptype: str) -> dict[str, Any] | None:
    for item in platforms:
        if isinstance(item, dict) and str(item.get("type")) == ptype:
            return item
    return None


def ensure_fields(
    platform: dict[str, Any],
    values: dict[str, Any],
    overwrite: bool,
    force_keys: frozenset[str] = frozenset(),
) -> list[str]:
    """按需写入字段，返回被改动的字段名列表。

    ``force_keys`` 里的字段只要在 .env 里显式给了值就直接写（开关类），
    其余字段只在「当前为空」或 ``overwrite`` 时写入（密钥类）。
    """
    changed: list[str] = []
    for key, value in values.items():
        if value is None or value == "":
            continue
        current = platform.get(key)
        if key in force_keys or current in (None, "", [], {}) or overwrite:
            if current != value:
                platform[key] = value
                changed.append(key)
    return changed


def want_qq_official() -> bool:
    appid = env_str("QQ_APPID")
    secret = env_str("QQ_SECRET")
    enabled = env_bool("QQ_ENABLE", bool(appid and secret))
    if not enabled:
        return False
    if not (appid and secret):
        log("QQ_ENABLE 打开了，但 QQ_APPID / QQ_SECRET 没填全，跳过")
        return False
    return True


def want_onebot() -> bool:
    token = env_str("ONEBOT_WS_TOKEN")
    enabled = env_bool("ONEBOT_ENABLE", bool(token))
    if enabled and not token:
        log("ONEBOT_ENABLE 打开了，但 ONEBOT_WS_TOKEN 是空的（无 token 也能连，但建议填）")
    return enabled


def apply_qq_official(platforms: list[Any], overwrite: bool) -> list[str]:
    notes: list[str] = []
    platform = find_platform(platforms, PLATFORM_QQ_OFFICIAL)
    created = False
    if platform is None:
        platform = {"id": str(uuid.uuid4()), "type": PLATFORM_QQ_OFFICIAL}
        platforms.append(platform)
        created = True

    changed = ensure_fields(
        platform,
        {
            "appid": env_str("QQ_APPID"),
            "secret": env_str("QQ_SECRET"),
            "enable": True,
            "enable_group_c2c": env_flag("QQ_ENABLE_GROUP_C2C"),
            "enable_guild_direct_message": env_flag("QQ_ENABLE_GUILD_DM"),
            "use_markdown": env_flag("QQ_USE_MARKDOWN"),
        },
        overwrite,
        force_keys=frozenset(
            {"enable_group_c2c", "enable_guild_direct_message", "use_markdown"}
        ),
    )
    # 始终确保平台是启用状态（除非显式要求覆盖且用户填了 false）
    if platform.get("enable") is not True:
        platform["enable"] = True
        changed.append("enable")

    verb = "新建" if created else "更新"
    if changed:
        notes.append(f"{verb} QQ 官方机器人配置（写入字段：{', '.join(changed)}）")
    else:
        notes.append("QQ 官方机器人配置已是最新，无需改动")
    return notes


def apply_onebot(platforms: list[Any], overwrite: bool) -> list[str]:
    notes: list[str] = []
    platform = find_platform(platforms, PLATFORM_AIOCQHTTP)
    created = False
    if platform is None:
        platform = {"id": str(uuid.uuid4()), "type": PLATFORM_AIOCQHTTP}
        platforms.append(platform)
        created = True

    try:
        port = int(env_str("ONEBOT_WS_PORT", "6199") or 6199)
    except ValueError:
        port = 6199
    changed = ensure_fields(
        platform,
        {
            "ws_reverse_host": env_str("ONEBOT_WS_HOST", "0.0.0.0"),
            "ws_reverse_port": port,
            "ws_reverse_token": env_str("ONEBOT_WS_TOKEN"),
            "enable": True,
        },
        overwrite,
    )
    if platform.get("enable") is not True:
        platform["enable"] = True
        changed.append("enable")

    verb = "新建" if created else "更新"
    if changed:
        notes.append(f"{verb} OneBot v11 反向 WS 配置（写入字段：{', '.join(changed)}）")
    else:
        notes.append("OneBot v11 配置已是最新，无需改动")
    return notes


def main() -> int:
    config = load_config()
    if config is None:
        log(f"没找到 {CONFIG_FILE}：AstrBot 首次启动后才会生成，稍后会自动重试")
        return 1

    platforms = config.get("platform")
    if not isinstance(platforms, list):
        platforms = []
        config["platform"] = platforms

    overwrite = env_bool("FISHING_CONFIG_OVERWRITE", False)
    if overwrite:
        log("FISHING_CONFIG_OVERWRITE=1：已存在的值也会被 .env 覆盖")

    notes: list[str] = []
    if want_qq_official():
        notes.extend(apply_qq_official(platforms, overwrite))
    if want_onebot():
        notes.extend(apply_onebot(platforms, overwrite))

    if not notes:
        log("没有需要注入的机器人凭据（.env 里 QQ_APPID/QQ_SECRET/ONEBOT_WS_TOKEN 都空着）")
        return 0

    backup(CONFIG_FILE)
    try:
        CONFIG_FILE.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as e:
        log(f"写入配置失败：{e}")
        return 1

    for note in notes:
        log(note)
    log("配置已写入。若平台没连上，执行一次 docker compose restart 让 AstrBot 重新读配置")
    return 0


if __name__ == "__main__":
    sys.exit(main())
