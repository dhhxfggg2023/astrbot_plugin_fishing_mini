# -*- coding: utf-8 -*-
"""数据编辑器页面 ↔ 插件 的「上传文件 + 轮询」数据通道。

================================ 为什么是这条路 ================================

AstrBot 的插件页面（``pages/<目录名>/index.html``）只拿到一个很窄的桥接 SDK：

    apiGet / apiPost / upload / download / subscribeSSE

**没有 PUT**，而「写插件配置」在 AstrBot 里只有
``PUT /api/plugins/{plugin_id}/config`` —— 页面够不着。
所以配置改不了、只能读（``GET /api/plugins/{plugin_id}/config`` 是有的）。

能用的写入口只剩一个：``POST /api/files``（SDK 的 ``upload``）。
它落到 ``ChatService.save_uploaded_file`` →
``<ASTRBOT_ROOT>/data/attachments/<清洗后的文件名>``，非图片走 ``attach_type="file"``，
**文件名基本原样保留**。于是就有了下面这条通道：

    页面  --upload(固定文件名)-->  data/attachments/fishing_editor_bridge.json
                                        |
                                        v  （插件每 POLL_INTERVAL 秒 stat + 读一次）
                                  动作白名单 --> self.config / 存档仓库
                                        |
    页面  <--apiGet 读配置-------- editor_status（插件回写的 JSON 字符串）

============================== 指令信封与 nonce ==============================

页面上传的文件内容就是一个 JSON：

    {"nonce": "<随机串>", "ts": 1789000000, "action": "save_numbers",
     "payload": {"stamina_max": 30}}

* **固定文件名** → 重复上传天然「覆盖」，文件里永远只有最新一条指令。
* **nonce** → 插件记住「上次处理过的 nonce」，相同就跳过，避免同一条指令
  被轮询反复执行（文件不会自己消失）。
* **启动播种** → 插件启动时先读一次现有文件的 nonce 并记为「已处理」，
  这样重启不会把上一次的指令又跑一遍。
* 解析失败 / 未知 action → **照样记 nonce 并回写错误**，绝不卡住轮询。

=============================== 安全边界 ===============================

* 只认白名单里的 action，其余一律拒绝并回写中文原因。
* ``save_content`` 只接受 5 张**内容表**（``fish_defs / rod_defs / bait_defs /
  item_defs / location_defs``）；``editor_status``、``data_action`` 这类
  管理项递进来会被明确拒绝。
* ``save_numbers`` 只接受**数值类**键：从 ``DEFAULTS`` 里筛掉
  ``*_defs / *_slots / *_upgrades`` 与 ``data_* / backup_* / editor_*``，
  再逐个按 DEFAULTS 里的类型校验（int/float/bool/str），非法就整批拒绝。
* 单个指令文件有大小上限，超过就当坏数据（记 nonce + 回写错误）。

⚠️ 维护约定：本模块的方法**不要**对共享的模块级变量做重新赋值
（``X = ...`` 只会改到本模块的副本），要改就原地改（``X.update()`` 等）。
本模块的全局（``logger`` / ``DEFAULTS`` / ``_safe_int`` …）由 main 在模块末尾注入。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

# ---------------------------------------------------------------------------
# 通道常量（页面侧必须与这里保持一致）
# ---------------------------------------------------------------------------

#: 页面把指令上传成这个名字，插件就盯这个名字
BRIDGE_FILE_NAME = "fishing_editor_bridge.json"
#: 轮询间隔（秒）：够快（1~2 秒生效），又不至于把磁盘打满
POLL_INTERVAL = 1.5
#: 指令文件大小上限（正常几 KB，超过说明不是我们的文件）
MAX_BRIDGE_BYTES = 256 * 1024

#: 内容表白名单：键 -> 期望的配置值类型
CONTENT_TABLES: dict[str, type] = {
    "fish_defs": str,   # 一行一条鱼的多行文本
    "rod_defs": list,   # 字符串数组，一条 = 一根竿
    "bait_defs": list,
    "item_defs": list,
    "location_defs": list,
}

#: 数值键的黑名单（后缀 / 前缀）—— 与 main 的「默认值同步」口径保持一致
NUMBER_KEY_BAD_SUFFIXES: tuple[str, ...] = ("_defs", "_slots", "_upgrades")
NUMBER_KEY_BAD_PREFIXES: tuple[str, ...] = ("data_", "backup_", "editor_")

#: 自动备份设置：页面字段名 -> 配置键 + 允许范围（None = 不限）
AUTOBACKUP_KEYS: dict[str, tuple[str, int, int]] = {
    "daily_hour": ("backup_daily_hour", 0, 23),
    "interval_hours": ("backup_interval_hours", 0, 72),
    "keep_daily": ("backup_keep_daily", 0, 3650),
    "keep_interval": ("backup_keep_interval", 0, 1000),
}

#: 自动备份字段的别名：页面/任务书里可能写成别的名字，统一归一到上面的键。
#: （``backup_keep_interval`` 是配置键本身，``keep_keep_interval`` 是历史笔误，
#:   两个都认，省得因为一个字段名把整批设置拒掉。）
AUTOBACKUP_ALIASES: dict[str, str] = {
    "begin_enable": "enable",
    "enabled": "enable",
    "keep_keep_interval": "keep_interval",
    "backup_keep_interval": "keep_interval",
    "keepinterval": "keep_interval",
    "backup_daily_hour": "daily_hour",
    "backup_interval_hours": "interval_hours",
    "backup_keep_daily": "keep_daily",
    "dailyhour": "daily_hour",
    "intervalhours": "interval_hours",
    "keepdaily": "keep_daily",
}

#: editor_status 里 kind 的中文名（存档卡片徽标用）
KIND_LABELS: dict[str, str] = {
    "daily": "每日存档",
    "auto": "按时存档",
    "manual": "手动存档",
}


def _defaults_map() -> dict[str, Any]:
    """``DEFAULTS``（由 main 注入）；拿不到就返回空表，功能降级但不报错。"""
    value = globals().get("DEFAULTS")
    return value if isinstance(value, dict) else {}


def _log_warning(text: str) -> None:
    """写一条警告日志（logger 由 main 注入；拿不到就静默）。"""
    log = globals().get("logger")
    if log is None:
        return
    try:
        log.warning(text)
    except Exception:  # pragma: no cover
        pass


def _log_info(text: str) -> None:
    log = globals().get("logger")
    if log is None:
        return
    try:
        log.info(text)
    except Exception:  # pragma: no cover
        pass


def _log_debug(text: str) -> None:
    log = globals().get("logger")
    if log is None:
        return
    try:
        log.debug(text)
    except Exception:  # pragma: no cover
        pass


def number_whitelist() -> list[str]:
    """可被 ``save_numbers`` 改写的配置键（从 DEFAULTS 里筛出来）。"""
    keys: list[str] = []
    for key, value in _defaults_map().items():
        if not isinstance(key, str) or not key:
            continue
        if key.startswith(NUMBER_KEY_BAD_PREFIXES):
            continue
        if key.endswith(NUMBER_KEY_BAD_SUFFIXES):
            continue
        if isinstance(value, bool) or isinstance(value, (int, float, str)):
            keys.append(key)
    return sorted(keys)


def format_size(size: Any) -> str:
    """字节数 -> 人看的字符串（页面存档卡片直接显示）。"""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "—"
    if value < 1024:
        return f"{int(value)} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / 1024 / 1024:.2f} MB"


def snapshot_view(items: list[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
    """把 ``list_snapshots()`` 的原始条目整理成页面要的字段。"""
    view: list[dict[str, Any]] = []
    for item in (items or [])[:limit]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        view.append(
            {
                "name": str(item.get("name") or ""),
                "rel": str(item.get("rel") or item.get("name") or ""),
                "kind": kind,
                "kind_name": str(
                    item.get("kind_name") or KIND_LABELS.get(kind) or kind
                ),
                "note": str(item.get("note") or ""),
                "time": str(item.get("mtime_text") or item.get("created_text") or ""),
                "mtime": int(item.get("mtime") or 0),
                "players": int(item.get("count") or 0),
                "size": format_size(item.get("size")),
                "size_bytes": int(item.get("size") or 0),
            }
        )
    return view


def _coerce_number(key: str, value: Any, default: Any) -> tuple[Any, str]:
    """按 DEFAULTS 里的类型校验一个数值配置。

    Returns:
        ``(转换后的值, "")`` 表示通过；``(None, 中文原因)`` 表示拒绝。
    """
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value, ""
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value), ""
        return None, f"「{key}」需要 true/false（布尔值）"
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"「{key}」需要整数"
        if isinstance(value, float) and not float(value).is_integer():
            return None, f"「{key}」需要整数（收到 {value}）"
        return int(value), ""
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"「{key}」需要数字"
        return float(value), ""
    if isinstance(default, str):
        if not isinstance(value, str):
            return None, f"「{key}」需要字符串"
        if len(value) > 20000:
            return None, f"「{key}」内容过长（超过 20000 字符）"
        return value, ""
    return None, f"「{key}」的类型不支持由页面修改"


class EditorBridgeMixin:
    """编辑器页面的数据通道（由 FishingPlugin 继承，见 main.py 的类定义）。"""

    # =====================================================================
    # 生命周期
    # =====================================================================
    def start_editor_bridge(self) -> None:
        """启动编辑器页面轮询任务（重复调用是安全的）。

        整段包在 try/except 里：**任何异常都不影响插件启动**，
        最差情况只是「页面保存不了」，游戏本体照常。
        """
        try:
            self._editor_last_nonce = ""
            self._editor_status_text = ""
            self._editor_seed_done = False
            task = getattr(self, "_editor_task", None)
            if task is not None and not task.done():
                return
            loop = asyncio.get_running_loop()
            self._editor_task = loop.create_task(self._editor_bridge_loop())
            _log_info(
                f"数据编辑器通道已启动：每 {POLL_INTERVAL:g} 秒检查 "
                f"{self._editor_bridge_path()}"
            )
        except Exception as e:  # pragma: no cover - 绝不能让插件启动失败
            self._editor_task = None
            _log_warning(f"数据编辑器通道启动失败（页面保存将不可用）：{e}")

    async def stop_editor_bridge(self) -> None:
        """取消轮询任务（停用/重载插件时调用）。"""
        try:
            task = getattr(self, "_editor_task", None)
            self._editor_task = None
            if task is None or task.done():
                return
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception as e:  # pragma: no cover
            _log_warning(f"数据编辑器通道停止失败（忽略）：{e}")

    # =====================================================================
    # 路径
    # =====================================================================
    def _editor_bridge_path(self) -> str:
        """指令文件的绝对路径：``<AstrBotRoot>/data/attachments/<固定名>``。

        必须与 ``ChatService.save_uploaded_file`` 的落点一致，所以这里直接复用
        AstrBot 自己的 ``get_astrbot_root()``（它会认 ``ASTRBOT_ROOT``、桌面版
        的 ``~/.astrbot``，最后才退回 cwd）—— 自己手写路径很容易在桌面版上跑偏。
        连 AstrBot 都导入不到（单测里裸跑本模块）时才退回环境变量。
        """
        root = ""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_root

            root = str(get_astrbot_root() or "")
        except Exception:
            root = ""
        if not root:
            # 兜底：与 astrbot_path.get_astrbot_root() 同序
            root = str(os.environ.get("ASTRBOT_ROOT") or "").strip()
            if not root:
                try:
                    from astrbot.core.utils.runtime_env import (
                        is_packaged_desktop_runtime,
                    )

                    if is_packaged_desktop_runtime():
                        root = os.path.join(os.path.expanduser("~"), ".astrbot")
                except Exception:
                    root = ""
            if not root:
                root = os.getcwd()
        return os.path.join(str(root), "data", "attachments", BRIDGE_FILE_NAME)

    # =====================================================================
    # 轮询
    # =====================================================================
    async def _editor_bridge_loop(self) -> None:
        """后台循环：读一条指令 -> 执行 -> 回写状态。绝不因为坏数据退出。"""
        try:
            # 启动播种：把「重启前留下的那条指令」记成已处理，不重复执行
            await self._editor_seed_nonce()
            while True:
                try:
                    await self._editor_poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # pragma: no cover - 循环必须活下去
                    _log_warning(f"编辑器通道轮询出错（会继续跑）：{e}")
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover
            _log_warning(f"编辑器通道轮询任务退出：{e}")

    async def _editor_seed_nonce(self) -> None:
        """播种 nonce：读一次现有文件但不执行。"""
        try:
            payload = self._editor_read_bridge_file()
            if isinstance(payload, dict):
                self._editor_last_nonce = str(payload.get("nonce") or "")
            self._editor_seed_done = True
        except Exception as e:  # pragma: no cover
            _log_debug(f"编辑器通道播种 nonce 失败（下次轮询再说）：{e}")

    def _editor_read_bridge_file(self) -> dict[str, Any] | None:
        """读指令文件；不存在 / 读不动 / 不是 JSON 对象都返回 None。"""
        path = self._editor_bridge_path()
        if not path:
            return None
        try:
            if not os.path.isfile(path):
                return None
            if os.path.getsize(path) > MAX_BRIDGE_BYTES:
                return None
            with open(path, encoding="utf-8") as fp:
                raw = fp.read(MAX_BRIDGE_BYTES + 1)
        except OSError:
            return None
        if len(raw) > MAX_BRIDGE_BYTES:
            return None
        stripped = raw.lstrip("\ufeff \t\r\n")
        if not stripped:
            return None
        try:
            payload = json.loads(stripped)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _editor_poll_once(self) -> None:
        """检查一次指令文件：没新指令就立刻返回（只 stat + 读小 JSON）。"""
        path = self._editor_bridge_path()
        if not path or not os.path.isfile(path):
            return  # 目录/文件还不存在 -> 静默跳过

        raw_text = ""
        payload: dict[str, Any] | None = None
        try:
            with open(path, encoding="utf-8") as fp:
                raw_text = fp.read(MAX_BRIDGE_BYTES + 1)
        except OSError as e:
            _log_debug(f"编辑器通道读文件失败（跳过）：{e}")
            return

        nonce = ""
        action = ""
        action_payload: dict[str, Any] = {}
        if len(raw_text) > MAX_BRIDGE_BYTES:
            nonce = ""  # 太大：连 nonce 都读不到，只能靠人工再传一次
            action = ""
            payload = None
        else:
            try:
                parsed = json.loads(raw_text.lstrip("\ufeff \t\r\n") or "null")
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                payload = parsed
                nonce = str(parsed.get("nonce") or "")
                action = str(parsed.get("action") or "").strip()
                raw_payload = parsed.get("payload")
                action_payload = raw_payload if isinstance(raw_payload, dict) else {}

        # 坏 JSON：只要文件里能抠出 nonce 就记下来，避免每 1.5 秒重试一次
        if payload is None:
            if not nonce:
                nonce = self._editor_nonce_from_text(raw_text)
            if not nonce:
                return
            if nonce == self._editor_last_nonce:
                return
            self._editor_last_nonce = nonce
            _log_warning("编辑器通道收到坏 JSON（已跳过并记录 nonce，不会卡住轮询）")
            await self._editor_write_status(
                action="", ok=False, message="页面传上来的不是合法 JSON，已忽略这一条"
            )
            return

        if not nonce or nonce == self._editor_last_nonce:
            return  # 同一条指令：已经处理过，跳过

        # 先记 nonce 再执行：无论后面成功还是炸了，都不会重复跑
        self._editor_last_nonce = nonce
        ok, message = await self._editor_run_action(action, action_payload)
        await self._editor_write_status(action=action, ok=ok, message=message)

    @staticmethod
    def _editor_nonce_from_text(raw_text: str) -> str:
        """坏 JSON 兜底：用正则把 ``"nonce": "xxx"`` 抠出来。"""
        match = re.search(r'"nonce"\s*:\s*"([^"]{1,128})"', raw_text or "")
        return match.group(1) if match else ""

    # =====================================================================
    # 动作分发
    # =====================================================================
    async def _editor_run_action(self, action: str, payload: dict[str, Any]) -> tuple[bool, str]:
        """执行一条指令。返回 ``(是否成功, 给页面看的中文说明)``。"""
        handlers = {
            "save_content": self._editor_save_content,
            "save_numbers": self._editor_save_numbers,
            "snapshot_create": self._editor_snapshot_create,
            "snapshot_restore": self._editor_snapshot_restore,
            "snapshot_delete": self._editor_snapshot_delete,
            "snapshot_rename": self._editor_snapshot_rename,
            "save_autobackup": self._editor_save_autobackup,
            "refresh": self._editor_refresh,
        }
        handler = handlers.get(str(action or "").strip())
        if handler is None:
            known = "、".join(sorted(handlers))
            return False, f"不认识的动作「{action}」（可用：{known}）"
        try:
            return await handler(payload if isinstance(payload, dict) else {})
        except Exception as e:  # pragma: no cover - 单条指令失败不该拖垮轮询
            _log_warning(f"编辑器动作「{action}」执行失败：{e}")
            return False, f"动作「{action}」执行失败：{e}"

    # ------------------------------------------------------------ save_content
    async def _editor_save_content(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """写内容表（鱼池/鱼竿/鱼饵/道具/钓点）。"""
        tables = payload.get("tables")
        if not isinstance(tables, dict) or not tables:
            return False, "save_content 需要 tables 字段（内容是 {表名: 新内容}）"
        unknown = [k for k in tables if k not in CONTENT_TABLES]
        if unknown:
            return False, (
                "只允许改这几张内容表："
                + "、".join(CONTENT_TABLES)
                + f"；拒绝「{'、'.join(map(str, unknown))}」"
            )

        normalized: dict[str, Any] = {}
        for key, value in tables.items():
            expect = CONTENT_TABLES[key]
            if expect is str:
                if not isinstance(value, str):
                    return False, f"「{key}」需要多行文本（字符串）"
                normalized[key] = value
                continue
            if not isinstance(value, list):
                return False, f"「{key}」需要字符串数组"
            lines: list[str] = []
            for index, entry in enumerate(value):
                if not isinstance(entry, str):
                    return False, f"「{key}」第 {index + 1} 条不是字符串"
                lines.append(entry)
            normalized[key] = lines

        try:
            self.config.update(normalized)
        except Exception as e:
            return False, f"写入配置失败：{e}"
        saved = await self._editor_save_config()
        self._editor_refresh_runtime()
        names = "、".join(f"{k}({len(v) if isinstance(v, list) else '文本'})" for k, v in normalized.items())
        if not saved:
            # 内存里已经生效（游戏立刻用新内容），但配置没落盘 —— 重启会回到旧内容
            return True, f"已写入内容表：{names}（配置这次没落盘，重启会丢）"
        return True, f"已写入内容表：{names}"

    # ------------------------------------------------------------ save_numbers
    async def _editor_save_numbers(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """写数值类配置（白名单 + 逐个类型校验）。"""
        if not isinstance(payload, dict) or not payload:
            return False, "save_numbers 需要至少一个「配置键: 新值」"
        defaults = _defaults_map()
        allowed = set(number_whitelist())
        changed: dict[str, Any] = {}

        for key, value in payload.items():
            key = str(key)
            if key not in allowed or key not in defaults:
                return False, f"「{key}」不是可改的数值项（内容表请用 save_content）"
            coerced, why = _coerce_number(key, value, defaults[key])
            if why:
                return False, why
            changed[key] = coerced

        try:
            self.config.update(changed)
        except Exception as e:
            return False, f"写入配置失败：{e}"
        saved = await self._editor_save_config()
        self._editor_refresh_runtime()
        names = "、".join(f"{k}={v}" for k, v in list(changed.items())[:8])
        more = "" if len(changed) <= 8 else f" 等 {len(changed)} 项"
        if not saved:
            return True, f"已写入 {names}{more}（配置这次没落盘，重启会丢）"
        return True, f"已写入 {names}{more}"

    # ------------------------------------------------------------ save_autobackup
    async def _editor_save_autobackup(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """写自动备份设置（开关 + 时间 + 保留份数）。"""
        if not isinstance(payload, dict) or not payload:
            return False, "save_autobackup 需要至少一个字段"
        unknown = [
            k for k in payload
            if AUTOBACKUP_ALIASES.get(str(k), str(k)) not in AUTOBACKUP_KEYS
            and AUTOBACKUP_ALIASES.get(str(k), str(k)) != "enable"
        ]
        if unknown:
            return False, f"不认识的自动备份字段「{'、'.join(map(str, unknown))}」"

        changed: dict[str, Any] = {}
        for field, raw in payload.items():
            name = AUTOBACKUP_ALIASES.get(str(field), str(field))
            if name == "enable":
                if not isinstance(raw, bool):
                    return False, "「enable」需要 true/false"
                changed["enable_auto_backup"] = raw
                continue
            key, low, high = AUTOBACKUP_KEYS[name]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return False, f"「{field}」需要数字"
            number = int(raw)
            if number < low or number > high:
                return False, f"「{field}」要在 {low}~{high} 之间（收到 {number}）"
            changed[key] = number

        try:
            self.config.update(changed)
        except Exception as e:
            return False, f"写入配置失败：{e}"
        saved = await self._editor_save_config()
        self._editor_refresh_runtime()
        names = "、".join(f"{k}={v}" for k, v in changed.items())
        if not saved:
            return True, f"自动备份已更新：{names}（配置这次没落盘，重启会丢）"
        return True, f"自动备份已更新：{names}"

    # ------------------------------------------------------------ refresh
    async def _editor_refresh(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """只刷新状态（页面轮询进度用）。"""
        return True, "状态已刷新"

    # ------------------------------------------------------------ 存档动作
    async def _editor_snapshot_create(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """新建一份手动存档（可带备注名）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return False, "存档模块不可用（_backup.py 是否缺失？）"
        note = str(payload.get("note") or "").strip()[:120] or "页面手动存档"
        message = await self._snapshot("manual", note=note)
        return True, f"已新建存档：{message}（备注：{note}）"

    async def _editor_snapshot_restore(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """恢复整份快照，或只恢复其中一个玩家（恢复前自动存一份）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return False, "存档模块不可用（_backup.py 是否缺失？）"
        name = str(payload.get("name") or "").strip()
        player = str(payload.get("player") or "").strip()
        if not name and not player:
            return False, "snapshot_restore 需要 name（快照名，可留空=最新）"
        message = await self._restore_snapshot(name, player)
        ok = "找不到" not in message and "没有" not in message and "失败" not in message
        return ok, message

    async def _editor_snapshot_delete(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """删除一份存档（删除前自动存一份，防手滑）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return False, "存档模块不可用（_backup.py 是否缺失？）"
        name = str(payload.get("name") or "").strip()
        if not name:
            return False, "snapshot_delete 需要 name（要删哪一份）"
        path = store.find_snapshot(name)
        if path is None:
            return False, f"找不到存档「{name}」（可能已经被删了）"
        target = path.name
        safety = await self._snapshot("manual", note=f"删除「{name}」前的自动存档")
        if not store.delete_snapshot(target):
            return False, f"删除「{target}」失败（文件可能被占用）"
        return True, f"已删除存档「{target}」；删除前的快照：{safety}"

    async def _editor_snapshot_rename(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """给一份存档改备注（不动文件名，保持「最新 = 按 mtime」的语义）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return False, "存档模块不可用（_backup.py 是否缺失？）"
        name = str(payload.get("name") or "").strip()
        if not name:
            return False, "snapshot_rename 需要 name（改哪一份）"
        if "note" not in payload:
            return False, "snapshot_rename 需要 note（新的备注名，可以是空串）"
        note = str(payload.get("note") or "").strip()[:120]
        if not store.rename_snapshot(name, note):
            return False, f"改名失败：找不到存档「{name}」（或文件不可写）"
        return True, f"已把「{name}」的备注改成「{note or '（空）'}」"

    # =====================================================================
    # 状态回写（插件 -> 页面：editor_status 配置项）
    # =====================================================================
    async def _editor_write_status(self, *, action: str, ok: bool, message: str) -> None:
        """把最新状态写成 JSON 字符串，塞进配置项 ``editor_status``。"""
        text = await self._editor_build_status(action=action, ok=ok, message=message)
        self._editor_status_text = text
        try:
            self.config["editor_status"] = text
        except Exception as e:  # pragma: no cover
            _log_debug(f"写 editor_status 失败：{e}")
            return
        if not await self._editor_save_config():
            _log_debug("editor_status 落盘失败（页面可能读到旧状态）")

    async def _editor_build_status(self, *, action: str, ok: bool, message: str) -> str:
        """拼出 editor_status 的 JSON（页面读配置项后直接 JSON.parse）。"""
        store = getattr(self, "backup_store", None)
        snapshots: list[dict[str, Any]] = []
        total = 0
        by_kind: dict[str, int] = {}
        next_auto = ""
        if store is not None:
            try:
                items = store.list_snapshots()
                snapshots = snapshot_view(items)
                total = len(items)
                for item in items:
                    key = str(item.get("kind") or "")
                    by_kind[key] = by_kind.get(key, 0) + 1
                next_auto = self._editor_next_auto_text(items)
            except Exception as e:  # pragma: no cover
                _log_debug(f"读取存档清单失败：{e}")

        player_count = 0
        try:
            player_count = len(await self._player_ids())
        except Exception as e:  # pragma: no cover
            _log_debug(f"读取玩家数失败：{e}")

        autobackup = {
            "enable": bool(self._editor_cfg_get("enable_auto_backup", True)),
            "daily_hour": int(self._editor_cfg_get("backup_daily_hour", 4) or 0),
            "interval_hours": int(self._editor_cfg_get("backup_interval_hours", 6) or 0),
            "keep_daily": int(self._editor_cfg_get("backup_keep_daily", 30) or 0),
            "keep_interval": int(self._editor_cfg_get("backup_keep_interval", 20) or 0),
        }

        payload = {
            "updated_at": int(time.time()),
            "updated_text": self._editor_now(),
            "last_action": str(action or ""),
            "last_result": str(message or ""),
            "ok": bool(ok),
            "snapshots": snapshots,
            "snapshot_total": total,
            "by_kind": by_kind,
            "players": player_count,
            "next_auto_backup": next_auto,
            "autobackup": autobackup,
            "numbers_editable": number_whitelist(),
            "bridge_file": BRIDGE_FILE_NAME,
        }
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):  # pragma: no cover
            return json.dumps(
                {
                    "updated_at": int(time.time()),
                    "ok": bool(ok),
                    "last_result": str(message or ""),
                    "snapshots": [],
                },
                ensure_ascii=False,
            )

    def _editor_next_auto_text(self, items: list[dict[str, Any]]) -> str:
        """下一次自动存档的大致时间（给人看的字符串，估不出来就空串）。"""
        try:
            if not self._editor_cfg_get("enable_auto_backup", True):
                return ""
            interval = int(self._editor_cfg_get("backup_interval_hours", 6) or 0)
            if interval <= 0:
                return ""
            auto_items = [x for x in items if x.get("kind") == "auto"]
            last_ts = float(auto_items[0].get("mtime") or 0) if auto_items else 0.0
            if last_ts <= 0:
                return "下次循环（约 1 分钟内）"
            next_ts = last_ts + interval * 3600
            if next_ts <= time.time():
                return "下次循环（约 1 分钟内）"
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(next_ts))
        except Exception:  # pragma: no cover
            return ""

    # =====================================================================
    # 小工具
    # =====================================================================
    def _editor_cfg_get(self, key: str, default: Any = None) -> Any:
        """读配置（优先 self.cfg，退回 self.config，再退回内置默认值）。"""
        try:
            cfg = getattr(self, "cfg", None)
            if isinstance(cfg, dict) and cfg.get(key) is not None:
                return cfg.get(key)
        except Exception:
            pass
        try:
            value = self.config.get(key)
            if value is not None:
                return value
        except Exception:
            pass
        return _defaults_map().get(key, default)

    async def _editor_save_config(self) -> bool:
        """落盘配置。优先 ``save_config_async``，退回 ``save_config``。"""
        saver = getattr(self.config, "save_config_async", None)
        if callable(saver):
            try:
                await saver()
                return True
            except Exception as e:
                _log_warning(f"编辑器写配置失败（save_config_async）：{e}")
        saver = getattr(self.config, "save_config", None)
        if callable(saver):
            try:
                saver()
                return True
            except Exception as e:  # pragma: no cover
                _log_warning(f"编辑器写配置失败（save_config）：{e}")
        return False

    def _editor_refresh_runtime(self) -> None:
        """配置改完立刻重建热路径缓存（新数值这一秒就生效，等页面重载）。

        只碰 ``cfg`` / 解析结构，不动玩家数据；出错只记日志。
        """
        try:
            refresh = getattr(self, "_refresh_config", None)
            if callable(refresh):
                refresh()
        except Exception as e:  # pragma: no cover
            _log_warning(f"编辑器改完配置后重建缓存失败（下次重载会生效）：{e}")

    @staticmethod
    def _editor_now() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")
