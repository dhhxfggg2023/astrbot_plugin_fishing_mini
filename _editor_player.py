# -*- coding: utf-8 -*-
"""玩家存档编辑器（v1.18.56）：把玩家的**全部数据**做成一个可编辑的大界面。

站长原话：「能改玩家的所有数据，不止你说的那些，单独弄个界面吧，之前的太小了」，
后面又定下三条：**鱼能逐条改**（鱼种/品质/异色/三维/估值、可增可删）、
**留一个原始 JSON 高级区**、**改好几处一起提交**。

## 这一模块负责什么

* `player_sections_payload()` —— 把一个玩家的存档拆成「分组 -> 字段 -> 当前值」，
  连同**枚举表**（鱼种/鱼饵/道具/鱼竿/钓点/称号/成就/变异）一起给页面。
  页面是纯渲染：以后本模块新增一个字段，界面自动多一行，不用改两处。
* `apply_player_edits()` —— 把页面来的编辑批量应用到一个玩家字典上（**纯内存**）。

## 安全口径（和改金币完全一致，一处都不放宽）

1. **白名单**：字段必须在 `PLAYER_FIELD_SPECS`（或「其它字段」兜底桶）里；
   没登记但有值的键走 `json` 类型，仍可改 —— 但类型要过校验。
2. **范围校验**：计数 0~10 亿（金币同）、比例 0~100、文本 ≤200 字、JSON 能解析。
3. **枚举校验**：鱼种 / 鱼饵 / 道具 / 鱼竿 / 钓点 / 称号 / 变异 / 成就 必须真的存在，
   写错就整批拒绝并点名（不让存档里出现「不存在的鱼」）。
4. **整批原子**：调用方在**深拷贝**上先跑一遍，任何一项不过就一个字都不改。
5. **改前自动存档**：由调用方（编辑器通道）负责，存档失败就放弃本次修改。

## 与「改金币」的关系

`_editor_bridge` 里的 `player_set` / `snapshot_player_set` 是**同一套实现**，
只是它们走的是老的 `PLAYER_FIELDS`（一小组常用字段）。这一模块是它的超集：
金币也在里面，所以两条通道不会打架（同一张表说了算）。
"""

from __future__ import annotations

import copy
import json
from typing import Any

#: 计数类字段的上限（金币同款；防止手滑写出天文数字）
COUNT_MAX = 1_000_000_000
#: 比例 / 倍率类字段的上限
RATIO_MAX = 100.0
#: 文本字段长度上限
TEXT_MAX = 200
#: 一次最多改多少项（防止页面一次提交几万条把插件卡死）
MAX_EDITS = 2000


def _spec(key: str, label: str, kind: str, group: str, desc: str, **extra: Any) -> dict:
    """造一条字段说明。``kind`` 决定页面渲染什么控件、后端怎么校验。"""
    row = {"key": key, "label": label, "kind": kind, "group": group, "desc": desc}
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# 字段清单：**唯一的一份**（页面照它渲染，后端照它校验）
# ---------------------------------------------------------------------------
#: 分组顺序（页面按这个顺序显示分区）
GROUP_ORDER: tuple[str, ...] = (
    "基础", "资产", "装备与解锁", "状态与计数", "手气与保底",
    "每日额度", "订单", "插曲连载", "世界与收益", "缓存与标志", "其它字段",
)

FISH_KINDS = ("fish", "fish_list")


def _field_specs() -> list[dict[str, Any]]:
    """全部字段说明（含分组）。**加一个字段就加一行**，页面自动跟着变。"""
    S: list[dict[str, Any]] = [
        # ---- 基础 ----
        _spec("user_id", "玩家 ID", "readonly", "基础", "存档主键，不能改（改了就等于换个人）"),
        _spec("data_version", "数据版本", "int", "基础", "存档结构版本，插件升级时用；一般不用动"),
        _spec("gold", "金币", "count", "基础", "主货币，上限 10 亿"),
        _spec("last_name", "昵称缓存", "text", "基础", "排行榜/档案显示用；下次发言会被覆盖"),
        _spec("last_platform", "来源平台缓存", "text", "基础", "例如 qq_official；排查平台问题用"),

        # ---- 资产 ----
        _spec("inventory", "背包（鱼）", "fish_list", "资产",
              "逐条可改：鱼种 / 个体品质 / 异色 / 三维 / 投喂次数 / 锁定；也能加一条或删一条"),
        _spec("aquarium", "水族馆（鱼）", "fish_list", "资产",
              "同上；缸里的鱼还会按在缸时间产出挂机收益"),
        _spec("baits", "鱼饵", "map", "资产", "鱼饵 id -> 数量（bread / worm / abyss_bait …）",
              enum="baits"),
        _spec("items", "道具", "map", "资产", "道具 id -> 数量（feed_basic / hot_soup / jade_lantern …）",
              enum="items"),
        _spec("collectibles", "杂物", "map", "资产",
              "杂物 id -> 数量（seaweed / treasure_chest …）；纸条在单独一栏",
              enum="collectibles"),
        _spec("bottle_notes", "纸条", "str_list", "资产", "漂流瓶里开出来的纸条（只保留最近 30 条）"),
        _spec("variants", "变异计数", "map_int", "资产",
              "变异 id -> 钓到过几次（golden / rainbow …）；这是统计，不是拥有",
              enum="variants"),
        _spec("collection", "图鉴", "json", "资产",
              "fish_id[#变异] -> {count,best_value,first_ts}；建议用 JSON 改（下面有折叠区）"),
        _spec("best_records", "最佳渔获纪录", "json", "资产", "按鱼种品质记录的最好成绩"),
        _spec("backpack_slots", "背包扩容档位", "json", "资产",
              "已购买的扩容档位下标；想给玩家扩容就改这里"),
        _spec("aquarium_slots", "鱼缸扩容档位", "json", "资产", "已解锁的鱼缸扩建栏位名"),
        _spec("decorations", "鱼缸装饰", "json", "资产",
              "摆着的装饰 [{id,rate,ts,expire_ts}]；到期会自动失效"),

        # ---- 装备与解锁 ----
        _spec("rods", "已拥有鱼竿", "str_list", "装备与解锁", "鱼竿 id 列表", enum="rods"),
        _spec("equipped_rod", "当前鱼竿", "text", "装备与解锁", "鱼竿 id", enum="rods"),
        _spec("equipped_bait", "当前鱼饵", "text", "装备与解锁",
              "鱼饵 id；空串 = 让插件自动挂最好的，none = 空钩", enum="baits"),
        _spec("auto_buff_item", "自动补给道具", "text", "装备与解锁",
              "手气/保底道具 id，用光会自动买一个（空 = 不自动）", enum="items"),
        _spec("locations", "已解锁钓点", "str_list", "装备与解锁", "钓点 id 列表", enum="locations"),
        _spec("current_location", "当前钓点", "text", "装备与解锁", "钓点 id", enum="locations"),
        _spec("titles", "已拥有称号", "str_list", "装备与解锁", "称号 id 列表", enum="titles"),
        _spec("title", "佩戴的称号", "text", "装备与解锁", "称号 id（空 = 不戴）", enum="titles"),

        # ---- 状态与计数 ----
        _spec("stamina", "体力", "count", "状态与计数", "-1 = 还没初始化（下次结算补满）；上限见 stamina_max"),
        _spec("stamina_ts", "体力计时戳", "int", "状态与计数", "上次结算体力的时间戳（秒）"),
        _spec("rod_level", "鱼竿等级", "int", "状态与计数", "内部用的等级缓存，一般跟着鱼竿走"),
        _spec("total_caught", "累计钓获", "count", "状态与计数", "等级就是按它算的"),
        _spec("total_sold", "累计卖出", "count", "状态与计数", ""),
        _spec("total_fed", "累计投喂", "count", "状态与计数", ""),
        _spec("total_orders", "累计交单", "count", "状态与计数", ""),
        _spec("perfect_pulls", "完美拉线次数", "count", "状态与计数", "成就用"),
        _spec("clutch_wins", "惊险拉线次数", "count", "状态与计数", "偏差但拉上来了，成就用"),
        _spec("last_fish_time", "上次钓鱼时间", "int", "状态与计数", "时间戳（秒）"),
        _spec("achievements", "已解锁成就", "str_list", "状态与计数",
              "成就 id 列表（写进来就等于解锁）", enum="achievements"),
        _spec("milestones", "已提示里程碑", "json", "状态与计数",
              "累计钓获数量列表，避免重复刷屏"),
        _spec("collection_progress", "图鉴进度（只读）", "readonly", "状态与计数",
              "由图鉴算出来的，不单独存"),

        # ---- 手气与保底 ----
        _spec("luck_charges", "一次性手气储备", "ratio", "手气与保底",
              "插曲/彩蛋给的，下一竿生效、用完即清"),
        _spec("buff_casts_left", "手气道具剩余竿数", "count", "手气与保底", "锦鲤玉佩/潮汐香/玉髓灯"),
        _spec("buff_quality", "手气强度", "ratio", "手气与保底", "例如 0.35 = 玉髓灯那档"),
        _spec("buff_floor_casts", "品质保底剩余竿数", "count", "手气与保底", ""),
        _spec("buff_floor", "品质保底值", "ratio", "手气与保底",
              "品质倍率下限，例如 3.5 ≈ 绝品"),

        # ---- 每日额度 ----
        _spec("daily_date", "额度日期", "text", "每日额度",
              "例如 2026-09-24；和今天不一样时插件会把下面几项清零后重算"),
        _spec("daily_used", "今日用量", "json", "每日额度",
              "{buff,heal,reroll,offering,lottery} 各用了多少"),

        # ---- 订单 ----
        _spec("orders", "当前订单", "json", "订单", "订单列表（改坏了下次刷新会自动换一批）"),
        _spec("order_date", "订单日期", "text", "订单", ""),
        _spec("order_next_ts", "下批刷新时间", "int", "订单", "时间戳（秒）"),
        _spec("order_location", "订单按哪个钓点抽", "text", "订单", "空 = 不限钓点", enum="locations"),
        _spec("order_move_rerolls", "本周期换钓点次数", "count", "订单", "和「换钓点重掷」额度有关"),

        # ---- 插曲连载 ----
        _spec("story", "连载进度", "json", "插曲连载",
              "{arc,ep,flags,since,seen,done,last}；想重置某条线就把对应项删掉"),

        # ---- 世界与收益 ----
        _spec("pond_last_ts", "鱼塘结算时间", "int", "世界与收益", "时间戳（秒）"),
        _spec("pond_claimed_ts", "上次领收益时间", "int", "世界与收益", "时间戳（秒）"),
        _spec("pond_best_income", "最佳挂机收益", "count", "世界与收益", ""),
        _spec("market_best_bonus", "最好的一次行情", "ratio", "世界与收益", ""),
        _spec("weather_date", "天气日期", "text", "世界与收益", "和今天不一样时会重掷"),
        _spec("weather", "今日天气", "text", "世界与收益", "天气 id", enum="weather"),
        _spec("market_date", "行情日期", "text", "世界与收益", ""),
        _spec("market", "今日行情", "json", "世界与收益", "各鱼种的价格加成列表"),

        # ---- 缓存与标志 ----
        _spec("offering_ts", "香火供奉到期", "int", "缓存与标志", "时间戳（秒），过去时间 = 没供奉"),
        _spec("last_sign_date", "上次签到日期", "text", "缓存与标志", ""),
        _spec("last_income_date", "上次领收益日期", "text", "缓存与标志", ""),
        _spec("event", "待处理插曲", "json", "缓存与标志",
              "正在等玩家做的选择 {id,ts}；清掉就等于跳过这次插曲"),

        # ---- 大鱼乐 ----
        _spec("lottery_loses", "大鱼乐连输", "count", "状态与计数", "到 pity_count 就保底"),
        _spec("lottery_total", "大鱼乐累计张数", "count", "状态与计数", ""),
    ]
    return S


#: 只读字段（页面画成灰色文字，提交时会被忽略）
READONLY_KINDS: frozenset[str] = frozenset({"readonly"})


def field_specs() -> list[dict[str, Any]]:
    """字段清单（给页面 + 给校验用）。"""
    return _field_specs()


def spec_map() -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in _field_specs()}


# ---------------------------------------------------------------------------
# 枚举表（页面用下拉，后端用来校验）
# ---------------------------------------------------------------------------
def _ids_of(rows: Any, key: str = "id") -> list[str]:
    out: list[str] = []
    for row in rows or []:
        if isinstance(row, dict):
            value = str(row.get(key) or "").strip()
        else:
            value = str(row or "").strip()
        if value:
            out.append(value)
    return out


def enum_tables(plugin: Any) -> dict[str, list[str]]:
    """给页面用的枚举表（都从**当前生效的内容表**取，站长改了鱼池这里跟着变）。"""
    g = globals()
    fish = _ids_of(g.get("FISH_POOL"))
    baits = list((getattr(plugin, "baits", None) or {}).keys())
    items = list((getattr(plugin, "items", None) or {}).keys())
    rods = [str(r.get("id")) for r in (g.get("RODS") or []) if isinstance(r, dict)]
    locations = [str(x.get("id")) for x in (g.get("LOCATIONS") or []) if isinstance(x, dict)]
    titles = []
    try:
        for row in getattr(plugin, "titles", None) or []:
            if isinstance(row, dict) and row.get("id"):
                titles.append(str(row["id"]))
    except Exception:                                            # pragma: no cover
        titles = []
    variants = [str(v.get("id")) for v in (g.get("VARIANTS") or []) if isinstance(v, dict)]
    achievements = list((g.get("ACHIEVEMENTS") or {}).keys())
    weathers = [str(w.get("id")) for w in (g.get("WEATHERS") or []) if isinstance(w, dict)]
    return {
        "fish": sorted(set(fish)),
        "baits": sorted(set(baits)),
        "items": sorted(set(items)),
        "rods": sorted(set(rods)),
        "locations": sorted(set(locations)),
        "titles": sorted(set(titles)),
        "variants": sorted(set(variants)),
        "achievements": sorted(set(achievements)),
        "weather": sorted(set(weathers)),
        "collectibles": _ids_of(g.get("COLLECTIBLES")),
        # 个体品质档（六档名字来自配置的 quality_tiers）
        "quality": [str(t[0]) for t in (g.get("QUALITY_TIERS") or []) if t],
        "fish_rarity": [str(r) for r in (g.get("RARITY_ORDER") or [])],
    }


# ---------------------------------------------------------------------------
# 读：把一个玩家存档拆成「分组 -> 字段 -> 值」
# ---------------------------------------------------------------------------
def _json_safe(value: Any) -> Any:
    """把值变成页面能显示的形态（不能 JSON 化的就转成字符串）。"""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


def section_payload(plugin: Any, player: dict[str, Any]) -> dict[str, Any]:
    """分组后的字段与当前值（页面直接渲染）。"""
    specs = _field_specs()
    seen = {row["key"] for row in specs}
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in specs:
        field = dict(row)
        field["value"] = _json_safe(player.get(row["key"]))
        field["has"] = row["key"] in player
        groups.setdefault(row["group"], []).append(field)

    # 兜底桶：存档里有、但清单里没登记的键（例如以后新增的功能字段）照常给出来，
    # 类型标成 json —— 既不会「看不到」，也不会因为没登记就被静默丢掉。
    others: list[dict[str, Any]] = []
    for key in sorted(player.keys()):
        if key in seen or str(key).startswith("_"):
            continue
        others.append({
            "key": str(key), "label": str(key), "kind": "json",
            "group": "其它字段",
            "desc": "存档里有、字段清单里还没登记的键（插件更新的新字段会先落到这里）",
            "value": _json_safe(player.get(key)),
            "has": True,
        })
    if others:
        groups["其它字段"] = others

    ordered = [g for g in GROUP_ORDER if g in groups]
    ordered += [g for g in groups if g not in ordered]
    return {
        "groups": [
            {"title": g, "items": groups[g]} for g in ordered
        ],
        "enums": enum_tables(plugin),
        "count_max": COUNT_MAX,
        "ratio_max": RATIO_MAX,
        "text_max": TEXT_MAX,
        # 原始 JSON 高级区用的原始数据（一个字符都不改）
        "raw_json": json.dumps(
            {k: _json_safe(v) for k, v in player.items()}, ensure_ascii=False, indent=1
        ),
    }


def player_summary(player: dict[str, Any]) -> dict[str, Any]:
    """玩家选择列表用的一行（尽量便宜）。"""
    return {
        "user_id": str(player.get("user_id") or ""),
        "name": str(player.get("last_name") or ""),
        "gold": int(player.get("gold") or 0) if str(player.get("gold") or 0).lstrip("-").isdigit() else 0,
        "inventory": len(player.get("inventory") or []),
        "aquarium": len(player.get("aquarium") or []),
    }


# ---------------------------------------------------------------------------
# 鱼：逐条增 / 删 / 改
# ---------------------------------------------------------------------------
#: 鱼身上允许手改的键（其余字段老实用代码算，别手改）
FISH_EDITABLE: tuple[str, ...] = (
    "fish_id", "quality", "variant", "attrs", "feed_uses", "feed_bonus",
    "live_bonus", "locked",
)


def _attr_pair(value: Any, default: int = 60) -> dict[str, int]:
    """三维属性：``{"meat":80,...}`` 或 ``"80,70,60"`` 都认。"""
    g = globals()
    weights = list(g.get("ATTR_WEIGHTS") or ("meat", "spirit", "sheen"))
    out: dict[str, int] = {}
    if isinstance(value, dict):
        src = value
    elif isinstance(value, (list, tuple)):
        src = {k: v for k, v in zip(weights, value)}
    elif isinstance(value, str) and value.strip():
        parts = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
        src = {k: v for k, v in zip(weights, parts)}
    else:
        src = {}
    for key in weights:
        try:
            number = int(float(str(src.get(key, default)).strip()))
        except (TypeError, ValueError):
            number = default
        out[str(key)] = max(1, min(100, number))
    return out


def _quality_mult_of(plugin: Any, quality: Any, fish: dict[str, Any]) -> float:
    """把「品质名」或「倍率」换成一个倍率（写坏了就按自然爆率摇一个）。"""
    g = globals()
    if quality is None or str(quality).strip() == "":
        roll = g.get("_roll_quality_mult")
        if callable(roll):
            try:
                return float(roll(
                    (getattr(plugin, "cfg", None) or {}).get("quality_weights"),
                    cfg=getattr(plugin, "cfg", None) or {},
                ))
            except Exception:                                     # pragma: no cover
                pass
        return 1.0
    text = str(quality).strip()
    for tier in (g.get("QUALITY_TIERS") or []):
        name, low, high = str(tier[0]), float(tier[1]), float(tier[2])
        if text == name:
            return low + (high - low) * 0.5        # 落在该档中间
    try:
        number = float(text)
    except (TypeError, ValueError):
        number = 1.0
    ceil = g.get("_quality_ceil")
    cap = float(ceil()) if callable(ceil) else 10.0
    return max(0.5, min(cap, number))


def edit_fish(plugin: Any, instance: dict[str, Any], edit: dict[str, Any]) -> tuple[bool, str]:
    """按 ``edit`` 改一条鱼（**就地改**）。返回 ``(是否成功, 说明)``。

    能改：``fish_id``（换鱼种）、``quality``（品质名或倍率）、``variant``（异色 id / 空 / none）、
    ``attrs``（三维，``{"meat":80,...}`` 或 ``"80,70,60"``）、``feed_uses``、``feed_bonus``、
    ``live_bonus``、``locked``；另外 ``recalc=true`` 时按当前三维与品质**重算估值**
    （默认不重算，因为估值是算出来的 —— 手改三维不重算会自相矛盾）。

    ⚠️ 估值重算是**双向**的（可以变便宜）：站长是在改存档，不是游戏里的投喂。
    """
    if not isinstance(instance, dict):
        return False, "这条鱼不是对象"
    g = globals()
    enum = enum_tables(plugin)
    changed: list[str] = []
    if "fish_id" in edit:
        fish_id = str(edit.get("fish_id") or "").strip()
        if fish_id and enum["fish"] and fish_id not in enum["fish"]:
            return False, f"没有「{fish_id}」这种鱼（鱼池里查不到）"
        if fish_id:
            instance["fish_id"] = fish_id
            changed.append(f"鱼种→{fish_id}")
    if "variant" in edit:
        raw = edit.get("variant")
        variant = "" if raw is None else str(raw).strip()
        if variant in ("none", "无", "否"):
            variant = ""
        if variant and enum["variants"] and variant not in enum["variants"]:
            return False, f"没有「{variant}」这种异色（变异表里查不到）"
        instance["variant"] = variant or None
        changed.append("异色→" + (variant or "无"))
    if "attrs" in edit:
        instance["attrs"] = _attr_pair(edit.get("attrs"))
        changed.append("三维→" + ",".join(str(v) for v in instance["attrs"].values()))
    if "quality" in edit:
        fish = (g.get("FISH_BY_ID") or {}).get(str(instance.get("fish_id") or ""), {})
        mult = _quality_mult_of(plugin, edit.get("quality"), fish)
        instance["quality_mult"] = round(mult, 4)
        label = g.get("_quality_label")
        instance["quality"] = label(mult)[0] if callable(label) else str(edit.get("quality") or "")
        changed.append(f"品质→{instance['quality']}")
    for key, label in (("feed_uses", "投喂次数"), ("feed_bonus", "投喂上限加成"),
                       ("live_bonus", "养成加成")):
        if key not in edit:
            continue
        try:
            number = int(float(str(edit.get(key)).strip()))
        except (TypeError, ValueError):
            return False, f"{label}要写整数"
        if number < 0:
            return False, f"{label}不能是负数"
        instance[key] = number
        changed.append(f"{label}→{number}")
    if "locked" in edit:
        instance["locked"] = bool(edit.get("locked"))
        changed.append("锁定→" + ("是" if instance["locked"] else "否"))
    if edit.get("recalc"):
        ok, why = recalc_fish_value(instance)
        if not ok:                                                # pragma: no cover
            return False, why
        changed.append(f"重算估值→{instance.get('value')}")
    if not changed:
        return False, "这条鱼没有任何改动"
    return True, "；".join(changed)


def recalc_fish_value(instance: dict[str, Any]) -> tuple[bool, str]:
    """按当前三维 + 品质倍率重算这条鱼的估值（**双向**，可以变便宜）。

    游戏里投喂只会涨（``_recalc_instance_value`` 取 max），但这里是改存档：
    站长把三维改小了，估值就该跟着降下来。
    """
    g = globals()
    fish = (g.get("FISH_BY_ID") or {}).get(str(instance.get("fish_id") or ""))
    if fish is None:
        return False, f"鱼种「{instance.get('fish_id')}」不在鱼池里，算不出估值"
    compute = g.get("_compute_value")
    fish_value = g.get("_fish_value")
    if not callable(compute) or not callable(fish_value):
        return False, "估值算法不可用（_calc 没注入？）"
    attrs = _attr_pair(instance.get("attrs"))
    instance["attrs"] = attrs
    variance = float(instance.get("value_variance") or 1.0)
    quality = float(instance.get("quality_mult") or 1.0)
    gear = float(instance.get("gear_mult") or 1.0)
    try:
        base = int(compute(fish_value(fish), attrs, quality, variance, gear))
    except Exception as e:                                        # pragma: no cover
        return False, f"重算失败：{e}"
    instance["base_value"] = max(1, base)
    instance["value"] = max(1, base) + int(instance.get("live_bonus") or 0)
    return True, f"估值 {instance['value']}"


def new_fish(plugin: Any, edit: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """按页面给的参数造一条新鱼（复用 `_new_instance`，保证字段齐全）。"""
    g = globals()
    enum = enum_tables(plugin)
    fish_id = str(edit.get("fish_id") or "").strip()
    if not fish_id:
        return None, "要写鱼种 id"
    if enum["fish"] and fish_id not in enum["fish"]:
        return None, f"没有「{fish_id}」这种鱼"
    raw = edit.get("variant")
    variant = "" if raw is None else str(raw).strip()
    if variant in ("none", "无", "否"):
        variant = ""
    if variant and enum["variants"] and variant not in enum["variants"]:
        return None, f"没有「{variant}」这种异色"
    quality = edit.get("quality")
    # 允许页面写成倍率数字；写品质名时先造出来再改品质（_new_instance 收的是倍率）
    try:
        mult = float(str(quality).strip()) if str(quality or "").strip() else 1.0
    except (TypeError, ValueError):
        mult = 1.0
    make = g.get("_new_instance")
    if not callable(make):
        return None, "造鱼算法不可用（_calc 没注入？）"
    instance = make(fish_id, mult, variant=variant or None, source="editor")
    if instance is None:
        return None, f"造不出这条鱼（{fish_id}）"
    if str(quality or "").strip():
        ok, why = edit_fish(plugin, instance, {
            "quality": quality, "attrs": edit.get("attrs"), "recalc": True,
        })
        if not ok:
            return None, why
    elif edit.get("attrs") is not None:
        edit_fish(plugin, instance, {"attrs": edit.get("attrs"), "recalc": True})
    else:
        recalc_fish_value(instance)
    # ⚠️ 最后**无条件**按最终的三维/品质再算一次（v1.18.56）：
    #    上面那两步都是「先算后设」，估值会是按随机三维算出来的旧值。
    #    新造出来的鱼没有历史包袱，估值必须和它现在的三维一致。
    recalc_fish_value(instance)
    return instance, "已新增一条鱼"


# ---------------------------------------------------------------------------
# 写：把一整批编辑应用到玩家字典上（纯内存）
# ---------------------------------------------------------------------------
def _coerce_count(value: Any, *, allow_negative: bool = False) -> tuple[int | None, str]:
    if isinstance(value, bool) or value is None:
        return None, "要写一个整数"
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None, f"「{value}」不是数字"
    if number != number or number in (float("inf"), float("-inf")):
        return None, f"「{value}」不是有效数字"
    if number < 0 and not allow_negative:
        return None, "不能是负数"
    if abs(number) > COUNT_MAX:
        return None, f"绝对值最多 {COUNT_MAX}"
    return int(number), ""


def _coerce_ratio(value: Any) -> tuple[float | None, str]:
    if isinstance(value, bool) or value is None:
        return None, "要写数字"
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None, f"「{value}」不是数字"
    if number != number or number in (float("inf"), float("-inf")):
        return None, f"「{value}」不是有效数字"
    if number < 0 or number > RATIO_MAX:
        return None, f"要在 0 ~ {RATIO_MAX} 之间"
    return number, ""


def _coerce_text(value: Any) -> tuple[str | None, str]:
    text = "" if value is None else str(value).strip()
    if len(text) > TEXT_MAX:
        return None, f"最多 {TEXT_MAX} 字（现在 {len(text)}）"
    return text, ""


def _check_enum(spec: dict[str, Any], value: Any, enum: dict[str, list[str]]) -> str:
    """枚举字段校验：值必须真的存在（空串 = 不设，跳过）。"""
    key = str(spec.get("enum") or "")
    if not key or value is None or str(value).strip() == "":
        return ""
    allowed = enum.get(key) or []
    if not allowed:
        return ""
    if str(value).strip() not in allowed:
        return f"{spec.get('label')}：「{value}」不存在（{key} 里没有这一项）"
    return ""


def _apply_map(
    player: dict[str, Any], spec: dict[str, Any], edit: dict[str, Any], enum: dict[str, list[str]]
) -> tuple[bool, str]:
    """计数表（鱼饵 / 道具 / 杂物 / 变异）里的一项：可增可减可直设，也能整表换。"""
    key = spec["key"]
    table = player.get(key)
    if not isinstance(table, dict):
        table = {}
        player[key] = table
    if "full" in edit:
        full = edit.get("full")
        if not isinstance(full, dict):
            return False, f"{spec['label']}要写成一个对象"
        cleaned: dict[str, int] = {}
        for name, count in full.items():
            value, why = _coerce_count(count)
            if why:
                return False, f"{spec['label']}（{name}）：{why}"
            if value:
                cleaned[str(name)] = value
        player[key] = cleaned
        return True, f"{spec['label']} 整表已替换（{len(cleaned)} 项）"
    name = str(edit.get("name") or "").strip()
    if not name:
        return False, f"{spec['label']}：要写 name（哪一项）"
    why = _check_enum(spec, name, enum)
    if why and spec.get("enum") != "variants":
        return False, why
    mode = str(edit.get("mode") or "add").lower()
    value, why2 = _coerce_count(
        edit.get("value"), allow_negative=(mode in ("add", "delta"))
    )
    if why2:
        return False, f"{spec['label']}（{name}）：{why2}"
    old = int(table.get(name) or 0)
    new = old + value if mode in ("add", "delta") else value
    if new < 0:
        new = 0
    if new == 0:
        table.pop(name, None)
    else:
        table[name] = new
    return True, f"{spec['label']} {name} {old}→{new}"


def _apply_str_list(
    player: dict[str, Any], spec: dict[str, Any], edit: dict[str, Any], enum: dict[str, list[str]]
) -> tuple[bool, str]:
    """字符串列表（鱼竿 / 钓点 / 称号 / 成就 / 纸条）：整表换 或 加/删一项。"""
    key = spec["key"]
    current = player.get(key)
    items = [str(x) for x in current] if isinstance(current, list) else []
    if "full" in edit:
        raw = edit.get("full")
        if isinstance(raw, str):
            raw = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
        if not isinstance(raw, list):
            return False, f"{spec['label']}要写成列表或逗号分隔的文本"
        cleaned: list[str] = []
        for item in raw:
            text, why = _coerce_text(item)
            if why:
                return False, f"{spec['label']}：{why}"
            if text and text not in cleaned:
                bad = _check_enum(spec, text, enum)
                if bad:
                    return False, bad
                cleaned.append(text)
        player[key] = cleaned
        return True, f"{spec['label']} 整表已替换（{len(cleaned)} 项）"
    item, why = _coerce_text(edit.get("item"))
    if why or not item:
        return False, f"{spec['label']}：要写 item（哪一项）"
    bad = _check_enum(spec, item, enum)
    if bad:
        return False, bad
    mode = str(edit.get("mode") or "add").lower()
    if mode in ("remove", "del", "delete", "删"):
        if item in items:
            items.remove(item)
            player[key] = items
            return True, f"{spec['label']} 去掉 {item}"
        return True, f"{spec['label']} 本来就没有 {item}"
    if item not in items:
        items.append(item)
        player[key] = items
        return True, f"{spec['label']} 加上 {item}"
    return True, f"{spec['label']} 已经有 {item}"


def _apply_json(
    player: dict[str, Any], spec: dict[str, Any], edit: dict[str, Any]
) -> tuple[bool, str]:
    """JSON 字段：整值替换（字符串会被解析成 JSON）。"""
    value = edit.get("value")
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            value = None
        else:
            try:
                value = json.loads(text)
            except (json.JSONDecodeError, TypeError) as e:
                return False, f"{spec['label']}：JSON 解析失败（{e}）"
    player[spec["key"]] = value
    return True, f"{spec['label']} 已替换"


def _apply_fish_list(
    plugin: Any, player: dict[str, Any], spec: dict[str, Any], edit: dict[str, Any]
) -> tuple[bool, str]:
    """鱼列表：加一条 / 删一条 / 清空 / 改第 N 条。"""
    key = spec["key"]
    current = player.get(key)
    items = current if isinstance(current, list) else []
    op = str(edit.get("op") or "edit").lower()
    if op == "clear":
        player[key] = []
        return True, f"{spec['label']} 已清空（原来 {len(items)} 条）"
    if op == "add":
        instance, why = new_fish(plugin, edit.get("value") or {})
        if instance is None:
            return False, why
        items.append(instance)
        player[key] = items
        return True, f"{spec['label']} 新增一条（现在 {len(items)} 条）"
    try:
        index = int(edit.get("index"))
    except (TypeError, ValueError):
        return False, f"{spec['label']}：要写 index（第几条，从 0 开始）"
    if index < 0 or index >= len(items):
        return False, f"{spec['label']}：没有第 {index} 条（共 {len(items)} 条）"
    if op in ("remove", "del", "delete", "删"):
        items.pop(index)
        player[key] = items
        return True, f"{spec['label']} 删掉第 {index} 条（现在 {len(items)} 条）"
    value = edit.get("value")
    if not isinstance(value, dict):
        return False, f"{spec['label']}：改第 {index} 条要写 value（一个对象）"
    ok, why = edit_fish(plugin, items[index], value)
    if not ok:
        return False, f"{spec['label']} 第 {index} 条：{why}"
    return True, f"{spec['label']} 第 {index} 条：{why}"


def apply_player_edits(
    plugin: Any, player: dict[str, Any], edits: Any, *, add: bool = False
) -> tuple[bool, str]:
    """把一批编辑应用到玩家字典上（**纯内存**，调用方负责存档与锁）。

    ``edits`` 是一个列表，按顺序应用。每一项的形态：

    * ``{"key": "gold", "value": 12345}`` —— 单字段直设
    * ``{"key": "total_caught", "value": -5, "mode": "add"}`` —— 增减（也可以靠 add=True 全局默认）
    * ``{"key": "baits", "name": "worm", "value": 20, "mode": "add"}`` —— 计数表一项
    * ``{"key": "baits", "full": {...}}`` —— 整表替换
    * ``{"key": "rods", "item": "void_rod", "mode": "add"|"remove"}`` / ``{"full": [...]}``
    * ``{"key": "inventory", "op": "add"|"remove"|"clear"|"edit", "index": 0, "value": {...}}``
    * ``{"key": "market", "value": "[]"}`` —— JSON 字段（字符串也行）
    * ``{"raw": "<整份 JSON>"}`` —— 原始 JSON 高级区：**整份替换**（会再走一遍字段校验）
    """
    if not isinstance(edits, list) or not edits:
        return False, "没有要改的字段"
    if len(edits) > MAX_EDITS:
        return False, f"一次最多改 {MAX_EDITS} 项（收到 {len(edits)} 项）"
    specs = spec_map()
    enum = enum_tables(plugin)
    done: list[str] = []

    # 原始 JSON：整份替换（放最前面，后面的编辑还能接着改）
    for edit in edits:
        if not isinstance(edit, dict) or "raw" not in edit:
            continue
        raw = edit.get("raw")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as e:
                return False, f"原始 JSON 解析失败：{e}"
        if not isinstance(raw, dict):
            return False, "原始 JSON 必须是一个对象"
        keep_id = player.get("user_id")
        player.clear()
        player.update(raw)
        if keep_id:
            player["user_id"] = keep_id          # 主键永远不许被 JSON 改掉
        done.append(f"整份 JSON 已替换（{len(raw)} 个键）")

    for edit in edits:
        if not isinstance(edit, dict):
            return False, "每一项编辑都要是一个对象"
        if "raw" in edit:
            continue
        key = str(edit.get("key") or "").strip()
        if not key:
            return False, "每一项编辑都要写 key"
        spec = specs.get(key)
        if spec is None:
            # 其它字段（存档里有、清单还没登记）：按 JSON 处理，仍可改
            spec = {"key": key, "label": key, "kind": "json", "group": "其它字段", "desc": ""}
        kind = spec.get("kind")
        if kind in READONLY_KINDS:
            return False, f"{spec.get('label')} 是只读的（{spec.get('desc') or '由代码算出来'}）"
        if kind == "fish_list":
            ok, detail = _apply_fish_list(plugin, player, spec, edit)
        elif kind == "map":
            ok, detail = _apply_map(player, spec, edit, enum)
        elif kind == "map_int":
            ok, detail = _apply_map(player, spec, edit, {})
        elif kind == "str_list":
            ok, detail = _apply_str_list(player, spec, edit, enum)
        elif kind == "json":
            ok, detail = _apply_json(player, spec, edit)
        elif kind == "count":
            mode = str(edit.get("mode") or ("add" if add else "set")).lower()
            value, why = _coerce_count(
                edit.get("value"), allow_negative=(mode in ("add", "delta"))
            )
            if why:
                return False, f"{spec.get('label')}：{why}"
            old = int(player.get(key) or 0)
            new = old + value if mode in ("add", "delta") else value
            if new < 0:
                new = 0
            limit = COUNT_MAX
            if new > limit:
                return False, f"{spec.get('label')} 最多 {limit}"
            player[key] = new
            ok, detail = True, f"{spec.get('label')} {old}→{new}"
        elif kind == "ratio":
            value, why = _coerce_ratio(edit.get("value"))
            if why:
                return False, f"{spec.get('label')}：{why}"
            player[key] = value
            ok, detail = True, f"{spec.get('label')}→{value}"
        elif kind == "int":
            value, why = _coerce_count(edit.get("value"))
            if why:
                return False, f"{spec.get('label')}：{why}"
            player[key] = value
            ok, detail = True, f"{spec.get('label')}→{value}"
        else:                                     # text / 未登记
            text, why = _coerce_text(edit.get("value"))
            if why:
                return False, f"{spec.get('label')}：{why}"
            bad = _check_enum(spec, text, enum)
            if bad:
                return False, bad
            player[key] = text
            ok, detail = True, f"{spec.get('label')}→{text or '（空）'}"
        if not ok:
            return False, detail
        done.append(detail)

    return True, "；".join(done) if done else "没有实际改动"


def clone_player(player: dict[str, Any]) -> dict[str, Any]:
    """深拷贝（调用方用它做「先试算再落盘」）。"""
    return copy.deepcopy(player)
