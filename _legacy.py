"""旧作用域数据找回（v1.18.1）。

**为什么需要它**：AstrBot 的插件数据是存在主库 ``data_v4.db`` 的 ``preferences``
表里的（``scope='plugin' / scope_id=<插件id> / key=<键>``）。插件作者名改过一次
（``dhhxfggg`` → ``dhhxfggg2023``），``plugin_id`` 跟着变，于是**老存档留在了旧的
scope_id 里**：新作用域读不到，表现就是「那几名玩家的进度突然从头开始」。

这个模块**只读**老作用域（用只读方式打开 SQLite 主库），把新作用域里缺的玩家
通过官方 KV 接口补进来；老数据一行都不改、不删（想清理请自己确认后再动手）。

设计上刻意把「读库」做成纯函数 ``read_legacy_rows()``，方便单测拿一个临时库验证，
不用碰站长的真实数据。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from typing import Any

#: 上一次读库失败的原因（给页面用：读不到时要能说清「为什么没找到」，而不是装没事）。
#: 成功读完（哪怕一条都没有）就清空。
LAST_ERROR = ""


def _log_warning(text: str) -> None:
    """写一条警告日志（``logger`` 由 main 注入；拿不到就静默，绝不 print）。"""
    log = globals().get("logger")
    if log is None:
        return
    try:
        log.warning(text)
    except Exception:  # pragma: no cover - 日志本身出错不该影响找回功能
        pass


def astrbot_db_path() -> str:
    """AstrBot 主库路径：优先读它自己的配置常量，拿不到再按 ASTRBOT_ROOT 猜。"""
    try:
        from astrbot.core.config.default import DB_PATH

        return str(DB_PATH)
    except Exception:
        root = os.environ.get("ASTRBOT_ROOT") or os.getcwd()
        return os.path.join(root, "data", "data_v4.db")


def plugin_name_of(plugin_id: str) -> str:
    """``作者/插件名`` -> ``插件名``（作用域尾巴，用来找同名的旧作用域）。"""
    return str(plugin_id or "").split("/")[-1]


def unwrap_kv(raw: Any) -> Any:
    """AstrBot 的 KV 值外面包了一层 ``{"val": ...}``；也兼容没包的情况。

    ⚠️ 真实库里 ``val`` 常常是**一个 JSON 字符串**（不是嵌套对象），见
    ``parse_player_value()``：所以拿到字符串必须再 ``json.loads`` 一次。
    """
    if isinstance(raw, dict) and set(raw) == {"val"}:
        return raw["val"]
    return raw


def parse_player_value(value: Any) -> dict[str, Any]:
    """把一条 ``player_*`` 的值解析成**玩家数据 dict**（三种真实形态都认）。

    真库里实际见过：

    * ``{"val": "<JSON 字符串>"}``，字符串里是**信封**（``__fishing_player__`` + ``data``）
      —— 当前作用域就是这样
    * ``{"val": "<JSON 字符串>"}``，字符串里是**裸玩家 dict**（老版本，``data_version: 3``，
      没有信封）—— 旧作用域里的就是这种
    * 已经解好的 dict（测试或别的写入方）

    解析不出来（不是 JSON / 不是 dict）返回 ``{}`` —— 调用方按「读不出内容」处理。
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    if not isinstance(value, dict):
        return {}
    data = value.get("data")
    if PLAYER_ENVELOPE in value and isinstance(data, dict):
        return data
    return value


def normalize_player_value(value: Any, uid: str) -> Any:
    """把老作用域里的值整理成「可以直接写进当前作用域」的东西。

    * JSON 字符串 -> 先解开（否则页面/存档那条只认 dict 的链路会看不见这名玩家）
    * 裸玩家 dict -> 尽量包成**信封**（``BACKUP_MODULE`` 由 main 注入；拿不到就写裸 dict，
      当前代码本来就兼容裸 dict，读档时会自动迁移）
    * 已经是信封 / 解析不出来 -> 原样返回（宁可原样搬，也不丢数据）
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return value
    if not isinstance(value, dict) or PLAYER_ENVELOPE in value:
        return value
    wrapper = globals().get("BACKUP_MODULE")
    if wrapper is not None and hasattr(wrapper, "wrap_player"):
        try:
            return wrapper.wrap_player(uid, value, int(globals().get("DATA_VERSION") or 1))
        except Exception as e:  # pragma: no cover - 包信封失败不影响搬运
            _log_warning(f"找回旧数据：给 {uid} 包信封失败（按裸数据写）：{e}")
    return value


def normalize_uid(uid: str) -> str:
    """清掉「@某人」形态的用户 id。

    真库里就有 ``player_<@A1B2C3D4...>`` 这种键（某次把消息里的 at 段当成了 uid）：
    直接搬进来只会多一条永远读不到的废数据，所以取里面的干净 id。
    """
    text = str(uid or "").strip()
    if text.startswith("<@") and text.endswith(">"):
        text = text[2:-1].lstrip("!").strip()
    return text


def read_legacy_rows(db_path: str, plugin_id: str) -> list[dict[str, Any]]:
    """同步只读：返回**其它作用域**里属于同一个插件的 ``(scope_id, key, value)``。

    打不开库（路径不对 / 被锁 / 权限）时返回空表并把原因记在 ``LAST_ERROR``
    —— 调用方按「没找到」处理，同时能把「为什么没找到」告诉站长，不静默失败。
    """
    global LAST_ERROR
    LAST_ERROR = ""
    name = plugin_name_of(plugin_id)
    if not name:
        LAST_ERROR = "插件 id 是空的，读不到作用域信息"
        return []
    if not db_path:
        LAST_ERROR = "拿不到 AstrBot 数据库路径"
        return []
    if not os.path.isfile(db_path):
        LAST_ERROR = f"数据库文件不存在：{db_path}"
        return []
    rows: list[tuple[str, str, str]] = []
    try:
        con = sqlite3.connect(
            "file:{}?mode=ro".format(str(db_path).replace("\\", "/")),
            uri=True,
            timeout=10,
        )
        try:
            rows = con.execute(
                "select scope_id, key, value from preferences "
                "where scope='plugin' and scope_id like ? and scope_id != ?",
                ("%/" + name, str(plugin_id)),
            ).fetchall()
        finally:
            con.close()
    except Exception as e:
        LAST_ERROR = f"打不开数据库（只读）：{e}"
        _log_warning(f"找回旧数据：打不开数据库 {db_path}：{e}")
        return []

    out: list[dict[str, Any]] = []
    for scope_id, key, value in rows:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            continue        # 不是 JSON 的行跳过（不是我们的数据）
        out.append(
            {"scope_id": str(scope_id), "key": str(key), "value": unwrap_kv(parsed)}
        )
    return out


def player_summary(row: dict[str, Any]) -> dict[str, Any]:
    """从一条 ``player_*`` 记录里摘出给站长看的摘要。"""
    raw_uid = str(row.get("key", "")).replace("player_", "", 1)
    uid = normalize_uid(raw_uid)
    data = parse_player_value(row.get("value"))
    return {
        "key": row.get("key", ""),
        "user_id": uid,
        # 键名不干净（``<@...>``）时告诉页面：导入会取干净 ID
        "uid_fixed": uid != raw_uid,
        "gold": int(data.get("gold") or 0),
        "caught": int(data.get("total_caught") or 0),
        "locations": len(data.get("locations") or []),
        "inventory": len(data.get("inventory") or []),
        "aquarium": len(data.get("aquarium") or []),
    }


def group_by_scope(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按 scope_id 分组（调用方自己决定顺序）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("scope_id") or ""), []).append(row)
    return grouped


def as_id_list(raw: Any) -> list[str]:
    """``player_index`` 的两种历史写法都要认：JSON 字符串（插件写的）/ 数组。"""
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if str(x).strip()]


#: 扫描时最多逐个核对多少条老记录（每条要查一次当前作用域的 KV）。
#: 超过就只报「有多少条」，缺谁交给导入时逐条判断 —— 避免一次点击打出上万次读库。
PRESENCE_CHECK_MAX = 2000

#: 玩家数据的信封标记（``_backup.ENVELOPE_KEY``，这里写死一份避免循环依赖）
PLAYER_ENVELOPE = "__fishing_player__"


class LegacyDataMixin:
    """给插件用的两个动作：``legacy_scan``（只看）/ ``legacy_import``（导入）。"""

    async def _legacy_rows(self) -> list[dict[str, Any]]:
        """读老作用域（放线程里跑，别卡住事件循环）。"""
        return await asyncio.to_thread(
            read_legacy_rows, astrbot_db_path(), str(self.plugin_id or "")
        )

    async def _legacy_exists(self, key: str) -> bool | None:
        """当前作用域里有没有这个键（读不到就返回 ``None`` = 不知道）。"""
        try:
            return await self.get_kv_data(key, None) is not None  # type: ignore[attr-defined]
        except Exception:
            return None

    async def legacy_scan(self) -> dict[str, Any]:
        """体检：列出「同名插件、别的 scope」里有哪些数据（**只读，不改任何东西**）。

        顺便逐个查一下当前作用域里是否已经有这名玩家（``present``），页面据此
        只把真正缺的挑出来，站长一眼看得出「并进来会多出谁」。
        老数据特别多（超过 ``PRESENCE_CHECK_MAX`` 行）时**不逐个核对**，
        ``present`` 一律给 ``None``（不知道）—— 缺谁交给导入时逐条判断。
        """
        rows = await self._legacy_rows()
        grouped = group_by_scope(rows)
        check_present = len(rows) <= PRESENCE_CHECK_MAX
        scopes: list[dict[str, Any]] = []
        flat: list[dict[str, Any]] = []
        for scope_id, items in grouped.items():
            players = [player_summary(r) for r in items if r["key"].startswith("player_")
                       and r["key"] != "player_index"]
            for p in players:
                p["scope_id"] = scope_id
                # 查的是**落地时会用的那个键**（脏键 ``player_<@x>`` 会先洗干净），
                # 否则导完了页面还一直显示「缺这名玩家」
                p["present"] = (
                    await self._legacy_exists("player_" + str(p["user_id"]))
                    if check_present else None
                )
            players.sort(key=lambda p: (bool(p.get("present")), -int(p.get("caught") or 0)))
            flat.extend(players)
            scopes.append(
                {
                    "scope_id": scope_id,
                    "rows": len(items),
                    "players": players,
                    "missing": sum(1 for p in players if p.get("present") is False),
                    "other_keys": sorted(
                        r["key"] for r in items
                        # player_index 不是玩家，但要露出来给站长看（导入时会合并它）
                        if not r["key"].startswith("player_") or r["key"] == "player_index"
                    ),
                }
            )
        scopes.sort(key=lambda s: s["rows"], reverse=True)
        return {
            "db_path": astrbot_db_path(),
            "current_scope": str(self.plugin_id or ""),
            "scopes": scopes,
            "players": flat,
            "players_found": len(flat),
            "players_missing": sum(1 for p in flat if p.get("present") is False),
            "presence_checked": check_present,
            # 读库失败的原因（没有失败就是空串）；页面照实显示，不装没事
            "error": LAST_ERROR,
        }

    async def legacy_import(self, scope_ids: list[str] | None = None) -> dict[str, Any]:
        """把老作用域里**新作用域没有的**玩家补进来。

        * 只补 ``player_*``：新作用域已有同名键的一律跳过（当前数据优先）
        * 键名与内容都先「洗干净」：``player_<@xxx>`` -> ``player_xxx``；
          值是 JSON 字符串就解开、裸 dict 就包成信封（见 ``normalize_player_value``）
        * ``player_index`` 做合并（否则页面列不出新导入的玩家）
        * 导入前先存一份手动快照；老作用域**不改不删**
        """
        rows = await self._legacy_rows()
        want = {str(x) for x in (scope_ids or []) if str(x).strip()}
        if want:
            rows = [r for r in rows if r["scope_id"] in want]

        imported: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        snapshot_note = ""
        for row in rows:
            key = str(row["key"])
            if not key.startswith("player_") or key == "player_index":
                continue
            uid = normalize_uid(key.replace("player_", "", 1))
            if not uid:
                failed.append(key)
                continue
            target = "player_" + uid
            try:
                existing = await self.get_kv_data(target, None)
            except Exception:
                existing = None
            if existing is not None:
                skipped.append(uid)
                continue
            if not snapshot_note:
                # 第一次真要写入前先留底：这份快照就是「旧的先留一份」
                try:
                    snapshot_note = await self._snapshot(   # type: ignore[attr-defined]
                        "manual", "找回旧作用域数据前的存档"
                    )
                except Exception as e:
                    snapshot_note = f"（快照失败：{e}）"
            try:
                await self.put_kv_data(target, normalize_player_value(row["value"], uid))
                imported.append(uid)
            except Exception:
                failed.append(uid)

        # 合并玩家索引：新索引 = 旧索引 ∪ 导入的 id（原格式原样写回，别把别人的写法改掉）
        index_note = ""
        if imported:
            try:
                idx = await self.get_kv_data("player_index", None)  # type: ignore[attr-defined]
                ids = as_id_list(idx)
                added = [x for x in imported if x not in ids]
                if added:
                    merged = ids + added
                    payload: Any = merged if isinstance(idx, list) else json.dumps(
                        merged, ensure_ascii=False
                    )
                    await self.put_kv_data("player_index", payload)  # type: ignore[attr-defined]
                index_note = f"（索引 +{len(added)}）"
            except Exception as e:
                index_note = f"（索引更新失败：{e}）"

        return {
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "snapshot": snapshot_note,
            "index_note": index_note,
        }
