# -*- coding: utf-8 -*-
"""大鱼乐（v1.18.51）：把现实彩票的玩法搬进游戏。

## 它是什么

花金币买票、即时开奖。奖品可以是**鱼**（含正常玩法钓不到的神话 / 神品）、
**金币**、**道具**、**鱼饵** —— 站长的原话是「什么鱼都可以，并且还有金币或者道具，鱼饵」。

## 经济护栏（这一节最重要，改奖表前必读）

彩票在游戏里就是一个**金币出口**，所以核心约束只有一条：

    **期望回报必须小于票价**（``lottery_expected_return(plugin) < ticket_price``）

默认奖表的期望回报约 **94%**（庄家优势 6%），`test_local.py` 里有一条断言专门卡
「期望不许 ≥ 票价」—— 站长把爆率调过头、或者不小心把奖池填成送钱，测试会当场变红。

另外两道护栏是设计出来的，不是补丁：

* ``lottery_daily_limit``（默认 50 张）：不然一个 9 亿金币的号可以一次性买两万张，
  期望上**必然**摸到头奖，几十天的进度一夜抹平。
* ``lottery_pity_count``（默认连输 15 张保底）：没有它，连输 40 张的体验会直接崩掉
  （现实彩票靠「希望」留人，游戏里没有保底就只剩「亏」）。

## 奖表格式（配置项 ``lottery_prizes``，一行一档）

```
id|概率|类型|参数|数量|说明
jackpot|0.001|fish|all:神品|1|神话鱼 · 神品 + 50 万金　🐉 头奖
```

* **概率**是相对权重（不必凑够 100，代码按总和归一化）—— 站长想调爆率就改这一列。
* 类型 ``fish``：参数 ``稀有度[:品质]``。稀有度写 ``all`` = 任意稀有度；
  品质写 ``all`` / 留空 = 按**自然爆率**抽（不硬塞高个体）；
  ``all:神品`` = 任意鱼种但个体必是神品。
* 类型 ``gold``：参数或数量列写金币数都认（``jackpot`` 那档另外加 ``lottery_jackpot_gold``）。
* 类型 ``item`` / ``bait``：参数是道具 / 鱼饵 id，数量 = 给几个（默认 1）。
* 类型 ``reward``：参数 ``id:数量,id:数量``（**道具包**，数量省略 = 1）。
* 类型 ``none``：谢谢惠顾。
* 以 ``#`` 开头的行是注释。

行首 id 是去重键：升级时官方新增的奖级会**追加**进老配置，站长改过的行不会被覆盖。

## 与插件其它部分的关系

* **不写任何新的存档路径**：中奖的鱼走 ``_new_instance`` + ``_record_catch``，
  图鉴 / 成就 / 里程碑 / 排行榜全部自动认；道具和鱼饵只加在 ``player["items"]`` /
  ``player["baits"]`` 这两个本来就有的口袋里。
* 期望值估算需要知道鱼池 / 道具 / 鱼饵的价格，所以这几个函数都接 ``plugin``
  参数（**不做模块级缓存**：站长热重载改了鱼池，估算要跟着变）。
"""

from __future__ import annotations

from typing import Any

#: 奖级类型（站长在奖表里写这些词）
PRIZE_KINDS: tuple[str, ...] = ("fish", "gold", "item", "bait", "reward", "baitpack", "none")

#: 期望值模型用的「典型加成」——真实玩家的装备各不相同，估算取一个中间值。
#: 站长想让估算更贴近自己服务器的后期配置，把这两个数往大调即可（不影响实际发放）。
EV_LOCATION_MULT: float = 1.4    # 典型钓点价值倍率（后期图 1.3~2.05）
EV_VARIANCE: float = 1.02        # VALUE_VARIANCE (0.92, 1.12) 的均值
#: 各品质的「典型倍率」：取该档位区间的中上值（个体品质越高越值钱）
EV_QUALITY_MULT: dict[str, float] = {
    "凡品": 0.90,
    "良品": 1.20,
    "精品": 1.70,
    "珍品": 2.75,
    "绝品": 4.75,
    "神品": 8.00,
}


def _lot_float(value: Any, default: float = 0.0) -> float:
    """容错转 float（配置是站长手写的，什么都可能填进来）。"""
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _lot_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def _lot_split(text: Any) -> list[str]:
    """按 ``|`` 切一行（自动 strip，保留空段以便按下标取值）。"""
    return [part.strip() for part in str(text or "").split("|")]


def parse_reward_pack(text: Any) -> list[tuple[str, int]]:
    """解析 ``reward`` 类型的 ``id:数量,id:数量``（数量省略 = 1）。"""
    out: list[tuple[str, int]] = []
    for chunk in str(text or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            head, _, tail = chunk.partition(":")
        else:
            head, tail = chunk, "1"
        item_id = head.strip()
        if not item_id:
            continue
        out.append((item_id, max(1, _lot_int(tail, 1))))
    return out


def parse_prize_rows(text: Any) -> list[dict[str, Any]]:
    """把奖表文本解析成奖级列表。

    坏行**跳过而不是抛错**（和鱼池/道具表一个口径）：站长写错一行不该让插件起不来，
    更不能让大鱼乐整个不可用。
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = _lot_split(line)
        if len(parts) < 3:
            continue
        prize_id = parts[0]
        kind = parts[2].lower() if len(parts) > 2 else ""
        if not prize_id or prize_id in seen or kind not in PRIZE_KINDS:
            continue
        weight = max(0.0, _lot_float(parts[1] if len(parts) > 1 else "", 0.0))
        if weight <= 0:
            continue
        count_raw = parts[4] if len(parts) > 4 else "1"
        rows.append({
            "id": prize_id,
            "weight": weight,
            "kind": kind,
            # ``none`` 的参数/数量没有意义，其余档留空也照样能解析（见各分支）
            "param": parts[3] if len(parts) > 3 else "",
            "count": max(0, _lot_int(count_raw, 1)) if count_raw.strip() else 1,
            "desc": (parts[5] if len(parts) > 5 and parts[5] else prize_id),
        })
        seen.add(prize_id)
    return rows


def _prize_count(row: dict[str, Any]) -> int:
    """这一档发几份（``none`` 恒为 0，其它至少 1）。"""
    if str(row.get("kind") or "") == "none":
        return 0
    return max(1, _lot_int(row.get("count"), 1))


def jackpot_id(plugin: Any) -> str:
    """哪一个奖级算「头奖」（配置 ``lottery_jackpot_prize``，留空 = 不设）。

    **没有任何一档是写死在代码里的**：头奖也一样，站长把奖品换掉、改成别的 id
    都能用 —— 只有这一档享受「额外送金币 + 群播报」两件待遇。
    """
    try:
        return str((plugin.cfg or {}).get("lottery_jackpot_prize") or "").strip()
    except Exception:                                                    # pragma: no cover
        return ""


def prize_gold_amount(
    row: dict[str, Any], jackpot_gold: int = 0, jackpot_prize: str = ""
) -> int:
    """``gold`` 档要给多少金币（参数列与数量列都认）。

    头奖那一档额外加 ``lottery_jackpot_gold``（站长可以调成 0 关掉）。
    """
    raw = str(row.get("param") or "").strip()
    amount = _lot_int(raw, 0) if raw else _lot_int(row.get("count"), 0)
    if jackpot_prize and str(row.get("id") or "") == jackpot_prize:
        amount += max(0, _lot_int(jackpot_gold, 0))
    return max(0, amount)


def _quality_weights(plugin: Any) -> list[float]:
    """个体品质权重（``cfg["quality_weights"]``，站长可配）。"""
    try:
        raw = (plugin.cfg or {}).get("quality_weights")
        if isinstance(raw, (list, tuple)) and raw:
            return [_lot_float(x, 0.0) for x in raw]
    except Exception:
        pass
    return [44.0, 28.0, 16.0, 9.0, 3.0, 0.0]


def _rarity_mean_value(rarity_spec: str) -> float:
    """某稀有度（或 ``all``）所有鱼种的基础价均值。"""
    spec = str(rarity_spec or "").strip()
    values: list[float] = []
    for fish in FISH_POOL:                       # noqa: F821 - 由 main 注入
        if spec and spec != "all" and str(fish.get("rarity")) != spec:
            continue
        values.append(_lot_float(fish.get("value"), 0.0))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _fish_prize_payout(plugin: Any, param: str, count: int) -> float:
    """一条鱼奖的期望面值（估算）。

    口径：``鱼种基础价均值 × 品质典型倍率 × 钓点/个体差异典型倍率``。
    这里是**估算**，服务两条用途：①「期望回报 < 票价」那条护栏；
    ② ``/钓鱼 大鱼乐 概率`` 的展示。真实发放走 `_new_instance`，一分钱都不会少给。
    """
    rarity, _, quality = str(param or "").partition(":")
    rarity = rarity.strip() or "all"
    quality = quality.strip()
    if quality in ("", "all"):
        # 没指定品质 → 按自然爆率抽，期望倍率 = 各档权重的加权平均
        weights = _quality_weights(plugin)
        total = sum(weights) or 1.0
        mult = sum(
            weights[i] * EV_QUALITY_MULT.get(name, 1.0)
            for i, name in enumerate(QUALITY_ORDER)      # noqa: F821
            if i < len(weights)
        ) / total
    else:
        mult = EV_QUALITY_MULT.get(quality, 1.0)
    per = _rarity_mean_value(rarity) * mult * EV_LOCATION_MULT * EV_VARIANCE
    return per * max(1, count)


def prize_payout(
    plugin: Any, row: dict[str, Any], jackpot_gold: int = 0, jackpot_prize: str = ""
) -> float:
    """该奖级的期望面值（估算，用于护栏与概率页）。

    ⚠️ 头奖的**额外金币**也要算进来：不算的话期望值会凭空少一大块，
    护栏就形同虚设（「期望 < 票价」必须按真实收益算）。
    """
    kind = str(row.get("kind") or "none")
    count = _prize_count(row)
    if kind == "none":
        return 0.0
    extra_gold = (
        prize_gold_amount(row, jackpot_gold, jackpot_prize)
        if jackpot_prize and str(row.get("id")) == jackpot_prize
        else 0
    )
    if kind == "gold":
        return float(prize_gold_amount(row, jackpot_gold, jackpot_prize))
    if kind == "fish":
        return extra_gold + _fish_prize_payout(plugin, str(row.get("param") or ""), count)
    if kind == "item":
        item = (getattr(plugin, "items", None) or {}).get(str(row.get("param") or ""))
        return extra_gold + _lot_float((item or {}).get("price"), 0.0) * count
    if kind == "bait":
        bait = (getattr(plugin, "baits", None) or {}).get(str(row.get("param") or ""))
        return extra_gold + _lot_float((bait or {}).get("price"), 0.0) * count
    if kind == "reward":
        total = float(extra_gold)
        for item_id, num in parse_reward_pack(row.get("param")):
            item = (getattr(plugin, "items", None) or {}).get(item_id)
            total += _lot_float((item or {}).get("price"), 0.0) * num
        return total
    if kind == "baitpack":
        total = float(extra_gold)
        for bait_id, num in parse_reward_pack(row.get("param")):
            bait = (getattr(plugin, "baits", None) or {}).get(bait_id)
            total += _lot_float((bait or {}).get("price"), 0.0) * num
        return total
    return float(extra_gold)


def lottery_expected_return(
    plugin: Any, rows: list[dict[str, Any]], jackpot_gold: int = 0,
    jackpot_prize: str = "",
) -> float:
    """整套奖表的**期望回报**（金币/张）。

    ``= Σ(概率 × 该档期望面值) / Σ概率``

    ⚠️ 这个数**必须小于** ``lottery_ticket_price``，否则玩家可以无限刷钱。
    这里算的是「不含保底」的裸期望；保底只会把这个数**抬高一点点**
    （默认 15 张保底一档三等奖，量级在 1% 以内），所以留的余量够用。
    """
    total_weight = sum(float(r.get("weight") or 0.0) for r in rows)
    if total_weight <= 0:
        return 0.0
    acc = sum(
        float(r.get("weight") or 0.0) * prize_payout(plugin, r, jackpot_gold, jackpot_prize)
        for r in rows
    )
    return acc / total_weight


def fmt_percent(p: float, total_scale: float = 1.0) -> str:
    """概率显示的**自适应小数位**。

    头奖是 0.001/(总权重 79) ≈ 0.0000126（= 0.00126%）：固定两位小数的话会显示成
    「0.00%」，看着像「根本不会中」——玩家会以为奖池是假的。所以这里按需要补小数位：
    至少两位，不够就加，最多六位（``0.000013%``），保证显示出来的数**不是 0**。
    """
    p = max(0.0, float(p))
    for digits in range(2, 7):
        if p * 100 >= 10 ** (-digits):
            return f"{p * 100:.{digits}f}%"
    return "<0.000001%"


def lottery_expectation_label(
    ev: float, price: int
) -> tuple[str, str]:
    """期望回报的说明文字 + 风险等级（``ok`` / ``warn`` / ``bad``）。

    彩票在游戏里是**金币出口**，期望回报必须小于票价。站长有权把奖表改成任何样子
    （包括送钱），但代码必须**明确告诉他现在这套是印钞机**，不能悄悄放过。
    """
    if price <= 0:
        return ("票价是 0：这张票不要钱，奖表就是纯产出（相当于无条件发奖）", "bad")
    ratio = ev / price
    label = (
        f"票价 {price:,} 金/张，长期期望约 {int(ev):,} 金/张（{ratio:.0%}）"
    )
    if ratio >= 1.0:
        return (label + "　⚠️ 这是**印钞机**（期望 ≥ 票价）：玩家可以无限刷钱", "bad")
    if ratio >= 0.95:
        return (label + "　⚠️ 期望太接近票价（庄家优势不足 5%），建议留更多余量", "warn")
    return (label + f"　（庄家优势 {1 - ratio:.0%}，正常）", "ok")


def ev_guard_message(
    plugin: Any, rows: list[dict[str, Any]], jackpot_gold: int = 0,
    jackpot_prize: str = "",
) -> str:
    """奖表是不是「印钞机」（期望 ≥ 票价）。空串 = 正常。"""
    try:
        price = max(0, _lot_int(getattr(plugin, "cfg", {}).get("lottery_ticket_price"), 0))
    except Exception:                                                    # pragma: no cover
        price = 0
    ev = lottery_expected_return(plugin, rows, jackpot_gold, jackpot_prize)
    _label, level = lottery_expectation_label(ev, price)
    if level != "bad":
        return ""
    return (
        f"大鱼乐的期望回报（{int(ev):,}/张）已经不低于票价（{price:,}/张）——"
        f"玩家可以靠它无限刷钱。请调低某档概率或调高票价（配置 lottery_prizes / "
        f"lottery_ticket_price）。"
    )


def lottery_odds_lines(
    plugin: Any, rows: list[dict[str, Any]], jackpot_gold: int = 0,
    jackpot_prize: str = "",
) -> list[str]:
    """``/钓鱼 大鱼乐 概率`` 用的概率表（现实彩票也公开概率）。"""
    total = sum(float(r.get("weight") or 0.0) for r in rows)
    if total <= 0:
        return ["⚠️ 奖表是空的（配置 lottery_prizes 没内容）"]
    top = jackpot_prize or ""
    lines = ["🎰 大鱼乐 · 奖级与概率"]
    for row in sorted(rows, key=lambda r: -_lot_float(r.get("weight"), 0.0)):
        p = float(row.get("weight") or 0.0) / total
        payout = prize_payout(plugin, row, jackpot_gold, jackpot_prize)
        odds = f"约 1/{max(1, round(1 / p)):,}" if p > 0 else "—"
        value = f"　≈{int(payout):,} 金" if payout > 0 else ""
        crown = "👑 " if top and str(row.get("id")) == top else ""
        lines.append(f"　{fmt_percent(p)}（{odds}）　{crown}{row.get('desc')}{value}")
    return lines


def roll_prize(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """按权重抽一档。奖表为空返回 None。"""
    import random

    total = sum(float(r.get("weight") or 0.0) for r in rows)
    if total <= 0:
        return None
    point = random.random() * total
    acc = 0.0
    for row in rows:
        acc += float(row.get("weight") or 0.0)
        if point <= acc:
            return row
    return rows[-1]


def pick_fish_for_prize(param: str) -> str | None:
    """按 ``稀有度[:品质]`` 挑一条鱼种 id（挑不到返回 None）。

    ``all`` / 留空 = 全鱼池按**基础权重**抽（和正常钓鱼一样看脸）；
    指定稀有度时在该稀有度内按基础权重抽。
    """
    import random

    rarity, _, _quality = str(param or "").partition(":")
    rarity = rarity.strip()
    pool = [
        fish for fish in FISH_POOL              # noqa: F821
        if (rarity in ("", "all") or str(fish.get("rarity")) == rarity)
    ]
    if not pool:
        return None
    weights = [max(0.0, _lot_float(fish.get("weight"), 1.0)) for fish in pool]
    if sum(weights) <= 0:
        chosen = random.choice(pool)
    else:
        chosen = random.choices(pool, weights=weights, k=1)[0]
    fish_id = str(chosen.get("id") or "")
    return fish_id or None


def prize_quality_mult(param: str) -> float | None:
    """这一档要求的个体品质倍率；没要求（按自然爆率）返回 None。

    没要求时调用方直接走 ``_roll_quality_mult``（自然爆率，神品权重 0 摇不出来）；
    要求了就给一个**落在该档区间内**的倍率（``all:神品`` 是这一档唯一能拿到神品的方式）。
    """
    _rarity, _, quality = str(param or "").partition(":")
    quality = quality.strip()
    if not quality or quality == "all":
        return None
    for name, low, high, _emoji in (globals().get("QUALITY_TIERS") or []):
        if name == quality:
            # 取该档中上值：既保证「不低于这一档」，又不至于每条都顶格
            return float(low) + (float(high) - float(low)) * 0.6
    return None
