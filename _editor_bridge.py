# -*- coding: utf-8 -*-
"""数据编辑器页面 ↔ 插件 的 Web API 通道（插件自己注册路由）。

=============================== 为什么是这条路 ================================

AstrBot 的插件页面（``pages/<目录名>/index.html``）拿到的是很窄的桥接 SDK
（``apiGet / apiPost / upload / download / subscribeSSE``）。它能把请求转发到
**插件自己注册的 Web API**：

    页面  bridge.apiGet("config")
      →  Dashboard  /api/v1/plugins/extensions/<插件名>/config
      →  本插件用 context.register_web_api("/<插件名>/config", ...) 注册的 handler

要点（已核对 AstrBot 源码与官方文档）：

* **路由必须带插件名前缀**（``/<plugin>/config``），页面侧的 endpoint **不带**
  （``config``）—— 少一层或多一层都会得到「未找到该路由」。
* handler 用 ``astrbot.api.web`` 的 ``json_response`` / ``error_response`` /
  ``request``；登录态由 Dashboard 的 ``require_plugin_scope`` 校验。
* 三种路由：GET ``config``（读）、POST ``config``（写内容表/数值/自动备份）、
  POST ``snapshot``（存档新建/恢复/删除/改名）。

⚠️ 历史：早先试过「页面上传文件 + 插件轮询」，在真实环境下被
``403 /api/files：Insufficient API key scope`` 挡掉（插件页面的 API key 没有
上传权限），已废弃 —— 不要再回到那条路。

============================== 安全边界 ==============================

* 写操作只认白名单：``save_content`` 只收 9 张内容表；``save_numbers`` 只收
  ``DEFAULTS`` 里的数值类键（筛掉 ``*_defs / *_slots / *_upgrades`` 与
  ``data_* / backup_* / editor_*``）并逐个类型校验，非法就整批拒绝。
* 未知 action、坏 JSON、非对象请求体 → 返回结构化错误，绝不 500。

⚠️ 维护约定：本模块的方法**不要**对共享的模块级变量做重新赋值
（``X = ...`` 只会改到本模块的副本），要改就原地改（``X.update()`` 等）。
本模块的全局（``logger`` / ``DEFAULTS`` / ``_safe_int`` …）由 main 在模块末尾注入。
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import time
from typing import Any

# ---------------------------------------------------------------------------
# 通道常量（页面侧必须与这里保持一致）
# ---------------------------------------------------------------------------

#: 页面调用的「相对插件」endpoint（路由注册时会加上 /<插件名> 前缀）
ENDPOINT_CONFIG = "config"
ENDPOINT_SNAPSHOT = "snapshot"

#: 内容表白名单：键 -> 期望的配置值类型
CONTENT_TABLES: dict[str, type] = {
    "fish_defs": str,   # 一行一条鱼的多行文本
    "rod_defs": list,   # 字符串数组，一条 = 一根竿
    "bait_defs": list,
    "item_defs": list,
    "location_defs": list,    "collectible_defs": str,
    "variant_defs": str,
    "weather_defs": str,
    "easter_egg_defs": str,
    "button_defs": str,   # 场景|文案|点击后发送|样式（回复里的按钮）
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
    "keep_manual": ("backup_keep_manual", 0, 10000),
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
    "backup_keep_manual": "keep_manual",
    "keepmanual": "keep_manual",
    "keep_manual_snapshots": "keep_manual",
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


# =============================================================================
# 页面 Web API（插件注册；handler 用 astrbot.api.web 的 helper）
# =============================================================================


def _meta_name() -> str:
    """读 metadata.yaml 里的 name（AstrBot 用作插件名，也就是路由前缀）。"""
    try:
        path = pathlib.Path(__file__).with_name("metadata.yaml")
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("name:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    except Exception:
        pass
    return ""


class EditorApiMixin:
    """把编辑器页面的读写接口挂成插件 Web API（由 EditorBridgeMixin 继承）。"""

    # ---------------------------------------------------------------- 路由表
    def _editor_plugin_names(self) -> list[str]:
        """可能被 AstrBot 当作插件名的标识（路由前缀必须与它一致）。"""
        names: list[str] = []

        def push(value: Any) -> None:
            text = str(value or "").strip().strip("/")
            if text and text not in names:
                names.append(text)

        push(getattr(self, "name", ""))          # star_manager 注入的 name
        push(_meta_name())                       # metadata.yaml 的 name
        push(pathlib.Path(__file__).parent.name)  # 插件目录名
        return names or ["astrbot_plugin_qq_fishing"]

    def _editor_route_specs(self) -> list[tuple[str, Any, list[str], str]]:
        """(路由模板, handler, 方法, 描述)。"""
        return [
            (
                "/{plugin}/" + ENDPOINT_CONFIG,
                self.editor_api_config,
                ["GET"],
                "读取群钓鱼的配置与编辑器状态",
            ),
            (
                "/{plugin}/" + ENDPOINT_CONFIG,
                self.editor_api_config_save,
                ["POST"],
                "保存群钓鱼的内容表 / 数值 / 自动备份设置",
            ),
            (
                "/{plugin}/" + ENDPOINT_SNAPSHOT,
                self.editor_api_snapshot,
                ["POST"],
                "群钓鱼存档：新建 / 恢复 / 删除 / 改名",
            ),
        ]

    # ------------------------------------------------------------ GET config
    def _editor_config_payload(self) -> dict[str, Any]:
        """交给页面的整份配置（内容表 + 数值项 + editor_status）。

        结构刻意与「读插件配置」一致：页面直接按配置键取值，
        没有 ``config`` 子对象（老代码的 ``res.config || res`` 也能吃下）。
        """
        payload: dict[str, Any] = {}
        try:
            payload = {str(k): v for k, v in dict(self.config).items()}
        except Exception as e:  # pragma: no cover
            _log_warning(f"读取插件配置失败：{e}")
        if not payload.get("editor_status"):
            payload["editor_status"] = getattr(self, "_editor_status_text", "") or ""
        payload["status"] = "ok"
        payload["transport"] = "plugin-api"
        return payload

    async def editor_api_config(self):
        """GET config：返回当前配置，供页面渲染。"""
        data = self._editor_config_payload()
        try:
            from astrbot.api.web import json_response
        except Exception:  # pragma: no cover - 单测里可能没有 astrbot
            return data
        return json_response(data)

    # ----------------------------------------------------------- POST config
    async def editor_api_config_save(self, body: dict[str, Any] | None = None):
        """POST config：支持两种写法。

        * ``{"action": "save_content", "payload": {...}}``（页面现在用这种）
        * ``{"tables": {...}}`` / ``{"numbers": {...}}`` / ``{"autobackup": {...}}``

        ``body`` 可以显式传入（单测用），不传就读真实请求体。
        """
        if body is None:
            body, err = await self._editor_request_json()
            if err:
                return self._editor_api_error(err)
        if not isinstance(body, dict):
            return self._editor_api_error("请求体必须是一个 JSON 对象")

        results: list[str] = []
        ok_all = True
        action = body.get("action")
        if isinstance(action, str) and action.strip():
            ok_all, message = await self._editor_run_action(
                action.strip(), body.get("payload") if isinstance(body.get("payload"), dict) else {}
            )
            results.append(message)
        else:
            plan = (
                ("tables", "save_content"),
                ("numbers", "save_numbers"),
                ("autobackup", "save_autobackup"),
            )
            for key, remote in plan:
                if key not in body:
                    continue
                chunk = body[key]
                if remote == "save_content":
                    chunk = {"tables": chunk}
                elif not isinstance(chunk, dict):
                    return self._editor_api_error(f"「{key}」需要是一个 JSON 对象")
                ok, message = await self._editor_run_action(remote, chunk)
                ok_all = ok_all and ok
                results.append(message)
            if not results:
                return self._editor_api_error(
                    "请求体里没有可保存的内容：至少要有 tables / numbers / autobackup 之一"
                )

        summary = "；".join(results)
        await self._editor_write_status(action="save", ok=ok_all, message=summary)
        return self._editor_api_ok(summary, ok=ok_all)

    # --------------------------------------------------------- POST snapshot
    async def editor_api_snapshot(self, body: dict[str, Any] | None = None):
        """POST snapshot：``{"action": "create|restore|delete|rename|refresh", ...}``。

        ``body`` 可以显式传入（单测用），不传就读真实请求体。
        """
        if body is None:
            body, err = await self._editor_request_json()
            if err:
                return self._editor_api_error(err)
        if not isinstance(body, dict):
            return self._editor_api_error("请求体必须是一个 JSON 对象")

        mapping = {
            "create": "snapshot_create",
            "restore": "snapshot_restore",
            "delete": "snapshot_delete",
            "rename": "snapshot_rename",
            "refresh": "refresh",
        }
        action = str(body.get("action") or "").strip().lower()
        remote = mapping.get(action)
        if remote is None:
            return self._editor_api_error(
                f"不认识的存档动作「{action}」（可用：{'、'.join(mapping)}）"
            )

        ok, message = await self._editor_run_action(remote, body)
        await self._editor_write_status(action=remote, ok=ok, message=message)
        return self._editor_api_ok(message, ok=ok)

    # ---------------------------------------------------------------- 小工具
    async def _editor_request_json(self) -> tuple[dict[str, Any], str]:
        """读 JSON 请求体，返回 ``(body, 错误说明)``。"""
        try:
            from astrbot.api.web import request as web_request
        except Exception:
            return {}, "当前 AstrBot 不支持 astrbot.api.web.request，无法读取请求体"
        try:
            body = await web_request.json(default={})
        except Exception as e:
            return {}, f"解析请求体失败：{e}"
        if not isinstance(body, dict):
            return {}, "请求体必须是一个 JSON 对象"
        return body, ""

    def _editor_api_ok(self, message: str, *, ok: bool = True):
        """成功响应：带上最新 editor_status，页面不用再轮询。"""
        payload = {
            "status": "ok" if ok else "error",
            "ok": bool(ok),
            "message": str(message or ""),
            "editor_status": getattr(self, "_editor_status_text", "") or "",
        }
        try:
            from astrbot.api.web import json_response
        except Exception:  # pragma: no cover
            return payload
        return json_response(payload)

    def _editor_api_error(self, message: str):
        """错误响应：用 AstrBot 约定的 error 信封。"""
        try:
            from astrbot.api.web import error_response
        except Exception:  # pragma: no cover
            return {"status": "error", "message": str(message or "")}
        return error_response(str(message or ""))


class EditorBridgeMixin(EditorApiMixin):
    """编辑器页面的数据通道（由 FishingPlugin 继承，见 main.py 的类定义）。

    读写都走**插件自己注册的 Web API**（见文件末尾的 EditorApiMixin）：
    旧版「页面上传文件 + 插件轮询」在真实环境下会被
    ``403 /api/files: Insufficient API key scope`` 挡掉，已废弃。
    """

    # =====================================================================
    # 生命周期
    # =====================================================================
    def start_editor_bridge(self) -> None:
        """注册编辑器页面的 Web API（重复调用安全；任何异常都不影响插件启动）。

        页面用相对路径调用（``config`` / ``snapshot``），Dashboard 会转发到
        ``/api/v1/plugins/extensions/<插件名>/<endpoint>``；路由必须带插件名前缀，
        否则匹配不到（用户此前看到的「未找到该路由」就是这个原因）。
        """
        try:
            self._editor_status_text = getattr(self, "_editor_status_text", "") or ""
            context = getattr(self, "context", None)
            register = getattr(context, "register_web_api", None)
            if not callable(register):
                _log_warning(
                    "当前 AstrBot 不支持 register_web_api：编辑器页面只能只读预览"
                )
                return
            routes: list[str] = []
            for name in self._editor_plugin_names():
                for route, handler, methods, desc in self._editor_route_specs():
                    full = route.format(plugin=name)
                    try:
                        register(full, handler, list(methods), desc)
                        routes.append(f"{full} [{'/'.join(methods)}]")
                    except Exception as e:
                        _log_warning(f"注册编辑器路由失败（{full}）：{e}")
            self._editor_registered_routes = routes
            _log_info("数据编辑器通道已就绪（插件 Web API）：" + "、".join(routes))
        except Exception as e:  # pragma: no cover - 绝不能让插件启动失败
            _log_warning(f"数据编辑器通道启动失败（页面保存将不可用）：{e}")

    async def stop_editor_bridge(self) -> None:
        """停用/重载插件时把自己注册的路由摘掉（AstrBot 没有提供反注册 API）。"""
        try:
            raw = getattr(self, "_editor_registered_routes", None) or []
            routes = {str(item).split(" [")[0] for item in raw}
            context = getattr(self, "context", None)
            table = getattr(context, "registered_web_apis", None)
            if routes and isinstance(table, list):
                table[:] = [
                    item
                    for item in table
                    if not (
                        isinstance(item, (tuple, list))
                        and item
                        and str(item[0]) in routes
                    )
                ]
            self._editor_registered_routes = []
        except Exception as e:  # pragma: no cover
            _log_warning(f"数据编辑器通道停止失败（忽略）：{e}")

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
        """删除一份存档。

        注意：**删除不建快照**。删档只是删掉一个备份文件，不动任何玩家数据，
        所以「删除前自动存档」纯属多余 —— 更糟的是删一份会立刻冒出一份新的，
        站长永远清不干净（历史 bug：manual 里因此攒了 55 份）。
        真正会改玩家数据的「恢复」才建快照，那份是有意义的。
        """
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
        if not store.delete_snapshot(target):
            return False, f"删除「{target}」失败（文件可能被占用）"
        return True, f"已删除存档「{target}」"

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
            "keep_manual": int(self._editor_cfg_get("backup_keep_manual", 0) or 0),
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
            "transport": "plugin-api",
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
