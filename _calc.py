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

#: 合法的效果键（v1.13.0 起由 ``_effects.EFFECTS`` 注册表在启动时同步过来；
#: 这里保留历史白名单做默认值，单独导入 _calc 时行为逐字不变）
EFFECT_ALLOWED: tuple[str, ...] = (
    "meat", "spirit", "sheen", "value_up",
    "decorate", "feed_bonus", "buff_quality", "heal",
)
#: 旧写法 -> 正式键名（``quality_up`` 是 v1.9 之前的写法）
EFFECT_ALIASES_EXT: dict[str, str] = {"quality_up": "buff_quality"}


def _parse_effects(text: str) -> dict[str, float]:
    """解析道具效果串：``meat=2;spirit=1;value_up=600``。

    支持的效果键分三类：
    * 喂鱼（一次性、永久加成）：``meat`` / ``spirit`` / ``sheen`` / ``value_up``
    * 水族馆装饰（耐久内持续加成挂机产出）：``decorate``
    * 其他：``feed_bonus``（提升这条鱼的投喂上限）、``buff_quality``（钓手手气 buff）、``heal``

    ``quality_up`` 是旧版写法，按 ``buff_quality`` 处理（老配置照常可用）。
    """
    effects: dict[str, float] = {}
    allowed = EFFECT_ALLOWED
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
        "feed_bonus": 0,      # 育灵水带来的额外投喂次数
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
        "feed_bonus": int(_clamp(_safe_int(raw.get("feed_bonus"), 0, 0), 0, 20)),
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

        # --- 钓手手气储备（锦鲤玉佩等道具累积）---
        player["luck_charges"] = _clamp(
            _safe_number(raw.get("luck_charges"), 0.0), 0.0, 5.0
        )
        player["buff_casts_left"] = int(
            _clamp(_safe_int(raw.get("buff_casts_left"), 0, 0), 0, 999)
        )

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
    # ---- 小插曲 ----
    ("story.prompt", "story", "小插曲的提示与选项", "story"),
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
    # ---- 商店 ----
    ("shop.list", "shop", "商店货架", ""),
    ("shop.usage", "shop", "商店用法说明", ""),
    ("shop.usage_short", "shop", "商店用法（简版，带「看货架」）", ""),
    ("shop.free_hook", "shop", "空钩不用买", ""),
    ("shop.not_found", "shop", "商店里没有这件东西", ""),
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
    # ---- 图鉴 ----
    ("collection.view", "collection", "图鉴总览", ""),
    ("collection.detail", "collection", "图鉴完整清单（分页）", ""),
    ("collection.location", "collection", "某个钓点的收集进度", ""),
    # ---- 查鱼 ----
    ("fishinfo.usage", "fishinfo", "查鱼 / 查钓点的用法说明", ""),
    ("fishinfo.detail", "fishinfo", "鱼或钓点的详情", ""),
    ("fishinfo.location_detail", "fishinfo", "钓点里的鱼种清单", ""),
    ("fishinfo.not_found", "fishinfo", "没有这种鱼，也没有这个钓点", ""),
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

#: 继承关系反查：父场景 -> 子场景（编辑器要提示「这几个场景在共用它」）
SCENE_CHILDREN: dict[str, tuple[str, ...]] = {}
for _scene_id, _parent in SCENE_PARENT.items():
    SCENE_CHILDREN[_parent] = SCENE_CHILDREN.get(_parent, ()) + (_scene_id,)

#: 每个场景一行最多摆几个按钮的**内置**默认值。默认值下与历史版本逐项一致：
#: cast 有 5 个按钮 -> 3 + 2 两行；bag/location 各 3 个 -> 一行；pull 1 个 -> 一行。
#: 键 "*" = 其余所有场景的默认值（配置项 button_layout 可以覆盖它们）
BUILTIN_BUTTONS_PER_ROW: dict[str, int] = {"*": 3, "story": 1}

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
BUTTON_COMMAND_WORDS: frozenset[str] = frozenset({
    "帮助", "菜单", "指令", "背包", "包", "bag", "鱼篓",
    "卖", "卖鱼", "卖光光", "卖光", "清空", "全卖", "空背包", "sellall", "一键卖出",
    "图鉴", "收集", "collection", "水族馆", "馆", "缸", "aquarium",
    "扩建背包", "扩容", "背包扩容", "鱼篓扩容",
    "锁定", "锁", "lock", "解锁", "解", "unlock",
    "今日", "天气", "行情", "today", "weather", "market",
    "排行", "排行榜", "榜", "rank", "top",
    "商店", "鱼饵", "道具", "shop", "买",
    "用", "使用", "use", "查", "查询", "鱼", "资料", "fish", "info",
    "事件", "插曲", "选择", "event",
    "换饵", "换鱼饵", "装备饵", "上饵", "bait",
    "体力", "体力值", "活力", "stamina",
    "档案", "profile", "me", "金币", "gold",
    "签到", "sign", "订单", "任务", "order", "orders",
    "钓点", "地点", "地图", "map", "location",
    "鱼竿", "竿", "rod", "杂物", "漂流瓶", "收集品", "collect",
    "拉", "去", "前往", "go", "扩建", "领取", "收益", "投喂", "放入", "取出", "卖出",
})


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
    return head in BUTTON_COMMAND_WORDS


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
