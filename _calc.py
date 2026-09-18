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

def _parse_effects(text: str) -> dict[str, float]:
    """解析道具效果串：``meat=2;spirit=1;quality_up=0.35``。"""
    effects: dict[str, float] = {}
    for token in (text or "").split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip()
        if key not in ("meat", "spirit", "sheen", "quality_up", "value_up", "heal"):
            continue
        effects[key] = _safe_number(value.strip(), 0.0)
    return effects

def _parse_item_defs(raw: Any) -> dict[str, dict[str, Any]]:
    """解析道具定义。返回 {item_id: {...}}。"""
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
        }
    return items

def _parse_aquarium_slots(raw: Any) -> list[dict[str, Any]]:
    """解析水族馆扩建栏位。"""
    slots: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS["aquarium_slots"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        slots.append({"name": parts[0], "price": max(0, _to_int(parts[1], 0))})
    return slots

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
    """``普通:0.8-1.0:⚪,优良:1.0-1.35:🟢`` → [(名称, 下限, 上限, emoji)]。"""
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
            }
        )
    if not rods:
        rods = [
            {"id": "bamboo", "name": "竹竿", "emoji": "🎋", "price": 0,
             "value_bonus": 0.0, "luck_bonus": 0.0, "unlock_level": 1,
             "desc": "备用的旧竿"}
        ]
    # 确保有免费的入门竿
    if not any(r["price"] <= 0 for r in rods):
        rods.insert(
            0,
            {"id": "bamboo", "name": "竹竿", "emoji": "🎋", "price": 0,
             "value_bonus": 0.0, "luck_bonus": 0.0, "unlock_level": 1,
             "desc": "备用的旧竿"},
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
        "equipped_bait": "none",
        "items": {},           # item_id -> 数量（含商店道具与钓上来的杂物）
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
        "last_sign_date": "",
        "last_income_date": "",
        "rod_level": 1,
        "achievements": [],
        # 已提示过的里程碑（累计钓获数量），避免重复刷屏
        "milestones": [],
        # 各鱼种品质的最佳渔获记录（长期目标）
        "best_records": {},
        "luck_charges": 0.0,
    }


#: 升级曲线参数：升到 L 级需要的累计钓获
#:   = base × (ratio^(L-1) − 1) / (ratio − 1) + growth × (L-1)²
#: 指数项负责「越往后越难」（把最高进度玩家卡在下一级之前），
#: 二次项默认 0，留着做微调。base/ratio/growth 都能在 WebUI 里改。

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

    quality_mult = _clamp(float(quality_mult), 0.5, 5.0)
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
        "live_bonus": 0,
        "locked": False,
        # 水族馆展出加成是否已领取（每条鱼终生只能领一次，防止无限叠加）
        "pond_claimed": False,
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


#: 名字里带这些字的多半是狠角色（敌对生物）。玩家看不到任何标记或提示，
#: 只有把两条放在同一个缸里才会「出事」——保持神秘感。

def _is_hostile(fish_id: str) -> bool:
    """这条鱼是不是「不好惹」的（内部判定，玩家侧无任何显示）。"""
    fish = FISH_BY_ID.get(fish_id)
    if fish is None:
        return False
    name = str(fish.get("name") or "")
    return any(word in name for word in HOSTILE_KEYWORDS)

def _fish_power(instance: dict[str, Any]) -> int:
    """一条鱼的综合实力：三维 + 个体品质 + 鱼种品质 + 变异 + 养成。

    敌对冲突就看这个值：高的一方活下来，低的一方没了。
    """
    attrs = instance.get("attrs") or {}
    power = sum(_safe_int(attrs.get(k), 0, 0) for k in ATTR_WEIGHTS)
    power += int(_safe_number(instance.get("quality_mult"), 1.0) * 60)
    power += RARITY_RANK.get(_fish_rarity(instance.get("fish_id", "")), 0) * 45
    power += int(_variant_mult(instance.get("variant")) * 40)
    feed = _safe_int(instance.get("feed_uses"), 0, 0)
    power += feed * 6
    return power

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

    quality_mult = _clamp(_safe_number(raw.get("quality_mult"), 1.0), 0.5, 5.0)
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
        "live_bonus": live_bonus,
        "locked": bool(raw.get("locked")),
        "pond_claimed": bool(raw.get("pond_claimed")),
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
        raw_event = raw.get("event")
        player["event"] = None
        if isinstance(raw_event, dict) and raw_event.get("id") in EVENT_BY_ID:
            player["event"] = {
                "id": str(raw_event["id"]),
                "ts": _safe_int(raw_event.get("ts"), 0, 0),
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

        # --- 洗髓丹累积的品质幸运储备 ---
        player["luck_charges"] = _clamp(
            _safe_number(raw.get("luck_charges"), 0.0), 0.0, 5.0
        )

        # --- 计数器 ---
        player["total_caught"] = _safe_int(raw.get("total_caught"), 0, 0)
        player["total_sold"] = _safe_int(raw.get("total_sold"), 0, 0)
        player["total_fed"] = _safe_int(raw.get("total_fed"), 0, 0)

        # --- 时间 ---
        player["last_fish_time"] = _safe_int(raw.get("last_fish_time"), 0, 0)
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
    """一行完整信息：``🌟✨ 黄金锦鲤 ✨传说 🟣极品 · 1280``。"""
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
