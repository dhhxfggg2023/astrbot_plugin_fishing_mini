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

#: 帮助页**正文**最多几行。渲染时页头、空行、空行、页脚再占 4 行，
#: 所以整条回复最多 20 行 —— 站长要求「每一条回复的文本量都控制好」，
#: 道具这种十几行的表会自动拆成「道具 1/2」「道具 2/2」而不是一口气刷屏。
HELP_PAGE_MAX_LINES = 16


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
        label = f"{rod.get('emoji', '')}{rod.get('name', '鱼竿')}"
        left = self._limited_rod_left(player, str(rod.get("id") or ""))
        # 限定竿（大鱼乐抽到的）标出剩余次数 —— 否则玩家不知道这根竿哪来的、
        # 也不知道还剩几次（v1.18.63）
        return f"{label}（限用·剩 {left} 次）" if left is not None else label

    def _limited_rod_left(self, player: dict[str, Any], rod_id: str) -> int | None:
        """这根竿如果是「限用竿」，返回剩余次数；不是（或没有）返回 None。"""
        if not rod_id:
            return None
        pocket = player.get("items")
        if not isinstance(pocket, dict):
            return None
        state = player.get("limited_uses")
        state = state if isinstance(state, dict) else {}
        for item_id, spec in (getattr(self, "items", None) or {}).items():
            if str((spec or {}).get("rod") or "") != rod_id:
                continue
            if _safe_int(pocket.get(item_id), 0, 0) <= 0:
                continue
            total = _safe_int((spec or {}).get("uses"), 0, 0)
            left = state.get(str(item_id))
            return total if left is None else max(0, _safe_int(left, total, 0))
        return None

    def _title_label(self, player: dict[str, Any]) -> str:
        """玩家当前戴的称号（v1.18.17，没戴返回空串）。

        称号纯炫耀、不加任何属性，所以展示处（档案 / 排行榜 / 称号列表）直接用这个。
        """
        tid = str(player.get("title") or "")
        title = (getattr(self, "title_by_id", None) or {}).get(tid)
        if not title:
            return ""
        return f"{title.get('emoji', '')}{title.get('name', '')}"

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
        """该场景按钮的来源：``config`` / ``inherit`` / ``default`` / ``group`` / ``none`` / ``off``。

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
        base = SCENE_BUTTON_BASE.get(scene) or ""
        if base and (BUTTONS.get(base) or _BUILTIN_BUTTONS.get(base)):
            return "group"
        return "none"

    def _scene_items(self, scene: str) -> list[tuple[str, str, int]]:
        """取某场景生效的按钮（元组形态）。

        优先级（v1.18.17 定稿）：自己配的 > 显式父场景 > **自己的出厂按钮** >
        同组基础场景（cast.miss_none → cast 那一组）> 空。

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
            # 自己的出厂按钮优先于「同组兜底」：bag.empty 有自己的按钮就别用 bag 的
            items = _BUILTIN_BUTTONS.get(scene) or []
        if not items:
            base = SCENE_BUTTON_BASE.get(scene) or ""
            if base:
                items = BUTTONS.get(base) or _BUILTIN_BUTTONS.get(base) or []
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

    # -------------------------------------------------------------------------
    # 动态按钮行：翻页（v1.18.33）/ 再次使用（v1.18.34）
    # -------------------------------------------------------------------------
    # 这两排的**按钮内容**在按钮表里（page.prev / page.next / item.again 三个场景，
    # 站长能在「💬 回复」页里改文案/指令/样式）；**什么时候用、占位符填什么**在这里决定。
    # 占位符见 _calc.DYNAMIC_BUTTON_SAMPLES。

    def _dynamic_button_items(
        self, scene: str, values: dict[str, str] | None
    ) -> list[tuple[str, str, int]]:
        """取一个**动态按钮场景**的按钮，并把占位符填成真值。

        动态按钮（``page.prev`` / ``page.next`` / ``item.again``）和普通场景一样，
        文案/指令/样式都来自 ``button_defs``（站点可在「💬 回复」页里改），
        区别只在于**什么时候用**由代码决定、占位符由代码填。
        """
        return [
            (
                _fill_button_values(label, values).strip(),
                # 填充后要 strip：`/钓鱼 用 {道具} {参数}` 在参数为空时会留下一个尾空格
                _fill_button_values(data, values).strip(),
                style,
            )
            for label, data, style in self._scene_items(scene)
        ]

    def _dynamic_rows(
        self, scene: str, values: dict[str, str] | None
    ) -> list[list[dict[str, Any]]]:
        """把某个动态按钮场景的按钮排成键盘行（场景「不要按钮」时为空）。"""
        if scene in BUTTON_EMPTY_SCENES:
            return []
        items = self._dynamic_button_items(scene, values)
        if not items:
            return []
        return self._layout_rows(scene, items)

    def _page_rows(self, scene: str | None, page: Any) -> list[list[dict[str, Any]]]:
        """需要翻页的界面：补一排「上一页 / 下一页」按钮。

        ``page`` = ``(当前页, 总页数, 指令前缀)``，例如 ``(2, 3, "/钓鱼 背包")``。
        按钮本身来自按钮表里的 ``page.prev`` / ``page.next`` 两个场景
        （默认 ``{指令} {页}`` -> ``/钓鱼 背包 1``），站长可以在「💬 回复」页里
        改文案、指令和样式；**什么时候出现**由这里决定：

        * 不给 ``page``、只有一页、或者指令前缀是空的 → 这一排不出现；
        * 第一页不给「上一页」、最后一页不给「下一页」（**不做首尾循环**，
          这样「这个按钮还在 = 还能往那边翻」不会骗人）；
        * 回复自己的场景在 ``button_empty_scenes`` 里（站长明确要「这里就是没按钮」）
          时整排不加；``page.prev`` / ``page.next`` 各自在名单里则只去掉对应那个。
        """
        if not page:
            return []
        if scene and scene in BUTTON_EMPTY_SCENES:
            return []
        try:
            current, total = int(page[0]), int(page[1])
            command = str(page[2]).strip()
        except (IndexError, TypeError, ValueError):
            return []
        if total <= 1 or not command:
            return []
        values = {"指令": command, "当前页": str(current), "总页": str(total)}
        items: list[tuple[str, str, int]] = []
        if current > 1 and "page.prev" not in BUTTON_EMPTY_SCENES:
            items += self._dynamic_button_items(
                "page.prev", {**values, "页": str(current - 1)}
            )
        if current < total and "page.next" not in BUTTON_EMPTY_SCENES:
            items += self._dynamic_button_items(
                "page.next", {**values, "页": str(current + 1)}
            )
        if not items:
            return []
        # 两个场景的按钮排在**同一排**里（默认就是一排两个）；行宽看 button_layout
        return self._layout_rows("page.next", items)

    def _use_again_values(
        self, player: dict[str, Any], item_id: str, spec: Any = ""
    ) -> dict[str, str] | None:
        """「再次使用」按钮的填充值；**没存货就不给**（``None`` = 不出这个按钮）。

        指令模板（``item.again``，默认 ``/钓鱼 用 {道具} {参数}``）照着玩家刚才那条拼
        —— 所以「用 洗髓丹 2」按下去还是洗栏位 2，「用 姜汤」按下去就是再喝一碗。
        道具名用**登记名**而不是玩家打的字，写 id / 简称也能对得上。
        """
        if _safe_int((player.get("items") or {}).get(item_id), 0, 0) <= 0:
            return None
        name = str((self.items.get(item_id) or {}).get("name") or "").strip()
        if not name:
            return None
        return {"道具": name, "参数": str(spec or "").strip()}

    def _again_rows(
        self, scene: str | None, values: Any
    ) -> list[list[dict[str, Any]]]:
        """道具用完之后补一排「再次使用」按钮（v1.18.34，v1.18.35 起可配）。

        ``values`` 为 ``None``（没存货 / 拼不出道具名）时整排不出现；
        回复自己的场景在 ``button_empty_scenes`` 里（站长明确要「这里就是没按钮」）
        时也不加，``item.again`` 自己在名单里同样去掉。
        """
        if not isinstance(values, dict) or not values:
            return []
        if scene and scene in BUTTON_EMPTY_SCENES:
            return []
        return self._dynamic_rows("item.again", values)

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

    def _event_rows(
        self, event_def: dict[str, Any], order: list[int] | None = None
    ) -> list[list[dict[str, Any]]]:
        """随机插曲的按钮：button_defs 的 story 行是模板，每个选项生成一行。

        ``order`` 是显示顺序（见 ``_interactions._event_order``）：
        按钮上的 ``{n}`` 必须用**显示序号**，才能和提示里的编号一致。
        """
        template = self._scene_buttons("story")
        indices = self._event_order_indices(event_def, order)
        rows: list[list[dict[str, Any]]] = []
        for shown, real_index in enumerate(indices, 1):
            choice = (event_def.get("choices") or [])[real_index]
            label = str(choice.get("label") or "")
            rows.append([
                self._btn(
                    _fill_button_text(text, label, shown),
                    _fill_button_text(data, label, shown),
                    style,
                )
                for text, data, style in template
            ])
        return rows

    @staticmethod
    def _event_order_indices(
        event_def: dict[str, Any], order: list[int] | None
    ) -> list[int]:
        """把存的显示顺序「洗」成一份可用的下标列表（坏数据一律退回自然顺序）。"""
        count = len(event_def.get("choices") or [])
        if not isinstance(order, list) or sorted(
            _safe_int(x, -1, 0) for x in order
        ) != list(range(count)):
            return list(range(count))
        return [_safe_int(x, 0, 0) for x in order]

    def _event_recap(self, event_def: dict[str, Any], player: dict[str, Any]) -> str:
        """连载的「前情提要」一行（不是连载就返回空串）。"""
        chain_id = str(event_def.get("chain") or "")
        if not chain_id:
            return ""
        chain = CHAIN_BY_ID.get(chain_id)
        name = str(event_def.get("chain_name") or (chain or {}).get("name") or chain_id)
        episode = _safe_int(event_def.get("episode"), 1, 1)
        total = len((chain or {}).get("episodes") or []) or episode
        head = f"📖 连载《{name}》第 {episode}/{total} 话"
        story = player.get("story") if isinstance(player.get("story"), dict) else {}
        last = str((story or {}).get("last") or "")
        return f"{head}　上次：{last}" if last else head

    # -------------------------------------------------------------------------
    # 随机小插曲
    # -------------------------------------------------------------------------

    def _event_text(
        self, event_def: dict[str, Any], player: dict[str, Any] | None = None
    ) -> str:
        """这一段的开场白。

        连载的后续话会用 ``text_when`` **按旗标挑版本**（前面选过什么，这里就怎么演）：
        取第一个「旗标为真」的版本，都不满足就用 ``default``；两样都没有才退回 ``text``。
        """
        when = event_def.get("text_when")
        if isinstance(when, dict) and when:
            story = (player or {}).get("story")
            flags = story.get("flags") if isinstance(story, dict) else {}
            flags = flags if isinstance(flags, dict) else {}
            for key, variant in when.items():
                if str(key) == "default":
                    continue
                if flags.get(str(key)):
                    return str(variant)
            if when.get("default"):
                return str(when["default"])
        return str(event_def.get("text") or "")

    def _event_prompt(
        self,
        event_def: dict[str, Any],
        order: list[int] | None = None,
        rows: bool = True,
        text: str | None = None,
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """插曲的文案 + 按钮。

        选项顺序按 ``order`` 打乱后显示，编号、文案、按钮三处一致
        （连载的「前情提要」是单独一条 `story.recap`，见 `_event_recap`）。
        ``text`` 由调用方用 ``_event_text()`` 解析好传进来（连载要按旗标换开场白）。
        """
        lines = [f"❔ {text if text is not None else self._event_text(event_def)}"]
        indices = self._event_order_indices(event_def, order)
        for shown, real_index in enumerate(indices, 1):
            choice = (event_def.get("choices") or [])[real_index]
            lines.append(f"　{shown}. {choice['label']}　/钓鱼 事件 {shown}")
        return "\n".join(lines), (self._event_rows(event_def, order) if rows else [])

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

    def _junk_label(self, junk_id: str) -> str:
        """一件**杂物**的「emoji + 中文名」；不是杂物就原样返回 id（方便调用方判断）。

        v1.18.75 加的：杂物以前没有「给人看的名字」这条路，于是它们的 id
        直接漏进了玩家看到的列表（站长：「bot 回复的都不能有函数名」）。
        """
        for junk in COLLECTIBLES:
            if str(junk.get("id")) == str(junk_id):
                return f"{junk.get('emoji', '')}{junk.get('name', junk_id)}"
        return str(junk_id)

    def _item_label(self, item_id: str) -> str:
        """一件东西的「中文名 + emoji」。

        ⚠️ v1.18.75：**绝不允许把内部 id 漏给玩家**（站长：「bot 回复的都不能有函数名」）。
        以前找不到就 `return item_id`，于是杂物 id（`old_boot` / `tin_can`…）会直接
        出现在「/钓鱼 用」的列表里。现在按三层兜底：
        ① 道具表；② **杂物表**（走 `_junk_label`，它有中文名和 emoji）；
        ③ 都没有才退回 id —— 但那种 id 只可能是配置写错的，同时打一条 debug 日志，
        免得又变成静默漏字。
        """
        item = self.items.get(item_id)
        if item:
            return f"{item.get('emoji', '')}{item.get('name', item_id)}"
        junk = self._junk_label(item_id)
        if junk != item_id:
            return junk
        logger.debug(f"展示时找不到这个东西的名字（配置里少了 id？）：{item_id}")
        return item_id

    def _buff_status_line(self, player: dict[str, Any]) -> str:
        """钓手手气 / 品质保底的当前状态（一行；没有就返回空串）。

        * 锦鲤玉佩（持续 N 竿）：玩家最想知道**还剩几竿** —— v1.18.0 起明确写出来
        * 一次性手气（插曲/彩蛋）：写着「一次性」，下一竿用完就消失
        * 两者**叠加**（v1.18.11 站长要求：来源不同就该加在一起），
          所以两者同时在时直接把**这一竿的合计**写出来，免得玩家自己猜。
        * 品质保底（v1.18.23，`quality_floor`）：与手气**并列**写在后面 ——
          它管的是「不出垃圾」，手气管的是「更容易出好的」，两件事不能混成一句。
        """
        left = _safe_int(player.get("buff_casts_left"), 0, 0)
        once = _safe_number(player.get("luck_charges"), 0.0)
        buff = _safe_number(player.get("buff_quality"), 0.0) if left > 0 else 0.0
        # 品质保底：同一个「钓手 buff」家族，但字段独立（各扣各的竿数）
        floor_casts = _safe_int(player.get("buff_floor_casts"), 0, 0)
        floor = _safe_number(player.get("buff_floor"), 0.0) if floor_casts > 0 else 0.0
        floor_text = ""
        if floor > 0:
            floor_name, floor_emoji = _quality_label(floor)
            floor_text = (
                f"{floor_emoji} 品质保底「{floor_name}」：还剩 {floor_casts} 竿"
            )
        line = ""
        if left > 0 and buff > 0:
            line = f"🎐 锦鲤玉佩：手气 +{buff:.0%}　还剩 {left} 竿"
            if once > 0:
                line += (
                    f"　🔮 一次性 +{once:.0%}（叠加：下一竿共 +{once + buff:.0%}，"
                    f"之后回到 +{buff:.0%}）"
                )
        elif left > 0:
            # 只有竿数没有数值（异常存档）：别显示成「+0%」，直接不提手气
            line = f"🎐 锦鲤玉佩：还剩 {left} 竿"
        elif once > 0:
            line = f"🔮 下一竿手气 +{once:.0%}（一次性，用完即清）"
        if floor_text:
            line = f"{line}　{floor_text}" if line else floor_text
        return line

    def _level_line(self, player: dict[str, Any]) -> str:
        """等级一行：``🎚 等级 7（+12/88）``。

        ⚠️ 等级以前**只**在 ``/钓鱼 钓点`` 里出现过，档案里反而没有 ——
        站长要求「等级显示统一放到档案」，所以这一行是唯一口径：
        按真实曲线（``_level_progress``）写「当前级 + 本级进度/升级所需」，
        满级时只写等级（没有下一级可升）。
        """
        level, into, need = _level_progress(player)
        if need <= 0:
            return f"🎚 等级 {level}（已满级）"
        return f"🎚 等级 {level}　升级进度 {into}/{need}"

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

        展示顺序和「取 / 卖 / 投喂 / 洗髓 的栏位号」必须一致，否则玩家会对不上号
        （v1.18.37 修：读档与每个按序号取鱼的入口都会走一遍，见 _calc._sort_tank）。
        """
        _sort_tank(aquarium)

    def _aquarium_view(self, player: dict[str, Any]) -> str:
        aquarium: list[dict[str, Any]] = player.get("aquarium") or []
        self._sort_aquarium(aquarium)
        capacity = self._aquarium_capacity(player)
        lines = [f"🐠 水族馆 {len(aquarium)}/{capacity}"]
        # 缸满了才提扩建（平时不占版面）：写清下一档叫什么、加几个位、多少钱
        unlocked = player.get("aquarium_slots") or []
        if not isinstance(unlocked, list):
            unlocked = []
        nxt_slot = next(
            (s for s in self.aquarium_slots if s["name"] not in unlocked), None
        )
        if nxt_slot is not None and len(aquarium) >= capacity:
            lines.append(
                f"🏠 缸满了　可扩建「{nxt_slot['name']}」"
                f"（+{max(1, _safe_int(nxt_slot.get('add'), 1, 1))} 个位，"
                f"{_fmt_gold(nxt_slot['price'])}）　/钓鱼 水族馆 扩建"
            )
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
                f"🪸 装饰位 0/{slots_cfg}　/钓鱼 道具 买 珊瑚造景"
            )
        if expired:
            lines.append(f"　（清理了 {expired} 个已失效的装饰）")
        # 香火供奉（v1.18.17 的后期金币回收口）：生效中就把剩余时间写出来
        offering_until = _safe_int(player.get("offering_ts"), 0, 0)
        if offering_until > now_ts:
            left_h = (offering_until - now_ts) / 3600.0
            lines.append(
                f"🕯️ 香火供奉中　挂机 +{_offering_bonus(player, self.cfg, now_ts):.0%}"
                f"　手气 +{_offering_luck(player, self.cfg, now_ts):.2f}"
                f"　剩 {left_h:.1f} 小时"
            )
        # 收益提示（与「/钓鱼 领」的结算口径完全一致：同一份 _pond_income）
        est = int(_pond_income(player, self.cfg, now_ts)["income"])
        if est > 0:
            lines.append(f"💰 现在可领 {_fmt_gold(est)}　/钓鱼 领")
        elif aquarium:
            lines.append("　💤 刚入缸的鱼还没开始产出（按每条鱼在缸里的时间算）")
        return "\n".join(lines)

    def _bait_shop_view(self, player: dict[str, Any]) -> str:
        """鱼饵店货架（v1.18.13 起商店拆成三家）：**只上架已解锁的**。"""
        baits = player.get("baits") or {}
        lines = [
            f"🪱 鱼饵店　💰 {_fmt_gold(player.get('gold', 0))}",
            f"🎣 当前鱼饵：{self._bait_label(player.get('equipped_bait', 'none'))}",
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
        if hidden:
            # 只说「还有」，不剧透清单、也不写等级数字
            lines.append("🔒 还有更多鱼饵，等级更高 / 换上更好的竿之后会陆续上架")
        # ⚠️ 这一行不能举具体饵名：`/钓鱼 鱼饵 买 蚯蚓` 会把未解锁的饵名写进低等级玩家的
        # 列表里，等于剧透（[6e] 那条「未解锁的完全不显示」断言就是被它踩红的）
        lines.append("💡 /钓鱼 鱼饵 买 <名字> [数量]　（也可以直接 /钓鱼 买 <名字>）")
        return "\n".join(lines)

    def _item_shop_view(self, player: dict[str, Any]) -> str:
        """道具店货架（v1.18.13）：饲料 / 仙露 / 育灵水 / 洗髓丹 / 玉佩 / 造景。"""
        items = player.get("items") or {}
        lines = [f"🎁 道具店　💰 {_fmt_gold(player.get('gold', 0))}"]
        locked: list[str] = []
        for item_id in self._item_list():
            item = self.items[item_id]
            if self._unlock_shortage(player, item):
                # 点名（而不是只说「还有更多道具」）：否则一件带等级门槛的道具在货架上
                # 完全看不到 —— 分不清是「没加载出来」还是「被锁着」，加新道具时最容易懵。
                locked.append(str(item.get("name") or item_id))
                continue
            owned = _safe_int(items.get(item_id), 0, 0)
            lines.append(
                f"　{self._item_label(item_id)} {item['price']}金　持有{owned}"
                f"　{item['desc']}"
            )
        if locked:
            show = "、".join(locked[:6]) + ("…" if len(locked) > 6 else "")
            lines.append(f"🔒 未上架 {len(locked)} 件：{show}（等级 / 鱼竿满足后上架）")
        lines.append("💡 /钓鱼 道具 买 <名字> [数量]　（也可以直接 /钓鱼 买 <名字>）")
        return "\n".join(lines)

    def _rod_pull_text(self, rod: dict[str, Any]) -> str:
        """鱼竿的「拉线手感」一行尾巴（没有加成时返回空串）。

        高阶竿（龙纹鲤竿 / 归墟竿）卖的是手感而不是纯数值，得让玩家看得见：
        ``　拉线窗口+15%`` / ``　不易脱钩``。
        """
        parts: list[str] = []
        window_bonus = _safe_number(rod.get("window_bonus"), 0.0)
        escape_factor = _safe_number(rod.get("escape_factor"), 1.0)
        if window_bonus:
            parts.append(f"拉线窗口+{window_bonus:.0%}")
        if escape_factor < 1.0:
            parts.append(f"逃脱率-{1.0 - escape_factor:.0%}")
        elif escape_factor > 1.0:
            parts.append(f"逃脱率+{escape_factor - 1.0:.0%}")
        return ("　" + "　".join(parts)) if parts else ""

    def _lookup_anything(
        self, player: dict[str, Any], keyword: str, exact: bool = False
    ) -> list[tuple[str, list[str]]]:
        """在「玩家看得见的东西」里按名字查一遍 `/钓鱼 查`。

        返回 ``[(分类标签, 详情行列表), …]``（可能命中多个分类，由调用方决定怎么展示）。

        查得到的是：鱼饵、鱼竿、道具、杂物、变异、天气、成就。
        鱼与钓点在主流程里先查过了（命中就直接出卡片），所以这里不重复。

        ``exact=True`` 时只认**完全同名**（用于「精确优先」：查「锦鲤玉佩」
        不该被鱼「锦鲤」的模糊匹配抢走）。

        ⚠️ **只列游戏内的东西**：插件配置键、回复场景、内部函数、数据结构这些
        「只有开发者才关心」的内容一律不进游戏（站长明确要求：游戏文本服务于游戏）。
        """
        key = str(keyword or "").strip()
        if not key:
            return []
        lower = key.lower()
        hits: list[tuple[str, list[str]]] = []

        def matches(name: Any, extra: str = "") -> bool:
            n = str(name or "").strip()
            if not n:
                return False
            if exact:
                return n == key or n.lower() == lower
            return key in n or n.lower() == lower or (extra and key in extra)

        # ---- 鱼饵（价格 / 手气 / 稀有度倾向 / 解锁 / 我的存量）----
        for bait_id in self._bait_list():
            bait = self.baits[bait_id]
            if not matches(bait.get("name"), bait_id):
                continue
            owned = _safe_int((player.get("baits") or {}).get(bait_id), 0, 0)
            equipped = player.get("equipped_bait") == bait_id
            mults = bait.get("rarity_mult") or {}
            best = max(mults, key=lambda r: _safe_number(mults.get(r), 1.0), default="")
            worst = min(mults, key=lambda r: _safe_number(mults.get(r), 1.0), default="")
            lines = [
                f"{bait.get('emoji', '')}{bait.get('name')}"
                f"　{_fmt_gold(bait.get('price', 0))} 金/个　持有 {owned}"
                + ("　（正在用）" if equipped else ""),
                f"　手气{_luck_stars(bait.get('luck'), 0.7)}"
                f"　上钩率{_luck_stars(self.bait_hook_map.get(bait_id), 1.0)}",
            ]
            if best and worst and best != worst:
                lines.append(f"　更容易钓到：{best}　更少见到：{worst}")
            unlock = self._rod_need_text(bait)
            if unlock:
                lines.append(f"　需要：{unlock}")
            if bait.get("desc"):
                lines.append(f"　{bait['desc']}")
            hits.append((f"🪱 鱼饵「{bait.get('name')}」", lines))

        # ---- 鱼竿（限定竿不卖，不进这家店；它有剩余次数时会顶替装备的竿）----
        _active_rod = self._rod(player)
        _active_limited = self._limited_rod_left(player, str(_active_rod.get("id") or ""))
        if _active_limited is not None:
            lines = [
                f"　{_active_rod.get('emoji', '')}{_active_rod.get('name', '鱼竿')}"
                f"（大鱼乐限定·剩 {_active_limited} 次）",
                "　　现在生效的就是它（用完自动换回你装备的竿）",
            ]
            hits.append((f"🎣 鱼竿「{_active_rod.get('name')}」（限定中）", lines))
        for rod in _shop_visible_rods(self.rods):
            if not matches(rod.get("name"), str(rod.get("id"))):
                continue
            owned_rod = rod["id"] in (player.get("rods") or [DEFAULT_ROD])
            lines = [
                f"{rod['emoji']}{rod['name']}　{_fmt_gold(rod['price'])} 金"
                + ("　（已拥有）" if owned_rod else ""),
                f"　价值+{rod['value_bonus']:.0%}"
                f"　手气{_luck_stars(rod.get('luck_bonus'), 0.2)}"
                + self._rod_pull_text(rod),
            ]
            if int(rod.get("unlock_level", 1)) > 1:
                lines.append(f"　解锁：{int(rod['unlock_level'])} 级")
            if rod.get("desc"):
                lines.append(f"　{rod['desc']}")
            hits.append((f"🎣 鱼竿「{rod['name']}」", lines))

        # ---- 道具（说清它到底干嘛用、怎么用）----
        for item_id in self._item_list():
            item = self.items[item_id]
            if not matches(item.get("name"), item_id):
                continue
            owned = _safe_int((player.get("items") or {}).get(item_id), 0, 0)
            lines = [
                f"{item.get('emoji', '')}{item.get('name')}"
                f"　{_fmt_gold(item.get('price', 0))} 金　持有 {owned}",
                f"　{item.get('desc')}",
            ]
            if int(item.get("unlock_level", 1)) > 1:
                lines.append(f"　解锁：{int(item['unlock_level'])} 级")
            lines.append(f"　用法：{self._item_usage_hint(item_id)}")
            hits.append((f"🎁 道具「{item.get('name')}」", lines))

        # ---- 杂物（卖价 + 收集情况）----
        for junk in COLLECTIBLES:
            if not matches(junk.get("name"), str(junk.get("id"))):
                continue
            got = _safe_int((player.get("collectibles") or {}).get(junk["id"]), 0, 0)
            lines = [
                f"{junk.get('emoji', '')}{junk.get('name')}"
                f"　卖价 {_fmt_gold(junk.get('value', 0))} 金"
                + (f"　已收集 {got} 个" if got else "　还没捞到过"),
                f"　{junk.get('desc', '')}",
                "　没中鱼的那一竿才有机会钩上来",
            ]
            hits.append((f"🧺 杂物「{junk.get('name')}」", lines))

        # ---- 变异 ----
        for variant in VARIANTS:
            if not matches(variant.get("name"), str(variant.get("id"))):
                continue
            got = _safe_int((player.get("variants") or {}).get(variant["id"]), 0, 0)
            lines = [
                f"{variant.get('emoji', '')}{variant.get('name')}"
                f"　价值 ×{_safe_number(variant.get('mult'), 1.0):.1f}"
                + (f"　遇到过 {got} 次" if got else "　还没遇到过"),
                f"　{variant.get('desc', '')}",
                "　纯运气：上钩那一刻小概率变成变异个体",
            ]
            hits.append((f"🧬 变异「{variant.get('name')}」", lines))

        # ---- 天气 ----
        today = self._today_weather_name(player)
        for weather_cfg in WEATHERS:
            if not matches(weather_cfg.get("name"), str(weather_cfg.get("id"))):
                continue
            lines = [
                f"{weather_cfg.get('emoji', '')}{weather_cfg.get('name')}"
                + ("　（今天就是它）" if today == weather_cfg.get("name") else ""),
                f"　{_weather_hint(weather_cfg)}",
            ]
            if weather_cfg.get("desc"):
                lines.append(f"　{weather_cfg['desc']}")
            lines.append("　天气每天随机一种，全天不变（/钓鱼 今日 看今天的）")
            hits.append((f"🌤️ 天气「{weather_cfg.get('name')}」", lines))

        # ---- 成就（按名字/说明模糊查）----
        unlocked = set(player.get("achievements") or [])
        for ach_id, ach_text in ACHIEVEMENTS.items():
            if not matches(ach_text, ach_id):
                continue
            done = ach_id in unlocked
            hits.append((
                f"🏅 成就「{self._ach_short_name(ach_text)}」",
                [
                    f"{'✅ 已达成' if done else '❔ 还没达成'}　{ach_text}",
                    "　成就达成时会在群里提示一次",
                ],
            ))

        return hits

    @staticmethod
    def _ach_short_name(ach_text: str) -> str:
        """成就的短名：``🏆 垂钓达人：累计钓到 50 条`` → ``垂钓达人``。

        成就文案统一是「emoji 名字：说明」的格式，查的时候玩家只关心名字。
        """
        text = str(ach_text or "")
        head = text.split("：", 1)[0]
        return head.split(" ", 1)[-1].strip() if " " in head else head.strip()

    def _item_usage_hint(self, item_id: str) -> str:
        """道具怎么用（一行，给 `/钓鱼 查` 的详情卡用）。"""
        effects = (self.items.get(item_id) or {}).get("effects") or {}
        if _safe_number(effects.get("quality_reroll"), 0.0) > 0:
            return "/钓鱼 洗 <水族馆栏位>"
        if _safe_number(effects.get("buff_quality"), 0.0) > 0:
            return "/钓鱼 用 <名字>（作用在自己身上，不用栏位）"
        if _safe_number(effects.get("quality_floor"), 0.0) > 0:
            return "/钓鱼 用 <名字>（作用在自己身上，接下来几竿品质有保底）"
        if _safe_number(effects.get("decorate"), 0.0) > 0:
            return "/钓鱼 用 <名字>（摆进鱼缸）"
        if _safe_number(effects.get("feed_bonus"), 0.0) > 0:
            return "/钓鱼 用 <名字> <水族馆栏位>"
        if _safe_number(effects.get("heal"), 0.0) > 0:
            return "/钓鱼 用 <名字>（回体力，不用栏位）"
        return "/钓鱼 喂 <名字> [水族馆栏位]（不写栏位就是喂全缸）"

    def _today_weather_name(self, player: dict[str, Any]) -> str:
        """今天这个玩家的天气名（没开天气/没生成就返回空串）。"""
        cfg = WEATHER_BY_ID.get(str(player.get("weather") or ""))
        return str(cfg.get("name") or "") if cfg else ""

    # -------------------------------------------------------------------------
    # 列表的堆叠显示（连钓战报与背包共用同一套规则）
    # -------------------------------------------------------------------------
    def _stack_show_separate(self, fish: dict[str, Any], variant: Any) -> bool:
        """这一条要不要单独占一行。

        规则（站长定的，连钓战报与背包**共用**）：
          * **传说 / 神话**（要拉线的高档鱼），或
          * **任意稀有度的异色个体**（带 variant）
        逐条列出；其余（常见/少见/稀有的普通个体）按「鱼种 + 异色」堆叠成一行。
        """
        if variant:
            return True
        return str(fish.get("rarity") or "") in ("传说", "神话")

    def _stack_rows(self, entries: list[dict[str, Any]]) -> list[str]:
        """把一批 ``{index, instance, alias, locked}`` 按上面的规则渲染成行。

        单独列出的沿用 ``alias``（连钓是「emoji+名字」，背包是整条 ``_instance_line``）；
        堆叠的合并成一行，**沿用这一批里第一条的序号** —— 这样背包的行号是连续可读的
        （``4. 🐡锦鲤 ×1``），不会出现跳号。
        """
        rows: list[str] = []
        stacked: dict[tuple[str, str], dict[str, Any]] = {}
        order: list[tuple[str, str]] = []
        for entry in entries:
            instance = entry.get("instance") or {}
            key = (
                str(instance.get("fish_id") or ""),
                str(instance.get("variant") or ""),
            )
            fish = FISH_BY_ID.get(key[0]) or {}
            if entry.get("locked") or self._stack_show_separate(fish, key[1]):
                rows.append(entry["alias"])
                continue
            item = stacked.get(key)
            if item is None:
                var_id = key[1]
                var_emoji = ""
                if var_id:
                    var_emoji = str((VARIANT_BY_ID.get(var_id) or {}).get("emoji") or "")
                item = {
                    "emoji": f"{_fish_emoji(fish)}{var_emoji}",
                    "name": str(fish.get("name") or "未知"),
                    "count": 0,
                    "value": 0,
                    "marked": bool(entry.get("mark")),
                    "index": entry.get("index"),
                }
                stacked[key] = item
                order.append(key)
            item["count"] += 1
            item["value"] += _instance_value(instance)
            if entry.get("mark"):
                item["marked"] = True
        for key in order:
            item = stacked[key]
            mark = "🔒" if item["marked"] else ""
            prefix = f"{item['index']:>2}." if isinstance(item.get("index"), int) else ""
            rows.append(
                f"{prefix}{mark}{item['emoji']}{item['name']} ×{item['count']}"
                f"　{_fmt_gold(item['value'])}金"
            )
        return rows

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
            + self._rod_pull_text(rod)
            + (f"　需{rod['unlock_level']}级" if rod.get("unlock_level", 1) > 1 else "")
            for rod in sorted(
                _shop_visible_rods(self.rods),
                key=lambda r: _safe_int(r.get("price"), 0, 0),
            )
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

        def paginate(
            title: str, rows: list[str], tail: list[str] | None = None
        ) -> list[tuple[str, list[str]]]:
            """把一张长表切成每页不超过 :data:`HELP_PAGE_MAX_LINES` 行的几页。

            ``tail``（「用法提示」那几行）只挂在最后一页，并且**算在行数里**——
            否则最后一页会正好超出一行（站长那边的道具页就是这么变成 21 行的）。
            """
            tail_rows = list(tail or [])
            chunks: list[list[str]] = []
            current: list[str] = []
            for index, row in enumerate(rows):
                rest = len(rows) - index - 1
                # 最后一行要给尾巴留位置（尾巴只出现在最后一页）
                limit = HELP_PAGE_MAX_LINES - (len(tail_rows) if rest <= 0 else 0)
                if current and len(current) >= max(limit, 1):
                    chunks.append(current)
                    current = []
                current.append(row)
            if current or not chunks:
                chunks.append(current)
            if len(chunks[-1]) + len(tail_rows) > HELP_PAGE_MAX_LINES:
                # 兜底：尾巴特别长时再拆一页（正常不会走到）
                last = chunks.pop()
                keep = max(HELP_PAGE_MAX_LINES - len(tail_rows), 1)
                chunks.append(last[:keep])
                chunks.append(last[keep:])
            chunks[-1] = chunks[-1] + tail_rows
            if len(chunks) == 1:
                return [(title, chunks[0])]
            return [
                (f"{title} {i}/{len(chunks)}", chunk)
                for i, chunk in enumerate(chunks, 1)
            ]
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
                    "　/钓鱼 卖 1 2 3…　 卖掉这些序号（不限个数，也能 卖 鲤鱼 / 卖 全部）",
                    "　/钓鱼 卖光光　　　 一次清空（锁定的会留着）",
                    "　/钓鱼 锁定 1　　　 锁定不想卖的鱼（/钓鱼 解锁 1 取消）",
                    "　/钓鱼 鱼竿　　　　 鱼竿店（买竿 / 换竿；装备 不带名字 = 换最好的）",
                    "　/钓鱼 道具　　　　 道具店（饲料 / 育灵水 / 洗髓丹…）",
                    "　/钓鱼 鱼饵　　　　 鱼饵店（面包屑 / 蚯蚓 / 红虫…）",
                    "　/钓鱼 买 蚯蚓 20　 懒得记店名就用它（自动认）",
                    "　/钓鱼 装备 星辉竿　换鱼竿（/钓鱼 鱼竿 看全部）",
                    "　/钓鱼 换饵 蚯蚓　　换当前鱼饵（/钓鱼 换饵 空钩 = 不挂饵）",
                    "　/钓鱼 自动 玉佩　　饵/手气道具用完自动买（/钓鱼 自动 看设置）",
                    "　/钓鱼 扩建背包　　 背包扩容",
                    f"　背包上限 {base_cap} 条起，不能无限囤货",
                ],
            ),
            (
                "赚钱养鱼",
                [
                    "　/钓鱼 签到　　　　 每天领一笔金币",
                    "　/钓鱼 今日　　　　 今日天气 + 鱼市行情",
                    "　/钓鱼 订单　　　　 订单（按当前钓点刷新，价随你的收入涨）",
                    "　/钓鱼 交 1 2　　　 交单（交过的不再收）",
                    "　— 鱼缸（挂机收益）—",
                    "　/钓鱼 水族馆　　　 看鱼缸",
                    "　/钓鱼 放 1 3 5…　 把背包这些序号放进缸（不限个数，放 全部 也行）",
                    "　/钓鱼 取 1　　　　 取回来（取 全部 也行）",
                    "　/钓鱼 领　　　　　 领挂机收益（鱼在缸里待得越久越多）",
                    "　/钓鱼 喂 高级饲料 1　投喂：涨三维、直接涨价",
                    "　/钓鱼 洗 2　　　　 洗髓丹：重掷第 2 条的个体品质（极小概率洗出神品）",
                    "　/钓鱼 用 珊瑚造景　 摆装饰：72 小时内挂机产出 +20%",
                    "　/钓鱼 水族馆 扩建　 花金币扩容鱼缸（越往后越贵、加得越多）",
                    "　/钓鱼 供奉　　　　 香火：花一笔大钱换 24 小时加成（后期钱多就点它）",
                ],
            ),
            (
                "称号与收集",
                [
                    "　/钓鱼 称号　　　　 称号清单（纯炫耀、不加属性，钱多就买一个）",
                    "　/钓鱼 称号 买 老钓手　买下并戴上",
                    "　/钓鱼 大鱼乐　　　 🎰 买彩票开奖（奖品可能是神话鱼 / 金币 / 道具）",
                    "　/钓鱼 大鱼乐 10　　 连抽 10 张（一次出汇总）",
                    "　/钓鱼 大鱼乐 概率　 看每一档的概率与长期期望（公开透明）",
                    "　/钓鱼 图鉴　　　　 各钓点的收集进度",
                    "　/钓鱼 图鉴 详　　　 完整鱼名单（可翻页）",
                    "　/钓鱼 查 <名字>　　 鱼/钓点/鱼饵/鱼竿/道具/杂物/变异/天气/成就 都能查",
                    "　/钓鱼 杂物　　　　 杂物与纸条收集",
                    "　/钓鱼 排行　　　　 群内排行榜（称号会显示在这里）",
                    "　/钓鱼 档案　　　　 等级、金币、统计、称号",
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
            paginate(
                "鱼竿",
                rod_lines,
                [
                    "　🔒 的竿要等级达标才能买",
                    "　/钓鱼 买 <竿名>　　 买鱼竿",
                    "　/钓鱼 装备 <竿名>　 换上",
                ],
            )
        )
        pages.extend(
            paginate(
                "鱼饵",
                bait_lines,
                [
                    "　部分鱼饵要等级 + 对应鱼竿才能买",
                    "　/钓鱼 买 <名字> [个数]　/钓鱼 换饵 <名字>",
                ],
            )
        )
        pages.extend(
            paginate(
                "道具",
                item_lines,
                [
                    "　/钓鱼 喂 <饲料> 1　投喂（三维永久上涨）",
                    "　/钓鱼 洗 1　　　　 洗髓丹：重掷个体品质，极小概率出「神品」",
                    "　/钓鱼 用 珊瑚造景　摆装饰：挂机产出 +20%",
                    f"　图鉴 {len(FISH_POOL)} 种　成就 {len(ACHIEVEMENTS)} 个",
                ],
            )
        )
        pages.extend(
            [
                (
                    "钓点与图鉴",
                    [
                        "　/钓鱼 钓点　　　　　 全部钓点 + 解锁条件（含你还差什么）",
                        "　/钓鱼 去 <钓点名>　 前往（写简称也行）",
                        "　/钓鱼 解锁 <钓点名> 满足条件后一键解锁并前往",
                        "　/钓鱼 图鉴　　　　　 各钓点的收集进度",
                        "　/钓鱼 图鉴 详　　　 完整鱼名单（可翻页）",
                        "　/钓鱼 查 <鱼名>　　 这鱼在哪些钓点、要不要拉线",
                        "　/钓鱼 查 <钓点名>　 这个钓点有哪些鱼",
                    ],
                ),
                (
                    "品质与拉线",
                    [
                        f"　鱼种：{rarity_line}（固有，不可变）",
                        "　个体：⚪凡品 🟢良品 💎精品 🏆珍品 👑绝品 🔱神品",
                        f"　只有 {interactive} 需要拉线",
                        "　🎯完美 > 👍良好 > 😅偏差，超时鱼会跑",
                        f"　图鉴 {len(FISH_POOL)} 种　成就 {len(ACHIEVEMENTS)} 个",
                        "　累计钓获 1/10/25/50/100… 有里程碑",
                        "　小鱼缸养久了还有挂机收益，见上一页",
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
        # 回体力道具的每日额度（v1.18.18）：能喝几次一眼看到
        soup_cap = _safe_int(cfg.get("hot_soup_daily_limit"), 0, 0)
        if soup_cap > 0:
            used = _daily_used(player, "heal")
            lines.append(
                f"　🍲 今日回体力额度 {used}/{soup_cap} 次"
                f"（剩 {max(0, soup_cap - used)} 次，/钓鱼 用 姜汤）"
            )
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
