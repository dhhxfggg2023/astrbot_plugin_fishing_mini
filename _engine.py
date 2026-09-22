# -*- coding: utf-8 -*-
"""钓鱼主流程与结算：抛竿判定、上鱼结算、杂物入账、最佳渔获纪录

这些方法是从 main.py 原样搬过来的（缩进未变），可以直接引用 main.py 的常量与
工具函数——共享方式是「main 在模块末尾把自己的全局注入本模块」，详见 main.py
顶部的说明。之所以能这样搬，是为了不改动几百处调用点。

⚠️ 维护约定：
  1. 不要在本模块对共享的模块级常量做重新赋值（`X = ...` 只会改到本模块副本），
     需要改数值请在 main.py 的 `_apply_tunable_config()` 里改。
  2. 本模块的方法通过 `self.` 互相调用，跨模块调用也一样。
"""

from __future__ import annotations


class EngineMixin:
    """钓鱼主流程与结算：抛竿判定、上鱼结算、杂物入账、最佳渔获纪录（由 FishingPlugin 继承，见 main.py 的类定义）。"""

    def _update_best_records(
        self, player: dict[str, Any], catch: dict[str, Any]
    ) -> None:
        """更新「最佳渔获」记录：按鱼种品质各留一条最高的。

        这是玩家的长期目标之一——图鉴看「有没有」，最佳记录看「有多好」。
        """
        try:
            fish_id = catch.get("fish_id", "")
            rarity = _fish_rarity(fish_id)
            value = _instance_value(catch)
            records = player.setdefault("best_records", {})
            if not isinstance(records, dict):
                records = {}
                player["best_records"] = records
            old = records.get(rarity)
            if not isinstance(old, dict) or value > _safe_int(old.get("value"), 0, 0):
                records[rarity] = {
                    "fish_id": fish_id,
                    "variant": catch.get("variant"),
                    "quality": catch.get("quality", ""),
                    "value": value,
                    "ts": _safe_int(catch.get("ts"), 0, 0),
                }
        except Exception as e:
            logger.debug(f"更新最佳渔获记录失败：{e}")

    def _auto_supply(
        self, player: dict[str, Any], *, times: int = 1, wanted: str = ""
    ) -> tuple[str, list[str]]:
        """下竿前的**自动补给**（v1.18.17，站长要的「用完了自动花钱补 + 自动装备」）。

        干三件事，全部**只花玩家自己的金币**，钱不够就什么都不买（绝不透支）：

        1. **自动挂饵**：玩家从没选过饵（``equipped_bait`` 为空）而背包里有饵时，
           挂上手气最高的那款 —— 自己选过空钩的人（``"none"``）不会被偷偷换掉；
        2. **自动补饵**：当前饵不够这一竿（连钓就是不够 N 竿）时按单价补齐，
           差多少买多少（买得起几个买几个）；
        3. **自动补手气道具**：玩家用 ``/钓鱼 自动 <道具名>`` 指定过的话，
           buff 用光时自动买 1 个并**立刻用上**（不指定就绝不替他买，
           免得一觉醒来被自动买掉一个 1.2 万的玉髓灯）。
           ``buff_casts_left`` / ``buff_floor_casts`` 任意一个还 > 0 时**只买不补的
           那一段整段跳过**（v1.18.33 修：以前只挡了额度检查，buff 生效期间每竿
           都会再买一个玉髓灯）。

        返回 ``(这一竿实际用的饵 id, 要写进结果里的说明行)``。
        """
        cfg = self.cfg
        notes: list[str] = []
        gold = _safe_int(player.get("gold"), 0, 0)
        stock_map = player.setdefault("baits", {})

        def stock_of(bait_id: str) -> int:
            return _safe_int(stock_map.get(bait_id), 0, 0)

        # ---- 1. 自动挂饵（只补「从没选过」的人，不覆盖玩家的选择）----
        # 玩家这一竿**点名**了饵（`/钓鱼 蚯蚓`）时不做自动挂饵：他的意图很明确。
        equipped = player.get("equipped_bait")
        if not isinstance(equipped, str):
            equipped = ""
        if not equipped and not wanted and _cfg_bool(cfg, "auto_equip_bait", True):
            owned = [(bid, stock_of(bid)) for bid in self._bait_list() if bid != "none"]
            owned = [(bid, n) for bid, n in owned if n > 0]
            if owned:
                owned.sort(
                    key=lambda kv: -_safe_number(self.baits[kv[0]].get("luck"), 0.0)
                )
                equipped = owned[0][0]
                player["equipped_bait"] = equipped
                notes.append(
                    f"🎣 自动挂上「{self.baits[equipped]['name']}」"
                    f"（还剩 {stock_of(equipped)} 个；不想用就 /钓鱼 换饵 空钩）"
                )

        # ---- 2. 这一竿用哪种饵 ----
        bait_id = "none"
        if wanted:
            matched = self._find_bait(wanted)
            if matched is not None:
                bait_id = matched
        elif equipped and equipped in self.baits and equipped != "none":
            bait_id = equipped

        # ---- 3. 自动补饵：差多少买多少（连钓按 N 竿算）----
        if bait_id != "none":
            need = max(1, times)
            have = stock_of(bait_id)
            want_more = need - have
            if want_more > 0 and _cfg_bool(cfg, "auto_supply_bait", True):
                price = self._bait_cost(bait_id)
                name = self.baits[bait_id]["name"]
                if price <= 0:
                    want_more = 0  # 免费饵不需要补
                buy = min(want_more, gold // price) if price > 0 else 0
                if buy > 0:
                    cost = buy * price
                    gold -= cost
                    player["gold"] = gold
                    stock_map[bait_id] = have + buy
                    notes.append(
                        f"🛒 自动补货 {buy} 个「{name}」（-{_fmt_gold(cost)} 金，"
                        f"余额 {_fmt_gold(gold)}）"
                    )
                elif want_more > 0:
                    notes.append(
                        f"💸 「{name}」不够了，金币也不够自动补货"
                        f"（缺 {want_more} 个，需 {_fmt_gold(want_more * price)}）"
                    )
            if stock_of(bait_id) <= 0:
                # 补不到（没开自动补给 / 买不起）→ 退回空钩，并说清楚怎么固定
                bait_id = "none"
                if not notes:
                    notes.append(
                        f"🎒 {self._bait_label(equipped or 'none')} 用完了，这一竿改用空钩"
                        f"（/钓鱼 鱼饵 买 可以补货，或 /钓鱼 换饵 空钩 固定用空钩）"
                    )

        # ---- 4. 自动补 + 自动用「钓手 buff」道具（手气 / 品质保底，要玩家先指定用哪一件）----
        auto_item = str(player.get("auto_buff_item") or "").strip()
        auto_effects = ((self.items.get(auto_item) or {}).get("effects") or {}) if auto_item else {}
        _is_caster_item = (
            _safe_number(auto_effects.get("buff_quality"), 0.0) > 0
            or _safe_number(auto_effects.get("quality_floor"), 0.0) > 0
        )
        # buff 还在身上时**什么都不做**：既不买、也不从背包里再吃一个。
        # ⚠️ v1.18.33 修的（站长报「玉髓灯的自动购买消耗异常」）：以前这个判断
        # 只挡着下面那段「每日额度」检查，买/用那一段却在它外面 —— 于是 buff
        # 生效期间**每一竿都会再买一个玉髓灯**（27000 金/竿，顺带把 15 竿的
        # 保底时长一直续着），一觉醒来金币就空了。
        # 手气 buff（buff_casts_left）与品质保底（buff_floor_casts）各算各的竿数，
        # 但都由同一件道具续，所以**任意一种在生效**就等它用完再说。
        _buff_active = (
            _safe_int(player.get("buff_casts_left"), 0, 0) > 0
            or _safe_int(player.get("buff_floor_casts"), 0, 0) > 0
        )
        if auto_item and _buff_active:
            auto_item = ""
        if (
            auto_item
            and _is_caster_item
            and _cfg_bool(cfg, "auto_supply_buff", True)
        ):
            # 每日额度（v1.18.18）：额度用完了就别再自动买了 —— 不然「限额」等于没有，
            # 玩家挂机一整天会一直自动续上手气道具。
            _daily_reset(player, self._today_text())
            buff_left = _daily_left(player, "buff", cfg.get("buff_daily_cast_limit"))
            if buff_left is not None and buff_left <= 0:
                notes.append(
                    f"🌙 今天的手气额度用完了"
                    f"（{_daily_used(player, 'buff')}/"
                    f"{_safe_int(cfg.get('buff_daily_cast_limit'), 0, 0)} 竿），"
                    f"没有自动补给"
                )
                auto_item = ""
        if auto_item:
            item = self.items.get(auto_item)
            effects = (item or {}).get("effects") or {}
            if item and (
                _safe_number(effects.get("buff_quality"), 0.0) > 0
                or _safe_number(effects.get("quality_floor"), 0.0) > 0
            ):
                bag = player.setdefault("items", {})
                have = _safe_int(bag.get(auto_item), 0, 0)
                price = _safe_int(item.get("price"), 0, 0)
                # 每日额度（v1.18.18）：自动补给也只能补到额度用完为止
                left = _daily_left(player, "buff", cfg.get("buff_daily_cast_limit"))
                # 每件道具自己的持续竿数（v1.18.20，`buff_casts=40`）
                grant = max(
                    1,
                    _safe_int(
                        effects.get("buff_casts"),
                        _safe_int(cfg.get("buff_cast_count"), 20, 1),
                        1,
                    ),
                )
                if left is not None:
                    grant = max(0, min(grant, left))
                if have <= 0 and price > 0 and gold >= price and grant > 0:
                    gold -= price
                    player["gold"] = gold
                    bag[auto_item] = 1
                    have = 1
                    notes.append(
                        f"🛒 自动补货 1 个「{item['name']}」"
                        f"（-{_fmt_gold(price)} 金，余额 {_fmt_gold(gold)}）"
                    )
                if have > 0 and grant > 0:
                    bag[auto_item] = have - 1
                    _floor = _safe_number(effects.get("quality_floor"), 0.0)
                    if _floor > 0:
                        # 品质保底类（v1.18.23）：走 buff_floor / buff_floor_casts
                        player["buff_floor_casts"] = grant
                        player["buff_floor"] = _floor
                        _what = f"品质保底「{_quality_label(_floor)[0]}」"
                    else:
                        player["buff_casts_left"] = grant
                        player["buff_quality"] = _safe_number(
                            effects.get("buff_quality"), 0.0
                        )
                        _what = (
                            f"手气 +"
                            f"{_safe_number(effects.get('buff_quality'), 0.0):.0%}"
                        )
                    _daily_add(player, "buff", grant)
                    notes.append(
                        f"🎐 自动用上「{item['name']}」（{_what}，{grant} 竿）"
                    )
                elif grant <= 0:
                    notes.append("🌙 今天的手气额度用完了，没有自动补给")
                elif not notes or notes[-1].find("自动补货 1 个") < 0:
                    notes.append(
                        f"💸 「{item['name']}」用完了，金币不够自动补货"
                        f"（需 {_fmt_gold(price)}）"
                    )
        return bait_id, notes

    async def _do_cast(self, event: AstrMessageEvent, user_id: str, bait_name: str):
        """执行一次抛竿（含互动玩法）。"""
        broadcast_catch: dict[str, Any] | None = None
        cfg = self.cfg

        lock = self._lock_for(user_id)
        if lock.locked():
            async for _r in self._say_msg(event, "cast.busy", event.plain_result("🎣 手上还捏着竿呢，先 /钓鱼 拉 或等它跑掉")):
                yield _r
            return

        async with lock:
            player = await self._load_player(user_id)
            now = time.time()

            # 记录昵称，供排行榜展示
            try:
                name = event.get_sender_name()
                if isinstance(name, str) and name:
                    player["last_name"] = name[:24]
            except Exception:
                pass

            # 记录来源平台，便于排查「QQ 官方机器人」等适配问题
            try:
                player["last_platform"] = str(event.get_platform_name() or "")
                self._recent_platforms[user_id] = player["last_platform"]
            except Exception:
                pass

            # 首次操作时确定今日天气与鱼市行情（全天不变）
            if self._ensure_weather(player) or self._ensure_market(player):
                await self._save_player(player)

            # --- 选鱼饵（含自动挂饵 / 自动补货 / 自动用手气道具，见 _auto_supply）---
            bait_id = "none"
            bait_note = ""
            if bait_name:
                matched = self._find_bait(bait_name)
                if matched is None:
                    names = "、".join(self.baits[b]["name"] for b in self._bait_list())
                    async for _r in self._say_msg(event, "cast.bad_bait", event.plain_result(
                            f"🤔 没有「{bait_name}」这种饵。可买：{names}"
                        )):
                        yield _r
                    return
                bait_name = str(self.baits[matched]["name"])
            bait_id, _supply_notes = self._auto_supply(
                player, times=1, wanted=bait_name
            )
            bait_note = "\n".join(_supply_notes)

            # --- 体力（取代原来的冷却时间：每钓一次 1 点，攒着最多 stamina_max 点）---
            limited = _stamina_enabled(cfg)
            if limited:
                stamina = _refresh_stamina(player, cfg)
                if stamina < 1:
                    wait = _stamina_wait_seconds(player, cfg)
                    cap = _safe_int(cfg.get("stamina_max"), 20, 0)
                    async for _r in self._say_msg(event, "cast.no_stamina", event.plain_result(
                            f"⚡ 体力不够了（0/{cap}），再过 {wait} 秒恢复 1 点\n"
                            f"　体力可以攒着，上限 {cap} 点（/钓鱼 体力 查看）"
                        )):
                        yield _r
                    return

            # --- 费用 ---
            # 鱼饵在下竿时只扣库存（买的时候已经付过钱），不再重复收饵钱；
            # 空钩下竿完全免费（fish_cost 默认 0，站长仍可在配置里开启钓费）。
            total_cost = max(0, _safe_int(cfg["fish_cost"], 0, 0))
            if total_cost and _safe_int(player.get("gold"), 0, 0) < total_cost:
                async for _r in self._say_msg(event, "cast.no_gold", event.plain_result(
                        f"💸 下竿需要 {total_cost} 金币，你只有 "
                        f"{_fmt_gold(player.get('gold', 0))}。可 /钓鱼 签到 或 /钓鱼 卖"
                    )):
                    yield _r
                return

            # --- 背包容量检查（限制无限囤货）---
            backpack_cap = _backpack_capacity(player, cfg)
            if len(player.get("inventory") or []) >= backpack_cap:
                async for _r in self._say_msg(event, "cast.bag_full", event.plain_result(
                        f"🎒 背包满了（{backpack_cap}）！先 /钓鱼 卖 或 /钓鱼 水族馆 放，"
                        f"也可以 /钓鱼 扩建背包"
                    )):
                    yield _r
                return

            # --- 扣费 / 扣饵 / 扣体力（各一次）---
            if total_cost:
                player["gold"] = _safe_int(player.get("gold"), 0, 0) - total_cost
            if limited:
                player["stamina"] = max(0, _safe_int(player.get("stamina"), 0, 0) - 1)

            # ---- 这一竿的结果：中鱼 / 钩上物件 / 空手而归（一竿只出一样）----
            # 顺序见 _roll_cast_outcome：先判中鱼（上鱼率 == 配置的咬钩率 × 钓点系数），
            # 没中鱼才可能钩上杂物；完全免费的空钩不出杂物。
            # 钓点系数 >= 1.0 的地图（默认前 3 张）必出鱼，越深越容易空竿。
            cast_loc = self._location(player)
            outcome, drop = self._roll_cast_outcome(
                bait_id, bait_id != "none" or total_cost > 0, cast_loc.get("id")
            )
            # 扣饵：中鱼 / 钩上杂物照扣；空竿看是哪种空竿 ——
            # 「没咬钩 / 鱼不开口」不扣（consume_bait_on_empty=false 的默认语义），
            # 但「咬了一口又吐掉」是真被咬走了，照扣（不然那句「白搭了」就是假话）
            cast_factor = self._location_hook_factor(cast_loc.get("id"))
            miss_scene, miss_tip, miss_eaten = _miss_flavor(
                bait_id, self._bait_label(bait_id), cast_loc, cast_factor
            )
            if _bait_consumed(
                bait_id=bait_id,
                bait_eaten=outcome != "fish" and miss_eaten,
                got_something=outcome != "nothing",
                every_cast=bool(cfg.get("consume_bait_on_empty")),
            ):
                baits = player.setdefault("baits", {})
                baits[bait_id] = max(0, _safe_int(baits.get(bait_id), 0, 0) - 1)
            if outcome != "fish":
                player["last_fish_time"] = int(now)
                await self._save_player(player)
                if outcome == "item" and drop is not None:
                    player = await self._load_player(user_id)
                    drop_text = await self._apply_collectible(player, drop, user_id)
                    lines = [t for t in (drop_text, bait_note) if t]
                    if lines:
                        async for reply in self._say(
                            event, "\n".join(lines), "cast.junk"
                        ):
                            yield reply
                    # 这一竿没钓到鱼：不计渔获、不进图鉴、不刷新最佳纪录、不播报
                    async for reply in self._maybe_trigger_story(event, user_id):
                        yield reply
                    return
                # 空竿的三种说法与扣饵规则由 _miss_flavor / _bait_consumed 统一决定
                async for _r in self._say(
                    event,
                    miss_tip,
                    miss_scene,
                    values={
                        "鱼饵": self._bait_label(bait_id),
                        "钓点": f"{cast_loc.get('emoji', '')}{cast_loc.get('name', '')}",
                    },
                ):
                    yield _r
                return

            # ---- 抽鱼种（按当前钓点的鱼池 + 今日天气）----
            loc = self._location(player)
            rod = self._rod(player)
            weather = self._weather(player)
            fish = self._roll_species(bait_id, loc["id"], weather)
            # 手气 = 一次性储备 + 玉佩这类「持续 N 竿」的加成，两者**叠加**
            # （来源不同、寿命不同，见 _calc._effective_luck）；
            # 本次抛竿读一次，收尾时消耗（一次性清空 + 玉佩竿数 -1，见 _consume_luck）
            luck = _effective_luck(player, cfg)
            # 品质保底（v1.18.23）：玉佩这类道具让接下来 N 竿「不出垃圾」
            floor = _effective_floor(player, cfg)
            gear_luck = _safe_number(rod.get("luck_bonus"), 0.0)
            player["last_fish_time"] = int(now)
            await self._save_player(player)

            # 鱼竿的「拉线手感」（高阶竿：窗口更长 / 更不容易跑）在这里生效
            spec = self._apply_rod_pull_bonus(self._interaction_window(fish, weather), rod)
            # 变异只在上钩瞬间掷一次；命中后普通鱼也会变成「惊喜」
            variant = self._roll_variant()
            weather_luck = _safe_number((weather or {}).get("luck"), 0.0)
            rod_value = _safe_number(rod.get("value_bonus"), 0.0)
            loc_value = _safe_number(loc.get("value_mult"), 1.0)
            codex_mult = self._codex_mult(player)

            # 低档鱼直接上钩；高档鱼走拉线互动
            if spec is None:
                bait = self.baits.get(bait_id) or {}
                quality_mult = _roll_quality_mult(
                    self.cfg["quality_weights"],
                    bait_luck=_safe_number(bait.get("luck"), 0.0)
                    + gear_luck
                    + weather_luck,
                    extra_luck=luck,
                    cfg=cfg,
                    floor=floor,
                )
                catch = _new_instance(
                    fish["id"],
                    quality_mult,
                    value_bonus=rod_value,
                    location_mult=loc_value,
                    variant=variant,
                    codex_mult=codex_mult,
                )
                rating = None
            else:
                # 把天气/装备/图鉴/变异信息带进互动，供拉线成功后造鱼使用。
                # ⚠️ 手气也必须带进去（v1.18.14 修的）：拉线的鱼（传说/神话）以前只吃
                # 鱼饵+天气+拉线评价，鱼竿手气和玉佩/插曲那份**一点没吃到**，
                # 可收尾时 `_consume_luck()` 照样扣玉佩额度 —— 花了钱没效果。
                spec = dict(spec)
                spec["variant"] = variant
                spec["weather_luck"] = weather_luck
                spec["gear_luck"] = gear_luck
                spec["player_luck"] = luck
                spec["player_floor"] = floor
                spec["rod_value_bonus"] = rod_value
                spec["location_mult"] = loc_value
                spec["codex_mult"] = codex_mult
                # ⚠️ **边产生边 yield**：提示要先送到玩家手里，他才来得及在窗口内
                # 发「拉」。以前这里是先攒成 messages 再一起吐，纯文本平台直接没法拉
                # （见 _interactions._iter_minigame 的说明）。
                result: dict[str, Any] | None = None
                async for kind, payload in self._iter_minigame(
                    event, user_id, fish, bait_id, spec
                ):
                    if kind == "result":
                        result = payload
                    else:
                        yield payload
                result = result or {"catch": None, "rating": "失败", "bonus": 0.0}
                player = await self._load_player(user_id)
                catch = result.get("catch")
                rating = result.get("rating")

            # 收尾消耗手气：一次性储备**一竿即清**（不管玉佩在不在），
            # 玉佩的竿数减 1、减到 0 就把加成一起清掉 —— 两套东西各走各的，不会互相顶替
            _consume_luck(player)
            await self._save_player(player)

            if catch is None:
                # 鱼跑了：扣费已在前面完成（饵已经消耗掉），
                # 但「饵用完了」这类提示必须照说，否则玩家只看到掉钱。
                if bait_note:
                    async for _r in self._say_msg(event, "cast.bait_note", event.plain_result(bait_note)):
                        yield _r
                return

            # 拉线技巧计数（用于成就）
            if rating == "完美":
                player["perfect_pulls"] = (
                    _safe_int(player.get("perfect_pulls"), 0, 0) + 1
                )
            elif rating == "偏差":
                player["clutch_wins"] = _safe_int(player.get("clutch_wins"), 0, 0) + 1

            broadcast_catch = catch
            result_text = self._format_result(
                player, catch, bait_id, total_cost, rating
            )
            if bait_note:
                result_text += f"\n{bait_note}"
            async for reply in self._say(
                event,
                result_text,
                "cast.hit",
                values={
                    "鱼名": str((FISH_BY_ID.get(catch.get("fish_id", "")) or {}).get("name") or ""),
                    "品质": str(catch.get("quality") or ""),
                    "估值": _fmt_gold(_instance_value(catch)),
                    "余额": _fmt_gold(player.get("gold", 0)),
                    "评价": str(rating or ""),
                },
            ):
                yield reply
            await self._finalize_catch(
                event,
                player,
                catch,
                user_id,
                perfect=rating == "完美",
                bait_id=bait_id,
            )

            # --- 偶尔来一段小插曲（触发条件不对外说明）---
            async for reply in self._maybe_trigger_story(event, user_id):
                yield reply

        # --- 群播报（锁外）：变异体 / 传说 / 神话都值得播报 ---
        if cfg["enable_group_broadcast"] and broadcast_catch is not None:
            if broadcast_catch.get("variant") or _fish_rarity(
                broadcast_catch.get("fish_id", "")
            ) in ("传说", "神话"):
                await self._broadcast(event, broadcast_catch)

    async def _do_multi_cast(self, event: AstrMessageEvent, user_id: str, times: int):
        """连钓 N 次（``/钓鱼 10``）。

        与单竿的差别：
          * 体力与鱼饵按**实际钓的次数**一次扣除（不够就整批拒绝，不做半途扣款）
          * 要拉线的鱼（默认 传说/神话）**逐条**弹拉线互动：窗口、最佳点位、评价、
            逃脱判定全走单竿那一套（v1.18.28 起）。站长想回到「连钓求快、一次判定」
            就关掉 ``multi_pull_enabled``，那时按「逃脱率 × ``multi_escape_mult``」判一次
          * 插曲、彩蛋、群播报都不触发 —— 连钓求的是快，不是刷屏
          * 成就 / 里程碑 / 排行榜在最后统一结算一次

        ⚠️ 拉了互动就意味着**整批期间会一直持着这个玩家的锁**：其他子命令要等这批
        走完（没拉的窗口也得等超时）。这是「逐条触发」的代价，站长可以用
        ``multi_cast_max`` 限制一次钓多少竿。
        """
        cfg = self.cfg
        limit = max(1, _safe_int(cfg.get("multi_cast_max"), 20, 1))
        if times > limit:
            async for _r in self._say_msg(event, "cast.multi_limit", event.plain_result(
                    f"🔒 一次最多连钓 {limit} 次（你填的是 {times}）\n"
                    f"　想钓更多就分几次，体力本来就可以攒着"
                )):
                yield _r
            return

        lock = self._lock_for(user_id)
        if lock.locked():
            async for _r in self._say_msg(event, "cast.multi_busy", event.plain_result("🎣 手上还捏着竿呢，先 /钓鱼 拉 或等它跑掉")):
                yield _r
            return

        async with lock:
            player = await self._load_player(user_id)
            now = time.time()

            # 记录昵称与来源平台（与单竿一致，排行榜要用）
            try:
                name = event.get_sender_name()
                if isinstance(name, str) and name:
                    player["last_name"] = name[:24]
            except Exception:
                pass
            try:
                player["last_platform"] = str(event.get_platform_name() or "")
                self._recent_platforms[user_id] = player["last_platform"]
            except Exception:
                pass

            if self._ensure_weather(player) or self._ensure_market(player):
                await self._save_player(player)

            # --- 体力 ---
            limited = _stamina_enabled(cfg)
            cap = _safe_int(cfg.get("stamina_max"), 20, 0)
            if limited:
                stamina = _refresh_stamina(player, cfg)
                if stamina < times:
                    wait = _stamina_wait_seconds(player, cfg)
                    async for _r in self._say_msg(event, "cast.multi_no_stamina", event.plain_result(
                            f"⚡ 体力不够：连钓 {times} 次要 {times} 点，"
                            f"你现在 {stamina}/{cap}\n"
                            f"　再过 {wait} 秒恢复 1 点（体力能攒着，/钓鱼 体力 查看）"
                        )):
                        yield _r
                    return

            # --- 鱼饵：当前装备的那一种（用完会自动补货 / 补不到就退回空钩）---
            bait_id, _supply_notes = self._auto_supply(player, times=times)
            if bait_id != "none":
                owned = _safe_int((player.get("baits") or {}).get(bait_id), 0, 0)
                if owned < times:
                    async for _r in self._say_msg(event, "cast.multi_no_bait", event.plain_result(
                            f"🎒 {self._bait_label(bait_id)}只剩 {owned} 个，"
                            f"连钓 {times} 次要 {times} 个\n"
                            f"　/钓鱼 鱼饵 买 {self.baits[bait_id]['name']} 补货，"
                            f"或先 /钓鱼 {owned} 把这几个用掉"
                        )):
                        yield _r
                    return

            # --- 钓费 / 背包容量 ---
            unit_cost = max(0, _safe_int(cfg["fish_cost"], 0, 0))
            cost_all = unit_cost * times
            if cost_all and _safe_int(player.get("gold"), 0, 0) < cost_all:
                async for _r in self._say_msg(event, "cast.multi_no_gold", event.plain_result(
                        f"💸 连钓 {times} 次要 {_fmt_gold(cost_all)} 金币，"
                        f"你只有 {_fmt_gold(player.get('gold', 0))}"
                    )):
                    yield _r
                return
            bag_cap = _backpack_capacity(player, cfg)
            free = max(0, bag_cap - len(player.get("inventory") or []))
            if free <= 0:
                async for _r in self._say_msg(event, "cast.multi_bag_full", event.plain_result(
                        f"🎒 背包满了（{bag_cap}）！先 /钓鱼 卖 或 /钓鱼 水族馆 放"
                    )):
                    yield _r
                return
            planned = min(times, free)
            truncated = planned < times

            # --- 扣费 / 扣饵 / 扣体力：只按实际开钓的次数扣 ---
            if unit_cost:
                player["gold"] = (
                    _safe_int(player.get("gold"), 0, 0) - unit_cost * planned
                )
            if limited:
                player["stamina"] = max(
                    0, _safe_int(player.get("stamina"), 0, 0) - planned
                )

            loc = self._location(player)
            rod = self._rod(player)
            weather = self._weather(player)
            bait = self.baits.get(bait_id) or {}
            rod_value = _safe_number(rod.get("value_bonus"), 0.0)
            loc_value = _safe_number(loc.get("value_mult"), 1.0)
            codex_mult = self._codex_mult(player)
            weather_luck = _safe_number((weather or {}).get("luck"), 0.0)
            bait_luck = (
                _safe_number(bait.get("luck"), 0.0)
                + _safe_number(rod.get("luck_bonus"), 0.0)
                + weather_luck
            )
            # 连钓里要拉线的鱼：默认**逐条弹拉线互动**（v1.18.28）。
            # 站长想保留「连钓求快、一次判定」的老手感，就把 multi_pull_enabled 关掉。
            multi_pull = _cfg_bool(cfg, "multi_pull_enabled", True)
            # 手气：**每一竿各自结算、各自消耗**（v1.18.9 修的）。
            # 以前整批只消耗 1 竿的玉佩额度 —— 连钓 15 次只掉 1 次 buff，站长报的就是这个。
            # 一次性储备（插曲/彩蛋给的那种）仍然只作用于**第 1 竿**，用完即清。
            buff_before = _safe_int(player.get("buff_casts_left"), 0, 0)
            floor_before = _safe_int(player.get("buff_floor_casts"), 0, 0)

            lines = [f"🎣 连钓 {planned} 次"]
            # 自动补给说明（自动挂饵 / 自动补货 / 自动用手气道具，见 _auto_supply）
            lines.extend(_supply_notes)
            if truncated:
                lines.append(
                    f"⚠️ 背包只剩 {free} 个位置，本次只钓 {planned} 次"
                    f"（体力与鱼饵也只扣 {planned} 份）"
                )
            stats = {"fish": 0, "item": 0, "nothing": 0, "escaped": 0}
            # 空竿里「没咬钩」的那几种（饵还在）；被鱼咬掉的照扣，和单竿同一套规则
            nobite = 0
            gained = 0
            cast_factor = self._location_hook_factor(loc.get("id"))

            for index in range(1, planned + 1):
                # 每一竿都按「当前手气」结算，并按同一规则消耗：
                # 空竿 / 杂物也算一竿，和体力、鱼饵的扣法保持一致
                luck = _effective_luck(player, cfg)
                floor = _effective_floor(player, cfg)
                _consume_luck(player)
                outcome, drop = self._roll_cast_outcome(
                    bait_id,
                    bait_id != "none" or unit_cost > 0,
                    loc.get("id"),
                )
                if outcome == "item" and drop is not None:
                    stats["item"] += 1
                    lines.append(f"{index}. {drop['emoji']} {drop['name']}（杂物）")
                    for extra in self._collect_bookkeeping(player, drop):
                        lines.append(f"　　{extra}")
                    continue
                if outcome != "fish":
                    stats["nothing"] += 1
                    # 和单竿完全同一套说法（以前这里只写「💨 空竿」，
                    # 看着像连钓的鱼从来不吃饵）
                    _m_scene, miss_tip, miss_eaten = _miss_flavor(
                        bait_id, self._bait_label(bait_id), loc, cast_factor
                    )
                    if not miss_eaten:
                        nobite += 1
                    lines.append(f"{index}. {miss_tip}")
                    continue

                fish = self._roll_species(bait_id, loc["id"], weather)
                # 鱼竿的「拉线手感」照样算进去（口径与单竿一致）
                spec = self._apply_rod_pull_bonus(
                    self._interaction_window(fish, weather), rod
                )
                # 变异只在上钩瞬间掷一次：拉线那条路径也要带着它（单竿同款顺序）
                variant = self._roll_variant()

                if spec is not None and not multi_pull:
                    # 关掉「连钓弹拉线」时的老行为：不弹互动，按
                    # 「鱼种逃脱率 × 没亲自拉线的惩罚」一次性判定
                    # （惩罚系数 multi_escape_mult，默认 2.0 —— 不然连钓里的
                    #   传说鱼几乎不会跑，比单竿还稳）
                    # ⚠️ 这里**必须是 elif**（下面那支才是弹互动）：写成两个独立 if 的话，
                    #   判定侥幸通过的鱼会接着掉进互动分支，等于开关没关掉 —— 踩过。
                    escape = _multi_escape_chance(spec, cfg)
                    if random.random() < escape:
                        stats["escaped"] += 1
                        lines.append(
                            f"{index}. 💨 {_fish_emoji(fish)}{fish['name']} 跑了"
                            f"（{self._rarity_name(fish['rarity'])}，"
                            f"连钓不拉线，逃脱率 {escape:.0%}）"
                        )
                        continue

                elif spec is not None:
                    # ---- 连钓里的拉线互动（v1.18.28）----
                    # 要拉线的鱼**逐条**弹互动：窗口 / 最佳点位 / 评价 / 逃脱判定
                    # 全走单竿那一套（`_play_minigame`），所以「连钓赌手速」和
                    # 「单竿赌手速」现在是同一件事，不再是一条捷径。
                    #
                    # ⚠️ 提示必须**边产生边 yield**（和单竿走同一个 `_iter_minigame`），
                    # 不能先攒成 messages 再一次性吐出去 —— 那样玩家要等窗口走完才
                    # 看到「咬钩了」，非 QQ 官方（提示走纯文本）的平台直接没法拉，
                    # 连钓更惨：整批都卡在这儿。
                    spec = dict(spec)
                    spec["variant"] = variant
                    spec["weather_luck"] = weather_luck
                    spec["gear_luck"] = _safe_number(rod.get("luck_bonus"), 0.0)
                    spec["player_luck"] = luck
                    spec["player_floor"] = floor
                    spec["rod_value_bonus"] = rod_value
                    spec["location_mult"] = loc_value
                    spec["codex_mult"] = codex_mult
                    # 窗口开头这段收到的「拉」丢掉：连钓自动接续，上一条的余震
                    # （连点 / 消息重投）会撞在这条刚弹出来的瞬间（见常量的说明）
                    spec["min_reaction"] = MULTI_PULL_MIN_REACTION
                    # 连钓的「拉」不回「✅ 收到，正在收线…」（一条一条拉，回执就是刷屏）
                    spec["quiet_ack"] = True
                    result: dict[str, Any] | None = None
                    async for kind, payload in self._iter_minigame(
                        event, user_id, fish, bait_id, spec
                    ):
                        if kind == "result":
                            result = payload
                        else:
                            yield payload
                    result = result or {
                        "catch": None,
                        "rating": "失败",
                        "bonus": 0.0,
                    }
                    catch = result.get("catch")
                    rating = str(result.get("rating") or "失败")
                    # 拉线技巧计数（成就「完美一拉」等）与单竿同一套
                    if rating == "完美":
                        player["perfect_pulls"] = (
                            _safe_int(player.get("perfect_pulls"), 0, 0) + 1
                        )
                    elif rating == "偏差":
                        player["clutch_wins"] = (
                            _safe_int(player.get("clutch_wins"), 0, 0) + 1
                        )
                    if catch is None:
                        stats["escaped"] += 1
                        lines.append(
                            f"{index}. 💨 {_fish_emoji(fish)}{fish['name']} 跑了"
                            f"（{self._rarity_name(fish['rarity'])}，{rating}）"
                        )
                        continue
                    self._record_catch(player, catch)
                    stats["fish"] += 1
                    value = _instance_value(catch)
                    gained += value
                    lines.append(
                        f"{index}. {_fish_emoji(fish)}{fish['name']}"
                        + (" ✨变异" if catch.get("variant") else "")
                        + f" {self._rarity_name(fish['rarity'])} {_fmt_gold(value)}金"
                        + f"　{rating}"
                    )
                    continue

                quality_mult = _roll_quality_mult(
                    self.cfg["quality_weights"],
                    bait_luck=bait_luck,
                    extra_luck=luck,
                    cfg=cfg,
                    floor=floor,
                )
                catch = _new_instance(
                    fish["id"],
                    quality_mult,
                    value_bonus=rod_value,
                    location_mult=loc_value,
                    variant=variant,
                    codex_mult=codex_mult,
                )
                if catch is None:
                    stats["nothing"] += 1
                    lines.append(f"{index}. 💨 空竿")
                    continue

                self._record_catch(player, catch)
                stats["fish"] += 1
                value = _instance_value(catch)
                gained += value
                lines.append(
                    f"{index}. {_fish_emoji(fish)}{fish['name']}"
                    + (" ✨变异" if variant else "")
                    + f" {self._rarity_name(fish['rarity'])} {_fmt_gold(value)}金"
                )

            # --- 统一结算：成就 / 里程碑 / 存档 / 排行榜 ---
            # 扣饵：中鱼 / 杂物 / 被鱼咬掉的空竿都扣；只有「没咬钩」的空竿不扣
            # （consume_bait_on_empty=true 时恢复「每竿都扣」的旧规则）。
            if bait_id == "none":
                consumed = 0
            elif bool(cfg.get("consume_bait_on_empty")):
                consumed = planned
            else:
                consumed = planned - nobite
            if consumed > 0:
                baits = player.setdefault("baits", {})
                baits[bait_id] = max(0, _safe_int(baits.get(bait_id), 0, 0) - consumed)
            new_ach = self._check_achievements(player)
            milestone = self._milestone_text(player)
            saved = await self._save_player(player)
            await self._touch_leaderboard(player)

            summary = (
                f"——————\n"
                f"✅ 上鱼 {stats['fish']} 条｜空竿 {stats['nothing']} 次"
                f"｜杂物 {stats['item']} 个"
            )
            if stats["escaped"]:
                summary += f"｜跑掉 {stats['escaped']} 条"
            lines.append(summary)
            if bait_id != "none" and consumed != planned:
                lines.append(
                    f"{self._bait_label(bait_id)} 本次 -{consumed}"
                    f"（没咬钩的空竿不耗饵：本可扣 {planned}）"
                )
            tail = f"🧮 渔获估值 {_fmt_gold(gained)} 金"
            if limited:
                tail += (
                    f"　⚡ 体力 "
                    f"{_refresh_stamina(player, cfg)}/{cap}"
                )
            lines.append(tail)
            # 玉佩：本批消耗了几竿、还剩几竿（以前这里什么都不写，玩家只能自己数）
            if buff_before > 0:
                left_now = _safe_int(player.get("buff_casts_left"), 0, 0)
                used_now = max(0, buff_before - left_now)
                lines.append(
                    f"🎐 锦鲤玉佩：本批 -{used_now} 竿"
                    + (f"　还剩 {left_now} 竿" if left_now > 0 else "　（用完了）")
                )
            # 品质保底（v1.18.23）：同一套「本批 -N 竿」的写法
            if floor_before > 0:
                floor_left_now = _safe_int(player.get("buff_floor_casts"), 0, 0)
                floor_used = max(0, floor_before - floor_left_now)
                lines.append(
                    f"🧿 品质保底：本批 -{floor_used} 竿"
                    + (
                        f"　还剩 {floor_left_now} 竿"
                        if floor_left_now > 0
                        else "　（用完了）"
                    )
                )

            async for _r in self._say_msg(event, "cast.multi_summary", event.plain_result("\n".join(lines))):
                yield _r
            if new_ach:
                async for _r in self._say_msg(event, "cast.multi_achievement", event.plain_result("🎉 " + "；".join(new_ach))):
                    yield _r
            if milestone:
                async for _r in self._say_msg(event, "cast.multi_milestone", event.plain_result(milestone)):
                    yield _r
            if not saved:
                async for _r in self._say_msg(event, "cast.multi_save_failed", event.plain_result(
                        "⚠️ 数据保存失败，这批渔获可能不会保留（请把这条消息发给管理员核对）"
                    )):
                    yield _r

    def _record_catch(self, player: dict[str, Any], catch: dict[str, Any]) -> None:
        """渔获入账（不含成就/存档）：背包、累计、图鉴、变异计数、最佳纪录。

        单竿（`_finalize_catch`）与连钓（`_do_multi_cast`）共用，
        避免两条路径的记账逻辑各写一份、日子久了长歪。
        """
        player.setdefault("inventory", []).append(catch)
        player["total_caught"] = _safe_int(player.get("total_caught"), 0, 0) + 1

        # 图鉴：变异体是独立条目（fish_id#variant）
        variant = catch.get("variant")
        codex_key = _codex_key(catch["fish_id"], variant)
        collection = player.setdefault("collection", {})
        entry = collection.get(codex_key)
        if not isinstance(entry, dict):
            entry = {"count": 0, "best_value": 0, "first_ts": catch["ts"]}
            collection[codex_key] = entry
        entry["count"] = _safe_int(entry.get("count"), 0, 0) + 1
        entry["best_value"] = max(
            _safe_int(entry.get("best_value"), 0, 0), _instance_value(catch)
        )
        if not _safe_int(entry.get("first_ts"), 0, 0):
            entry["first_ts"] = catch["ts"]

        # 变异计数
        if variant:
            variants = player.setdefault("variants", {})
            variants[variant] = _safe_int(variants.get(variant), 0, 0) + 1

        # 最佳渔获记录（长期目标：不断刷新自己的纪录）
        self._update_best_records(player, catch)

    async def _finalize_catch(
        self,
        event: AstrMessageEvent,
        player: dict[str, Any],
        catch: dict[str, Any],
        user_id: str,
        perfect: bool = False,
        bait_id: str = "none",
    ) -> None:
        """把渔获写进背包 / 图鉴 / 成就并保存。"""
        try:
            self._record_catch(player, catch)

            # 奇遇事件（低概率小惊喜，不影响平衡）
            # 先结算彩蛋再查成就，这样「意外之喜」能在当竿立刻解锁
            egg = self._roll_easter_egg()
            egg_text = self._apply_easter_egg(player, egg, bait_id) if egg else ""
            if egg_text:
                coll = player.setdefault("collectibles", {})
                key = f"egg_{egg['id']}"
                coll[key] = _safe_int(coll.get(key), 0, 0) + 1

            new_achievements = self._check_achievements(player, catch)
            variant = catch.get("variant")
            if perfect and "perfect_pull" not in player["achievements"]:
                player["achievements"].append("perfect_pull")
                new_achievements.append(ACHIEVEMENTS["perfect_pull"])
            if variant and "first_variant" not in player["achievements"]:
                player["achievements"].append("first_variant")
                new_achievements.append(ACHIEVEMENTS["first_variant"])
            if (
                variant == "prismatic"
                and "prismatic_one" not in player["achievements"]
            ):
                player["achievements"].append("prismatic_one")
                new_achievements.append(ACHIEVEMENTS["prismatic_one"])

            # 里程碑：整十/整百竿的额外小奖励文案
            milestone = self._milestone_text(player)

            saved = await self._save_player(player)
            # 更新群内排行榜索引
            await self._touch_leaderboard(player)

            if new_achievements:
                await self._push(
                    event,
                    "cast.achievement",
                    "🎉 " + "；".join(new_achievements),
                    values={"列表": "；".join(new_achievements)},
                )
            if egg_text:
                await self._push(event, "cast.egg", egg_text)
            if milestone:
                await self._push(event, "cast.milestone", milestone)
            if not saved:
                await self._push(
                    event,
                    "cast.save_failed",
                    "⚠️ 数据保存失败，这条记录可能不会保留\n"
                    "　请把这条消息发给管理员核对（日志里有详情）",
                )
        except Exception as e:
            logger.error(f"写入渔获失败（玩家 {user_id}）：{e}", exc_info=True)

    def _collect_bookkeeping(
        self, player: dict[str, Any], drop: dict[str, Any]
    ) -> list[str]:
        """杂物记账（不含成就与存档），返回要追加的提示行。

        单竿走 `_apply_collectible`（再补成就与存档），连钓在循环里只记账、
        最后统一结算——两条路径共用这一份规则，避免各写一份。
        """
        items = player.setdefault("items", {})
        items[drop["id"]] = _safe_int(items.get(drop["id"]), 0, 0) + 1
        coll = player.setdefault("collectibles", {})
        coll[drop["id"]] = _safe_int(coll.get(drop["id"]), 0, 0) + 1

        lines: list[str] = []
        if drop["id"] == "drift_bottle":
            if random.random() < float(self.cfg["bottle_note_chance"]):
                note = random.choice(BOTTLE_NOTES)
                notes = player.setdefault("bottle_notes", [])
                if note not in notes:
                    notes.append(note)
                    player["bottle_notes"] = notes[-30:]
                    lines.append(f"📜 瓶里有张纸条：{note}")
                else:
                    lines.append(f"📜 又是这张纸条：{note}")
            else:
                lines.append("📜 摇了摇……瓶子是空的。")
        elif _safe_int(drop.get("value"), 0, 0) >= 25:
            # 值钱的杂物直接折算成金币，省得再手动卖
            gold = int(drop["value"])
            player["gold"] = _safe_int(player.get("gold"), 0, 0) + gold
            lines.append(f"💰 这东西值钱，直接换了 {_fmt_gold(gold)} 金币")
        return lines

    async def _apply_collectible(
        self, player: dict[str, Any], drop: dict[str, Any], user_id: str
    ) -> str:
        """处理钓上来的杂物：记账、漂流瓶开纸条、值钱的直接折算金币。

        现在杂物与鱼互斥（一竿只出一样），所以文案要明确「这一竿上来的
        不是鱼」，别让玩家以为同时还钓到了鱼。
        """
        try:
            lines = [
                f"{drop['emoji']} 钩子空了，倒是带上来一个 {drop['name']}（这一竿没有鱼）",
                f"　{drop['desc']}　已收进杂物收藏",
            ]
            lines.extend(self._collect_bookkeeping(player, drop))

            new_ach = self._check_achievements(player)
            await self._save_player(player)
            if new_ach:
                lines.append("🎉 " + "；".join(new_ach))
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"处理杂物失败（玩家 {user_id}）：{e}", exc_info=True)
            return ""

