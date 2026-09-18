# -*- coding: utf-8 -*-
"""子命令实现：背包 / 卖鱼 / 商店 / 图鉴 / 钓点 / 鱼竿 / 水族馆 / 订单 / 签到…

这些方法是从 main.py 原样搬过来的（缩进未变），可以直接引用 main.py 的常量与
工具函数——共享方式是「main 在模块末尾把自己的全局注入本模块」，详见 main.py
顶部的说明。之所以能这样搬，是为了不改动几百处调用点。

⚠️ 维护约定：
  1. 不要在本模块对共享的模块级常量做重新赋值（`X = ...` 只会改到本模块副本），
     需要改数值请在 main.py 的 `_apply_tunable_config()` 里改。
  2. 本模块的方法通过 `self.` 互相调用，跨模块调用也一样。
"""

from __future__ import annotations


class CommandsMixin:
    """子命令实现：背包 / 卖鱼 / 商店 / 图鉴 / 钓点 / 鱼竿 / 水族馆 / 订单 / 签到…（由 FishingPlugin 继承，见 main.py 的类定义）。"""

    async def _cmd_event(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """处理小插曲的选择：/钓鱼 事件 1"""
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            current = player.get("event") or {}
            event_def = EVENT_BY_ID.get(current.get("id", ""))
            if not isinstance(event_def, dict):
                owner, ts = self._recent_events.get(self._session_key(event), ("", 0.0))
                if owner and owner != user_id and time.time() - ts < 600:
                    yield event.plain_result(
                        "🙅 这是别人的动静，你插不上手\n"
                        "　自己下竿的时候才会遇到属于你的小插曲"
                    )
                    return
                yield event.plain_result("🤔 眼下没什么需要你决定的事")
                return
            # 插曲放着不管会自己散掉（10 分钟）
            if int(time.time()) - _safe_int(current.get("ts"), 0, 0) > 600:
                player.pop("event", None)
                await self._save_player(player)
                yield event.plain_result("💨 你犹豫了一会儿，那点动静已经过去了")
                return

            choices = event_def.get("choices") or []
            idx = _to_int(a2, 0)
            if not (1 <= idx <= len(choices)):
                text, _ = self._event_prompt(event_def, rows=False)
                yield event.plain_result(text)
                return

            choice = choices[idx - 1]
            player.pop("event", None)
            lines = [f"　{choice['text']}"]

            # 结算：奖励都很轻，不影响经济
            good = random.random() < 0.65
            if good:
                lines.append(f"　{choice.get('good') or '……'}")
                gold = _safe_int(choice.get("gold"), 0, 0)
                if gold:
                    player["gold"] = _safe_int(player.get("gold"), 0, 0) + gold
                    lines.append(f"　💰 +{_fmt_gold(gold)}")
                bait_count = _safe_int(choice.get("bait"), 0, 0)
                if bait_count:
                    bait_id = "worm"
                    baits = player.setdefault("baits", {})
                    baits[bait_id] = _safe_int(baits.get(bait_id), 0, 0) + bait_count
                    lines.append(f"　{self._bait_label(bait_id)} ×{bait_count}")
                if choice.get("luck"):
                    gain = _safe_number(choice["luck"], 0.0)
                    player["luck_charges"] = _clamp(
                        _safe_number(player.get("luck_charges"), 0.0) + gain, 0.0, 2.0
                    )
                    lines.append(f"　🔮 下一竿手气：{_luck_stars(gain, 0.3)}")
                if choice.get("note"):
                    note = random.choice(BOTTLE_NOTES)
                    notes = player.setdefault("bottle_notes", [])
                    if note not in notes:
                        notes.append(note)
                        player["bottle_notes"] = notes[-30:]
                    lines.append(f"　📜 你记下了一句：{note}")
            else:
                lines.append(f"　{choice.get('idle') or '什么也没发生。'}")

            new_ach = self._check_achievements(player)
            saved = await self._save_player(player)
            yield event.plain_result("\n".join(lines))

    def _order_rarities(self, level: int) -> tuple[str, ...]:
        """按等级取当前可出现的订单品质。"""
        result: tuple[str, ...] = ("常见",)
        for lv, rarities in ORDER_RARITY_BY_LEVEL:
            if level >= lv:
                result = rarities
        return result

    def _roll_orders(self, level: int) -> list[dict[str, Any]]:
        """生成一批订单。越贵的鱼要得越少。"""
        rarities = set(self._order_rarities(level))
        candidates = [f for f in FISH_POOL if f["rarity"] in rarities]
        if not candidates:
            candidates = list(FISH_POOL)
        # 同一批订单不出现重复鱼种
        unique: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for fish in candidates:
            if fish["id"] in seen_ids:
                continue
            seen_ids.add(fish["id"])
            unique.append(fish)
        candidates = unique or candidates
        count = min(int(self.cfg["order_count"]), len(candidates))
        picked = random.sample(candidates, count)

        mult = float(self.cfg["order_reward_mult"])
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
                    "reward": max(1, int(unit * need * mult)),
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


    def _ensure_orders(self, player: dict[str, Any]) -> bool:
        """确保当前这批订单已生成（不定时刷新）。

        刷新规则：到了 `order_next_ts` 就换一批新的，没做完的旧单直接过期。
        返回 True 表示数据有变化需要保存。
        """
        now = int(time.time())
        next_ts = _safe_int(player.get("order_next_ts"), 0, 0)
        has_orders = bool(player.get("orders"))
        if has_orders and next_ts > now:
            return False
        player["orders"] = self._roll_orders(_player_level(player))
        player["order_next_ts"] = self._next_order_ts(now)
        player["order_date"] = self._today_text()  # 只用于展示「这是哪天接的单」
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
                yield event.plain_result(
                    f"📋 订单需要 {int(self.cfg['order_unlock_level'])} 级"
                    f"（你现在 {level} 级，多钓鱼吧）"
                )
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
                    yield event.plain_result(
                        f"📖 /钓鱼 订单 交 <序号>　1~{len(orders)}，"
                        f"支持 1 2 3 / 1-3 / 全部"
                    )
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
                    yield event.plain_result(msg)
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
                yield event.plain_result("\n".join(lines))
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

            lines = [
                f"📋 当前订单（{self._order_wait_text(player)}，"
                f"过期会换一批）"
            ]
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
            yield event.plain_result("\n".join(lines))

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
                    yield event.plain_result("🤔 没有这个钓点，/钓鱼 钓点 看看")
                    return
                if target["id"] not in unlocked:
                    yield event.plain_result(
                        f"🔒 {target['name']} 还没解锁，先 /钓鱼 钓点 解锁 {target['name']}"
                    )
                    return
                if target["id"] == current:
                    yield event.plain_result(f"📍 你已经在 {target['name']} 了")
                    return
                player["current_location"] = target["id"]
                saved = await self._save_player(player)
                lines = [
                    f"🚶 前往 {target['emoji']}{target['name']}　"
                    f"价值×{target['value_mult']:.2f}"
                ]
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            # ---- 解锁 ----
            if sub in ("解锁", "unlock", "开"):
                target = self._find_location(a3 or a2)
                if target is None:
                    yield event.plain_result("🤔 没有这个钓点")
                    return
                if target["id"] in unlocked:
                    yield event.plain_result(f"✅ {target['name']} 已解锁")
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
                    yield event.plain_result(
                        f"🔒 还不能去 {target['emoji']}{target['name']}，还差：\n"
                        + "\n".join(f"　· {x}" for x in lacks)
                        + "\n　（图鉴里的隐藏生物不算数）"
                    )
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
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            # ---- 查看 ----
            lines = [f"🗺️ 钓点　当前 {self._location_label(player)}　等级 {level}"]
            for loc in sorted(
                self.locations,
                key=lambda l: (_safe_int(l.get("level_gate"), 1, 1),
                               _safe_int(l.get("gold_gate"), 0, 0)),
            ):
                is_unlocked = loc["id"] in unlocked
                here = "📍" if loc["id"] == current else "　"
                if is_unlocked:
                    lines.append(
                        f"{here}{loc['emoji']}{loc['name']}　×{loc['value_mult']:.2f}"
                        f"　{loc['desc']}"
                    )
                else:
                    prev_id = self._prev_location_id(loc["id"])
                    if prev_id:
                        prev_cfg = self.location_by_id.get(prev_id) or {}
                        got, need = self._location_codex_progress(player, prev_id)
                        ratio = _clamp(
                            _safe_number(self.cfg.get("location_codex_gate"), 0.8),
                            0.0,
                            1.0,
                        )
                        gate_need = int(need * ratio + 0.999)
                        mark = "✅" if got >= gate_need else "🔒"
                        lines.append(
                            f"{mark}{loc['emoji']}{loc['name']}　图鉴 "
                            f"{got}/{need}（需 {gate_need}）"
                            f"　{_fmt_gold(loc['gold_gate'])}金"
                            f"　{loc['level_gate']}级"
                        )
                    else:
                        lines.append(f"🔒{loc['emoji']}{loc['name']}")
            lines.append("💡 点按钮看图鉴，或写：/钓鱼 去 <钓点名>")
            async for reply in self._say(event, "\n".join(lines), self._location_rows()):
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
                    yield event.plain_result("🤔 没有这款鱼竿")
                    return
                if rod["id"] in owned:
                    yield event.plain_result(f"✅ 你已经有 {rod['name']} 了")
                    return
                price = int(rod["price"])
                if _safe_int(player.get("gold"), 0, 0) < price:
                    yield event.plain_result(
                        f"💸 {rod['name']} 需要 {_fmt_gold(price)} 金币，"
                        f"你只有 {_fmt_gold(player.get('gold', 0))}"
                    )
                    return
                player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                owned.append(rod["id"])
                player["rods"] = owned
                player["equipped_rod"] = rod["id"]
                lines = [
                    f"🎣 买到 {rod['emoji']}{rod['name']}！已自动装备",
                    f"　价值+{rod['value_bonus']:.0%}　手气{_luck_stars(rod['luck_bonus'], 0.2)}"
                    f"　💰 {_fmt_gold(player['gold'])}",
                ]
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            if sub in ("用", "装备", "换", "use", "equip"):
                rod = self._find_rod(a3 or a2)
                if rod is None:
                    yield event.plain_result("🤔 没有这款鱼竿")
                    return
                if rod["id"] not in owned:
                    yield event.plain_result(f"🎒 你还没买 {rod['name']}")
                    return
                player["equipped_rod"] = rod["id"]
                saved = await self._save_player(player)
                lines = [f"✅ 已装备 {self._rod_label(player)}"]
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            lines = [f"🎣 鱼竿　当前 {self._rod_label(player)}"]
            for rod in self.rods:
                here = "📍" if rod["id"] == equipped else "　"
                tag = "已拥有" if rod["id"] in owned else f"{_fmt_gold(rod['price'])}金"
                lines.append(
                    f"{here}{rod['emoji']}{rod['name']}　{tag}　"
                    f"价值+{rod['value_bonus']:.0%}　手气{_luck_stars(rod['luck_bonus'], 0.2)}"
                )
            lines.append("💡 /钓鱼 鱼竿 买 <名称> ｜ /钓鱼 鱼竿 用 <名称>")
            yield event.plain_result("\n".join(lines))

    async def _cmd_backpack_upgrade(self, event: AstrMessageEvent, user_id: str):
        """背包扩容（公开入口，自己加锁）。"""
        async with self._lock_for(user_id):
            async for result in self._do_backpack_upgrade(event, user_id):
                yield result

    async def _do_backpack_upgrade(self, event: AstrMessageEvent, user_id: str):
        """背包扩容的实际逻辑。

        ⚠️ 调用方必须**已经持有该玩家的锁**。这是为了避免
        「/钓鱼 商店 扩容」转发时重复获取同一把 asyncio.Lock 造成自死锁
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
            yield event.plain_result(f"🎒 背包已扩到最大（{capacity}）")
            return

        up = self.backpack_upgrades[nxt]
        price = int(up.get("price", 0))
        if _safe_int(player.get("gold"), 0, 0) < price:
            yield event.plain_result(
                f"💸 扩容 +{up['add']} 需要 {_fmt_gold(price)} 金币，"
                f"你只有 {_fmt_gold(player.get('gold', 0))}"
            )
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
        yield event.plain_result("\n".join(lines))

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
            yield event.plain_result("\n".join(lines))

    async def _cmd_leaderboard(self, event: AstrMessageEvent, user_id: str, a2: str):
        """群内排行榜：/钓鱼 排行 [金币|图鉴|收获|最贵]"""
        index = await self.get_kv_data(self._leaderboard_key(), {})
        if not isinstance(index, dict) or not index:
            yield event.plain_result("📊 还没有排行数据，先去 /钓鱼 抛几竿吧")
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
            yield event.plain_result(f"📊 还没有「{title}」的数据")
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
            lines.append(f"{medal} {name[:12]}　{_fmt_gold(value)} {unit}{extra}{mark}")
        if my_rank is None:
            lines.append("　你还没有上榜，加油！")
        elif my_rank > 10:
            lines.append(f"　你的排名：第 {my_rank} 名")
        lines.append("💡 /钓鱼 排行 金币｜图鉴｜最贵")
        yield event.plain_result("\n".join(lines))

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
                yield event.plain_result(
                    "📖 /钓鱼 锁定 <序号…>　锁定的鱼不会被卖出\n"
                    "　/钓鱼 解锁 <序号…>\n"
                    "　先用 /钓鱼 背包 看序号"
                )
                return
            bad = [i for i in indices if i > len(ordered)]
            if bad:
                yield event.plain_result(
                    f"🤔 序号 {'/'.join(map(str, bad))} 超出 1~{len(ordered)}"
                )
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
        yield event.plain_result("\n".join(lines))

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
                yield event.plain_result("\n".join(lines))
                return

            bad = [i for i in indices if i > len(ordered)]
            if bad:
                yield event.plain_result(
                    f"🤔 序号 {'/'.join(map(str, bad))} 超出 1~{len(ordered)}"
                )
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
        yield event.plain_result("\n".join(lines))


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
        yield event.plain_result("\n".join(lines))

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
            yield event.plain_result(
                f"🎒 背包空空的（容量 {cap}）\n💡 发 /钓鱼 下竿试试手气"
            )
            return

        per_page = 20
        page = max(1, _to_int(a2, 1))
        total_pages = max(1, (len(inventory) + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page

        ordered = sorted(inventory, key=_sort_key)
        value = _inventory_value(inventory)
        lines = [
            f"🎒 背包 {len(inventory)}/{cap} 条 · 总估值 {_fmt_gold(value)} 金币"
            + (f" · 第 {page}/{total_pages} 页" if total_pages > 1 else "")
        ]
        for idx, instance in enumerate(
            ordered[start : start + per_page], start=start + 1
        ):
            mark = "🔒" if instance.get("locked") else "　"
            lines.append(
                f"{idx:>2}.{mark}{_instance_line(instance)}　{_attrs_line(instance)}"
            )
        lines.append("【用法】")
        lines.append("　/钓鱼 卖 1 2 3　按序号卖（可给多个）")
        lines.append("　/钓鱼 卖 鲤鱼　　按鱼名卖光这种鱼")
        lines.append("　/钓鱼 卖光光　　一次清空背包")
        lines.append("　/钓鱼 锁定 1　　 锁定后不会被卖出")
        lines.append("　/钓鱼 水族馆 放 1 2　放进水族馆")
        if total_pages > 1:
            lines.append(f"💡 /钓鱼 背包 {page % total_pages + 1} 看下一页")
        async for reply in self._say(event, "\n".join(lines), self._bag_rows()):
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
                yield event.plain_result("🎒 背包空空的，没东西可卖")
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
                yield event.plain_result(
                    "🧹 「卖 垃圾」这个玩法已经去掉了\n"
                    "　想一次清空背包：/钓鱼 卖光光（锁定的鱼会留下）\n"
                    "　想只卖某种鱼：/钓鱼 卖 鲤鱼（可加数量）\n"
                    "　想按序号卖：/钓鱼 卖 1 2 3"
                )
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
                    yield event.plain_result(
                        f"🤔 序号要在 1~{len(ordered)} 之间（发 /钓鱼 背包 看序号）"
                    )
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
                    yield event.plain_result(
                        f"🤔 没有叫「{first}」的鱼\n"
                        f"　可卖示例：{sample} …\n"
                        f"　也可以按序号卖：/钓鱼 卖 1 2 3"
                    )
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
                    yield event.plain_result(f"🤔 你还没有 {fish['name']}")
                    return
                targets = group if count <= 0 else group[: min(count, len(group))]

            if not targets:
                if sell_all and skipped_locked:
                    yield event.plain_result(
                        f"🔒 背包里 {skipped_locked} 条鱼都锁着，卖光光不会动它们\n"
                        f"　想一起卖：/钓鱼 解锁 1 2 3 之后再 /钓鱼 卖光光"
                    )
                    return
                yield event.plain_result(
                    "🤔 没有可卖的鱼\n"
                    "　可能原因：背包是空的、序号超范围、或这种鱼你还没有\n"
                    "　发 /钓鱼 背包 看背包，或用 /钓鱼 卖光光 一次卖光"
                )
                return

            sold = [(x, price_of(x)[0]) for x in targets]
            bonus = sum(price_of(x)[1] for x in targets)
            player["inventory"] = [
                x for x in inventory if id(x) not in {id(t) for t in targets}
            ]
            async for out in self._finalize_sale(event, player, sold, "💵 卖出", bonus):
                yield out
            if skipped_locked:
                yield event.plain_result(
                    f"🔒 另有 {skipped_locked} 条锁定的鱼留在背包里"
                    f"（/钓鱼 解锁 1 可以解锁）"
                )

    async def _cmd_fish_info(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """查鱼：``/钓鱼 查 鲤鱼`` 或 ``/钓鱼 查 山间湖泊``。

        钓点太多、鱼有 203 种，玩家记不住谁在哪儿——所以这个入口给两件事：
        1) 输入鱼名 → 它在哪些钓点、什么品质、基准价、要不要拉线、自己有没有
        2) 输入钓点名 → 这个钓点有哪些鱼（带基准价，按品质分组）
        """
        name = (a2 or "").strip()
        if not name:
            sample = "、".join(f["name"] for f in FISH_POOL[:5])
            yield event.plain_result(
                "📖 /钓鱼 查 <鱼名 或 钓点名>\n"
                f"　例：/钓鱼 查 鲤鱼　/钓鱼 查 山间湖泊\n"
                f"　常见鱼：{sample} …\n"
                "　不知道名字就发 /钓鱼 图鉴 看进度、/钓鱼 图鉴 详 看完整清单"
            )
            return

        player = await self._load_player(user_id)
        collection = player.get("collection") or {}
        held = _held_counts(player)

        def mine(fish_id: str) -> tuple[int, int, int]:
            entry = collection.get(fish_id)
            total = _safe_int(entry.get("count"), 0, 0) if isinstance(entry, dict) else 0
            best = _safe_int(entry.get("best_value"), 0, 0) if isinstance(entry, dict) else 0
            return total, best, held.get(fish_id, 0)

        # ---- 按钓点查 ----
        loc = self._find_location(name)
        if loc is not None:
            ids = [
                fid
                for fid, w in (LOCATION_WEIGHTS.get(loc["id"]) or {}).items()
                if w > 0 and fid in FISH_BY_ID
            ]
            if not ids:
                yield event.plain_result(f"🐟 {loc['name']} 没有配置鱼种")
                return
            lines = [
                f"{loc['emoji']} {loc['name']}　共 {len(ids)} 种"
                f"　价值×{loc['value_mult']:.2f}"
                f"　需{loc['level_gate']}级"
                + (f"/{_fmt_gold(loc['gold_gate'])}金" if loc["gold_gate"] else "")
            ]
            for rarity in RARITY_ORDER:
                group = sorted(
                    (FISH_BY_ID[fid] for fid in ids if _fish_rarity(fid) == rarity),
                    key=lambda f: f["value"],
                )
                if not group:
                    continue
                lines.append(f"【{self._rarity_name(rarity)}】")
                for fish in group:
                    total, _best, now = mine(fish["id"])
                    mark = "✅" if total > 0 else "❔"
                    lines.append(
                        f"　{mark}{fish['name']}　{_fmt_gold(fish['value'])}金"
                        + (f"　存{now}" if now else "")
                    )
            lines.append("💡 /钓鱼 查 <鱼名> 看它在哪些钓点出现")
            yield event.plain_result("\n".join(lines))
            return

        # ---- 按鱼名查 ----
        fish = self._find_fish_by_name(name)
        if fish is None:
            yield event.plain_result(
                f"🤔 没有叫「{name}」的鱼，也没这个钓点\n"
                "　试试 /钓鱼 图鉴 详 [页码] 看完整鱼名单，"
                "或 /钓鱼 钓点 看钓点列表"
            )
            return
        homes = [
            loc_cfg
            for loc_cfg in self.locations
            if (LOCATION_WEIGHTS.get(loc_cfg["id"]) or {}).get(fish["id"], 0) > 0
        ]
        rarity = fish["rarity"]
        interactive = rarity in self.interactive_rarities
        total, best, now = mine(fish["id"])
        lines = [
            f"{_fish_emoji(fish)} {fish['name']}　{self._rarity_name(rarity)}"
            f"　基准价 {_fmt_gold(fish['value'])} 金币",
            f"　上钩难易：{'要拉线（会跑，手要快）' if interactive else '直接上钩，不用拉线'}",
        ]
        if fish.get("flavor"):
            lines.append(f"　{fish['flavor']}")
        if homes:
            lines.append("　出没钓点：" + "、".join(
                f"{h['emoji']}{h['name']}(×{h['value_mult']:.2f})" for h in homes
            ))
        else:
            lines.append("　出没钓点：暂时没人见到过（隐藏鱼？）")
        if total > 0:
            lines.append(
                f"　我的记录：共 {total} 条　最高卖过 {_fmt_gold(best)}"
                + (f"　背包里还有 {now} 条" if now else "")
            )
        else:
            lines.append("　我的记录：还没钓到过 ❔")
        lines.append("💡 /钓鱼 查 <钓点名> 看那个钓点的全部鱼种")
        yield event.plain_result("\n".join(lines))

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

        # ---- 详 [页码]：完整清单，按品质从低到高、每页 15 种 ----
        if lower in ("详", "详细", "all", "detail") or (len(toks) == 1 and arg.isdigit()):
            page = (
                _to_int(toks[1], 1)
                if lower in ("详", "详细", "all", "detail") and len(toks) > 1
                else (_to_int(arg, 1) if arg.isdigit() else 1)
            )
            ordered = sorted(
                FISH_POOL,
                key=lambda f: (RARITY_RANK.get(f["rarity"], 0), f["value"]),
            )
            per_page = 15
            total_pages = max(1, (len(ordered) + per_page - 1) // per_page)
            page = max(1, min(page, total_pages))
            start = (page - 1) * per_page
            lines = [
                f"📖 鱼种图鉴（详）{len(owned)}/{len(FISH_POOL)}"
                f"　第 {page}/{total_pages} 页"
            ]
            for fish in ordered[start : start + per_page]:
                total, best = entry_of(fish["id"])
                if total > 0:
                    now = held.get(fish["id"], 0)
                    lines.append(
                        f"　{_fish_emoji(fish)}{fish['name']}"
                        f"　{self._rarity_name(fish['rarity'])}"
                        f"　共{total} 最高{_fmt_gold(best)}"
                        + (f" 存{now}" if now else "")
                    )
                else:
                    lines.append(
                        f"　❔ ???　{self._rarity_name(fish['rarity'])}"
                    )
            tail_page = page % total_pages + 1
            lines.append(f"💡 /钓鱼 图鉴 详 {tail_page} 看下一页")
            yield event.plain_result("\n".join(lines))
            return

        # ---- 图鉴 <钓点名>：这个钓点里还差哪些 ----
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
            for fid in ids:
                fish = FISH_BY_ID[fid]
                total, best = entry_of(fid)
                if total > 0:
                    now = held.get(fid, 0)
                    lines.append(
                        f"　{_fish_emoji(fish)}{fish['name']}"
                        f"　{self._rarity_name(fish['rarity'])}"
                        f"　共{total} 最高{_fmt_gold(best)}"
                        + (f" 存{now}" if now else "")
                    )
                else:
                    lines.append(
                        f"　❔ ???　{self._rarity_name(fish['rarity'])}"
                    )
            lines.append("💡 /钓鱼 图鉴 看各钓点总进度")
            yield event.plain_result("\n".join(lines))
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
        yield event.plain_result("\n".join(lines))

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
                yield event.plain_result(self._aquarium_view(player))
                return

            # ---- 扩建 ----
            if sub in ("扩建", "升级", "expand"):
                unlocked = player.setdefault("aquarium_slots", [])
                nxt = next(
                    (s for s in self.aquarium_slots if s["name"] not in unlocked), None
                )
                if nxt is None:
                    yield event.plain_result("🏠 已经扩到最大了")
                    return
                price = int(nxt["price"])
                if _safe_int(player.get("gold"), 0, 0) < price:
                    yield event.plain_result(
                        f"💸 扩建「{nxt['name']}」需要 {_fmt_gold(price)}，金币不足"
                    )
                    return
                player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                unlocked.append(nxt["name"])
                lines = [
                    f"🏠 扩建成功：{nxt['name']}　容量 → {self._aquarium_capacity(player)}",
                    f"💰 余额 {_fmt_gold(player['gold'])}",
                ]
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            # ---- 领取鱼塘挂机收益（按小时累积，与水族馆合并）----
            if sub in ("领", "领取", "收益", "income", "collect"):
                now_ts = int(time.time())
                last = _safe_int(player.get("pond_last_ts"), 0, 0)
                if last <= 0:
                    # 第一次：开始计时
                    player["pond_last_ts"] = now_ts
                    await self._save_player(player)
                    yield event.plain_result(
                        "🏞️ 鱼塘开始计产了！\n"
                        "　每小时产出馆藏估值的 "
                        f"{float(self.cfg['pond_income_per_hour']) * 100:.1f}%，"
                        f"最多累积 {int(self.cfg['pond_income_cap_hours'])} 小时\n"
                        "　过一阵子再来 /钓鱼 水族馆 领"
                    )
                    return

                total_value = _inventory_value(aquarium)
                if total_value <= 0:
                    yield event.plain_result("🐠 水族馆是空的，鱼塘没有产出")
                    return

                hours = min(
                    (now_ts - last) / 3600.0,
                    float(self.cfg["pond_income_cap_hours"]),
                )
                rate = float(self.cfg["pond_income_per_hour"])
                cap_coins = int(self.cfg["pond_income_cap_coins"])
                income = min(int(total_value * rate * hours), cap_coins)
                if income <= 0:
                    wait_min = max(1, int(60 - (now_ts - last) / 60.0))
                    yield event.plain_result(
                        f"⏳ 产出还不够，再等约 {wait_min} 分钟（每小时结算一次）"
                    )
                    return

                player["pond_last_ts"] = now_ts
                player["pond_claimed_ts"] = now_ts
                player["pond_best_income"] = max(
                    _safe_int(player.get("pond_best_income"), 0, 0), income
                )
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + income
                cap_note = "（已达单次上限）" if income >= cap_coins else ""
                lines = [
                    f"🏞️ 鱼塘产出 +{_fmt_gold(income)} 金币{cap_note}",
                    f"　挂机 {hours:.1f} 小时 · 馆藏估值 {_fmt_gold(total_value)}",
                    f"💰 余额 {_fmt_gold(player['gold'])}",
                ]
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            # ---- 投喂（统一走 /钓鱼 用 <道具> <栏位号>）----
            if sub in ("喂", "投喂", "feed"):
                yield event.plain_result(
                    "📖 投喂请用：/钓鱼 用 <道具名> <水族馆栏位号>\n"
                    "　例：/钓鱼 用 高级饲料 1"
                )
                return

            # ---- 放入（支持批量序号：放 1 2 3 / 放 1-3 / 放 全部）----
            if sub in ("放", "放入", "养", "add"):
                ordered = sorted(inventory, key=_sort_key)
                indices = self._parse_indices(spec_text, ordered)
                if not indices:
                    yield event.plain_result(
                        "📖 /钓鱼 水族馆 放 <背包序号…>\n"
                        "　例：/钓鱼 水族馆 放 1　或　放 1 3 5　或　放 1-5\n"
                        f"　背包有 {len(inventory)} 条，水族馆 {len(aquarium)}/{capacity}\n"
                        "　先用 /钓鱼 背包 看序号"
                    )
                    return

                room = capacity - len(aquarium)
                if room <= 0:
                    yield event.plain_result(
                        f"🐠 水族馆已满（{len(aquarium)}/{capacity}）\n"
                        "　可 /钓鱼 水族馆 扩建 扩容，或先 /钓鱼 水族馆 取/卖"
                    )
                    return

                # 先取出要放的鱼（按序号降序 pop，避免索引错位）
                picked: list[dict[str, Any]] = []
                for idx in sorted(indices, reverse=True):
                    picked.append(ordered[idx - 1])
                picked.reverse()

                accepted = picked[:room]
                skipped = len(picked) - len(accepted)
                for instance in accepted:
                    inventory.remove(instance)
                    instance["source"] = "aquarium"
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
                # 缸里的相处结果（机制对玩家不可见，只给现象）
                duel_lines, changed = self._resolve_tank_conflicts(aquarium)
                if changed:
                    player["aquarium"] = aquarium
                    await self._save_player(player)
                lines.extend(duel_lines)
                lines.append(f"　水族馆 {len(aquarium)}/{capacity}")
                if skipped:
                    lines.append(
                        f"　⚠️ 容量不足，{skipped} 条没放进去（先扩建或取出一些）"
                    )
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            # ---- 取出（支持批量序号）----
            if sub in ("取", "取出", "拿", "take"):
                indices = self._parse_indices(spec_text, aquarium)
                if not indices:
                    yield event.plain_result(
                        f"📖 /钓鱼 水族馆 取 <栏位号…>\n"
                        f"　例：/钓鱼 水族馆 取 1　或　取 1 3 5\n"
                        f"　当前水族馆有 {len(aquarium)} 条（发 /钓鱼 水族馆 看栏位）"
                    )
                    return
                got: list[tuple[dict[str, Any], int]] = []
                for idx in sorted(indices, reverse=True):
                    if not (1 <= idx <= len(aquarium)):
                        continue
                    instance = aquarium.pop(idx - 1)
                    gain = self._claim_aquarium_bonus(instance)
                    instance["source"] = "fishing"
                    inventory.append(instance)
                    got.append((instance, gain))
                player["inventory"] = inventory
                saved = await self._save_player(player)
                total_gain = sum(g for _, g in got)
                lines = [f"🎣 取出 {len(got)} 条鱼（估值 +{_fmt_gold(total_gain)}）"]
                for instance, gain in got[:5]:
                    lines.append(
                        f"　{_instance_line(instance)}"
                        + (f"　养大+{_fmt_gold(gain)}" if gain else "　（加成已领过）")
                    )
                if len(got) > 5:
                    lines.append(f"　… 其余 {len(got) - 5} 条已放入背包")
                lines.append(f"🧺 背包 {len(inventory)}/{_backpack_capacity(player, self.cfg)}")
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            # ---- 卖掉馆藏（支持批量序号）----
            if sub in ("卖", "卖出", "sell"):
                indices = self._parse_indices(spec_text, aquarium)
                if not indices:
                    yield event.plain_result(
                        "📖 /钓鱼 水族馆 卖 <栏位号…>\n"
                        "　例：/钓鱼 水族馆 卖 1　或　卖 1 3 5"
                    )
                    return
                discount = float(self.cfg["sell_discount"])
                income = 0
                sold: list[tuple[dict[str, Any], int, int]] = []
                for idx in sorted(indices, reverse=True):
                    if not (1 <= idx <= len(aquarium)):
                        continue
                    instance = aquarium.pop(idx - 1)
                    gain = self._claim_aquarium_bonus(instance)
                    price = max(1, int(_instance_value(instance) * discount))
                    income += price
                    sold.append((instance, price, gain))
                if not sold:
                    yield event.plain_result(f"🤔 没有有效的栏位号（1~{len(aquarium)}）")
                    return
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + income
                player["total_sold"] = _safe_int(player.get("total_sold"), 0, 0) + len(
                    sold
                )
                await self._touch_leaderboard(player)
                lines = [f"💵 卖出馆藏 {len(sold)} 条 → {_fmt_gold(income)} 金币"]
                for instance, price, gain in sold[:5]:
                    lines.append(
                        f"　{_instance_line(instance, with_value=False)} → {_fmt_gold(price)}"
                        + (f"（含展出+{_fmt_gold(gain)}）" if gain else "")
                    )
                if len(sold) > 5:
                    lines.append(f"　… 其余 {len(sold) - 5} 条同上")
                lines.append(f"💰 余额 {_fmt_gold(player['gold'])}")
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            yield event.plain_result(
                "📖 水族馆用法\n"
                "　/钓鱼 水族馆　　　　　　欣赏\n"
                "　/钓鱼 水族馆 放 1　　　 从背包放入\n"
                "　/钓鱼 水族馆 取 1　　　 取回（估值+加成）\n"
                "　/钓鱼 水族馆 卖 1　　　 直接卖（估值+加成）\n"
                "　/钓鱼 用 <道具> 1　　　投喂提升三维\n"
                "　/钓鱼 水族馆 领　　　　 领取每日收益\n"
                "　/钓鱼 水族馆 扩建　　　 花金币扩容"
            )

    def _resolve_tank_conflicts(
        self, aquarium: list[dict[str, Any]]
    ) -> tuple[list[str], bool]:
        """结算水族馆里的「相处结果」：狠角色同缸必有一方出事。

        **刻意不做任何提示**：不告诉玩家哪种鱼凶、也不告诉判定规则，
        只给出结果（水浑了 / 少了一条），让玩家自己摸规律。
        返回 (要追加的文案, 是否改动了缸内内容)。
        """
        lines: list[str] = []
        changed = False
        try:
            # 逐对结算：只要有一方是狠角色，弱者就会被淘汰
            for _ in range(len(aquarium)):
                hostile = [
                    (i, x) for i, x in enumerate(aquarium) if _is_hostile(x.get("fish_id", ""))
                ]
                if not hostile:
                    break
                # 狠角色之间、以及狠角色与邻居之间都可能出事
                i_h, h = hostile[0]
                rival_idx = None
                for j, other in enumerate(aquarium):
                    if j == i_h:
                        continue
                    if _is_hostile(other.get("fish_id", "")) or j in (
                        i_h - 1,
                        i_h + 1,
                    ):
                        rival_idx = j
                        break
                if rival_idx is None:
                    break
                rival = aquarium[rival_idx]
                if _fish_power(h) >= _fish_power(rival):
                    loser, winner = rival, h
                else:
                    loser, winner = h, rival
                aquarium.remove(loser)
                changed = True
                loser_name = _fish_name(loser.get("fish_id", ""))
                lines.append(
                    f"　…缸里有点动静，{_fish_emoji(FISH_BY_ID.get(winner.get('fish_id',''), {}))}"
                    f"{_fish_name(winner.get('fish_id', ''))} 把 "
                    f"{loser_name} 逼到了角落，{loser_name} 没了"
                )
        except Exception as e:  # pragma: no cover
            logger.debug(f"水族馆相处结算失败：{e}")
        return lines, changed



    # =========================================================================
    # 商店 / 道具
    # =========================================================================

    async def _cmd_shop(self, event: AstrMessageEvent, user_id: str, a2: str, a3: str = ""):
        """商店。

        - ``/钓鱼 商店``                    看货架
        - ``/钓鱼 商店 买 <名字>``           买一组（鱼饵按组、道具 1 个）
        - ``/钓鱼 商店 买 <名字> <数量>``     批量买（组数/个数）
        - ``/钓鱼 商店 扩容``                背包扩容
        """
        sub = (a2 or "").strip().lower()
        # 少打空格的容错：`买蚯蚓` / `扩容` 这类粘连写法也能拆开
        peeled_shop = self._peel_action(sub, SHOP_ACTIONS)
        if peeled_shop:
            sub, glued = peeled_shop
            a3 = f"{glued} {a3}".strip()
        spec = self._tokens(a3)
        if spec and spec[0].lower() == sub and len(spec) > 1:
            spec = spec[1:]
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)

            if not sub:
                yield event.plain_result(self._shop_view(player))
                return

            # 「商店 扩容」：转发到背包扩容（调用无锁版本，避免自死锁）
            if sub in ("扩容", "扩建背包", "背包扩容", "鱼篓扩容", "鱼篓"):
                async for result in self._do_backpack_upgrade(event, user_id):
                    yield result
                return

            if sub in ("买", "购买", "buy"):
                if not spec:
                    yield event.plain_result(
                        "📖 /钓鱼 商店 买 <名字> [数量]\n"
                        "　例：/钓鱼 商店 买 蚯蚓（1 个）　买 蚯蚓 20（20 个）\n"
                        "　鱼饵和道具都按「个」买，数量不写就是 1"
                    )
                    return
                name = spec[0]
                times = _to_int(spec[1], 1) if len(spec) > 1 else 1
                # 少打空格的容错：`买 蚯蚓2` 等价于 `买 蚯蚓 2`
                if len(spec) == 1:
                    split_nc = self._split_name_count(name)
                    if split_nc and (
                        self._find_bait(split_nc[0]) or self._find_item(split_nc[0])
                    ):
                        name, times_text = split_nc
                        times = _to_int(times_text, 1)
                times = max(1, min(times, 999))
                bait_id = self._find_bait(name)
                item_id = self._find_item(name)

                if bait_id == "none":
                    yield event.plain_result(
                        "🪝 空钩是免费的，不需要购买\n"
                        "　直接发 /钓鱼 或 /钓鱼 空钩 就能用它下竿"
                    )
                    return
                if bait_id is None and item_id is None:
                    names = "、".join(
                        [self.baits[b]["name"] for b in self._bait_list()]
                        + [i["name"] for i in self.items.values()]
                    )
                    yield event.plain_result(
                        f"🤔 商店里没有「{name}」\n　在售：{names}"
                    )
                    return

                if bait_id is not None:
                    bait = self.baits[bait_id]
                    unit = max(0, int(bait.get("price", 0)))   # 单价：按个卖
                    want = max(1, min(times, 9999))
                    price = unit * want
                    if _safe_int(player.get("gold"), 0, 0) < price:
                        yield event.plain_result(
                            f"💸 金币不足：买 {want} 个需要 {_fmt_gold(price)}，"
                            f"你只有 {_fmt_gold(player.get('gold', 0))}"
                            f"（{_fmt_gold(unit)}/个）"
                        )
                        return
                    amount = want
                    player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                    baits = player.setdefault("baits", {})
                    baits[bait_id] = _safe_int(baits.get(bait_id), 0, 0) + amount
                    player["equipped_bait"] = bait_id
                    saved = await self._save_player(player)
                    lines = [
                        f"🛒 购买 {self._bait_label(bait_id)} ×{amount}"
                        f"（{_fmt_gold(unit)}/个）→ {_fmt_gold(price)} 金币"
                    ]
                else:
                    item = self.items[item_id]
                    price = int(item.get("price", 0)) * times
                    if _safe_int(player.get("gold"), 0, 0) < price:
                        yield event.plain_result(
                            f"💸 金币不足：买 {times} 个需要 {_fmt_gold(price)}，"
                            f"你只有 {_fmt_gold(player.get('gold', 0))}"
                        )
                        return
                    player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                    items = player.setdefault("items", {})
                    items[item_id] = _safe_int(items.get(item_id), 0, 0) + times
                    saved = await self._save_player(player)
                    lines = [
                        f"🛒 购买 {self._item_label(item_id)} ×{times}"
                        f" → {_fmt_gold(price)} 金币",
                        f"　{item['desc']}",
                    ]

                # 统一补上「持有量 + 余额」这两条必要信息
                if bait_id is not None:
                    lines.append("　已装备为当前鱼饵")
                    lines.append(
                        f"　持有 {player['baits'].get(bait_id, 0)} 个"
                        f"　💰 余额 {_fmt_gold(player['gold'])}"
                    )
                else:
                    lines.append(
                        f"　持有 {player['items'].get(item_id, 0)} 个"
                        f"　💰 余额 {_fmt_gold(player['gold'])}"
                    )
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            yield event.plain_result(
                "📖 /钓鱼 商店　　　　　　看货架\n"
                "　/钓鱼 商店 买 <名字> [数量]\n"
                "　/钓鱼 商店 扩容　　　 背包扩容"
            )


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
                    lines.append("　（没有鱼饵，/钓鱼 商店 买 蚯蚓）")
                lines.append("💡 /钓鱼 换饵 蚯蚓　或　/钓鱼 换饵 空钩（不消耗鱼饵）")
                yield event.plain_result("\n".join(lines))
                return

            target = self._find_bait(name)
            if target is None:
                names = "、".join(
                    [self.baits[b]["name"] for b in self._bait_list()]
                    + ["空钩"]
                )
                yield event.plain_result(
                    f"🤔 没有「{name}」这种饵。可以换：{names}"
                )
                return

            if target != "none" and owned_of(target) <= 0:
                yield event.plain_result(
                    f"🎒 你还没有 {self._bait_label(target)}，"
                    f"先去 /钓鱼 商店 买 {self.baits[target]['name']}"
                )
                return

            if target == current:
                yield event.plain_result(
                    f"🎣 当前用的就是 {self._bait_label(target)}"
                    + (f"（还剩 {owned_of(target)} 个）" if target != "none" else "")
                )
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
            yield event.plain_result("\n".join(lines))

    async def _cmd_use_item(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str
    ):
        """使用道具：/钓鱼 用 <道具名> [水族馆栏位序列]

        - 饲料类：需要指定水族馆栏位号，提升那条鱼的三维。
          栏位支持批量：`用 高级饲料 1 2 3` / `用 高级饲料 1-5` / `用 高级饲料 全部`，
          每个栏位消耗 1 个道具，道具用完就停（并在回复里说明）。
        - 洗髓丹（quality_up）：作用在自己身上，购买后自动累积到幸运值。
        """
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
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
                    yield event.plain_result("🎒 没有道具，去 /钓鱼 商店 买")
                    return
                names = "、".join(
                    self._item_label(i) for i, c in owned.items() if _safe_int(c, 0, 0) > 0
                )
                yield event.plain_result(f"📖 /钓鱼 用 <道具> <序列>　你有：{names or '无'}")
                return

            items = player.get("items") or {}
            if _safe_int(items.get(item_id), 0, 0) <= 0:
                yield event.plain_result(
                    f"🎒 没有 {self._item_label(item_id)}，去 /钓鱼 商店 买"
                )
                return

            item = self.items.get(item_id) or {}
            effects = item.get("effects") or {}

            # --- 洗髓丹：提升「下一批鱼」的个体品质幸运 ---
            if _safe_number(effects.get("quality_up"), 0.0) > 0:
                items[item_id] = _safe_int(items.get(item_id), 0, 0) - 1
                gain = _safe_number(effects.get("quality_up"), 0.0)
                player["luck_charges"] = _clamp(
                    _safe_number(player.get("luck_charges"), 0.0) + gain, 0.0, 2.0
                )
                saved = await self._save_player(player)
                lines = [
                    f"🔮 使用 {self._item_label(item_id)}",
                    f"　下一竿手气：{_luck_stars(gain, 0.3)}（储备 {_luck_stars(player['luck_charges'], 0.5)}）",
                ]
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            # --- 饲料类：需要水族馆目标（支持批量栏位）---
            aquarium: list[dict[str, Any]] = player.get("aquarium") or []
            if not aquarium:
                yield event.plain_result("🐠 水族馆是空的，先把鱼放进去养")
                return
            # 不带栏位 = 喂全缸（「喂养对所有鱼生效」）
            if not (a3 or "").strip():
                slots = list(range(1, len(aquarium) + 1))
            else:
                slots = self._parse_indices(a3, aquarium)
            if not slots:
                yield event.plain_result(
                    f"📖 /钓鱼 用 {item.get('name', item_id)} [水族馆栏位]\n"
                    f"　不写栏位就是喂全缸；也可以写 1 2 3 / 1-3"
                )
                return

            stock = _safe_int(items.get(item_id), 0, 0)
            max_uses = int(self.cfg["feed_max_uses"])
            used = 0
            grown: list[str] = []
            skipped_full: list[int] = []
            value_gain = 0
            for idx in slots:
                if stock <= 0:
                    break
                instance = aquarium[idx - 1]
                if _safe_int(instance.get("feed_uses"), 0, 0) >= max_uses:
                    skipped_full.append(idx)
                    continue
                stock -= 1
                used += 1
                _, delta = _apply_feed(instance, effects)
                value_gain += delta
                grown.append(f"　{idx}. {_instance_line(instance, with_value=False)}"
                             f"　{_safe_int(instance.get('feed_uses'), 0, 0)}/{max_uses}")

            if used <= 0:
                if skipped_full:
                    yield event.plain_result(
                        f"🍖 栏位 {'、'.join(str(i) for i in skipped_full)} "
                        f"都已经喂满 {max_uses} 次了"
                    )
                else:
                    yield event.plain_result(f"🎒 没有 {self._item_label(item_id)} 了")
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
            yield event.plain_result("\n".join(lines))

    # =========================================================================
    # 档案 / 签到 / 管理员
    # =========================================================================

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
            "📊 档案",
            f"💰 {_fmt_gold(player.get('gold', 0))}　🎣 {self._rod_label(player)}"
            f"　📍 {self._location_label(player)}",
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
        ]
        luck = _safe_number(player.get("luck_charges"), 0.0)
        if luck > 0:
            lines.append(f"🔮 手气储备 {_luck_stars(luck, 0.5)}")
        yield event.plain_result("\n".join(lines))

    async def _cmd_sign(self, event: AstrMessageEvent, user_id: str):
        today = self._today_text()
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            if player.get("last_sign_date") == today:
                yield event.plain_result(
                    f"📅 今天已签到　💰 {_fmt_gold(player.get('gold', 0))}"
                )
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
        yield event.plain_result("\n".join(lines))

    # =========================================================================
    # 帮助
    # =========================================================================



    # =========================================================================
    # 生命周期
    # =========================================================================

