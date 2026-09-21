# -*- coding: utf-8 -*-
"""数值计算与配置解析。

这一层决定「鱼值多少钱、长多少属性、等级怎么算、配置怎么写坏都不崩」，
是调数值时最常改的地方，所以从 main.py 里拆出来单独放。

运行方式：main.py 用 _load_sibling 加载本模块，并做两件事——
  1. 在 main 顶部把本模块的公开函数注入 main 的命名空间（调用点一行不用改）
  2. 在模块末尾把 main 的常量（FISH_BY_ID / VALUE_VARIANCE…）注入本模块，
     所以下面的函数可以像还在主文件里一样直接引用它们

⚠️ 维护约定：本模块只做「纯计算」，不要引入玩家状态写入；也不要对共享的
模块级变量做重新赋值（`X = ...` 只会改到本模块的副本），需要改就原地改。
"""

from __future__ import annotations

import random
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

def _variant_mult(variant_id: str | None) -> float:
    """变异个体的价值倍率（非变异返回 1.0）。"""
    if not variant_id:
        return 1.0
    variant = VARIANT_BY_ID.get(variant_id)
    return float(variant["mult"]) if variant else 1.0

def _codex_key(fish_id: str, variant: str | None = None) -> str:
    """图鉴条目的键：普通个体用 fish_id，变异体加后缀区分。"""
    return f"{fish_id}{VARIANT_CODEX_SUFFIX}{variant}" if variant else fish_id

def _split_codex_key(key: str) -> tuple[str, str | None]:
    """把图鉴键拆回 (fish_id, variant_id)。"""
    if VARIANT_CODEX_SUFFIX in key:
        fish_id, _, variant = key.partition(VARIANT_CODEX_SUFFIX)
        return fish_id, (variant or None)
    return key, None

def _is_number(value: Any) -> bool:
    """是否为可用作数值的 int/float（bool 不算）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)

def _safe_number(value: Any, default: float) -> float:
    """把任意值安全地转成 float。

    注意：插件配置里从「竖线分隔字符串」解析出来的字段都是 **str**，
    所以这里必须处理字符串，不能只认 int/float。
    """
    if isinstance(value, bool):
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return float(default)
    return float(default)

def _safe_int(raw: Any, default: int = 0, minimum: int | None = None) -> int:
    """把任意值安全地转成 int（同样要能处理配置里的字符串）。"""
    if isinstance(raw, bool):
        value = default
    elif isinstance(raw, (int, float)):
        value = int(raw)
    elif isinstance(raw, str):
        try:
            value = int(float(raw.strip()))
        except (TypeError, ValueError):
            value = default
    else:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

def _to_int(text: Any, default: int = 0) -> int:
    """安全地把用户输入转成 int（用户输入一律是 str）。"""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default

def _fmt_gold(amount: Any) -> str:
    try:
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return "0"

def _time_text(timestamp: Any) -> str:
    try:
        ts = float(timestamp)
        if ts <= 0:
            return "——"
        return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
    except (OSError, OverflowError, ValueError, TypeError):
        return "未知"

def _text_now(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """当前时间文本（存档/状态面板统一用它）。"""
    return datetime.now().strftime(fmt)

def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

def _instance_value(instance: dict[str, Any]) -> int:
    """鱼实例的当前价值。"""
    return max(0, _safe_int(instance.get("value"), 0, 0))

def _inventory_value(fish_list: list[dict[str, Any]]) -> int:
    return sum(_instance_value(x) for x in fish_list)

def _tank_display_seconds(instance: dict[str, Any], now: int | None = None) -> int:
    """这条鱼**这一轮**在缸里待了多久（秒）。纯函数，不改数据。

    ``tank_since`` = 放进缸的时刻（0 = 没有记录：老存档、或手改进去的鱼）。
    """
    now = int(now if now is not None else time.time())
    since = _safe_int(instance.get("tank_since"), 0, 0)
    if since <= 0:
        return 0
    return max(0, now - since)

def _daily_reset(player: dict[str, Any], today: str) -> bool:
    """跨天就把「今日额度」清零（惰性结算，和体力/洗髓丹同一个套路）。

    玩家从早玩到晚也**不能无限吃道具**：手气道具、回体力道具、洗髓丹、香火供奉
    都各自有一份每日额度（v1.18.18，站长提的「道具你不做一些限制吗」）。
    这里只管「日期变了就清空」，具体额度由 ``_daily_left`` 按配置算。
    """
    changed = False
    if str(player.get("daily_date") or "") != str(today or ""):
        player["daily_date"] = str(today or "")
        player["daily_used"] = {}
        changed = True
    if not isinstance(player.get("daily_used"), dict):
        player["daily_used"] = {}
        changed = True
    return changed


def _daily_used(player: dict[str, Any], key: str) -> int:
    """今天这个额度已经用掉多少。"""
    used = player.get("daily_used")
    if not isinstance(used, dict):
        return 0
    return max(0, _safe_int(used.get(key), 0, 0))


def _daily_left(player: dict[str, Any], key: str, limit: Any) -> int | None:
    """今天这个额度还剩多少；``limit <= 0`` 表示不限额（返回 ``None``）。"""
    cap = _safe_int(limit, 0, 0)
    if cap <= 0:
        return None
    return max(0, cap - _daily_used(player, key))


def _daily_add(player: dict[str, Any], key: str, amount: int = 1) -> int:
    """记一次额度消耗，返回消耗后的今日累计值。"""
    used = player.get("daily_used")
    if not isinstance(used, dict):
        used = {}
        player["daily_used"] = used
    total = max(0, _safe_int(used.get(key), 0, 0) + max(0, int(amount)))
    used[key] = total
    return total


def _daily_line(
    player: dict[str, Any], cfg: dict[str, Any], today: str
) -> str:
    """档案里那行「今日额度」（没开任何限额时返回空串，不占版面）。"""
    parts: list[str] = []
    for key, label, unit, cfg_key in (
        ("buff", "手气", "竿", "buff_daily_cast_limit"),
        ("heal", "回体力", "次", "hot_soup_daily_limit"),
        ("reroll", "洗髓丹", "颗", "reroll_daily_total"),
        ("offering", "供奉", "次", "offering_daily_limit"),
    ):
        cap = _safe_int((cfg or {}).get(cfg_key), 0, 0)
        if cap <= 0:
            continue
        parts.append(f"{label} {_daily_used(player, key)}/{cap} {unit}".rstrip())
    if not parts:
        return ""
    return "📅 今日额度：" + "・".join(parts) + "（每天 0 点重置）"


def _pond_income(
    player: dict[str, Any], cfg: dict[str, Any], now: int | None = None
) -> dict[str, Any]:
    """鱼塘（水族馆）挂机收益：**每条鱼按各自待在缸里的时间**产出。

    ⚠️ 这里修的是「空缸攒时间、领之前把鱼塞进去」那个漏洞（v1.18.0）：
    以前是「**当前**馆藏估值 × 距上次领取小时数」，所以可以让缸空着攒满 12 小时，
    领之前再放一条贵鱼进去按满额结算（那条鱼其实只待了一瞬）。
    现在每条鱼只算 ``max(它进缸的时刻, 上次结算时刻) → 现在`` 这一段，
    空缸期间谁也不产出；一直养在缸里的鱼照旧拿满（和升级前一样）。

    返回 ``{income, hours, value, counted, pending}``：
    ``hours`` = 计入的最大时长（文案用）、``value`` = 当前馆藏估值、
    ``counted`` = 真产出过的鱼数、``pending`` = 刚放进去还没产出的鱼数。
    """
    now = int(now if now is not None else time.time())
    last = _safe_int(player.get("pond_last_ts"), 0, 0)
    if last <= 0:
        last = now
    cap_hours = max(0.0, _safe_number(cfg.get("pond_income_cap_hours"), 12.0))
    rate = max(0.0, _safe_number(cfg.get("pond_income_per_hour"), 0.02))
    cap_coins = _safe_int(cfg.get("pond_income_cap_coins"), 5000, 0)
    bonus = 1.0 + _decoration_bonus(player, now=now)
    # 香火供奉（v1.18.17）：花大钱换的限时加成，和装饰加成**相加**（都是「鱼缸更好」）
    bonus += _offering_bonus(player, cfg, now=now)

    raw = 0.0
    hours = 0.0
    total = 0
    counted = 0
    tank = [x for x in (player.get("aquarium") or []) if isinstance(x, dict)]
    for instance in tank:
        value = _instance_value(instance)
        total += value
        since = _safe_int(instance.get("tank_since"), 0, 0)
        if since <= 0:
            # ⚠️ 没有入缸时间 = **不产出**。故意不留「没计时就放行」的兜底：
            # 那种兜底会让任何绕过「放入」直接写进缸里的鱼白拿收益（单测里撞见过）。
            # 老存档里已经在缸里的鱼，由读档时的 `_migrate_tank_clocks()` 一次性补时间。
            continue
        start = max(since, last)
        span = now - start
        if span <= 0 or value <= 0 or rate <= 0:
            continue
        used = min(span / 3600.0, cap_hours)
        raw += value * rate * used
        hours = max(hours, used)
        counted += 1
    income = int(raw * bonus)
    if cap_coins > 0:          # 0 = 不封顶（给站长留的自由度）
        income = min(income, cap_coins)
    return {
        "income": income,
        "hours": hours,
        "value": total,
        "counted": counted,
        "pending": len(tank) - counted,
    }

def _parse_bait_defs(raw: Any) -> dict[str, dict[str, Any]]:
    """解析鱼饵定义。返回 {bait_id: {...}}，一定包含 none（空钩）。

    新格式（10 段）：
        ``id|名称|emoji|单价|一组数量|品质幸运|稀有度权重|解锁等级|需要鱼竿|说明``
    旧格式（8~9 段）：没有解锁等级/需要鱼竿 —— 一律视为 1 级、无鱼竿要求
    （向后兼容：老配置不会因为格式不同而把鱼饵锁死）。
    """
    baits: dict[str, dict[str, Any]] = {}
    items = raw if isinstance(raw, list) else DEFAULTS["bait_defs"]
    for entry in items:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 7:
            continue
        bait_id, name, emoji = parts[0], parts[1], parts[2]
        if not bait_id or not name:
            continue
        price = max(0, _to_int(parts[3], 0))
        stock = max(0, _to_int(parts[4], 0))
        luck = _clamp(_safe_number(parts[5], 0.0), 0.0, 5.0)
        mults: list[float] = []
        for token in (parts[6] or "").split(","):
            mults.append(_clamp(_safe_number(token, 1.0), 0.01, 50.0))
        while len(mults) < len(RARITY_ORDER):
            mults.append(1.0)
        if len(parts) >= 10:
            unlock = max(1, _to_int(parts[7], 1))
            need_rod = parts[8]
            desc = parts[9]
        else:
            unlock = 1
            need_rod = ""
            desc = parts[7] if len(parts) > 7 else ""
        baits[bait_id] = {
            "id": bait_id,
            "name": name,
            "emoji": emoji,
            "price": price,
            "stock": stock,
            "luck": luck,
            "rarity_mult": {
                rarity: mults[idx] for idx, rarity in enumerate(RARITY_ORDER)
            },
            "unlock_level": unlock,
            "need_rod": need_rod,
            "desc": desc,
        }

    if "none" not in baits:
        baits["none"] = {
            "id": "none",
            "name": "空钩",
            "emoji": "🪝",
            "price": 0,
            "stock": 0,
            "luck": 0.0,
            "rarity_mult": {r: 1.0 for r in RARITY_ORDER},
            "unlock_level": 1,
            "need_rod": "",
            "desc": "什么也不挂，全凭本事",
        }
    # 空钩永远免费、永远能买（不参与解锁限制）
    baits["none"]["price"] = 0
    baits["none"]["unlock_level"] = 1
    baits["none"]["need_rod"] = ""
    return baits

#: 合法效果键的**兜底**默认值（单独导入 _calc 时用它，行为与历史版本逐字一致）。
#: 真正生效的列表来自 `_effects.EFFECTS` 注册表 —— 见下面的 `_effect_allowed()`。
EFFECT_ALLOWED: tuple[str, ...] = (
    "meat", "spirit", "sheen", "value_up",
    "decorate", "feed_bonus", "buff_quality", "heal",
)
#: 旧写法 -> 正式键名（``quality_up`` 是 v1.9 之前的写法）
EFFECT_ALIASES_EXT: dict[str, str] = {"quality_up": "buff_quality"}


def _effect_allowed() -> tuple[str, ...]:
    """当前合法的效果键：**优先直接问注册表**，拿不到才用本地兜底。

    ⚠️ 为什么不在模块级缓存一份：main.py 会把 ``_calc`` 的全局复制进自己的命名空间
    （方便调用点少写前缀），末尾的 ``_expose_globals_all()`` 又把它**推回** ``_calc``
    —— 于是 `_effects.sync_to_calc()` 刚写好的白名单会被那份**旧副本覆盖**，
    扩展注册的效果键（和新加的内置键）在真实运行时全都解析不出来，
    而单测因为直接调 sync 反而看不到（v1.13.0 埋的坑，v1.18.0 才发现）。
    直接读注册表就没有这个顺序问题了。
    """
    module = sys.modules.get("astrbot_fishing_effects")
    table = getattr(module, "EFFECTS", None) if module is not None else None
    if isinstance(table, dict) and table:
        return tuple(table)
    return EFFECT_ALLOWED


def _parse_effects(text: str) -> dict[str, float]:
    """解析道具效果串：``meat=2;spirit=1;value_up=600``。

    支持的效果键分三类：
    * 养成（一次性、永久、作用在这条鱼身上）：``meat`` / ``spirit`` / ``sheen`` /
      ``value_up`` / ``feed_bonus`` / ``quality_reroll``（重掷个体品质）
    * 水族馆装饰（耐久内持续加成挂机产出）：``decorate``
    * 其他：``buff_quality``（钓手手气 buff）、``heal``（预留），以及扩展注册的键

    合法的键以 ``_effects.EFFECTS`` 注册表为准（见 ``_effect_allowed``）。
    ``quality_up`` 是旧版写法，按 ``buff_quality`` 处理（老配置照常可用）。
    """
    effects: dict[str, float] = {}
    allowed = _effect_allowed()
    aliases = EFFECT_ALIASES_EXT
    for token in (text or "").split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip()
        if key in aliases:               # 旧写法兼容（quality_up -> buff_quality）
            key = aliases[key]
        if key not in allowed:
            continue
        effects[key] = _safe_number(value.strip(), 0.0)
    return effects

def _parse_item_defs(raw: Any) -> dict[str, dict[str, Any]]:
    """解析道具定义。返回 {item_id: {...}}。

    格式：``id|名称|emoji|单价|说明|效果``，v1.18.16 起可再加第 7 段
    **解锁等级**（省略 = 1 级，老配置照常读）：
    道具的价格是**一口价**，但它的收益随鱼价水涨船高，所以后期道具必须靠等级门槛
    来卡「什么时候买才划算」，否则要么前期买亏、要么后期白菜价。
    """
    items: dict[str, dict[str, Any]] = {}
    entries = raw if isinstance(raw, list) else DEFAULTS["item_defs"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 6:
            continue
        item_id, name, emoji = parts[0], parts[1], parts[2]
        if not item_id or not name:
            continue
        items[item_id] = {
            "id": item_id,
            "name": name,
            "emoji": emoji,
            "price": max(0, _to_int(parts[3], 0)),
            "desc": parts[4],
            "effects": _parse_effects(parts[5]),
            "unlock_level": max(1, _to_int(parts[6], 1)) if len(parts) > 6 else 1,
        }
    return items

def _parse_titles(raw: Any) -> list[dict[str, Any]]:
    """解析称号表（v1.18.17 的后期金币回收口）。

    格式：``id|名称|emoji|价格|说明``。称号**纯炫耀、不加任何属性** ——
    它的作用就是让后期钱多到没处花的人有个能买的东西，所以不需要平衡数值。
    """
    titles: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS.get("title_defs") or []
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 4 or not parts[0] or not parts[1]:
            continue
        titles.append(
            {
                "id": parts[0],
                "name": parts[1],
                "emoji": parts[2],
                "price": max(0, _to_int(parts[3], 0)),
                "desc": parts[4] if len(parts) > 4 else "",
            }
        )
    titles.sort(key=lambda t: t["price"])
    return titles


def _parse_aquarium_slots(raw: Any) -> list[dict[str, Any]]:
    """解析水族馆扩建栏位：``名字|价格`` 或 ``名字|价格|加几个位``。

    第三段（v1.18.13 新增）不写就是 +1 个位；后面几档写 2~4，
    这样「越贵的一次加得越多」，玩家不会觉得在高价档买一格亏。
    """
    slots: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS["aquarium_slots"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        add = 1
        if len(parts) >= 3:
            add = _clamp(_to_int(parts[2], 1), 1, 99)
        slots.append(
            {
                "name": parts[0],
                "price": max(0, _to_int(parts[1], 0)),
                "add": add,
            }
        )
    return slots


def _parse_fish_defs(
    raw: Any,
    *,
    location_ids: tuple[str, ...],
    name_to_id: dict[str, str],
    rarity_order: tuple[str, ...],
    warn: Any = None,
) -> list[dict[str, Any]]:
    """解析 `fish_defs`：一行一条鱼 ``id|名称|稀有度|基准价|分布|说明``。

    容错点：空行与 `#` 注释、全角 `｜`/`，`/`：`、多余空格、中文钓点名都认；
    分布的 `*` 表示「所有钓点」，省略权重 = 1.0；
    稀有度写错 → 退回最常见那一档；**分布为空的行会被跳过**
    （否则会变成「加了鱼却永远钓不到」这种玄学问题）。
    """
    text = str(raw or "")
    rows: list[dict[str, Any]] = []
    bad = 0
    first_bad = ""
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.replace("｜", "|")
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4 or not parts[0] or not parts[1]:
            bad += 1
            first_bad = first_bad or line
            continue
        fid, name, rarity = parts[0], parts[1], parts[2]
        try:
            value = int(round(float(parts[3].replace(",", "").replace("，", ""))))
        except (TypeError, ValueError):
            bad += 1
            first_bad = first_bad or line
            continue
        if rarity not in rarity_order:
            rarity = rarity_order[0]
        dist: list[tuple[str, float]] = []
        for chunk in (parts[4] if len(parts) > 4 else "").replace("，", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            loc_part, weight_part = chunk, ""
            for sep in (":", "："):
                if sep in chunk:
                    loc_part, _, weight_part = chunk.partition(sep)
                    break
            loc_part = loc_part.strip()
            try:
                weight = float(weight_part) if weight_part.strip() else 1.0
            except ValueError:
                weight = 1.0
            weight = max(0.0, weight)
            if loc_part in ("*", "全部", "所有"):
                targets = list(location_ids)
            else:
                targets = [name_to_id.get(loc_part, loc_part)]
            for loc in targets:
                if loc in location_ids:
                    dist.append((loc, weight))
        if not dist:
            bad += 1
            first_bad = first_bad or line
            continue
        rows.append(
            {
                "id": fid,
                "name": name,
                "rarity": rarity,
                "value": max(1, value),
                "dist": dist,
                "flavor": (parts[5] if len(parts) > 5 else "")[:24],
            }
        )
    if bad and warn:
        warn(f"{bad} 行格式有问题已跳过（首条：{first_bad[:60]}）")
    return rows

def _parse_escape_map(raw: Any) -> dict[str, float]:
    """解析 ``传说:0.22,神话:0.32`` 形式的逃脱率表。"""
    result: dict[str, float] = {}
    text = raw if isinstance(raw, str) else ""
    for token in text.split(","):
        token = token.strip()
        if not token or ":" not in token:
            continue
        name, _, value = token.partition(":")
        name = name.strip()
        if name:
            result[name] = _clamp(_safe_number(value.strip(), 0.2), 0.0, 1.0)
    if not result:
        result = {"传说": 0.22, "神话": 0.32}
    return result


#: 上钩率解析失败 / 键不认识时的兜底值（与空钩同档，绝不回退成 100%）

def _try_float(text: Any) -> float | None:
    """能转成数字就返回，否则 None（不用默认值兜底，便于区分「没写」和「写错」）。"""
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None

def _cfg_str(cfg: dict[str, Any], key: str) -> str:
    """取字符串配置（兼容被写成列表的情况）。"""
    value = cfg.get(key)
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return "" if value is None else str(value)

#: 字符串里哪些写法算「开」（手改配置文件 / 编辑器页面填 1 都要认）
_TRUE_WORDS: frozenset[str] = frozenset(
    {"1", "true", "yes", "on", "y", "是", "开", "打开", "启用"}
)

def _cfg_bool(cfg: dict[str, Any], key: str, default: bool = False) -> bool:
    """取布尔配置。

    ⚠️ 不能直接 ``bool(value)``：Python 里 ``bool("false")`` 是 True，
    手改配置写成字符串时会把「关」当成「开」。字符串一律按字面判断，
    数字按 0/非 0 判断，认不出的（None / 空串 / 乱写）回退 ``default``。
    """
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return default
        if text in _TRUE_WORDS:
            return True
        if text in ("0", "false", "no", "off", "n", "否", "关", "关闭", "禁用"):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default

def _norm_pairs(raw: str) -> list[tuple[str, str]]:
    """把 ``a:1,b:2`` 切成 (键, 值)，兼容全角逗号/冒号/分号与多余空格。"""
    text = (
        str(raw)
        .replace("，", ",")
        .replace("：", ":")
        .replace("；", ";")
        .replace("、", ",")
        .replace(";", ",")
    )
    pairs: list[tuple[str, str]] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, value = chunk.partition(":")
        name = name.strip()
        if not name:
            continue
        pairs.append((name, value.strip() if sep else ""))
    return pairs

def _split_range(text: Any) -> tuple[float, float] | None:
    """``0.92-1.12`` / ``40~78`` / ``60`` → (低, 高)。"""
    body = (
        str(text)
        .replace("~", "-")
        .replace("～", "-")
        .replace("—", "-")
        .replace("－", "-")
        .strip()
    )
    low_text, sep, high_text = body.partition("-")
    if not sep:
        single = _try_float(body)
        return (single, single) if single is not None else None
    low, high = _try_float(low_text), _try_float(high_text)
    if low is None or high is None:
        return None
    return (low, high) if low <= high else (high, low)

def _parse_named_floats(
    raw: str, defaults: dict[str, float], key: str
) -> dict[str, float]:
    """``常见:14,少见:4.6`` → {常见: 14.0, ...}；缺项保留默认值。"""
    result = dict(defaults)
    if not raw.strip():
        return result
    for name, text in _norm_pairs(raw):
        if name not in result:
            _tunable_warn(key, f"里的「{name}」不认识（已忽略这一项）")
            continue
        number = _try_float(text)
        if number is None or number < 0:
            _tunable_warn(key, f"里的「{name}:{text}」不是合法数值")
            continue
        result[name] = round(number, 4)
    return result

def _parse_named_texts(
    raw: str, defaults: dict[str, str], key: str
) -> dict[str, str]:
    """``meat:肉质,spirit:灵性`` → 展示名映射。"""
    result = dict(defaults)
    if not raw.strip():
        return result
    for name, text in _norm_pairs(raw):
        if name not in result:
            _tunable_warn(key, f"里的「{name}」不是已知属性")
            continue
        if text:
            result[name] = text
    return result

def _parse_named_ranges(
    raw: str, defaults: dict[str, tuple[float, float]], key: str
) -> dict[str, tuple[float, float]]:
    """``常见:40-78`` → {常见: (40.0, 78.0)}。"""
    result = dict(defaults)
    if not raw.strip():
        return result
    for name, text in _norm_pairs(raw):
        if name not in result:
            _tunable_warn(key, f"里的「{name}」不认识（已忽略这一项）")
            continue
        bounds = _split_range(text)
        if bounds is None:
            _tunable_warn(key, f"里的「{name}:{text}」不是 下限-上限 格式")
            continue
        result[name] = bounds
    return result

def _parse_number_list(raw: str, defaults: list[float], key: str) -> list[float]:
    """``5,6,7,...`` → [5.0, 6.0, 7.0]；个数不符时整体回退默认。"""
    if not raw.strip():
        return list(defaults)
    values: list[float] = []
    for chunk in _norm_pairs(raw):
        # 纯数字列表里没有冒号，_norm_pairs 会把整段当名字
        number = _try_float(chunk[1] or chunk[0])
        if number is None:
            _tunable_warn(key, f"里的「{chunk[0]}」不是合法数值")
            return list(defaults)
        values.append(number)
    if len(values) != len(defaults):
        _tunable_warn(
            key, f"需要 {len(defaults)} 个数值（每个钓点一个），当前 {len(values)} 个"
        )
        return list(defaults)
    return values

def _parse_number_range(
    raw: str, default: tuple[float, float], key: str
) -> tuple[float, float]:
    """``0.92-1.12`` → (0.92, 1.12)。"""
    if not raw.strip():
        return tuple(default)
    bounds = _split_range(raw)
    if bounds is None:
        _tunable_warn(key, f"「{raw}」不是 下限-上限 格式")
        return tuple(default)
    return bounds

def _parse_quality_tiers(
    raw: str,
    defaults: list[tuple[str, float, float, str]],
    key: str,
) -> list[tuple[str, float, float, str]]:
    """``凡品:0.8-1.0:⚪,良品:1.0-1.35:🟢`` → [(名称, 下限, 上限, emoji)]。"""
    if not raw.strip():
        return list(defaults)
    tiers: list[tuple[str, float, float, str]] = []
    for name, text in _norm_pairs(raw):
        body = text.split(":")
        bounds = _split_range(body[0]) if body else None
        emoji = body[1].strip() if len(body) > 1 else ""
        if bounds is None:
            _tunable_warn(key, f"里的「{name}:{text}」区间格式不对（应为 下限-上限）")
            continue
        tiers.append((name, bounds[0], bounds[1], emoji or "⚪"))
    if not tiers:
        _tunable_warn(key, "没能解析出任何档位")
        return list(defaults)
    return tiers

def _parse_name_values(raw: str, key: str) -> dict[str, float]:
    """``锦鲤:120,鲲:25000`` → 覆盖表（名字或 id 均可）。"""
    result: dict[str, float] = {}
    if not raw.strip():
        return result
    for name, text in _norm_pairs(raw):
        number = _try_float(text)
        if number is None or number < 0:
            _tunable_warn(key, f"里的「{name}:{text}」不是合法价格")
            continue
        result[name] = round(number, 2)
    return result

def _parse_word_list(
    raw: str, defaults: tuple[str, ...], key: str
) -> tuple[str, ...]:
    """``鳄,鲨,蛇`` → ('鳄', '鲨', '蛇')。"""
    if not raw.strip():
        return tuple(defaults)
    words = tuple(word for word, _ in _norm_pairs(raw) if word)
    if not words:
        _tunable_warn(key, "没能解析出任何关键词")
        return tuple(defaults)
    return words

def _fish_value(fish: dict[str, Any]) -> float:
    """鱼的基准价：先看单条覆盖（id 或名字），否则 内置价 × 全局倍率。"""
    if not isinstance(fish, dict):
        return 0.0
    for lookup in (str(fish.get("id") or ""), str(fish.get("name") or "")):
        if lookup and lookup in FISH_VALUE_OVERRIDES:
            return max(0.0, float(FISH_VALUE_OVERRIDES[lookup]))
    base = _try_float(fish.get("value")) or 0.0
    return max(0.0, base * FISH_VALUE_MULT)

def _parse_hook_rates(
    raw: Any, baits: dict[str, dict[str, Any]]
) -> tuple[dict[str, float], list[str]]:
    """解析 ``id:概率`` 形式的上钩率表，返回 (表, 不认识的键)。

    做了这些容错（站长手写配置很容易踩）：
    * 全角逗号 ``，`` / 中文冒号 ``：`` / 多余空格
    * 百分数写法（数值 > 1 时自动 /100，``worm:80`` → 0.80）
    * 中文饵名（用鱼饵表里的 ``name`` 反查 id，``面包屑:0.8`` → ``bread``）
    * 值解析不出来 → 回退 :data:`HOOK_RATE_FALLBACK`（而不是 1.0）
    """
    result: dict[str, float] = {}
    unknown: list[str] = []
    text = raw if isinstance(raw, str) else ""
    by_name: dict[str, str] = {}
    for bid, bait in baits.items():
        name = str(bait.get("name") or "").strip()
        if name:
            by_name[name] = bid
    normalized = text.replace("，", ",").replace("：", ":")
    for token in normalized.split(","):
        token = token.strip()
        if not token or ":" not in token:
            continue
        key, _, value = token.partition(":")
        key = key.strip()
        if not key:
            continue
        # 键：先当 id，再当展示名（中文名/大小写都能认）
        bid = key if key in baits else by_name.get(key)
        if bid is None:
            lowered = key.lower()
            bid = lowered if lowered in baits else by_name.get(key)
        if bid is None:
            unknown.append(key)
            continue
        raw_value = value.strip().rstrip("%").strip()
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            number = HOOK_RATE_FALLBACK
        if number > 100.0:
            number = HOOK_RATE_FALLBACK     # 明显写错了（比如多打一个 0）
        elif number > 1.0:
            number = number / 100.0         # 百分数写法
        result[bid] = _clamp(number, 0.0, 1.0)
    return result, unknown

def _parse_rod_defs(raw: Any) -> list[dict[str, Any]]:
    """解析鱼竿定义。

    新格式（8 段）：``id|名称|emoji|价格|价值加成|幸运加成|解锁等级|描述``
    旧格式（7 段）：``id|名称|emoji|价格|价值加成|幸运加成|描述`` —— 没有解锁等级，
    一律视为 1 级（向后兼容：老配置不会因为格式不同而把鱼竿锁死）。

    v1.18.15 起可再加两段**拉线手感**（高阶竿专用，省略 = 没有加成）：
        ``…|描述|拉线窗口加成|逃脱率系数``
        拉线窗口加成：0.15 = 窗口变长 15%（更好拉）
        逃脱率系数  ：0.90 = 逃脱率打九折（不容易跑）
    """
    rods: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS["rod_defs"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 6 or not parts[0] or not parts[1]:
            continue
        if len(parts) >= 8:
            unlock = max(1, _to_int(parts[6], 1))
            desc = parts[7]
        else:
            unlock = 1
            desc = parts[6] if len(parts) > 6 else ""
        rods.append(
            {
                "id": parts[0],
                "name": parts[1],
                "emoji": parts[2],
                "price": max(0, _to_int(parts[3], 0)),
                "value_bonus": _clamp(_safe_number(parts[4], 0.0), 0.0, 5.0),
                "luck_bonus": _clamp(_safe_number(parts[5], 0.0), 0.0, 2.0),
                "unlock_level": unlock,
                "desc": desc,
                # 拉线手感（可选列）：窗口加成 0~2、逃脱率系数 0.2~1（>1 = 更容易跑）
                "window_bonus": _clamp(_safe_number(parts[8], 0.0), 0.0, 2.0)
                if len(parts) > 8
                else 0.0,
                "escape_factor": _clamp(_safe_number(parts[9], 1.0), 0.2, 1.0)
                if len(parts) > 9
                else 1.0,
            }
        )
    if not rods:
        rods = [
            {"id": "bamboo", "name": "竹竿", "emoji": "🎋", "price": 0,
             "value_bonus": 0.0, "luck_bonus": 0.0, "unlock_level": 1,
             "window_bonus": 0.0, "escape_factor": 1.0, "desc": "备用的旧竿"}
        ]
    # 确保有免费的入门竿
    if not any(r["price"] <= 0 for r in rods):
        rods.insert(
            0,
            {"id": "bamboo", "name": "竹竿", "emoji": "🎋", "price": 0,
             "value_bonus": 0.0, "luck_bonus": 0.0, "unlock_level": 1,
             "window_bonus": 0.0, "escape_factor": 1.0, "desc": "备用的旧竿"},
        )
    return rods

def _parse_location_defs(raw: Any) -> list[dict[str, Any]]:
    """解析钓点定义：``id|名称|emoji|解锁等级|解锁金币|价值倍率|说明``。"""
    locations: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS["location_defs"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 6 or not parts[0] or not parts[1]:
            continue
        locations.append(
            {
                "id": parts[0],
                "name": parts[1],
                "emoji": parts[2],
                "level_gate": max(1, _to_int(parts[3], 1)),
                "gold_gate": max(0, _to_int(parts[4], 0)),
                "value_mult": _clamp(_safe_number(parts[5], 1.0), 0.1, 10.0),
                "desc": parts[6] if len(parts) > 6 else "",
            }
        )
    if not locations:
        locations = [
            {"id": "novice", "name": "新手村", "emoji": "🏡", "level_gate": 1,
             "gold_gate": 0, "value_mult": 1.0, "desc": "村口小池塘"}
        ]
    return locations

def _parse_backpack_upgrades(raw: Any) -> list[dict[str, Any]]:
    """解析背包扩容阶梯：``增量|价格``。"""
    ups: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS["backpack_upgrades"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 2:
            continue
        add = _to_int(parts[0], 0)
        if add > 0:
            ups.append({"add": add, "price": max(0, _to_int(parts[1], 0))})
    return ups

def _default_player(user_id: str) -> dict[str, Any]:
    """新玩家初始数据。"""
    return {
        "user_id": str(user_id),
        "data_version": DATA_VERSION,
        "gold": 100,
        "inventory": [],       # 背包（鱼实例列表，容量受限）
        "aquarium": [],        # 水族馆（鱼实例列表）
        "collection": {},      # fish_id -> {count,best_value,first_ts}
        "baits": {},           # bait_id -> 数量
        # 装备的鱼饵：空串 = 从没选过（插件会自动挂最好的），"none" = 玩家自己选了空钩
        "equipped_bait": "",
        "items": {},           # item_id -> 数量（含商店道具与钓上来的杂物）
        "auto_buff_item": "",  # 自动补给并激活的手气道具 id（空 = 不自动）
        "title": "",           # 当前佩戴的称号 id（纯炫耀，v1.18.17）
        "titles": [],          # 已买下的称号 id 列表
        "offering_ts": 0,      # 香火供奉到期时间戳（0 = 没有供奉）
        # 今日额度（v1.18.18）：{"buff": 手气竿数, "heal": 回体力次数,
        # "reroll": 洗髓丹颗数, "offering": 供奉次数}；跨天由 _daily_reset 清零
        "daily_date": "",
        "daily_used": {},
        "decorations": [],     # 水族馆装饰：[{id, rate, ts, expire_ts}]（耐久到点自动失效）
        "buff_casts_left": 0,  # 钓手手气 buff 还剩几竿（0 = 没有 buff）
        "aquarium_slots": [],  # 已解锁的水族馆扩建栏位名
        "backpack_slots": [],  # 已购买的背包扩容档位下标
        # 鱼竿：拥有 + 当前装备
        "rods": ["bamboo"],
        "equipped_rod": "bamboo",
        # 钓点：已解锁 + 当前所在地
        "locations": ["novice"],
        "current_location": "novice",
        # 订单：接单日期 + 订单列表 + 下一批刷新时间（不定时刷新）
        "order_date": "",
        "orders": [],
        "order_next_ts": 0,
        # 这批订单是按哪个钓点抽的（"" = 没按钓点）；以及本周期换钓点换过几次
        "order_location": "",
        "order_move_rerolls": 0,
        # 正在等玩家决定的随机小插曲：{"id": ..., "ts": ...}
        "event": None,
        # 每日天气 / 鱼市行情（按日期缓存，全天不变）
        "weather_date": "",
        "weather": "",
        "market_date": "",
        "market": [],
        # 鱼塘（水族馆）挂机收益
        "pond_last_ts": 0,
        "pond_claimed_ts": 0,
        "pond_best_income": 0,
        "market_best_bonus": 0,
        # 昵称与平台缓存（排行榜展示 / 排查平台适配问题）
        "last_name": "",
        "last_platform": "",
        # 收集：钓上来的杂物种类记录
        "collectibles": {},
        "bottle_notes": [],    # 收集到的纸条（仅保留最近 30 条）
        "variants": {},        # variant_id -> 累计钓到的次数
        "total_caught": 0,
        "total_sold": 0,
        "total_fed": 0,
        "total_orders": 0,
        "perfect_pulls": 0,
        "clutch_wins": 0,
        "last_fish_time": 0,
        # 体力：-1 = 还没初始化（首次结算时补满，老存档升级后不会被卡）
        "stamina": -1,
        "stamina_ts": 0,
        "last_sign_date": "",
        "last_income_date": "",
        "rod_level": 1,
        "achievements": [],
        # 已提示过的里程碑（累计钓获数量），避免重复刷屏
        "milestones": [],
        # 各鱼种品质的最佳渔获记录（长期目标）
        "best_records": {},
        # 一次性手气储备（插曲 / 彩蛋 / 扩展）：下一竿生效，**一竿即清**；
        # 和玉佩那种持续型 buff **叠加**（来源不同），见 _effective_luck
        "luck_charges": 0.0,
        # 持续型手气（锦鲤玉佩）：buff_casts_left > 0 时每竿都加这么多
        "buff_casts_left": 0,
        "buff_quality": 0.0,
        # 小插曲的「连续剧」进度（v1.18.13）：
        #   arc    = 正在连载的那条线的 id（"" = 没有在连载）
        #   ep     = 已经演到第几话（下一话 = ep + 1）
        #   flags  = 攒下来的旗标（喂过猫、教过小孩、听过铃声…）
        #   since  = 距上一话过了几竿（用来控制「下一话」不要紧挨着来）
        #   seen   = 已经演过的一次性插曲 id（once 的那些不再重复）
        #   done   = 已经演完的连载 id
        #   last   = 上一话的结局一句话（下一话开头当「前情提要」）
        "story": {
            "arc": "",
            "ep": 0,
            "flags": {},
            "since": 0,
            "seen": [],
            "done": [],
            "last": "",
        },
    }


#: 升级曲线参数：升到 L 级需要的累计钓获
#:   = base × (ratio^(L-1) − 1) / (ratio − 1) + growth × (L-1)²
#: 指数项负责「越往后越难」（把最高进度玩家卡在下一级之前），
#: 二次项默认 0，留着做微调。base/ratio/growth 都能在 WebUI 里改。

def _offering_bonus(
    player: dict[str, Any], cfg: dict[str, Any], now: int | None = None
) -> float:
    """香火供奉还在生效时的挂机产出加成（v1.18.17）。

    供奉是后期金币回收口：花一笔大钱换 24 小时的「挂机产出 +50%」。
    过期的（``offering_ts <= now``）一律算 0，不用手动清字段。
    """
    until = _safe_int(player.get("offering_ts"), 0, 0)
    if until <= 0:
        return 0.0
    now = int(now if now is not None else time.time())
    if until <= now:
        return 0.0
    return max(0.0, _safe_number((cfg or {}).get("offering_income_bonus"), 0.5))


def _offering_luck(
    player: dict[str, Any], cfg: dict[str, Any], now: int | None = None
) -> float:
    """香火供奉还在生效时的手气加成（同上，过期即 0）。"""
    until = _safe_int(player.get("offering_ts"), 0, 0)
    if until <= 0:
        return 0.0
    now = int(now if now is not None else time.time())
    if until <= now:
        return 0.0
    return max(0.0, _safe_number((cfg or {}).get("offering_luck_bonus"), 0.05))


def _effective_luck(player: dict[str, Any], cfg: dict[str, Any] | None = None) -> float:
    """本次抛竿实际吃到的手气加成。

    **两种不同来源的手气，叠加**（站长 v1.18.11 明确要求）：

    * ``luck_charges``：一次性储备（随机插曲 / 彩蛋 / 扩展给的「下一竿手气」）
      —— 只作用于**这一竿**，抛完即清（见 ``_consume_luck``）
    * ``buff_quality``：持续型 buff（锦鲤玉佩），只在 ``buff_casts_left > 0`` 时生效，
      剩下的每一竿都加这么多

    它们唯一的区别是**寿命**，不是种类，所以该加起来：拿到插曲手气的那一竿 =
    一次性 + 玉佩，之后的竿回到只有玉佩。

    ⚠️ 上限：这里只做单项钳制（各 0~5），最终吃进掷骰时还会在
    ``_roll_quality_mult`` 里与鱼饵/鱼竿/天气的手气**一起**钳到 0~1，
    所以叠起来不会把品质分布顶穿。

    历史坑（v1.18.5 → v1.18.11）：这两样以前共用 ``luck_charges`` 一个字段，
    于是玉佩生效期间「一竿即清」被跳过，插曲给的手气挂着不消失、还每竿都叠上去。
    当时的修法是拆字段 + ``max()`` 取较高值（顺带把叠加也一起禁掉了，属于过度修正）。
    现在寿命已经由两个字段各管各的，叠加不会再产生残留 —— 该加回去。
    """
    once = _clamp(_safe_number(player.get("luck_charges"), 0.0), 0.0, 5.0)
    casts = _safe_int(player.get("buff_casts_left"), 0, 0)
    buff = (
        _clamp(_safe_number(player.get("buff_quality"), 0.0), 0.0, 5.0)
        if casts > 0
        else 0.0
    )
    # 香火供奉的限时手气（v1.18.17）：和上面两种一样是「这一竿吃到的运气」，
    # 所以一起相加；没传 cfg 时按 0 处理（纯函数调用方不用管它）。
    offering = _offering_luck(player, cfg) if cfg else 0.0
    return once + buff + offering


def _consume_luck(player: dict[str, Any]) -> None:
    """抛竿收尾时消耗手气（连钓里每竿各调一次，见 ``_engine``）。

    * 一次性储备 **一竿即清**（不管玉佩在不在生效）
    * 持续型 buff 的剩余竿数 -1；减到 0 时把加成一起清掉，别留个空 buff
    """
    player["luck_charges"] = 0.0
    casts = _safe_int(player.get("buff_casts_left"), 0, 0)
    if casts > 0:
        casts -= 1
    player["buff_casts_left"] = casts
    if casts <= 0:
        player["buff_quality"] = 0.0


def _level_threshold(level: int) -> int:
    """升到 ``level`` 级需要的累计钓获（指数曲线，见 LEVEL_CURVE）。"""
    n = max(0, int(level) - 1)
    if n <= 0:
        return 0
    base = _safe_number(LEVEL_CURVE.get("base"), 5.0)
    ratio = _safe_number(LEVEL_CURVE.get("ratio"), 1.08)
    growth = _safe_number(LEVEL_CURVE.get("growth"), 0.0)
    if abs(ratio - 1.0) < 1e-9:
        # ratio = 1 时等比公式会除以 0，退化成等差数列
        geometric = base * n
    else:
        geometric = base * (ratio**n - 1.0) / (ratio - 1.0)
    return int(round(geometric + growth * n * n))

def _player_level(player: dict[str, Any]) -> int:
    """当前等级：按累计钓获查曲线，上限 MAX_LEVEL。"""
    total = _safe_int(player.get("total_caught"), 0, 0)
    level = 1
    while level < MAX_LEVEL and total >= _level_threshold(level + 1):
        level += 1
    return level

def _level_progress(player: dict[str, Any]) -> tuple[int, int, int]:
    """返回 (当前等级, 本级已钓条数, 升到下一级还要几条)。

    按真实曲线算，不再用「每级固定 N 条」的旧口径 —— 指数曲线下每级跨度不同。
    """
    caught = _safe_int(player.get("total_caught"), 0, 0)
    level = _player_level(player)
    if level >= MAX_LEVEL:
        return level, 0, 0
    here = _level_threshold(level)
    nxt = _level_threshold(level + 1)
    return level, max(0, caught - here), max(1, nxt - here)

def _stamina_enabled(cfg: dict[str, Any]) -> bool:
    """体力系统是否生效。

    ``stamina_max`` 或 ``stamina_regen_seconds`` 有一个是 0 就等于「本服不限体力」——
    这是站长想要「回到随便钓」时的开关，不用改代码。
    """
    return (
        _safe_int(cfg.get("stamina_max"), 20, 0) > 0
        and _safe_int(cfg.get("stamina_regen_seconds"), 45, 0) > 0
    )

def _refresh_stamina(player: dict[str, Any], cfg: dict[str, Any], now: int | None = None) -> int:
    """结算体力并返回当前值（惰性结算，不需要后台定时器）。

    规则（都能在 WebUI 里调）：
      * 每钓一次消耗 1 点；体力可以攒着，上限 ``stamina_max``（默认 20）
      * 每 ``stamina_regen_seconds`` 秒恢复 1 点（默认 45，即原来的冷却时间）
      * 满体力时**不累积时间**（否则攒满之后停一会儿就能连钓两轮）
      * 不足 1 点的零头保留在 ``stamina_ts`` 里，不会因为频繁查询被吃掉
      * 老存档没有 ``stamina`` 字段时按**满体力**初始化（更新后不会被挡在门外）
    返回当前体力；不限体力时返回 ``stamina_max``（0 = 不限制）。
    """
    cap = _safe_int(cfg.get("stamina_max"), 20, 0)
    regen = _safe_int(cfg.get("stamina_regen_seconds"), 45, 0)
    moment = int(time.time() if now is None else now)

    if cap <= 0 or regen <= 0:
        player["stamina"] = cap if cap > 0 else 0
        player["stamina_ts"] = moment
        return player["stamina"]

    current = _safe_int(player.get("stamina"), -1, -1)
    if current < 0:
        # 未初始化（新玩家或老存档）：直接给满，别让更新变成惩罚
        player["stamina"] = cap
        player["stamina_ts"] = moment
        return cap

    if current >= cap:
        player["stamina"] = cap
        player["stamina_ts"] = moment
        return cap

    last = _safe_int(player.get("stamina_ts"), 0, 0) or moment
    if last > moment:          # 系统时间被改回过去：以现在为基准，别把体力算飞
        last = moment
    gained = (moment - last) // regen
    if gained <= 0:
        player["stamina"] = current
        player["stamina_ts"] = last
        return current

    current = min(cap, current + gained)
    player["stamina"] = current
    # 只推进「整点」的时间，零头留着，下次接着算
    player["stamina_ts"] = moment if current >= cap else last + gained * regen
    return current

def _stamina_wait_seconds(player: dict[str, Any], cfg: dict[str, Any], now: int | None = None) -> int:
    """距离下一点体力恢复还有多少秒（不限体力或已满时返回 0）。"""
    if not _stamina_enabled(cfg):
        return 0
    cap = _safe_int(cfg.get("stamina_max"), 20, 0)
    regen = _safe_int(cfg.get("stamina_regen_seconds"), 45, 0)
    moment = int(time.time() if now is None else now)
    if _safe_int(player.get("stamina"), -1, -1) >= cap:
        return 0
    last = _safe_int(player.get("stamina_ts"), 0, 0) or moment
    if last > moment:
        last = moment
    return max(0, regen - (moment - last) % regen)

def _backpack_capacity(player: dict[str, Any], cfg: dict[str, Any]) -> int:
    """背包容量 = 基础容量 + 已购买的扩容档位。"""
    owned = player.get("backpack_slots") or []
    if not isinstance(owned, list):
        owned = []
    upgrades = cfg.get("_backpack_upgrades") or []
    total = int(cfg.get("backpack_base", 30))
    for idx in owned:
        if isinstance(idx, int) and 0 <= idx < len(upgrades):
            total += int(upgrades[idx].get("add", 0))
    return total

def _new_instance(
    fish_id: str,
    quality_mult: float,
    value_override: float | None = None,
    attrs: dict[str, int] | None = None,
    source: str = "fishing",
    value_bonus: float = 0.0,
    location_mult: float = 1.0,
    variant: str | None = None,
    codex_mult: float = 1.0,
) -> dict[str, Any] | None:
    """生成一条鱼实例。fish_id 不在图鉴里时返回 None。"""
    fish = FISH_BY_ID.get(fish_id)
    if fish is None:
        return None

    quality_mult = _clamp(float(quality_mult), 0.5, _quality_ceil())
    quality, _ = _quality_label(quality_mult)

    # 三维属性
    if attrs:
        final_attrs = {
            key: int(_clamp(_safe_int(attrs.get(key), 60), 1, 100))
            for key in ATTR_WEIGHTS
        }
    else:
        low, high = RARITY_ATTR_RANGE.get(fish["rarity"], (40, 80))
        final_attrs = {
            key: random.randint(int(low), int(high)) for key in ATTR_WEIGHTS
        }

    # 个体差异：上钩时固定下来，之后重算价值都复用它（否则投喂可能掉价）
    variance = random.uniform(*VALUE_VARIANCE)
    # 装备/钓点/变异/图鉴集齐加成，同样在上钩时固化进 base_value
    gear = (
        (1.0 + max(0.0, float(value_bonus)))
        * max(0.1, float(location_mult))
        * _variant_mult(variant)
        * max(0.1, float(codex_mult))
    )
    if value_override is not None and _is_number(value_override):
        base = max(1, int(value_override))
    else:
        base = _compute_value(
            _fish_value(fish), final_attrs, quality_mult, variance, gear
        )

    return {
        "id": uuid.uuid4().hex[:10],
        "fish_id": fish_id,
        "variant": variant,
        # base_value 是「算出来的基础价」，value = base_value + live_bonus（养成加成）
        "base_value": base,
        "value": base,
        "quality": quality,
        "quality_mult": round(quality_mult, 4),
        "value_variance": round(variance, 4),
        "gear_mult": round(gear, 4),
        "attrs": final_attrs,
        "feed_uses": 0,
        "feed_bonus": 0,      # 育灵水带来的额外投喂次数
        "live_bonus": 0,
        "locked": False,
        # 水族馆：这条鱼是什么时候放进缸的（0 = 不在缸里）。
        # 挂机收益按「每条鱼各自在缸里的时间」算，就靠这个字段（v1.18.0）
        "tank_since": 0,
        "source": source,
        "ts": int(time.time()),
    }

def _compute_value(
    base_value: float,
    attrs: dict[str, int],
    quality_mult: float,
    variance: float | None = None,
    gear_mult: float = 1.0,
) -> int:
    """售价 = 基准价 x 属性系数 x 个体品质倍率 x 个体差异 x 装备/钓点倍率。

    关键：``variance``（个体差异）与 ``gear_mult``（鱼竿+钓点）在鱼上钩时
    掷一次就固化下来，之后重算价值必须传入同一个值。否则每次投喂都重掷随机数，
    会出现「喂了反而掉价」。
    """
    weighted = sum(
        _clamp(_safe_number(attrs.get(key), ATTR_PAR), 0, 100) * weight
        for key, weight in ATTR_WEIGHTS.items()
    )
    attr_factor = 1.0 + (weighted - ATTR_PAR) / 100.0
    if variance is None:
        variance = random.uniform(*VALUE_VARIANCE)
    return max(
        1,
        int(
            round(
                base_value
                * attr_factor
                * quality_mult
                * variance
                * max(0.1, float(gear_mult))
            )
        ),
    )


def _feed_cap(instance: dict[str, Any], cfg: dict[str, Any]) -> int:
    """这条鱼的投喂上限 = 全局基础值 + 育灵水加成。"""
    base = max(0, _safe_int((cfg or {}).get("feed_max_uses"), 10, 0))
    bonus = max(0, _safe_int(instance.get("feed_bonus"), 0, 0))
    return base + bonus


def _prune_decorations(player: dict[str, Any], now: int | None = None) -> int:
    """清掉过期的水族馆装饰，返回本次清掉的数量（惰性结算，离线时间照走）。"""
    entries = player.get("decorations")
    if not isinstance(entries, list):
        player["decorations"] = []
        return 0
    now_ts = int(now if now is not None else time.time())
    alive: list[dict[str, Any]] = []
    dropped = 0
    for entry in entries:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        if _safe_int(entry.get("expire_ts"), 0, 0) > now_ts:
            alive.append(entry)
        else:
            dropped += 1
    player["decorations"] = alive
    return dropped


def _decoration_bonus(player: dict[str, Any], now: int | None = None) -> float:
    """当前生效的装饰产出加成总和（顺带清掉过期的）。"""
    _prune_decorations(player, now=now)
    total = 0.0
    for entry in player.get("decorations") or []:
        if isinstance(entry, dict):
            total += max(0.0, _safe_number(entry.get("rate"), 0.0))
    return total


def _decoration_hours_left(entry: dict[str, Any], now: int | None = None) -> float:
    """某个装饰还剩多少小时（用于展示）。"""
    now_ts = int(now if now is not None else time.time())
    left = _safe_int(entry.get("expire_ts"), 0, 0) - now_ts
    return max(0.0, left / 3600.0)


def _ensure_gear_mult(instance: dict[str, Any]) -> float:
    """拿到这条鱼固化的「鱼竿 + 钓点 + 变异 + 图鉴」倍率。

    老存档没存 ``gear_mult``：用「当前基础价 ÷ 不带装备的算法价」反推一次并写回。
    ⚠️ 必须在**改动 attrs / 个体品质之前**调用，否则反推出来的倍率是错的
    （这是修过的真实 bug：投喂时漏传 gear_mult，喂一次鱼就大幅掉价）。
    """
    raw = instance.get("gear_mult")
    if _is_number(raw):
        return _clamp(float(raw), 0.1, 50.0)
    fish = FISH_BY_ID.get(instance.get("fish_id", ""))
    old_base = _safe_int(instance.get("base_value"), 0, 0)
    if fish is None or old_base <= 0:
        return 1.0
    attrs = instance.get("attrs") if isinstance(instance.get("attrs"), dict) else {}
    variance = _clamp(
        _safe_number(instance.get("value_variance"), 1.0),
        VALUE_VARIANCE[0],
        VALUE_VARIANCE[1],
    )
    pure = _compute_value(
        _fish_value(fish),
        {key: _safe_int(attrs.get(key), int(ATTR_PAR), 1) for key in ATTR_WEIGHTS},
        _safe_number(instance.get("quality_mult"), 1.0),
        variance,
    )
    gear = _clamp(old_base / max(1, pure), 0.1, 50.0)
    instance["gear_mult"] = round(gear, 4)
    return gear


def _recalc_instance_value(
    instance: dict[str, Any], attrs: dict[str, int], quality_mult: float, gear_mult: float
) -> int:
    """按给定参数重算基础价，**只涨不跌**，并刷新 ``value``。返回刷完的价值。

    投喂（饲料）和洗髓丹（改个体品质）都走这里，算法只留一处。
    """
    fish = FISH_BY_ID.get(instance.get("fish_id", ""))
    old_base = _safe_int(instance.get("base_value"), 0, 0)
    if fish is not None:
        variance = _clamp(
            _safe_number(instance.get("value_variance"), 1.0),
            VALUE_VARIANCE[0],
            VALUE_VARIANCE[1],
        )
        new_base = _compute_value(
            _fish_value(fish), attrs, quality_mult, variance, gear_mult
        )
        instance["base_value"] = max(old_base, new_base)
    instance["value"] = _safe_int(
        instance.get("base_value"), _instance_value(instance), 1
    ) + _safe_int(instance.get("live_bonus"), 0, 0)
    return _instance_value(instance)


def _apply_quality(instance: dict[str, Any], quality_mult: float) -> tuple[str, int]:
    """改这条鱼的**个体品质**并重算估值（只涨不跌）。返回 ``(新品质名, 新价值)``。

    洗髓丹用。个体差异 / 装备倍率这些「上钩时固化」的东西一律保留，
    所以洗髓只会让鱼更值钱，不会把别的加成洗掉。
    """
    gear = _ensure_gear_mult(instance)
    quality_mult = _clamp(float(quality_mult), 0.5, _quality_ceil())
    instance["quality_mult"] = round(quality_mult, 4)
    label, _emoji = _quality_label(quality_mult)
    instance["quality"] = label
    attrs = instance.get("attrs") if isinstance(instance.get("attrs"), dict) else {}
    fixed = {key: _safe_int(attrs.get(key), int(ATTR_PAR), 1) for key in ATTR_WEIGHTS}
    return label, _recalc_instance_value(instance, fixed, quality_mult, gear)


def _apply_feed(instance: dict[str, Any], effects: dict[str, float]) -> tuple[dict[str, int], int]:
    """对一条鱼应用饲料效果。

    设计要点：基础价值 ``base_value`` 与养成加成 ``live_bonus`` **分开存**。
    投喂只重算 base_value 并把 value_up 累加到 live_bonus，
    最终价值 = base_value + live_bonus —— 这样投喂永远不会让鱼掉价
    （早期版本直接用「当前价值」当基准，随机差异会导致喂了反而变便宜）。

    返回 (属性变化量, 价值变化量)。
    """
    attrs = instance.setdefault("attrs", {})
    before = {key: _safe_int(attrs.get(key), int(ATTR_PAR), 1) for key in ATTR_WEIGHTS}
    # ⚠️ 装备倍率要在改 attrs 之前固化（老存档没有这个字段时靠旧价反推）
    gear_mult = _ensure_gear_mult(instance)
    for key in ATTR_WEIGHTS:
        delta = int(round(_safe_number(effects.get(key), 0)))
        attrs[key] = int(_clamp(before[key] + delta, 1, 100))

    old_value = _instance_value(instance)
    value_up = int(round(_safe_number(effects.get("value_up"), 0)))
    if value_up:
        instance["live_bonus"] = _safe_int(instance.get("live_bonus"), 0, 0) + value_up

    fish = FISH_BY_ID.get(instance.get("fish_id", ""))
    if fish is not None:
        quality_mult = _safe_number(instance.get("quality_mult"), 1.0)
        # 复用上钩时固定的个体差异，保证「喂养只涨不跌」
        variance = _clamp(
            _safe_number(instance.get("value_variance"), 1.0),
            VALUE_VARIANCE[0],
            VALUE_VARIANCE[1],
        )
        old_base = _safe_int(instance.get("base_value"), 0, 0)
        # ⚠️ 必须把上钩时固化的装备倍率一起传进去：漏传会让鱼竿/钓点/变异/图鉴
        # 加成全部被打回 1.0，喂一次就大幅掉价（这是修过的真实 bug）。
        raw_gear = instance.get("gear_mult")
        if _is_number(raw_gear):
            gear_mult = _clamp(float(raw_gear), 0.1, 50.0)
        elif old_base > 0:
            # 旧存档没存 gear_mult：用「喂之前的价格 ÷ 不带装备的算法价」反推，
            # 这样这一次投喂仍按原来的鱼竿+钓点档次涨价
            pure_old = _compute_value(
                _fish_value(fish), before, quality_mult, variance
            )
            gear_mult = _clamp(old_base / max(1, pure_old), 0.1, 50.0)
            instance["gear_mult"] = round(gear_mult, 4)
        else:
            gear_mult = 1.0
        new_base = _compute_value(
            _fish_value(fish), attrs, quality_mult, variance, gear_mult
        )
        # 只涨不跌的保险：任何情况下都不允许投喂把基础价压低
        instance["base_value"] = max(old_base, new_base)
    instance["value"] = _safe_int(instance.get("base_value"), old_value, 1) + _safe_int(
        instance.get("live_bonus"), 0, 0
    )
    instance["feed_uses"] = _safe_int(instance.get("feed_uses"), 0, 0) + 1

    gained = {key: attrs[key] - before[key] for key in ATTR_WEIGHTS}
    return gained, _instance_value(instance) - old_value

def _quality_label(multiplier: float) -> tuple[str, str]:
    """根据品质倍率反推个体品质名与 emoji。"""
    for name, low, high, emoji in QUALITY_TIERS:
        if low <= multiplier < high:
            return name, emoji
    top = QUALITY_TIERS[-1]
    if multiplier >= top[1]:
        return top[0], top[3]
    bottom = QUALITY_TIERS[0]
    return bottom[0], bottom[3]


def _quality_ceil() -> float:
    """个体品质倍率的钳制上限 = **最高档的上限**（至少 5.0）。

    站长可以往 ``quality_tiers`` 里加更高的档（例如「神话:4.0-6.0」），
    钳制值必须跟着走，否则洗出来的倍率会在读档时被削回 5.0。
    """
    try:
        return max(5.0, float(QUALITY_TIERS[-1][2]))
    except Exception:  # pragma: no cover - 档位表被写坏时退回老上限
        return 5.0


# ---------------------------------------------------------------------------
# 洗髓丹：每条鱼每天能吃几颗（吃满了当天就「厌恶」，第二天恢复）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 空竿：单竿与连钓**共用同一套**说法与扣饵规则（v1.18.10）
# ---------------------------------------------------------------------------

def _miss_flavor(
    bait_id: str, bait_label: str, loc: dict[str, Any], factor: float | None
) -> tuple[str, str, bool]:
    """空竿的三种说法。返回 ``(场景键, 文案, 这一竿的饵是不是被鱼咬掉了)``。

    * 空钩：鱼碰了碰就游走 —— 谈不上丢饵
    * 深水（钓点系数 < 0.75）：鱼不开口，饵还在
    * 其余：**咬了一口又吐掉** —— 文案说「白搭了」，那就得真扣（见 ``_bait_consumed``）

    以前连钓路径只写「💨 空竿」，站长看着像「连钓的鱼从来不吃饵」，
    所以这里把两种路径的说法合并成一份。
    """
    if bait_id == "none":
        return "cast.miss_none", "🪝 空钩在水里漂了半天，鱼碰了碰就游走了", False
    if factor is not None and factor < 0.75:
        name = f"{loc.get('emoji', '')}{loc.get('name', '')}"
        return "cast.miss_deep", f"🌊 {name} 水太深了，鱼不太愿意开口", False
    return "cast.miss_bait", f"🎣 咬了一口又吐掉了——{bait_label} 白搭了", True


def _bait_consumed(
    *, bait_id: str, bait_eaten: bool, got_something: bool, every_cast: bool
) -> bool:
    """这一竿要不要扣饵。

    * ``every_cast``（配置 ``consume_bait_on_empty``）= 每竿都扣（旧规则）
    * 中鱼 / 钩上杂物 = 照扣
    * 空竿：**被鱼咬掉的那一种照扣**（文案都写「白搭了」了），
      只有「没咬钩 / 鱼不开口」才不扣
    """
    if bait_id == "none":
        return False
    return bool(every_cast) or bool(got_something) or bool(bait_eaten)


def _multi_escape_chance(spec: dict[str, Any], cfg: dict[str, Any]) -> float:
    """连钓里这条要拉线的鱼「跑掉」的概率。

    连钓不弹拉线（一次判定），如果直接用鱼种的标称逃脱率，就等于
    **「不拉线也几乎不会跑」** —— 单竿里玩家手慢/超时是必跑的，于是连钓变成
    「传说鱼也不会跑」的刷分捷径（站长报的就是这个）。

    所以这里乘一个「没亲自拉线」的惩罚系数 ``multi_escape_mult``（默认 2.5，
    1.0 = 恢复旧行为，0 = 连钓里这些鱼永远不跑），上限 0.95。
    """
    base = _clamp(_safe_number((spec or {}).get("escape"), 0.0), 0.0, 1.0)
    mult = _clamp(_safe_number((cfg or {}).get("multi_escape_mult"), 2.5), 0.0, 10.0)
    return _clamp(base * mult, 0.0, 0.95)


def _reroll_daily_cap(cfg: dict[str, Any]) -> int:
    """每条鱼每天最多吃几颗洗髓丹（``reroll_daily_limit``，0 = 不限）。"""
    return max(0, _safe_int((cfg or {}).get("reroll_daily_limit"), 3, 0))


def _reroll_used(instance: dict[str, Any], today: str) -> int:
    """这条鱼**今天**已经吃了几颗洗髓丹（跨天自动归零，惰性结算）。"""
    if str(instance.get("reroll_day") or "") != str(today or ""):
        return 0
    return max(0, _safe_int(instance.get("reroll_today"), 0, 0))


def _reroll_mark(instance: dict[str, Any], today: str, used: int) -> None:
    """记下「今天吃到第几颗」（跨天会自然作废，不用定时任务）。"""
    instance["reroll_day"] = str(today or "")
    instance["reroll_today"] = max(0, int(used))


def _reroll_averse(instance: dict[str, Any], cfg: dict[str, Any], today: str) -> bool:
    """今天是不是已经吃腻了（达到上限 -> 拒绝再喂，当天「厌恶」）。"""
    cap = _reroll_daily_cap(cfg)
    return cap > 0 and _reroll_used(instance, today) >= cap

def _repair_instance(raw: Any) -> dict[str, Any] | None:
    """修复一条鱼实例；无法修复返回 None。"""
    if not isinstance(raw, dict):
        return None
    fish_id = raw.get("fish_id")
    if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
        return None

    instance_id = raw.get("id")
    if not isinstance(instance_id, str) or not instance_id:
        instance_id = uuid.uuid4().hex[:10]

    quality_mult = _clamp(_safe_number(raw.get("quality_mult"), 1.0), 0.5, _quality_ceil())
    quality, _ = _quality_label(quality_mult)

    # 三维：老数据没有，就按品质区间中值补一个（保证价格合理）
    raw_attrs = raw.get("attrs")
    low, high = RARITY_ATTR_RANGE.get(FISH_BY_ID[fish_id]["rarity"], (40, 80))
    mid = int((low + high) / 2)
    attrs: dict[str, int] = {}
    for key in ATTR_WEIGHTS:
        source = raw_attrs.get(key) if isinstance(raw_attrs, dict) else None
        attrs[key] = int(_clamp(_safe_int(source, mid), 1, 100))

    # 装备倍率：老数据没有就按 1.0（等价于没吃任何鱼竿/钓点加成）
    gear_mult = _clamp(_safe_number(raw.get("gear_mult"), 1.0), 0.1, 50.0)

    value = _safe_int(raw.get("value"), -1)
    if value <= 0:
        # 老数据连 value 都没有：按同一条鱼的算法补一个价，顺便带上装备倍率
        value = _compute_value(
            _fish_value(FISH_BY_ID[fish_id]), attrs, quality_mult, None, gear_mult
        )

    live_bonus = _safe_int(raw.get("live_bonus"), 0, 0)
    # base_value 是「不含养成加成」的基础价；老数据没有就用 value - live_bonus 反推
    base_value = _safe_int(raw.get("base_value"), -1)
    if base_value <= 0:
        base_value = max(1, value - max(0, live_bonus))

    # 个体差异：老数据没有就补一个中值，保证后续重算稳定
    variance = _clamp(
        _safe_number(raw.get("value_variance"), 1.0), VALUE_VARIANCE[0], VALUE_VARIANCE[1]
    )
    variant = raw.get("variant")
    if not isinstance(variant, str) or variant not in VARIANT_BY_ID:
        variant = None

    return {
        "id": instance_id,
        "fish_id": fish_id,
        "variant": variant if variant in VARIANT_BY_ID else None,
        "base_value": base_value,
        "value": max(1, value),
        "quality": quality,
        "quality_mult": round(quality_mult, 4),
        "value_variance": round(variance, 4),
        "gear_mult": round(gear_mult, 4),
        "attrs": attrs,
        "feed_uses": _safe_int(raw.get("feed_uses"), 0, 0),
        "feed_bonus": int(_clamp(_safe_int(raw.get("feed_bonus"), 0, 0), 0, 20)),
        # 洗髓丹的「今天吃了几颗」：跨天自动作废（只记日期 + 次数，不需要定时任务）
        # ⚠️ 同样必须列在白名单里，否则每次读档都把当天次数清零，限制就失效了
        "reroll_day": str(raw.get("reroll_day") or ""),
        "reroll_today": max(0, _safe_int(raw.get("reroll_today"), 0, 0)),
        "live_bonus": live_bonus,
        "locked": bool(raw.get("locked")),
        # ⚠️ 必须列出来：_repair_instance 是白名单式重建，漏掉就等于每次读档把
        # 「这条鱼在缸里待了多久」清零 -> 挂机收益又变成按当前估值白算（漏洞复活）
        "tank_since": max(0, _safe_int(raw.get("tank_since"), 0, 0)),
        "source": raw.get("source")
        if isinstance(raw.get("source"), str)
        else "fishing",
        "ts": _safe_int(raw.get("ts"), 0, 0),
    }

def _sync_collection(player: dict[str, Any]) -> None:
    """用背包 + 水族馆校正图鉴（count 为累计值，只增不减）。"""
    collection = player.setdefault("collection", {})
    all_fish = list(player.get("inventory", [])) + list(player.get("aquarium", []))
    held: dict[str, int] = {}
    for instance in all_fish:
        fish_id = instance.get("fish_id")
        if isinstance(fish_id, str) and fish_id in FISH_BY_ID:
            held[fish_id] = held.get(fish_id, 0) + 1

    for fish_id, count in held.items():
        entry = collection.get(fish_id)
        if not isinstance(entry, dict):
            entry = {"count": 0, "best_value": 0, "first_ts": 0}
            collection[fish_id] = entry
        entry["count"] = max(_safe_int(entry.get("count"), 0, 0), count)

    for instance in all_fish:
        fish_id = instance.get("fish_id")
        if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
            continue
        entry = collection.get(fish_id)
        if not isinstance(entry, dict):
            continue
        entry["best_value"] = max(
            _safe_int(entry.get("best_value"), 0, 0), _instance_value(instance)
        )
        if not _safe_int(entry.get("first_ts"), 0, 0):
            entry["first_ts"] = _safe_int(instance.get("ts"), 0, 0)

def _held_counts(player: dict[str, Any]) -> dict[str, int]:
    held: dict[str, int] = {}
    for instance in list(player.get("inventory", [])) + list(
        player.get("aquarium", [])
    ):
        fish_id = instance.get("fish_id")
        if isinstance(fish_id, str) and fish_id in FISH_BY_ID:
            held[fish_id] = held.get(fish_id, 0) + 1
    return held

def _repair_player(raw: Any, user_id: str) -> tuple[dict[str, Any], bool]:
    """修复/迁移玩家数据，返回 (player, 是否迁移过)。"""
    player = _default_player(user_id)
    if not isinstance(raw, dict):
        return player, False

    migrated = False
    try:
        player["gold"] = _safe_int(raw.get("gold"), player["gold"], 0)

        # --- 背包 ---
        raw_inv = raw.get("inventory")
        if isinstance(raw_inv, list):
            cleaned: list[dict[str, Any]] = []
            for item in raw_inv:
                instance = _repair_instance(item)
                if instance is not None:
                    cleaned.append(instance)
            player["inventory"] = cleaned
        elif isinstance(raw_inv, dict):
            # v1/v2：{"carp": 3}
            migrated = True
            converted: list[dict[str, Any]] = []
            for fish_id, count in raw_inv.items():
                if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
                    continue
                for _ in range(min(_safe_int(count, 0, 0), 500)):
                    instance = _new_instance(
                        fish_id, _roll_quality_mult(DEFAULTS["quality_weights"]), source="legacy"
                    )
                    if instance is not None:
                        converted.append(instance)
            player["inventory"] = converted

        # --- 水族馆 ---
        raw_aq = raw.get("aquarium")
        if isinstance(raw_aq, list):
            aquarium: list[dict[str, Any]] = []
            for item in raw_aq:
                instance = _repair_instance(item)
                if instance is not None:
                    aquarium.append(instance)
            player["aquarium"] = aquarium

        # --- 图鉴（键可能是 fish_id，也可能是 fish_id#variant 的变异条目）---
        raw_col = raw.get("collection")
        collection: dict[str, dict[str, Any]] = {}
        if isinstance(raw_col, dict):
            for key, entry in raw_col.items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                fish_id, variant = _split_codex_key(key)
                if fish_id not in FISH_BY_ID:
                    continue
                if variant is not None and variant not in VARIANT_BY_ID:
                    continue
                collection[_codex_key(fish_id, variant)] = {
                    "count": _safe_int(entry.get("count"), 0, 0),
                    "best_value": _safe_int(entry.get("best_value"), 0, 0),
                    "first_ts": _safe_int(entry.get("first_ts"), 0, 0),
                }
        player["collection"] = collection

        # --- 订单（不定时刷新）---
        order_date = raw.get("order_date", "")
        player["order_date"] = order_date if isinstance(order_date, str) else ""
        # 刷新时间戳必须保留：丢了会导致每次读档都换一批订单（交单永远失败）
        player["order_next_ts"] = _safe_int(raw.get("order_next_ts"), 0, 0)
        # 这批订单是按哪个钓点抽的（空 = 老存档 / 开关关着）；以及本周期换过几次
        order_location = raw.get("order_location", "")
        player["order_location"] = order_location if isinstance(order_location, str) else ""
        player["order_move_rerolls"] = max(
            0, _safe_int(raw.get("order_move_rerolls"), 0, 0)
        )
        # ⚠️ v1.18.17 起删掉了「狠角色」那套相处机制（站长：没意思、还容易造成巨大损失），
        # 老存档里的 hostiles_seen 字段读档时直接丢弃、不再写回。
        raw_orders = raw.get("orders")
        orders: list[dict[str, Any]] = []
        if isinstance(raw_orders, list):
            for item in raw_orders:
                if not isinstance(item, dict):
                    continue
                fish_id = item.get("fish_id")
                if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
                    continue
                orders.append(
                    {
                        "fish_id": fish_id,
                        "need": max(1, _safe_int(item.get("need"), 1, 1)),
                        "have": max(0, _safe_int(item.get("have"), 0, 0)),
                        "reward": max(1, _safe_int(item.get("reward"), 1, 1)),
                        "done": bool(item.get("done")),
                    }
                )
        player["orders"] = orders

        # --- 正在等玩家决定的随机小插曲 ---
        # `order` = 选项的显示顺序（v1.18.13 起每次都会打乱，所以必须存下来，
        # 否则玩家点「1」会和提示里看到的选项对不上）
        raw_event = raw.get("event")
        player["event"] = None
        if isinstance(raw_event, dict) and raw_event.get("id") in EVENT_BY_ID:
            order_raw = raw_event.get("order")
            player["event"] = {
                "id": str(raw_event["id"]),
                "ts": _safe_int(raw_event.get("ts"), 0, 0),
                "order": (
                    [_safe_int(x, -1, 0) for x in order_raw]
                    if isinstance(order_raw, list)
                    else []
                ),
            }

        # --- 天气与鱼市（按日期缓存）---
        for key in ("weather_date", "weather", "market_date"):
            value = raw.get(key, "")
            player[key] = value if isinstance(value, str) else ""
        if player["weather"] and player["weather"] not in WEATHER_BY_ID:
            player["weather"] = ""
        raw_market = raw.get("market")
        market: list[dict[str, Any]] = []
        if isinstance(raw_market, list):
            for item in raw_market:
                if not isinstance(item, dict):
                    continue
                fish_id = item.get("fish_id")
                if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
                    continue
                market.append(
                    {
                        "fish_id": fish_id,
                        "mult": _clamp(_safe_number(item.get("mult"), 1.5), 1.0, 10.0),
                    }
                )
        player["market"] = market

        # --- 鱼塘挂机收益时间戳 ---
        player["pond_last_ts"] = _safe_int(raw.get("pond_last_ts"), 0, 0)
        player["pond_claimed_ts"] = _safe_int(raw.get("pond_claimed_ts"), 0, 0)
        player["pond_best_income"] = _safe_int(raw.get("pond_best_income"), 0, 0)
        player["market_best_bonus"] = _safe_int(raw.get("market_best_bonus"), 0, 0)
        last_name = raw.get("last_name", "")
        player["last_name"] = last_name if isinstance(last_name, str) else ""

        # --- 变异收集 ---
        raw_variants = raw.get("variants")
        variants: dict[str, int] = {}
        if isinstance(raw_variants, dict):
            for vid, cnt in raw_variants.items():
                if isinstance(vid, str) and vid in VARIANT_BY_ID:
                    variants[vid] = _safe_int(cnt, 0, 0)
        player["variants"] = variants

        # --- 鱼饵 ---
        raw_baits = raw.get("baits")
        baits: dict[str, int] = {}
        if isinstance(raw_baits, dict):
            for bait_id, count in raw_baits.items():
                if isinstance(bait_id, str) and bait_id != "none":
                    baits[bait_id] = _safe_int(count, 0, 0)
        legacy_bait = _safe_int(raw.get("bait_count"), 0, 0)
        if legacy_bait > 0:
            baits["worm"] = baits.get("worm", 0) + legacy_bait
            migrated = True
        player["baits"] = baits

        equipped = raw.get("equipped_bait")
        player["equipped_bait"] = equipped if isinstance(equipped, str) else "none"

        # --- 自动补给 / 称号 / 香火（v1.18.17）---
        auto_buff = raw.get("auto_buff_item")
        player["auto_buff_item"] = auto_buff if isinstance(auto_buff, str) else ""
        title = raw.get("title")
        player["title"] = title if isinstance(title, str) else ""
        raw_titles = raw.get("titles")
        player["titles"] = (
            [t for t in raw_titles if isinstance(t, str)]
            if isinstance(raw_titles, list)
            else []
        )
        player["offering_ts"] = _safe_int(raw.get("offering_ts"), 0, 0)
        # 今日额度（v1.18.18）：日期对不上就当今天还没用（老存档也走这条路）
        raw_used = raw.get("daily_used")
        player["daily_used"] = (
            {
                str(k): max(0, _safe_int(v, 0, 0))
                for k, v in raw_used.items()
                if isinstance(k, str)
            }
            if isinstance(raw_used, dict)
            else {}
        )
        raw_date = raw.get("daily_date")
        player["daily_date"] = raw_date if isinstance(raw_date, str) else ""

        # --- 道具 ---
        raw_items = raw.get("items")
        items: dict[str, int] = {}
        if isinstance(raw_items, dict):
            for item_id, count in raw_items.items():
                if isinstance(item_id, str):
                    items[item_id] = _safe_int(count, 0, 0)
        player["items"] = items

        # --- 水族馆扩建 ---
        raw_slots = raw.get("aquarium_slots")
        if isinstance(raw_slots, list):
            player["aquarium_slots"] = [s for s in raw_slots if isinstance(s, str)]

        # --- 背包扩容档位 ---
        raw_bp = raw.get("backpack_slots")
        if isinstance(raw_bp, list):
            player["backpack_slots"] = [
                v for v in raw_bp if isinstance(v, int) and not isinstance(v, bool)
            ]

        # --- 鱼竿 ---
        raw_rods = raw.get("rods")
        rods: list[str] = []
        if isinstance(raw_rods, list):
            rods = [r for r in raw_rods if isinstance(r, str) and r in ROD_BY_ID]
        if not rods:
            rods = [DEFAULT_ROD]
        player["rods"] = rods
        equipped_rod = raw.get("equipped_rod")
        player["equipped_rod"] = (
            equipped_rod if equipped_rod in rods else rods[0]
        )

        # --- 钓点 ---
        raw_locs = raw.get("locations")
        locs: list[str] = []
        if isinstance(raw_locs, list):
            locs = [l for l in raw_locs if isinstance(l, str) and l in LOCATION_BY_ID]
        if DEFAULT_LOCATION not in locs:
            locs.insert(0, DEFAULT_LOCATION)
        player["locations"] = locs
        current = raw.get("current_location")
        player["current_location"] = current if current in locs else DEFAULT_LOCATION


        # --- 杂物图鉴与纸条（彩蛋计数 egg_* 也要保留）---
        raw_coll = raw.get("collectibles")
        col: dict[str, int] = {}
        if isinstance(raw_coll, dict):
            for cid, cnt in raw_coll.items():
                if not isinstance(cid, str):
                    continue
                if cid in COLLECTIBLE_BY_ID or (
                    cid.startswith("egg_") and cid[4:] in EASTER_EGG_BY_ID
                ):
                    col[cid] = _safe_int(cnt, 0, 0)
        player["collectibles"] = col

        raw_notes = raw.get("bottle_notes")
        if isinstance(raw_notes, list):
            player["bottle_notes"] = [n for n in raw_notes if isinstance(n, str)][-30:]

        # --- 计数 ---
        player["total_orders"] = _safe_int(raw.get("total_orders"), 0, 0)
        # 拉线技巧计数（用于成就）
        player["perfect_pulls"] = _safe_int(raw.get("perfect_pulls"), 0, 0)
        player["clutch_wins"] = _safe_int(raw.get("clutch_wins"), 0, 0)

        # --- 里程碑提示记录 ---
        raw_ms = raw.get("milestones")
        player["milestones"] = (
            [_safe_int(m, 0, 0) for m in raw_ms if _safe_int(m, 0, 0) > 0]
            if isinstance(raw_ms, list)
            else []
        )

        # --- 最佳渔获纪录（图鉴页展示 + 长期目标成就）---
        raw_records = raw.get("best_records")
        records: dict[str, dict[str, Any]] = {}
        if isinstance(raw_records, dict):
            for rarity, rec in raw_records.items():
                if rarity not in RARITY_RANK or not isinstance(rec, dict):
                    continue
                fish_id = rec.get("fish_id")
                if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
                    continue
                variant = rec.get("variant")
                records[rarity] = {
                    "fish_id": fish_id,
                    "variant": variant if variant in VARIANT_BY_ID else None,
                    "quality": rec.get("quality")
                    if rec.get("quality") in QUALITY_RANK
                    else QUALITY_TIERS[0][0],
                    "value": _safe_int(rec.get("value"), 0, 0),
                    "ts": _safe_int(rec.get("ts"), 0, 0),
                }
        player["best_records"] = records

        # --- 小插曲的「连续剧」进度（v1.18.13）---
        # ⚠️ 这一段是白名单式重建：**漏了哪个字段，读档时就被丢掉**
        #（`tank_since` / `reroll_day` 当年都是同一个坑）。
        raw_story = raw.get("story")
        story_raw = raw_story if isinstance(raw_story, dict) else {}
        flags_raw = story_raw.get("flags")
        player["story"] = {
            "arc": str(story_raw.get("arc") or ""),
            "ep": max(0, _safe_int(story_raw.get("ep"), 0, 0)),
            "flags": (
                {str(k): v for k, v in flags_raw.items() if isinstance(k, str)}
                if isinstance(flags_raw, dict)
                else {}
            ),
            "since": max(0, _safe_int(story_raw.get("since"), 0, 0)),
            "seen": (
                [str(x) for x in story_raw.get("seen") if isinstance(x, str)][-200:]
                if isinstance(story_raw.get("seen"), list)
                else []
            ),
            "done": (
                [str(x) for x in story_raw.get("done") if isinstance(x, str)][-50:]
                if isinstance(story_raw.get("done"), list)
                else []
            ),
            "last": str(story_raw.get("last") or "")[:120],
        }
        # 正在连载的那条线如果已经从内容表里删掉了（改过 _game_data），
        # 就把进度清干净，免得卡在一条永远演不下去的线上
        if player["story"]["arc"] and player["story"]["arc"] not in CHAIN_BY_ID:
            player["story"]["arc"] = ""
            player["story"]["ep"] = 0

        # --- 钓手手气（两套东西，别混在一起）---
        #   luck_charges    = 一次性储备（随机插曲 / 彩蛋 / 扩展给的「下一竿手气」）：**一竿即清**
        #   buff_quality    = 锦鲤玉佩这类「持续 N 竿」的每竿加成，只在 buff_casts_left > 0 时生效
        # 两者**叠加**（来源不同、寿命不同，见 _effective_luck）。
        # 以前两者共用 luck_charges：玉佩生效期间那次「一竿即清」被跳过，于是插曲给的手气
        # 一直挂着不消失、还每竿都叠在玉佩上（站长报的 bug）—— v1.18.5 拆成两个字段。
        player["luck_charges"] = _clamp(
            _safe_number(raw.get("luck_charges"), 0.0), 0.0, 5.0
        )
        player["buff_casts_left"] = int(
            _clamp(_safe_int(raw.get("buff_casts_left"), 0, 0), 0, 999)
        )
        player["buff_quality"] = _clamp(
            _safe_number(raw.get("buff_quality"), 0.0), 0.0, 5.0
        )
        if player["buff_casts_left"] <= 0:
            # 没有剩余竿数 = 持续型 buff 已经结束，加成不该留着
            player["buff_quality"] = 0.0
        elif player["buff_quality"] <= 0 < player["luck_charges"]:
            # 老存档迁移：以前玉佩的加成写在 luck_charges 里（没有 buff_quality 这个字段），
            # 照着「还在生效」把它当成本次 buff 的每竿加成，并把一次性储备清零（不双算）
            player["buff_quality"] = player["luck_charges"]
            player["luck_charges"] = 0.0

        # --- 水族馆装饰（耐久型）：只做结构修复，过期清理走惰性结算 ---
        raw_dec = raw.get("decorations")
        decorations: list[dict[str, Any]] = []
        if isinstance(raw_dec, list):
            for entry in raw_dec:
                if not isinstance(entry, dict):
                    continue
                dec_id = str(entry.get("id") or "").strip()
                if not dec_id:
                    continue
                decorations.append(
                    {
                        "id": dec_id,
                        "rate": _clamp(_safe_number(entry.get("rate"), 0.0), 0.0, 5.0),
                        "ts": _safe_int(entry.get("ts"), 0, 0),
                        "expire_ts": _safe_int(entry.get("expire_ts"), 0, 0),
                    }
                )
        player["decorations"] = decorations

        # --- 计数器 ---
        player["total_caught"] = _safe_int(raw.get("total_caught"), 0, 0)
        player["total_sold"] = _safe_int(raw.get("total_sold"), 0, 0)
        player["total_fed"] = _safe_int(raw.get("total_fed"), 0, 0)

        # --- 时间 ---
        player["last_fish_time"] = _safe_int(raw.get("last_fish_time"), 0, 0)
        # --- 体力（老存档没有这两个字段 → -1 表示「下次结算补满」）---
        player["stamina"] = _safe_int(raw.get("stamina"), -1, -1)
        player["stamina_ts"] = _safe_int(raw.get("stamina_ts"), 0, 0)
        for key in ("last_sign_date", "last_income_date"):
            value = raw.get(key, "")
            player[key] = value if isinstance(value, str) else ""

        player["rod_level"] = _safe_int(raw.get("rod_level"), 1, 1)

        # --- 成就 ---
        raw_ach = raw.get("achievements")
        achievements: list[str] = []
        if isinstance(raw_ach, list):
            for item in raw_ach:
                if isinstance(item, str) and item in ACHIEVEMENTS:
                    achievements.append(item)
        player["achievements"] = achievements

        _sync_collection(player)

    except Exception as e:  # pragma: no cover
        logger.warning(f"玩家 {user_id} 数据修复失败，已重置：{e}")
        return _default_player(user_id), False

    player["user_id"] = str(user_id)
    player["data_version"] = DATA_VERSION
    return player, migrated

def _instance_line(instance: dict[str, Any], with_value: bool = True) -> str:
    """一行完整信息：``🌟✨ 黄金锦鲤 ✨传说 🏆珍品 · 1280``。"""
    fish_id = instance.get("fish_id", "")
    fish = FISH_BY_ID.get(fish_id)
    if fish is None:
        return "❔ 未知"
    variant = instance.get("variant")
    variant_tag = ""
    name = fish["name"]
    if variant:
        vcfg = VARIANT_BY_ID.get(variant)
        if vcfg:
            variant_tag = f"{vcfg['emoji']}{vcfg['name']}·"
            name = f"{vcfg['name']}{name}"
    locked = "🔒" if instance.get("locked") else ""
    text = (
        f"{variant_tag}{_fish_emoji(fish)} {name} "
        f"{_rarity_tag(fish_id)} {_quality_tag(instance)}{locked}"
    )
    if with_value:
        text += f" · {_fmt_gold(_instance_value(instance))}"
    return text

def _attrs_line(instance: dict[str, Any], compact: bool = True) -> str:
    """三维属性一行展示。

    两个模式都**保留完整属性名**（肉质/灵性/光泽）——
    缩写成「肉/灵/光」虽然短，但玩家看不懂，尤其对不上投喂时涨的是哪一项。
    ``compact=True`` 只省掉数值之间的空格。
    """
    attrs = instance.get("attrs") or {}
    if compact:
        return " ".join(
            f"{label}{_safe_int(attrs.get(key), 0, 0)}"
            for key, label in ATTR_LABELS.items()
        )
    return "　".join(
        f"{label} {_safe_int(attrs.get(key), 0, 0)}" for key, label in ATTR_LABELS.items()
    )

def _sort_key(instance: dict[str, Any]) -> tuple[int, int, int]:
    """排序：变异 > 个体品质 > 鱼种品质 > 价值，均从高到低。"""
    return (
        0 if instance.get("variant") else 1,
        -QUALITY_RANK.get(instance.get("quality", ""), 0),
        -RARITY_RANK.get(_fish_rarity(instance.get("fish_id", "")), 0),
        -_instance_value(instance),
    )


# -----------------------------------------------------------------------------
# 内容表解析（collectible_defs / variant_defs / weather_defs / easter_egg_defs）
# 与 `_parse_fish_defs` 同一套容错风格：空行与 # 注释、全角标点、坏行跳过并告警。
# -----------------------------------------------------------------------------
def _defs_lines(raw: Any) -> list[str]:
    """把多行文本切成干净的行（空行与 `#` 注释丢掉，全角竖线转半角）。"""
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip().replace("｜", "|")
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _defs_num(value: Any, default: float = 0.0) -> float:
    """宽容的数字解析：支持 `1,200`、`1，200`、`12.5`。"""
    try:
        return float(str(value).replace(",", "").replace("，", "").strip())
    except (TypeError, ValueError):
        return float(default)


def _parse_collectible_defs(raw: Any, *, warn: Any = None) -> list[dict[str, Any]]:
    """解析 `collectible_defs`：``id|名称|emoji|权重|价值|说明``。"""
    rows: list[dict[str, Any]] = []
    bad = 0
    first_bad = ""
    for line in _defs_lines(raw):
        p = [x.strip() for x in line.split("|")]
        if len(p) < 4 or not p[0] or not p[1]:
            bad += 1
            first_bad = first_bad or line
            continue
        rows.append({
            "id": p[0],
            "name": p[1],
            "emoji": p[2] if len(p) > 2 else "🥫",
            "weight": max(0.0, _defs_num(p[3] if len(p) > 3 else 0, 0)),
            "value": int(round(_defs_num(p[4] if len(p) > 4 else 0, 0))),
            "desc": p[5] if len(p) > 5 else "",
        })
    if bad and warn:
        warn(f"collectible_defs 有 {bad} 行格式不对已跳过（首条：{first_bad[:40]}）")
    return rows


def _parse_variant_defs(raw: Any, *, warn: Any = None) -> list[dict[str, Any]]:
    """解析 `variant_defs`：``id|名称|emoji|相对权重|价值倍率|说明``。

    输出字段名与 `_game_data.VARIANTS` 保持一致（价值倍率键名是 ``mult``）。
    """
    rows: list[dict[str, Any]] = []
    bad = 0
    first_bad = ""
    for line in _defs_lines(raw):
        p = [x.strip() for x in line.split("|")]
        if len(p) < 5 or not p[0] or not p[1]:
            bad += 1
            first_bad = first_bad or line
            continue
        mult = _defs_num(p[4] if len(p) > 4 else 1.0, 1.0)
        rows.append({
            "id": p[0],
            "name": p[1],
            "emoji": p[2] if len(p) > 2 else "✨",
            "weight": max(0.0, _defs_num(p[3] if len(p) > 3 else 0, 0)),
            "mult": max(0.1, mult),
            "desc": p[5] if len(p) > 5 else "",
        })
    if bad and warn:
        warn(f"variant_defs 有 {bad} 行格式不对已跳过（首条：{first_bad[:40]}）")
    return rows


def _parse_weather_defs(raw: Any, *, warn: Any = None) -> list[dict[str, Any]]:
    """解析 `weather_defs`：``id|名称|emoji|权重|稀有度倍率|窗口倍率|运气|逃脱倍率|说明``。

    稀有度倍率写成 ``少见:1.4,稀有:1.5,传说:1.3``（留空 = 该天气不额外加成）。
    """
    rows: list[dict[str, Any]] = []
    bad = 0
    first_bad = ""
    for line in _defs_lines(raw):
        p = [x.strip() for x in line.split("|")]
        if len(p) < 4 or not p[0] or not p[1]:
            bad += 1
            first_bad = first_bad or line
            continue
        rarity_mult: dict[str, float] = {}
        for chunk in (p[4] if len(p) > 4 else "").replace("，", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            key, _, value = chunk.replace("：", ":").partition(":")
            key = key.strip()
            if key:
                rarity_mult[key] = _defs_num(value, 1.0)
        rows.append({
            "id": p[0],
            "name": p[1],
            "emoji": p[2] if len(p) > 2 else "🌤️",
            "weight": max(0.0, _defs_num(p[3] if len(p) > 3 else 0, 0)),
            "rarity_mult": rarity_mult,
            "window_mult": _defs_num(p[5] if len(p) > 5 else 1.0, 1.0),
            "luck": _defs_num(p[6] if len(p) > 6 else 0.0, 0.0),
            "escape_mult": _defs_num(p[7] if len(p) > 7 else 1.0, 1.0),
            "desc": p[8] if len(p) > 8 else "",
        })
    if bad and warn:
        warn(f"weather_defs 有 {bad} 行格式不对已跳过（首条：{first_bad[:40]}）")
    return rows


def _parse_easter_egg_defs(raw: Any, *, warn: Any = None) -> list[dict[str, Any]]:
    """解析 `easter_egg_defs`：``id|权重|文案|效果``。

    效果写成 ``gold=12`` / ``luck=0.08`` / ``note=1`` / ``heal_bait=1``（分号分隔多个）；
    ``note`` 与 ``heal_bait`` 是开关型效果，写 1/true/yes 都算开启。
    """
    rows: list[dict[str, Any]] = []
    bad = 0
    first_bad = ""
    for line in _defs_lines(raw):
        p = [x.strip() for x in line.split("|")]
        if len(p) < 3 or not p[0] or not p[2]:
            bad += 1
            first_bad = first_bad or line
            continue
        row: dict[str, Any] = {
            "id": p[0],
            "weight": max(0.0, _defs_num(p[1] if len(p) > 1 else 0, 0)),
            "text": p[2],
        }
        for chunk in (p[3] if len(p) > 3 else "").replace("；", ";").split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            key, _, value = chunk.replace("：", "=").partition("=")
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if key in ("note", "heal_bait"):
                row[key] = value.lower() in ("1", "true", "yes", "on", "y", "")
            elif key in ("gold", "luck"):
                row[key] = (
                    int(round(_defs_num(value, 0)))
                    if key == "gold"
                    else _defs_num(value, 0.0)
                )
        rows.append(row)
    if bad and warn:
        warn(f"easter_egg_defs 有 {bad} 行格式不对已跳过（首条：{first_bad[:40]}）")
    return rows


# -----------------------------------------------------------------------------
# 回复场景总表（唯一的「场景清单」）
#
# 为什么要有这张表：插件里**每一条回复**都有一个场景键（scene id），站长可以给
# 任意一条回复单独配按钮与文案。这张表是三件事的唯一依据：
#   1. 校验 button_defs / text_overrides 里的场景键（不认识的直接跳过并告警）
#   2. 编辑器的「💬 回复」页按它枚举全部场景（按指令分组）
#   3. test_local.py 用它做护栏：场景表 ↔ 文案表（_texts.py）↔ 代码里的回复出口
#      三者少了一个就报错，避免以后新增回复忘了登记
#
# 「继承」字段：parent 场景没被单独配置时，该场景沿用 parent 的按钮。
# 这是为了**升级前后默认行为逐字不变**：老版本的 5 个场景（cast/pull/bag/
# location/story）仍然存在、仍是内置默认按钮的拥有者，只是原来共用它们的那些
# 回复各自拿到了更细的场景键（例如 cast.hit / cast.junk / help.page 都继承 cast）。
# -----------------------------------------------------------------------------

#: 场景分组：(组 id, 标题, 说明)。编辑器按这个顺序分组展示
SCENE_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("cast", "🎣 下竿", "抛竿 / 连钓过程中的每一条回复"),
    ("pull", "🪝 拉线", "咬钩提示与拉线判定结果"),
    ("story", "❔ 小插曲", "水面上偶尔发生的小事件"),
    ("bag", "🎒 背包", "背包与锁定"),
    ("sell", "💰 卖鱼", "卖出 / 卖光光"),
    ("shop", "🛒 商店", "买鱼饵、买道具"),
    ("backpack", "🎒 扩容", "背包扩容"),
    ("rod", "🎣 鱼竿", "买竿与换竿"),
    ("bait", "🪱 换饵", "切换当前鱼饵"),
    ("location", "📍 钓点", "钓点列表、前往、解锁"),
    ("orders", "📋 订单", "每日订单"),
    ("aquarium", "🐠 水族馆", "放 / 取 / 卖 / 领 / 扩建"),
    ("item", "🎁 用道具", "饲料、培育、装饰"),
    ("collection", "📕 图鉴", "收集进度"),
    ("fishinfo", "🔍 查鱼", "查鱼 / 查钓点"),
    ("collectibles", "🧺 杂物", "杂物与纸条"),
    ("profile", "📇 档案", "等级 / 金币 / 统计"),
    ("stamina", "⚡ 体力", "体力查看"),
    ("sign", "📅 签到", "每日签到"),
    ("today", "🌤️ 今日", "天气与行情"),
    ("rank", "📊 排行", "群内排行榜"),
    ("help", "❓ 帮助", "帮助分页与用法提示"),
    ("custom", "🧩 自定义", "站长自定义命令"),
    ("extras", "🏷 称号与供奉", "自动补给 / 称号 / 香火供奉（v1.18.17 的后期金币回收口）"),
    ("system", "⚙️ 通用", "兜底报错、群播报与共用按钮组"),
)

#: 全部回复场景：(场景 id, 分组, 说明, 继承场景)。继承场景留空 = 默认没有按钮
REPLY_SCENES: tuple[tuple[str, str, str, str], ...] = (
    # ---- 共用按钮组（老版本的 5 个场景，默认按钮的拥有者）----
    ("cast", "system", "共用按钮组：下竿相关回复（未单独配置时都继承它）", ""),
    ("pull", "system", "共用按钮组：拉线相关回复（未单独配置时都继承它）", ""),
    ("bag", "system", "共用按钮组：背包相关回复（未单独配置时都继承它）", ""),
    ("location", "system", "共用按钮组：钓点相关回复（未单独配置时都继承它）", ""),
    ("story", "system", "共用按钮组：小插曲（按选项生成，{label}/{n} 是模板占位符）", ""),
    # ---- 下竿 ----
    ("cast.hit", "cast", "钓到鱼之后那条结果", "cast"),
    ("cast.junk", "cast", "这一竿钩上的是杂物", "cast"),
    ("cast.miss_none", "cast", "空竿：空钩没鱼理", ""),
    ("cast.miss_bait", "cast", "空竿：咬了一口又吐掉", ""),
    ("cast.miss_deep", "cast", "空竿：深水钓点鱼不开口", ""),
    ("cast.bait_note", "cast", "这一竿的鱼饵变化提示（挂在结果后面）", ""),
    ("cast.busy", "cast", "上一竿还在等「拉」，又抛了一竿", ""),
    ("cast.bad_bait", "cast", "指定的鱼饵不存在", ""),
    ("cast.no_stamina", "cast", "体力不够，抛不了竿", ""),
    ("cast.no_gold", "cast", "金币不够付钓费", ""),
    ("cast.bag_full", "cast", "背包满了，钓上来的鱼装不下", ""),
    ("cast.achievement", "cast", "这一竿解锁了新成就", ""),
    ("cast.milestone", "cast", "这一竿达成了累计里程碑", ""),
    ("cast.egg", "cast", "这一竿触发了彩蛋", ""),
    ("cast.save_failed", "cast", "存档失败提示（单竿）", ""),
    ("cast.multi_limit", "cast", "连钓次数超过上限", ""),
    ("cast.multi_busy", "cast", "还在等「拉」的时候想连钓", ""),
    ("cast.multi_bad_times", "cast", "连钓次数不是正整数", ""),
    ("cast.multi_no_stamina", "cast", "连钓体力不够", ""),
    ("cast.multi_no_bait", "cast", "连钓鱼饵不够", ""),
    ("cast.multi_no_gold", "cast", "连钓金币不够", ""),
    ("cast.multi_bag_full", "cast", "连钓途中背包满了", ""),
    ("cast.multi_summary", "cast", "连钓战果汇总（每次钓获一行）", ""),
    ("cast.multi_achievement", "cast", "连钓之后解锁的成就", ""),
    ("cast.multi_milestone", "cast", "连钓之后达成的里程碑", ""),
    ("cast.multi_save_failed", "cast", "连钓存档失败提示", ""),
    # ---- 拉线 ----
    ("pull.hook", "pull", "鱼咬钩了，提示在限定时间内发「拉」", "pull"),
    ("pull.confirm", "pull", "拉线成功受理的即时回执", ""),
    ("pull.none", "pull", "发「拉」的时候并没有鱼咬钩", ""),
    ("pull.timeout", "pull", "超时没拉，鱼吐钩跑了", ""),
    ("pull.escape", "pull", "拉到了但鱼挣脱跑了", ""),
    # ---- 小插曲 / 连载剧情 ----
    ("story.prompt", "story", "小插曲（或连载下一话）的提示与选项", "story"),
    ("story.recap", "story", "连载开头那行「第 N 话　上次：…」", ""),
    ("story.wrong_owner", "story", "想插手别人的小插曲", ""),
    ("story.none", "story", "当前没有需要决定的事", ""),
    ("story.expired", "story", "犹豫太久，插曲已经过去了", ""),
    ("story.bad_choice", "story", "插曲选项序号写得不对", ""),
    ("story.result", "story", "做出选择之后的结果", ""),
    # ---- 背包 ----
    ("bag.list", "bag", "背包内容（分页）", "bag"),
    ("bag.empty", "bag", "背包是空的", ""),
    ("lock.usage", "bag", "锁定/解锁的用法说明", ""),
    ("lock.bad_index", "bag", "锁定/解锁的序号超出背包范围", ""),
    ("lock.done", "bag", "锁定成功", ""),
    ("lock.all_done", "bag", "解锁成功（批量解锁全部）", ""),
    ("unlock.done", "bag", "解锁成功", ""),
    # ---- 卖鱼 ----
    ("sell.result", "sell", "卖出的结算结果", ""),
    ("sell.empty", "sell", "背包空空的，没东西可卖", ""),
    ("sell.removed", "sell", "「卖 垃圾」这个老玩法已去掉的提示", ""),
    ("sell.bad_index", "sell", "卖的序号超出背包范围", ""),
    ("sell.no_fish", "sell", "没有叫这个名字的鱼", ""),
    ("sell.missing", "sell", "背包里没有这种鱼", ""),
    ("sell.all_locked", "sell", "选中的鱼都锁着，卖光光不会动它们", ""),
    ("sell.nothing", "sell", "没有可卖的鱼（兜底提示）", ""),
    ("sell.locked_note", "sell", "卖完之后提示还有锁定的鱼留下", ""),
    # ---- 商店（v1.18.13 拆成三家：鱼竿店 / 道具店 / 鱼饵店）----
    # shop.list / shop.usage 保留成「共用按钮组」：两家货架、两条用法说明分别继承它，
    # 所以老配置里给 shop.list 配过的按钮照样生效（升级前后默认行为不变）。
    ("shop.list", "shop", "共用按钮组：货架（鱼饵店/道具店都继承它）", ""),
    ("shop.usage", "shop", "共用按钮组：商店用法说明", ""),
    ("shop.bait_list", "shop", "鱼饵店货架", "shop.list"),
    ("shop.item_list", "shop", "道具店货架", "shop.list"),
    ("shop.bait_usage", "shop", "鱼饵店用法说明", "shop.usage"),
    ("shop.item_usage", "shop", "道具店用法说明", "shop.usage"),
    ("shop.moved", "shop", "老的「商店」命令已拆成三家（指路）", "shop.usage"),
    ("shop.wrong_shop_rod", "shop", "在鱼饵/道具店里写鱼竿名字", ""),
    ("shop.wrong_shop_item", "shop", "在鱼饵店里写道具名字", ""),
    ("shop.wrong_shop_bait", "shop", "在道具店里写鱼饵名字", ""),
    ("shop.free_hook", "shop", "空钩不用买", ""),
    ("shop.not_found", "shop", "这家店里没有这件东西", ""),
    ("shop.locked", "shop", "等级 / 鱼竿不够，还没上架", ""),
    ("shop.no_gold_bait", "shop", "买鱼饵金币不足", ""),
    ("shop.no_gold_item", "shop", "买道具金币不足", ""),
    ("shop.bought", "shop", "买到东西了", ""),
    # ---- 背包扩容 ----
    ("backpack.upgraded", "backpack", "背包扩容成功", ""),
    ("backpack.max", "backpack", "背包已经扩到最大", ""),
    ("backpack.no_gold", "backpack", "扩容金币不足", ""),
    # ---- 鱼竿 ----
    ("rod.list", "rod", "鱼竿列表", ""),
    ("rod.not_found", "rod", "没有这款鱼竿", ""),
    ("rod.owned", "rod", "已经有这款竿了", ""),
    ("rod.level_low", "rod", "等级不够，买不了这款竿", ""),
    ("rod.no_gold", "rod", "买竿金币不足", ""),
    ("rod.bought", "rod", "买到鱼竿了", ""),
    ("rod.not_owned", "rod", "还没买这款竿", ""),
    ("rod.equipped", "rod", "换好竿了", ""),
    # ---- 换饵 ----
    ("bait.equipped", "bait", "换饵成功", ""),
    ("bait.not_found", "bait", "没有这种鱼饵", ""),
    ("bait.locked", "bait", "这种鱼饵还没解锁", ""),
    ("bait.not_owned", "bait", "背包里没有这种鱼饵", ""),
    ("bait.same", "bait", "当前用的就是它", ""),
    ("bait.empty", "bait", "没有鱼饵可用", ""),
    # ---- 钓点 ----
    ("location.list", "location", "钓点列表", "location"),
    ("location.not_found", "location", "没有这个钓点", ""),
    ("location.locked", "location", "钓点还没解锁", ""),
    ("location.already_here", "location", "已经在这个钓点了", ""),
    ("location.moved", "location", "前往钓点成功", ""),
    ("location.unlocked", "location", "解锁钓点成功", ""),
    ("location.unlock_go", "location", "解锁并直接前往成功", ""),
    ("location.unlock_need", "location", "还不能去：条件没满足", ""),
    # ---- 订单 ----
    ("orders.locked", "orders", "等级不够，还看不到订单", ""),
    ("orders.usage", "orders", "订单用法说明", ""),
    ("orders.list", "orders", "当前订单列表", ""),
    ("orders.submit_result", "orders", "交单结果", ""),
    # ---- 水族馆 ----
    ("aquarium.view", "aquarium", "水族馆总览", ""),
    ("aquarium.usage", "aquarium", "水族馆用法说明", ""),
    ("aquarium.max", "aquarium", "水族馆已经扩到最大", ""),
    ("aquarium.no_gold", "aquarium", "扩建金币不足", ""),
    ("aquarium.upgraded", "aquarium", "扩建成功", ""),
    ("aquarium.income_start", "aquarium", "鱼塘开始计产了", ""),
    ("aquarium.income_empty", "aquarium", "空缸没有产出", ""),
    ("aquarium.income_wait", "aquarium", "产出还不够，再等等", ""),
    ("aquarium.income", "aquarium", "领到了挂机产出", ""),
    ("aquarium.feed_usage", "aquarium", "投喂用法提示（要用「用」指令）", ""),
    ("aquarium.put_usage", "aquarium", "放鱼用法说明", ""),
    ("aquarium.full", "aquarium", "水族馆已满", ""),
    ("aquarium.put_done", "aquarium", "放鱼结果", ""),
    ("aquarium.take_usage", "aquarium", "取鱼用法说明", ""),
    ("aquarium.take_done", "aquarium", "取鱼结果", ""),
    ("aquarium.sell_usage", "aquarium", "卖馆藏用法说明", ""),
    ("aquarium.sell_done", "aquarium", "卖馆藏结果", ""),
    ("aquarium.bad_slot", "aquarium", "栏位号不对", ""),
    # ---- 用道具 ----
    ("item.empty", "item", "背包里没有道具", ""),
    ("item.usage", "item", "道具用法说明", ""),
    ("item.missing", "item", "没有这个道具", ""),
    ("item.used", "item", "道具使用成功", ""),
    ("item.deco_disabled", "item", "本服没有开放装饰位", ""),
    ("item.deco_full", "item", "装饰位满了", ""),
    ("item.deco_used", "item", "装饰已生效", ""),
    ("item.breed_no_fish", "item", "空缸不能培育", ""),
    ("item.breed_usage", "item", "培育用法说明", ""),
    ("item.breed_bad_slot", "item", "培育的栏位号不对", ""),
    ("item.breed_failed", "item", "培育没能用出去", ""),
    ("item.breed_done", "item", "培育结果", ""),
    ("item.feed_no_fish", "item", "空缸不能投喂", ""),
    ("item.feed_usage", "item", "投喂用法说明", ""),
    ("item.feed_full", "item", "这些栏位都喂满了", ""),
    ("item.feed_missing", "item", "饲料已经用完了", ""),
    ("item.feed_done", "item", "投喂结果", ""),
    # 洗髓丹（quality_reroll）：和培育/投喂分开，文案与按钮都能单独配
    ("item.reroll_no_fish", "item", "空缸不能洗髓", ""),
    ("item.reroll_usage", "item", "洗髓用法说明", ""),
    ("item.reroll_bad_slot", "item", "洗髓的栏位号不对", ""),
    ("item.reroll_failed", "item", "洗髓没能用出去", ""),
    ("item.reroll_done", "item", "洗髓结果", ""),
    # ---- 图鉴 ----
    ("collection.view", "collection", "图鉴总览", ""),
    ("collection.detail", "collection", "图鉴完整清单（分页）", ""),
    ("collection.location", "collection", "某个钓点的收集进度", ""),
    # ---- 查鱼 ----
    ("fishinfo.detail", "fishinfo", "鱼或钓点的详情", ""),
    ("fishinfo.location_detail", "fishinfo", "钓点里的鱼种清单", ""),
    ("fishinfo.not_found", "fishinfo", "什么都没查到", ""),
    ("fishinfo.index", "fishinfo", "/钓鱼 查 的用法（能查哪些东西）", ""),
    ("fishinfo.multi_match", "fishinfo", "一个词命中好几样东西", ""),
    ("fishinfo.empty", "fishinfo", "这个钓点还没有配置鱼种", ""),
    # ---- 杂物 ----
    ("collectibles.view", "collectibles", "杂物与纸条收集", ""),
    # ---- 档案 / 体力 / 签到 / 今日 ----
    ("profile.view", "profile", "档案（等级 / 金币 / 统计）", ""),
    ("profile.renamed", "profile", "老指令「金币」改名的提示", ""),
    ("stamina.view", "stamina", "体力状态", ""),
    ("sign.done", "sign", "今天已经签到过了", ""),
    ("sign.result", "sign", "签到成功", ""),
    ("today.view", "today", "今日天气与行情", ""),
    # ---- 排行 ----
    ("rank.empty", "rank", "全服还没有排行数据", ""),
    ("rank.no_data", "rank", "这个榜单还没有数据", ""),
    ("rank.view", "rank", "排行榜内容", ""),
    # ---- 帮助 / 自定义 / 通用 ----
    ("help.page", "help", "帮助分页", "cast"),
    ("help.unknown", "help", "不认识的用法（兜底提示）", ""),
    ("custom.send", "custom", "自定义命令「发送:」的回复", ""),
    ("system.error", "system", "操作出错的兜底回复", ""),
    ("broadcast.catch", "system", "群播报：有人钓到了鱼", ""),
    # ---- 自动补给 / 称号 / 香火供奉（v1.18.17 的后期玩法与金币回收口）----
    ("auto.view", "extras", "自动补给设置与可选项", ""),
    ("auto.on", "extras", "设定好自动补给的手气道具", ""),
    ("auto.off", "extras", "关掉手气道具的自动补给", ""),
    ("auto.bad", "extras", "指定的自动补给道具不合法", ""),
    ("title.list", "extras", "称号清单（后期金币回收）", ""),
    ("title.bought", "extras", "买下并戴上称号", ""),
    ("title.owned", "extras", "这个称号早就买过了", ""),
    ("title.not_found", "extras", "没有这个称号", ""),
    ("title.no_gold", "extras", "金币不够买称号", ""),
    ("title.not_owned", "extras", "还没买这个称号就想戴", ""),
    ("title.equipped", "extras", "换上称号", ""),
    ("title.disabled", "extras", "本服没配称号表", ""),
    ("offering.done", "extras", "香火供奉成功（限时挂机 + 手气加成）", ""),
    ("offering.no_gold", "extras", "金币不够供奉", ""),
)

#: 场景 id 列表（button_defs / text_overrides 只认这些键）
SCENE_IDS: tuple[str, ...] = tuple(row[0] for row in REPLY_SCENES)

#: 允许出现在 button_defs / text_overrides 里的场景（旧名字，保持向后兼容）
BUTTON_SCENES: tuple[str, ...] = SCENE_IDS

#: 场景 -> 继承场景（没单独配按钮时用父场景的）
SCENE_PARENT: dict[str, str] = {
    row[0]: row[3] for row in REPLY_SCENES if row[3]
}

#: 场景 -> 说明 / 分组
SCENE_DESC: dict[str, str] = {row[0]: row[2] for row in REPLY_SCENES}
SCENE_GROUP: dict[str, str] = {row[0]: row[1] for row in REPLY_SCENES}
SCENE_LABEL: dict[str, str] = {row[0]: row[2] for row in REPLY_SCENES}

#: 每个分组「基础场景」：整组没单独配按钮时都回退到它（v1.18.17，站长要
#: 「所有需要的地方都加按钮」）。以前只有手写的十几条父子关系，于是
#: ``cast.miss_none`` / ``aquarium.full`` / ``item.feed_done`` 这类场景**一个按钮都没有**。
BUTTON_BASE_BY_GROUP: dict[str, str] = {
    "cast": "cast",
    "pull": "pull",
    "story": "story.result",     # story.prompt 自己有动态按钮（{label}/{n}），不受影响
    "bag": "bag",
    "sell": "sell.result",
    "shop": "shop.list",
    "backpack": "backpack.upgraded",
    "rod": "rod.list",
    "bait": "bait.equipped",
    "location": "location",
    "orders": "orders.list",
    "aquarium": "aquarium.view",
    "item": "item.used",
    "collection": "collection.view",
    "fishinfo": "fishinfo.detail",
    "collectibles": "collectibles.view",
    "profile": "profile.view",
    "stamina": "stamina.view",
    "sign": "sign.done",
    "today": "today.view",
    "rank": "rank.view",
    "help": "help.page",
    "custom": "",
    "extras": "title.list",
    "system": "cast",            # 报错 / 群播报：给「再来一竿 / 看背包」最实用
}

#: 场景 -> 按钮兜底场景（自己 → 同组基础场景；基础场景本身不需要兜底）
SCENE_BUTTON_BASE: dict[str, str] = {
    scene_id: base
    for scene_id, group in SCENE_GROUP.items()
    for base in (BUTTON_BASE_BY_GROUP.get(group, ""),)
    if base and base != scene_id and base in SCENE_GROUP
}

#: 继承关系反查：父场景 -> 子场景（编辑器要提示「这几个场景在共用它」）
SCENE_CHILDREN: dict[str, tuple[str, ...]] = {}
for _scene_id, _parent in SCENE_PARENT.items():
    SCENE_CHILDREN[_parent] = SCENE_CHILDREN.get(_parent, ()) + (_scene_id,)

#: 每个场景一行最多摆几个按钮的**内置**默认值。默认值下与历史版本逐项一致：
#: cast 有 5 个按钮 -> 3 + 2 两行；bag/location 各 3 个 -> 一行；pull 1 个 -> 一行。
#: 键 "*" = 其余所有场景的默认值（配置项 button_layout 可以覆盖它们）
BUILTIN_BUTTONS_PER_ROW: dict[str, int] = {"*": 4, "story": 1}

#: 生效的「每行几个」（配置接管后就地更新；_views.py 读它排版）
BUTTONS_PER_ROW: dict[str, int] = dict(BUILTIN_BUTTONS_PER_ROW)

#: 单行按钮个数上限（防止站长把键盘排爆）
BUTTONS_PER_ROW_MAX = 5


def scene_rows_per_row(scene: str) -> int:
    """某个场景一行摆几个按钮（场景自身 > "*" 全局 > 3）。"""
    value = BUTTONS_PER_ROW.get(scene)
    if value is None:
        value = BUTTONS_PER_ROW.get("*")
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 3
    return max(1, min(BUTTONS_PER_ROW_MAX, number))


def _parse_button_layout(raw: Any, *, warn: Any = None) -> dict[str, int]:
    """解析 `button_layout`：``场景|每行几个``（``*`` = 全局默认）。

    坏行跳过并告警；返回 ``{场景: 个数}``（可能含 ``"*"``）。
    """
    rows: dict[str, int] = {}
    bad = 0
    first_bad = ""
    for line in _defs_lines(raw):
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            bad += 1
            first_bad = first_bad or line
            continue
        scene = parts[0].lower()
        if scene != "*" and scene not in SCENE_IDS:
            bad += 1
            first_bad = first_bad or line
            continue
        try:
            number = int(float(parts[1]))
        except (TypeError, ValueError):
            bad += 1
            first_bad = first_bad or line
            continue
        if number < 1 or number > BUTTONS_PER_ROW_MAX:
            bad += 1
            first_bad = first_bad or line
            continue
        rows[scene] = number
    if bad and warn:
        warn(
            f"button_layout 有 {bad} 行不合法已跳过（首条：{first_bad[:40]}），"
            f"每行个数只能是 1~{BUTTONS_PER_ROW_MAX}"
        )
    return rows

#: 样式别名 -> QQ 官方键盘的 ``render_data.style``（内置默认只用到 0 和 1）
#:
#: 官方文档《消息按钮》写得很死：``render_data.style | int | 是 | 按钮样式：0 灰色线框，1 蓝色线框``
#: （bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html，2026-07-21 版）。
#: v1.13.1 之前这张表写成 default=1 / primary=4 —— 那是猜的：结果是「默认（灰）」的按钮
#: 在 QQ 里显示成蓝色线框，而 primary=4 根本不是文档里的取值。现在按官方取值来，
#: 谁想要别的颜色，直接在样式列写数字（0~255 原样透传）。
BUTTON_STYLE_ALIASES: dict[str, int] = {
    "": 0,
    "default": 0,
    "默认": 0,
    "灰": 0,
    "gray": 0,
    "grey": 0,
    "primary": 1,
    "主要": 1,
    "蓝": 1,
    "blue": 1,
}

#: 按钮点击后发送的指令，第一个词必须在这里（否则点了没反应，属于死按钮）。
#: 与 main.py 子命令分派的 keyword 对齐；`/钓鱼` 单独出现与 `/钓鱼 <数字>` 也算合法。
#:
#: ⚠️ 这是**可变集合**：main.py 定义完 ``SUBCOMMAND_KEYWORDS`` 之后会把整张子命令表
#: 并进来（见 `_sync_button_command_words`），所以「路由认识的写法」与「按钮能指向的
#: 写法」永远不会再各写一份（v1.18.17 之前就是各写一份，于是 `/钓鱼 称号` 这种新命令
#: 的按钮会被当死按钮丢掉）。
BUTTON_COMMAND_WORDS: set[str] = {
    "帮助", "菜单", "指令", "背包", "包", "bag", "鱼篓",
    "卖", "卖鱼", "卖光光", "卖光", "清空", "全卖", "空背包", "sellall", "一键卖出",
    "图鉴", "收集", "collection", "水族馆", "馆", "缸", "aquarium",
    "扩建背包", "扩容", "背包扩容", "鱼篓扩容",
    "锁定", "锁", "lock", "解锁", "解", "unlock",
    "今日", "天气", "行情", "today", "weather", "market",
    "排行", "排行榜", "榜", "rank", "top",
    "商店", "铺子", "shop",
    "鱼竿", "竿", "rod",
    # 三家店（v1.18.13）：道具店与鱼饵店各有自己的名字，别再写回「商店」
    "道具", "道具店", "物品", "item", "items",
    "鱼饵", "鱼饵店", "饵店", "饵",
    "用", "使用", "use", "查", "查询", "鱼", "资料", "fish", "info",
    "事件", "插曲", "选择", "event",
    "换饵", "换鱼饵", "装备饵", "上饵", "bait",
    "体力", "体力值", "活力", "stamina",
    "档案", "profile", "me", "金币", "gold",
    "签到", "sign", "订单", "任务", "order", "orders",
    "钓点", "地点", "地图", "map", "location",
    "杂物", "漂流瓶", "收集品", "collect",
    "拉", "去", "前往", "go", "扩建", "领取", "收益", "投喂", "放入", "取出", "卖出",
    # v1.18.0 的短写法（按钮可以直接指向它们）
    "放", "养", "取", "拿", "领", "收租", "喂", "洗", "洗髓",
    # 「买」是智能买（自动认三家店），按钮可以直接用它
    "交", "交单", "交货", "购买", "买", "装备", "换竿", "换鱼竿",
}


def _fill_button_text(template: Any, label: str, n: int = 0) -> str:
    """替换按钮模板里的 ``{label}`` / ``{n}``（不用 str.format，避免文案里的花括号报错）。"""
    return str(template).replace("{label}", str(label)).replace("{n}", str(n))


def _parse_button_style(value: Any) -> int | None:
    """解析按钮样式；不认识就返回 None（调用方回退默认）。"""
    # 不能写 ``value or ""``：数字 0 是合法样式（QQ 的 0 号色），会被 or 当成「没写」
    raw = "" if value is None or value is False else str(value).strip().lower()
    if raw in BUTTON_STYLE_ALIASES:
        return BUTTON_STYLE_ALIASES[raw]
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 255 else None


#: 按钮默认样式（配置没写时的兜底，= QQ 键盘的 render_data.style：0 = 灰色线框）
BUTTON_STYLE_DEFAULT = 0
#: 样式策略：``table`` = 按按钮表里每行自己写的（历史行为）/ ``uniform`` = 全部统一
BUTTON_STYLE_MODES: tuple[str, ...] = ("按按钮表", "统一")
BUTTON_STYLE_MODE_ALIASES: dict[str, str] = {
    "按按钮表": "table", "逐条": "table", "按行": "table",
    "table": "table", "per_button": "table", "row": "table",
    "统一": "uniform", "全部统一": "uniform", "统一设置": "uniform",
    "uniform": "uniform", "all": "uniform", "one": "uniform",
}


def _parse_button_style_mode(value: Any) -> str:
    """解析 ``button_style_mode``；不认识的一律回退 ``table``（= 历史行为）。"""
    raw = str(value or "").strip().lower()
    return BUTTON_STYLE_MODE_ALIASES.get(raw, "table")


def _apply_button_style_policy(
    rows: dict[str, list[tuple[str, str, int]]], mode: Any, style: Any
) -> dict[str, list[tuple[str, str, int]]]:
    """按策略统一按钮样式：``统一`` 时把每一行都换成 ``style``（认不出就用默认样式）。

    只看**样式**这一列，文案与指令原样保留，所以开「统一」再关掉不会丢东西。
    """
    if _parse_button_style_mode(mode) != "uniform":
        return rows
    uniform = _parse_button_style(style)
    if uniform is None:
        uniform = BUTTON_STYLE_DEFAULT
    return {
        scene: [(label, data, uniform) for label, data, _old in items]
        for scene, items in rows.items()
    }


#: 站长自己配的「命令别名 / 自定义命令」里那些词：按钮也可以指向它们（原地更新）。
#: 由 main 在应用配置时填进来 —— 不然站长给自定义命令配了按钮，会被这里当死按钮丢掉。
BUTTON_COMMAND_EXTRA: set[str] = set()


def _button_command_ok(data: str) -> bool:
    """这一行「点击后发送」是不是本插件认识的指令（防止出现点了没反应的死按钮）。"""
    text = str(data or "").strip()
    if text == "/钓鱼":
        return True
    if not text.startswith("/钓鱼"):
        return False
    rest = text[len("/钓鱼"):].strip()
    if not rest:
        return True
    head = rest.split()[0]
    if head.isdigit():          # /钓鱼 10 = 连钓 10 次
        return True
    return head in BUTTON_COMMAND_WORDS or head in BUTTON_COMMAND_EXTRA


def _parse_button_defs(
    raw: Any, *, warn: Any = None, default_style: Any = None
) -> dict[str, list[tuple[str, str, int]]]:
    """解析 `button_defs`：``场景|按钮文案|点击后发送|样式``。

    返回 ``{场景: [(文案, 指令, 样式), ...]}``。坏行（字段不够、场景不认识、
    指令不认识）跳过并告警；``{label}``/``{n}`` 原样保留给 story 模板用。

    ``default_style`` = 这一行**没写样式**时用哪个（不传就是历史行为：``default``）。
    """
    rows: dict[str, list[tuple[str, str, int]]] = {}
    bad = 0
    first_bad = ""
    for line in _defs_lines(raw):
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 3 or not parts[0] or not parts[1] or not parts[2]:
            bad += 1
            first_bad = first_bad or line
            continue
        scene = parts[0].lower()
        if scene not in BUTTON_SCENES:
            bad += 1
            first_bad = first_bad or line
            continue
        label, data = parts[1], parts[2]
        if not _button_command_ok(data):
            bad += 1
            first_bad = first_bad or line
            continue
        # 样式列：留空 / 写坏都算「没写」，用全局兜底样式（默认行为 = 1，历史一致）
        fallback = _parse_button_style(default_style)
        if fallback is None:
            fallback = BUTTON_STYLE_DEFAULT
        raw_style = parts[3].strip() if len(parts) > 3 else ""
        style = _parse_button_style(raw_style) if raw_style else None
        if style is None:
            style = fallback
        rows.setdefault(scene, []).append((label, data, style))
    if bad and warn:
        warn(f"button_defs 有 {bad} 行不合法已跳过（首条：{first_bad[:40]}）")
    return rows


# -----------------------------------------------------------------------------
# 命令别名（command_aliases）与自定义命令（custom_commands）
#   别名：  规范子命令|别名1,别名2
#   自定义：命令名|发送:文本   或   命令名|执行:子命令;子命令 参数
#
# 设计红线（避免站长配出「死命令」）：
#   * 别名只**增加**，绝不覆盖内置写法——内置分派链一行都不用改，默认配置下
#     运行时别名表是**空**的，因此升级后行为逐字不变（等价性测试卡死这一点）
#   * 自定义命令只在「内置子命令都不认识」时才会被匹配，且 `执行:` 的目标
#     必须是内置子命令 —— 于是自定义命令之间天然无法互相调用（防递归）
# -----------------------------------------------------------------------------
#: 自定义命令支持的动作（只做两种：够用、可控、不易配错）
CUSTOM_COMMAND_ACTIONS: tuple[str, ...] = ("发送", "执行")
#: 自定义命令条数上限（防止把配置写爆）
CUSTOM_COMMAND_MAX_ROWS = 50
#: 自定义命令「名字」「内容」长度上限
CUSTOM_COMMAND_NAME_MAX = 16
CUSTOM_COMMAND_BODY_MAX = 200
#: 单个子命令最多挂几个别名、单个别名最长几个字
COMMAND_ALIAS_MAX_PER_ROW = 20
COMMAND_ALIAS_MAX_LEN = 12


def _alias_words(value: Any) -> list[str]:
    """把「别名1,别名2」切成列表（半角逗号、全角逗号、顿号都认）。"""
    text = str(value or "")
    for sep in ("，", "、"):
        text = text.replace(sep, ",")
    return [x.strip() for x in text.split(",") if x.strip()]


def _alias_word_problem(word: str) -> str | None:
    """别名/命令名「长得不像话」的检查；没问题返回 None。"""
    if not word:
        return "写了个空的别名"
    if any(ch.isspace() for ch in word):
        return f"「{word}」里有空格（一个别名只能是连续的一段文字）"
    if word.isdigit():
        return f"「{word}」是纯数字（会和 /钓鱼 连钓写法冲突）"
    if len(word) > COMMAND_ALIAS_MAX_LEN:
        return f"「{word}」太长（上限 {COMMAND_ALIAS_MAX_LEN} 字）"
    return None


def _command_owners(
    keywords: dict[str, tuple[str, ...]] | list[str] | tuple[str, ...] | set[str],
) -> tuple[dict[str, str], set[str]]:
    """由关键词表算出 (写法 → 规范子命令, 全部内置写法)。

    ``keywords`` 既可以是 ``{规范名: (写法...)}``，也可以是平铺的写法集合
    （自定义命令表的 ``执行:`` 只用得着后者）。
    """
    owner: dict[str, str] = {}
    flat: set[str] = set()
    items = keywords.items() if isinstance(keywords, dict) else ((w, (w,)) for w in keywords)
    for canonical, words in items:
        canonical = str(canonical)
        flat.add(canonical)
        owner.setdefault(canonical, canonical)
        for word in words or ():
            word = str(word)
            flat.add(word)
            owner.setdefault(word, canonical)
    return owner, flat


def _build_command_aliases(
    raw: Any,
    keywords: dict[str, tuple[str, ...]],
    *,
    reserved: Any = None,
    warn: Any = None,
) -> tuple[dict[str, str], list[str]]:
    """解析 ``command_aliases``，返回 ``(运行时别名表, 问题列表)``。

    运行时别名表是 ``别名 -> 规范子命令``，只会装**新增**的别名：

    * 行内写的、本来就是内置写法的词 → 静默忽略（分派链自己认，本来就有效）
    * 与**别的**子命令的内置写法撞名 → 跳过 + 记问题（不许把「背包」映射到别处）
    * 目标子命令不存在 / 行格式不对 / 别名不合规 → 跳过 + 记问题
    * 两条配置行争同一个别名 → 先到先得，后到的记问题
    * 与鱼饵同名的别名 → 跳过 + 记问题（下竿时会先被当成鱼饵接走）

    参数 ``reserved`` 是「会被更靠前的分支接走」的词（小写），例如鱼饵名。
    """
    owner, flat = _command_owners(keywords)
    reserved_set = {str(x).strip().lower() for x in (reserved or ()) if str(x).strip()}
    merged: dict[str, str] = {}
    problems: list[str] = []
    for line in _defs_lines(raw):
        parts = [x.strip() for x in line.split("|")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            problems.append(f"行格式不对（应为「规范子命令|别名,别名」）：{line[:40]}")
            continue
        canonical, alias_text = parts
        if canonical not in owner:
            problems.append(f"目标子命令不存在：{canonical}（该行已跳过）")
            continue
        words = _alias_words(alias_text)
        if not words:
            problems.append(f"「{canonical}」这一行没写别名（该行已跳过）")
            continue
        if len(words) > COMMAND_ALIAS_MAX_PER_ROW:
            problems.append(
                f"「{canonical}」一行写了 {len(words)} 个别名，"
                f"只保留前 {COMMAND_ALIAS_MAX_PER_ROW} 个"
            )
            words = words[:COMMAND_ALIAS_MAX_PER_ROW]
        for word in words:
            low = word.lower()
            if low == canonical.lower():
                continue
            bad = _alias_word_problem(word)
            if bad:
                problems.append(bad)
                continue
            home = owner.get(low) or owner.get(word)
            if home is not None:
                if home != canonical:
                    problems.append(
                        f"别名「{word}」已经是「{home}」的内置写法，"
                        f"不能再给「{canonical}」（已跳过）"
                    )
                continue
            if low in reserved_set:
                problems.append(f"别名「{word}」和鱼饵/道具重名，下竿时会被先接走（已跳过）")
                continue
            taken = merged.get(low)
            if taken is not None:
                if taken != canonical:
                    problems.append(
                        f"别名「{word}」被「{taken}」和「{canonical}」同时占用，"
                        f"只认先写的「{taken}」（已跳过）"
                    )
                continue
            merged[low] = canonical
    if problems and warn:
        warn(f"有 {len(problems)} 处已跳过（首条：{problems[0]}）")
    return merged, problems


def _split_command_pieces(body: Any) -> list[str]:
    """把 ``执行:`` 的内容切成一条条子命令（半角/全角分号、换行都算分隔）。"""
    text = str(body or "").replace("；", ";").replace("\r", "\n").replace("\n", ";")
    return [x.strip() for x in text.split(";") if x.strip()]


def _parse_custom_commands(
    raw: Any,
    keywords: dict[str, tuple[str, ...]] | set[str] | tuple[str, ...] | list[str],
    *,
    reserved: Any = None,
    warn: Any = None,
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """解析 ``custom_commands``，返回 ``(自定义命令表, 问题列表)``。

    表是 ``命令名(小写) -> (动作, 内容)``；动作只认 ``发送`` / ``执行``：

    * ``发送:文本`` —— 直接回复，支持 ``{金币}`` 之类占位符（原样存着，发的时候再替换）
    * ``执行:子命令 参数;子命令`` —— 依次走一遍**内置**子命令分派

    校验不过的行一律跳过并记问题：

    * 命令名与内置子命令/别名/其它自定义命令/鱼饵重名（内置永远优先）
    * 名字带空格或斜杠、纯数字、超长；内容超长
    * ``执行:`` 指向不存在的子命令 → 因为只认内置写法，所以自定义命令之间
      不可能互相调用（防递归），也不会出现「命令套命令」的死循环
    * 最多 ``CUSTOM_COMMAND_MAX_ROWS`` 条
    """
    owner, flat = _command_owners(keywords)
    reserved_set = {str(x).strip().lower() for x in (reserved or ()) if str(x).strip()}
    out: dict[str, tuple[str, str]] = {}
    problems: list[str] = []
    lines = _defs_lines(raw)
    if len(lines) > CUSTOM_COMMAND_MAX_ROWS:
        problems.append(
            f"最多 {CUSTOM_COMMAND_MAX_ROWS} 条，后面的 {len(lines) - CUSTOM_COMMAND_MAX_ROWS} 条已忽略"
        )
        lines = lines[:CUSTOM_COMMAND_MAX_ROWS]
    for line in lines:
        parts = [x.strip() for x in line.split("|")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            problems.append(f"行格式不对（应为「命令名|动作:内容」）：{line[:40]}")
            continue
        name, spec = parts
        low = name.lower()
        if "/" in name or any(ch.isspace() for ch in name):
            problems.append(f"命令名「{name}」不能有空格或斜杠")
            continue
        if name.isdigit():
            problems.append(f"命令名「{name}」不能是纯数字（会和连钓写法冲突）")
            continue
        if len(name) > CUSTOM_COMMAND_NAME_MAX:
            problems.append(f"命令名「{name}」太长（上限 {CUSTOM_COMMAND_NAME_MAX} 字）")
            continue
        if low in flat or name in flat:
            problems.append(f"命令名「{name}」和内置子命令/写法重名（内置优先，已跳过）")
            continue
        if low in reserved_set:
            problems.append(f"命令名「{name}」和已有的别名/鱼饵重名（已跳过）")
            continue
        if low in out:
            problems.append(f"命令名「{name}」写了两次，只认第一条")
            continue
        head, _, content = spec.replace("：", ":").partition(":")
        action = head.strip().lower()
        content = content.strip()
        if action in ("send", "发送"):
            action = "发送"
        elif action in ("run", "执行"):
            action = "执行"
        else:
            problems.append(f"「{name}」的动作「{head.strip()[:12]}」不认识（只支持 发送: / 执行:）")
            continue
        if not content:
            problems.append(f"「{name}」的 {action}: 后面是空的")
            continue
        if len(content) > CUSTOM_COMMAND_BODY_MAX:
            problems.append(
                f"「{name}」的内容太长（{len(content)} 字，上限 {CUSTOM_COMMAND_BODY_MAX}）"
            )
            continue
        if action == "执行":
            pieces = _split_command_pieces(content)
            missing = [
                piece for piece in pieces
                if (piece.split() or [""])[0].lower() not in
                {w.lower() for w in flat}
            ]
            if not pieces or missing:
                problems.append(
                    f"「{name}」里的子命令不存在：{(missing or ['(空)'])[0][:20]}"
                    f"（执行: 只能写内置子命令）"
                )
                continue
        out[low] = (action, content)
    if problems and warn:
        warn(f"有 {len(problems)} 条已跳过（首条：{problems[0]}）")
    return out, problems


def _fill_custom_text(template: Any, values: dict[str, Any] | None = None) -> str:
    """替换自定义命令文本里的 ``{占位符}``。

    不认识的占位符**原样保留**（不用 ``str.format``，避免文案里出现别的花括号就报错）。
    """
    text = str(template or "")
    for key, value in (values or {}).items():
        text = text.replace("{" + str(key) + "}", str(value))
    return text
