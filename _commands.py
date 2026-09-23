# -*- coding: utf-8 -*-
"""子命令实现：背包 / 卖鱼 / 三家商店（鱼竿·道具·鱼饵）/ 图鉴 / 钓点 / 水族馆 / 订单 / 签到…

这些方法是从 main.py 原样搬过来的（缩进未变），可以直接引用 main.py 的常量与
工具函数——共享方式是「main 在模块末尾把自己的全局注入本模块」，详见 main.py
顶部的说明。之所以能这样搬，是为了不改动几百处调用点。

⚠️ 维护约定：
  1. 不要在本模块对共享的模块级常量做重新赋值（`X = ...` 只会改到本模块副本），
     需要改数值请在 main.py 的 `_apply_tunable_config()` 里改。
  2. 本模块的方法通过 `self.` 互相调用，跨模块调用也一样。
"""

from __future__ import annotations

import sys
from typing import Any


def _effects_module() -> Any:
    """拿效果注册表模块（v1.13.0）。

    main.py 用 ``_load_sibling`` 把兄弟模块注册成 ``astrbot_fishing_*`` 名字，
    所以这里按名字找；找不到就返回 None（插件照常跑，只是不处理扩展效果）。
    """
    for name in ("astrbot_fishing_effects", "_effects"):
        module = sys.modules.get(name)
        if module is not None:
            return module
    try:
        import _effects as module       # 单测直接导入时走这里
        return module
    except Exception:
        return None


def _ext_effect_lines(plugin: Any, player: dict[str, Any], item_id: str,
                      effects: dict[str, Any]) -> list[str]:
    """跑一遍扩展效果（内置键由插件原有分支处理，这里不插手）。"""
    module = _effects_module()
    if module is None:
        return []
    try:
        return module.apply_extension_effects(plugin, player, item_id, effects)
    except Exception as e:                       # 扩展再坏也不能影响玩家
        module.log_error(f"扩展效果处理失败：{type(e).__name__}: {e}")
        return []


def _has_ext_effect(effects: dict[str, Any]) -> tuple[bool, bool]:
    """返回 ``(有扩展键, 有内置键)``。"""
    module = _effects_module()
    if module is None or not effects:
        return False, False
    ext = builtin = False
    for key in effects:
        spec = module.EFFECTS.get(key)
        if spec is None:
            continue
        if spec.source == module.BUILTIN_SOURCE:
            builtin = True
        else:
            ext = True
    return ext, builtin


class CommandsMixin:
    """子命令实现：背包 / 卖鱼 / 三家商店（鱼竿·道具·鱼饵）/ 图鉴 / 钓点 / 水族馆 / 订单 / 签到…（由 FishingPlugin 继承，见 main.py 的类定义）。"""

    async def _cmd_event(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """处理小插曲的选择：/钓鱼 事件 1"""
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            current = player.get("event") or {}
            event_def = EVENT_BY_ID.get(current.get("id", ""))
            if not isinstance(event_def, dict):
                owner, ts = self._recent_events.get(self._session_key(event), ("", 0.0))
                if owner and owner != user_id and time.time() - ts < 600:
                    async for _r in self._say_msg(event, "story.wrong_owner", event.plain_result(
                            "🙅 这是别人的动静，你插不上手\n"
                            "　自己下竿的时候才会遇到属于你的小插曲"
                        )):
                        yield _r
                    return
                async for _r in self._say_msg(event, "story.none", event.plain_result("🤔 眼下没什么需要你决定的事")):
                    yield _r
                return
            # 插曲放着不管会自己散掉（10 分钟）
            if int(time.time()) - _safe_int(current.get("ts"), 0, 0) > 600:
                player.pop("event", None)
                await self._save_player(player)
                async for _r in self._say_msg(event, "story.expired", event.plain_result("💨 你犹豫了一会儿，那点动静已经过去了")):
                    yield _r
                return

            choices = event_def.get("choices") or []
            # 选项顺序是**打乱后**存下来的：玩家看到的 1/2 到底对应哪个选项，
            # 以存档里的 order 为准（提示、按钮、这里三处必须一致）
            order = self._event_order_indices(event_def, current.get("order"))
            idx = _to_int(a2, 0)
            if not (1 <= idx <= len(order)):
                text, _ = self._event_prompt(
                    event_def, order, rows=False,
                    text=self._event_text(event_def, player),
                )
                async for _r in self._say_msg(event, "story.bad_choice", event.plain_result(text)):
                    yield _r
                return

            choice = choices[order[idx - 1]]
            player.pop("event", None)
            lines = [f"　{choice['text']}"]

            # 结算：奖励都很轻，不影响经济。
            # v1.18.0 起奖励支持**随机区间**（`"gold": [20, 60]`），并且两个选项都该有
            # 自己的收益 —— 旧数据写死单个数字（`"gold": 40`）也照旧能用。
            good_chance = _clamp(
                _safe_number(choice.get("good_chance"), 0.65), 0.0, 1.0
            )
            good = random.random() < good_chance
            reward = choice.get("reward") if isinstance(choice.get("reward"), dict) else {}
            if not reward:                      # 兼容旧格式：奖励直接写在选项上
                reward = {
                    key: choice[key]
                    for key in ("gold", "bait", "luck", "note")
                    if choice.get(key) is not None
                }
            if good:
                lines.append(f"　{choice.get('good') or '……'}")
                lines.extend(self._grant_event_reward(player, reward))
            else:
                lines.append(f"　{choice.get('idle') or '什么也没发生。'}")
                # 空手也不是白来：给一点点安慰（站长要求「两个选项都要有随机收益」）
                lines.extend(self._grant_event_reward(player, self._event_consolation(reward)))

            new_ach = self._check_achievements(player)
            # ---- 剧情进度：旗标 / 前情提要 / 一次性与连载的收尾（v1.18.13）----
            self._record_story_choice(player, event_def, choice)
            saved = await self._save_player(player)
            async for _r in self._say_msg(event, "story.result", event.plain_result("\n".join(lines))):
                yield _r

    def _record_story_choice(
        self, player: dict[str, Any], event_def: dict[str, Any], choice: dict[str, Any]
    ) -> None:
        """把这一次的选择写进剧情进度（让下一段接得上）。

        * 选项里的 ``set`` → 记旗标（后面的插曲/连载用它决定演什么）
        * 记一句「上次：…」，下一话开头当前情提要
        * 一次性插曲记进 ``seen``（``once`` 的不再重复）
        * 连载演完最后一话 → 收尾（这条线不再从头演）
        """
        story = self._story_state(player)
        flags = story["flags"]
        for key, value in (choice.get("set") or {}).items():
            if isinstance(key, str):
                flags[key] = value
        recap = str(
            choice.get("recap") or event_def.get("recap") or choice.get("text") or ""
        ).strip()
        if recap:
            story["last"] = recap[:120]
        event_id = str(event_def.get("id") or "")
        chain_id = str(event_def.get("chain") or "")
        if chain_id:
            chain = CHAIN_BY_ID.get(chain_id) or {}
            episodes = chain.get("episodes") or []
            if _safe_int(event_def.get("episode"), 0, 0) >= len(episodes):
                if chain_id not in story["done"]:
                    story["done"].append(chain_id)
                story["arc"] = ""
                story["ep"] = 0
        elif event_def.get("once") and event_id and event_id not in story["seen"]:
            story["seen"].append(event_id)

    def _event_reward_range(self, value: Any) -> tuple[float, float]:
        """把奖励写法统一成 ``(最小, 最大)``：数字 -> 定值，``[a, b]`` -> 区间。"""
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            low = _safe_number(value[0], 0.0)
            high = _safe_number(value[1], low)
            return (low, max(low, high))
        number = _safe_number(value, 0.0)
        return (number, number)

    def _event_consolation(self, reward: dict[str, Any]) -> dict[str, Any]:
        """「没赶上好事」时的安慰奖：只给最小的那一份钱/饵，手气与纸条不给。

        两头都有收益，玩家才不会永远只点同一个选项；但也不至于让空结果和白拿一样。
        """
        small: dict[str, Any] = {}
        if "gold" in reward:
            low, _high = self._event_reward_range(reward["gold"])
            if low > 0:
                small["gold"] = [max(1, low * 0.25), max(1, low * 0.5)]
        if "bait" in reward:
            low, _high = self._event_reward_range(reward["bait"])
            if low >= 2:
                small["bait"] = [1, 1]
        return small

    def _grant_event_reward(
        self, player: dict[str, Any], reward: dict[str, Any]
    ) -> list[str]:
        """按奖励表发奖（区间内随机）。返回要显示的文案行。"""
        lines: list[str] = []
        if not reward:
            return lines
        if "gold" in reward:
            low, high = self._event_reward_range(reward["gold"])
            gold = int(round(random.uniform(low, high)))
            if gold > 0:
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + gold
                lines.append(f"　💰 +{_fmt_gold(gold)}")
        if "bait" in reward:
            low, high = self._event_reward_range(reward["bait"])
            count = int(round(random.uniform(low, high)))
            if count > 0:
                bait_id = "worm"
                baits = player.setdefault("baits", {})
                baits[bait_id] = _safe_int(baits.get(bait_id), 0, 0) + count
                lines.append(f"　{self._bait_label(bait_id)} ×{count}")
        if "luck" in reward:
            low, high = self._event_reward_range(reward["luck"])
            gain = max(0.0, random.uniform(low, high))
            if gain > 0:
                # 一次性手气：只作用于**下一竿**，用完即清（_calc._consume_luck）。
                # 和锦鲤玉佩**叠加**（来源不同、寿命不同），这里把合计写出来。
                before = _safe_number(player.get("luck_charges"), 0.0)
                player["luck_charges"] = _clamp(before + gain, 0.0, 2.0)
                end = _safe_number(player["luck_charges"], 0.0)
                pendant = (
                    _safe_number(player.get("buff_quality"), 0.0)
                    if _safe_int(player.get("buff_casts_left"), 0, 0) > 0
                    else 0.0
                )
                hint = f"　🔮 下一竿手气：{_luck_stars(gain, 0.3)}"
                if pendant > 0:
                    hint += (
                        f"（玉佩 +{pendant:.0%} 叠加，下一竿共 +{end + pendant:.0%}，"
                        f"之后回到 +{pendant:.0%}）"
                    )
                lines.append(hint)
        if "note" in reward and BOTTLE_NOTES:
            low, high = self._event_reward_range(reward["note"])
            chance = _clamp(random.uniform(min(low, high), max(low, high)), 0.0, 1.0)
            if random.random() < chance:
                note = random.choice(BOTTLE_NOTES)
                notes = player.setdefault("bottle_notes", [])
                if note not in notes:
                    notes.append(note)
                    player["bottle_notes"] = notes[-30:]
                lines.append(f"　📜 你记下了一句：{note}")
        return lines

    def _order_rarities(self, level: int) -> tuple[str, ...]:
        """按等级取当前可出现的订单品质。"""
        result: tuple[str, ...] = ("常见",)
        for lv, rarities in ORDER_RARITY_BY_LEVEL:
            if level >= lv:
                result = rarities
        return result

    def _order_follow_location(self) -> bool:
        """订单是否跟着当前钓点走（配置开关，默认开）。"""
        return bool(self.cfg.get("order_follow_location", True))

    def _order_include_hidden(self) -> bool:
        """订单能不能点「隐藏生物」（默认能，= v1.14.0 之前的行为）。"""
        return bool(self.cfg.get("order_include_hidden", True))

    def _order_move_limit(self) -> int:
        """每个刷新周期内，换钓点最多能换几批订单（0 = 换钓点也不换单，防刷单）。"""
        return max(0, _safe_int(self.cfg.get("order_move_rerolls"), 1, 0))

    def _order_pools(
        self, level: int, location_id: str | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """订单抽签池，返回 ``(首选池, 补充池)``（v1.14.0：默认只从**当前钓点**抽）。

        * 给了 ``location_id`` -> 候选 = 这个钓点的鱼（`_location_pool`）。
          首选 = 等级品质档内的（贵、奖励高），补充 = 同图其它品质的
          —— 本图这一档不够时就拿同图的鱼补满，**绝不跨图**，
          所以「站在哪儿就只能被点哪儿的货」永远成立。
        * ``location_id=None`` -> 老行为：首选 = 全鱼池里等级品质档内的鱼，
          补充池为空（老版本也只从品质档里抽）。
        * 隐藏生物（大肥鱼这类「藏起来的小惊喜」）默认照样能点，由
          ``order_include_hidden`` 决定；关掉后只有在一只都不剩时才放它进来。
        """
        rarities = set(self._order_rarities(level))
        pool: list[dict[str, Any]] = []
        if location_id:
            seen: set[str] = set()
            for fish, _weight in _location_pool(location_id):
                if fish["id"] in seen:
                    continue
                seen.add(fish["id"])
                pool.append(fish)
        if not pool:
            pool = list(FISH_POOL)
        if not self._order_include_hidden():
            visible = [f for f in pool if f["id"] not in HIDDEN_EVERYWHERE]
            if visible:
                pool = visible
        preferred = [f for f in pool if f["rarity"] in rarities]
        if not preferred:
            # 这个钓点整档都不匹配（站长自配的图才可能）-> 全用本图的鱼
            return pool, []
        if not location_id:
            return preferred, []
        extra = [f for f in pool if f["rarity"] not in rarities]
        return preferred, extra

    def _order_income_factor(self, player: dict[str, Any]) -> float:
        """订单奖励的**动态系数**（v1.18.17）：让订单价跟上玩家自己的收入。

        站长报「订单价格太低」—— 原来的订单价只看鱼的基准价，完全没算玩家身上
        那堆加成（鱼竿价值加成、当前钓点的价值倍率、等级带来的收益成长），
        于是后期一条订单还不如随手卖两条鱼。现在：

            系数 = 钓点倍率 × (1 + 鱼竿价值加成) × (1 + order_level_growth × (等级-1))

        再钳到 ``[1.0, order_factor_max]``。默认 62 级龙宫 + 归墟竿 ≈ 6.0（封顶），
        也就是订单单价约等于卖店的 4~5 倍；1 级新手仍然是「比卖店赚一倍」。
        """
        rod = self._rod(player)
        loc = self._location(player)
        level = max(1, _player_level(player))
        gear = 1.0 + max(0.0, _safe_number(rod.get("value_bonus"), 0.0))
        place = max(1.0, _safe_number(loc.get("value_mult"), 1.0))
        growth = max(0.0, _safe_number(self.cfg.get("order_level_growth"), 0.02))
        factor = gear * place * (1.0 + growth * (level - 1))
        cap = max(1.0, _safe_number(self.cfg.get("order_factor_max"), 6.0))
        return _clamp(factor, 1.0, cap)

    def _roll_orders(
        self, level: int, location_id: str | None = None, player: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """生成一批订单。越贵的鱼要得越少。

        ``location_id`` = 按这个钓点抽鱼；``None`` = 不按钓点（老行为，全鱼池）。
        ``player`` = 用来算动态系数（鱼竿 / 钓点 / 等级），拿不到就退回 1.0。
        """
        preferred, extra = self._order_pools(level, location_id)
        total = len(preferred) + len(extra)
        count = min(int(self.cfg["order_count"]), total)
        picked = random.sample(preferred, min(count, len(preferred)))
        if len(picked) < count:  # 本图这一档不够 -> 用同图其它品质补满
            picked += random.sample(extra, count - len(picked))

        mult = float(self.cfg["order_reward_mult"])
        factor = self._order_income_factor(player) if player is not None else 1.0
        orders: list[dict[str, Any]] = []
        for fish in picked:
            if _fish_value(fish) <= 10:
                need = random.randint(3, 6)
            elif _fish_value(fish) <= 60:
                need = random.randint(2, 4)
            elif _fish_value(fish) <= 300:
                need = random.randint(1, 2)
            else:
                need = 1
            unit = max(1, int(_fish_value(fish) * 1.3))  # 参考单价（含品质均值）
            orders.append(
                {
                    "fish_id": fish["id"],
                    "need": need,
                    "have": 0,
                    "reward": max(1, int(unit * need * mult * factor)),
                    "done": False,
                }
            )
        return orders

    def _next_order_ts(self, now: int | None = None) -> int:
        """下一批订单出现的时间戳（几小时一批，间隔在配置里可调）。"""
        now = int(now if now is not None else time.time())
        low = max(1, _safe_int(self.cfg.get("order_refresh_min_hours"), 3, 1))
        high = max(low, _safe_int(self.cfg.get("order_refresh_max_hours"), 6, 1))
        return now + random.randint(low * 3600, high * 3600)


    def _order_location_or_none(self, player: dict[str, Any]) -> str | None:
        """这批订单该按哪个钓点抽；``None`` = 不按钓点（开关关了）。"""
        if not self._order_follow_location():
            return None
        return str(player.get("current_location") or DEFAULT_LOCATION)

    def _ensure_orders(self, player: dict[str, Any]) -> bool:
        """确保当前这批订单是「对的」：没过期，而且跟当前钓点对得上（v1.14.0）。

        刷新规则：
        * 还没订单 / 到 `order_next_ts` 了 -> 换一批新的（老规则，没动）；
        * 开着「订单跟随钓点」但订单是别的钓点的 -> 也换一批，让点单只点脚下
          钓得到的鱼；换钓点**不重置**刷新倒计时，而且每个刷新周期最多换
          `order_move_rerolls` 次（默认 1），所以来回换图刷不出无限批订单。

        返回 True 表示数据有变化需要保存。
        """
        now = int(time.time())
        next_ts = _safe_int(player.get("order_next_ts"), 0, 0)
        has_orders = bool(player.get("orders"))
        location = self._order_location_or_none(player)
        # 到点（或第一次）刷新：跟钓点无关，照旧换一批，并把「换钓点次数」清零。
        # 没到点但订单是别的钓点的：也算要换，但要受 order_move_rerolls 限制。
        expired = (not has_orders) or next_ts <= now
        moved = (
            (not expired)
            and location is not None
            and str(player.get("order_location") or "") != location
        )
        if not expired and not moved:
            return False
        rerolls = _safe_int(player.get("order_move_rerolls"), 0, 0)
        if moved and rerolls >= self._order_move_limit():
            return False
        player["orders"] = self._roll_orders(_player_level(player), location, player)
        player["order_next_ts"] = self._next_order_ts(now)
        player["order_date"] = self._today_text()  # 只用于展示「这是哪天接的单」
        player["order_location"] = location or ""
        player["order_move_rerolls"] = (rerolls + 1) if moved else 0
        return True

    async def _cmd_orders(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str
    ):
        """订单：/钓鱼 订单 ｜ /钓鱼 订单 交 1（不定时刷新，交过就不能再交）"""
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            level = _player_level(player)
            changed = self._ensure_orders(player)

            if level < int(self.cfg["order_unlock_level"]):
                if changed:
                    await self._save_player(player)
                async for _r in self._say_msg(event, "orders.locked", event.plain_result(
                        f"📋 订单需要 {int(self.cfg['order_unlock_level'])} 级"
                        f"（你现在 {level} 级，多钓鱼吧）"
                    )):
                    yield _r
                return

            orders: list[dict[str, Any]] = player.get("orders") or []
            sub = (a2 or "").strip().lower()
            # 少打空格的容错：`交1`
            peeled_order = self._peel_action(sub, ORDER_ACTIONS)
            if peeled_order:
                sub, glued = peeled_order
                a3 = f"{glued} {a3}".strip()

            # ---- 提交订单（支持批量：交 1 2 / 交 1-3 / 交 全部）----
            if sub in ("交", "提交", "submit"):
                idxs = self._parse_indices(a3, orders)
                if not idxs:
                    async for _r in self._say_msg(event, "orders.usage", event.plain_result(
                            f"📖 /钓鱼 订单 交 <序号>　1~{len(orders)}，"
                            f"支持 1 2 3 / 1-3 / 全部"
                        )):
                        yield _r
                    return

                done_lines: list[str] = []
                total_reward = 0
                short: list[str] = []
                already: list[int] = []
                inventory: list[dict[str, Any]] = player.get("inventory") or []

                for idx in idxs:
                    order = orders[idx - 1]
                    fish = FISH_BY_ID.get(order.get("fish_id", ""))
                    if fish is None:
                        continue
                    if order.get("done"):
                        already.append(idx)
                        continue
                    need = _safe_int(order.get("need"), 1, 1)
                    # 优先拿最便宜的个体交单，把好鱼留给自己
                    group = sorted(
                        [x for x in inventory if x.get("fish_id") == fish["id"]],
                        key=_instance_value,
                    )
                    if len(group) < need:
                        short.append(
                            f"{fish['name']} 还差 {need - len(group)} 条"
                        )
                        continue
                    used_ids = {id(x) for x in group[:need]}
                    inventory = [
                        x for x in inventory if id(x) not in used_ids
                    ]
                    reward = _safe_int(order.get("reward"), 1, 1)
                    total_reward += reward
                    order["done"] = True
                    player["total_orders"] = (
                        _safe_int(player.get("total_orders"), 0, 0) + 1
                    )
                    done_lines.append(
                        f"　{idx}. {fish['name']} ×{need} → {_fmt_gold(reward)}"
                    )

                if not done_lines:
                    msg = "🤔 这些订单都没交成"
                    if already:
                        msg += (
                            "　已经交过："
                            + "、".join(str(i) for i in already)
                        )
                    if short:
                        msg += "\n　鱼不够：" + "；".join(short)
                    async for _r in self._say_msg(event, "orders.submit_result", event.plain_result(msg)):
                        yield _r
                    return

                player["inventory"] = inventory
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + total_reward

                lines = [f"📦 交单 {len(done_lines)} 单 → {_fmt_gold(total_reward)} 金币"]
                lines.extend(done_lines)
                lines.append(f"💰 余额 {_fmt_gold(player['gold'])}")
                if already:
                    lines.append(
                        f"　（跳过已经交过的 {'、'.join(str(i) for i in already)}）"
                    )
                if short:
                    lines.append("　（鱼不够：" + "；".join(short) + "）")
                saved = await self._save_with_notices(player, lines)
                async for _r in self._say_msg(event, "orders.submit_result", event.plain_result("\n".join(lines))):
                    yield _r
                return

            # ---- 查看订单 ----
            if changed:
                await self._save_player(player)
            inventory = player.get("inventory") or []
            held: dict[str, int] = {}
            for inst in inventory:
                fid = inst.get("fish_id")
                if isinstance(fid, str):
                    held[fid] = held.get(fid, 0) + 1

            lines = [self._order_head_text(player)]
            if self._order_follow_location():
                batch_loc = str(player.get("order_location") or "")
                here = str(player.get("current_location") or DEFAULT_LOCATION)
                if batch_loc and batch_loc != here:
                    batch_name = (self.location_by_id.get(batch_loc) or {}).get(
                        "name", batch_loc
                    )
                    lines.append(
                        f"⚠️ 这批是「{batch_name}」的单，你人在"
                        f"「{self._location_label(player)}」"
                        "（回去交，或等它刷新）"
                    )
            for i, order in enumerate(orders, start=1):
                fish = FISH_BY_ID.get(order.get("fish_id", ""))
                if fish is None:
                    continue
                need = _safe_int(order.get("need"), 1, 1)
                have = min(held.get(fish["id"], 0), need)
                mark = "✅" if order.get("done") else ("📦" if have >= need else "⏳")
                lines.append(
                    f"{i}.{mark}{_fish_emoji(fish)}{fish['name']} "
                    f"{have}/{need}　→ {_fmt_gold(order.get('reward', 0))}"
                )
            lines.append("💡 /钓鱼 订单 交 1 提交（支持 交 1 2 3 / 交 全部）")
            async for _r in self._say_msg(event, "orders.list", event.plain_result("\n".join(lines))):
                yield _r

    # -------------------------------------------------------------------------
    # 钓点 / 鱼竿 / 背包
    # -------------------------------------------------------------------------


    async def _cmd_locations(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str
    ):
        """钓点：/钓鱼 钓点 ｜ /钓鱼 钓点 解锁 <名称> ｜ /钓鱼 去 <名称>

        解锁规则：等级达标 + 上一个钓点图鉴开到 80%（隐藏生物不计） + 一次性金币，三样齐了才能前往。
        """
        sub = (a2 or "").strip().lower()
        # 少打空格的容错：`解锁湖泊` / `去湖泊`
        peeled_loc = self._peel_action(sub, LOCATION_ACTIONS)
        if peeled_loc:
            sub, glued = peeled_loc
            a3 = f"{glued} {a3}".strip()
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            level = _player_level(player)
            unlocked: list[str] = player.get("locations") or []
            current = player.get("current_location", DEFAULT_LOCATION)

            # ---- 前往 ----
            if sub in ("去", "前往", "go"):
                target = self._find_location(a3 or a2)
                if target is None:
                    async for _r in self._say_msg(event, "location.not_found", event.plain_result("🤔 没有这个钓点，/钓鱼 钓点 看看")):
                        yield _r
                    return
                if target["id"] not in unlocked:
                    async for _r in self._say_msg(event, "location.locked", event.plain_result(
                            f"🔒 {target['name']} 还没解锁，先 /钓鱼 钓点 解锁 {target['name']}"
                        )):
                        yield _r
                    return
                if target["id"] == current:
                    async for _r in self._say_msg(event, "location.already_here", event.plain_result(f"📍 你已经在 {target['name']} 了")):
                        yield _r
                    return
                player["current_location"] = target["id"]
                # 订单跟着钓点走：换地图后点单只会点这儿的鱼（开关关了就什么都不做）
                had_orders = bool(player.get("orders"))
                orders_swapped = had_orders and self._ensure_orders(player)
                saved = await self._save_player(player)
                lines = [
                    f"🚶 前往 {target['emoji']}{target['name']}　"
                    f"价值×{target['value_mult']:.2f}"
                ]
                if orders_swapped:
                    lines.append(
                        f"📋 订单已换成「{target['name']}」的（旧单作废，鱼还在背包）"
                    )
                if not saved:
                    lines.append("⚠️ 保存失败")
                async for _r in self._say_msg(event, "location.moved", event.plain_result("\n".join(lines))):
                    yield _r
                return

            # ---- 解锁 ----
            if sub in ("解锁", "unlock", "开"):
                target = self._find_location(a3 or a2)
                if target is None:
                    async for _r in self._say_msg(event, "location.not_found", event.plain_result("🤔 没有这个钓点")):
                        yield _r
                    return
                if target["id"] in unlocked:
                    async for _r in self._say_msg(event, "location.unlocked", event.plain_result(f"✅ {target['name']} 已解锁")):
                        yield _r
                    return
                # 解锁条件：等级 + 上一个钓点图鉴 80% + 一次性金币
                ratio = _clamp(
                    _safe_number(self.cfg.get("location_codex_gate"), 0.8), 0.0, 1.0
                )
                prev_id = self._prev_location_id(target["id"])
                prev_cfg = self.location_by_id.get(prev_id) or {} if prev_id else {}
                got = need = 0
                if prev_id:
                    got, need = self._location_codex_progress(player, prev_id)
                codex_need = int(need * ratio + 0.999)
                price = _safe_int(target.get("gold_gate"), 0, 0)
                gold_now = _safe_int(player.get("gold"), 0, 0)
                lacks: list[str] = []
                if prev_id and need and got < codex_need:
                    lacks.append(
                        f"图鉴「{prev_cfg.get('name', prev_id)}」{got}/{need}"
                        f"（需 {codex_need} 种，差 {codex_need - got} 种）"
                        f"　/钓鱼 图鉴 {prev_cfg.get('name', prev_id)}"
                    )
                if price > gold_now:
                    lacks.append(
                        f"金币 {_fmt_gold(price)}（你有 {_fmt_gold(gold_now)}，"
                        f"差 {_fmt_gold(price - gold_now)}）"
                    )
                if level < target["level_gate"]:
                    lacks.append(f"等级 {target['level_gate']} 级（你现在 {level} 级）")
                if lacks:
                    async for _r in self._say_msg(event, "location.unlock_need", event.plain_result(
                            f"🔒 还不能去 {target['emoji']}{target['name']}，还差：\n"
                            + "\n".join(f"　· {x}" for x in lacks)
                            + "\n　（图鉴里的隐藏生物不算数）"
                        )):
                        yield _r
                    return
                player["gold"] = gold_now - price
                unlocked.append(target["id"])
                player["locations"] = unlocked
                player["current_location"] = target["id"]
                # ⚠️ 先更新 locations 再检查成就，否则「解锁第二个钓点」这类
                # 依赖 locations 数量的成就要等到下一次操作才解锁。
                lines = [
                    f"🗺️ 解锁并前往 {target['emoji']}{target['name']}！",
                    f"　价值×{target['value_mult']:.2f}　"
                    f"💰 余额 {_fmt_gold(player['gold'])}",
                ]
                # 订单跟着钓点走（同「前往」）
                if bool(player.get("orders")) and self._ensure_orders(player):
                    lines.append(
                        f"📋 订单已换成「{target['name']}」的（旧单作废，鱼还在背包）"
                    )
                saved = await self._save_with_notices(player, lines)
                async for _r in self._say_msg(event, "location.unlock_go", event.plain_result("\n".join(lines))):
                    yield _r
                return

            # ---- 查看（v1.18.17：锁着的钓点也把解锁条件写全，站长要的）----
            # 已解锁：名称 + 价值倍率 + 描述；未解锁：把「等级 / 金币 / 上一张图的图鉴」
            # 三项条件和**你现在的进度**一起写出来，够条件了就直接提示可以解锁。
            ratio = _clamp(
                _safe_number(self.cfg.get("location_codex_gate"), 0.8), 0.0, 1.0
            )
            gold_now = _safe_int(player.get("gold"), 0, 0)
            ready: list[str] = []
            rows_all: list[str] = []
            for loc in sorted(
                self.locations,
                key=lambda l: (_safe_int(l.get("level_gate"), 1, 1),
                               _safe_int(l.get("gold_gate"), 0, 0)),
            ):
                if loc["id"] in unlocked:
                    here = "📍" if loc["id"] == current else "　"
                    rows_all.append(
                        f"{here}{loc['emoji']}{loc['name']}　×{loc['value_mult']:.2f}"
                        f"　{loc['desc']}"
                    )
                    continue
                prev_id = self._prev_location_id(loc["id"])
                prev_cfg = self.location_by_id.get(prev_id) or {} if prev_id else {}
                got = need = 0
                if prev_id:
                    got, need = self._location_codex_progress(player, prev_id)
                codex_need = int(need * ratio + 0.999)
                price = _safe_int(loc.get("gold_gate"), 0, 0)
                gate = _safe_int(loc.get("level_gate"), 1, 1)
                conds = [f"{gate} 级"]
                if price:
                    conds.append(f"{_fmt_gold(price)} 金")
                if prev_id and need:
                    conds.append(f"「{prev_cfg.get('name', prev_id)}」图鉴 {codex_need} 种")
                lacks = []
                if level < gate:
                    lacks.append(f"差 {gate - level} 级")
                if price > gold_now:
                    lacks.append(f"差 {_fmt_gold(price - gold_now)} 金")
                if prev_id and need and got < codex_need:
                    lacks.append(f"图鉴 {got}/{codex_need}")
                if lacks:
                    rows_all.append(
                        f"🔒{loc['emoji']}{loc['name']}　需 " + "・".join(conds)
                        + "　（" + "，".join(lacks) + "）"
                    )
                else:
                    ready.append(loc["name"])
                    rows_all.append(
                        f"🔓{loc['emoji']}{loc['name']}　需 " + "・".join(conds)
                        + "　✅ 条件已满足"
                    )

            # 19 个钓点一行一个太长：每页 10 个（正文 ≤16 行的老规矩）
            per_page = 10
            chunks = [
                rows_all[i:i + per_page] for i in range(0, len(rows_all), per_page)
            ] or [[]]
            page = _to_int(a3 or a2, 1)
            page = int(_clamp(page, 1, len(chunks)))
            title = "🗺️ 钓点" if len(chunks) == 1 else f"🗺️ 钓点 {page}/{len(chunks)}"
            body = [
                f"{title}　当前 {self._location_label(player)}　等级 {level}"
            ] + chunks[page - 1]
            if ready:
                body.append(
                    "💡 可以解锁：" + "、".join(ready[:4])
                    + ("…" if len(ready) > 4 else "")
                    + "　发 /钓鱼 解锁 <钓点名>"
                )
            body.append("💡 /钓鱼 去 <钓点名> 前往，/钓鱼 图鉴 <钓点名> 看收集进度")
            if len(chunks) > 1:
                body.append(f"　翻页：/钓鱼 钓点 {page % len(chunks) + 1}")
            # 「上一页 / 下一页」按钮（首尾页各缺一个，只有一页时整排不出现）
            async for reply in self._say(
                event, "\n".join(body), "location.list",
                page=(page, len(chunks), "/钓鱼 钓点"),
            ):
                yield reply

    async def _cmd_rods(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str
    ):
        """鱼竿：/钓鱼 鱼竿 ｜ 买 <名称> ｜ 用 <名称>"""
        sub = (a2 or "").strip().lower()
        # 少打空格的容错：`买碳素竿` / `用碳素竿`
        peeled_rod = self._peel_action(sub, ROD_ACTIONS)
        if peeled_rod:
            sub, glued = peeled_rod
            a3 = f"{glued} {a3}".strip()
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            owned: list[str] = player.get("rods") or [DEFAULT_ROD]
            equipped = player.get("equipped_rod", DEFAULT_ROD)

            if sub in ("买", "购买", "buy"):
                rod = self._find_rod(a3 or a2)
                if rod is None:
                    async for _r in self._say_msg(event, "rod.not_found", event.plain_result("🤔 没有这款鱼竿")):
                        yield _r
                    return
                if rod["id"] in owned:
                    async for _r in self._say_msg(event, "rod.owned", event.plain_result(f"✅ 你已经有 {rod['name']} 了")):
                        yield _r
                    return
                # 等级门槛：不够就明确告诉他还差多少
                refuse = self._unlock_refuse_text(player, rod)
                if refuse:
                    async for _r in self._say_msg(event, "rod.level_low", event.plain_result(
                            f"{refuse}\n　多钓几竿就升级了，升级后回来 /钓鱼 鱼竿 买 {rod['name']}"
                        )):
                        yield _r
                    return
                price = int(rod["price"])
                if _safe_int(player.get("gold"), 0, 0) < price:
                    async for _r in self._say_msg(event, "rod.no_gold", event.plain_result(
                            f"💸 {rod['name']} 需要 {_fmt_gold(price)} 金币，"
                            f"你只有 {_fmt_gold(player.get('gold', 0))}"
                        )):
                        yield _r
                    return
                player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                owned.append(rod["id"])
                player["rods"] = owned
                player["equipped_rod"] = rod["id"]
                lines = [
                    f"🎣 买到 {rod['emoji']}{rod['name']}！已自动装备",
                    f"　价值+{rod['value_bonus']:.0%}　手气{_luck_stars(rod['luck_bonus'], 0.2)}"
                    + self._rod_pull_text(rod)
                    + f"　💰 {_fmt_gold(player['gold'])}",
                ]
                saved = await self._save_with_notices(player, lines)
                async for _r in self._say_msg(event, "rod.bought", event.plain_result("\n".join(lines))):
                    yield _r
                return

            if sub in ("用", "装备", "换", "use", "equip"):
                # 不带名字 = 自动换上「已拥有里价值加成最高的那根」（按钮用得上）
                want = (a3 or a2 or "").strip()
                if not want:
                    pool = [r for r in self.rods if r["id"] in owned] or list(self.rods)
                    best = max(
                        pool,
                        key=lambda r: (
                            _safe_number(r.get("value_bonus"), 0.0),
                            _safe_number(r.get("luck_bonus"), 0.0),
                        ),
                    )
                    if best["id"] == equipped:
                        async for _r in self._say_msg(event, "rod.equipped", event.plain_result(
                                f"✅ 你现在用的就是最好的那根：{self._rod_label(player)}"
                        )):
                            yield _r
                        return
                    player["equipped_rod"] = best["id"]
                    saved = await self._save_player(player)
                    lines = [
                        f"✅ 自动换上最好的竿：{self._rod_label(player)}"
                        f"（价值+{best['value_bonus']:.0%}）",
                    ]
                    if not saved:
                        lines.append("⚠️ 保存失败")
                    async for _r in self._say_msg(event, "rod.equipped", event.plain_result("\n".join(lines))):
                        yield _r
                    return
                rod = self._find_rod(want)
                if rod is None:
                    async for _r in self._say_msg(event, "rod.not_found", event.plain_result("🤔 没有这款鱼竿")):
                        yield _r
                    return
                if rod["id"] not in owned:
                    async for _r in self._say_msg(event, "rod.not_owned", event.plain_result(f"🎒 你还没买 {rod['name']}")):
                        yield _r
                    return
                player["equipped_rod"] = rod["id"]
                saved = await self._save_player(player)
                lines = [f"✅ 已装备 {self._rod_label(player)}"]
                if not saved:
                    lines.append("⚠️ 保存失败")
                async for _r in self._say_msg(event, "rod.equipped", event.plain_result("\n".join(lines))):
                    yield _r
                return

            lines = [f"🎣 鱼竿　当前 {self._rod_label(player)}"]
            hidden = 0
            for rod in self.rods:
                owned_rod = rod["id"] in owned
                # 没解锁的竿不显示（等级够了才上架），已拥有的永远显示
                if not owned_rod and self._unlock_shortage(player, rod):
                    hidden += 1
                    continue
                here = "📍" if rod["id"] == equipped else "　"
                tag = "已拥有" if owned_rod else f"{_fmt_gold(rod['price'])}金"
                lines.append(
                    f"{here}{rod['emoji']}{rod['name']}　{tag}　"
                    f"价值+{rod['value_bonus']:.0%}　手气{_luck_stars(rod['luck_bonus'], 0.2)}"
                    + self._rod_pull_text(rod)
                )
            if hidden:
                lines.append("🔒 还有更多鱼竿，等级更高之后会陆续上架")
            lines.append("💡 /钓鱼 鱼竿 买 <名称> ｜ /钓鱼 鱼竿 用 <名称>")
            async for _r in self._say_msg(event, "rod.list", event.plain_result("\n".join(lines))):
                yield _r

    async def _cmd_backpack_upgrade(self, event: AstrMessageEvent, user_id: str):
        """背包扩容（公开入口，自己加锁）。"""
        async with self._lock_for(user_id):
            async for result in self._do_backpack_upgrade(event, user_id):
                yield result

    async def _do_backpack_upgrade(self, event: AstrMessageEvent, user_id: str):
        """背包扩容的实际逻辑。

        ⚠️ 调用方必须**已经持有该玩家的锁**。这是为了避免
        「/钓鱼 商店 扩容」这类老写法转发时重复获取同一把 asyncio.Lock 造成自死锁
        （asyncio.Lock 不可重入，我们真的踩过这个坑）。
        """
        player = await self._load_player(user_id)
        owned = player.get("backpack_slots") or []
        if not isinstance(owned, list):
            owned = []
        capacity = _backpack_capacity(player, self.cfg)

        nxt = next(
            (i for i in range(len(self.backpack_upgrades)) if i not in owned), None
        )
        if nxt is None:
            async for _r in self._say_msg(event, "backpack.max", event.plain_result(f"🎒 背包已扩到最大（{capacity}）")):
                yield _r
            return

        up = self.backpack_upgrades[nxt]
        price = int(up.get("price", 0))
        if _safe_int(player.get("gold"), 0, 0) < price:
            async for _r in self._say_msg(event, "backpack.no_gold", event.plain_result(
                    f"💸 扩容 +{up['add']} 需要 {_fmt_gold(price)} 金币，"
                    f"你只有 {_fmt_gold(player.get('gold', 0))}"
                )):
                yield _r
            return
        player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
        owned.append(nxt)
        player["backpack_slots"] = owned
        saved = await self._save_player(player)
        lines = [
            f"🎒 背包扩容 +{up['add']}！容量 {capacity} → "
            f"{_backpack_capacity(player, self.cfg)}",
            f"💰 余额 {_fmt_gold(player['gold'])}",
        ]
        if not saved:
            lines.append("⚠️ 保存失败")
        async for _r in self._say_msg(event, "backpack.upgraded", event.plain_result("\n".join(lines))):
            yield _r

    async def _cmd_today(self, event: AstrMessageEvent, user_id: str):
        """今日天气 + 鱼市行情 + 图鉴加成，一眼看完今天的看点。"""
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            changed = self._ensure_weather(player) | self._ensure_market(player)
            if changed:
                await self._save_player(player)

            weather = self._weather(player)
            lines = [f"📅 今日 · {player.get('weather_date') or self._today_text()}"]
            if weather:
                # 概率/倍率一律不写给玩家看，只留味道对得上的定性描述
                lines.append(
                    f"{weather['emoji']} {weather['name']}　{weather['desc']}"
                )
                lines.append(
                    f"　今日水情：{_weather_hint(weather)}"
                )
            else:
                lines.append("（天气系统未启用）")

            market = self._market_label(player)
            if market:
                lines.append(f"📈 今日高价：{market}")
                lines.append("　卖这些鱼能多赚不少，行情每天刷新")
            else:
                lines.append("📈 今日无特别行情")

            lines.append(f"📕 图鉴集齐加成：{self._codex_mult_text(player)}")
            completed = self._codex_completed_rarities(player)
            if completed:
                lines.append(
                    "　已集齐：" + "、".join(self._rarity_name(r) for r in completed)
                )
            async for _r in self._say_msg(event, "today.view", event.plain_result("\n".join(lines))):
                yield _r

    async def _cmd_leaderboard(self, event: AstrMessageEvent, user_id: str, a2: str):
        """群内排行榜：/钓鱼 排行 [金币|图鉴|收获|最贵]"""
        index = await self.get_kv_data(self._leaderboard_key(), {})
        if not isinstance(index, dict) or not index:
            async for _r in self._say_msg(event, "rank.empty", event.plain_result("📊 还没有排行数据，先去 /钓鱼 抛几竿吧")):
                yield _r
            return

        key = (a2 or "").strip().lower()
        if key in ("金币", "gold", "钱"):
            field, title, unit = "gold", "金币", "金币"
        elif key in ("图鉴", "种类", "kinds", "collect"):
            field, title, unit = "kinds", "图鉴种类", "种"
        elif key in ("最贵", "单条", "best"):
            field, title, unit = "best", "单条最贵", "金币"
        else:
            field, title, unit = "caught", "累计钓获", "条"

        rows = []
        for uid, entry in index.items():
            if not isinstance(entry, dict):
                continue
            value = _safe_int(entry.get(field), 0, 0)
            if value <= 0:
                continue
            name = entry.get("name") or uid
            rows.append((value, str(name), str(uid), entry))
        if not rows:
            async for _r in self._say_msg(event, "rank.no_data", event.plain_result(f"📊 还没有「{title}」的数据")):
                yield _r
            return

        rows.sort(key=lambda r: -r[0])
        me = str(user_id)
        my_rank = next((i for i, r in enumerate(rows, 1) if r[2] == me), None)

        lines = [f"🏆 本群排行榜 · {title}"]
        for i, (value, name, uid, entry) in enumerate(rows[:10], 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i:>2}.")
            mark = " ←你" if uid == me else ""
            extra = ""
            if field == "best":
                fish = FISH_BY_ID.get(entry.get("best_fish", ""))
                if fish:
                    extra = f"（{fish['name']}）"
            # 称号（v1.18.17）：排行榜是称号最主要的展示位之一
            title_label = self._title_label(entry)
            if title_label:
                extra += f"　🏷{title_label}"
            lines.append(f"{medal} {name[:12]}　{_fmt_gold(value)} {unit}{extra}{mark}")
        if my_rank is None:
            lines.append("　你还没有上榜，加油！")
        elif my_rank > 10:
            lines.append(f"　你的排名：第 {my_rank} 名")
        lines.append("💡 /钓鱼 排行 金币｜图鉴｜最贵")
        async for _r in self._say_msg(event, "rank.view", event.plain_result("\n".join(lines))):
            yield _r

    async def _cmd_lock(self, event: AstrMessageEvent, user_id: str, *rest):
        """锁定/解锁：/钓鱼 锁定 1 2 ｜ /钓鱼 解锁 1

        锁定的鱼不会被「/钓鱼 卖」或「/钓鱼 卖光光」卖掉，避免手滑。
        序号之后可能还有更多参数（形参只声明到 a3），所以额外从原始消息尾部补齐。
        """
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            inventory: list[dict[str, Any]] = player.get("inventory") or []
            ordered = sorted(inventory, key=_sort_key)

            # 序号统一交给 _parse_indices（支持 "1 2 3" / "1-5" / "全部"）
            indices = self._parse_indices(" ".join(self._tokens(*rest)), ordered)

            if not indices:
                async for _r in self._say_msg(event, "lock.usage", event.plain_result(
                        "📖 /钓鱼 锁定 <序号…>　锁定的鱼不会被卖出\n"
                        "　/钓鱼 解锁 <序号…>\n"
                        "　先用 /钓鱼 背包 看序号"
                    )):
                    yield _r
                return
            bad = [i for i in indices if i > len(ordered)]
            if bad:
                async for _r in self._say_msg(event, "lock.bad_index", event.plain_result(
                        f"🤔 序号 {'/'.join(map(str, bad))} 超出 1~{len(ordered)}"
                    )):
                    yield _r
                return
            touched = 0
            for idx in indices:
                instance = ordered[idx - 1]
                if not bool(instance.get("locked")):
                    instance["locked"] = True
                    touched += 1
            player["inventory"] = inventory
            saved = await self._save_player(player)

        lines = [f"🔒 锁定了 {touched} 条鱼（已锁的跳过）"]
        lines.append("　这些鱼不会被 /钓鱼 卖 或 /钓鱼 卖光光 卖掉")
        if not saved:
            lines.append("⚠️ 保存失败")
        async for _r in self._say_msg(event, "lock.done", event.plain_result("\n".join(lines))):
            yield _r

    async def _cmd_unlock(self, event: AstrMessageEvent, user_id: str, *rest):
        """解锁：/钓鱼 解锁 1 2"""
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            inventory: list[dict[str, Any]] = player.get("inventory") or []
            ordered = sorted(inventory, key=_sort_key)

            parts: list[str] = []
            indices = self._parse_indices(" ".join(self._tokens(*rest)), ordered)

            if not indices:
                # 没给序号就全部解锁
                touched = 0
                for instance in inventory:
                    if bool(instance.get("locked")):
                        instance["locked"] = False
                        touched += 1
                player["inventory"] = inventory
                saved = await self._save_player(player)
                lines = [f"🔓 已解锁全部 {touched} 条鱼"]
                if not saved:
                    lines.append("⚠️ 保存失败")
                async for _r in self._say_msg(event, "lock.all_done", event.plain_result("\n".join(lines))):
                    yield _r
                return

            bad = [i for i in indices if i > len(ordered)]
            if bad:
                async for _r in self._say_msg(event, "lock.bad_index", event.plain_result(
                        f"🤔 序号 {'/'.join(map(str, bad))} 超出 1~{len(ordered)}"
                    )):
                    yield _r
                return
            touched = 0
            for idx in indices:
                instance = ordered[idx - 1]
                if bool(instance.get("locked")):
                    instance["locked"] = False
                    touched += 1
            player["inventory"] = inventory
            saved = await self._save_player(player)
        lines = [f"🔓 解锁了 {touched} 条鱼"]
        if not saved:
            lines.append("⚠️ 保存失败")
        async for _r in self._say_msg(event, "unlock.done", event.plain_result("\n".join(lines))):
            yield _r


    async def _cmd_collectibles(self, event: AstrMessageEvent, user_id: str):
        player = await self._load_player(user_id)
        coll: dict[str, int] = player.get("collectibles") or {}
        notes: list[str] = player.get("bottle_notes") or []
        got = len([c for c in coll if _safe_int(coll.get(c), 0, 0) > 0])

        lines = [f"🎁 杂物收集 {got}/{len(COLLECTIBLES)}"]
        # 已收集的按价值从高到低排，未收集的（❔）统一放最后
        ordered = sorted(
            COLLECTIBLES,
            key=lambda item: (
                _safe_int(coll.get(item["id"]), 0, 0) <= 0,
                -_safe_int(item.get("value"), 0, 0),
            ),
        )
        for item in ordered:
            count = _safe_int(coll.get(item["id"]), 0, 0)
            if count:
                have = _safe_int((player.get("items") or {}).get(item["id"]), 0, 0)
                lines.append(
                    f"　{item['emoji']}{item['name']} 累计×{count}"
                    + (f" 现有{have}" if have else "")
                )
            else:
                lines.append("　❔ ???")
        lines.append(f"📜 纸条 {len(notes)}/{len(BOTTLE_NOTES)} 张")
        if notes:
            lines.append(f"　最近：{notes[-1]}")
        async for _r in self._say_msg(
            event, "collectibles.view", event.plain_result("\n".join(lines))
        ):
            yield _r

    # =========================================================================
    # 指令入口
    # =========================================================================


    # =========================================================================
    # 背包 / 卖鱼 / 图鉴
    # =========================================================================

    async def _cmd_bag(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """背包。默认列出前 20 条；`/钓鱼 背包 2` 翻页。"""
        player = await self._load_player(user_id)
        inventory: list[dict[str, Any]] = player.get("inventory") or []
        cap = _backpack_capacity(player, self.cfg)
        if not inventory:
            async for _r in self._say_msg(event, "bag.empty", event.plain_result(
                    f"🎒 背包空空的（容量 {cap}）\n💡 发 /钓鱼 下竿试试手气"
                )):
                yield _r
            return

        ordered = sorted(inventory, key=_sort_key)
        value = _inventory_value(inventory)
        # 列表按连钓战报的堆叠规则渲染（站长要求）：传说/神话/异色的逐条列出，
        # 其余同种鱼合并成一行 —— 背包里几十条小鱼不再刷屏。
        # ⚠️ 序号只对**单独列出**的那些有效（显示规则与卖鱼的序号一致）；
        #    堆叠行写的是「这种鱼有几条、值多少」，要按序号卖就翻页找单独那条，
        #    或者直接用 `/钓鱼 卖 <鱼名>`（推荐，一次清掉同一批）。
        row_entries = [
            {
                "index": position,
                "instance": instance,
                "alias": f"{position:>2}.{mark}{_instance_line(instance)}"
                         f"　{_attrs_line(instance)}",
                "locked": bool(instance.get("locked")),
                "mark": bool(instance.get("locked")),
            }
            for position, (mark, instance) in enumerate(
                (("🔒" if item.get("locked") else "　", item) for item in ordered),
                start=1,
            )
        ]
        rendered = self._stack_rows(row_entries)
        per_page = 20
        page = max(1, _to_int(a2, 1))
        total_pages = max(1, (len(rendered) + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page

        lines = [
            f"🎒 背包 {len(inventory)}/{cap} 条 · 总估值 {_fmt_gold(value)} 金币"
            + (f" · 第 {page}/{total_pages} 页" if total_pages > 1 else "")
        ]
        lines.extend(rendered[start : start + per_page])
        lines.append("【用法】")
        lines.append("　/钓鱼 卖 1 2 3　按序号卖（可给多个）")
        lines.append("　/钓鱼 卖 鲤鱼　　按鱼名卖光这种鱼（堆叠的批次这样卖最快）")
        lines.append("　/钓鱼 卖光光　　一次清空背包")
        lines.append("　/钓鱼 锁定 1　　 锁定后不会被卖出")
        lines.append("　/钓鱼 水族馆 放 1 2　放进水族馆")
        if total_pages > 1:
            lines.append(f"💡 /钓鱼 背包 {page % total_pages + 1} 看下一页")
        # 「上一页 / 下一页」按钮（首尾页各缺一个，只有一页时整排不出现）
        async for reply in self._say(
            event, "\n".join(lines), "bag.list",
            page=(page, total_pages, "/钓鱼 背包"),
        ):
            yield reply

    async def _cmd_sell(self, event: AstrMessageEvent, user_id: str, *rest):
        """卖鱼。支持全部形式：

        - ``/钓鱼 卖``              全部卖出（低价优先）
        - ``/钓鱼 卖 1 2 3``        按序号卖（任意条数）
        - ``/钓鱼 卖 1-5``          按区间卖
        - ``/钓鱼 卖 全部``          全部卖出
        - ``/钓鱼 卖光光``          清空背包（锁定的留着）
        - ``/钓鱼 卖 鲤鱼``          卖光某一种鱼
        - ``/钓鱼 卖 鲤鱼 3``        卖这种鱼的 3 条
        """
        tokens = self._tokens(*rest)
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            self._ensure_market(player)  # 确保今日行情已生成
            inventory: list[dict[str, Any]] = player.get("inventory") or []
            if not inventory:
                async for _r in self._say_msg(event, "sell.empty", event.plain_result("🎒 背包空空的，没东西可卖")):
                    yield _r
                return

            ordered = sorted(inventory, key=_sort_key)
            discount = float(self.cfg["sell_discount"])

            def price_of(instance: dict[str, Any]) -> tuple[int, int]:
                """返回 (最终售价, 行情加成部分)。"""
                base = max(1, int(_instance_value(instance) * discount))
                mult = self._market_mult(player, instance.get("fish_id", ""))
                final = max(1, int(base * mult))
                return final, max(0, final - base)

            # ---- 「卖 垃圾」已经取消：说清楚现在该用什么 ----
            if tokens and tokens[0] in (
                "__junk_removed__", "垃圾", "小鱼", "杂鱼", "junk", "trash",
            ):
                async for _r in self._say_msg(event, "sell.removed", event.plain_result(
                        "🧹 「卖 垃圾」这个玩法已经去掉了\n"
                        "　想一次清空背包：/钓鱼 卖光光（锁定的鱼会留下）\n"
                        "　想只卖某种鱼：/钓鱼 卖 鲤鱼（可加数量）\n"
                        "　想按序号卖：/钓鱼 卖 1 2 3"
                    )):
                    yield _r
                return

            # ---- 解析目标 ----
            targets: list[dict[str, Any]] = []
            sell_all = (
                not tokens
                or tokens[0] in ("全部", "所有", "all", "全", "光光", "卖光", "清空")
            )
            skipped_locked = 0
            if sell_all:
                # 「卖光光」= 把背包清空：锁定的鱼留着（那是玩家特意保护的）
                unlocked = [x for x in inventory if not bool(x.get("locked"))]
                skipped_locked = len(inventory) - len(unlocked)
                targets = sorted(unlocked, key=_instance_value)
            elif all(self._is_index_token(t) or "-" in t or "~" in t for t in tokens):
                indices = self._parse_indices(" ".join(tokens), ordered)
                if not indices:
                    async for _r in self._say_msg(event, "sell.bad_index", event.plain_result(
                            f"🤔 序号要在 1~{len(ordered)} 之间（发 /钓鱼 背包 看序号）"
                        )):
                        yield _r
                    return
                targets = [ordered[i - 1] for i in indices]
            else:
                # 按鱼名：第一个 token 是名字，后面可选数量
                first, glued_count = tokens[0], ""
                # 少打空格的容错：`卖 鲤鱼3` 等价于 `卖 鲤鱼 3`
                if len(tokens) == 1:
                    split_nc = self._split_name_count(first)
                    if split_nc and self._find_fish_by_name(split_nc[0]):
                        first, glued_count = split_nc
                fish = self._find_fish_by_name(first)
                if fish is None:
                    sample = "、".join(f["name"] for f in FISH_POOL[:6])
                    async for _r in self._say_msg(event, "sell.no_fish", event.plain_result(
                            f"🤔 没有叫「{first}」的鱼\n"
                            f"　可卖示例：{sample} …\n"
                            f"　也可以按序号卖：/钓鱼 卖 1 2 3"
                        )):
                        yield _r
                    return
                count = (
                    _to_int(tokens[1], 0)
                    if len(tokens) > 1
                    else _to_int(glued_count, 0)
                )
                group = sorted(
                    [x for x in inventory if x.get("fish_id") == fish["id"]],
                    key=_instance_value,
                )
                if not group:
                    async for _r in self._say_msg(event, "sell.missing", event.plain_result(f"🤔 你还没有 {fish['name']}")):
                        yield _r
                    return
                targets = group if count <= 0 else group[: min(count, len(group))]

            if not targets:
                if sell_all and skipped_locked:
                    async for _r in self._say_msg(event, "sell.all_locked", event.plain_result(
                            f"🔒 背包里 {skipped_locked} 条鱼都锁着，卖光光不会动它们\n"
                            f"　想一起卖：/钓鱼 解锁 1 2 3 之后再 /钓鱼 卖光光"
                        )):
                        yield _r
                    return
                async for _r in self._say_msg(event, "sell.nothing", event.plain_result(
                        "🤔 没有可卖的鱼\n"
                        "　可能原因：背包是空的、序号超范围、或这种鱼你还没有\n"
                        "　发 /钓鱼 背包 看背包，或用 /钓鱼 卖光光 一次卖光"
                    )):
                    yield _r
                return

            sold = [(x, price_of(x)[0]) for x in targets]
            bonus = sum(price_of(x)[1] for x in targets)
            player["inventory"] = [
                x for x in inventory if id(x) not in {id(t) for t in targets}
            ]
            async for out in self._finalize_sale(event, player, sold, "💵 卖出", bonus):
                yield out
            if skipped_locked:
                async for _r in self._say_msg(event, "sell.locked_note", event.plain_result(
                        f"🔒 另有 {skipped_locked} 条锁定的鱼留在背包里"
                        f"（/钓鱼 解锁 1 可以解锁）"
                    )):
                    yield _r

    async def _cmd_fish_info(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """/钓鱼 查 <名字>：**凡是游戏里看得到的东西都能查**。

        - 鱼 → 品质、基准价、要不用拉线、出没钓点、自己的记录
        - 钓点 → 这个钓点的鱼种清单（按品质分组）
        - 鱼饵 / 鱼竿 / 道具 / 杂物 / 变异 / 天气 / 成就 → 各自的说明卡

        匹配规则（v1.18.16 定的，避免「查 锦鲤玉佩」被鱼「锦鲤」抢走）：
        1) **先找完全同名的**：命中一个就出卡片，命中多个就列出来让你挑；
        2) 没有再退到「名字里含这几个字」的模糊匹配，规则同上。

        ⚠️ 只查玩家在游戏里看得见的东西；插件配置键、回复场景、内部函数这些
        开发者才关心的内容不进游戏（站长明确要求：游戏文本服务于游戏）。
        """
        name = (a2 or "").strip()
        if not name:
            async for _r in self._say_msg(event, "fishinfo.index", event.plain_result(
                    "📖 /钓鱼 查 <名字>\n"
                    "　鱼、钓点、鱼饵、鱼竿、道具、杂物、变异、天气、成就 都能查，\n"
                    "　例如 /钓鱼 查 鲲　/钓鱼 查 蚯蚓　/钓鱼 查 龙宫　/钓鱼 查 月夜\n"
                    "　名字记不清就先用 /钓鱼 图鉴 与 /钓鱼 钓点 翻一翻"
                )):
                yield _r
            return

        player = await self._load_player(user_id)
        collection = player.get("collection") or {}
        held = _held_counts(player)

        def mine(fish_id: str) -> tuple[int, int, int]:
            entry = collection.get(fish_id)
            total = _safe_int(entry.get("count"), 0, 0) if isinstance(entry, dict) else 0
            best = _safe_int(entry.get("best_value"), 0, 0) if isinstance(entry, dict) else 0
            return total, best, held.get(fish_id, 0)

        # ---- 各类详情卡（都是「几行短句」，方便统一走详情/多命中两条出口）----
        # 站长要求「每一条回复的文本量都控制好」：钓点卡最多 14 行（含结尾那两行
        # 「…还有 N 种」和「/钓鱼 查 <鱼名>」），超了就截断并指路
        # （龙宫 15 种鱼全列是 20+ 行，刷屏而且没人一次看完）。
        CARD_MAX_LINES = 14

        def loc_card(loc_cfg: dict[str, Any]) -> list[str]:
            ids = [
                fid
                for fid, w in (LOCATION_WEIGHTS.get(loc_cfg["id"]) or {}).items()
                if w > 0 and fid in FISH_BY_ID
            ]
            # 空竿率（v1.18.24 加上）：上鱼率 = 鱼饵上钩率 × 钓点系数；
            # 系数 ≥ 1.0 的钓点（前 3 张图）是**必出鱼**，所以那里写 0%。
            # 以前游戏里完全看不到这个数，站长只能靠手感 —— 「感觉没变」有一半是没处可看。
            _eq = player.get("equipped_bait")
            _eq = _eq if isinstance(_eq, str) and _eq in self.baits else "none"
            _factor = self._location_hook_factor(loc_cfg["id"])
            _factor = 1.0 if _factor is None else _factor
            _empty = (
                0.0
                if _factor >= 1.0
                else max(0.0, 1.0 - min(1.0, self._hook_rate(_eq) * _factor))
            )
            # 跑鱼率（v1.18.26 加上）：拉线窗口的逃脱率**不是按钓点写的**，而是按稀有度
            # （rarity_escape_chance × 难度系数 × 天气 × 鱼竿），所以这里按这个钓点真实的
            # 传说/神话鱼池加权算一个「良好评价」下的期望值 —— 站长问「各钓点的逃脱率」时
            # 游戏里至少有个能看的数（以前只能靠手感）。
            _esc: dict[str, list[float]] = {}
            for _fid, _w in (LOCATION_WEIGHTS.get(loc_cfg["id"]) or {}).items():
                _fish = FISH_BY_ID.get(_fid)
                if not _fish or _w <= 0 or _fish["rarity"] not in ("传说", "神话"):
                    continue
                _spec = self._interaction_window(_fish, None)
                if _spec is None:
                    continue
                _bucket = _esc.setdefault(_fish["rarity"], [0.0, 0.0])
                _bucket[0] += _w * _safe_number(_spec.get("escape"), 0.0)
                _bucket[1] += _w
            out = [
                f"{loc_cfg['emoji']} {loc_cfg['name']}　共 {len(ids)} 种"
                f"　价值×{loc_cfg['value_mult']:.2f}"
                f"　需{loc_cfg['level_gate']}级"
                + (f"/{_fmt_gold(loc_cfg['gold_gate'])}金" if loc_cfg["gold_gate"] else "")
                + f"　空竿{_empty:.0%}（{self._bait_label(_eq)}）"
            ]
            _esc_parts = [
                f"{name} {bucket[0] / bucket[1]:.0%}"
                for name, bucket in _esc.items()
                if bucket[1] > 0
            ]
            if _esc_parts:
                out.append(
                    "　拉线跑鱼率（评价「良好」）：" + "　".join(_esc_parts)
                    + "　（完美 ×0.3、偏差 ×1.6、超时必跑）"
                )
            hidden = 0
            # 结尾固定还有「…还有 N 种」+「/钓鱼 查 <鱼名>」两行，先扣掉
            body_cap = CARD_MAX_LINES - 2
            for rarity in RARITY_ORDER:
                group = sorted(
                    (FISH_BY_ID[fid] for fid in ids if _fish_rarity(fid) == rarity),
                    key=lambda f: f["value"],
                )
                if not group:
                    continue
                if len(out) + 1 >= body_cap:
                    hidden += len(group)
                    continue
                out.append(f"【{self._rarity_name(rarity)}】")
                for one in group:
                    if len(out) >= body_cap:
                        hidden += 1
                        continue
                    total, _best, now = mine(one["id"])
                    mark = "✅" if total > 0 else "❔"
                    out.append(
                        f"　{mark}{one['name']}　{_fmt_gold(one['value'])}金"
                        + (f"　存{now}" if now else "")
                    )
            if hidden:
                out.append(f"　…还有 {hidden} 种，发 /钓鱼 图鉴 {loc_cfg['name']} 看全部")
            out.append("💡 /钓鱼 查 <鱼名> 看它在哪些钓点出现")
            return out

        def fish_card(one: dict[str, Any]) -> list[str]:
            home_list = [
                loc_cfg
                for loc_cfg in self.locations
                if (LOCATION_WEIGHTS.get(loc_cfg["id"]) or {}).get(one["id"], 0) > 0
            ]
            out = [
                f"{_fish_emoji(one)} {one['name']}　{self._rarity_name(one['rarity'])}"
                f"　基准价 {_fmt_gold(one['value'])} 金币",
            ]
            # ⚠️ v1.18.17：「狠角色」那套相处机制已按站长的要求整体删掉
            # （没意思，而且容易把玩家养了很久的鱼吃掉造成巨大损失），鱼卡不再有那行标注。
            out.append(
                f"　上钩难易："
                f"{'要拉线（会跑，手要快）' if one['rarity'] in self.interactive_rarities else '直接上钩，不用拉线'}"
            )
            if one.get("flavor"):
                out.append(f"　{one['flavor']}")
            if home_list:
                out.append("　出没钓点：" + "、".join(
                    f"{h['emoji']}{h['name']}(×{h['value_mult']:.2f})" for h in home_list
                ))
            else:
                out.append("　出没钓点：暂时没人见到过（隐藏鱼？）")
            total, best, now = mine(one["id"])
            if total > 0:
                out.append(
                    f"　我的记录：共 {total} 条　最高卖过 {_fmt_gold(best)}"
                    + (f"　背包里还有 {now} 条" if now else "")
                )
            else:
                out.append("　我的记录：还没钓到过 ❔")
            out.append("💡 /钓鱼 查 <钓点名> 看那个钓点的全部鱼种")
            return out

        loc = self._find_location(name)
        fish = self._find_fish_by_name(name)
        if loc is not None:
            ids = [
                fid
                for fid, w in (LOCATION_WEIGHTS.get(loc["id"]) or {}).items()
                if w > 0 and fid in FISH_BY_ID
            ]
            if not ids:
                async for _r in self._say_msg(event, "fishinfo.empty", event.plain_result(
                        f"🐟 {loc['name']} 没有配置鱼种"
                )):
                    yield _r
                return

        # 精确同名优先：查「锦鲤玉佩」不该被鱼「锦鲤」抢走（模糊匹配留作兜底）。
        # 每条卡片带上自己该走的**场景键**：钓点仍然走 fishinfo.location_detail
        # （站长以前给这个场景配过按钮/文案，不能因为改查法就丢），其余走 fishinfo.detail。
        exact: list[tuple[str, list[str], str]] = []
        if fish is not None and str(fish["name"]) == name:
            exact.append((f"🐟 鱼「{fish['name']}」", fish_card(fish), "fishinfo.detail"))
        if loc is not None and (
            str(loc["name"]) == name or str(loc["id"]).lower() == name.lower()
        ):
            exact.append((
                f"{loc['emoji']} 钓点「{loc['name']}」", loc_card(loc),
                "fishinfo.location_detail",
            ))
        exact.extend(
            (label, lines, "fishinfo.detail")
            for label, lines in self._lookup_anything(player, name, exact=True)
        )

        fuzzy: list[tuple[str, list[str], str]] = []
        if fish is not None:
            fuzzy.append((f"🐟 鱼「{fish['name']}」", fish_card(fish), "fishinfo.detail"))
        if loc is not None:
            fuzzy.append((
                f"{loc['emoji']} 钓点「{loc['name']}」", loc_card(loc),
                "fishinfo.location_detail",
            ))
        fuzzy.extend(
            (label, lines, "fishinfo.detail")
            for label, lines in self._lookup_anything(player, name)
        )

        cards = exact or fuzzy

        # ---- 出口一：什么都没查到 ----
        if not cards:
            async for _r in self._say_msg(event, "fishinfo.not_found", event.plain_result(
                    f"🤔 没找到「{name}」\n"
                    "　能查：鱼 / 钓点 / 鱼饵 / 鱼竿 / 道具 / 杂物 / 变异 / 天气 / 成就\n"
                    "　也可以 /钓鱼 查 看用法、/钓鱼 图鉴 详 看鱼名单"
            )):
                yield _r
            return

        # ---- 出口二：只命中一个 → 直接出详情卡（卡片自己会限长，最多 20 行）----
        if len(cards) == 1:
            async for _r in self._say_msg(event, cards[0][2], event.plain_result(
                    "\n".join(cards[0][1][:20])
            )):
                yield _r
            return

        # ---- 出口三：一个词命中好几样 → 列出来让他挑（不带正文，省版面）----
        lines = [f"🔍 「{name}」能查到 {len(cards)} 处："]
        for label, detail, _scene in cards[:8]:
            lines.append(f"　{label}　{detail[0]}")
        if len(cards) > 8:
            lines.append(f"　…还有 {len(cards) - 8} 处")
        lines.append("　写全一点就能直接看详细（例如 /钓鱼 查 高级饲料）")
        async for _r in self._say_msg(event, "fishinfo.multi_match", event.plain_result("\n".join(lines))):
            yield _r

    async def _cmd_collection(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """鱼种图鉴（203 种鱼，所以按钓点分区 + 可翻页）。

        - ``/钓鱼 图鉴``            各钓点的收集进度（一行一个钓点）
        - ``/钓鱼 图鉴 湖泊``        看某个钓点里还差哪些鱼
        - ``/钓鱼 图鉴 详 [页码]``   按品质排序的完整清单，每页 15 种
        """
        player = await self._load_player(user_id)
        collection: dict[str, dict[str, Any]] = player.get("collection") or {}
        held = _held_counts(player)
        owned = {
            fid
            for fid, e in collection.items()
            if isinstance(e, dict) and _safe_int(e.get("count"), 0, 0) > 0
        }
        arg = (a2 or "").strip()
        toks = self._tokens(a2)
        lower = toks[0].lower() if toks else ""

        def entry_of(fish_id: str) -> tuple[int, int]:
            entry = collection.get(fish_id)
            if not isinstance(entry, dict):
                return 0, 0
            return _safe_int(entry.get("count"), 0, 0), _safe_int(
                entry.get("best_value"), 0, 0
            )

        # ---- 详 [页码]：已收集的清单（按品质从低到高、每页 15 种）----
        if lower in ("详", "详细", "all", "detail") or (len(toks) == 1 and arg.isdigit()):
            page = (
                _to_int(toks[1], 1)
                if lower in ("详", "详细", "all", "detail") and len(toks) > 1
                else (_to_int(arg, 1) if arg.isdigit() else 1)
            )
            # 未收集的不再逐条列 ❔ 占位，只列已经钓到的；还差多少在末尾一句带过
            collected = [
                fish
                for fish in sorted(
                    FISH_POOL,
                    key=lambda f: (RARITY_RANK.get(f["rarity"], 0), f["value"]),
                )
                if entry_of(fish["id"])[0] > 0
            ]
            per_page = 15
            total_pages = max(1, (len(collected) + per_page - 1) // per_page)
            page = max(1, min(page, total_pages))
            start = (page - 1) * per_page
            lines = [
                f"📖 鱼种图鉴（详）{len(owned)}/{len(FISH_POOL)}"
                f"　第 {page}/{total_pages} 页"
            ]
            if not collected:
                lines.append("　（还没钓到鱼，先去 /钓鱼 下竿）")
            for fish in collected[start : start + per_page]:
                total, best = entry_of(fish["id"])
                now = held.get(fish["id"], 0)
                lines.append(
                    f"　{_fish_emoji(fish)}{fish['name']}"
                    f"　{self._rarity_name(fish['rarity'])}"
                    f"　共{total} 最高{_fmt_gold(best)}"
                    + (f" 存{now}" if now else "")
                )
            lack = len(FISH_POOL) - len(collected)
            lines.append(
                f"📌 还差 {lack} 种（共 {len(FISH_POOL)} 种）" if lack
                else f"🏅 全部 {len(FISH_POOL)} 种都收集齐了！"
            )
            if total_pages > 1:
                tail_page = page % total_pages + 1
                lines.append(f"💡 /钓鱼 图鉴 详 {tail_page} 看下一页")
            async for _r in self._say_msg(
                event,
                "collection.detail",
                event.plain_result("\n".join(lines)),
                page=(page, total_pages, "/钓鱼 图鉴 详"),
            ):
                yield _r
            return

        # ---- 图鉴 <钓点名>：这个钓点里已收集的鱼 ----
        loc = self._find_location(arg) if arg and lower not in ("详", "详细") else None
        if loc is not None:
            ids = [
                fid
                for fid, w in (LOCATION_WEIGHTS.get(loc["id"]) or {}).items()
                if w > 0 and fid in FISH_BY_ID
            ]
            ids.sort(
                key=lambda fid: (
                    RARITY_RANK.get(_fish_rarity(fid), 0),
                    _safe_int(FISH_BY_ID[fid].get("value"), 0, 0),
                )
            )
            got = [fid for fid in ids if fid in owned]
            lines = [
                f"{loc['emoji']} {loc['name']} 图鉴 {len(got)}/{len(ids)}"
                f"　×{loc['value_mult']:.2f}"
            ]
            # 没收集到的不逐条列 ❔，只在末尾报一句还差几种（少刷屏、不剧透）
            for fid in ids:
                total, best = entry_of(fid)
                if total <= 0:
                    continue
                fish = FISH_BY_ID[fid]
                now = held.get(fid, 0)
                lines.append(
                    f"　{_fish_emoji(fish)}{fish['name']}"
                    f"　{self._rarity_name(fish['rarity'])}"
                    f"　共{total} 最高{_fmt_gold(best)}"
                    + (f" 存{now}" if now else "")
                )
            lack = len(ids) - len(got)
            lines.append(
                f"📌 还差 {lack} 种（共 {len(ids)} 种）" if lack
                else f"🏅 这个钓点已集齐（共 {len(ids)} 种）"
            )
            lines.append("💡 /钓鱼 图鉴 看各钓点总进度")
            async for _r in self._say_msg(event, "collection.location", event.plain_result("\n".join(lines))):
                yield _r
            return

        # ---- 主视图：一行一个钓点（✅ = 已够八成，可以前往下一个钓点）----
        lines = [f"📖 鱼种图鉴 {len(owned)}/{len(FISH_POOL)}"]
        ratio = _clamp(
            _safe_number(self.cfg.get("location_codex_gate"), 0.8), 0.0, 1.0
        )
        gate_ok = 0
        fully_done = 0
        for loc_cfg in self.locations:
            ids = self._location_species(loc_cfg["id"])
            if not ids:
                continue
            got = [fid for fid in ids if fid in owned]
            need_gate = int(len(ids) * ratio + 0.999)
            if len(got) >= need_gate:
                gate_ok += 1
            if len(got) >= len(ids):
                fully_done += 1
            mark = "✅" if len(got) >= need_gate else "　"
            lines.append(
                f"{mark}{loc_cfg['emoji']}{loc_cfg['name']} {len(got)}/{len(ids)}"
            )
        missing = len(FISH_POOL) - len(owned)
        lines.append(
            f"🏅 {len(self.locations)} 个钓点全部集齐！" if fully_done == len(self.locations)
            else f"📌 已开八成 {gate_ok}/{len(self.locations)} 个钓点"
                 f"　全图鉴还差 {missing} 种"
        )
        done = self._codex_completed_rarities(player)
        if done:
            lines.append(
                "📕 已集齐品质：" + "、".join(self._rarity_name(r) for r in done)
            )
        lines.extend(self._best_records_text(player))
        lines.append("💡 /钓鱼 图鉴 <钓点名> 看还差哪些　/钓鱼 图鉴 详 看完整清单")
        async for _r in self._say_msg(event, "collection.view", event.plain_result("\n".join(lines))):
            yield _r

    # =========================================================================
    # 水族馆
    # =========================================================================

    async def _cmd_aquarium(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str = ""
    ):
        """水族馆：放/取/卖/领/扩建/投喂。

        ``a3`` 是「子命令之后全部剩余参数」拼成的字符串，
        因此 ``放 1``、``放 1 3 5``、``取 1-3``、``取 全部`` 都能正常解析。
        """
        sub = (a2 or "").strip().lower()
        # 少打空格的容错：`放1` / `取1-3` / `卖全部` 都能拆开
        peeled_aq = self._peel_action(sub, AQUARIUM_ACTIONS)
        if peeled_aq:
            sub, glued = peeled_aq
            a3 = f"{glued} {a3}".strip()
        spec = self._tokens(a3)
        # 兜底：如果上层把子命令一并塞进了 a3，剥掉开头的重复子命令
        if spec and spec[0].lower() == sub and len(spec) > 1:
            spec = spec[1:]
        spec_text = " ".join(spec)

        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            aquarium: list[dict[str, Any]] = player.setdefault("aquarium", [])
            inventory: list[dict[str, Any]] = player.get("inventory") or []
            capacity = self._aquarium_capacity(player)

            # ---- 欣赏 ----
            if not sub:
                async for _r in self._say_msg(event, "aquarium.view", event.plain_result(self._aquarium_view(player))):
                    yield _r
                return

            # ---- 扩建 ----
            if sub in ("扩建", "升级", "expand"):
                unlocked = player.setdefault("aquarium_slots", [])
                nxt = next(
                    (s for s in self.aquarium_slots if s["name"] not in unlocked), None
                )
                if nxt is None:
                    async for _r in self._say_msg(event, "aquarium.max", event.plain_result("🏠 已经扩到最大了")):
                        yield _r
                    return
                price = int(nxt["price"])
                added = max(1, _safe_int(nxt.get("add"), 1, 1))
                if _safe_int(player.get("gold"), 0, 0) < price:
                    async for _r in self._say_msg(event, "aquarium.no_gold", event.plain_result(
                            f"💸 扩建「{nxt['name']}」（+{added} 个位）需要 "
                            f"{_fmt_gold(price)}，金币不足"
                            f"\n　现在还差 {_fmt_gold(price - _safe_int(player.get('gold'), 0, 0))}"
                        )):
                        yield _r
                    return
                player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                unlocked.append(nxt["name"])
                lines = [
                    f"🏠 扩建成功：{nxt['name']}"
                    f"（+{added} 个位）　容量 → {self._aquarium_capacity(player)}",
                    f"💰 余额 {_fmt_gold(player['gold'])}",
                ]
                saved = await self._save_with_notices(player, lines)
                async for _r in self._say_msg(event, "aquarium.upgraded", event.plain_result("\n".join(lines))):
                    yield _r
                return

            # ---- 领取鱼塘挂机收益（按小时累积，与水族馆合并）----
            if sub in ("领", "领取", "收益", "income", "collect"):
                now_ts = int(time.time())
                last = _safe_int(player.get("pond_last_ts"), 0, 0)
                if last <= 0:
                    # 第一次：开始计时
                    player["pond_last_ts"] = now_ts
                    await self._save_player(player)
                    async for _r in self._say_msg(event, "aquarium.income_start", event.plain_result(
                            "🏞️ 鱼塘开始计产了！\n"
                            "　每小时产出馆藏估值的 "
                            f"{float(self.cfg['pond_income_per_hour']) * 100:.1f}%，"
                            f"最多累积 {int(self.cfg['pond_income_cap_hours'])} 小时\n"
                            "　过一阵子再来 /钓鱼 水族馆 领"
                        )):
                        yield _r
                    return

                result = _pond_income(player, self.cfg, now_ts)
                total_value = int(result["value"])
                if total_value <= 0:
                    async for _r in self._say_msg(event, "aquarium.income_empty", event.plain_result("🐠 水族馆是空的，鱼塘没有产出")):
                        yield _r
                    return

                income = int(result["income"])
                hours = float(result["hours"])
                if income <= 0:
                    if int(result["pending"]) > 0 and int(result["counted"]) <= 0:
                        # 鱼刚放进去：收益按「每条鱼在缸里的时间」算，所以它还没开始产出
                        text = (
                            "⏳ 刚放进去的鱼还没产出（收益按每条鱼在缸里的时间算）\n"
                            "　养一会儿再来 /钓鱼 水族馆 领"
                        )
                    else:
                        wait_min = max(1, int(60 - (now_ts - last) / 60.0))
                        text = f"⏳ 产出还不够，再等约 {wait_min} 分钟"
                    async for _r in self._say_msg(event, "aquarium.income_wait", event.plain_result(text)):
                        yield _r
                    return

                player["pond_last_ts"] = now_ts
                player["pond_claimed_ts"] = now_ts
                player["pond_best_income"] = max(
                    _safe_int(player.get("pond_best_income"), 0, 0), income
                )
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + income
                cap_coins = int(self.cfg["pond_income_cap_coins"])
                cap_note = "（已达单次上限）" if cap_coins > 0 and income >= cap_coins else ""
                lines = [
                    f"🏞️ 鱼塘产出 +{_fmt_gold(income)} 金币{cap_note}",
                    f"　按 {hours:.1f} 小时计产 · 馆藏估值 {_fmt_gold(total_value)}",
                ]
                if int(result["pending"]) > 0:
                    lines.append(
                        f"　（{int(result['pending'])} 条刚放进去，这次还没产出）"
                    )
                deco_bonus = _decoration_bonus(player, now=now_ts)
                if deco_bonus > 0:
                    lines.append(f"　🪸 装饰加成 +{deco_bonus:.0%}")
                lines.append(f"💰 余额 {_fmt_gold(player['gold'])}")
                saved = await self._save_with_notices(player, lines)
                async for _r in self._say_msg(event, "aquarium.income", event.plain_result("\n".join(lines))):
                    yield _r
                return

            # ---- 投喂（统一走 /钓鱼 用 <道具> <栏位号>）----
            if sub in ("喂", "投喂", "feed"):
                async for _r in self._say_msg(event, "aquarium.feed_usage", event.plain_result(
                        "📖 投喂请用：/钓鱼 用 <道具名> <水族馆栏位号>\n"
                        "　例：/钓鱼 用 高级饲料 1"
                    )):
                    yield _r
                return

            # ---- 放入（支持批量序号：放 1 2 3 / 放 1-3 / 放 全部）----
            if sub in ("放", "放入", "养", "add"):
                ordered = sorted(inventory, key=_sort_key)
                indices = self._parse_indices(spec_text, ordered)
                if not indices:
                    async for _r in self._say_msg(event, "aquarium.put_usage", event.plain_result(
                            "📖 /钓鱼 水族馆 放 <背包序号…>\n"
                            "　例：/钓鱼 水族馆 放 1　或　放 1 3 5　或　放 1-5\n"
                            f"　背包有 {len(inventory)} 条，水族馆 {len(aquarium)}/{capacity}\n"
                            "　先用 /钓鱼 背包 看序号"
                        )):
                        yield _r
                    return

                room = capacity - len(aquarium)
                if room <= 0:
                    async for _r in self._say_msg(event, "aquarium.full", event.plain_result(
                            f"🐠 水族馆已满（{len(aquarium)}/{capacity}）\n"
                            "　可 /钓鱼 水族馆 扩建 扩容，或先 /钓鱼 水族馆 取/卖"
                        )):
                        yield _r
                    return

                # 先取出要放的鱼（按序号降序 pop，避免索引错位）
                picked: list[dict[str, Any]] = []
                for idx in sorted(indices, reverse=True):
                    picked.append(ordered[idx - 1])
                picked.reverse()

                accepted = picked[:room]
                skipped = len(picked) - len(accepted)
                now_put = int(time.time())
                for instance in accepted:
                    inventory.remove(instance)
                    instance["source"] = "aquarium"
                    # 开始计时：展出加成要「在缸里待够时间」才给（v1.16.0）
                    instance["tank_since"] = now_put
                    aquarium.append(instance)
                player["inventory"] = inventory
                self._sort_aquarium(aquarium)
                player["aquarium"] = aquarium
                saved = await self._save_player(player)

                lines = [f"🐠 放入 {len(accepted)} 条鱼"]
                for instance in accepted[:5]:
                    lines.append(f"　{_instance_line(instance)}")
                if len(accepted) > 5:
                    lines.append(f"　… 其余 {len(accepted) - 5} 条已放入")
                lines.append(f"　水族馆 {len(aquarium)}/{capacity}")
                if skipped:
                    lines.append(
                        f"　⚠️ 容量不足，{skipped} 条没放进去（先扩建或取出一些）"
                    )
                if not saved:
                    lines.append("⚠️ 保存失败")
                async for _r in self._say_msg(event, "aquarium.put_done", event.plain_result("\n".join(lines))):
                    yield _r
                return

            # ---- 取出（支持批量序号）----
            if sub in ("取", "取出", "拿", "take"):
                # 先按展示顺序排好：界面上第几条 = 这里取第几条（见 _calc._sort_tank）
                self._sort_aquarium(aquarium)
                indices = self._parse_indices(spec_text, aquarium)
                if not indices:
                    async for _r in self._say_msg(event, "aquarium.take_usage", event.plain_result(
                            f"📖 /钓鱼 水族馆 取 <栏位号…>\n"
                            f"　例：/钓鱼 水族馆 取 1　或　取 1 3 5\n"
                            f"　当前水族馆有 {len(aquarium)} 条（发 /钓鱼 水族馆 看栏位）"
                        )):
                        yield _r
                    return
                got: list[dict[str, Any]] = []
                for idx in sorted(indices, reverse=True):
                    if not (1 <= idx <= len(aquarium)):
                        continue
                    instance = aquarium.pop(idx - 1)
                    # 取出 = 停止计产：清掉入缸时间戳（收益按在缸时长算，见 _pond_income）
                    instance["tank_since"] = 0
                    instance["source"] = "fishing"
                    inventory.append(instance)
                    got.append(instance)
                player["inventory"] = inventory
                saved = await self._save_player(player)
                lines = [f"🎣 取出 {len(got)} 条鱼"]
                for instance in got[:5]:
                    lines.append(f"　{_instance_line(instance)}")
                if len(got) > 5:
                    lines.append(f"　… 其余 {len(got) - 5} 条已放入背包")
                lines.append(f"🧺 背包 {len(inventory)}/{_backpack_capacity(player, self.cfg)}")
                if not saved:
                    lines.append("⚠️ 保存失败")
                async for _r in self._say_msg(event, "aquarium.take_done", event.plain_result("\n".join(lines))):
                    yield _r
                return

            # ---- 卖掉馆藏（支持批量序号）----
            if sub in ("卖", "卖出", "sell"):
                # 同上：序号以界面上的展示顺序为准
                self._sort_aquarium(aquarium)
                indices = self._parse_indices(spec_text, aquarium)
                if not indices:
                    async for _r in self._say_msg(event, "aquarium.sell_usage", event.plain_result(
                            "📖 /钓鱼 水族馆 卖 <栏位号…>\n"
                            "　例：/钓鱼 水族馆 卖 1　或　卖 1 3 5"
                        )):
                        yield _r
                    return
                discount = float(self.cfg["sell_discount"])
                income = 0
                sold: list[tuple[dict[str, Any], int]] = []
                for idx in sorted(indices, reverse=True):
                    if not (1 <= idx <= len(aquarium)):
                        continue
                    instance = aquarium.pop(idx - 1)
                    instance["tank_since"] = 0        # 卖掉了 = 不再计产
                    price = max(1, int(_instance_value(instance) * discount))
                    income += price
                    sold.append((instance, price))
                if not sold:
                    async for _r in self._say_msg(event, "aquarium.bad_slot", event.plain_result(f"🤔 没有有效的栏位号（1~{len(aquarium)}）")):
                        yield _r
                    return
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + income
                player["total_sold"] = _safe_int(player.get("total_sold"), 0, 0) + len(
                    sold
                )
                await self._touch_leaderboard(player)
                lines = [f"💵 卖出馆藏 {len(sold)} 条 → {_fmt_gold(income)} 金币"]
                for instance, price in sold[:5]:
                    lines.append(
                        f"　{_instance_line(instance, with_value=False)} → {_fmt_gold(price)}"
                    )
                if len(sold) > 5:
                    lines.append(f"　… 其余 {len(sold) - 5} 条同上")
                lines.append(f"💰 余额 {_fmt_gold(player['gold'])}")
                saved = await self._save_with_notices(player, lines)
                async for _r in self._say_msg(event, "aquarium.sell_done", event.plain_result("\n".join(lines))):
                    yield _r
                return

            async for _r in self._say_msg(event, "aquarium.usage", event.plain_result(
                    "📖 水族馆用法\n"
                    "　/钓鱼 水族馆　　　　　　欣赏\n"
                    "　/钓鱼 水族馆 放 1　　　 从背包放入\n"
                    "　/钓鱼 水族馆 取 1　　　 取回\n"
                    "　/钓鱼 水族馆 卖 1　　　 直接卖\n"
                    "　/钓鱼 用 <道具> 1　　　投喂 / 洗髓 / 培育\n"
                    "　/钓鱼 水族馆 领　　　　 领取挂机收益\n"
                    "　/钓鱼 水族馆 扩建　　　 花金币扩容\n"
                    "　💤 养在缸里的鱼按「各自待了多久」产出金币，养得越久越多"
                )):
                yield _r

    # =========================================================================
    # 商店（v1.18.13 起拆成三家：鱼竿店 / 道具店 / 鱼饵店）
    # =========================================================================

    async def _cmd_buy_usage(self, event: AstrMessageEvent, user_id: str):
        """``/钓鱼 买`` 不带名字：把三家店的买法一次说清（别只丢一句「用法不对」）。"""
        async for _r in self._say_msg(event, "shop.usage", event.plain_result(
                "📖 /钓鱼 买 <名字> [数量]　会自动认出它在哪家店\n"
                "　三家店也可以直接进：\n"
                "　　/钓鱼 鱼竿　　鱼竿店\n"
                "　　/钓鱼 道具　　道具店（饲料 / 仙露 / 洗髓丹…）\n"
                "　　/钓鱼 鱼饵　　鱼饵店（面包屑 / 蚯蚓 / 红虫…）\n"
                "　例：/钓鱼 买 蚯蚓 20　｜　/钓鱼 道具 买 洗髓丹 1"
        )):
            yield _r

    async def _cmd_shop_moved(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """老的 `/钓鱼 商店`：货架拆成三家了，这里只给一句指路。"""
        todo = (a2 or "").strip()
        lines = [
            "🛒 商店拆成三家了，货架看着清爽些：",
            "　/钓鱼 鱼竿　　鱼竿店（价格 / 价值加成 / 手气 / 解锁等级）",
            "　/钓鱼 道具　　道具店（饲料 / 仙露 / 育灵水 / 洗髓丹 / 玉佩 / 造景）",
            "　/钓鱼 鱼饵　　鱼饵店（面包屑 / 蚯蚓 / 红虫 / 玉米粒…）",
        ]
        if todo:
            # 老玩家多半是接着写了「买 蚯蚓」或「扩容」，那就直接告诉他新写法
            if todo.startswith(("扩容", "扩建", "背包扩")):
                lines.append("　👉 背包扩容现在直接发：/钓鱼 扩建背包")
            else:
                name = todo[1:].strip() if todo[0] in "买购" else todo
                lines.append(
                    f"　👉 买「{name}」现在发：/钓鱼 鱼竿 买 {name}"
                    f"　或 /钓鱼 道具 买 {name}　或 /钓鱼 鱼饵 买 {name}"
                )
        lines.append("　（三家店都认「买 <名字> [数量]」，也可以直接 /钓鱼 买 <名字>）")
        async for _r in self._say_msg(event, "shop.moved", event.plain_result("\n".join(lines))):
            yield _r

    async def _do_buy_from_shop(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str, kind: str
    ):
        """三店共用的购买逻辑（调用方必须已经持有该玩家的锁）。

        ``kind``：``bait``（鱼饵店）/ ``item``（道具店）。
        别的店里没有的东西直接说清楚去哪家买，别只回一句「没这个货」。
        """
        player = await self._load_player(user_id)
        spec = self._tokens(a3)
        if not spec:
            usage = (
                "📖 /钓鱼 鱼饵 买 <名字> [数量]\n　例：/钓鱼 鱼饵 买 蚯蚓 20"
                if kind == "bait"
                else "📖 /钓鱼 道具 买 <名字> [数量]\n　例：/钓鱼 道具 买 高级饲料 5"
            )
            async for _r in self._say_msg(event, f"shop.{kind}_usage", event.plain_result(usage)):
                yield _r
            return
        name = spec[0]
        times = _to_int(spec[1], 1) if len(spec) > 1 else 1
        # 少打空格的容错：`买蚯蚓2` 等价于 `买 蚯蚓 2`
        if len(spec) == 1:
            split_nc = self._split_name_count(name)
            if split_nc and (self._find_bait(split_nc[0]) or self._find_item(split_nc[0])):
                name, times_text = split_nc
                times = _to_int(times_text, 1)
        times = max(1, min(times, 999))

        bait_id = self._find_bait(name)
        item_id = self._find_item(name)
        rod = self._find_rod(name)

        if bait_id == "none":
            async for _r in self._say_msg(event, "shop.free_hook", event.plain_result(
                    "🪝 空钩是免费的，不需要购买\n"
                    "　直接发 /钓鱼 或 /钓鱼 空钩 就能用它下竿"
            )):
                yield _r
            return
        if rod is not None and bait_id is None and item_id is None:
            async for _r in self._say_msg(event, "shop.wrong_shop_rod", event.plain_result(
                    f"🎣 「{rod['name']}」是鱼竿，要去鱼竿店买：\n"
                    f"　/钓鱼 鱼竿 买 {rod['name']}"
            )):
                yield _r
            return
        if kind == "bait" and bait_id is None and item_id is not None:
            async for _r in self._say_msg(event, "shop.wrong_shop_item", event.plain_result(
                    f"🎁 「{self.items[item_id]['name']}」是道具，要去道具店买：\n"
                    f"　/钓鱼 道具 买 {self.items[item_id]['name']}"
            )):
                yield _r
            return
        if kind == "item" and item_id is None and bait_id is not None:
            async for _r in self._say_msg(event, "shop.wrong_shop_bait", event.plain_result(
                    f"🪱 「{self.baits[bait_id]['name']}」是鱼饵，要去鱼饵店买：\n"
                    f"　/钓鱼 鱼饵 买 {self.baits[bait_id]['name']}"
            )):
                yield _r
            return
        if bait_id is None and item_id is None:
            # 只报「这一家已上架」的名字：没解锁的东西不剧透
            if kind == "bait":
                names = "、".join(
                    self.baits[b]["name"]
                    for b in self._bait_list()
                    if not self._unlock_shortage(player, self.baits[b])
                )
                other = "道具店（/钓鱼 道具）"
            else:
                names = "、".join(
                    i["name"]
                    for i in self.items.values()
                    if not self._unlock_shortage(player, i)
                )
                other = "鱼饵店（/钓鱼 鱼饵）"
            async for _r in self._say_msg(event, "shop.not_found", event.plain_result(
                    f"🤔 这家店里没有「{name}」\n　在售：{names}\n"
                    f"　鱼竿去 /钓鱼 鱼竿，别的道具去 {other}"
            )):
                yield _r
            return

        if kind == "bait" and bait_id is not None:
            bait = self.baits[bait_id]
            # 等级 / 需要鱼竿的购买门槛（只限制购买，已持有的不受影响）
            refuse = self._unlock_refuse_text(player, bait)
            if refuse:
                async for _r in self._say_msg(event, "shop.locked", event.plain_result(
                        f"{refuse}\n　升级靠多钓鱼；要鱼竿就去 /钓鱼 鱼竿 买"
                )):
                    yield _r
                return
            unit = max(0, int(bait.get("price", 0)))   # 单价：按个卖
            want = max(1, min(times, 9999))
            price = unit * want
            if _safe_int(player.get("gold"), 0, 0) < price:
                async for _r in self._say_msg(event, "shop.no_gold_bait", event.plain_result(
                        f"💸 金币不足：买 {want} 个需要 {_fmt_gold(price)}，"
                        f"你只有 {_fmt_gold(player.get('gold', 0))}"
                        f"（{_fmt_gold(unit)}/个）"
                )):
                    yield _r
                return
            amount = want
            player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
            baits = player.setdefault("baits", {})
            baits[bait_id] = _safe_int(baits.get(bait_id), 0, 0) + amount
            player["equipped_bait"] = bait_id
            saved = await self._save_player(player)
            lines = [
                f"🪱 购买 {self._bait_label(bait_id)} ×{amount}"
                f"（{_fmt_gold(unit)}/个）→ {_fmt_gold(price)} 金币",
                "　已装备为当前鱼饵",
                f"　持有 {player['baits'].get(bait_id, 0)} 个"
                f"　💰 余额 {_fmt_gold(player['gold'])}",
            ]
        else:
            item = self.items[item_id]
            price = int(item.get("price", 0)) * times
            if _safe_int(player.get("gold"), 0, 0) < price:
                async for _r in self._say_msg(event, "shop.no_gold_item", event.plain_result(
                        f"💸 金币不足：买 {times} 个需要 {_fmt_gold(price)}，"
                        f"你只有 {_fmt_gold(player.get('gold', 0))}"
                )):
                    yield _r
                return
            player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
            items = player.setdefault("items", {})
            items[item_id] = _safe_int(items.get(item_id), 0, 0) + times
            saved = await self._save_player(player)
            lines = [
                f"🎁 购买 {self._item_label(item_id)} ×{times}"
                f" → {_fmt_gold(price)} 金币",
                f"　{item['desc']}",
                f"　持有 {player['items'].get(item_id, 0)} 个"
                f"　💰 余额 {_fmt_gold(player['gold'])}",
            ]
        if not saved:
            lines.append("⚠️ 保存失败")
        async for _r in self._say_msg(event, "shop.bought", event.plain_result("\n".join(lines))):
            yield _r

    async def _cmd_bait_shop(self, event: AstrMessageEvent, user_id: str, a2: str, a3: str = ""):
        """鱼饵店：/钓鱼 鱼饵 ｜ /钓鱼 鱼饵 买 <名字> [数量]"""
        sub = (a2 or "").strip().lower()
        peeled = self._peel_action(sub, SHOP_ACTIONS)
        if peeled:
            sub, glued = peeled
            a3 = f"{glued} {a3}".strip()
        async with self._lock_for(user_id):
            if not sub:
                player = await self._load_player(user_id)
                async for _r in self._say_msg(event, "shop.bait_list", event.plain_result(self._bait_shop_view(player))):
                    yield _r
                return
            if sub in ("买", "购买", "buy"):
                async for result in self._do_buy_from_shop(event, user_id, a2, a3, "bait"):
                    yield result
                return
            player = await self._load_player(user_id)
            async for _r in self._say_msg(event, "shop.bait_usage", event.plain_result(
                    "📖 /钓鱼 鱼饵　　　　　　看鱼饵货架\n"
                    "　/钓鱼 鱼饵 买 <名字> [数量]\n"
                    "　例：/钓鱼 鱼饵 买 蚯蚓 20"
            )):
                yield _r

    async def _cmd_item_shop(self, event: AstrMessageEvent, user_id: str, a2: str, a3: str = ""):
        """道具店：/钓鱼 道具 ｜ /钓鱼 道具 买 <名字> [数量]"""
        sub = (a2 or "").strip().lower()
        peeled = self._peel_action(sub, SHOP_ACTIONS)
        if peeled:
            sub, glued = peeled
            a3 = f"{glued} {a3}".strip()
        async with self._lock_for(user_id):
            if not sub:
                player = await self._load_player(user_id)
                async for _r in self._say_msg(event, "shop.item_list", event.plain_result(self._item_shop_view(player))):
                    yield _r
                return
            if sub in ("买", "购买", "buy"):
                async for result in self._do_buy_from_shop(event, user_id, a2, a3, "item"):
                    yield result
                return
            player = await self._load_player(user_id)
            async for _r in self._say_msg(event, "shop.item_usage", event.plain_result(
                    "📖 /钓鱼 道具　　　　　　看道具货架\n"
                    "　/钓鱼 道具 买 <名字> [数量]\n"
                    "　例：/钓鱼 道具 买 高级饲料 5"
            )):
                yield _r


    async def _cmd_equip_bait(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str = ""
    ):
        """换饵：/钓鱼 换饵 [饵名|空钩]

        不带参数时列出背包里的鱼饵并标出当前用的是哪个；
        带参数时把「当前鱼饵」写进存档，之后裸发 `/钓鱼` 就一直用它。
        """
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            baits = player.get("baits") or {}
            current = player.get("equipped_bait", "none")
            name = (a2 or "").strip()

            def owned_of(bid: str) -> int:
                return _safe_int(baits.get(bid), 0, 0)

            if not name:
                lines = [f"🎣 当前鱼饵：{self._bait_label(current)}"]
                lines.append("　— 背包里的饵 —")
                any_bait = False
                for bid in self._bait_list():
                    count = owned_of(bid)
                    if count <= 0:
                        continue
                    any_bait = True
                    mark = "✅" if bid == current else "　"
                    lines.append(
                        f"{mark}{self._bait_label(bid)} ×{count}"
                        f"　{self.baits[bid].get('desc', '')}"
                    )
                if not any_bait:
                    lines.append("　（没有鱼饵，/钓鱼 鱼饵 买 蚯蚓）")
                lines.append("💡 /钓鱼 换饵 蚯蚓　或　/钓鱼 换饵 空钩（不消耗鱼饵）")
                async for _r in self._say_msg(event, "bait.equipped", event.plain_result("\n".join(lines))):
                    yield _r
                return

            target = self._find_bait(name)
            if target is None:
                names = "、".join(
                    [
                        self.baits[b]["name"]
                        for b in self._bait_list()
                        if not self._unlock_shortage(player, self.baits[b])
                    ]
                    + ["空钩"]
                )
                async for _r in self._say_msg(event, "bait.not_found", event.plain_result(
                        f"🤔 没有「{name}」这种饵。可以换：{names}"
                    )):
                    yield _r
                return

            if target != "none" and owned_of(target) <= 0:
                refuse = self._unlock_refuse_text(player, self.baits[target])
                if refuse:
                    async for _r in self._say_msg(event, "bait.locked", event.plain_result(f"{refuse}（买到之后就能换）")):
                        yield _r
                    return
                async for _r in self._say_msg(event, "bait.not_owned", event.plain_result(
                        f"🎒 你还没有 {self._bait_label(target)}，"
                        f"先去 /钓鱼 鱼饵 买 {self.baits[target]['name']}"
                    )):
                    yield _r
                return

            if target == current:
                async for _r in self._say_msg(event, "bait.same", event.plain_result(
                        f"🎣 当前用的就是 {self._bait_label(target)}"
                        + (f"（还剩 {owned_of(target)} 个）" if target != "none" else "")
                    )):
                    yield _r
                return

            player["equipped_bait"] = target
            new_ach = self._check_achievements(player)
            saved = await self._save_player(player)
            lines = [f"🎣 已换饵：{self._bait_label(current)} → {self._bait_label(target)}"]
            if target == "none":
                lines.append("　空钩：不花鱼饵，上钩的多是小鱼小虾")
            else:
                lines.append(
                    f"　还剩 {owned_of(target)} 个"
                    f"　{self.baits[target].get('desc', '')}"
                )
            async for _r in self._say_msg(event, "bait.empty", event.plain_result("\n".join(lines))):
                yield _r

    def _note_item_used(
        self,
        player: dict[str, Any],
        item_id: str,
        count: int = 1,
        bag: dict[str, Any] | None = None,
    ) -> int:
        """扣掉 ``count`` 个道具，并把它记进「今日用量」（v1.18.46）。

        为什么单独一个口子：``/钓鱼 用`` 的消耗点散在六七个效果分支里
        （扩展 / 手气 / 洗髓 / 姜汤 / 装饰 / 投喂……），以前每处都自己写
        ``items[item_id] = ... - 1``。加「每件道具的每日上限」时，如果逐处补代码
        就必然漏记某几个分支 —— 收口到这一个方法，新增分支只要照抄这一行。

        Returns:
            扣完之后**还剩几个**（可能为 0）。
        """
        bag = player.setdefault("items", {}) if bag is None else bag
        left = max(0, _safe_int(bag.get(item_id), 0, 0) - max(0, int(count)))
        bag[item_id] = left
        used = max(0, int(count))
        limit = _safe_int((self.items.get(item_id) or {}).get("daily_limit"), 0, 0)
        if used and limit > 0:
            _daily_add(player, f"item_{item_id}", used)
        return left

    async def _cmd_use_item(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str
    ):
        """使用道具：/钓鱼 用 <道具名> [水族馆栏位序列]

        - 饲料类：需要指定水族馆栏位号，提升那条鱼的三维。
          栏位支持批量：`用 高级饲料 1 2 3` / `用 高级饲料 1-5` / `用 高级饲料 全部`，
          每个栏位消耗 1 个道具，道具用完就停（并在回复里说明）。
        - 锦鲤玉佩（buff_quality）：作用在**钓手自己**身上，接下来 N 竿手气更好。
        - 珊瑚造景（decorate）：摆进水族馆，耐久内持续加成挂机产出（不喂鱼）。
        - 育灵水（feed_bonus）：喂给水族馆里的一条鱼，提升它的投喂上限。
        """
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            #: 入口快照库存：各「消耗分支」在扣完道具后统一走 `_note_item_used()`
            #: 累计每日用量（每日上限就靠它；v1.18.46）。
            _stock_before = dict(player.get("items") or {})
            item_id = self._find_item(a2)
            # 少打空格的容错：/钓鱼 用高级饲料 1、/钓鱼 用 高级饲料 1-3
            if item_id is None:
                peeled_item = self._peel_action(
                    a2, tuple(i["name"] for i in self.items.values())
                )
                if peeled_item:
                    guess, glued = peeled_item
                    item_id = self._find_item(guess)
                    if item_id is not None:
                        a3 = f"{glued} {a3}".strip()
            if item_id is None:
                owned = player.get("items") or {}
                if not owned:
                    async for _r in self._say_msg(event, "item.empty", event.plain_result("🎒 没有道具，去 /钓鱼 道具 买")):
                        yield _r
                    return
                names = "、".join(
                    self._item_label(i) for i, c in owned.items() if _safe_int(c, 0, 0) > 0
                )
                async for _r in self._say_msg(event, "item.usage", event.plain_result(f"📖 /钓鱼 用 <道具> <序列>　你有：{names or '无'}")):
                    yield _r
                return

            items = player.get("items") or {}
            if _safe_int(items.get(item_id), 0, 0) <= 0:
                async for _r in self._say_msg(event, "item.missing", event.plain_result(
                        f"🎒 没有 {self._item_label(item_id)}，去 /钓鱼 道具 买"
                    )):
                    yield _r
                return

            item = self.items.get(item_id) or {}
            effects = item.get("effects") or {}

            # --- 每日使用上限（v1.18.46：写在道具表第 8 段，一件一件可配）---
            # 站长要求「道具的每日使用上限可配置化」。0 / 省略 = 不限，
            # 于是「后期强度道具不能从早用到晚」这件事可以逐件调，不用加全局配置键。
            daily_limit = _safe_int(item.get("daily_limit"), 0, 0)
            if daily_limit > 0:
                _daily_reset(player, self._today_text())
                daily_key = f"item_{item_id}"
                used_today = _daily_used(player, daily_key)
                if used_today >= daily_limit:
                    async for _r in self._say_msg(event, "item.daily_limit", event.plain_result(
                            f"🌙 {self._item_label(item_id)}今天已经用满了"
                            f"（{used_today}/{daily_limit} 次），明天再来"
                        )):
                        yield _r
                    return

            # --- 扩展效果（v1.13.0：extensions/*.py 注册的键）-----------------
            # 只用扩展键的道具，插件默认不认识（下面每个分支都不会命中），
            # 所以这里帮扩展把「消耗一件 + 保存 + 提示」做完；和内置效果混在
            # 一起的道具仍由下面的分支负责消耗与提示，这里只跑扩展逻辑。
            has_ext, has_builtin = _has_ext_effect(effects)
            if has_ext and not has_builtin:
                ext_lines = _ext_effect_lines(self, player, item_id, effects)
                self._note_item_used(player, item_id, 1, items)
                await self._save_player(player)
                async for _r in self._say_msg(
                    event,
                    "item.used",
                    event.plain_result(
                        f"🧩 {self._item_label(item_id)}（扩展效果）\n" + "\n".join(ext_lines)
                    ),
                    again_values=self._use_again_values(player, item_id, a3),
                ):
                    yield _r
                return
            if has_ext:
                if _ext_effect_lines(self, player, item_id, effects):
                    await self._save_player(player)

            # --- 钓手手气 buff（锦鲤玉佩）：作用在人身上，持续 buff_cast_count 竿 ---
            # 它写的是 buff_quality（每竿加多少）+ buff_casts_left（还剩几竿），
            # **不碰 luck_charges**（那是一次性的，见 _calc._effective_luck / _consume_luck）
            if _safe_number(effects.get("buff_quality"), 0.0) > 0:
                # 每日额度（v1.18.18）：手气道具**每天合计只能生效 N 竿**，
                # 否则玩家从早挂到晚 = 品质分布被永久顶穿。
                today_text = self._today_text()
                if _daily_reset(player, today_text):
                    pass
                left = _daily_left(
                    player, "buff", self.cfg.get("buff_daily_cast_limit")
                )
                if left is not None and left <= 0:
                    async for _r in self._say_msg(event, "item.used", event.plain_result(
                            f"🌙 今天的手气额度用完了"
                            f"（{_daily_used(player, 'buff')}/"
                            f"{_safe_int(self.cfg.get('buff_daily_cast_limit'), 0, 0)} 竿）\n"
                            f"　{self._item_label(item_id)}先留着，明天 0 点重置"
                        )):
                        yield _r
                    return
                self._note_item_used(player, item_id, 1, items)
                gain = _clamp(_safe_number(effects.get("buff_quality"), 0.0), 0.0, 2.0)
                # 每件道具自己的持续竿数（v1.18.20）：`buff_casts=40`；
                # 没写就用全局 buff_cast_count。以前**所有**手气道具都用全局值，
                # 于是「潮汐香 40 竿 / 玉髓灯 15 竿」只是说明文字，实际都是 20 竿。
                cap_casts = int(
                    _safe_int(
                        effects.get("buff_casts"), self.cfg["buff_cast_count"], 1
                    )
                )
                cap_casts = int(_clamp(cap_casts, 1, 9999))
                # 额度不够一整轮时就只给剩下的那几竿（不浪费这一件道具的其余部分）
                casts = cap_casts if left is None else max(1, min(cap_casts, left))
                _daily_add(player, "buff", casts)
                # ---- 同类效果**不叠加**（v1.18.20，站长：「同类效果不能叠加，
                # 比如说都加手气的道具」）----
                # 手气道具只有一份 buff：同时用两件时取**较高的那个数值**，
                # 时长取较长的那个，绝不把 +20% 和 +35% 加成 +55%。
                old_gain = _safe_number(player.get("buff_quality"), 0.0)
                old_left = _safe_int(player.get("buff_casts_left"), 0, 0)
                replaced = old_left > 0 and gain > old_gain + 1e-9
                if old_left > 0:
                    player["buff_quality"] = _clamp(max(old_gain, gain), 0.0, 2.0)
                else:
                    player["buff_quality"] = gain
                player["buff_casts_left"] = max(old_left, casts)
                saved = await self._save_player(player)
                _limit_cap = _safe_int(self.cfg.get("buff_daily_cast_limit"), 0, 0)
                lines = [
                    f"🎐 使用 {self._item_label(item_id)}",
                    f"　作用在你自己身上：接下来 {player['buff_casts_left']} 竿手气更好"
                    f"（{_luck_stars(_safe_number(player.get('buff_quality'), 0.0), 0.3)}）",
                    "　（不是喂鱼，鱼的三维不会变）",
                ]
                if old_left > 0:
                    lines.append(
                        f"　🧷 同类手气**不叠加**：按较强的那个算 "
                        f"+{_safe_number(player.get('buff_quality'), 0.0):.0%}"
                        + (
                            f"（这件更强，替掉了原来的 +{old_gain:.0%}）"
                            if replaced
                            else f"（原来那件更强，这件只续时长）"
                        )
                    )
                if _limit_cap > 0:
                    lines.append(
                        f"　📅 今日手气额度 {_daily_used(player, 'buff')}/{_limit_cap} 竿"
                    )
                if not saved:
                    lines.append("⚠️ 保存失败")
                # 还有货就给「再次使用」按钮（照着刚才那条指令再发一次）
                async for _r in self._say_msg(
                    event,
                    "item.used",
                    event.plain_result("\n".join(lines)),
                    again_values=self._use_again_values(player, item_id, a3),
                ):
                    yield _r
                return

            # --- 品质保底（v1.18.23，锦鲤玉佩/潮汐香/玉髓灯）：接下来 N 竿不出垃圾 ---
            # 和手气 buff 是**两件事**：手气让分布往高档偏，保底直接抬下限。
            # 用的是同一个「手气额度」桶（都是强度型道具，玩家从早挂到晚也刷不出无限强度），
            # 但字段独立（buff_floor / buff_floor_casts），寿命各算各的。
            if _safe_number(effects.get("quality_floor"), 0.0) > 0:
                today_text = self._today_text()
                if _daily_reset(player, today_text):
                    pass
                left = _daily_left(
                    player, "buff", self.cfg.get("buff_daily_cast_limit")
                )
                if left is not None and left <= 0:
                    async for _r in self._say_msg(event, "item.used", event.plain_result(
                            f"🌙 今天的手气额度用完了"
                            f"（{_daily_used(player, 'buff')}/"
                            f"{_safe_int(self.cfg.get('buff_daily_cast_limit'), 0, 0)} 竿）\n"
                            f"　{self._item_label(item_id)}先留着，明天 0 点重置"
                        )):
                        yield _r
                    return
                self._note_item_used(player, item_id, 1, items)
                floor = _clamp(
                    _safe_number(effects.get("quality_floor"), 0.0), 0.0, _quality_ceil()
                )
                cap_casts = int(
                    _safe_int(
                        effects.get("buff_casts"), self.cfg["buff_cast_count"], 1
                    )
                )
                cap_casts = int(_clamp(cap_casts, 1, 9999))
                casts = cap_casts if left is None else max(1, min(cap_casts, left))
                _daily_add(player, "buff", casts)
                # 同类不叠加：两件保底道具同时用，取**较高的保底**、时长取较长的那次
                old_floor = _safe_number(player.get("buff_floor"), 0.0)
                old_floor_left = _safe_int(player.get("buff_floor_casts"), 0, 0)
                floor_replaced = old_floor_left > 0 and floor > old_floor + 1e-9
                if old_floor_left > 0:
                    player["buff_floor"] = max(old_floor, floor)
                else:
                    player["buff_floor"] = floor
                player["buff_floor_casts"] = max(old_floor_left, casts)
                saved = await self._save_player(player)
                _limit_cap = _safe_int(self.cfg.get("buff_daily_cast_limit"), 0, 0)
                now_floor = _safe_number(player.get("buff_floor"), 0.0)
                floor_name, floor_emoji = _quality_label(now_floor)
                old_name = _quality_label(old_floor)[0] if old_floor > 0 else ""
                lines = [
                    f"🧿 使用 {self._item_label(item_id)}",
                    f"　作用在你自己身上：接下来 {player['buff_floor_casts']} 竿有品质保底，"
                    f"不低于「{floor_emoji}{floor_name}」"
                    f"（{now_floor:.1f} 倍起步）",
                    "　（不是喂鱼，鱼的三维不会变；神品仍然只能靠洗髓丹）",
                ]
                if old_floor_left > 0:
                    lines.append(
                        f"　🧷 同类保底**不叠加**：按较高的那个算「{floor_name}」"
                        + (
                            f"（这件更高，替掉了原来的「{old_name}」）"
                            if floor_replaced
                            else "（原来那件更高，这件只续时长）"
                        )
                    )
                if _limit_cap > 0:
                    lines.append(
                        f"　📅 今日手气额度 {_daily_used(player, 'buff')}/{_limit_cap} 竿"
                    )
                if not saved:
                    lines.append("⚠️ 保存失败")
                # 还有货就给「再次使用」按钮（照着刚才那条指令再发一次）
                async for _r in self._say_msg(
                    event,
                    "item.used",
                    event.plain_result("\n".join(lines)),
                    again_values=self._use_again_values(player, item_id, a3),
                ):
                    yield _r
                return

            # --- 洗髓丹（quality_reroll）：重掷这条鱼的个体品质，取更好的那次 ---
            # 效果值和「重掷几次」同义：写 3 就是掷 3 次取最好（次数越多越容易出珍品/绝品）
            if _safe_number(effects.get("quality_reroll"), 0.0) > 0:
                reroll_tank: list[dict[str, Any]] = player.get("aquarium") or []
                if not reroll_tank:
                    async for _r in self._say_msg(event, "item.reroll_no_fish", event.plain_result(
                            "🐠 水族馆是空的，先把要洗的鱼放进去"
                        )):
                        yield _r
                    return
                if not (a3 or "").strip():
                    async for _r in self._say_msg(event, "item.reroll_usage", event.plain_result(
                            f"📖 /钓鱼 用 {item.get('name', item_id)} <水族馆栏位>\n"
                            "　写上要洗髓的那条鱼的栏位号（1 2 3 / 1-3 都行）"
                        )):
                        yield _r
                    return
                # 序号以 /钓鱼 水族馆 的展示顺序为准（投喂/洗髓改过估值后，缸里的
                # 顺序会变，这里排一遍才不会指错鱼 —— v1.18.37）
                self._sort_aquarium(reroll_tank)
                picked = self._parse_indices(a3, reroll_tank)
                if not picked:
                    async for _r in self._say_msg(event, "item.reroll_bad_slot", event.plain_result(
                            "🤔 栏位号不对，/钓鱼 水族馆 看看序号"
                        )):
                        yield _r
                    return
                rolls = max(1, min(50, int(round(_safe_number(effects.get("quality_reroll"), 1.0)))))
                weights = list(self.cfg.get("quality_weights") or [])
                # 神品：只能在洗髓时靠这个概率命中（自然上钩的权重是 0）
                myth_chance = _clamp(
                    _safe_number(self.cfg.get("quality_myth_chance"), 0.0025), 0.0, 1.0
                )
                myth_name, myth_low, myth_high = (
                    QUALITY_TIERS[-1][0], QUALITY_TIERS[-1][1], QUALITY_TIERS[-1][2]
                )
                today = self._today_text()
                cap = _reroll_daily_cap(self.cfg)
                _daily_reset(player, today)
                # 每日总额度（v1.18.18）：在「每条鱼每天 N 颗」之上再加一层，
                # 挡住「买一堆丹把整个缸洗一遍」
                total_left = _daily_left(
                    player, "reroll", self.cfg.get("reroll_daily_total")
                )
                if total_left is not None and total_left <= 0:
                    async for _r in self._say_msg(event, "item.reroll_failed", event.plain_result(
                            f"🌙 今天的洗髓丹额度用完了"
                            f"（{_daily_used(player, 'reroll')}/"
                            f"{_safe_int(self.cfg.get('reroll_daily_total'), 0, 0)} 颗）\n"
                            "　明天 0 点重置"
                        )):
                        yield _r
                    return
                lines = []
                used = 0
                best_gain = 0
                averse: list[int] = []
                for idx in picked:
                    if _safe_int(items.get(item_id), 0, 0) <= 0:
                        break
                    if total_left is not None and used >= total_left:
                        break          # 今天的总额度用完了
                    instance = reroll_tank[idx - 1]
                    # 今天吃腻了（达到每日上限）：跳过，并告诉玩家明天再来
                    if _reroll_averse(instance, self.cfg, today):
                        averse.append(idx)
                        continue
                    before_mult = _safe_number(instance.get("quality_mult"), 1.0)
                    before_label = str(instance.get("quality") or "")
                    before_value = _instance_value(instance)
                    best = before_mult
                    hit_myth = False
                    for _ in range(rolls):
                        if myth_chance > 0 and random.random() < myth_chance:
                            best = max(best, random.uniform(myth_low, myth_high))
                            hit_myth = True
                            break          # 出了神品就不用再掷了
                        best = max(best, _roll_quality_mult(weights))
                    self._note_item_used(player, item_id, 1, items)
                    used += 1
                    _daily_add(player, "reroll", 1)
                    eaten = _reroll_used(instance, today) + 1
                    _reroll_mark(instance, today, eaten)
                    tail = f"　今天 {eaten}/{cap}" if cap > 0 else f"　今天 {eaten}"
                    if best > before_mult + 1e-9:
                        new_label, new_value = _apply_quality(instance, best)
                        best_gain = max(best_gain, new_value - before_value)
                        lines.append(
                            f"　{idx}. {_fish_name(instance.get('fish_id', ''))} "
                            f"{before_label} → {new_label}"
                            f"（估值 {_fmt_gold(before_value)} → {_fmt_gold(new_value)}）{tail}"
                        )
                        if hit_myth and new_label == myth_name:
                            lines.append(f"　　🌟 洗出{myth_name}了！这条鱼脱胎换骨")
                    else:
                        lines.append(
                            f"　{idx}. {_fish_name(instance.get('fish_id', ''))} "
                            f"这颗丹没洗出更好的（保持 {before_label}）{tail}"
                        )
                if used <= 0:
                    if averse:
                        text = (
                            f"🤢 栏位 {'、'.join(str(i) for i in averse)} "
                            f"今天已经吃满 {cap} 颗洗髓丹了，闻着就烦（厌恶）\n"
                            "　明天再洗吧；上限可在编辑器「⚙️ 数值」页改（reroll_daily_limit）"
                        )
                    else:
                        text = "🔮 没能用出去\n" + "\n".join(lines or ["　（没有可用目标）"])
                    async for _r in self._say_msg(event, "item.reroll_failed", event.plain_result(text)):
                        yield _r
                    return
                saved = await self._save_player(player)
                head = [
                    f"🔮 {self._item_label(item_id)} ×{used}",
                    f"　剩余道具 {_safe_int(items.get(item_id), 0, 0)}",
                ]
                if averse:
                    head.append(
                        f"　（跳过今天已吃满的栏位 {len(averse)} 条，"
                        f"它们现在对洗髓丹「厌恶」）"
                    )
                if not saved:
                    head.append("⚠️ 保存失败")
                async for _r in self._say_msg(
                    event,
                    "item.reroll_done",
                    event.plain_result("\n".join(head + lines[:6])),
                    again_values=self._use_again_values(player, item_id, a3),
                ):
                    yield _r
                return

            # --- 水族馆装饰（珊瑚造景）：摆进鱼缸，耐久内持续加成挂机产出 ---
            if _safe_number(effects.get("decorate"), 0.0) > 0:
                slots = int(self.cfg["decoration_slots"])
                hours = int(self.cfg["decoration_hours"])
                now_ts = int(time.time())
                expired = _prune_decorations(player, now=now_ts)
                current = list(player.get("decorations") or [])
                if slots <= 0:
                    async for _r in self._say_msg(event, "item.deco_disabled", event.plain_result(
                            "🪸 本服没有开放装饰位（decoration_slots = 0）"
                        )):
                        yield _r
                    return
                # 数字参数 = 一次摆几个（装饰没有「全缸」语义，不写就是 1 个）。
                want = _to_int((a3 or "").strip(), 0)
                if want <= 0:
                    want = 1
                owned = _safe_int(items.get(item_id), 0, 0)
                if owned <= 0:
                    async for _r in self._say_msg(event, "item.deco_none", event.plain_result(
                            f"🪸 没有 {self._item_label(item_id)} 了（先去 /钓鱼 道具 买）"
                        )):
                        yield _r
                    return
                now_ts = int(time.time())
                rate = _safe_number(effects.get("decorate"), 0.0)
                decos = list(player.get("decorations") or [])
                room = max(0, slots - len(decos))
                # ⚠️ 位子满了但新装饰**更强**时，允许顶掉最弱的那个（v1.18.45 修）。
                #    站长报的：「只能加，不能被新的更好的道具覆盖，还得等时间结束」——
                #    以前位子一满就直接拒绝，买了更好的珊瑚也只能干等 72 小时。
                #    现在：有空位就填；没空位且新装饰 ≥ 场上最弱的，就替换掉最弱的
                #    （被顶掉的那个提前报废，剩余耐久不返还 —— 提示里说清楚）。
                replace_idx: int | None = None
                replaced: dict[str, Any] | None = None
                if room <= 0 and decos:
                    weakest = min(
                        range(len(decos)),
                        key=lambda i: _safe_number(decos[i].get("rate"), 0.0),
                    )
                    weakest_rate = _safe_number(decos[weakest].get("rate"), 0.0)
                    if rate >= weakest_rate:
                        replace_idx = weakest
                        replaced = decos[weakest]
                        room = 1        # 允许摆 1 个（顶掉它）
                if room <= 0:
                    weakest_rate = min(
                        (_safe_number(d.get("rate"), 0.0) for d in decos), default=0.0
                    )
                    async for _r in self._say_msg(event, "item.deco_full", event.plain_result(
                            f"🪸 装饰位满了（{len(decos)}/{slots}），而且这个没有比场上的更好\n"
                            f"　场上最弱的是 +{weakest_rate:.0%}，等它失效或换个更强的来顶"
                        )):
                        yield _r
                    return
                place = max(1, min(want, owned, room))
                self._note_item_used(player, item_id, place, items)
                replaced_note = ""
                if replace_idx is not None and replaced is not None:
                    # 顶掉最弱的那个（只顶 1 个；其余仍按空位算）
                    gone = decos.pop(replace_idx)
                    replaced_note = (
                        f"　♻️ 顶掉了「{self._item_label(str(gone.get('id') or ''))}」"
                        f"（+{_safe_number(gone.get('rate'), 0.0):.0%}），它提前失效"
                    )
                    player["decorations"] = decos
                    place = 1
                    self._note_item_used(player, item_id, 1, items)
                # 逐个入列：每个装饰记自己的 ts / expire_ts，互相独立计时
                for _ in range(place):
                    player.setdefault("decorations", []).append(
                        {
                            "id": item_id,
                            "rate": rate,
                            "ts": now_ts,
                            "expire_ts": now_ts + hours * 3600,
                        }
                    )
                saved = await self._save_player(player)
                head = f"🪸 摆好了 {self._item_label(item_id)}"
                lines = [head if place == 1 else f"{head} ×{place}"]
                if replaced_note:
                    lines.append(replaced_note)
                if place < want and not replaced_note:
                    lack = (
                        f"库存只有 {owned} 个" if owned < want
                        else f"装饰位只剩 {room} 个"
                    )
                    lines.append(f"　{lack}，摆了 {place} 个")
                lines.append(
                    f"　挂机产出 +{rate * place:.0%}　持续 {hours} 小时（离线时间也照算）"
                )
                lines.append(f"　装饰位 {len(player['decorations'])}/{slots}")
                lines.append(
                    "　到期："
                    + time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(now_ts + hours * 3600)
                    )
                )
                if expired:
                    lines.append(f"　（顺带清理了 {expired} 个已失效的装饰）")
                if not saved:
                    lines.append("⚠️ 保存失败")
                async for _r in self._say_msg(
                    event,
                    "item.deco_used",
                    event.plain_result("\n".join(lines)),
                    again_values=self._use_again_values(player, item_id, a3),
                ):
                    yield _r
                return

            # --- 体力类（heal）：直接回体力，不需要水族馆（v1.18.16 启用）---
            # 效果键 `heal` 从 v1.13.0 起就登记着「预留」，一直没人用；现在给姜汤这类道具用上。
            # 三种情况都不该扣道具：体力系统没开、体力已经满、道具不够。
            heal = int(round(_safe_number(effects.get("heal"), 0.0)))
            if heal > 0:
                if not _stamina_enabled(self.cfg):
                    async for _r in self._say_msg(event, "item.used", event.plain_result(
                            f"🍲 本服没开体力限制（想钓就钓），{item.get('name', item_id)}先留着吧"
                    )):
                        yield _r
                    return
                cap = int(self.cfg["stamina_max"])
                _refresh_stamina(player, self.cfg)
                before = _safe_int(player.get("stamina"), 0, 0)
                if before >= cap:
                    async for _r in self._say_msg(event, "item.used", event.plain_result(
                            f"⚡ 体力已经满了（{before}/{cap}），"
                            f"{item.get('name', item_id)}先留着"
                    )):
                        yield _r
                    return
                if _safe_int(items.get(item_id), 0, 0) <= 0:
                    async for _r in self._say_msg(event, "item.missing", event.plain_result(
                            f"🎒 没有 {self._item_label(item_id)} 了"
                    )):
                        yield _r
                    return
                # 每日额度（v1.18.18）：回体力道具一天最多喝几次（体力是「别从早玩到晚」的闸门）
                _daily_reset(player, self._today_text())
                heal_left = _daily_left(
                    player, "heal", self.cfg.get("hot_soup_daily_limit")
                )
                if heal_left is not None and heal_left <= 0:
                    async for _r in self._say_msg(event, "item.used", event.plain_result(
                            f"🌙 今天的回体力额度用完了"
                            f"（{_daily_used(player, 'heal')}/"
                            f"{_safe_int(self.cfg.get('hot_soup_daily_limit'), 0, 0)} 次）\n"
                            f"　{item.get('name', item_id)}先留着，明天 0 点重置"
                        )):
                        yield _r
                    return
                self._note_item_used(player, item_id, 1, items)
                _daily_add(player, "heal", 1)
                player["stamina"] = min(cap, before + heal)
                saved = await self._save_player(player)
                lines = [
                    f"🍲 喝下 {self._item_label(item_id)}",
                    f"　⚡ 体力 {before} → {player['stamina']}/{cap}",
                    f"　剩余 {_safe_int(items.get(item_id), 0, 0)} 个",
                ]
                _soup_cap = _safe_int(self.cfg.get("hot_soup_daily_limit"), 0, 0)
                if _soup_cap > 0:
                    lines.append(f"　📅 今日额度 {_daily_used(player, 'heal')}/{_soup_cap} 次")
                if not saved:
                    lines.append("⚠️ 保存失败")
                # 还有货就给「再次使用」按钮（照着刚才那条指令再发一次）
                async for _r in self._say_msg(
                    event,
                    "item.used",
                    event.plain_result("\n".join(lines)),
                    again_values=self._use_again_values(player, item_id, a3),
                ):
                    yield _r
                return

            # --- 育灵水（feed_bonus）：对水族馆里的一条鱼生效，提升它的投喂上限 ---
            if _safe_number(effects.get("feed_bonus"), 0.0) > 0:
                tank: list[dict[str, Any]] = player.get("aquarium") or []
                if not tank:
                    async for _r in self._say_msg(event, "item.breed_no_fish", event.plain_result("🐠 水族馆是空的，先把鱼放进去再培育")):
                        yield _r
                    return
                if not (a3 or "").strip():
                    async for _r in self._say_msg(event, "item.breed_usage", event.plain_result(
                            f"📖 /钓鱼 用 {item.get('name', item_id)} <水族馆栏位>\n"
                            "　写上要培育的那条鱼的栏位号（1 2 3 / 1-3 都行）"
                        )):
                        yield _r
                    return
                self._sort_aquarium(tank)
                picked = self._parse_indices(a3, tank)
                if not picked:
                    async for _r in self._say_msg(event, "item.breed_bad_slot", event.plain_result("🤔 栏位号不对，/钓鱼 水族馆 看看序号")):
                        yield _r
                    return
                bonus = int(round(_safe_number(effects.get("feed_bonus"), 0.0)))
                lines = []
                used = 0
                for idx in picked:
                    if _safe_int(items.get(item_id), 0, 0) <= 0:
                        break
                    instance = tank[idx - 1]
                    before = _safe_int(instance.get("feed_bonus"), 0, 0)
                    if before >= 20:
                        lines.append(f"　{idx}. 已经培育到上限（+20 次）")
                        continue
                    instance["feed_bonus"] = min(20, before + bonus)
                    self._note_item_used(player, item_id, 1, items)
                    used += 1
                    lines.append(
                        f"　{idx}. {_instance_line(instance, with_value=False)}"
                        f"　投喂上限 {_feed_cap(instance, self.cfg)} 次"
                    )
                if used <= 0:
                    async for _r in self._say_msg(event, "item.breed_failed", event.plain_result(
                            "🌱 没能用出去：\n" + "\n".join(lines or ["　（没有可用目标）"])
                        )):
                        yield _r
                    return
                saved = await self._save_player(player)
                head = [
                    f"🌱 {self._item_label(item_id)} ×{used}",
                    f"　剩余道具 {_safe_int(items.get(item_id), 0, 0)}",
                ]
                if not saved:
                    head.append("⚠️ 保存失败")
                async for _r in self._say_msg(
                    event,
                    "item.breed_done",
                    event.plain_result("\n".join(head + lines[:6])),
                    again_values=self._use_again_values(player, item_id, a3),
                ):
                    yield _r
                return

            # --- 饲料类：需要水族馆目标（支持批量栏位）---
            aquarium: list[dict[str, Any]] = player.get("aquarium") or []
            if not aquarium:
                async for _r in self._say_msg(event, "item.feed_no_fish", event.plain_result("🐠 水族馆是空的，先把鱼放进去养")):
                    yield _r
                return
            # 序号以展示顺序为准（见 _calc._sort_tank）
            self._sort_aquarium(aquarium)
            # 不带栏位 = 喂全缸（「喂养对所有鱼生效」）
            if not (a3 or "").strip():
                slots = list(range(1, len(aquarium) + 1))
            else:
                slots = self._parse_indices(a3, aquarium)
            if not slots:
                async for _r in self._say_msg(event, "item.feed_usage", event.plain_result(
                        f"📖 /钓鱼 用 {item.get('name', item_id)} [水族馆栏位]\n"
                        f"　不写栏位就是喂全缸；也可以写 1 2 3 / 1-3"
                    )):
                    yield _r
                return

            stock = _safe_int(items.get(item_id), 0, 0)
            max_uses = int(self.cfg["feed_max_uses"])   # 基础值；每条鱼还可能被育灵水加成
            used = 0
            grown: list[str] = []
            skipped_full: list[int] = []
            value_gain = 0
            for idx in slots:
                if stock <= 0:
                    break
                instance = aquarium[idx - 1]
                if _safe_int(instance.get("feed_uses"), 0, 0) >= _feed_cap(
                    instance, self.cfg
                ):
                    skipped_full.append(idx)
                    continue
                # ⚠️ 走统一的消耗口：它同时扣库存 + 记「今日用量」
                # （每件道具的每日上限就靠这个计数；v1.18.46）
                stock = self._note_item_used(player, item_id, 1, items)
                used += 1
                _, delta = _apply_feed(instance, effects)
                value_gain += delta
                grown.append(f"　{idx}. {_instance_line(instance, with_value=False)}"
                             f"　{_safe_int(instance.get('feed_uses'), 0, 0)}"
                             f"/{_feed_cap(instance, self.cfg)}")

            if used <= 0:
                if skipped_full:
                    async for _r in self._say_msg(event, "item.feed_full", event.plain_result(
                            f"🍖 栏位 {'、'.join(str(i) for i in skipped_full)} "
                            f"都已经喂满 {max_uses} 次了"
                        )):
                        yield _r
                else:
                    async for _r in self._say_msg(event, "item.feed_missing", event.plain_result(f"🎒 没有 {self._item_label(item_id)} 了")):
                        yield _r
                return

            items[item_id] = stock
            player["total_fed"] = _safe_int(player.get("total_fed"), 0, 0) + used
            new_ach = self._check_achievements(player)
            saved = await self._save_player(player)

            sign = "+" if value_gain >= 0 else ""
            lines = [
                f"🍽 {self._item_label(item_id)} ×{used}"
                f"　馆藏总价 {sign}{_fmt_gold(value_gain)}"
                f"　剩余道具 {stock}"
            ]
            # 单条时展开细节，批量时只列前 6 条，避免刷屏
            lines.extend(grown[:6])
            if len(grown) > 6:
                lines.append(f"　… 其余 {len(grown) - 6} 条也喂到了")
            if skipped_full:
                lines.append(
                    f"　（跳过已喂满的栏位 {len(skipped_full)} 条）"
                )
            # 还有货就给「再次使用」按钮（照着刚才那条命令再发一次）
            async for _r in self._say_msg(
                event,
                "item.feed_done",
                event.plain_result("\n".join(lines)),
                again_values=self._use_again_values(player, item_id, a3),
            ):
                yield _r

    # =========================================================================
    # 自动补给 / 称号 / 供奉（v1.18.17）
    # =========================================================================

    async def _cmd_auto_supply(
        self, event: AstrMessageEvent, user_id: str, spec: str
    ):
        """``/钓鱼 自动 [手气道具名|关]``：设定「用完自动补 + 自动用」的那件道具。

        * 不带参数 = 看当前设置与可选项；
        * ``关`` / ``关掉`` / ``off`` = 取消自动；
        * 写了道具名 = 只认**钓手 buff 类**道具（玉佩 / 潮汐香 / 玉髓灯这种带
          ``buff_quality`` 或 ``quality_floor`` 的），别的道具（饲料之类）不给设 ——
          免得把喂鱼的道具当成自动消耗品每竿买一个。
        """
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            want = (spec or "").strip()
            buff_items = [
                (iid, item)
                for iid, item in self.items.items()
                if _safe_number((item.get("effects") or {}).get("buff_quality"), 0.0) > 0
                or _safe_number((item.get("effects") or {}).get("quality_floor"), 0.0) > 0
            ]
            buff_items.sort(key=lambda kv: _safe_int(kv[1].get("unlock_level"), 1, 1))

            def _buff_desc(item: dict[str, Any]) -> str:
                """这件道具自动用上之后给什么（手气 / 品质保底）。"""
                eff = item.get("effects") or {}
                floor = _safe_number(eff.get("quality_floor"), 0.0)
                if floor > 0:
                    return f"品质保底「{_quality_label(floor)[0]}」"
                return f"手气 +{_safe_number(eff.get('buff_quality'), 0.0):.0%}"

            if not want:
                current = str(player.get("auto_buff_item") or "")
                lines = ["🤖 自动补给"]
                lines.append(
                    "　当前："
                    + (
                        f"{self._item_label(current)}（鱼饵用光会自动买）"
                        if current
                        else "只自动补鱼饵（手气道具没指定）"
                    )
                )
                lines.append("　可指定：")
                for iid, item in buff_items:
                    gate = _safe_int(item.get("unlock_level"), 1, 1)
                    locked = "" if _player_level(player) >= gate else f"（{gate} 级解锁）"
                    lines.append(
                        f"　　{self._item_label(iid)}　{_fmt_gold(item.get('price', 0))} 金"
                        f"　{_buff_desc(item)}"
                        f"{locked}"
                    )
                lines.append("　写法：/钓鱼 自动 锦鲤玉佩　｜　/钓鱼 自动 关")
                async for _r in self._say_msg(event, "auto.view", event.plain_result("\n".join(lines))):
                    yield _r
                return

            if want in ("关", "关掉", "取消", "off", "none", "无"):
                player["auto_buff_item"] = ""
                await self._save_player(player)
                async for _r in self._say_msg(event, "auto.off", event.plain_result(
                        "✅ 已关掉手气道具的自动补给（鱼饵照旧自动补）"
                )):
                    yield _r
                return

            target = None
            for iid, _item in buff_items:
                if want == iid or want == str(_item.get("name") or ""):
                    target = iid
                    break
            if target is None:
                names = "、".join(str(item.get("name")) for _i, item in buff_items)
                async for _r in self._say_msg(event, "auto.bad", event.plain_result(
                        f"🤔 只能指定手气类道具（{names}）\n　发了 /钓鱼 自动 看清单"
                )):
                    yield _r
                return
            player["auto_buff_item"] = target
            saved = await self._save_player(player)
            item = self.items[target]
            lines = [
                f"✅ 自动补给已设为 {self._item_label(target)}",
                f"　buff 用光时自动买 1 个并立刻用上（现在 "
                f"{_safe_int((player.get('items') or {}).get(target), 0, 0)} 个，"
                f"单价 {_fmt_gold(item.get('price', 0))}）",
                "　金币不够就什么都不买（不会透支）；/钓鱼 自动 关 可以取消",
            ]
            if not saved:
                lines.append("⚠️ 保存失败")
            async for _r in self._say_msg(event, "auto.on", event.plain_result("\n".join(lines))):
                yield _r

    async def _cmd_titles(self, event: AstrMessageEvent, user_id: str, spec: str):
        """``/钓鱼 称号 [买 <名字>|戴 <名字>]``：后期金币回收（纯炫耀、无属性）。

        称号是给「钱多到没处花」的玩家准备的目标：一次性买断、随时换着戴，
        展示在 `/钓鱼 档案` 与群排行榜里，**不影响任何数值**（不破坏平衡）。
        """
        if not self.titles:
            async for _r in self._say_msg(event, "title.disabled", event.plain_result(
                    "🏷 本服没有配置称号（title_defs 留空）"
            )):
                yield _r
            return
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            owned = [t for t in (player.get("titles") or []) if isinstance(t, str)]
            current = str(player.get("title") or "")
            sub, _, arg = (spec or "").strip().partition(" ")
            sub = sub.strip()
            arg = arg.strip()

            def find(name: str) -> dict[str, Any] | None:
                name = (name or "").strip()
                for t in self.titles:
                    if name and (name == t["id"] or name == t["name"]):
                        return t
                return None

            if sub in ("买", "购买", "buy"):
                title = find(arg or "")
                if title is None:
                    async for _r in self._say_msg(event, "title.not_found", event.plain_result(
                            "🤔 没有这个称号，发 /钓鱼 称号 看清单"
                    )):
                        yield _r
                    return
                if title["id"] in owned:
                    async for _r in self._say_msg(event, "title.owned", event.plain_result(
                            f"✅ 你已经有「{title['name']}」了（/钓鱼 称号 戴 {title['name']}）"
                    )):
                        yield _r
                    return
                price = _safe_int(title.get("price"), 0, 0)
                gold = _safe_int(player.get("gold"), 0, 0)
                if gold < price:
                    async for _r in self._say_msg(event, "title.no_gold", event.plain_result(
                            f"💸 「{title['name']}」要 {_fmt_gold(price)} 金币，"
                            f"你还差 {_fmt_gold(price - gold)}"
                    )):
                        yield _r
                    return
                player["gold"] = gold - price
                owned.append(title["id"])
                player["titles"] = owned
                player["title"] = title["id"]
                saved = await self._save_with_notices(player, [])
                lines = [
                    f"🏷 买下称号「{title['emoji']}{title['name']}」并戴上了！",
                    f"　{title['desc']}",
                    f"　💰 余额 {_fmt_gold(player['gold'])}",
                ]
                if not saved:
                    lines.append("⚠️ 保存失败")
                async for _r in self._say_msg(event, "title.bought", event.plain_result("\n".join(lines))):
                    yield _r
                return

            if sub in ("戴", "佩戴", "换", "use", "equip"):
                title = find(arg or "")
                if title is None or title["id"] not in owned:
                    async for _r in self._say_msg(event, "title.not_owned", event.plain_result(
                            "🎒 你还没买这个称号（/钓鱼 称号 买 <名字>）"
                    )):
                        yield _r
                    return
                player["title"] = title["id"]
                await self._save_player(player)
                async for _r in self._say_msg(event, "title.equipped", event.plain_result(
                        f"✅ 已戴上「{title['emoji']}{title['name']}」"
                )):
                    yield _r
                return

            lines = [f"🏷 称号　当前：{self._title_label(player) or '（没戴）'}"]
            gold = _safe_int(player.get("gold"), 0, 0)
            for t in self.titles:
                have = t["id"] in owned
                here = "📍" if t["id"] == current else "　"
                tag = "已拥有" if have else f"{_fmt_gold(t['price'])}金"
                lines.append(
                    f"{here}{t['emoji']}{t['name']}　{tag}　{t['desc']}"
                )
            lines.append(f"💰 你有 {_fmt_gold(gold)}")
            lines.append("💡 /钓鱼 称号 买 <名字>　｜　/钓鱼 称号 戴 <名字>")
            async for _r in self._say_msg(event, "title.list", event.plain_result("\n".join(lines))):
                yield _r

    async def _cmd_offering(self, event: AstrMessageEvent, user_id: str):
        """``/钓鱼 供奉``：花一笔大钱换 24 小时的挂机产出 + 手气加成（后期金币回收）。

        这是**主动**的钱坑：越到后期越划算，钱少的人不买也不亏。
        可重复供奉（时间不叠加，直接续到「现在 + offering_hours」）。
        """
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            now = int(time.time())
            price = max(0, _safe_int(self.cfg.get("offering_price"), 200000, 0))
            hours = max(1, _safe_int(self.cfg.get("offering_hours"), 24, 1))
            income_bonus = max(0.0, _safe_number(self.cfg.get("offering_income_bonus"), 0.5))
            luck_bonus = max(0.0, _safe_number(self.cfg.get("offering_luck_bonus"), 0.05))
            until = _safe_int(player.get("offering_ts"), 0, 0)
            gold = _safe_int(player.get("gold"), 0, 0)
            # 每日额度（v1.18.18）：供奉默认一天一次（效果本来就有 24 小时，
            # 再叠加只是把钱重复烧掉，不如把额度写清楚）
            _daily_reset(player, self._today_text())
            offer_left = _daily_left(
                player, "offering", self.cfg.get("offering_daily_limit")
            )
            if offer_left is not None and offer_left <= 0:
                async for _r in self._say_msg(event, "offering.no_gold", event.plain_result(
                        f"🕯️ 今天已经供奉过了"
                        f"（每天 "
                        f"{_safe_int(self.cfg.get('offering_daily_limit'), 1, 1)} 次）\n"
                        f"　现在的香火还有效，明天再来上香吧"
                    )):
                    yield _r
                return
            if gold < price:
                async for _r in self._say_msg(event, "offering.no_gold", event.plain_result(
                        f"💸 供奉一次要 {_fmt_gold(price)} 金币，"
                        f"你还差 {_fmt_gold(price - gold)}\n"
                        f"　供奉效果：{hours} 小时内挂机产出 +{income_bonus:.0%}、"
                        f"手气 +{luck_bonus:.2f}"
                )):
                    yield _r
                return
            player["gold"] = gold - price
            player["offering_ts"] = now + hours * 3600
            _daily_add(player, "offering", 1)
            lines = [
                f"🕯️ 供奉成功！接下来 {hours} 小时：",
                f"　挂机产出 +{income_bonus:.0%}　手气 +{luck_bonus:.2f}",
                f"　💰 余额 {_fmt_gold(player['gold'])}",
                "　（可重复供奉续时间，不叠加效果）",
            ]
            _offer_cap = _safe_int(self.cfg.get("offering_daily_limit"), 0, 0)
            if _offer_cap > 0:
                lines.append(
                    f"　📅 今日供奉 {_daily_used(player, 'offering')}/{_offer_cap} 次"
                )
            saved = await self._save_with_notices(player, lines)
            async for _r in self._say_msg(event, "offering.done", event.plain_result("\n".join(lines))):
                yield _r

    # =========================================================================
    # 档案 / 签到 / 管理员
    # =========================================================================

    async def _cmd_stamina(self, event: AstrMessageEvent, user_id: str):
        """体力：/钓鱼 体力

        体力是惰性结算的（按时间戳现算），所以这里只读不写 ——
        看一眼体力不会产生存档写入，也不会把「恢复零头」抹掉。
        """
        player = await self._load_player(user_id)
        async for _r in self._say_msg(event, "stamina.view", event.plain_result(self._stamina_text(player))):
            yield _r

    async def _cmd_gold_renamed(self, event: AstrMessageEvent, user_id: str):
        """旧指令 /钓鱼 金币 的迁移提示：它原本显示的是档案，名不符实。"""
        player = await self._load_player(user_id)
        async for _r in self._say_msg(event, "profile.renamed", event.plain_result(
                f"📇 这个指令改名了：/钓鱼 档案\n"
                f"　金币只是档案里的一项（你现在 "
                f"{_fmt_gold(player.get('gold', 0))} 金币）"
            )):
            yield _r

    async def _cmd_profile(self, event: AstrMessageEvent, user_id: str):
        player = await self._load_player(user_id)
        inventory = player.get("inventory") or []
        aquarium = player.get("aquarium") or []
        collection = player.get("collection") or {}
        baits = player.get("baits") or {}
        items = player.get("items") or {}
        capacity = self._aquarium_capacity(player)

        bait_text = "、".join(
            f"{self.baits[b]['name']}×{_safe_int(baits.get(b), 0, 0)}"
            for b in self._bait_list()
            if _safe_int(baits.get(b), 0, 0) > 0
        ) or "无"
        item_text = "、".join(
            f"{self.items[i]['name']}×{_safe_int(items.get(i), 0, 0)}"
            for i in self._item_list()
            if _safe_int(items.get(i), 0, 0) > 0
        ) or "无"
        kinds = sum(
            1
            for e in collection.values()
            if isinstance(e, dict) and _safe_int(e.get("count"), 0, 0) > 0
        )
        total_value = _inventory_value(inventory) + _inventory_value(aquarium)
        cap = _backpack_capacity(player, self.cfg)
        aq_cap = self._aquarium_capacity(player)

        lines = [
            "📇 档案"
            + (f"　🏷 {self._title_label(player)}" if self._title_label(player) else ""),
            # 等级统一放在档案里（站长要求）：以前只有 /钓鱼 钓点 里写过「等级 N」，
            # 档案反而没有 —— 玩家升级了却看不出来。这里按真实曲线写清「当前级 + 进度」。
            self._level_line(player),
            f"💰 {_fmt_gold(player.get('gold', 0))}　🎣 {self._rod_label(player)}"
            f"　📍 {self._location_label(player)}",
        ]
        if _stamina_enabled(self.cfg):
            stamina_cap = int(self.cfg["stamina_max"])
            lines.append(
                f"⚡ 体力 {_refresh_stamina(player, self.cfg)}/{stamina_cap}"
                f"　（每 {int(self.cfg['stamina_regen_seconds'])} 秒回 1 点）"
            )
        lines.extend([
            f"🧰 鱼饵 {self._bait_label(player.get('equipped_bait', 'none'))}"
            f"　背包 {len(inventory)}/{cap}　水族馆 {len(aquarium)}/{aq_cap}",
            f"🧮 渔获估值 {_fmt_gold(total_value)}"
            f"　🏅 累计钓 {_safe_int(player.get('total_caught'), 0, 0)}"
            f"　卖 {_safe_int(player.get('total_sold'), 0, 0)}"
            f"　喂 {_safe_int(player.get('total_fed'), 0, 0)}",
            f"📖 图鉴 {kinds}/{len(FISH_POOL)}　🎁 杂物 "
            f"{sum(1 for c, n in (player.get('collectibles') or {}).items() if _safe_int(n, 0, 0) > 0)}"
            f"/{len(COLLECTIBLES)}　📜 纸条 {len(player.get('bottle_notes') or [])}",
            f"🎖 成就 {len(player.get('achievements') or [])}/{len(ACHIEVEMENTS)}"
            f"　📋 订单 {_safe_int(player.get('total_orders'), 0, 0)}",
            f"🎒 饵：{bait_text}",
            f"🧰 道具：{item_text}",
        ])
        # 今日额度（v1.18.18）：没开任何限额时这行是空的，不占版面
        # ⚠️ 跨天时这里顺手把额度落盘：不然「看一眼档案」不写盘，别的视图
        # （比如编辑器玩家页）读到的还是昨天的计数。
        if _daily_reset(player, self._today_text()):
            await self._save_player(player)
        _quota_line = _daily_line(player, self.cfg, self._today_text())
        if _quota_line:
            lines.append(_quota_line)
        buff = self._buff_status_line(player)
        if buff:
            lines.append(buff)
        async for _r in self._say_msg(event, "profile.view", event.plain_result("\n".join(lines))):
            yield _r

    async def _cmd_sign(self, event: AstrMessageEvent, user_id: str):
        today = self._today_text()
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            if player.get("last_sign_date") == today:
                async for _r in self._say_msg(event, "sign.done", event.plain_result(
                        f"📅 今天已签到　💰 {_fmt_gold(player.get('gold', 0))}"
                    )):
                    yield _r
                return
            reward = int(self.cfg["sign_reward"])
            player["last_sign_date"] = today
            player["gold"] = _safe_int(player.get("gold"), 0, 0) + reward
            new_ach = self._check_achievements(player)
            saved = await self._save_player(player)
        lines = [f"✅ 签到 +{reward}　💰 {_fmt_gold(player['gold'])}"]
        if new_ach:
            lines.append("🎉 " + "；".join(new_ach))
        if not saved:
            lines.append("⚠️ 保存失败")
        async for _r in self._say_msg(event, "sign.result", event.plain_result("\n".join(lines))):
            yield _r

    # =========================================================================
    # 帮助
    # =========================================================================



    # =========================================================================
    # 生命周期
    # =========================================================================

