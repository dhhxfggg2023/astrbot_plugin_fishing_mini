# -*- coding: utf-8 -*-
"""存档与数据管理（纯标准库，可单独测试）。

=============================== 目录约定 ===============================

所有存档都放在**插件目录**下的 ``backups/``（可在插件配置里改 ``backup_dir``）：

    <插件目录>/backups/
        README.md                      存档说明（本模块自动生成/更新）
        index.json                     快照清单（网页面板读它）
        daily/2026-09-17.json          每日存档：一天一份，绝不覆盖
        daily/2026-09-17_043012.json   同一天第二次执行会带时间后缀
        auto/2026-09-17_0600.json      按时存档（每 N 小时一份）
        manual/20260917-213000.json    手动存档（配置里点「立即存档」）
        players/<用户ID>.json          单个玩家导出（可直接发回给玩家）
        exported/<用户ID>.json         最近一次导出的副本（给网页面板下载用）

=============================== 数据封装 ===============================

每个玩家的存档都包一层信封，自带版本与时间，换版本/换机器都能认出来：

    {
      "__fishing_player__": 1,     # 信封版本
      "user_id": "123456",
      "data_version": 5,           # 玩家数据结构版本
      "saved_at": 1789650000,
      "saved_text": "2026-09-18 04:00:00",
      "data": { ...玩家数据... }
    }

读档时兼容两种形态：没有信封的老数据（裸 dict）照常读入，
下次保存时自动升级成信封格式 —— 内测期间数据不会因为格式变化丢失。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

#: 信封标记与版本
ENVELOPE_KEY = "__fishing_player__"
ENVELOPE_VERSION = 1
#: 快照文件标记与版本
SNAPSHOT_KEY = "__fishing_backup__"
SNAPSHOT_VERSION = 1

KIND_DAILY = "daily"
KIND_AUTO = "auto"
KIND_MANUAL = "manual"
KIND_NAMES = {KIND_DAILY: "每日存档", KIND_AUTO: "按时存档", KIND_MANUAL: "手动存档"}

README_TEXT = """# 存档目录说明（群钓鱼插件）

这个目录由 **群钓鱼插件**自动维护，别手动删里面的文件（要删也先备份一份）。

## 目录结构

| 路径 | 是什么 | 覆盖规则 |
| --- | --- | --- |
| `daily/YYYY-MM-DD.json` | **每日存档**：当天 0 点后第一次自动存档 | 一天一份，**永不覆盖**；同一天再存会加时间后缀 |
| `auto/YYYY-MM-DD_HHMM.json` | **按时存档**：每 N 小时一份（配置 `backup_interval_hours`） | 每份独立；超过保留份数才删最旧的 |
| `manual/*.json` | **手动存档**：配置里选「立即存档」生成 | 每份独立，不自动删除 |
| `players/<用户ID>.json` | **单个玩家导出**：可直接发给玩家或换机器导入 | 同一玩家会覆盖（只留最新） |
| `exported/<用户ID>.json` | 导出副本，供网页面板/插件配置下载 | 覆盖 |
| `index.json` | 快照清单（时间、类型、玩家数、大小） | 每次存档后重写 |

## 怎么用（都在 AstrBot 网页里，不用发指令）

1. **WebUI → 插件 → 群钓鱼 → 配置 → 「数据管理」**：
   - `立即存档` / `导出全部玩家` / `导出单个玩家`
   - `从快照恢复` / `恢复单个玩家`
   - `清除单个玩家` / `清除全部玩家数据`（要勾「我已确认」）
2. **上传导入**：把存档 JSON 拖到「上传存档文件（导入用）」那一项，保存后在
   `data_action` 里选「导入上传的存档」。
3. **下载导出**：导出结果会出现在「导出存档文件（下载用）」那一项里，点文件名就能下载。
4. **自动存档**：`enable_auto_backup` 打开后，插件会按时存档（默认每 6 小时）
   并在每天 `backup_daily_hour` 点后生成当日存档。

## 恢复要注意什么

- 恢复是**整条玩家记录覆盖**：先自动做一次「恢复前快照」，所以恢复错了还能再倒回来。
- 快照里的玩家 ID 就是当时的用户 ID；换平台（QQ 号 → openid）不会自动对应。
- 数据版本 (`data_version`) 会在读档时自动迁移；跨大版本恢复的玩家数据也能读。
"""


def _text(ts: float | int | None = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.fromtimestamp(float(ts if ts is not None else time.time())).strftime(fmt)


def wrap_player(
    user_id: str, data: dict[str, Any], data_version: int, now: float | None = None
) -> dict[str, Any]:
    """把一个玩家的数据包成信封（自带版本/时间/身份）。"""
    ts = float(now if now is not None else time.time())
    return {
        ENVELOPE_KEY: ENVELOPE_VERSION,
        "user_id": str(user_id),
        "data_version": int(data_version),
        "saved_at": int(ts),
        "saved_text": _text(ts),
        "data": data,
    }


def unwrap_player(raw: Any) -> tuple[dict[str, Any] | None, bool]:
    """拆信封：返回 (玩家数据, 是否是信封格式)。

    兼容三种情况：
    - 新格式（带信封）→ 取出 ``data``
    - 老格式（裸 dict）→ 原样返回，``False``
    - 脏数据 → ``(None, False)``
    """
    if not isinstance(raw, dict):
        return None, False
    if ENVELOPE_KEY in raw:
        data = raw.get("data")
        if isinstance(data, dict):
            return data, True
        return None, True
    return raw, False


def envelope_meta(raw: Any) -> dict[str, Any]:
    """只看信封头（不解包），用于列表展示。"""
    if not isinstance(raw, dict) or ENVELOPE_KEY not in raw:
        return {}
    return {
        "user_id": raw.get("user_id", ""),
        "data_version": raw.get("data_version", 0),
        "saved_at": raw.get("saved_at", 0),
        "saved_text": raw.get("saved_text", ""),
    }


class BackupStore:
    """存档仓库：负责所有落盘动作（不含任何 AstrBot 依赖，方便单测）。"""

    def __init__(self, root: str | os.PathLike[str], data_version: int = 1) -> None:
        self.root = Path(root)
        self.data_version = int(data_version)

    # ---------------------------------------------------------------- 目录
    def path_of(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def ensure_layout(self) -> None:
        """建目录 + 写说明文件（说明文件内容变了会更新，其它文件不动）。"""
        self.root.mkdir(parents=True, exist_ok=True)
        for kind in KIND_NAMES:
            self.path_of(kind).mkdir(parents=True, exist_ok=True)
        self.path_of("players").mkdir(parents=True, exist_ok=True)
        self.path_of("exported").mkdir(parents=True, exist_ok=True)
        readme = self.path_of("README.md")
        try:
            if not readme.exists() or readme.read_text(encoding="utf-8") != README_TEXT:
                readme.write_text(README_TEXT, encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ 写快照
    def snapshot_name(self, kind: str, now: float | None = None) -> str:
        """快照文件名：同一天/同一分钟也不覆盖（自动加后缀）。"""
        ts = float(now if now is not None else time.time())
        stamp = _text(ts, "%Y-%m-%d") if kind == KIND_DAILY else _text(ts, "%Y-%m-%d_%H%M")
        base = f"{stamp}.json"
        target = self.path_of(kind, base)
        if not target.exists():
            return base
        for index in range(1, 100):
            candidate = f"{stamp}_{index:02d}.json"
            if not self.path_of(kind, candidate).exists():
                return candidate
        return f"{stamp}_{int(ts)}.json"

    def write_snapshot(
        self,
        kind: str,
        players: dict[str, Any],
        *,
        note: str = "",
        now: float | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """写一份快照（players 可以是信封，也可以是裸数据，写之前统一包信封）。"""
        self.ensure_layout()
        ts = float(now if now is not None else time.time())
        wrapped: dict[str, Any] = {}
        for user_id, value in players.items():
            data, _ = unwrap_player(value)
            if data is None:
                continue
            wrapped[str(user_id)] = (
                value if isinstance(value, dict) and ENVELOPE_KEY in value
                else wrap_player(str(user_id), data, self.data_version, ts)
            )
        payload = {
            SNAPSHOT_KEY: SNAPSHOT_VERSION,
            "kind": kind,
            "kind_name": KIND_NAMES.get(kind, kind),
            "created_at": int(ts),
            "created_text": _text(ts),
            "data_version": self.data_version,
            "note": note,
            "count": len(wrapped),
            "players": wrapped,
        }
        name = self.snapshot_name(kind, ts)
        path = self.path_of(kind, name)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        # 只保留最近 N 份由调用方决定：这里只更新清单
        self.rebuild_index()
        return path, payload

    # ------------------------------------------------------------ 读快照
    def list_snapshots(self) -> list[dict[str, Any]]:
        """列出所有快照（新到旧）。"""
        result: list[dict[str, Any]] = []
        for kind in KIND_NAMES:
            folder = self.path_of(kind)
            if not folder.is_dir():
                continue
            for path in folder.glob("*.json"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                info: dict[str, Any] = {
                    "kind": kind,
                    "kind_name": KIND_NAMES[kind],
                    "name": path.name,
                    "rel": f"{kind}/{path.name}",
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "mtime_text": _text(stat.st_mtime),
                    "count": 0,
                    "note": "",
                }
                try:
                    head = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(head, dict):
                        info["count"] = int(head.get("count") or 0)
                        info["note"] = str(head.get("note") or "")
                        info["created_text"] = str(head.get("created_text") or "")
                except (OSError, ValueError):
                    pass
                result.append(info)
        result.sort(key=lambda item: item["mtime"], reverse=True)
        return result

    def find_snapshot(self, name: str) -> Path | None:
        """按文件名/相对路径/latest 找快照。"""
        wanted = (name or "").strip()
        if not wanted or wanted.lower() in ("latest", "最新", "最近"):
            items = self.list_snapshots()
            if not items:
                return None
            return self.path_of(*items[0]["rel"].split("/"))
        candidate = self.path_of(*wanted.replace("\\", "/").split("/"))
        if candidate.is_file():
            return candidate
        for kind in KIND_NAMES:
            path = self.path_of(kind, wanted)
            if path.is_file():
                return path
        return None

    def load_snapshot(self, name: str) -> dict[str, Any] | None:
        path = self.find_snapshot(name)
        if path is None:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        players = data.get("players")
        if not isinstance(players, dict):
            # 兼容「单个玩家文件」：整个文件就是一个信封
            player, _ = unwrap_player(data)
            if player is None:
                return None
            uid = str(data.get("user_id") or "")
            return {
                "kind": "single",
                "created_text": str(data.get("saved_text") or ""),
                "count": 1 if uid else 0,
                "players": {uid: data} if uid else {},
                "path": str(path),
            }
        data["path"] = str(path)
        return data

    # ---------------------------------------------------------- 单玩家文件
    def export_player(self, user_id: str, value: Any, now: float | None = None) -> Path | None:
        """导出单个玩家到 players/ 与 exported/（后者给面板下载）。"""
        data, _ = unwrap_player(value)
        if data is None:
            return None
        envelope = (
            value
            if isinstance(value, dict) and ENVELOPE_KEY in value
            else wrap_player(user_id, data, self.data_version, now)
        )
        safe = "".join(ch for ch in str(user_id) if ch.isalnum() or ch in "-_") or "player"
        self.ensure_layout()
        text = json.dumps(envelope, ensure_ascii=False, indent=1)
        for folder in ("players", "exported"):
            try:
                self.path_of(folder, f"{safe}.json").write_text(text, encoding="utf-8")
            except OSError:
                continue
        return self.path_of("players", f"{safe}.json")

    def list_exported(self) -> list[dict[str, Any]]:
        folder = self.path_of("players")
        result: list[dict[str, Any]] = []
        if not folder.is_dir():
            return result
        for path in folder.glob("*.json"):
            try:
                stat = path.stat()
            except OSError:
                continue
            result.append(
                {
                    "user_id": path.stem,
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "mtime_text": _text(stat.st_mtime),
                }
            )
        result.sort(key=lambda item: item["mtime"], reverse=True)
        return result

    def read_player_file(self, user_id: str) -> dict[str, Any] | None:
        safe = "".join(ch for ch in str(user_id) if ch.isalnum() or ch in "-_")
        for folder in ("players", "exported"):
            path = self.path_of(folder, f"{safe}.json")
            if path.is_file():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return None
        return None

    # ------------------------------------------------------ 改名 / 删除
    def rename_snapshot(self, name: str, note: str) -> bool:
        """给快照改备注（只动 JSON 里的 ``note``，**不重命名文件**）。

        为什么不重命名文件：网页面板与「最新快照」判定都按 ``mtime`` 排序，
        跟着文件名走的相对路径（``manual/xxx.json``）也被配置项引用着，
        重命名会让「最新快照」的语义漂移、还可能留下断掉的引用。
        所以这里只改内容，顺带刷新 ``index.json`` 让面板立刻看到新备注。

        Args:
            name: 快照名（文件名 / ``kind/文件名`` / ``latest``）。
            note: 新的备注文本（空串 = 清空备注）。

        Returns:
            bool: 找到并写成功 ``True``；找不到快照、文件坏掉或写盘失败 ``False``。
        """
        try:
            path = self.find_snapshot(name)
            if path is None:
                return False
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            if not isinstance(data, dict):
                return False
            data["note"] = str(note if note is not None else "")
            try:
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
                )
            except OSError:
                return False
            self.rebuild_index()
            return True
        except Exception:  # pragma: no cover - 兜底：坏快照不该拖垮调用方
            return False

    def rewrite_snapshot(self, name: str, payload: dict[str, Any]) -> bool:
        """把改好的快照**原地重写**回去（原子替换，只动这一份文件）。

        用途：编辑器页面的「改存档里某个玩家的金币」——改完要等「恢复」才生效，
        所以不能碰实时数据。写盘用「临时文件 + ``os.replace``」，中途断电/出错
        也不会留下半截 JSON 把存档写坏。

        安全边界：目标文件必须真的在存档根目录里（挡住 ``..`` 之类的越界写法）。

        Args:
            name: 快照名（文件名 / ``kind/文件名`` / ``latest``）。
            payload: 完整快照内容（一般是 ``load_snapshot`` 读出来的那份改过之后）。

        Returns:
            bool: 写成功 ``True``；找不到、越界、写盘失败 ``False``。
        """
        try:
            path = self.find_snapshot(name)
            if path is None or not path.is_file():
                return False
            root = self.root.resolve()
            target = path.resolve()
            if target != root and root not in target.parents:
                return False
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            os.replace(tmp, path)
            self.rebuild_index()
            return True
        except Exception:  # pragma: no cover - 兜底：坏存档不该拖垮调用方
            return False

    def delete_snapshot(self, name: str) -> bool:
        """删除一份快照（**幂等**：不存在就返回 ``False``，不抛异常）。

        Args:
            name: 快照名（文件名 / ``kind/文件名`` / ``latest``）。
                  指向 ``players/``、``exported/`` 这类单玩家文件时也会删掉，
                  因为 ``find_snapshot`` 认它们。

        Returns:
            bool: 真的删掉了 ``True``；本来就不存在 / 删不掉 ``False``。
        """
        try:
            path = self.find_snapshot(name)
            if path is None:
                return False
            if not path.is_file():
                return False
            try:
                path.unlink()
            except OSError:
                return False
            self.rebuild_index()
            return True
        except Exception:  # pragma: no cover - 兜底：删除失败不该拖垮调用方
            return False

    # ------------------------------------------------------------ 清理
    def prune(self, kind: str, keep: int) -> list[str]:
        """只保留最新的 keep 份（0 = 不清理），返回被删掉的文件名。"""
        if keep <= 0:
            return []
        items = [item for item in self.list_snapshots() if item["kind"] == kind]
        removed: list[str] = []
        for item in items[keep:]:
            path = self.path_of(*item["rel"].split("/"))
            try:
                path.unlink()
                removed.append(item["name"])
            except OSError:
                continue
        if removed:
            self.rebuild_index()
        return removed

    def prune_daily_days(self, keep_days: int) -> list[str]:
        """每日存档按天数清理（0 = 永久保留）。"""
        if keep_days <= 0:
            return []
        cutoff = time.time() - keep_days * 86400
        removed: list[str] = []
        for item in self.list_snapshots():
            if item["kind"] != KIND_DAILY or item["mtime"] >= cutoff:
                continue
            path = self.path_of(*item["rel"].split("/"))
            try:
                path.unlink()
                removed.append(item["name"])
            except OSError:
                continue
        if removed:
            self.rebuild_index()
        return removed

    # ------------------------------------------------------------ 清单
    def rebuild_index(self) -> dict[str, Any]:
        """重写 index.json（网页面板/排查都读它）。"""
        items = self.list_snapshots()
        payload = {
            SNAPSHOT_KEY: SNAPSHOT_VERSION,
            "updated_at": int(time.time()),
            "updated_text": _text(),
            "data_version": self.data_version,
            "root": str(self.root),
            "total": len(items),
            "by_kind": {
                kind: len([x for x in items if x["kind"] == kind]) for kind in KIND_NAMES
            },
            "snapshots": items[:200],
            "players": self.list_exported()[:200],
        }
        try:
            self.path_of("index.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            pass
        return payload

    def total_size(self) -> int:
        total = 0
        if not self.root.is_dir():
            return 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def copy_into(self, source: str | os.PathLike[str], kind: str, name: str) -> Path | None:
        """把外部文件拷进存档目录（导入前留一份原始文件）。"""
        src = Path(source)
        if not src.is_file():
            return None
        self.ensure_layout()
        target = self.path_of(kind, name)
        try:
            shutil.copyfile(src, target)
        except OSError:
            return None
        self.rebuild_index()
        return target
