# -*- coding: utf-8 -*-
"""展示渲染：背包 / 商店 / 图鉴 / 结果文案 / 帮助页 / 按钮行。

这些方法原本散在 main.py 各处（7000 行里找一段文案很痛苦），现在集中到这里。
它们用到 main.py 的常量与工具函数（FISH_BY_ID、_fmt_gold、_instance_line…），
共享方式是「main 在模块末尾把自己的全局注入本模块」，所以下面的代码可以像
还在主文件里一样直接引用，一行都不用改。

⚠️ 维护约定：本模块只做**展示**，不要在这里改玩家数据；也不要对共享的
模块级变量做重新赋值（`X = ...` 只会改到本模块的副本），需要改就原地改。
"""

from __future__ import annotations

import time
from typing import Any


class ViewsMixin:
    """展示相关方法（由 FishingPlugin 继承，见 main.py 的类定义）。"""

    def _rarity_name(self, rarity: str) -> str:
        """按配置把品质名映射成展示名（下标即稀有度）。"""
        idx = RARITY_RANK.get(rarity, 0)
        names = self.cfg.get("rarity_display_names") or []
        if 0 <= idx < len(names):
            return str(names[idx])
        return rarity

    def _rod_label(self, player: dict[str, Any]) -> str:
        rod = self._rod(player)
        return f"{rod.get('emoji', '')}{rod.get('name', '鱼竿')}"

    def _location_label(self, player: dict[str, Any]) -> str:
        loc = self._location(player)
        return f"{loc.get('emoji', '')}{loc.get('name', '钓点')}"

    def _weather_label(self, player: dict[str, Any]) -> str:
        weather = self._weather(player)
        return f"{weather['emoji']}{weather['name']}" if weather else ""

    def _market_label(self, player: dict[str, Any]) -> str:
        parts = []
        for entry in player.get("market") or []:
            if not isinstance(entry, dict):
                continue
            fish = FISH_BY_ID.get(entry.get("fish_id", ""))
            if fish is None:
                continue
            mult = _safe_number(entry.get("mult"), 1.0)
            parts.append(
                f"{_fish_emoji(fish)}{fish['name']}+{(mult - 1) * 100:.0f}%"
            )
        return "　".join(parts)

    # -------------------------------------------------------------------------
    # 变异个体
    # -------------------------------------------------------------------------

    def _bag_rows(self) -> list[list[dict[str, Any]]]:
        """背包视图的按钮：卖光光 / 水族馆 / 再来一竿。"""
        return [[
            self._btn("卖光光", "/钓鱼 卖光光"),
            self._btn("水族馆", "/钓鱼 水族馆"),
            self._btn("再来一竿", "/钓鱼"),
        ]]

    def _location_rows(self) -> list[list[dict[str, Any]]]:
        """钓点视图的按钮：图鉴 / 查看背包。"""
        return [[
            self._btn("查图鉴", "/钓鱼 图鉴"),
            self._btn("背包", "/钓鱼 背包"),
            self._btn("今日", "/钓鱼 今日"),
        ]]

    def _cast_rows(self) -> list[list[dict[str, Any]]]:
        """抛竿结果下面的常用按钮。"""
        return [
            [
                self._btn("再来一竿", "/钓鱼"),
                self._btn("看背包", "/钓鱼 背包"),
                self._btn("今日", "/钓鱼 今日"),
            ],
            [self._btn("卖光光", "/钓鱼 卖光光"), self._btn("帮助", "/钓鱼 帮助")],
        ]

    def _pull_rows(self) -> list[list[dict[str, Any]]]:
        """咬钩提示下面的按钮。"""
        return [[self._btn("拉线！", "/钓鱼 拉", style=4)]]

    def _event_rows(self, event_def: dict[str, Any]) -> list[list[dict[str, Any]]]:
        rows: list[list[dict[str, Any]]] = []
        for index, choice in enumerate(event_def.get("choices") or [], 1):
            rows.append([self._btn(f"{choice['label']}", f"/钓鱼 事件 {index}")])
        return rows

    # -------------------------------------------------------------------------
    # 随机小插曲
    # -------------------------------------------------------------------------

    def _event_prompt(
        self, event_def: dict[str, Any], rows: bool = True
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """插曲的文案 + 按钮。"""
        lines = [f"❔ {event_def['text']}"]
        for index, choice in enumerate(event_def.get("choices") or [], 1):
            lines.append(f"　{index}. {choice['label']}　/钓鱼 事件 {index}")
        text = "\n".join(lines)
        return text, (self._event_rows(event_def) if rows else [])

    def _milestone_text(self, player: dict[str, Any]) -> str:
        """刚达到里程碑时返回文案并标记；否则返回空串。"""
        caught = _safe_int(player.get("total_caught"), 0, 0)
        shown = player.setdefault("milestones", [])
        if not isinstance(shown, list):
            shown = []
            player["milestones"] = shown
        if caught in MILESTONES and caught not in shown:
            shown.append(caught)
            return MILESTONES[caught]
        return ""

    def _best_records_text(self, player: dict[str, Any]) -> list[str]:
        """渲染「最佳渔获」几行，供图鉴页展示。"""
        records = player.get("best_records") or {}
        if not isinstance(records, dict) or not records:
            return []
        lines = ["【最佳渔获】"]
        for rarity in reversed(RARITY_ORDER):  # 从高到低
            rec = records.get(rarity)
            if not isinstance(rec, dict):
                continue
            fish = FISH_BY_ID.get(rec.get("fish_id", ""))
            if fish is None:
                continue
            variant = rec.get("variant")
            vtag = ""
            if variant and variant in VARIANT_BY_ID:
                vcfg = VARIANT_BY_ID[variant]
                vtag = f"{vcfg['emoji']}{vcfg['name']}"
            quality = rec.get("quality", "")
            lines.append(
                f"　{self._rarity_name(rarity)}：{vtag}{fish['name']}"
                f"　{QUALITY_EMOJI.get(quality, '')}{quality}"
                f"　{_fmt_gold(rec.get('value', 0))} 金币"
            )
        return lines if len(lines) > 1 else []

    # -------------------------------------------------------------------------
    # 图鉴集齐的永久加成
    # -------------------------------------------------------------------------

    def _codex_mult_text(self, player: dict[str, Any]) -> str:
        mult = self._codex_mult(player)
        return f"+{(mult - 1) * 100:.0f}%" if mult > 1.0 else "无"

    def _bait_label(self, bait_id: str) -> str:
        bait = self.baits.get(bait_id)
        if not bait:
            return "🪝空钩"
        return f"{bait.get('emoji', '')}{bait.get('name', bait_id)}"

    def _item_label(self, item_id: str) -> str:
        item = self.items.get(item_id)
        if not item:
            return item_id
        return f"{item.get('emoji', '')}{item.get('name', item_id)}"

    def _format_result(
        self,
        player: dict[str, Any],
        catch: dict[str, Any],
        bait_id: str,
        total_cost: int,
        rating: str | None,
    ) -> str:
        """上鱼结果，尽量简短。"""
        head = f"🎣 {_instance_line(catch)}"
        if rating:
            mark = {"完美": "🎯", "良好": "👍", "偏差": "😅"}.get(rating, "")
            head = f"{mark}{rating}！{head}"
        fish = FISH_BY_ID.get(catch.get("fish_id", ""))
        if fish and fish["rarity"] in ("传说", "神话"):
            head += f"　{fish['flavor']}"
        lines = [head, f"　{_attrs_line(catch)}　余额 {_fmt_gold(player.get('gold', 0))}"]
        return "\n".join(lines)

    def _order_wait_text(self, player: dict[str, Any]) -> str:
        """还有多久刷新（给玩家一个明确的等待预期）。"""
        now = int(time.time())
        next_ts = _safe_int(player.get("order_next_ts"), 0, 0)
        if next_ts <= now:
            return "随时会来新单"
        minutes = max(1, (next_ts - now) // 60)
        if minutes < 60:
            return f"约 {minutes} 分钟后刷新"
        hours, rest = minutes // 60, minutes % 60
        if rest < 5:
            return f"约 {hours} 小时后刷新"
        return f"约 {hours} 小时 {rest} 分钟后刷新"

    @staticmethod
    def _sort_aquarium(aquarium: list[dict[str, Any]]) -> None:
        """把缸里的鱼排成固定顺序（变异 > 个体品质 > 鱼种品质 > 价值）。

        展示顺序和「取出/卖出 N」的序号必须一致，否则玩家会对不上号。
        """
        try:
            aquarium.sort(key=_sort_key)
        except Exception:
            pass

    def _aquarium_view(self, player: dict[str, Any]) -> str:
        aquarium: list[dict[str, Any]] = player.get("aquarium") or []
        self._sort_aquarium(aquarium)
        capacity = self._aquarium_capacity(player)
        lines = [f"🐠 水族馆 {len(aquarium)}/{capacity}"]
        if not aquarium:
            lines.append("　（空缸）　/钓鱼 水族馆 放 1 放鱼进来")
            return "\n".join(lines)

        total = 0
        best = None
        for idx, instance in enumerate(aquarium, start=1):
            value = _instance_value(instance)
            total += value
            if best is None or value > _instance_value(best):
                best = instance
            feed = _safe_int(instance.get("feed_uses"), 0, 0)
            lines.append(
                f"{idx:>2}.{_instance_line(instance)}　{_attrs_line(instance)}"
                + (f" 喂{feed}" if feed else "")
            )
        lines.append(f"🧮 估值 {_fmt_gold(total)}（取出/卖出 ×{self.cfg['aquarium_bonus']:g}）")
        if best is not None:
            lines.append(f"👑 镇馆之宝：{_instance_line(best)}")
        # 今日收益提示（与「/钓鱼 水族馆 领」的结算口径完全一致）
        today = self._today_text()
        if player.get("last_income_date") != today:
            now_ts = int(time.time())
            last = _safe_int(player.get("pond_last_ts"), 0, 0) or now_ts
            hours = min(
                (now_ts - last) / 3600.0,
                float(self.cfg["pond_income_cap_hours"]),
            )
            est = min(
                int(total * float(self.cfg["pond_income_per_hour"]) * hours),
                int(self.cfg["pond_income_cap_coins"]),
            )
            if est > 0:
                lines.append(f"💰 今日可领 {_fmt_gold(est)}　/钓鱼 水族馆 领")
        return "\n".join(lines)

    def _shop_view(self, player: dict[str, Any]) -> str:
        """商店货架：**只上架已解锁的东西**（未达等级/缺鱼竿的整条不显示）。"""
        baits = player.get("baits") or {}
        items = player.get("items") or {}
        lines = [
            f"🛒 商店　💰 {_fmt_gold(player.get('gold', 0))}",
            f"🎣 当前鱼饵：{self._bait_label(player.get('equipped_bait', 'none'))}",
            "— 鱼饵 —",
        ]
        hidden = 0
        for bait_id in self._bait_list():
            bait = self.baits[bait_id]
            if self._unlock_shortage(player, bait):
                hidden += 1
                continue
            owned = _safe_int(baits.get(bait_id), 0, 0)
            lines.append(
                f"　{self._bait_label(bait_id)} {bait['price']}金/个"
                f"　持有{owned}　手气{_luck_stars(bait.get('luck'), 0.7)}"
                f"　{bait.get('desc', '')}"
            )
        lines.append("— 道具 —")
        for item_id in self._item_list():
            item = self.items[item_id]
            if self._unlock_shortage(player, item):
                hidden += 1
                continue
            owned = _safe_int(items.get(item_id), 0, 0)
            lines.append(
                f"　{self._item_label(item_id)} {item['price']}金　持有{owned}"
                f"　{item['desc']}"
            )
        if hidden:
            # 只说「还有」，不剧透清单、也不写等级数字
            lines.append("🔒 还有更多鱼饵与道具，等级更高 / 换上更好的竿之后会陆续上架")
        return "\n".join(lines)

    def _help_pages(self) -> list[tuple[str, list[str]]]:
        """帮助分页内容：(标题, 行列表)。每页都尽量短，避免刷屏。"""
        cfg = self.cfg
        cd = (
            "无冷却"
            if int(cfg["cooldown_seconds"]) <= 0
            else f'{int(cfg["cooldown_seconds"])}s'
        )
        interactive = "/".join(
            sorted(self.interactive_rarities, key=lambda r: RARITY_RANK.get(r, 0))
        )
        rarity_line = " < ".join(self._rarity_name(r) for r in RARITY_ORDER)
        base_cap = _backpack_capacity({"backpack_slots": []}, cfg)

        loc_lines = [
            f"　{loc['emoji']}{loc['name']}　×{loc['value_mult']:.2f}"
            f"　需{loc['level_gate']}级"
            + (f"/{_fmt_gold(loc['gold_gate'])}金" if loc["gold_gate"] else "")
            for loc in sorted(
                self.locations,
                key=lambda l: (_safe_int(l.get("level_gate"), 1, 1),
                               _safe_int(l.get("gold_gate"), 0, 0)),
            )
        ]
        rod_lines = [
            f"　{rod['emoji']}{rod['name']}　{_fmt_gold(rod['price'])}金"
            f"　价值+{rod['value_bonus']:.0%}　手气{_luck_stars(rod['luck_bonus'], 0.2)}"
            + (f"　需{rod['unlock_level']}级" if rod.get("unlock_level", 1) > 1 else "")
            for rod in sorted(self.rods, key=lambda r: _safe_int(r.get("price"), 0, 0))
        ]
        bait_lines = [
            f"　{self.baits[b]['emoji']}{self.baits[b]['name']}　"
            f"{self.baits[b]['price']}金/个　"
            f"手气{_luck_stars(self.baits[b]['luck'], 0.7)}"
            + (
                f"　需{self._rod_need_text(self.baits[b])}"
                if self._rod_need_text(self.baits[b])
                else ""
            )
            for b in self._bait_list()
        ]
        item_lines = [
            f"　{self._item_label(iid)}　{self.items[iid]['price']}金　"
            f"{self.items[iid]['desc']}"
            for iid in self._item_list()
        ]

        fee_line = (
            f"　下竿免费（空钩不花钱）　冷却 {cd}　签到 {cfg['sign_reward']}"
            if int(cfg["fish_cost"]) <= 0
            else f"　钓费 {cfg['fish_cost']}/竿　冷却 {cd}　签到 {cfg['sign_reward']}"
        )
        pages: list[tuple[str, list[str]]] = [
            (
                "基础",
                [
                    "　/钓鱼　　　　　下竿（写 钓/抛竿 也行）",
                    "　/钓鱼 拉　　　 拉线（也可写 收线/提竿）",
                    "　/钓鱼 背包　　 看背包",
                    "　/钓鱼 卖光光　 清空背包换金币",
                    "　/钓鱼 换饵 蚯蚓　换鱼饵（换饵 空钩 不花钱）",
                    "　/钓鱼 金币　　 档案",
                    "　/钓鱼 签到　　 每日金币",
                    "　/钓鱼 今日　　 今日天气与行情",
                    "　/钓鱼 排行　　 群内排行榜",
                    "　/钓鱼 锁定 1　 锁定不想卖的鱼",
                    "　/钓鱼 事件 1　 水面上偶尔会有事发生",
                    fee_line,
                ],
            ),
        ]

        # 钓点有 16 个，一页放不下：每 6 个一页
        per_page = 6
        chunks = [
            loc_lines[i : i + per_page] for i in range(0, len(loc_lines), per_page)
        ] or [[]]
        for index, chunk in enumerate(chunks, 1):
            title = "钓点" if len(chunks) == 1 else f"钓点 {index}/{len(chunks)}"
            extra = (
                [
                    "　/钓鱼 去 <名称>　　　 前往",
                    "　/钓鱼 钓点 解锁 <名>　解锁",
                ]
                if index == len(chunks)
                else []
            )
            pages.append((title, chunk + extra))

        pages.extend(
            [
                ("鱼竿", rod_lines + [
                    "　🔒 的竿要等级达标才能买",
                    "　/钓鱼 鱼竿 买 <名> ｜ 用 <名>",
                ]),
                (
                    "鱼饵与道具",
                    bait_lines
                    + [
                        "　— 道具 —",
                    ]
                    + item_lines
                    + [
                        "　部分鱼饵要等级 + 对应鱼竿才能买",
                        "　/钓鱼 商店 买 <名> [个数]",
                        "　/钓鱼 用 <道具> [栏位]",
                    ],
                ),
                (
                    "养成与赚钱",
                    [
                        "　/钓鱼 水族馆　　　　 放/取/卖/领/扩建",
                        "　/钓鱼 订单　　　　　 订单（不定时刷新，收益更高）",
                        "　/钓鱼 订单 交 1 2　　批量交单（交过的不再收）",
                        "　/钓鱼 商店 扩容　　　背包扩容",
                        f"　背包上限 {base_cap} 起，不能无限囤货",
                        "　养鱼提升肉质/灵性/光泽 → 直接涨价",
                    ],
                ),
                (
                    "收集与社交",
                    [
                        "　/钓鱼 查 <鱼名>　　 这条鱼在哪些钓点",
                    "　/钓鱼 查 <钓点名>　 这个钓点有哪些鱼",
                    "　/钓鱼 图鉴　　　　　 鱼的收集进度",
                        "　/钓鱼 杂物　　　　　 杂物与纸条收集",
                    ],
                ),
                (
                    "品质与拉线",
                    [
                        f"　鱼种：{rarity_line}（固有，不可变）",
                        "　个体：⚪普通 🟢优良 🔵稀有 🟣极品 🌟传说",
                        f"　只有 {interactive} 需要拉线",
                        "　🎯完美 > 👍良好 > 😅偏差，超时鱼会跑",
                        f"　图鉴 {len(FISH_POOL)} 种　成就 {len(ACHIEVEMENTS)} 个",
                        "　累计钓获 1/10/25/50/100… 有里程碑",
                    ],
                ),
            ]
        )
        return pages

    def _help_text(self, page: int = 1) -> str:
        """分页帮助，避免一次性输出过多文字。"""
        pages = self._help_pages()
        total = len(pages)
        page = int(_clamp(page, 1, total))
        title, lines = pages[page - 1]
        head = f"🎣 帮助 {page}/{total} · {title}"
        tail = (
            f"💡 /钓鱼 帮助 {page + 1}"
            if page < total
            else "💡 /钓鱼 帮助 1 回到第一页"
        )
        return "\n".join([head, ""] + lines + ["", tail])
