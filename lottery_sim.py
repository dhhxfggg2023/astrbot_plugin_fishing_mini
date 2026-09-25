# -*- coding: utf-8 -*-
"""大鱼乐模拟器 / 完整奖池（v1.18.63 起自带）。

用法（在插件目录里跑）::

    python lottery_sim.py                 # 抽 200,000 张，用你自己的配置
    python lottery_sim.py 1000000         # 抽 100 万张（更稳）
    python lottery_sim.py 200000 --seed 7 # 固定随机种子（可复现）

它会读**你正在用的那份配置**（``.astrbot/data/config/astrbot_plugin_fishing_mini_config.json``），
所以价格、奖表、概率、钓点倍率、品质权重全都是你自己设的值 ——
不会拿代码里的出厂默认值算（这是站长明确要求的口径）。

输出三块：
  1. **完整奖池**：每一档的中文说明、概率、1/概率、名义价值（按你的配置现算）
  2. **抽卡模拟**：每档「平均多少张出一份」「一半人在多少张内出」以及 80/90/99 分位
  3. **经济结论**：每张票的期望回报、庄家优势，以及「拿到任何一件也回不了本」的核对
"""
from __future__ import annotations

import json
import os
import random
import sys
from statistics import mean, median

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

#: 你正在用的那份配置（AstrBot 的插件配置就是它）
LIVE_CONFIG = os.path.join(
    os.path.expanduser("~"), ".astrbot", "data", "config",
    "astrbot_plugin_fishing_mini_config.json",
)


def load_live_config() -> dict:
    """读活配置；读不到就用出厂默认（并明确告诉你）。"""
    if os.path.isfile(LIVE_CONFIG):
        try:
            with open(LIVE_CONFIG, encoding="utf-8-sig") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data:
                print(f"✅ 用的是你的配置：{LIVE_CONFIG}")
                return data
        except Exception as exc:                                      # pragma: no cover
            print(f"⚠️ 读你的配置失败（{exc}），退回出厂默认值")
    else:
        print(f"⚠️ 没找到 {LIVE_CONFIG}，退回出厂默认值")
    return {}


def build_plugin(cfg: dict):
    """造一个插件实例（不连 AstrBot，只要能解析表/算期望就行）。"""
    import main as mod

    class _Ctx:
        def __init__(self) -> None:
            self._dir = HERE

    full = dict(mod.DEFAULTS)
    full.update(cfg or {})
    # 存档目录钉到临时位置，别碰真实存档
    full.setdefault("backup_dir", os.path.join(HERE, "_sim_backups"))
    plugin = mod.FishingPlugin(context=_Ctx(), config=full)
    plugin.name = "astrbot_plugin_fishing_mini"
    plugin.author = "sim"
    plugin.plugin_id = "sim/astrbot_plugin_fishing_mini"
    return mod, plugin


def percentile(sorted_values: list[int], q: float) -> int:
    if not sorted_values:
        return 0
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def main() -> None:
    draws = 200_000
    seed = None
    args = [a for a in sys.argv[1:]]
    if args and args[0].isdigit():
        draws = int(args[0])
        args = args[1:]
    if "--seed" in args:
        try:
            seed = int(args[args.index("--seed") + 1])
        except Exception:
            seed = None

    mod, plugin = build_plugin(load_live_config())
    lot = mod.LOTTERY
    rows = lot.parse_prize_rows(plugin.cfg.get("lottery_prizes"))
    if not rows:
        print("❌ 奖表是空的（配置 lottery_prizes 没内容）")
        return
    price = max(0, int(mod._safe_int(plugin.cfg.get("lottery_ticket_price"), 0)))
    daily = max(0, int(mod._safe_int(plugin.cfg.get("lottery_daily_limit"), 0)))
    per_call = max(1, int(mod._safe_int(plugin.cfg.get("lottery_max_per_call"), 30)))
    pity = max(0, int(mod._safe_int(plugin.cfg.get("lottery_pity_count"), 0)))
    jack_gold = max(0, int(mod._safe_int(plugin.cfg.get("lottery_jackpot_gold"), 0)))
    jack_id = str(plugin.cfg.get("lottery_jackpot_prize") or "")
    total_w = sum(float(r.get("weight") or 0.0) for r in rows)

    print(f"\n票价 {price:,} 金/张｜每日限购 {daily or '不限'} 张｜一次最多 {per_call} 张"
          f"｜连输保底 {pity or '关'} 张｜头奖档 = {jack_id or '（没设）'}")
    if not price:
        print("⚠️ 票价是 0：这套奖表等于无条件发奖（没有「亏钱」可言）")

    # ---------------- 1. 完整奖池 ----------------
    print("\n" + "=" * 108)
    print("① 完整奖池（名义价值按**你的配置**现算：鱼价 × 钓点倍率 × 品质权重 × 个体差异）")
    print("=" * 108)
    print(f"{'奖档':<14}{'说明':<30}{'概率':>10}{'约 1/':>9}{'名义价值':>12}{'≈几张票':>10}{'单档期望':>10}")
    print("-" * 108)
    ev_total = 0.0
    for row in sorted(rows, key=lambda r: -float(r.get("weight") or 0.0)):
        p = float(row.get("weight") or 0.0) / total_w if total_w else 0.0
        val = lot.prize_payout(plugin, row, jack_gold, jack_id)
        tickets = (val / price) if price else 0.0
        ev_total += p * val
        desc = str(row.get("desc") or "")[:28]
        print(f"{str(row.get('id')):<14}{desc:<30}{p:>9.4%}{max(1, round(1 / p)) if p else 0:>9,}"
              f"{val:>12,.0f}{tickets:>10.2f}{p * val:>10,.0f}")
    print("-" * 108)
    print(f"{'合计':<14}{'':<30}{'100.0000%':>10}{'':>9}{'':>12}{'':>10}{ev_total:>10,.0f}")
    ratio = (ev_total / price) if price else 0.0
    print(f"\n每张票期望回报 {ev_total:,.0f} 金 = 票价的 {ratio:.2%}"
          f"（{'庄家优势 ' + format(1 - ratio, '.2%') if ratio < 1 else '⚠️ 这是印钞机：期望 ≥ 票价'}）")
    print(f"连输保底（{pity} 张）会把这个数略微抬高，量级在 1% 以内。")

    # ---------------- 2. 抽卡模拟 ----------------
    print("\n" + "=" * 108)
    print(f"② 抽卡模拟：连抽 {draws:,} 张" + (f"（随机种子 {seed}）" if seed is not None else "") +
          " —— 每档「多少张能出一份」")
    print("=" * 108)
    rng = random.Random(seed)
    weights = [float(r.get("weight") or 0.0) for r in rows]
    ids = [str(r.get("id")) for r in rows]
    cum: list[float] = []
    acc = 0.0
    for w in weights:
        acc += w
        cum.append(acc)

    def roll() -> int:
        point = rng.random() * (cum[-1] or 1.0)
        for i, edge in enumerate(cum):
            if point <= edge:
                return i
        return len(cum) - 1

    seen: dict[int, list[int]] = {i: [] for i in range(len(rows))}
    counts = [0] * len(rows)
    last: dict[int, int] = {}
    for n in range(1, draws + 1):
        idx = roll()
        counts[idx] += 1
        if idx in last:
            seen[idx].append(n - last[idx])
        last[idx] = n

    print(f"{'奖档':<14}{'说明':<26}{'实际概率':>10}{'命中次数':>10}{'平均间隔':>10}"
          f"{'中位':>8}{'80%':>8}{'90%':>8}{'99%':>9}")
    print("-" * 108)
    for i, row in enumerate(sorted(range(len(rows)), key=lambda k: -weights[k])):
        gaps = sorted(seen[row])
        p_hat = counts[row] / draws
        gaps_txt = (
            f"{mean(gaps):>10,.0f}{median(gaps):>8,.0f}"
            f"{percentile(gaps, 0.8):>8,}{percentile(gaps, 0.9):>8,}{percentile(gaps, 0.99):>9,}"
            if gaps else f"{'—':>10}{'—':>8}{'—':>8}{'—':>8}{'—':>9}"
        )
        print(f"{ids[row]:<14}{str(rows[row].get('desc') or '')[:24]:<26}"
              f"{p_hat:>9.4%}{counts[row]:>10,}{gaps_txt}")
    print("-" * 108)
    print("「平均间隔」= 平均多少张出一份；「中位」= 一半的人在这么多张内能出；")
    print("80/90/99% = 这么多次里至少出一次的张数（运气差的那条尾巴有多长）。")

    # ---------------- 3. 限定奖的「抽到也亏」核对 ----------------
    print("\n" + "=" * 108)
    print("③ 限定奖核对：抽到任何一件，是否都回不了本")
    print("=" * 108)
    print(f"{'奖档':<14}{'名义价值':>12}{'抽到需期望张数':>16}{'那些票的成本':>14}{'结论':>12}")
    print("-" * 108)
    limited = [r for r in rows if str(r.get("kind")) in ("rod", "bait") and
               (str(r.get("id")) not in ("bait_pack",))]
    for row in limited:
        p = float(row.get("weight") or 0.0) / total_w if total_w else 0.0
        val = lot.prize_payout(plugin, row, jack_gold, jack_id)
        if p <= 0:
            continue
        cost = (1 / p) * price
        verdict = "亏" if val < cost else "⚠️ 赚"
        print(f"{str(row.get('id')):<14}{val:>12,.0f}{1 / p:>16,.0f}{cost:>14,.0f}{verdict:>12}")
    print("-" * 108)

    # ---------------- 4. 抽满一整天 ----------------
    if daily:
        print(f"\n④ 每日限购 {daily} 张：一天最多花 {daily * price:,} 金，"
              f"期望收回 {daily * ev_total:,.0f} 金（净亏 {daily * (price - ev_total):,.0f} 金/天）")
        print(f"   按一次最多 {per_call} 张算，一天要点 {max(1, -(-daily // per_call))} 次连抽。")


if __name__ == "__main__":
    main()
