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
* v1.10.0 起多了第四个：GET/POST ``players``（实时玩家列表 / 某份存档里的玩家 /
  改金币 ``set_gold`` / ``snapshot_gold``）。

⚠️ 历史：早先试过「页面上传文件 + 插件轮询」，在真实环境下被
``403 /api/files：Insufficient API key scope`` 挡掉（插件页面的 API key 没有
上传权限），已废弃 —— 不要再回到那条路。

============================== 安全边界 ==============================

* 写操作只认白名单：``save_content`` 只收 ``CONTENT_TABLES`` 里的内容表；
  ``save_numbers`` 只收 ``DEFAULTS`` 里的数值类键（筛掉 ``*_defs / *_slots / *_upgrades`` 与
  ``data_* / backup_* / editor_*``）并逐个类型校验，非法就整批拒绝；
  ``players`` 只允许改 **gold** 一个字段（范围校验 + ``confirm=true`` + 改前自动存档）。
* 未知 action、坏 JSON、非对象请求体 → 返回结构化错误，绝不 500。

⚠️ 维护约定：本模块的方法**不要**对共享的模块级变量做重新赋值
（``X = ...`` 只会改到本模块的副本），要改就原地改（``X.update()`` 等）。
本模块的全局（``logger`` / ``DEFAULTS`` / ``_safe_int`` …）由 main 在模块末尾注入。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import pathlib
import re
import time
from typing import Any


def _effects_module() -> Any:
    """效果注册表模块（v1.13.0）；拿不到就返回 None，页面自动回退内置清单。"""
    import sys

    for name in ("astrbot_fishing_effects", "_effects"):
        module = sys.modules.get(name)
        if module is not None:
            return module
    try:
        import _effects as module
        return module
    except Exception:
        return None

# ---------------------------------------------------------------------------
# 通道常量（页面侧必须与这里保持一致）
# ---------------------------------------------------------------------------

#: 页面调用的「相对插件」endpoint（路由注册时会加上 /<插件名> 前缀）
ENDPOINT_CONFIG = "config"
ENDPOINT_SNAPSHOT = "snapshot"
ENDPOINT_PLAYERS = "players"
ENDPOINT_SCENES = "scenes"

#: 玩家金币的合法范围（防止手滑写出天文数字把经济系统写崩）
PLAYER_GOLD_MAX = 1_000_000_000
#: 一次最多回给页面多少行玩家（列表按金币从高到低；搜索时也受这个上限保护）
PLAYER_LIST_LIMIT = 500
#: 不带搜索词时最多扫描索引里最近多少个玩家（索引最多 5000）
PLAYER_SCAN_LIMIT = 2000
#: 玩家页支持的动作（页面侧必须与这里一致）
PLAYER_ACTIONS: tuple[str, ...] = (
    "list", "snapshot_list", "set_gold", "snapshot_gold",
    # v1.18.53：玩家实时数据的通用编辑（金币之外的增/减/改）
    "player_get", "player_set", "player_add", "snapshot_player_set",
    # v1.18.56：玩家存档编辑器（全字段 + 逐条改鱼 + 原始 JSON）
    "player_full_get", "player_full_set",
)

#: 玩家数据的**可编辑字段白名单**：字段 -> (中文名, 类型, 说明)。
#: 类型：``count`` = 非负整数（可增可减，减到 0 为止）；``text`` = 短文本；
#: ``map`` = ``名字:数量`` 的计数器表（鱼饵 / 道具）；``json`` = 结构化列表（谨慎）。
#: ⚠️ **只列玩家自己的数据**：配置（鱼池/道具定义）不在这里，那是内容表的事。
PLAYER_FIELDS: dict[str, tuple[str, str, str]] = {
    "gold": ("金币", "count", "主货币；上限 10 亿（防手滑写出天文数字）"),
    "total_caught": ("累计钓获", "count", "总钓鱼条数（等级按它算）"),
    "total_sold": ("累计卖出", "count", "卖过多少条鱼"),
    "total_fed": ("累计投喂", "count", "投喂过多少次"),
    "total_orders": ("累计交单", "count", "完成过多少个订单"),
    "perfect_pulls": ("完美拉线", "count", "拉线评价「完美」的次数（成就用）"),
    "clutch_wins": ("惊险拉线", "count", "拉线评价「偏差」但拉上来的次数（成就用）"),
    "stamina": ("体力", "count", "当前体力；-1 = 还没初始化（下次结算补满）"),
    "lottery_total": ("大鱼乐累计张数", "count", "买过多少张彩票"),
    "lottery_loses": ("大鱼乐连输", "count", "连输几张（到 pity_count 就保底）"),
    "title": ("佩戴称号", "text", "称号 id（空 = 不戴）"),
    "current_location": ("当前钓点", "text", "钓点 id（例如 novice / dragon_palace）"),
    "equipped_rod": ("当前鱼竿", "text", "鱼竿 id（例如 bamboo / void_rod）"),
    "equipped_bait": ("当前鱼饵", "text", "鱼饵 id；空串 = 自动挂最好的，none = 空钩"),
}

#: 这些字段是**计数表**（``名字:数量`` 逗号分隔），支持「加/减某几项」
PLAYER_MAP_FIELDS: dict[str, tuple[str, str]] = {
    "baits": ("鱼饵数量", "鱼饵 id 来自鱼饵表（bread / worm / abyss_bait …）"),
    "items": ("道具数量", "道具 id 来自道具表（feed_basic / hot_soup / jade_lantern …）"),
    "collectibles": ("杂物收集", "杂物 id 来自杂物表（seaweed / treasure_chest …）"),
    "variants": ("变异计数", "变异 id 来自变异表（golden / rainbow …）"),
}

#: 计数类字段的单项上限（玩家数据写坏了对谁都没好处）
PLAYER_COUNT_MAX = 1_000_000_000

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
    "title_defs": list,   # 称号表（v1.18.17 的后期金币回收口）
    "button_defs": str,   # 场景|文案|点击后发送|样式（回复里的按钮）
    "command_aliases": str,   # 规范子命令|别名,别名（命令别名）
    "custom_commands": str,   # 命令名|动作:内容（自定义命令）
    # 大鱼乐奖表（v1.18.51）：id|概率|类型|参数|数量|说明（奖品完全由它决定）
    "lottery_prizes": str,
}


#: 数值键的黑名单（后缀 / 前缀）—— 与 main 的「默认值同步」口径保持一致
NUMBER_KEY_BAD_SUFFIXES: tuple[str, ...] = ("_defs", "_slots", "_upgrades")
NUMBER_KEY_BAD_PREFIXES: tuple[str, ...] = ("data_", "backup_", "editor_")
#: 例外：名字像内容表，但页面上就是「一行逗号分隔的文本」，该由 save_numbers 改
#: ⚠️ ``backpack_upgrades``（鱼篓扩容阶梯）以前这里错拼成 ``backup_upgrades``，
#: 于是它被前缀规则 ``backup_`` 挡掉、编辑器里根本改不了 —— v1.18.16 一并修掉。
NUMBER_KEY_ALLOW: frozenset[str] = frozenset(
    {"aquarium_slots", "backpack_upgrades", "decoration_slots"},
)
#: 例外：名字像管理项，但就是普通配置（v1.18.16 放开的）
#: —— 站长要求「编辑器要能编辑插件的所有东西」。
#: * ``backpack_upgrades`` 虽然以 ``backup_`` 开头，其实是「鱼篓扩容阶梯」（一直漏在这儿：
#:   它既被 :data:`NUMBER_KEY_ALLOW` 拼错名字漏掉，又被前缀规则先一步挡掉）；
#: * ``backup_dir`` 是存档目录（一个字符串路径），是唯一一个既安全又真有人想改的
#:   ``backup_`` 键。
#: ⚠️ ``backup_import_file`` / ``backup_export_file`` 这类**文件上传**字段不在其中：
#: 插件页面的 API key 没有 /api/files 权限（历史上被 403 挡过），导入导出请走「💾 存档」页。
NUMBER_KEY_PREFIX_ALLOW: frozenset[str] = frozenset(
    {"backpack_upgrades", "backup_dir"}
)

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


def _custom_actions() -> list[str]:
    """自定义命令支持的动作（来自 _calc；拿不到就给写死的两种）。"""
    calc = globals().get("CALC")
    actions = getattr(calc, "CUSTOM_COMMAND_ACTIONS", None)
    return [str(x) for x in actions] if actions else ["发送", "执行"]


def _player_level_of(player: dict[str, Any]) -> int:
    """玩家等级（用 main 注入的 ``_player_level``；拿不到就退回 1）。"""
    func = globals().get("_player_level")
    if callable(func):
        try:
            return int(func(player))
        except Exception:
            return 1
    return 1


def _player_int(value: Any, default: int = 0) -> int:
    """宽容取整（玩家数据里的字段可能是 str / float / None）。"""
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return int(default)


def _coerce_gold(value: Any) -> tuple[int | None, str]:
    """把页面传来的金币解析成合法整数，返回 ``(金币, 错误说明)``。

    ⚠️ 先按浮点判负再取整：``int(-0.5)`` 会变成 ``0``，不先判的话
    「-0.5」会被悄悄当成 0 接受（历史坑，测试里专门卡了一条）。
    """
    if isinstance(value, bool) or value is None:
        return None, "金币要写数字"
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None, f"金币「{value}」不是数字"
    if number != number or number in (float("inf"), float("-inf")):  # NaN / ±inf
        return None, f"金币「{value}」不是有效数字"
    if number < 0:
        return None, "金币不能是负数"
    gold = int(number)
    if gold > PLAYER_GOLD_MAX:
        return None, f"金币最多 {PLAYER_GOLD_MAX}（防止手滑写出天文数字）"
    return gold, ""


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


#: `_conf_schema.json` 的缓存（进程内读一次就够；文件是插件自带的，不会在运行时变）
_CONFIG_SCHEMA_CACHE: dict[str, Any] | None = None


def _config_schema() -> dict[str, Any]:
    """读插件自带的 ``_conf_schema.json``（配置项的说明/类型/默认值）。

    用途：下发给编辑器页面，让「插件有、页面没登记」的新键**自动**长出一行
    （见 ``_editor_config_payload``）。读不到就返回空 dict，页面退回老行为。
    """
    global _CONFIG_SCHEMA_CACHE
    if _CONFIG_SCHEMA_CACHE is not None:
        return _CONFIG_SCHEMA_CACHE
    data: dict[str, Any] = {}
    try:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_conf_schema.json"
        )
        with open(path, encoding="utf-8-sig") as fp:
            parsed = json.load(fp)
        if isinstance(parsed, dict):
            data = {str(k): v for k, v in parsed.items() if isinstance(v, dict)}
    except Exception as e:  # pragma: no cover - 拿不到就当没这功能
        _log_debug(f"读取 _conf_schema.json 失败：{e}")
    _CONFIG_SCHEMA_CACHE = data
    return data


def number_whitelist() -> list[str]:
    """可被 ``save_numbers`` 改写的配置键（从 DEFAULTS 里筛出来）。

    插件面板里的配置项全部隐藏了，所以这里的口径要尽量宽：
    数值 / 布尔 / 字符串 / **列表**（页面按逗号分隔的文本提交）都能改，
    只把内容表（``*_defs``）和数据管理项（``data_*`` / ``backup_*`` / ``editor_*``）挡在外面。

    两个例外（见 :data:`NUMBER_KEY_ALLOW` / :data:`NUMBER_KEY_PREFIX_ALLOW`）：
    ``aquarium_slots`` / ``backpack_upgrades`` / ``decoration_slots`` 名字像内容表，
    其实是一行逗号分隔的文本；``backpack_upgrades`` / ``backup_dir`` 还会被前缀规则
    误伤，所以也在前缀例外里（v1.18.16 放开，站长要「编辑器能编辑所有东西」）。
    """
    keys: list[str] = []
    for key, value in _defaults_map().items():
        if not isinstance(key, str) or not key:
            continue
        if key.startswith(NUMBER_KEY_BAD_PREFIXES) and key not in NUMBER_KEY_PREFIX_ALLOW:
            continue
        if key.endswith(NUMBER_KEY_BAD_SUFFIXES) and key not in NUMBER_KEY_ALLOW:
            continue
        if isinstance(value, (bool, int, float, str, list)):
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


def _split_list_text(text: str) -> list[str]:
    """``44,28,16`` / ``精致缸|1600,生态缸|5400`` -> 列表（兼容中文逗号、分号、换行）。"""
    raw = str(text or "").replace("，", ",").replace("；", ",").replace(";", ",")
    return [piece.strip() for piece in raw.replace("\n", ",").split(",") if piece.strip()]


def _as_number(value: Any) -> float | None:
    """数字 / **数字字符串** -> float；其它一律 ``None``。

    ⚠️ 字符串也算（v1.18.33）：页面上「值是文本」的那几行是按字符串提交的，
    只要有一行标错（历史上 `luck_weight_step` / `luck_cap` 就被标成了文本行），
    整批 ``save_numbers`` 会被拒收 —— 表现就是「配置面板保存不了数据」。
    宽容收下数字字符串，页面标错也不会把保存卡死；真写了非数字（``abc``）照样拒。
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / ±inf
        return None
    return number


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
        # 文本行形态的开关（页面按字符串提交）：「true / false / 1 / 0 / 是 / 否」都认
        text = str(value).strip().lower()
        if text in ("true", "1", "是", "开", "on", "yes"):
            return True, ""
        if text in ("false", "0", "否", "关", "off", "no"):
            return False, ""
        return None, f"「{key}」需要 true/false（布尔值）"
    if isinstance(default, int):
        number = _as_number(value)
        if number is None:
            return None, f"「{key}」需要整数"
        if not number.is_integer():
            return None, f"「{key}」需要整数（收到 {value}）"
        return int(number), ""
    if isinstance(default, float):
        number = _as_number(value)
        if number is None:
            return None, f"「{key}」需要数字"
        return number, ""
    if isinstance(default, list):
        # 列表型（品质权重 / 品质显示名 / 扩建价格 …）：页面按逗号分隔的文本提交，
        # 这里拆成列表；列表里本来是数字的（例如 quality_weights）再转回数字，
        # 否则读配置的地方 `sum(weights)` 会炸在字符串上。
        pieces = value if isinstance(value, list) else _split_list_text(value)
        if not pieces:
            return None, f"「{key}」不能为空（用逗号分隔）"
        if len(pieces) > 200:
            return None, f"「{key}」条目太多（{len(pieces)} 个）"
        want_number = bool(default) and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in default
        )
        if want_number:
            numbers: list[float] = []
            for piece in pieces:
                # ⚠️ 必须走 _as_number：裸 float() 会收下 "nan" / "inf"，
                # 而 NaN 一旦写进配置，JSONResponse(allow_nan=False) 之后每次
                # GET config / 打开编辑器页面都会 500，只能手改配置文件才能救回来。
                number = _as_number(piece)
                if number is None:
                    return None, f"「{key}」每一项都要是数字（收到「{piece}」）"
                numbers.append(number)
            if all(float(n).is_integer() for n in numbers):
                return [int(n) for n in numbers], ""
            return numbers, ""
        return [str(x) for x in pieces], ""
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
        return names or ["astrbot_plugin_fishing_mini"]

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
            (
                "/{plugin}/" + ENDPOINT_PLAYERS,
                self.editor_api_players,
                ["GET"],
                "读取群钓鱼的实时玩家列表（金币 / 等级 / 钓获）",
            ),
            (
                "/{plugin}/" + ENDPOINT_PLAYERS,
                self.editor_api_players,
                ["POST"],
                "改玩家金币（实时玩家，或某份存档里的玩家）",
            ),
            (
                "/{plugin}/" + ENDPOINT_SCENES,
                self.editor_api_scenes,
                ["GET"],
                "读取群钓鱼的回复场景表（每条回复的按钮 + 文案 + 预览）",
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
        # 配置项的**元信息**（v1.18.47）：把 _conf_schema.json 的 description/type/default
        # 一并发给页面。于是「插件新加了配置键、页面还没登记」时，页面能**自动**长出一行
        # （用 schema 里的说明当标签），而不是把站长挡在「未登记、没有入口」的红行上。
        # 这样新增配置项不再需要手抄进页面的 NUMBER_KEYS —— 从根上治「配置 UI 老出问题」。
        schema = _config_schema()
        if schema:
            payload["config_schema"] = schema
        # 效果键清单（v1.13.0）：道具页的效果下拉/校验都读这里，不再写死清单，
        # 于是改 _effects.EFFECTS 或加一个扩展，页面会自动多出新键。
        fx = _effects_module()
        if fx is not None:
            payload["effect_keys"] = fx.effect_table()
            payload["effect_hint"] = fx.effect_hint()
            payload["extensions"] = fx.report_lines()
        return payload

    async def editor_api_config(self):
        """GET config：返回当前配置，供页面渲染。"""
        data = self._editor_config_payload()
        try:
            from astrbot.api.web import json_response
        except Exception:  # pragma: no cover - 单测里可能没有 astrbot
            return data
        return json_response(data)

    # ------------------------------------------------------------ GET scenes
    def _editor_default_buttons(self, scene: str) -> list[list[Any]]:
        """某场景的**出厂**按钮（含继承：子场景没内置时用父场景的）。

        ``button_empty_scenes`` 里的场景返回空：页面别再把「出厂按钮」显示成
        生效按钮，不然站长会以为没删掉。
        """
        if scene in BUTTON_EMPTY_SCENES:
            return []
        items = _BUILTIN_BUTTONS.get(scene) or []
        if not items:
            parent = SCENE_PARENT.get(scene) or ""
            if parent:
                items = _BUILTIN_BUTTONS.get(parent) or []
        if not items:
            # v1.18.17：整组兜底到「组内基础场景」（cast.miss_none → cast）
            base = SCENE_BUTTON_BASE.get(scene) or ""
            if base:
                items = _BUILTIN_BUTTONS.get(base) or []
        return [[label, data, style] for label, data, style in items]

    def _editor_scene_entry(self, scene: str) -> dict[str, Any]:
        """一个回复场景的全部信息（编辑器的一张卡片）。"""
        text_lib = TEXT_LIB
        template = "{原文}"
        placeholders: list[str] = ["原文"]
        samples: dict[str, str] = {"原文": ""}
        dynamic = False
        if text_lib is not None:
            template = str(text_lib.TEXTS.get(scene) or "{原文}")
            placeholders = list(text_lib.PLACEHOLDERS.get(scene) or ("原文",))
            samples = dict(text_lib.SAMPLES.get(scene) or {"原文": ""})
            dynamic = scene in set(text_lib.DYNAMIC)
        parent = SCENE_PARENT.get(scene) or ""
        source = self._scene_source(scene)
        if source == "group":
            # 同组兜底：把「兜底场景」也写进 parent，页面就能显示「继承自 aquarium.view」
            parent = SCENE_BUTTON_BASE.get(scene) or parent
        override = str(TEXT_OVERRIDES.get(scene) or "")
        preview = ""
        if text_lib is not None:
            preview = text_lib.render_scene(
                scene, samples.get("原文", ""), samples, {scene: template}
            )
        return {
            "id": scene,
            "label": SCENE_DESC.get(scene) or scene,
            "desc": SCENE_DESC.get(scene) or "",
            "parent": parent,
            "parent_label": parent,
            "group": SCENE_GROUP.get(scene) or "system",
            "source": source,
            "per_row": scene_rows_per_row(scene),
            "default_buttons": self._editor_default_buttons(scene),
            "buttons": [
                [label, data, style] for label, data, style in self._scene_items(scene)
            ],
            "dynamic_text": dynamic,
            # 按钮专用场景（v1.18.35）：没有回复文案，卡片上不显示文案编辑器
            "button_only": scene in BUTTON_ONLY_SCENES,
            "text": {
                "template": template,
                "placeholders": placeholders,
                "samples": samples,
                "override": override,
                "preview": preview,
                "dynamic": dynamic,
            },
        }

    def _editor_scene_payload(self) -> dict[str, Any]:
        """编辑器「💬 回复」页要的全部数据：按指令分组的场景卡片。"""
        by_group: dict[str, list[str]] = {}
        for scene in SCENE_IDS:
            by_group.setdefault(SCENE_GROUP.get(scene) or "system", []).append(scene)
        groups: list[dict[str, Any]] = []
        for group_id, label, desc in SCENE_GROUPS:
            scenes = by_group.pop(group_id, [])
            if not scenes:
                continue
            groups.append({
                "id": group_id,
                "label": label,
                "desc": desc,
                "scenes": [self._editor_scene_entry(s) for s in scenes],
            })
        # 兜底：分组表里没登记的场景也照常给出来（防止以后新增场景忘了登记分组）
        for group_id, scenes in by_group.items():
            groups.append({
                "id": group_id,
                "label": group_id,
                "desc": "（未登记分组的场景）",
                "scenes": [self._editor_scene_entry(s) for s in scenes],
            })
        fx = _effects_module()
        return {
            "status": "ok",
            "transport": "plugin-api",
            "scene_total": len(SCENE_IDS),
            "buttons_per_row_default": int(BUILTIN_BUTTONS_PER_ROW.get("*", 3)),
            "buttons_per_row_max": int(CALC.BUTTONS_PER_ROW_MAX),
            "button_styles": [
                {"value": "default", "label": "默认（灰）"},
                {"value": "primary", "label": "主要（蓝）"},
            ],
            # 按钮样式全局设置（v1.13.0）：策略 + 统一样式
            "button_style_modes": [
                {"value": "按按钮表", "label": "按按钮表（每行各写各的）"},
                {"value": "统一", "label": "全部统一（所有按钮一个样式）"},
            ],
            "button_style_mode": str(self.config.get("button_style_mode") or "按按钮表"),
            "button_default_style": str(self.config.get("button_default_style") or "default"),
            # 明确不要按钮的场景（逗号分隔）：页面用它显示「已关闭按钮」并提供一键恢复
            "button_empty_scenes": str(self.config.get("button_empty_scenes") or ""),
            # 效果键清单：页面不再写死，改 _effects.EFFECTS 或加扩展都会自动出现
            "effect_keys": (fx.effect_table() if fx else []),
            "effect_hint": (fx.effect_hint() if fx else ""),
            "extensions": (fx.report_lines() if fx else []),
            "text_overrides_hint": (
                "一行一条：场景|模板；{原文} = 插件原本拼好的那段文字"
            ),
            "groups": groups,
        }

    async def editor_api_scenes(self):
        """GET scenes：回复场景表（按钮 + 文案模板 + 示例预览）。"""
        try:
            data = self._editor_scene_payload()
        except Exception as e:  # pragma: no cover - 页面不该因此白屏
            _log_warning(f"读取回复场景表失败：{e}")
            return self._editor_api_error(f"读取回复场景表失败：{e}")
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

    # ---------------------------------------------------------- players 玩家页
    def _player_row(self, user_id: str, raw: Any) -> dict[str, Any]:
        """把一份玩家存储内容整理成页面要的一行（只读，绝不改数据）。"""
        player, _enveloped = (None, False)
        module = globals().get("BACKUP_MODULE")
        try:
            if module is not None:
                player, _enveloped = module.unwrap_player(raw)
            elif isinstance(raw, dict):
                player = raw
        except Exception:
            player = raw if isinstance(raw, dict) else None
        if not isinstance(player, dict):
            return {}
        meta: dict[str, Any] = {}
        try:
            if module is not None:
                meta = module.envelope_meta(raw) or {}
        except Exception:
            meta = {}
        return {
            "user_id": str(user_id),
            "name": str(player.get("last_name") or ""),
            "gold": _player_int(player.get("gold"), 0),
            "level": _player_level_of(player),
            "caught": _player_int(player.get("total_caught"), 0),
            "sold": _player_int(player.get("total_sold"), 0),
            "fish": len(player.get("inventory") or []),
            "aquarium": len(player.get("aquarium") or []),
            "saved_text": str(meta.get("saved_text") or ""),
        }

    async def _editor_player_rows(self, query: str = "") -> tuple[list[dict[str, Any]], int]:
        """读出玩家列表（可按 uid / 昵称搜索），返回 ``(行, 索引总数)``。

        只读：走 ``get_kv_data`` 原始读取 + 信封拆包，**不调用** ``_load_player``
        （那条路会顺手迁移并回写玩家数据，列个表不该产生副作用）。
        """
        ids = [str(x) for x in await self._player_ids()]
        total = len(ids)
        wanted = str(query or "").strip().lower()
        if wanted:
            scanned = ids
        else:
            scanned = ids[-PLAYER_SCAN_LIMIT:]
        rows: list[dict[str, Any]] = []
        for uid in scanned:
            try:
                raw = await self.get_kv_data(self._kv_key(uid), None)
            except Exception:
                continue
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            row = self._player_row(uid, raw)
            if not row:
                continue
            if wanted and wanted not in row["user_id"].lower() and wanted not in row["name"].lower():
                continue
            rows.append(row)
        rows.sort(key=lambda item: (-int(item.get("gold") or 0), str(item.get("user_id"))))
        return rows[:PLAYER_LIST_LIMIT], total

    async def _editor_player_payload(self, query: str = "") -> dict[str, Any]:
        """玩家列表的响应体（附带保存上限等元信息，页面直接渲染）。"""
        rows, total = await self._editor_player_rows(query)
        return {
            "status": "ok",
            "ok": True,
            "transport": "plugin-api",
            "players": rows,
            "count": len(rows),
            "total": total,
            "limit": PLAYER_LIST_LIMIT,
            "scan_limit": PLAYER_SCAN_LIMIT,
            "gold_max": PLAYER_GOLD_MAX,
            "query": str(query or ""),
            "editor_status": getattr(self, "_editor_status_text", "") or "",
        }

    async def _editor_snapshot_player_payload(self, name: str) -> dict[str, Any]:
        """读出「某份存档里」的玩家列表（只读；不改存档、不动实时数据）。"""
        store = getattr(self, "backup_store", None)
        wanted = str(name or "").strip()
        if store is None:
            return {"status": "error", "ok": False, "players": [],
                    "message": "存档模块不可用（_backup.py 是否缺失？）"}
        snap = store.load_snapshot(wanted) if wanted else None
        if not snap:
            return {"status": "error", "ok": False, "players": [],
                    "message": f"找不到存档「{wanted or '最新'}」（先刷新存档列表）"}
        players = snap.get("players")
        rows = [
            row
            for row in (
                self._player_row(str(uid), raw)
                for uid, raw in (players.items() if isinstance(players, dict) else [])
            )
            if row
        ]
        rows.sort(key=lambda item: (-int(item.get("gold") or 0), str(item.get("user_id"))))
        path_text = str(snap.get("path") or wanted)
        return {
            "status": "ok",
            "ok": True,
            "transport": "plugin-api",
            "players": rows[:PLAYER_LIST_LIMIT],
            "count": len(rows),
            "total": len(rows),
            "limit": PLAYER_LIST_LIMIT,
            "gold_max": PLAYER_GOLD_MAX,
            "snapshot": {
                "name": os.path.basename(path_text),
                "note": str(snap.get("note") or ""),
                "created_text": str(snap.get("created_text") or ""),
                "kind": str(snap.get("kind") or ""),
            },
            "editor_status": getattr(self, "_editor_status_text", "") or "",
        }

    async def editor_api_players(self, body: dict[str, Any] | None = None):
        """GET/POST players：实时玩家列表；POST 还能改金币。

        * GET（或 POST ``{"action": "list", "query": "..."}``）→ 实时玩家列表
        * POST ``{"action": "snapshot_list", "name": "..."}`` → 某份存档里的玩家
        * POST ``{"action": "set_gold", "user_id", "gold", "confirm": true}``
          → 改**实时**玩家金币（改前自动存一份 auto 档）
        * POST ``{"action": "snapshot_gold", "name", "user_id", "gold", "confirm": true}``
          → 改**某份存档里**的玩家金币（改完要「恢复」这份存档才生效）
        """
        if body is None:
            reader = getattr(self, "_editor_request_json", None)
            if callable(reader):
                body, _err = await reader()
        if not isinstance(body, dict):
            body = {}
        action = str(body.get("action") or "list").strip().lower()
        if action in ("", "list", "refresh"):
            return await self._editor_player_payload(str(body.get("query") or ""))
        if action == "snapshot_list":
            return await self._editor_snapshot_player_payload(str(body.get("name") or ""))
        if action == "player_get":
            # 读一个玩家的可编辑数据（只读）：给页面渲染「改数据」面板
            ok, data = await self._editor_player_detail(
                str(body.get("user_id") or body.get("uid") or "")
            )
            if not ok:
                return self._editor_api_error(str(data))
            payload = {"status": "ok", "ok": True, "transport": "plugin-api"}
            payload.update(data if isinstance(data, dict) else {})
            return payload
        if action == "player_full_get":
            # 玩家存档编辑器（v1.18.56）：读全部数据（分组 + 枚举 + 原始 JSON）
            ok, data = await self._editor_player_detail_full(
                str(body.get("user_id") or body.get("uid") or ""),
                snapshot=str(body.get("snapshot") or ""),
            )
            if not ok:
                return self._editor_api_error(str(data))
            payload = {"status": "ok", "ok": True, "transport": "plugin-api"}
            payload.update(data if isinstance(data, dict) else {})
            return payload
        if action not in PLAYER_ACTIONS:
            return self._editor_api_error(
                "不认识的玩家动作「" + action + "」（可用："
                + "、".join(PLAYER_ACTIONS) + "）"
            )
        # set_gold / snapshot_gold：复用 config 通道的动作分发（校验与提示都在一起）
        ok, message = await self._editor_run_action(action, body)
        await self._editor_write_status(action=action, ok=ok, message=message)
        if not ok:
            return self._editor_api_error(message)
        return self._editor_api_ok(message, ok=True)

    # ------------------------------------------- 玩家实时数据（v1.18.53 通用编辑）
    def _editor_player_module(self) -> Any:
        """玩家存档编辑器模块（v1.18.56）；拿不到就退回 None（老行为照常）。"""
        for name in ("astrbot_fishing_editor_player", "_editor_player"):
            module = sys.modules.get(name)
            if module is not None:
                return module
        return globals().get("EDITOR_PLAYER")

    @staticmethod
    def _snapshot_file_name(text_value: Any) -> str:
        """从「给人看的快照说明」里取出**带目录的相对路径**。

        `_snapshot()` 返回的是 ``2026-09-24_1923_07.json（1 名玩家）`` 这种说明
        （带玩家数、有时还带备注），而 store 要的是文件名。

        ⚠️ **必须带上 ``manual/`` 这样的目录**（v1.18.56 修的真 bug）：
        ``find_snapshot("xxx.json")`` 是先按纯文件名去 **auto/ 找**的，
        而自动档和时间戳撞名是常事（都以分钟为粒度）——
        于是「改存档里的玩家」会**静默改到同名的那份自动档**上。
        前端传进来的通常已经是 ``manual/xxx.json``（快照列表里的 rel 字段），
        这里原样保留；只有拿到「纯文件名」时才由调用方补目录。
        """
        raw = str(text_value or "").strip().strip("（）()")
        if not raw:
            return ""
        import re
        # 先看有没有「目录/文件名.json」形态
        match = re.search(r"[^\s（）()]*?[\w.\-]+\.json", raw)
        if match:
            return match.group(0).lstrip("/")
        return raw

    def _resolve_snapshot_ref(self, value: Any) -> str:
        """把页面给的快照标识解析成 store 能唯一认出来的 ``kind/name``。

        优先级：已经带目录的相对路径 > 在索引里按文件名查到的 rel > 原样。
        """
        text_value = self._snapshot_file_name(value)
        if not text_value:
            return ""
        if "/" in text_value:
            return text_value
        store = getattr(self, "backup_store", None)
        lister = getattr(store, "list_snapshots", None)
        if callable(lister):
            try:
                for item in lister():
                    if str(item.get("name") or "") == text_value:
                        return str(item.get("rel") or text_value)
            except Exception as e:                               # pragma: no cover
                _log_debug(f"快照索引查询失败（按纯文件名兜底）：{e}")
        return text_value

    async def _editor_player_detail_full(
        self, user_id: str, *, snapshot: str = ""
    ) -> tuple[bool, str | dict[str, Any]]:
        """读一个玩家的**全部数据**（分组 + 枚举 + 原始 JSON）。

        ``snapshot`` 非空时读那份存档里的玩家（只读文件，不动实时数据）。
        """
        uid = str(user_id or "").strip()
        if not uid:
            return False, "缺少 user_id"
        snapshot = self._resolve_snapshot_ref(snapshot)
        module = self._editor_player_module()
        if module is None:
            return False, "玩家编辑器模块没加载（_editor_player.py 缺失？）"
        player = None
        if snapshot:
            store = getattr(self, "backup_store", None)
            if store is None:
                return False, "存档模块不可用"
            try:
                snap = store.load_snapshot(snapshot)
            except Exception as e:
                return False, f"读存档「{snapshot}」失败：{e}"
            players = (snap or {}).get("players")
            raw = (players or {}).get(uid)
            if raw is None:
                return False, f"存档「{snapshot}」里没有玩家 {uid}"
            player = self._unwrap_player_dict(raw)
        else:
            try:
                raw = await self.get_kv_data(self._kv_key(uid), None)
            except Exception as e:
                return False, f"读玩家 {uid} 失败：{e}"
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    return False, f"玩家 {uid} 的数据不是合法 JSON"
            player = self._unwrap_player_dict(raw)
        if not isinstance(player, dict):
            return False, f"找不到玩家 {uid}（让他先在群里发一条消息再改）"
        payload = module.section_payload(self, player)
        payload["user_id"] = uid
        payload["name"] = str(player.get("last_name") or "")
        payload["from_snapshot"] = bool(snapshot)
        return True, payload

    @staticmethod
    def _unwrap_player_dict(raw: Any) -> dict[str, Any] | None:
        """拆信封拿玩家字典（拿不到就返回 None）。

        ⚠️ KV 里存的**可能是字符串**（信封 JSON 的原样文本，取决于 AstrBot 的
        序列化路径）：`write_snapshot` / `unwrap_player` 都只认 dict，
        直接喂字符串会让这个玩家被**静默丢掉**（快照少人、编辑器说「存档里没有这个玩家」）。
        所以这里先统一 json.loads 一遍。
        """
        value = raw
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
        module = globals().get("BACKUP_MODULE")
        player = value
        try:
            if module is not None:
                player, _env = module.unwrap_player(value)
        except Exception:
            player = value
        return player if isinstance(player, dict) else None

    async def _editor_save_player_full(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """保存玩家**全部数据**的编辑（v1.18.56）。

        与改金币同一套安全口径：白名单 + 枚举/范围校验 + 二次确认 + **改前自动存档**
        + 玩家锁 + 立刻落盘；而且**先在深拷贝上试算**，任何一项不过就一个字都不改。
        """
        module = self._editor_player_module()
        if module is None:
            return False, "玩家编辑器模块没加载（_editor_player.py 缺失？）"
        user_id = str(payload.get("user_id") or payload.get("uid") or "").strip()
        if not user_id:
            return False, "缺少 user_id（改哪个玩家）"
        if payload.get("confirm") is not True:
            return False, "改玩家数据会影响真实存档：请在页面上再确认一次（confirm=true）"
        edits = payload.get("edits")
        if not isinstance(edits, list) or not edits:
            return False, "没有要改的字段（edits 是空的）"
        snapshot = self._resolve_snapshot_ref(payload.get("snapshot"))
        known = {str(x) for x in await self._player_ids()}
        if not snapshot and user_id not in known:
            return False, f"找不到玩家 {user_id}（让他先在群里发一条消息再改）"

        if snapshot:
            # 改的是**存档文件**：先整份读出来、在内存里改好、再原子写回
            store = getattr(self, "backup_store", None)
            if store is None:
                return False, "存档模块不可用（_backup.py 是否缺失？）"
            try:
                snap = store.load_snapshot(snapshot)
            except Exception as e:
                return False, f"读存档「{snapshot}」失败：{e}"
            if not isinstance(snap, dict):
                return False, f"存档「{snapshot}」格式不对"
            players = snap.get("players")
            if not isinstance(players, dict) or user_id not in players:
                return False, f"存档「{snapshot}」里没有玩家 {user_id}"
            original_raw = players.get(user_id)
            player = self._unwrap_player_dict(original_raw)
            if player is None:
                return False, f"存档「{snapshot}」里玩家 {user_id} 的数据不是对象"
            ok, detail = module.apply_player_edits(self, player, edits)
            if not ok:
                return False, detail
            backup_module = globals().get("BACKUP_MODULE")
            enveloped = isinstance(original_raw, dict) and player is not original_raw
            if backup_module is not None and enveloped:
                try:
                    players[user_id] = backup_module.wrap_player(
                        user_id, player, int(snap.get("data_version") or 1)
                    )
                except Exception:                            # pragma: no cover
                    players[user_id] = player
            else:
                players[user_id] = player
            try:
                # ⚠️ 用 load_snapshot 回来的**真实路径**（snap["path"]）写回：
                #    不能拿 "manual/xxx.json" 去 path_of 拼（它会把整串当成一个文件名，
                #    于是写到 backups/manual/manual/xxx.json 这种不存在的目录）。
                path = pathlib.Path(str(snap.get("path") or ""))
                if not path.is_file():
                    path = store.path_of(str(snap.get("kind") or "manual"),
                                         pathlib.Path(snapshot).name)
                snap["count"] = len(players)
                atomic = getattr(backup_module, "_atomic_write_text", None)
                text = json.dumps(snap, ensure_ascii=False, indent=1)
                if callable(atomic):
                    atomic(path, text)
                else:                                        # pragma: no cover
                    path.write_text(text, encoding="utf-8")
                rebuild = getattr(store, "rebuild_index", None)
                if callable(rebuild):
                    rebuild()
            except Exception as e:
                return False, f"写回存档「{snapshot}」失败：{e}"
            return True, (
                f"已改存档「{snapshot}」里玩家 {user_id} 的数据：{detail}"
                f"（要「恢复」这份存档才生效）"
            )

        # 实时玩家：自动存档 -> 锁 -> 深拷贝试算 -> 落盘
        message = ""
        snapshotter = getattr(self, "_snapshot", None)
        if callable(snapshotter):
            try:
                message = await snapshotter(
                    "auto", note=f"改玩家数据前自动存档（{user_id}：{str(edits)[:40]}）"
                )
            except Exception as e:
                return False, f"改数据前的自动存档失败，已放弃本次修改：{e}"

        lock_for = getattr(self, "_lock_for", None)
        lock = lock_for(user_id) if callable(lock_for) else None
        if lock is None:  # pragma: no cover
            class _NoLock:
                async def __aenter__(self):
                    return None

                async def __aexit__(self, *exc: Any) -> bool:
                    return False

            lock = _NoLock()
        async with lock:
            player = await self._load_player(user_id)
            trial = module.clone_player(player)
            ok, detail = module.apply_player_edits(self, trial, edits)
            if not ok:
                return False, detail
            player.clear()
            player.update(trial)
            saved = await self._save_player(player)
        if not saved:
            return False, f"玩家 {user_id} 的数据没写成功（看插件日志）"
        setter = getattr(self, "_set_status", None)
        if callable(setter):
            try:
                setter(f"页面改玩家数据：{user_id} {detail[:60]}")
            except Exception:  # pragma: no cover
                pass
        tail = f"（改前已自动存档：{message}）" if message else ""
        return True, f"已改玩家 {user_id}：{detail}{tail}"

    def _player_field_view(self, player: dict[str, Any]) -> dict[str, Any]:
        """把玩家的可编辑字段整理成页面要的结构（只读，绝不改数据）。

        返回 ``{"sections": [...], "level": n}``：每一节是一组字段，
        每项带着中文名、说明、类型与当前值 —— 页面照着渲染，不需要自己知道字段名单
        （页面与插件的字段口径永远一致：**白名单只有这一份**，在 PLAYER_FIELDS 里）。
        """
        sections: list[dict[str, Any]] = []
        counters: list[dict[str, Any]] = []
        for key, (label, _kind, desc) in PLAYER_FIELDS.items():
            value = player.get(key)
            if key == "stamina" and value in (None, ""):
                value = -1
            counters.append({
                "key": key, "label": label, "desc": desc,
                "value": value if isinstance(value, (int, float, str)) else "",
            })
        sections.append({"title": "数值与状态", "kind": "fields", "items": counters})

        maps: list[dict[str, Any]] = []
        for key, (label, desc) in PLAYER_MAP_FIELDS.items():
            raw = player.get(key)
            entries: list[dict[str, Any]] = []
            if isinstance(raw, dict):
                for name, count in sorted(raw.items(), key=lambda kv: str(kv[0])):
                    entries.append({"name": str(name), "count": _player_int(count, 0)})
            maps.append({"key": key, "label": label, "desc": desc, "entries": entries})
        sections.append({"title": "计数表（鱼饵 / 道具 / 杂物 / 变异）", "kind": "maps", "items": maps})

        inventory = player.get("inventory") or []
        aquarium = player.get("aquarium") or []
        sections.append({
            "title": "鱼（只读明细 + 可整体清空）",
            "kind": "fish",
            "items": [
                {"key": "inventory", "label": "背包", "count": len(inventory)},
                {"key": "aquarium", "label": "水族馆", "count": len(aquarium)},
            ],
        })
        return {
            "sections": sections,
            "level": _player_level_of(player),
            "field_max": PLAYER_COUNT_MAX,
        }

    async def _editor_player_detail(self, user_id: str) -> tuple[bool, str | dict[str, Any]]:
        """读一个玩家的可编辑数据（只读；不改数据、不产生副作用）。"""
        uid = str(user_id or "").strip()
        if not uid:
            return False, "player_get 需要 user_id"
        try:
            raw = await self.get_kv_data(self._kv_key(uid), None)
        except Exception as e:
            return False, f"读玩家 {uid} 失败：{e}"
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return False, f"玩家 {uid} 的数据不是合法 JSON"
        module = globals().get("BACKUP_MODULE")
        player = raw
        try:
            if module is not None:
                player, _env = module.unwrap_player(raw)
        except Exception:
            player = raw
        if not isinstance(player, dict):
            return False, f"找不到玩家 {uid}（让他先在群里发一条消息再改）"
        view = self._player_field_view(player)
        view.update({"user_id": uid, "name": str(player.get("last_name") or "")})
        return True, view

    @staticmethod
    def _coerce_player_count(
        value: Any, *, allow_negative: bool = False
    ) -> tuple[int | None, str]:
        """计数类字段：整数（容错收下 ``1.2e3`` 这种写法）。

        ``allow_negative=False``（直接设值）时拒收负数 —— 「把金币设成 -5」一定是手滑；
        ``allow_negative=True``（增减模式）时收下负数，它就是「扣多少」。
        """
        if isinstance(value, bool) or value is None:
            return None, "要写一个整数"
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None, f"「{value}」不是数字"
        if number != number or number in (float("inf"), float("-inf")):
            return None, f"「{value}」不是有效数字"
        if number < 0 and not allow_negative:
            return None, "不能是负数（想减少就写「-N」并在页面上选「减少」）"
        if abs(number) > PLAYER_COUNT_MAX:
            return None, f"绝对值最多 {PLAYER_COUNT_MAX}（防止手滑写出天文数字）"
        return int(number), ""

    def _apply_player_edits(
        self, player: dict[str, Any], edits: Any, *, add: bool
    ) -> tuple[bool, str]:
        """把页面来的编辑应用到玩家字典上（**纯内存**，调用方负责存档）。

        ``add=True`` = 增减模式：正数是加、负数是减（减到 0 为止，不会变负）；
        ``add=False`` = 直接设成这个值。

        支持的编辑形态（``edits`` 是一个列表，按顺序应用）：
        * ``{"field": "gold", "value": 1000}`` —— 单字段
        * ``{"map": "baits", "key": "worm", "value": 20}`` —— 计数表里的一项
        * ``{"map": "items", "clear": true}`` / ``{"clear": "inventory"}`` —— 清一项表
        """
        if not isinstance(edits, list) or not edits:
            return False, "没有要改的字段（edits 是空的）"
        if len(edits) > 200:
            return False, f"一次最多改 200 项（收到 {len(edits)} 项）"
        done: list[str] = []
        for edit in edits:
            if not isinstance(edit, dict):
                return False, "每一项编辑都要是一个对象"
            # --- 清空某个计数表 / 鱼列表 ---
            clear = edit.get("clear")
            if clear is not None:
                target = str(clear).strip()
                if target in PLAYER_MAP_FIELDS:
                    player[target] = {}
                    done.append(f"清空{PLAYER_MAP_FIELDS[target][0]}")
                    continue
                if target in ("inventory", "aquarium"):
                    player[target] = []
                    done.append("清空" + ("背包" if target == "inventory" else "水族馆"))
                    continue
                return False, f"不能清空「{target}」（只有计数表和背包/水族馆能清）"
            # --- 计数表里的一项 ---
            map_key = str(edit.get("map") or "").strip()
            if map_key:
                if map_key not in PLAYER_MAP_FIELDS:
                    return False, (
                        f"不认识的计数表「{map_key}」（可用："
                        + "、".join(PLAYER_MAP_FIELDS) + "）"
                    )
                name = str(edit.get("key") or "").strip()
                if not name:
                    return False, f"改「{PLAYER_MAP_FIELDS[map_key][0]}」要写 key（哪一项）"
                amount, why = self._coerce_player_count(
                    edit.get("value"), allow_negative=add
                )
                if why:
                    return False, f"{PLAYER_MAP_FIELDS[map_key][0]}（{name}）：{why}"
                table = player.get(map_key)
                if not isinstance(table, dict):
                    table = {}
                    player[map_key] = table
                old = _player_int(table.get(name), 0)
                new = max(0, old + amount) if add else amount
                if new <= 0:
                    table.pop(name, None)
                else:
                    table[name] = new
                done.append(f"{PLAYER_MAP_FIELDS[map_key][0]} {name} {old}→{new}")
                continue
            # --- 单字段 ---
            field = str(edit.get("field") or "").strip()
            if not field:
                return False, "每一项编辑都要写 field（或 map / clear）"
            if field not in PLAYER_FIELDS:
                return False, (
                    f"「{field}」不是可改的玩家字段（可用："
                    + "、".join(PLAYER_FIELDS) + "）"
                )
            label, kind, _desc = PLAYER_FIELDS[field]
            if kind == "text":
                value = str(edit.get("value") or "").strip()
                if len(value) > 64:
                    return False, f"{label} 太长了（最多 64 字）"
                player[field] = value
                done.append(f"{label} → {value or '（空）'}")
                continue
            amount, why = self._coerce_player_count(
                edit.get("value"), allow_negative=add
            )
            if why:
                return False, f"{label}：{why}"
            old = _player_int(player.get(field), 0)
            # 体力是特例：-1 表示「还没初始化」，不能当成 0 去加减
            if field == "stamina" and old < 0 and add:
                old = 0
            new = max(0, old + amount) if add else amount
            limit = PLAYER_GOLD_MAX if field == "gold" else PLAYER_COUNT_MAX
            if new > limit:
                return False, f"{label} 最多 {limit}（想给更多就分批，或调高上限）"
            player[field] = new
            done.append(f"{label} {old}→{new}")
        return True, "；".join(done) if done else "没有实际改动"

    async def _editor_set_player_data(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """改**实时玩家**的数据（金币之外的字段也能改；改前自动存档）。

        安全口径和改金币完全一致：白名单字段 + 范围校验 + 二次确认 + **改前自动存档**
        + 玩家锁 + 立刻落盘。失败一律不改（先校验完再动数据）。
        """
        user_id = str(payload.get("user_id") or payload.get("uid") or "").strip()
        if not user_id:
            return False, "player_set 需要 user_id（改哪个玩家）"
        mode = str(payload.get("mode") or "set").strip().lower()
        add = mode in ("add", "delta", "增减", "增", "加")
        if payload.get("confirm") is not True:
            return False, "改玩家数据会影响真实存档：请在页面上再确认一次（confirm=true）"
        known = {str(x) for x in await self._player_ids()}
        if user_id not in known:
            return False, f"找不到玩家 {user_id}（让他先在群里发一条消息再改）"

        # 先干跑一遍：把坏输入挡在「自动存档」之前，免得白存一份档
        probe: dict[str, Any] = {}
        ok, detail = self._apply_player_edits(probe, payload.get("edits"), add=add)
        if not ok:
            return False, detail

        # 干跑是在一个**空字典**上做的，所以「减到 0 为止」「金币上限」这类**依赖当前值**
        # 的校验必须在真玩家数据上再走一遍 —— 否则「给 9 亿金币的号加 5 亿」会绕过上限。
        message = ""
        snapshotter = getattr(self, "_snapshot", None)
        if callable(snapshotter):
            try:
                message = await snapshotter(
                    "auto", note=f"改玩家数据前自动存档（{user_id}：{detail[:40]}）"
                )
            except Exception as e:
                return False, f"改数据前的自动存档失败，已放弃本次修改：{e}"

        lock_for = getattr(self, "_lock_for", None)
        lock = lock_for(user_id) if callable(lock_for) else None
        if lock is None:  # pragma: no cover
            class _NoLock:
                async def __aenter__(self):
                    return None

                async def __aexit__(self, *exc: Any) -> bool:
                    return False

            lock = _NoLock()
        async with lock:
            player = await self._load_player(user_id)
            # 先在一个深拷贝上试算：任何一条校验不过就整批不改（数据保持原样）
            trial = copy.deepcopy(player)
            ok, detail = self._apply_player_edits(trial, payload.get("edits"), add=add)
            if not ok:
                return False, detail
            player.clear()
            player.update(trial)
            saved = await self._save_player(player)
        if not saved:
            return False, f"玩家 {user_id} 的数据没写成功（看插件日志）"
        setter = getattr(self, "_set_status", None)
        if callable(setter):
            try:
                setter(f"页面改玩家数据：{user_id} {detail[:60]}")
            except Exception:  # pragma: no cover
                pass
        tail = f"（改前已自动存档：{message}）" if message else ""
        return True, f"已改玩家 {user_id}：{detail}{tail}"

    async def _editor_set_snapshot_player(
        self, payload: dict[str, Any]
    ) -> tuple[bool, str]:
        """改**某份存档里**某个玩家的数据（改完要「恢复」这份存档才生效）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return False, "存档模块不可用（_backup.py 是否缺失？）"
        if payload.get("confirm") is not True:
            return False, "改存档内容需要再确认一次（confirm=true）"
        name = str(payload.get("name") or "").strip()
        user_id = str(payload.get("user_id") or payload.get("uid") or "").strip()
        if not name or not user_id:
            return False, "snapshot_player_set 需要 name（哪份存档）和 user_id（哪个玩家）"
        mode = str(payload.get("mode") or "set").strip().lower()
        add = mode in ("add", "delta", "增减", "增", "加")
        probe: dict[str, Any] = {}
        ok, detail = self._apply_player_edits(probe, payload.get("edits"), add=add)
        if not ok:
            return False, detail

        try:
            snap = store.load_snapshot(name)
        except Exception as e:
            return False, f"读存档「{name}」失败：{e}"
        if not isinstance(snap, dict):
            return False, f"存档「{name}」格式不对（不是对象）"
        players = snap.get("players")
        if not isinstance(players, dict):
            return False, f"存档「{name}」里没有 players（这不是玩家存档？）"
        raw = players.get(user_id)
        if raw is None:
            return False, f"存档「{name}」里没有玩家 {user_id}"
        module = globals().get("BACKUP_MODULE")
        # ⚠️ 用统一的拆包（能认「字符串形态的信封」）：直接喂 unwrap_player 的话，
        #    字符串会被判成脏数据，报「玩家数据不是对象」。
        player = self._unwrap_player_dict(raw)
        enveloped = isinstance(raw, dict) and player is not raw
        if player is None:
            return False, f"存档「{name}」里玩家 {user_id} 的数据不是对象"
        ok, detail = self._apply_player_edits(player, payload.get("edits"), add=add)
        if not ok:                                          # pragma: no cover
            return False, detail
        if module is not None:
            try:
                players[user_id] = module.wrap_player(player) if enveloped else player
            except Exception:
                players[user_id] = player
        else:
            players[user_id] = player
        # 原子写回**同一份**快照（保留 kind/created_at/note 等元信息，
        # 只换 players 里这一个玩家）—— 不能走 write_snapshot，那会另存一份新档。
        try:
            path = store.path_of(str(snap.get("kind") or "manual"), name)
            snap["count"] = len(players)
            atomic = getattr(module, "_atomic_write_text", None) if module else None
            text = json.dumps(snap, ensure_ascii=False, indent=1)
            if callable(atomic):
                atomic(path, text)
            else:                                            # pragma: no cover
                path.write_text(text, encoding="utf-8")
            rebuild = getattr(store, "rebuild_index", None)
            if callable(rebuild):
                rebuild()
        except Exception as e:
            return False, f"写回存档「{name}」失败：{e}"
        return True, (
            f"已改存档「{name}」里玩家 {user_id} 的数据：{detail}"
            f"（要「恢复」这份存档才会生效）"
        )

    async def _editor_set_player_gold(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """改实时玩家的金币（改前自动存一份 auto 档；范围校验 + 二次确认）。"""
        user_id = str(payload.get("user_id") or payload.get("uid") or "").strip()
        if not user_id:
            return False, "set_gold 需要 user_id（改哪个玩家）"
        if payload.get("confirm") is not True:
            return False, "改金币会动真实玩家数据：请在页面上再确认一次（confirm=true）"
        gold, why = _coerce_gold(payload.get("gold"))
        if why:
            return False, why
        known = {str(x) for x in await self._player_ids()}
        if user_id not in known:
            return False, f"找不到玩家 {user_id}（让他先在群里发一条消息再改）"

        # 改数据前先自动存档：用 auto/（跟着 backup_interval_hours 轮转，
        # 不会像 manual/ 那样越攒越多——历史上 manual 攒到 55 份就是这么来的）
        message = ""
        snapshotter = getattr(self, "_snapshot", None)
        if callable(snapshotter):
            try:
                message = await snapshotter(
                    "auto", note=f"改金币前自动存档（{user_id} → {gold}）"
                )
            except Exception as e:
                return False, f"改金币前的自动存档失败，已放弃本次修改：{e}"

        lock_for = getattr(self, "_lock_for", None)
        lock = lock_for(user_id) if callable(lock_for) else None
        if lock is None:  # pragma: no cover - 正常插件一定有锁
            class _NoLock:
                async def __aenter__(self):
                    return None

                async def __aexit__(self, *exc: Any) -> bool:
                    return False

            lock = _NoLock()
        old = 0
        async with lock:
            player = await self._load_player(user_id)
            old = _player_int(player.get("gold"), 0)
            player["gold"] = gold
            saved = await self._save_player(player)
        if not saved:
            return False, f"玩家 {user_id} 的数据没写成功（看插件日志）"
        detail = f"已把玩家 {user_id} 的金币 {old} → {gold}"
        if message:
            detail += f"（改前已自动存档：{message}）"
        setter = getattr(self, "_set_status", None)
        if callable(setter):
            try:
                setter(f"页面改金币：{user_id} {old} → {gold}")
            except Exception:  # pragma: no cover
                pass
        return True, detail

    async def _editor_set_snapshot_gold(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """改**某份存档里**某个玩家的金币（改完要「恢复」这份存档才生效）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return False, "存档模块不可用（_backup.py 是否缺失？）"
        if payload.get("confirm") is not True:
            return False, "改存档内容需要再确认一次（confirm=true）"
        name = str(payload.get("name") or "").strip()
        user_id = str(payload.get("user_id") or payload.get("uid") or "").strip()
        if not name or not user_id:
            return False, "snapshot_gold 需要 name（哪份存档）和 user_id（哪个玩家）"
        gold, why = _coerce_gold(payload.get("gold"))
        if why:
            return False, why

        snap = store.load_snapshot(name)
        if not snap:
            return False, f"找不到存档「{name}」（刷新一下存档列表）"
        players = snap.get("players")
        if not isinstance(players, dict) or user_id not in players:
            return False, f"存档「{snap.get('path', name)}」里没有玩家 {user_id}"
        module = globals().get("BACKUP_MODULE")
        raw = players[user_id]
        data: Any = raw
        enveloped = False
        if module is not None:
            data, enveloped = module.unwrap_player(raw)
        if not isinstance(data, dict):
            return False, f"存档里玩家 {user_id} 的数据坏了，没有改动"
        old = _player_int(data.get("gold"), 0)
        data["gold"] = gold
        if module is not None and enveloped:
            meta = module.envelope_meta(raw) or {}
            players[user_id] = module.wrap_player(
                user_id, data, int(meta.get("data_version") or 0) or 1
            )
        else:
            players[user_id] = data
        snap.pop("path", None)
        if not store.rewrite_snapshot(name, snap):
            return False, f"存档「{name}」写入失败（文件可能被占用）"
        return True, (
            f"存档「{name}」里玩家 {user_id} 的金币 {old} → {gold}；"
            f"这份存档**恢复之后**才会生效（当前在线玩家数据没动）"
        )

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
            # 回复页：按钮 + 文案 + 每行几个（三份文本一起提交）
            "save_replies": self._editor_save_replies,
            "snapshot_create": self._editor_snapshot_create,
            "snapshot_restore": self._editor_snapshot_restore,
            "snapshot_delete": self._editor_snapshot_delete,
            "snapshot_rename": self._editor_snapshot_rename,
            "save_autobackup": self._editor_save_autobackup,
            "refresh": self._editor_refresh,
            # 玩家页：改实时玩家金币 / 改某份存档里的玩家金币
            "set_gold": self._editor_set_player_gold,
            "snapshot_gold": self._editor_set_snapshot_gold,
            # 玩家页（v1.18.53）：金币之外的实时数据也能增/减/改
            "player_set": self._editor_set_player_data,
            "snapshot_player_set": self._editor_set_snapshot_player,
            # 玩家存档编辑器（v1.18.56）：全字段 + 逐条改鱼 + 原始 JSON
            "player_full_set": self._editor_save_player_full,
            # 旧作用域找回（作者名改过之后老存档会落在别的 scope 里）
            "legacy_scan": self._editor_legacy_scan,
            "legacy_import": self._editor_legacy_import,
            # 只是收起页面那块面板（+ 让插件忘掉缓存的扫描结果），不碰任何数据
            "legacy_clear": self._editor_legacy_clear,
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
        self._editor_note_user_edits(list(normalized))
        saved = await self._editor_save_config()
        self._editor_refresh_runtime()
        names = "、".join(f"{k}({len(v) if isinstance(v, list) else '文本'})" for k, v in normalized.items())
        if not saved:
            # 内存里已经生效（游戏立刻用新内容），但配置没落盘 —— 重启会回到旧内容
            return True, f"已写入内容表：{names}（配置这次没落盘，重启会丢）"
        return True, f"已写入内容表：{names}"

    # ------------------------------------------------------------ save_replies
    #: 回复页能写的文本表（一行一条）
    REPLY_TEXT_KEYS: tuple[str, ...] = ("button_defs", "text_overrides", "button_layout")
    #: 回复页能写的单选设置（按钮样式全局设置，v1.13.0）
    REPLY_SCALAR_KEYS: tuple[str, ...] = (
        "button_style_mode", "button_default_style", "button_empty_scenes",
    )

    async def _editor_save_replies(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """写回复相关的配置：三份文本表 + 两个按钮样式全局设置。

        * ``button_defs`` / ``text_overrides`` / ``button_layout``：一行一条的文本，
          坏行由解析器跳过并告警（不会写坏配置），所以这里只校验类型
        * ``button_style_mode`` / ``button_default_style``：两个单选取值
          （样式策略与统一样式），先校验取值再写
        写完把**实际生效**的数量回报给页面。
        """
        if not isinstance(payload, dict) or not payload:
            return False, (
                "save_replies 需要至少一项：button_defs / text_overrides / "
                "button_layout / button_style_mode / button_default_style"
            )
        changed: dict[str, str] = {}
        for key in self.REPLY_TEXT_KEYS:
            if key not in payload:
                continue
            value = payload.get(key)
            if not isinstance(value, str):
                return False, f"「{key}」需要多行文本（字符串）"
            changed[key] = value
        for key in self.REPLY_SCALAR_KEYS:
            if key not in payload:
                continue
            value = payload.get(key)
            if not isinstance(value, str):
                return False, f"「{key}」需要文本"
            changed[key] = value.strip()
        mode = changed.get("button_style_mode")
        if mode is not None and CALC._parse_button_style_mode(mode) != "uniform" \
                and mode not in ("按按钮表", "table", "逐条"):
            return False, f"样式策略只认「按按钮表」或「统一」，收到「{mode}」"
        if "button_default_style" in changed:
            if CALC._parse_button_style(changed["button_default_style"]) is None:
                return False, (
                    f"样式只认 default / primary 或 0-255 的数字，"
                    f"收到「{changed['button_default_style']}」"
                )
        if not changed:
            return False, (
                "save_replies 只认 button_defs / text_overrides / button_layout / "
                "button_style_mode / button_default_style / button_empty_scenes"
            )
        try:
            self.config.update(changed)
        except Exception as e:
            return False, f"写入配置失败：{e}"
        self._editor_note_user_edits(list(changed))
        saved = await self._editor_save_config()
        self._editor_refresh_runtime()
        detail = "；".join(
            [
                f"按钮 {sum(len(v) for v in BUTTONS.values())} 个（{len(BUTTONS)} 个场景）",
                f"文案覆盖 {len(TEXT_OVERRIDES)} 条",
                "样式策略 "
                + ("全部统一" if CALC._parse_button_style_mode(
                    self.config.get("button_style_mode")) == "uniform" else "按按钮表")
                + f"（{self.config.get('button_default_style') or 'default'}）",
                "排布 " + "、".join(
                    f"{k}×{v}" for k, v in sorted(BUTTONS_PER_ROW.items())
                ),
            ]
        )
        if not saved:
            return True, f"已保存回复设置：{detail}（配置这次没落盘，重启会丢）"
        return True, f"已保存回复设置：{detail}"

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
        self._editor_note_user_edits(list(changed))
        saved = await self._editor_save_config()
        self._editor_refresh_runtime()
        names = "、".join(f"{k}={v}" for k, v in list(changed.items())[:8])
        more = "" if len(changed) <= 8 else f" 等 {len(changed)} 项"
        if not saved:
            return True, f"已写入 {names}{more}（配置这次没落盘，重启会丢）"
        return True, f"已写入 {names}{more}"

    # ------------------------------------------------- 站长改过哪些配置（升级不覆盖）
    def _editor_user_edited_keys(self) -> list[str]:
        """站长自己改过的配置键（给页面显示：这些项升级时不会被覆盖）。"""
        try:
            current = self.config.get("user_edited_keys")
        except Exception:
            return []
        return sorted({str(x) for x in (current or []) if str(x).strip()})

    def _editor_note_user_edits(self, keys: Any) -> list[str]:
        """记下站长手动改过的配置键 —— 升级同步默认值时会跳过这些键。

        站长反馈过「更新一次就要重改一遍配置」，所以只要他从编辑器里保存过，
        这个键就永远归他（想恢复出厂值：自己改回去，或把 defaults_sync_mode 设成 all）。
        """
        try:
            wanted = [str(k) for k in (keys or []) if str(k).strip()]
        except Exception:
            return []
        if not wanted:
            return []
        try:
            current = self.config.get("user_edited_keys")
        except Exception:
            current = None
        edited = [str(x) for x in (current or []) if str(x).strip()]
        added = [k for k in wanted if k not in edited]
        if not added:
            return edited
        merged = edited + added
        try:
            self.config["user_edited_keys"] = merged
        except Exception as e:  # pragma: no cover - 记不上也不该影响保存
            _log_debug(f"记录站长改过的配置键失败：{e}")
            return edited
        return merged

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
        self._editor_note_user_edits(list(changed))
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
    # 旧作用域数据找回（v1.18.1）
    # =====================================================================
    async def _editor_legacy_scan(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """扫一遍「同名插件、别的 scope」，只读不改。"""
        scan = getattr(self, "legacy_scan", None)
        if not callable(scan):
            return False, "找回模块不可用（_legacy.py 缺失？）"
        try:
            info = await scan()
        except Exception as e:
            return False, f"扫描失败：{e}"
        # 缓存一份给页面（状态回写时会带上，页面据此画「哪个 scope 有几个玩家」）
        self._editor_legacy_cache = info
        scopes = info.get("scopes") or []
        if not scopes:
            why = str(info.get("error") or "")
            return True, (
                (f"读不到旧数据：{why}。" if why else "没找到别的作用域里的老数据")
                + f"（当前作用域 {info.get('current_scope')}；库 {info.get('db_path')}）"
            )
        parts = []
        for s in scopes[:4]:
            parts.append(
                f"{s['scope_id']}：{len(s['players'])} 名玩家 / {s['rows']} 行"
            )
        return True, "扫描到旧数据 —— " + "；".join(parts) + "（点「导入」把缺的补进来）"

    async def _editor_legacy_import(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """把老作用域里缺的玩家补进来（导入前先存一份手动快照）。"""
        do_import = getattr(self, "legacy_import", None)
        if not callable(do_import):
            return False, "找回模块不可用（_legacy.py 缺失？）"
        scopes = payload.get("scopes") if isinstance(payload, dict) else None
        want = [str(x) for x in scopes] if isinstance(scopes, list) and scopes else None
        try:
            res = await do_import(want)
        except Exception as e:
            return False, f"导入失败：{e}"
        imported = res.get("imported") or []
        skipped = res.get("skipped") or []
        failed = res.get("failed") or []
        # 导完重新扫一遍：页面上的「缺谁」列表立刻变准（老数据仍在，只是都标成「已有」）
        scan = getattr(self, "legacy_scan", None)
        if callable(scan):
            try:
                self._editor_legacy_cache = await scan()
            except Exception as e:  # pragma: no cover - 重扫失败不影响导入结果
                _log_debug(f"导入后重扫旧作用域失败：{e}")
        if not imported and not failed:
            return True, (
                f"没有需要导入的玩家（当前作用域里都有：{len(skipped)} 名）"
            )
        detail = (
            f"导入 {len(imported)} 名玩家"
            + (f"、跳过 {len(skipped)} 名（当前数据优先）" if skipped else "")
            + (f"、失败 {len(failed)} 名" if failed else "")
        )
        return True, (
            f"{detail}；导入前已存档：{res.get('snapshot') or '（无）'}"
            f"{res.get('index_note') or ''}"
        )

    async def _editor_legacy_clear(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """收起「找回旧数据」面板：只清掉插件缓存的那份扫描结果，**数据一行都不动**。"""
        self._editor_legacy_cache = None
        return True, "已收起面板（老数据一行都没动）"

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
            # 命令页要用：页面据此校验「规范子命令」，不用在 JS 里再抄一份
            "subcommands": list(globals().get("SUBCOMMAND_KEYWORDS") or ()),
            "custom_actions": _custom_actions(),
            "gold_max": PLAYER_GOLD_MAX,
            # 玩家页要用：上一次「找回旧数据」的扫描结果（没扫过就是 null）
            "legacy": getattr(self, "_editor_legacy_cache", None),
            # 数值页要用：站长自己改过的键（升级时不会被新版默认值覆盖）
            "user_edited_keys": self._editor_user_edited_keys(),
            # 版本号（v1.18.54）：页面把它显示在顶部。
            # 用途很实际：站长报「改了没反应」时，一眼就能看出**插件与页面是不是同一版**
            # （浏览器缓存了旧 index.html 时，页面就会比插件旧 —— 那种情况下页面不认识
            #   插件的新动作，报错也会很难看懂）。
            "plugin_version": self._editor_plugin_version(),
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

    def _editor_plugin_version(self) -> str:
        """插件版本号（读 metadata.yaml；读不到就空串）。

        页面把「页面版本 vs 插件版本」并排显示 —— 浏览器缓存了旧页面时，
        站长能立刻看出来，不用猜「为什么点了没反应」。
        """
        try:
            path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "metadata.yaml"
            )
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip().startswith("version:"):
                        return line.split(":", 1)[1].strip()
        except Exception as e:  # pragma: no cover
            _log_debug(f"读插件版本号失败：{e}")
        return ""

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
