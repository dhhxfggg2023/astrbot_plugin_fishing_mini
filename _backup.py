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

import hashlib
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

#: ``index.json`` 里「每次生成都会变」的字段：比较内容有没有变时忽略它们
#: （它们照样写进文件，索引的对外结构一个字段都没少，见 ``rebuild_index``）
INDEX_VOLATILE_KEYS = ("updated_at", "updated_text")

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


def _log_warning(text: str) -> None:
    """写一条警告日志（logger 由 main 注入；拿不到就静默）。

    本模块走的是「纯标准库、可单独导入测试」的路子，所以不 import astrbot 的
    logger，也不 print：注入过 ``logger`` 就写日志，没注入就安静降级。
    """
    log = globals().get("logger")
    if log is None:
        return
    try:
        log.warning(text)
    except Exception:  # pragma: no cover - 日志本身出错不该影响存档
        pass


def _log_debug(text: str) -> None:
    """写一条调试日志（logger 由 main 注入；拿不到就静默）。"""
    log = globals().get("logger")
    if log is None:
        return
    try:
        log.debug(text)
    except Exception:  # pragma: no cover - 同上
        pass


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


def _index_stamps(ts: float | None = None) -> tuple[int, str]:
    """``index.json`` 的两个时间字段（同一个时刻算出来，免得跨秒对不上）。"""
    stamp = float(ts if ts is not None else time.time())
    return int(stamp), _text(stamp)


def _index_semantic(payload: Any) -> Any:
    """取出索引里「有意义」的部分：去掉每次生成都会变的时间戳字段。

    只用于**比较内容有没有变**，不会写进文件 —— ``index.json`` 的对外结构
    （含 ``updated_at`` / ``updated_text``）一个字段都没动。
    """
    if not isinstance(payload, dict):
        return None
    return {
        key: value for key, value in payload.items() if key not in INDEX_VOLATILE_KEYS
    }


def _content_digest(value: Any) -> str:
    """内容摘要（sha1）：进程内判断「盘上还是上次那份清单」用。"""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - 正常内容不会序列化失败
        return ""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


_TMP_SUFFIX = ".tmp"


def _sweep_stale_tmp(path: Path) -> None:
    """清掉这个文件旁边「上次崩溃留下」的临时文件。

    命名规则见 `_atomic_write_text`：``<名字>.<pid>.tmp``。**只清已经死掉的
    pid 的**（``os.kill(pid, 0)`` 探测），本进程的临时文件不动 —— 否则
    「写临时文件 -> 清一遍 -> 改名」这段时间里会把自己的活儿删掉。
    """
    for stale in path.parent.glob(f"{path.name}.*{_TMP_SUFFIX}"):
        try:
            pid = int(stale.name[len(path.name) + 1: -len(_TMP_SUFFIX)])
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
            continue          # 那个进程还活着，别动它的临时文件
        except OSError:
            pass              # 进程没了 -> 这是崩溃残留
        try:
            stale.unlink()
        except OSError:
            pass


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写文本：先写同目录临时文件，再 ``os.replace`` 顶上去。

    好处：进程被杀 / 写盘出错时，**已经存在的那份文件一个字节都不会动**
    （不会出现半截 JSON）；本次的临时文件在异常路径上也会删干净。

    ⚠️ 临时文件名带 ``pid`` 唯一化：以前固定叫 ``xxx.json.tmp``，
    插件重载 / 两个进程（或两个线程）同时写同一个文件时会互相踩
    —— 一个把临时文件改名走了，另一个 ``os.replace`` 就报「文件不存在」。
    带上 pid 之后，顺手把「已经死掉的进程」留下的残留扫掉（见 `_sweep_stale_tmp`）。
    """
    _sweep_stale_tmp(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}{_TMP_SUFFIX}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:  # 含 KeyboardInterrupt：先清掉临时文件再往上抛
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class BackupStore:
    """存档仓库：负责所有落盘动作（不含任何 AstrBot 依赖，方便单测）。"""

    def __init__(self, root: str | os.PathLike[str], data_version: int = 1) -> None:
        self.root = Path(root)
        self.data_version = int(data_version)
        #: ``index.json`` 的进程内缓存：上次写进去的内容摘要 + 当时的文件/目录指纹。
        #: 让同一进程里反复调用 ``rebuild_index()`` 几乎零开销（见该方法说明）。
        self._index_digest: str | None = None
        self._index_payload: dict[str, Any] | None = None
        self._index_sig: tuple[int, int] | None = None
        self._scan_sig: tuple[tuple[str, str, int, int], ...] | None = None
        #: 上一次报过的索引错误（同一个错只告警一次，见 ``_warn_index``）
        self._index_error = ""

    # ---------------------------------------------------------------- 目录
    def path_of(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def _contained(self, path: Path) -> bool:
        """这个路径是否真的落在存档根目录里（挡住 ``../`` 之类的越界写法）。

        ⚠️ 必须有：`path_of` 只是拼路径，不校验。任何**外部传入的名字**
        （编辑器页面 / 插件配置里的快照名）都可能是 ``../../cmd_config.json``，
        没有这道检查就能读/改名/删掉存档根目录之外的任意 JSON 文件。
        原来的 ``rewrite_snapshot`` 里已经有了这段判断，这里提出来给所有调用点共用。
        """
        try:
            root = self.root.resolve()
            target = path.resolve()
        except OSError:
            return False
        return target != root and root in target.parents

    def ensure_layout(self) -> None:
        """建目录 + 写说明文件（说明文件内容变了会更新，其它文件不动）。

        ⚠️ 整段容忍 ``OSError``：存档根目录只读 / 盘满 / 被占用时，
        以前这个 ``mkdir`` 会把异常抛给调用方（导出玩家、导入前的备份都没包），
        于是「存档目录不可写」会表现成别处的莫名报错。现在只降级：
        目录建不出来时后续写盘各自失败并被各自捕获，插件照常跑。
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            for kind in KIND_NAMES:
                self.path_of(kind).mkdir(parents=True, exist_ok=True)
            self.path_of("players").mkdir(parents=True, exist_ok=True)
            self.path_of("exported").mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _log_warning(f"存档目录不可用（{self.root}）：{e}")
            return
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
        # 原子写：快照写一半时断电/被杀，以前会留下半截 JSON（读回来直接坏档）
        _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=1))
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
        if candidate.is_file() and self._contained(candidate):
            return candidate
        for kind in KIND_NAMES:
            path = self.path_of(kind, wanted)
            if path.is_file() and self._contained(path):
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
                _atomic_write_text(self.path_of(folder, f"{safe}.json"), text)
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
                _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1))
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
            if not self._contained(path):   # 二次确认（find_snapshot 已经挡过一道）
                return False
            _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=1))
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
    def _index_file_sig(self) -> tuple[int, int] | None:
        """``index.json`` 的轻量指纹（修改时间 + 大小）；读不到算 ``None``。"""
        try:
            stat = self.path_of("index.json").stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _scan_signature(self) -> tuple[tuple[str, str, int, int], ...]:
        """存档目录的清单指纹：只看文件名/大小/修改时间，**不读文件内容**。

        比 ``list_snapshots()`` 便宜得多（那一版要把每个快照 JSON 读进来解析），
        用来判断「目录自上次调用后有没有动过」。
        """
        parts: list[tuple[str, str, int, int]] = []
        for folder in (*KIND_NAMES, "players"):
            try:
                with os.scandir(self.path_of(folder)) as entries:
                    for entry in entries:
                        if not entry.name.lower().endswith(".json"):
                            continue
                        try:
                            info = entry.stat()
                        except OSError:
                            continue
                        parts.append(
                            (folder, entry.name, info.st_size, info.st_mtime_ns)
                        )
            except OSError:  # 目录还没建：当空目录
                continue
        parts.sort()
        return tuple(parts)

    def _cached_index(self) -> dict[str, Any] | None:
        """进程内缓存命中就返回上次那份清单（不扫盘、不读盘、不写盘）。

        必须同时满足才算命中：上次记下的 ``index.json`` 指纹没变（没被别的进程
        或手动改过），且存档目录的清单指纹没变（没有新存档、没有删/改名/改内容）。
        """
        if self._index_payload is None or self._index_sig is None:
            return None
        if self._index_file_sig() != self._index_sig:
            return None
        if self._scan_signature() != self._scan_sig:
            return None
        return dict(self._index_payload)  # 浅拷贝一份，别让调用方改坏缓存

    def _read_index_file(self) -> tuple[dict[str, Any] | None, tuple[int, int] | None]:
        """读回磁盘上现有的 ``index.json``（不存在/坏掉都算 ``None``，同时给出指纹）。"""
        path = self.path_of("index.json")
        try:
            stat = path.stat()
        except OSError:
            return None, None
        sig = (stat.st_mtime_ns, stat.st_size)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, sig
        return (data if isinstance(data, dict) else None), sig

    def _remember_index(
        self,
        payload: dict[str, Any],
        digest: str,
        sig: tuple[int, int] | None,
        scan_sig: tuple[tuple[str, str, int, int], ...] | None,
    ) -> None:
        """记下「盘上现在就是这份清单」，供下一次调用秒回。

        ``scan_sig`` 用的是**扫盘之前**记下的目录指纹：万一扫盘/写盘期间目录又变了，
        下次调用会指纹不符 → 老老实实重扫一遍，绝不会拿着旧清单糊弄过去。
        """
        self._index_payload = payload
        self._index_digest = digest
        self._index_sig = sig
        self._scan_sig = scan_sig

    def _forget_index(self) -> None:
        """缓存作废（写盘失败时用）：下次调用重新扫盘、重试写入。"""
        self._index_payload = None
        self._index_digest = None
        self._index_sig = None
        self._scan_sig = None

    def _warn_index(self, text: str) -> None:
        """索引相关的告警：同一个错只告警一次（每 60 秒调一次，别刷屏）。"""
        if self._index_error == text:
            _log_debug(text)
            return
        self._index_error = text
        _log_warning(text)

    def _empty_index(self) -> dict[str, Any]:
        """扫盘失败时的降级返回：结构与正常清单一致，数字清零。"""
        updated_at, updated_text = _index_stamps()
        return {
            SNAPSHOT_KEY: SNAPSHOT_VERSION,
            "updated_at": updated_at,
            "updated_text": updated_text,
            "data_version": self.data_version,
            "root": str(self.root),
            "total": 0,
            "by_kind": {kind: 0 for kind in KIND_NAMES},
            "snapshots": [],
            "players": [],
        }

    def _build_index_payload(self) -> dict[str, Any]:
        """扫一遍存档目录，拼出清单内容（不落盘）。"""
        items = self.list_snapshots()
        updated_at, updated_text = _index_stamps()
        return {
            SNAPSHOT_KEY: SNAPSHOT_VERSION,
            "updated_at": updated_at,
            "updated_text": updated_text,
            "data_version": self.data_version,
            "root": str(self.root),
            "total": len(items),
            "by_kind": {
                kind: len([x for x in items if x["kind"] == kind]) for kind in KIND_NAMES
            },
            "snapshots": items[:200],
            "players": self.list_exported()[:200],
        }

    def _write_index_if_changed(
        self,
        payload: dict[str, Any],
        scan_sig: tuple[tuple[str, str, int, int], ...],
    ) -> bool:
        """内容与磁盘上那份语义一致就跳过写入，否则原子替换。返回是否真写了。

        比较口径：只看 ``_index_semantic()``（去掉 ``updated_at`` / ``updated_text``
        这两个每次生成都会变的字段）之后的**完整内容**，与字段顺序、缩进、文件名
        写法都无关 —— 时间戳要是也拿来比，就会永远判定成「变了」。
        """
        semantic = _index_semantic(payload)
        digest = _content_digest(semantic)
        # 同一进程内刚确认过「盘上就是这份内容」，且文件没被动过 → 连读盘都省了
        if (
            self._index_sig is not None
            and digest == self._index_digest
            and self._index_file_sig() == self._index_sig
        ):
            self._remember_index(payload, digest, self._index_sig, scan_sig)
            return False
        disk, sig = self._read_index_file()
        if disk is not None and _index_semantic(disk) == semantic:
            # 换进程后第一次调用也会走到这儿：内容一样就一个字节都不写（mtime 不动）
            self._remember_index(disk, digest, sig, scan_sig)
            return False
        _atomic_write_text(
            self.path_of("index.json"),
            json.dumps(payload, ensure_ascii=False, indent=1),
        )
        self._remember_index(payload, digest, self._index_file_sig(), scan_sig)
        return True

    def rebuild_index(self) -> dict[str, Any]:
        """重建 ``index.json``（网页面板/排查都读它），返回清单内容。

        ``_data_admin._refresh_data_status()`` 每 60 秒就会调一次这里，所以做了三层
        优化（**索引文件的对外结构一个字段都没改**，读它的人不用动）：

        1. **进程内缓存**：上次写进去的内容摘要 + 文件/目录指纹都还在且没变，
           直接返回上次那份（不扫盘、不读盘、不写盘）；
        2. **内容不变不写盘**：重新扫出来的内容与磁盘上那份语义一致时跳过写入，
           所以 ``index.json`` 的 mtime 不会被动；
        3. **原子替换**：真要写时才「同目录临时文件 + ``os.replace``」，
           出错不会留下半截 JSON，也绝不会破坏已有索引。

        异常一律记日志降级返回，绝不抛给调用方。

        Returns:
            dict: 清单内容（结构见 ``_build_index_payload``）。
        """
        # 1) 进程内缓存命中：连扫盘都省了
        try:
            cached = self._cached_index()
        except Exception as e:  # pragma: no cover - 指纹算不出来就按未命中处理
            _log_debug(f"存档清单缓存检查失败（按未命中处理）：{e}")
            cached = None
        if cached is not None:
            return cached

        # 2) 重新扫一份（先记下扫盘前的目录指纹，写完之后它就是缓存指纹）
        try:
            scan_sig = self._scan_signature()
            payload = self._build_index_payload()
        except Exception as e:
            self._warn_index(f"重建存档清单失败（本次不写盘）：{e}")
            self._forget_index()
            return self._empty_index()

        # 3) 内容没变就不写盘；要写就原子替换
        try:
            if self._write_index_if_changed(payload, scan_sig):
                _log_debug("存档清单有变化，已重写 index.json")
        except Exception as e:
            self._warn_index(f"写入存档清单失败（保留原文件）：{e}")
            self._forget_index()
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
