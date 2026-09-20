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

    def _scene_buttons(self, scene: str) -> list[tuple[str, str, int]]:
        """取某场景的按钮：配置优先，缺失或整段被过滤光则回退内置默认。

        ``button_empty_scenes`` 里列出的场景直接空 —— 站长要的是「这个场景就是没按钮」。
        """
        if scene in BUTTON_EMPTY_SCENES:
            return []
        items = BUTTONS.get(scene) or []
        if not items:
            items = _BUILTIN_BUTTONS.get(scene) or []
        return list(items)

    def _scene_configured_rows(self, scene: str) -> list[tuple[str, str, int]]:
        """站长在 button_defs 里**专门给这个场景**配的按钮（不含继承、不含内置）。"""
        return list(BUTTONS.get(scene) or [])

    def _scene_source(self, scene: str) -> str:
        """该场景按钮的来源：``config`` / ``inherit`` / ``default`` / ``none`` / ``off``。

        编辑器页面用它显示「这组按钮是哪来的」，排查「为什么这里没按钮」很有用。
        """
        if scene in BUTTON_EMPTY_SCENES:
            return "off"
        if self._scene_configured_rows(scene):
            return "config"
        parent = SCENE_PARENT.get(scene) or ""
        if parent and (BUTTONS.get(parent) or _BUILTIN_BUTTONS.get(parent)):
            return "inherit"
        if _BUILTIN_BUTTONS.get(scene):
            return "default"
        return "none"

    def _scene_items(self, scene: str) -> list[tuple[str, str, int]]:
        """取某场景生效的按钮（元组形态）：自己配的 > 继承父场景 > 内置默认。

        ``button_empty_scenes`` 里列出的场景**直接返回空**：站长明确说不要按钮时，
        连「继承父场景」也要断掉（否则删了一个子场景的按钮，父场景的又会冒出来）。
        """
        if scene in BUTTON_EMPTY_SCENES:
            return []
        items = BUTTONS.get(scene) or []
        if not items:
            # 父场景也可能只有出厂按钮（站长没配过 cast，但 cast.hit 要继承它的按钮）
            parent = SCENE_PARENT.get(scene) or ""
            if parent:
                items = BUTTONS.get(parent) or _BUILTIN_BUTTONS.get(parent) or []
        if not items:
            items = _BUILTIN_BUTTONS.get(scene) or []
        return list(items)

    def _scene_rows(self, scene: str) -> list[list[dict[str, Any]]]:
        """取某场景生效的按钮行（可直接塞给 ``_say``）。

        「继承」是为了**升级前后默认行为逐字不变**：老版本只有 5 个场景，
        现在同一个回复有了更细的场景键，没单独配时就沿用老场景那一组。
        """
        return self._layout_rows(scene, self._scene_items(scene))

    def _scene_text(
        self, scene: str | None, text: str, values: dict[str, Any] | None = None
    ) -> str:
        """按场景叠加 ``text_overrides`` 里的文案覆盖；没覆盖或渲染失败一律用原文。

        与按钮同一套继承规则：某场景没单独配文案时，看它的父场景有没有配
        （于是 ``cast|🎣 {原文}`` 能给一整组下竿回复统一加前缀）。
        """
        if not scene:
            return text
        template = TEXT_OVERRIDES.get(scene)
        if not template:
            parent = SCENE_PARENT.get(scene) or ""
            if parent:
                template = TEXT_OVERRIDES.get(parent)
        if not template:
            return text
        return TEXT_LIB.render_scene(scene, text, values, {scene: template})

    def _layout_rows(
        self, scene: str, items: list[tuple[str, str, int]]
    ) -> list[list[dict[str, Any]]]:
        """把 ``[(文案, 指令, 样式), ...]`` 按「每行几个」排成按钮行。"""
        per_row = scene_rows_per_row(scene)
        rows: list[list[dict[str, Any]]] = []
        for start in range(0, len(items), per_row):
            rows.append([
                self._btn(text, data, style)
                for text, data, style in items[start:start + per_row]
            ])
        return rows

    def _button_rows(
        self, scene: str, label: str = "", n: int = 0
    ) -> list[list[dict[str, Any]]]:
        """按配置生成按钮行：每行最多 ``scene_rows_per_row(scene)`` 个。

        ``label`` / ``n`` 供 story 场景的模板占位符 ``{label}`` / ``{n}`` 使用；
        其它场景没有占位符时就是原样文案。内容全部来自配置项 ``button_defs``。
        """
        items = self._scene_buttons(scene)
        per_row = scene_rows_per_row(scene)
        rows: list[list[dict[str, Any]]] = []
        for start in range(0, len(items), per_row):
            rows.append([
                self._btn(
                    _fill_button_text(text, label, n),
                    _fill_button_text(data, label, n),
                    style,
                )
                for text, data, style in items[start:start + per_row]
            ])
        return rows

    def _bag_rows(self) -> list[list[dict[str, Any]]]:
        """背包视图的按钮（button_defs 的 bag.list 行，未配置则继承 bag）。"""
        return self._scene_rows("bag.list")

    def _location_rows(self) -> list[list[dict[str, Any]]]:
        """钓点视图的按钮（button_defs 的 location.list 行）。"""
        return self._scene_rows("location.list")

    def _cast_rows(self) -> list[list[dict[str, Any]]]:
        """抛竿结果下面的常用按钮（button_defs 的 cast 共用行）。"""
        return self._scene_rows("cast")

    def _pull_rows(self) -> list[list[dict[str, Any]]]:
        """咬钩提示下面的按钮（button_defs 的 pull.hook 行）。"""
        return self._scene_rows("pull.hook")

    def _event_rows(self, event_def: dict[str, Any]) -> list[list[dict[str, Any]]]:
        """随机插曲的按钮：button_defs 的 story 行是模板，每个选项生成一行。"""
        template = self._scene_buttons("story")
        rows: list[list[dict[str, Any]]] = []
        for index, choice in enumerate(event_def.get("choices") or [], 1):
            label = str(choice.get("label") or "")
            rows.append([
                self._btn(
                    _fill_button_text(text, label, index),
                    _fill_button_text(data, label, index),
                    style,
                )
                for text, data, style in template
            ])
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

    def _buff_status_line(self, player: dict[str, Any]) -> str:
        """钓手手气的当前状态（一行；没有就返回空串）。

        * 锦鲤玉佩（持续 N 竿）：玩家最想知道**还剩几竿** —— v1.18.0 起明确写出来
        * 一次性手气（插曲/彩蛋）：写着「一次性」，下一竿用完就消失
        * 两者**不叠加**：同时有时取较高的那个（v1.18.5 修的，写清楚免得玩家算不明白）
        """
        left = _safe_int(player.get("buff_casts_left"), 0, 0)
        once = _safe_number(player.get("luck_charges"), 0.0)
        buff = _safe_number(player.get("buff_quality"), 0.0) if left > 0 else 0.0
        if left > 0 and buff > 0:
            line = f"🎐 锦鲤玉佩：手气 +{buff:.0%}　还剩 {left} 竿"
            if once > buff:
                line += f"　🔮 另有一次性 +{once:.0%}（下一竿取较高的）"
            elif once > 0:
                line += f"　🔮 一次性 +{once:.0%} 更低，不叠加"
            return line
        if left > 0:
            # 只有竿数没有数值（异常存档）：别显示成「+0%」，直接不提手气
            return f"🎐 锦鲤玉佩：还剩 {left} 竿"
        if once > 0:
            return f"🔮 下一竿手气 +{once:.0%}（一次性）"
        return ""

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
        # 手气 buff 生效时把剩余竿数带上（buff 结束就不显示，不占版面）
        if _safe_int(player.get("buff_casts_left"), 0, 0) > 0:
            lines.append(f"　{self._buff_status_line(player)}")
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

    def _order_head_text(self, player: dict[str, Any]) -> str:
        """订单列表的表头：这批单是哪个钓点的、还有多久刷新（v1.14.0）。"""
        wait = self._order_wait_text(player)
        loc_id = str(player.get("order_location") or "")
        loc = (self.location_by_id.get(loc_id) if loc_id else None) or {}
        if not self._order_follow_location() or not loc.get("name"):
            # 开关关了 / 老存档还没记钓点 -> 老文案逐字不变
            return f"📋 当前订单（{wait}，过期会换一批）"
        return f"📋 当前订单（📍{loc['name']}　{wait}，过期或换钓点会换一批）"

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
        slots_cfg = int(self.cfg["decoration_slots"])
        now_ts = int(time.time())
        expired = _prune_decorations(player, now=now_ts)
        decos = list(player.get("decorations") or [])
        deco_bonus = _decoration_bonus(player, now=now_ts)
        if not aquarium:
            lines.append("　（空缸）　/钓鱼 水族馆 放 1 放鱼进来")
            if decos:
                lines.append(
                    f"🪸 装饰 {len(decos)}/{slots_cfg} 个正在计时 —— 缸里没鱼就没有产出"
                )
            if expired:
                lines.append(f"　（清理了 {expired} 个已失效的装饰）")
            return "\n".join(lines)

        total = 0
        best = None
        # 收益按「每条鱼各自在缸里的时间」算（v1.18.0）：标一下还没开始产出的鱼
        pending = 0
        # 洗髓丹：今天吃了几颗 / 是不是已经厌恶（只标有记录的，没洗过的不占版面）
        reroll_cap = _reroll_daily_cap(self.cfg)
        today = self._today_text()
        for idx, instance in enumerate(aquarium, start=1):
            value = _instance_value(instance)
            total += value
            if best is None or value > _instance_value(best):
                best = instance
            feed = _safe_int(instance.get("feed_uses"), 0, 0)
            extra = f" 喂{feed}" if feed else ""
            rolled = _reroll_used(instance, today)
            if rolled:
                if reroll_cap > 0 and rolled >= reroll_cap:
                    extra += f"　🔮{rolled}/{reroll_cap} 🤢厌恶"
                else:
                    extra += f"　🔮{rolled}" + (f"/{reroll_cap}" if reroll_cap > 0 else "")
            shown = _tank_display_seconds(instance, now_ts)
            if shown < 60:
                pending += 1
                extra += "　🖼刚入缸"
            else:
                extra += f"　🖼{shown / 3600.0:.1f}h"
            lines.append(
                f"{idx:>2}.{_instance_line(instance)}　{_attrs_line(instance)}" + extra
            )
        lines.append(f"🧮 馆藏估值 {_fmt_gold(total)}")
        if pending:
            lines.append(
                f"　🖼 {pending} 条刚入缸 —— 收益按每条鱼在缸里的时间算，养着才有产出"
            )
        if best is not None:
            lines.append(f"👑 镇馆之宝：{_instance_line(best)}")
        # 装饰（耐久型）：显示剩余小时，顺带清掉过期的
        if decos:
            parts = []
            for entry in decos:
                left = _decoration_hours_left(entry, now=now_ts)
                parts.append(
                    f"{self._item_label(str(entry.get('id')))}剩{left:.0f}小时"
                )
            lines.append(
                f"🪸 装饰 {len(decos)}/{slots_cfg}　" + "　".join(parts)
            )
            lines.append(f"　挂机产出 +{deco_bonus:.0%}（离线时间也照算）")
        else:
            lines.append(
                f"🪸 装饰位 0/{slots_cfg}　/钓鱼 商店 买 珊瑚造景"
            )
        if expired:
            lines.append(f"　（清理了 {expired} 个已失效的装饰）")
        # 收益提示（与「/钓鱼 领」的结算口径完全一致：同一份 _pond_income）
        est = int(_pond_income(player, self.cfg, now_ts)["income"])
        if est > 0:
            lines.append(f"💰 现在可领 {_fmt_gold(est)}　/钓鱼 领")
        elif aquarium:
            lines.append("　💤 刚入缸的鱼还没开始产出（按每条鱼在缸里的时间算）")
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
        # 体力取代了老的冷却：每钓一次 1 点，随时间恢复，能攒着
        if _stamina_enabled(cfg):
            stamina_line = (
                f"　体力上限 {int(cfg['stamina_max'])} 点"
                f"（每 {int(cfg['stamina_regen_seconds'])} 秒回 1 点）"
            )
        else:
            stamina_line = "　本服不限体力（想钓就钓）"
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
            f"　下竿免费（空钩不花钱）　签到 {cfg['sign_reward']}"
            if int(cfg["fish_cost"]) <= 0
            else f"　钓费 {cfg['fish_cost']}/竿　签到 {cfg['sign_reward']}"
        )
        pages: list[tuple[str, list[str]]] = [
            (
                "开始钓",
                [
                    "　/钓鱼　　　　　　　下竿（发「钓」也行）",
                    "　/钓鱼 10　　　　　 连钓 10 次（扣 10 点体力 + 10 个饵）",
                    "　/钓鱼 拉　　　　　 咬钩后拉线（收线 / 提竿 也行）",
                    "　/钓鱼 体力　　　　 看体力",
                    stamina_line,
                    fee_line,
                ],
            ),
            (
                "背包与买卖",
                [
                    "　/钓鱼 背包　　　　 看背包（带序号和价格）",
                    "　/钓鱼 卖 1 2　　　 卖掉第 1、2 条（也能 卖 鲤鱼 / 卖 全部）",
                    "　/钓鱼 卖光光　　　 一次清空（锁定的会留着）",
                    "　/钓鱼 锁定 1　　　 锁定不想卖的鱼（/钓鱼 解锁 1 取消）",
                    "　/钓鱼 商店　　　　 看货架",
                    "　/钓鱼 买 蚯蚓 20　 买鱼饵 / 道具 / 鱼竿（写名字就行）",
                    "　/钓鱼 装备 星辉竿　换鱼竿（/钓鱼 鱼竿 看全部）",
                    "　/钓鱼 换饵 蚯蚓　　换当前鱼饵（/钓鱼 换饵 空钩 = 不挂饵）",
                    "　/钓鱼 扩建背包　　 背包扩容",
                    f"　背包上限 {base_cap} 条起，不能无限囤货",
                ],
            ),
            (
                "赚钱养鱼",
                [
                    "　/钓鱼 签到　　　　 每天领一笔金币",
                    "　/钓鱼 今日　　　　 今日天气 + 鱼市行情",
                    "　/钓鱼 订单　　　　 订单（按当前钓点刷新，比卖店赚一倍）",
                    "　/钓鱼 交 1 2　　　 交单（交过的不再收）",
                    "　— 鱼缸（挂机收益）—",
                    "　/钓鱼 水族馆　　　 看鱼缸",
                    "　/钓鱼 放 1 3　　　 把背包第 1、3 条放进缸",
                    "　/钓鱼 取 1　　　　 取回来",
                    "　/钓鱼 领　　　　　 领挂机收益（鱼在缸里待得越久越多）",
                    "　/钓鱼 喂 高级饲料 1　投喂：涨三维、直接涨价",
                    "　/钓鱼 洗 2　　　　 洗髓丹：重掷第 2 条的个体品质（极小概率洗出神话）",
                    "　/钓鱼 用 珊瑚造景　 摆装饰：72 小时内挂机产出 +20%",
                    "　/钓鱼 水族馆 扩建　 花金币扩容鱼缸",
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
                    "　/钓鱼 钓点　　　　　 全部钓点 + 解锁条件",
                    "　/钓鱼 去 <名称>　　 前往（写简称也行）",
                    "　/钓鱼 解锁 <名称>　 解锁并前往",
                ]
                if index == len(chunks)
                else []
            )
            pages.append((title, chunk + extra))

        pages.extend(
            [
                ("鱼竿", rod_lines + [
                    "　🔒 的竿要等级达标才能买",
                    "　/钓鱼 买 <竿名>　　 买鱼竿",
                    "　/钓鱼 装备 <竿名>　 换上",
                ]),
                (
                    "鱼饵",
                    bait_lines
                    + [
                        "　部分鱼饵要等级 + 对应鱼竿才能买",
                        "　/钓鱼 买 <名字> [个数]　/钓鱼 换饵 <名字>",
                    ],
                ),
                (
                    "道具",
                    item_lines
                    + [
                        "　/钓鱼 喂 <饲料> 1　投喂（三维永久上涨）",
                        "　/钓鱼 洗 1　　　　 洗髓丹：重掷个体品质，极小概率出「神话」",
                        "　/钓鱼 用 珊瑚造景　摆装饰：挂机产出 +20%",
                        f"　图鉴 {len(FISH_POOL)} 种　成就 {len(ACHIEVEMENTS)} 个",
                    ],
                ),
                (
                    "钓点与收集",
                    [
                        "　/钓鱼 图鉴　　　　　 各钓点的收集进度",
                        "　/钓鱼 图鉴 详　　　 完整鱼名单（可翻页）",
                        "　/钓鱼 查 <鱼名>　　 这鱼在哪些钓点、要不要拉线",
                        "　/钓鱼 查 <钓点名>　 这个钓点有哪些鱼",
                        "　/钓鱼 杂物　　　　　 杂物与纸条收集",
                        "　/钓鱼 排行　　　　　 群内排行榜（金币 / 图鉴 / 最贵）",
                        "　/钓鱼 档案　　　　　 等级、金币、统计",
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

    def _stamina_text(self, player: dict[str, Any]) -> str:
        """体力页：/钓鱼 体力（只陈述状态，不显示任何概率）"""
        cfg = self.cfg
        if not _stamina_enabled(cfg):
            return "⚡ 本服未启用体力限制：想钓就钓，不用等"
        cap = int(cfg["stamina_max"])
        current = _refresh_stamina(player, cfg)
        lines = [f"⚡ 体力 {current}/{cap}"]
        if current >= cap:
            lines.append("　已满　攒着不亏，随时可以 /钓鱼 10 连钓")
        else:
            wait = _stamina_wait_seconds(player, cfg)
            lines.append(f"　下一点恢复：还需 {wait} 秒")
        if current >= 2:
            lines.append(f"　满体力能连钓 {current} 次（/钓鱼 {current}）")
        return "\n".join(lines)

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
