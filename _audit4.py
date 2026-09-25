# -*- coding: utf-8 -*-
"""审计脚本 1：白名单差集 / 读档崩溃面 / 迁移副作用（只读，不改插件代码）。"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import traceback
from typing import Any

sys.path.insert(0, ".")
import test_local as tl  # noqa: E402
import main as m  # noqa: E402

CALC = m.CALC
SEP = "=" * 78


def show(title: str) -> None:
    print(f"\n{SEP}\n{title}\n{SEP}")


# ---------------------------------------------------------------- 方向 1
show("方向1a：_default_player 的键 vs _repair_player 重建后的键（差集）")
fresh = m._default_player("UID")
fixed, _mg = m._repair_player(copy.deepcopy(fresh), "UID")
lost = sorted(set(fresh) - set(fixed))
extra = sorted(set(fixed) - set(fresh))
print("default 键数 =", len(fresh), " repair 输出键数 =", len(fixed))
print("★读档会丢的键（在 default 里、repair 没产出）：", lost or "无")
print("repair 多出来的键：", extra or "无")

# default 里的重复键？
import re  # noqa: E402
src = open("_calc.py", encoding="utf-8").read()
block = src[src.index("def _default_player"):src.index("#: 升级曲线参数")]
keys = re.findall(r'^\s{8}"([a-z_0-9]+)":', block, re.M)
dups = [k for k in set(keys) if keys.count(k) > 1]
print("_default_player 里字面重复写了两遍的键：", dups or "无")

show("方向1b：_new_instance 的键 vs _repair_instance 重建后的键（差集）")
fish_id = m.FISH_POOL[0]["id"]
inst = CALC._new_instance(fish_id, 1.0)
rinst = CALC._repair_instance(copy.deepcopy(inst))
lost_i = sorted(set(inst) - set(rinst))
print("new_instance 键：", sorted(inst))
print("repair_instance 键：", sorted(rinst))
print("★读档会丢的鱼字段：", lost_i or "无")

# 值也要活下来（键在 ≠ 值在）
show("方向1c：值级 round-trip（键在但值被改写/归零的字段）")
pl = m._default_player("UID")
probe = {
    "gold": 987654, "total_caught": 4321, "total_sold": 111, "total_fed": 22,
    "total_orders": 7, "perfect_pulls": 3, "clutch_wins": 2,
    "lottery_loses": 5, "lottery_total": 9, "rod_level": 4,
    "stamina": 17, "stamina_ts": 1600000000, "pond_last_ts": 1600000123,
    "pond_claimed_ts": 1600000456, "pond_best_income": 777,
    "market_best_bonus": 55, "last_fish_time": 1600000999,
    "luck_charges": 1.5, "buff_casts_left": 6, "buff_quality": 0.8,
    "buff_floor_casts": 3, "buff_floor": 2.0, "offering_ts": 1600001111,
    "limited_rod_casts": 11, "limited_uses": {"tide_rod": 3},
    "last_platform": "aiocqhttp", "last_name": "老张",
    "weather_date": "2026-09-25", "market_date": "2026-09-25",
    "daily_date": "2026-09-25", "daily_used": {"buff": 2, "heal": 1},
    "order_date": "2026-09-25", "order_next_ts": 1600002222,
    "order_location": "novice", "order_move_rerolls": 2,
    "sign_date": "x", "last_sign_date": "2026-09-25",
    "last_income_date": "2026-09-24", "aquarium_slots": ["s1"],
    "backpack_slots": [0, 1], "equipped_bait": "worm", "auto_buff_item": "lucky",
    "title": "t1", "titles": ["t1"], "equipped_rod": "bamboo",
    "current_location": "novice", "achievements": [], "milestones": [10],
    "bottle_notes": ["a"], "variants": {}, "collectibles": {},
    "baits": {"worm": 5}, "items": {"lucky_charm": 2}, "decorations": [],
    "collection": {}, "best_records": {}, "orders": [], "market": [],
}
pl.update(probe)
pl["story"] = {"arc": "", "ep": 4, "flags": {"fed": True}, "since": 2,
               "seen": ["e1"], "done": ["c1"], "last": "上次"}

# 合法实例（含台账/喂食/缸内计时）
good = CALC._new_instance(fish_id, 1.2)
good.update({"feed_uses": 4, "feed_bonus": 2, "feed_debt": 1, "live_bonus": 33,
             "locked": True, "tank_since": 1600000000, "feed_bonus_used": 3,
             "reroll_day": "2026-09-25", "reroll_today": 1,
             "log": [{"item": "reroll_pill", "ts": 1, "before": {"value": 10}}]})
pl["inventory"] = [copy.deepcopy(good)]
pl["aquarium"] = [copy.deepcopy(good)]
pl["collection"] = {fish_id: {"count": 2, "best_value": 999, "first_ts": 5}}

rt, _ = m._repair_player(copy.deepcopy(pl), "UID")
bad = []
for key, want in probe.items():
    if key not in rt:
        bad.append(f"{key}: 键丢了")
    elif rt[key] != want:
        bad.append(f"{key}: {want!r} -> {rt[key]!r}")
if rt.get("story", {}).get("ep") != 4:
    bad.append(f"story.ep: 4 -> {rt.get('story', {}).get('ep')!r}")
for idx, where in ((0, "inventory"), (0, "aquarium")):
    ri = rt[where][idx]
    for k in ("feed_uses", "feed_bonus", "feed_debt", "live_bonus", "locked",
              "tank_since", "feed_bonus_used", "reroll_day", "reroll_today", "log"):
        if ri.get(k) != good.get(k):
            bad.append(f"{where}[0].{k}: {good.get(k)!r} -> {ri.get(k)!r}")
print("★值/键对不上的字段：")
for line in bad or ["无"]:
    print("   -", line)

# ---------------------------------------------------------------- 方向 3
show("方向3：畸形存档 -> _repair_player 是否抛异常（抛了就整档重置成新号）")
GOLD = 987654321
base = m._default_player("UID")
base["gold"] = GOLD
base["total_caught"] = 4242
base["inventory"] = [copy.deepcopy(good)]
base["aquarium"] = [copy.deepcopy(good)]

HOSTILE = [None, [], {}, "x", 123, -1, True, [None], [1, "a"], {"a": 1},
           [[1]], float("nan"), float("inf"), [{}], [["x"]]]

FIELDS = sorted(set(base) | {"event", "limited_uses", "daily_used", "story"})
fails: list[tuple[str, Any, str]] = []
for field in FIELDS:
    for hv in HOSTILE:
        raw = copy.deepcopy(base)
        raw[field] = copy.deepcopy(hv)
        try:
            out, _ = m._repair_player(raw, "UID")
        except Exception as e:
            fails.append((field, hv, f"抛出 {type(e).__name__}: {e}"))
            continue
        if out.get("gold") != GOLD:
            fails.append((field, hv, "整档被重置成新号（gold 变成 %r）" % out.get("gold")))
print(f"★字段 x 畸形值 共 {len(FIELDS) * len(HOSTILE)} 组，事故 {len(fails)} 组：")
seen = set()
for field, hv, why in fails:
    sig = (field, why.split("（")[0])
    if sig in seen:
        continue
    seen.add(sig)
    print(f"   - {field} = {hv!r:>18} -> {why}")

show("方向3b：鱼实例（inventory/aquarium 元素）畸形值")
inst_fails = []
for field in sorted(good):
    for hv in HOSTILE:
        raw = copy.deepcopy(base)
        broken = copy.deepcopy(good)
        broken[field] = copy.deepcopy(hv)
        raw["inventory"] = [broken]
        raw["aquarium"] = [broken]
        try:
            out, _ = m._repair_player(raw, "UID")
        except Exception as e:
            inst_fails.append((field, hv, f"抛出 {type(e).__name__}: {e}"))
            continue
        if out.get("gold") != GOLD:
            inst_fails.append((field, hv, "整档被重置成新号"))
print(f"★鱼字段 x 畸形值 共 {len(good) * len(HOSTILE)} 组，事故 {len(inst_fails)} 组：")
seen = set()
for field, hv, why in inst_fails:
    if (field, why) in seen:
        continue
    seen.add((field, why))
    print(f"   - fish.{field} = {hv!r:>18} -> {why}")

show("方向3c：_repair_instance 单独跑（元素级），畸形值下返回 None = 鱼被吞掉")
eaten = []
for field in sorted(good):
    for hv in HOSTILE:
        broken = copy.deepcopy(good)
        broken[field] = copy.deepcopy(hv)
        try:
            out = CALC._repair_instance(broken)
        except Exception as e:
            eaten.append((field, hv, f"抛出 {type(e).__name__}: {e}"))
            continue
        if out is None:
            eaten.append((field, hv, "返回 None（这条鱼直接消失）"))
print("★被吞掉的组合：", len(eaten))
for field, hv, why in eaten[:15]:
    print(f"   - {field} = {hv!r:>18} -> {why}")
