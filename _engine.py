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
        self, player: dict[str, Any], *, times: int = 1, wanted: str = "",
        batch: bool = False,
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
            # ⚠️ 限定饵（深渊秘饵 / 贵客饵）**在货架上是买不到的，所以 baits 库存永远是 0** ——
            #    它到底还能用几次，看的是**凭证道具的剩余次数**（v1.18.70 修：
            #    以前这里读 0，于是刚抽到就被判「用完了」，白白退回空钩/普通饵）。
            if self._bait_is_limited(bait_id):
                return self._limited_bait_left(player, bait_id)
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
        # ⚠️ v1.18.70：**限定饵凭证优先**。抽到「深渊秘饵 / 贵客饵」的凭证后，
        #    只要它还有次数，这一竿就用它那种特权饵 —— 抽到就能用，不用手动装备
        #    （和限定竿一个口径）。凭证用完自动回落到玩家装备的普通饵。
        bait_id = "none"
        _limited_bait = self._active_limited_bait(player)
        if _limited_bait:
            bait_id = _limited_bait
        elif wanted:
            matched = self._find_bait(wanted)
            if matched is not None:
                bait_id = matched
        elif equipped and equipped in self.baits and equipped != "none":
            bait_id = equipped

        # ---- 3. 自动补饵：差多少买多少（连钓按 N 竿算）----
        # ⚠️「限定饵」**永远不自动买**（它根本买不到）：差货时把它换成玩家**能买到的**
        #    那款普通饵再补货（站长：「这种限定鱼饵不能自动购买补饵，应该自动补换
        #    之前的可以购买的鱼饵」）。换完之后这一竿就按普通饵钓，凭证不扣。
        if bait_id != "none" and self._bait_is_limited(bait_id):
            # ⚠️ v1.18.81：**只在「一次都抛不了」时才换掉**。以前按 `times` 判断，
            #    于是「凭证剩 2 次 + 连钓 5 竿」也被判定为差货、当场换成普通饵 ——
            #    凭证白抽（审计实测：修复后这条用例从「不该被拦」变成被拦）。
            #    够不够整批由 `_do_multi_cast` 如实告知（那是玩家能看懂的地方），
            #    自动补给这里只负责「有没有得用」。
            if int(stock_of(bait_id) or 0) <= 0:
                fallback = self._purchasable_fallback_bait(equipped or wanted, player, strict=True)
                notes.append(
                    f"🕳️ 「{self.baits[bait_id]['name']}」是抽到的限定饵，买不到；"
                    + (
                        f"自动改用「{self.baits[fallback]['name']}」"
                        if fallback else "这一竿改用空钩"
                    )
                )
                bait_id = fallback or "none"

        if bait_id != "none" and not self._bait_is_limited(bait_id):
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
        notes.extend(self._auto_supply_buff(player))
        return bait_id, notes

    @staticmethod
    def _buff_active(player: dict[str, Any]) -> bool:
        """身上还有手气 buff 或品质保底吗（两种各算各的竿数，任意一种在就走 True）。"""
        return (
            _safe_int(player.get("buff_casts_left"), 0, 0) > 0
            or _safe_int(player.get("buff_floor_casts"), 0, 0) > 0
        )

    def _auto_supply_buff(self, player: dict[str, Any]) -> list[str]:
        """自动补 + 自动用「钓手 buff」道具（手气 / 品质保底）。

        玩家得先用 ``/钓鱼 自动 <道具名>`` 指定用哪一件（不指定就绝不替他买，
        免得一觉醒来被自动买掉一个 1.2 万的玉髓灯）。三条规矩：

        * **buff 还在身上就什么都不做** —— 既不买、也不从背包里再吃一个
          （v1.18.33 修：以前这个判断只挡着额度检查，于是每竿都再买一个玉髓灯）；
        * **每日额度用完了也不买**（不限额的话「挂机一整天」等于手气常驻）；
        * 买得起才买（绝不透支），买完立刻用上。

        连钓里**中途**也会调一次：整批只在开头补一次的话，「15 竿」的玉髓灯
        在 ``/钓鱼 20`` 的最后 5 竿就断了（v1.18.37 修，见 ``_do_multi_cast``）。

        返回要写进结果里的说明行（没有就空列表）。
        """
        cfg = self.cfg
        auto_item = str(player.get("auto_buff_item") or "").strip()
        item = self.items.get(auto_item) if auto_item else None
        effects = (item or {}).get("effects") or {}
        if not (
            item
            and (
                _safe_number(effects.get("buff_quality"), 0.0) > 0
                or _safe_number(effects.get("quality_floor"), 0.0) > 0
            )
            and _cfg_bool(cfg, "auto_supply_buff", True)
            and not self._buff_active(player)
        ):
            return []

        notes: list[str] = []
        # 每日额度（v1.18.18）：额度用完了就别再自动买了 —— 不然「限额」等于没有，
        # 玩家挂机一整天会一直自动续上手气道具。
        _daily_reset(player, self._today_text())
        left = _daily_left(player, "buff", cfg.get("buff_daily_cast_limit"))
        if left is not None and left <= 0:
            notes.append(
                f"🌙 今天的手气额度用完了"
                f"（{_daily_used(player, 'buff')}/"
                f"{_safe_int(cfg.get('buff_daily_cast_limit'), 0, 0)} 竿），"
                f"没有自动补给"
            )
            return notes

        bag = player.setdefault("items", {})
        have = _safe_int(bag.get(auto_item), 0, 0)
        price = _safe_int(item.get("price"), 0, 0)
        gold = _safe_int(player.get("gold"), 0, 0)
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
            notes.append(f"🎐 自动用上「{item['name']}」（{_what}，{grant} 竿）")
        elif grant <= 0:
            notes.append("🌙 今天的手气额度用完了，没有自动补给")
        elif not notes or notes[-1].find("自动补货 1 个") < 0:
            notes.append(
                f"💸 「{item['name']}」用完了，金币不够自动补货"
                f"（需 {_fmt_gold(price)}）"
            )
        return notes

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
            # ⚠️ strict=True：这一竿接下来会 _save_player，绝不能拿「读失败的空账号」
            # 去写回 —— 那会把玩家的金币/背包/图鉴整个清空（实测：一次瞬时读失败
            # 就能把 50000 金币 + 20 条鱼变成初始值）。读失败就中止，什么都不写。
            try:
                player = await self._load_player(user_id, strict=True)
            except PlayerLoadError as e:
                logger.error(f"抛竿中止（玩家 {user_id} 存档读取失败）：{e}")
                async for _r in self._say_msg(
                    event,
                    "cast.load_failed",
                    event.plain_result(
                        "😵 读取你的存档失败了（可能是数据库正忙）。"
                        "这一竿没有扣任何东西，稍后再试一次就好。"
                    ),
                ):
                    yield _r
                return
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

            # --- 选鱼饵（只解析「点名的那种饵」，不花钱）---
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

            # --- 体力（取代原来的冷却时间：每钓一次 1 点，攒着最多 stamina_max 点）---
            # ⚠️ 必须排在**自动补给之前**：`_auto_supply`（下一段）会花钱 —— 补饵、
            # 自动买手气道具。以前它在体力检查前面，于是没体力的那一竿会先被悄悄扣
            # 一笔钱、然后直接 return，扣款提示一个字都不显示。
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

            # --- 自动挂饵 / 自动补货 / 自动用手气道具（见 _auto_supply）---
            bait_id, _supply_notes = self._auto_supply(
                player, times=1, wanted=bait_name
            )

            # ⚠️ v1.18.81：**先算「这一竿生效哪件凭证」，再扣次数** —— 顺序反了的话
            #    「凭证只剩 1 次」那一竿扣完就失效了，玩家付了次数却吃不到特权
            #    （审计实测：双尾竿剩 1 次只出 1 条、潮汐剩 1 次只掷 1 次）。
            _lim_active = self._active_limited_item_ids(player)

            # --- v1.18.63：扣「限用道具」一次的用量（大鱼乐抽到的体验版竿/秘饵）---
            # 这些道具买不到（uses > 0 不进商店），抽到就直接生效；每抛一竿消耗 1 次，
            # 用完就消失。备注拼进 bait_note（收尾时随结果一起说，见下面的 cast.bait_note /
            # 结果正文）—— 不额外多发一条消息，免得白白吃掉被动回复的次数。
            # ⚠️ v1.18.81：只扣**生效的那两件**（竿一件 + 饵一件），不扫全口袋
            #    （以前一竿能把 6 件凭证一起扣掉）。
            _limited_notes = _use_limited_items(player, self.items, _lim_active)
            bait_note = "\n".join(_supply_notes + _limited_notes)

            # --- 限定竿/饵的**特权**（v1.18.63）---
            # 竿：异色多掷 / 每 N 竿一次神品 / 拉线必完美 / 一竿两条
            # 饵：全图鱼口 / 只抽传说
            # 全部由「当前生效的那根竿 / 那种饵」决定 —— 抽到凭证就有次数，用完自动失效。
            _rod_now = self._rod(player)
            _rod_fx = _rod_now.get("special") if isinstance(_rod_now.get("special"), dict) else {}
            _bait_now = self.baits.get(bait_id) or {}
            _bait_fx = _bait_now.get("special") if isinstance(_bait_now.get("special"), dict) else {}
            # 神品节流：每 myth_every 竿才给一次（计数存在玩家身上，跨竿持续）
            _myth_every = max(0, int(_safe_number(_rod_fx.get("myth_every"), 0.0)))
            _myth_hit = False
            if _myth_every > 0:
                _cast_no = _safe_int(player.get("limited_rod_casts"), 0, 0) + 1
                if _cast_no >= _myth_every:
                    _cast_no = 0
                    _myth_hit = True
                player["limited_rod_casts"] = _cast_no
            elif "limited_rod_casts" in player:
                player.pop("limited_rod_casts", None)

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
                    # ⚠️ 这里**不要**再 _load_player 一次：上面的 _save_player 刚把
                    # 内存里的 player 落过盘，重读只会多开一条「读失败 -> 空账号覆盖」
                    # 的路（而且重读失败还会丢掉这次杂物）。直接用内存里这份即可。
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
            rod = _rod_now
            weather = self._weather(player)
            # 限定饵的「全图鱼口 / 只抽传说」在这里生效（都没配就是老行为）
            fish = self._roll_species(
                bait_id, loc["id"], weather,
                all_pool=_safe_number(_bait_fx.get("all_pool"), 0.0) > 0,
                legend_only=_safe_number(_bait_fx.get("legend_only"), 0.0) > 0,
            )
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
            # ⚠️ 限定竿「异色猎手」（v1.18.63）：多掷 N 次取第一个命中 ——
            #    概率从 p 提到 1-(1-p)^(N+1)，但**仍是看脸**，不是必出。
            variant = self._roll_variant()
            for _ in range(max(0, int(_safe_number(_rod_fx.get("variant_extra"), 0.0)))):
                if variant:
                    break
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
                if _myth_hit:
                    # 限定竿「星陨」：这一竿的个体品质直接是神品（第 70 百分位）
                    quality_mult = _myth_quality_mult()
                    bait_note = (bait_note + "\n" if bait_note else "") + "☄️ 星陨之赐：这一竿必出神品"
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
                # 限定竿「瞬手」：拉线必判完美（见 _judge_pull）
                if _safe_number(_rod_fx.get("perfect"), 0.0) > 0:
                    spec["perfect_pull"] = 1.0
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
                # ⚠️ 同样不重读（见上面杂物分支的说明）：拉线期间内存里的 player
                # 就是权威状态，重读只是多一条会把存档读成空账号的通道。
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

            # --- 限定竿「双尾」：一次成功上钩算两条（v1.18.63）---
            # 第二条走**和第一条完全一样的**记录路径（图鉴/成就/里程碑/最佳纪录都认），
            # 背包满了就只留一条（并说清楚），免得把背包撑爆。
            if _safe_number(_rod_fx.get("double"), 0.0) > 0:
                _cap = _backpack_capacity(player, cfg)
                if len(player.get("inventory") or []) < _cap:
                    _q2 = _roll_quality_mult(
                        self.cfg["quality_weights"],
                        bait_luck=_safe_number((self.baits.get(bait_id) or {}).get("luck"), 0.0)
                        + gear_luck + weather_luck,
                        extra_luck=luck,
                        cfg=cfg,
                        floor=floor,
                    )
                    _second = _new_instance(
                        catch.get("fish_id"), _q2,
                        value_bonus=rod_value, location_mult=loc_value,
                        variant=catch.get("variant"), codex_mult=codex_mult,
                    )
                    if _second is not None:
                        await self._finalize_catch(
                            event, player, _second, user_id,
                            perfect=rating == "完美", bait_id=bait_id,
                        )
                        async for _r in self._say_msg(
                            event, "cast.double",
                            event.plain_result(
                                "🎣 双尾竿：同一竿又上来一条 —— "
                                + _instance_line(_second)
                            ),
                        ):
                            yield _r
                else:
                    async for _r in self._say_msg(
                        event, "cast.double_full",
                        event.plain_result("🎣 双尾竿本想再来一条，背包满了（先 /钓鱼 卖）"),
                    ):
                        yield _r

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
            # strict=True：连钓同样会 _save_player，读失败必须中止（同单竿的说明）
            try:
                player = await self._load_player(user_id, strict=True)
            except PlayerLoadError as e:
                logger.error(f"连钓中止（玩家 {user_id} 存档读取失败）：{e}")
                async for _r in self._say_msg(
                    event,
                    "cast.load_failed",
                    event.plain_result(
                        "😵 读取你的存档失败了（可能是数据库正忙）。"
                        "这一批没有扣任何东西，稍后再试一次就好。"
                    ),
                ):
                    yield _r
                return
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
            # ⚠️ v1.18.79 修（站长：「连钓的时候限定鱼饵用完了居然提示让我去买，你这不是
            #    严重错误吗，明明只能抽出来」）：以前这里直接读 `player["baits"]`，于是
            #    「凭证还剩 5 次」被算成 0 个，整批连钓被拦下、还让人去买**买不到**的限定饵。
            #    现在按「哪一种饵真的会被用」分别算：
            #      * **抽到的限定饵优先**（`_active_limited_bait`）—— 算它的**剩余次数**，
            #        不够时如实说「还能抛 N 竿」（绝不提示购买），并说明后面会换成哪种普通饵；
            #      * 玩家**点名**了某种普通饵（`/钓鱼 蚯蚓 10`）—— 只算那种饵的库存；
            #      * 其余按装备的普通饵算库存。
            #    ⚠️ 这里**没有** `wanted`（那是单竿 `_do_cast` 的形参）：连钓本来就不支持
            #    「点名饵」，所以限定饵优先的口径在这里恒成立。
            _lim_bait = self._active_limited_bait(player)
            bait_id, _supply_notes = self._auto_supply(player, times=times, batch=True)
            if _lim_bait and bait_id == _lim_bait:
                _avail = self._limited_bait_left(player, _lim_bait)
                _fb = self._purchasable_fallback_bait(player.get("equipped_bait") or "")
                _other = _safe_int((player.get("baits") or {}).get(_fb or ""), 0, 0)
                # ⚠️ v1.18.79：按**合计**判断，别只看限定饵那几次 ——
                #    凭证 2 次 + 普通饵 5 个 = 7 竿，连钓 5 明明是够的（踩过）。
                if _avail + _other < times:
                    async for _r in self._say_msg(event, "cast.multi_no_bait", event.plain_result(
                            f"🎒 抽到的「{self.baits[_lim_bait]['name']}」还能抛 {_avail} 竿"
                            f"（买不到，只能靠大鱼乐抽）\n"
                            f"　连钓 {times} 竿不够，"
                            + (f"后面会改用能买到的「{self.baits[_fb]['name']}」（还有 {_other} 个）"
                               if _fb and _other else "后面会改用空钩（/钓鱼 换饵 空钩）")
                            + f"\n　先 /钓鱼 {_avail + _other} 把这 {_avail + _other} 竿用掉"
                        )):
                        yield _r
                    return
            elif bait_id != "none":
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
            # ⚠️ 自动补给的说明**不写在这儿**（v1.18.50）：以前「🛒 自动补货 20 个深渊饵」
            #    和「🎐 自动用上玉髓灯」会插在渔获列表中间（补货发生在第 N 竿，
            #    第 N 竿的说明就顺手贴在那儿），把「1. 2. 3. …」的清单劈成两段。
            #    现在整批的补给说明攒进 `_supply_all`，等渔获清单 + 汇总都列完，
            #    统一贴到最末尾（见下面 `_supply_all` 那段）。
            _supply_all = list(_supply_notes)
            #: v1.18.79：整批开始前每件限用道具还剩几次（收尾时算「这一批扣了几次」）
            _lim_before = {
                _lid: self._limited_item_left(player, _lid)
                for _lid in self._limited_item_list()
            }
            if truncated:
                lines.append(
                    f"⚠️ 背包只剩 {free} 个位置，本次只钓 {planned} 次"
                    f"（体力与鱼饵也只扣 {planned} 份）"
                )
            stats = {"fish": 0, "item": 0, "nothing": 0, "escaped": 0}
            # 连钓结果的行号：**不再等于第几竿**，而是「第几条被列出来的记录」。
            # 一竿一条时会跟 loop 的 index 完全一致，所以老玩家看不出区别。
            display = 1
            #: 连钓的堆叠渔获 ``key -> {...}``（见 `_multi_stack_line`）：
            #: 普通鱼不再一竿一行，攒起来一行表示。
            stacked: dict[tuple[str, str, str], dict[str, Any]] = {}
            # 空竿里「没咬钩」的那几种（饵还在）；被鱼咬掉的照扣，和单竿同一套规则
            nobite = 0
            gained = 0
            cast_factor = self._location_hook_factor(loc.get("id"))
            #: 「buff 刚用光」是否已经尝试补过（补不到就不再每竿刷提示，见循环开头）
            _buff_refill_tried = False
            #: v1.18.79：抽到的限定饵**中途用完**时提示一次 —— 站长要的是
            #: 「写清楚还能连钓多少次 / 后面换什么」，**不是**让他去买（那东西买不到）。
            #: v1.18.81：改成**每一竿重算**（见循环里的 `_lim_now`）——以前整批锁死开批
            #: 时那种饵，于是凭证用完后剩下的竿继续白吃它的特权（审计实测 4 竿全白嫖），
            #: 而且下面那句「后面改用 X」永远不会触发（死分支）。
            _lim_bait = bait_id if self._bait_is_limited(bait_id) else ""

            for index in range(1, planned + 1):
                # buff 在这一批**中途**用光了：照「自动补给」再补一个（v1.18.37）。
                # 单竿每竿都会走一遍 _auto_supply，连钓以前只在整批开头走一次 ——
                # 于是「玉髓灯 15 竿」在 /钓鱼 20 的最后 5 竿就没保底了。
                # 只在「刚用光」那一下试一次（`_refill_tried`），补不到就安静地
                # 按没 buff 继续 —— 不然剩下每一竿都要刷一句提示。
                if index > 1:
                    if self._buff_active(player):
                        _buff_refill_tried = False
                    elif not _buff_refill_tried:
                        _buff_refill_tried = True
                        # ⚠️ **只**去重「手气额度用完了」那一句：额度用光后每竿都会
                        #    试着补一次，以前会把同一句「用完了」重复贴进战报
                        #    （站长要求：第一次用完显示一次就够）。
                        #    其它提示（``🎐 自动用上「玉髓灯」``）**必须每次都留** ——
                        #    15 竿的灯在 20 连钓里确实会补上两次，那是实情，不能吞。
                        # v1.18.50：一律攒进 `_supply_all`，跟批首那几条一起贴到末尾，
                        #    不再插在渔获清单中间。
                        for _refill_note in self._auto_supply_buff(player):
                            if "额度用完" in _refill_note and _refill_note in _supply_all:
                                continue
                            _supply_all.append(_refill_note)
                # 每一竿都按「当前手气」结算，并按同一规则消耗：
                # 空竿 / 杂物也算一竿，和体力、鱼饵的扣法保持一致
                # ⚠️ v1.18.79：**连钓也要扣限用道具的次数**（限定竿/限定饵的凭证）。
                #    以前只有单竿 `_do_cast` 调了 `_use_limited_items`，连钓循环里没有 ——
                #    于是「深渊秘饵/潮汐竿」在连钓时**次数永远不扣**，等于无限白用
                #    （站长报的「限定鱼饵用完了还在让我买」背后就是这个）。
                #    口径与单竿一致：抛一竿算一次。
                #    ⚠️ 它返回的行里有「每次还剩 N 次」，逐竿贴进战报会刷屏
                #    （实测连钓 3 竿刷 3 行），所以这里**只留「用完了」这种一次性事件**，
                #    「这一批一共扣了几次」等整批结束后统一说一次（见 `_limited_batch_note`）。
                for _lim_note in _use_limited_items(
                    player, self.items, self._active_limited_item_ids(player)
                ):
                    if "用完" in _lim_note and _lim_note not in _supply_all:
                        _supply_all.append(_lim_note)

                # ⚠️ v1.18.81：**每一竿重新决定用哪种饵**。以前整批锁死开批时那种饵，
                #    于是限定饵凭证中途用完后，剩下的竿继续白吃它的特权
                #    （审计实测：凭证 1 次 + 连钓 4 竿 -> 4 竿全走 abyss_secret，
                #    替身饵一个没扣）；而且「后面改用 X」那句永远不触发（死分支）。
                _lim_now = self._active_limited_bait(player)
                if _lim_bait and not _lim_now:
                    _fb = self._purchasable_fallback_bait(
                        player.get("equipped_bait") or "", player
                    )
                    bait_id = _fb or "none"
                    _supply_all.append(
                        f"🕳️ 抽到的「{self.baits[_lim_bait]['name']}」的额度用完了，"
                        f"后面 {planned - index + 1} 竿改用"
                        f"「{self.baits[_fb]['name'] if _fb else '空钩'}」"
                        f"（它买不到，只能靠大鱼乐抽）"
                    )
                    _lim_bait = ""
                elif _lim_now:
                    bait_id = _lim_now
                else:
                    _equipped = str(player.get("equipped_bait") or "")
                    bait_id = _equipped if _equipped in self.baits else "none"
                # ⚠️ v1.18.82：每一竿重新取「生效的竿 / 饵」的 special —— 限定竿和限定饵
                #    都可能在批中失效（凭证扣完），特权必须跟着走。
                _rod_now_loop = self._rod(player)
                rod_fx = (
                    _rod_now_loop.get("special")
                    if isinstance(_rod_now_loop.get("special"), dict) else {}
                )
                # 星陨竿「每 N 竿一次神品」：计数器存在玩家身上，跨竿持续（与单竿同口径）
                myth_every = max(0, int(_safe_number(rod_fx.get("myth_every"), 0.0)))
                myth_hit = False
                if myth_every > 0:
                    _cast_no = _safe_int(player.get("limited_rod_casts"), 0, 0) + 1
                    if _cast_no >= myth_every:
                        _cast_no = 0
                        myth_hit = True
                    player["limited_rod_casts"] = _cast_no
                elif "limited_rod_casts" in player:
                    player.pop("limited_rod_casts", None)
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
                    lines.append(f"{display}. {drop['emoji']} {drop['name']}（杂物）")
                    display += 1
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
                    lines.append(f"{display}. {miss_tip}")
                    display += 1
                    continue

                fish = self._roll_species(bait_id, loc["id"], weather)
                # 鱼竿的「拉线手感」照样算进去（口径与单竿一致）
                spec = self._apply_rod_pull_bonus(
                    self._interaction_window(fish, weather), rod
                )
                # 变异只在上钩瞬间掷一次：拉线那条路径也要带着它（单竿同款顺序）
                variant = self._roll_variant()
                # ⚠️ v1.18.82：**限定竿的特殊效果在连钓里也要生效**。以前这一段完全没有
                #    `special` 的逻辑（`_rod_fx` 只在单竿用过），于是连钓里：
                #    潮汐不多掷异色、星陨不给神品、瞬手不是必完美（站长实测：必完美还跑鱼）、
                #    双尾只出 1 条 —— 四种竿的特权全部无效，但凭证照样扣次数。
                # ⚠️ v1.18.82：**潮汐竿「异色猎手」在连钓里也要多掷**（见上面那段说明）。
                for _ in range(max(0, int(_safe_number(rod_fx.get("variant_extra"), 0.0)))):
                    if variant:
                        break
                    variant = self._roll_variant()

                # ⚠️ v1.18.82：**瞬手竿「拉线必完美」必须在逃脱判定之前设好** ——
                #    这是站长报的那条（「必定完美拉线还是跑了三条」）。
                #    注意位置：判定在 `if spec is not None and not multi_pull:` 那一支，
                #    而下面「弹拉线」是 `elif` —— 以前我只把这段写进了 elif 里，
                #    于是不开拉线互动时**根本执行不到**，实测 escape 仍是 0.95。
                if spec is not None and _safe_number(rod_fx.get("perfect"), 0.0) > 0:
                    spec = dict(spec)
                    spec["perfect_pull"] = 1.0

                if spec is not None and not multi_pull:
                    # 关掉「连钓弹拉线」时的老行为：不弹互动，按
                    # 「鱼种逃脱率 × 没亲自拉线的惩罚」一次性判定
                    # （惩罚系数 multi_escape_mult，默认 2.0 —— 不然连钓里的
                    #   传说鱼几乎不会跑，比单竿还稳）
                    # ⚠️ 这里**必须是 elif**（下面那支才是弹互动）：写成两个独立 if 的话，
                    #   判定侥幸通过的鱼会接着掉进互动分支，等于开关没关掉 —— 踩过。
                    escape = _multi_escape_chance(spec, cfg)
                    if random.random() < escape:
                        # ⚠️ v1.18.69：连钓里跑掉的鱼**不再逐条列一行**（站长：「连钓钓鱼跑了
                        # 不要提示，免得占用一次信息回复次数导致更有可能没有按钮」）。
                        # 跑了多少条在下面的战报「跑掉 N 条」里照样看得见，信息不丢。
                        stats["escaped"] += 1
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
                    # （「瞬手」的 `perfect_pull` 已经在上面设好了，这里不用重复）
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
                    if catch is None:
                        # 同上（v1.18.69）：连钓里跑了的不逐条播报，只记进「跑掉 N 条」。
                        stats["escaped"] += 1
                        continue
                    # 拉线技巧计数（成就「神之一手 / 惊险一刻」等）与单竿同一套。
                    # ⚠️ 必须写在「鱼跑了」的 continue **之后**：脱钩的那条不算拉上来
                    # （v1.18.36 修：以前写在前面，偏差拉线即使脱钩也+1，成就会当场弹
                    #  「偏差拉线也把鱼拉了上来」，跟战报里的「跑了」自相矛盾）。
                    if rating == "完美":
                        player["perfect_pulls"] = (
                            _safe_int(player.get("perfect_pulls"), 0, 0) + 1
                        )
                    elif rating == "偏差":
                        player["clutch_wins"] = (
                            _safe_int(player.get("clutch_wins"), 0, 0) + 1
                        )
                    self._record_catch(player, catch)
                    stats["fish"] += 1
                    value = _instance_value(catch)
                    gained += value
                    if self._multi_show_separate(fish, catch):
                        lines.append(
                            f"{display}. {_fish_emoji(fish)}{fish['name']}"
                            + (" ✨变异" if catch.get("variant") else "")
                            + f" {self._rarity_name(fish['rarity'])} {_fmt_gold(value)}金"
                            + f"　{rating}"
                        )
                        display += 1
                    else:
                        self._multi_stack(
                            stacked, fish, catch, value,
                            suffix=f"　{rating}" if rating else "",
                        )
                    continue

                quality_mult = _roll_quality_mult(
                    self.cfg["quality_weights"],
                    bait_luck=bait_luck,
                    extra_luck=luck,
                    cfg=cfg,
                    floor=floor,
                )
                if myth_hit:
                    # 星陨竿「每 N 竿一次神品」（与单竿同口径：那一竿的个体品质直接神品）
                    quality_mult = _myth_quality_mult()
                    if "☄️ 星陨之赐" not in "\n".join(_supply_all):
                        _supply_all.append("☄️ 星陨之赐：这一批里有一竿必出神品")
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
                    lines.append(f"{display}. 💨 空竿")
                    display += 1
                    continue

                self._record_catch(player, catch)
                stats["fish"] += 1
                value = _instance_value(catch)
                gained += value
                if self._multi_show_separate(fish, catch):
                    lines.append(
                        f"{display}. {_fish_emoji(fish)}{fish['name']}"
                        + (" ✨变异" if variant else "")
                        + f" {self._rarity_name(fish['rarity'])} {_fmt_gold(value)}金"
                    )
                    display += 1
                else:
                    self._multi_stack(stacked, fish, catch, value)

                # ⚠️ v1.18.82：**双尾竿「一竿两条」在连钓里也要生效**。第二条走和第一条
                #    完全一样的记录路径（图鉴/成就/里程碑都认），背包满了就只留一条。
                if _safe_number(rod_fx.get("double"), 0.0) > 0:
                    _cap2 = _backpack_capacity(player, cfg)
                    if len(player.get("inventory") or []) < _cap2:
                        _q2 = _roll_quality_mult(
                            self.cfg["quality_weights"],
                            bait_luck=bait_luck,
                            extra_luck=luck,
                            cfg=cfg,
                            floor=floor,
                        )
                        _second = _new_instance(
                            fish["id"], _q2,
                            value_bonus=rod_value, location_mult=loc_value,
                            variant=variant, codex_mult=codex_mult,
                        )
                        if _second is not None:
                            self._record_catch(player, _second)
                            stats["fish"] += 1
                            gained += _instance_value(_second)
                            self._multi_stack(
                                stacked, fish, _second, _instance_value(_second),
                                suffix="　🎣双尾",
                            )
                    elif "双尾" not in "\n".join(_supply_all):
                        _supply_all.append("🎣 双尾竿本想再来一条，背包满了（先 /钓鱼 卖）")

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
            # ⚠️ 先落盘，再做成就/里程碑/排行榜这些「装饰性」的事：
            #    以前整批只有最后那一次 _save_player，中间任何异常（成就表算错、
            #    里程碑文案出错）都会让**整批渔获连同已扣的饵一起消失**，
            #    而玩家看到的却是一份正常的连钓汇总。宁可先保住数据。
            try:
                saved = await self._save_player(player)
            except Exception as e:  # pragma: no cover - 存档失败也不该吞掉汇总
                saved = False
                logger.error(f"连钓落盘失败（玩家 {user_id}）：{e}", exc_info=True)
            try:
                new_ach = self._check_achievements(player)
                milestone = self._milestone_text(player)
            except Exception as e:  # pragma: no cover
                new_ach, milestone = [], ""
                logger.error(f"连钓收尾计算失败（玩家 {user_id}）：{e}", exc_info=True)
            try:
                await self._save_player(player)
            except Exception:  # pragma: no cover
                pass
            await self._touch_leaderboard(player)

            summary = (
                f"——————\n"
                f"✅ 上鱼 {stats['fish']} 条｜空竿 {stats['nothing']} 次"
                f"｜杂物 {stats['item']} 个"
            )
            if stats["escaped"]:
                summary += f"｜跑掉 {stats['escaped']} 条"
            # 普通鱼堆叠在汇总之前补上（见 `_multi_stack_lines`）：先列出来的
            # 是「值得一条一条看」的（传说/神话/异色），剩下的同类合并成一行。
            # ⚠️ 先把已列出的重新编号成 1..N，堆叠行再往下接 —— 否则堆叠行会带着
            #    错的序号（比如「4. 传说 / 5. 锁定 / 1. 🐠鲤鱼 ×3」）。
            lines = self._renumber_records(lines)
            lines.extend(self._multi_stack_lines(stacked, display))
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

            # --- 自动补给说明：整批结束后**一次性**贴在末尾（v1.18.50）---
            # 站长的两条要求：位置别插在渔获中间、同一种补货只提示一次。
            # 所以这里对「完全相同的句子」去重后再贴（不同竿补了两次玉髓灯 →
            # 两句话一模一样，合成一条 + ×2 更省字；鱼饵和道具都走这一套）。
            # v1.18.79：限用道具（限定竿/限定饵凭证）**整批只说一次**「扣了几次、还剩几次」
            # —— 逐竿刷「还可用 N 次」会把战报撑爆（实测连钓 3 竿刷 3 行）。
            for _lid in self._limited_item_list():
                _left = self._limited_item_left(player, _lid)
                _used_now = _lim_before.get(_lid, 0) - _left
                if _used_now > 0:
                    _supply_all.append(
                        f"{self._item_label(_lid)} 本批抛了 {_used_now} 竿，"
                        + (f"还剩 {_left} 次" if _left > 0 else "额度已用完")
                    )
            if _supply_all:
                merged: list[str] = []
                counts: dict[str, int] = {}
                for note in _supply_all:
                    if note in counts:
                        counts[note] += 1
                        continue
                    counts[note] = 1
                    merged.append(note)
                for note in merged:
                    times_note = counts.get(note, 1)
                    lines.append(
                        f"{note}　×{times_note}" if times_note > 1 else note
                    )

            # 成就 / 里程碑并进**同一条**战报（v1.18.48）。
            #
            # ⚠️ 以前它们是各自 `_say_msg(...)` 发出去的两条带按钮的消息，而
            #    `msg_seq` 只是随机数、`msg_id` 又是同一个（同一条入站消息）——
            #    QQ 对一条入站消息**只允许第一条回复挂键盘**，于是后发的成就推送
            #    把战报的键盘挤掉（战报这时已经挂不上了）。
            #
            # v1.18.49 起键盘归属交给 `_interactions._keyboard_slot_free` 统一分配：
            #    这条入站消息的键盘名额，已经在前面给了「⚡ N 秒内拉」那条咬钩提示
            #    （连钓里唯一要玩家当场动手的回复），所以战报这里自动退化成纯文本。
            #    战报的按钮在配置页/编辑器里照样配（场景仍然有按钮），只是不发出去。
            if new_ach:
                lines.append("🎉 " + "；".join(new_ach))
            if milestone:
                lines.append(milestone)

            async for _r in self._say_msg(event, "cast.multi_summary", event.plain_result("\n".join(lines))):
                yield _r
            if not saved:
                async for _r in self._say_msg(event, "cast.multi_save_failed", event.plain_result(
                        "⚠️ 数据保存失败，这批渔获可能不会保留（请把这条消息发给管理员核对）"
                    )):
                    yield _r

    #: 连钓里**逐条列出**的鱼种稀有度（其余稀有度堆叠显示）
    MULTI_SEPARATE_RARITIES: tuple[str, ...] = ("传说", "神话")

    def _multi_show_separate(self, fish: dict[str, Any], catch: dict[str, Any]) -> bool:
        """连钓里这条鱼要不要单独占一行。

        规则（站长定的）：只有
          * **传说 / 神话**（需要拉线的高档鱼），或
          * **任意稀有度的异色个体**（变异）
        逐条列出；其余（常见/少见/稀有的普通个体）按「鱼种 + 异色」堆叠成一行。
        """
        if catch.get("variant"):
            return True
        return str(fish.get("rarity") or "") in self.MULTI_SEPARATE_RARITIES

    def _multi_stack(
        self,
        stacked: dict[tuple[str, str, str], dict[str, Any]],
        fish: dict[str, Any],
        catch: dict[str, Any],
        value: int,
        suffix: str = "",
    ) -> None:
        """把一条「不值得单列」的渔获记进堆叠表。"""
        key = (
            str(fish.get("id") or ""),
            str(fish.get("rarity") or ""),
            str(catch.get("variant") or ""),
        )
        entry = stacked.get(key)
        if entry is None:
            # 异色要能一眼看见：同类异色走**单独的 key**（所以不会被合并进普通那行），
            # emoji 也带上它的变异图标 —— 不然堆叠行只剩「🐟白条 ×3」，
            # 玩家看不出里面混了异色。
            var_id = str(catch.get("variant") or "")
            var_emoji = ""
            if var_id:
                var_emoji = str((VARIANT_BY_ID.get(var_id) or {}).get("emoji") or "")
            entry = {
                "emoji": f"{_fish_emoji(fish)}{var_emoji}",
                "name": str(fish.get("name") or ""),
                "count": 0,
                "value": 0,
                "suffix": str(suffix or ""),
            }
            stacked[key] = entry
        entry["count"] += 1
        entry["value"] += int(value)

    def _multi_stack_lines(
        self,
        stacked: dict[tuple[str, str, str], dict[str, Any]],
        start_index: int,
    ) -> list[str]:
        """把堆叠表渲染成若干行（接在已经列出的记录后面，行号往下续）。

        格式：``{序号}. {emoji}{鱼名} ×{条数}　{合计}金``。

        ⚠️ 行号是「第几条被列出来的记录」，不是「第几竿」（一竿一条时两者一致）。
        所以调用方**必须**在渲染前把已列出的那些记录重新编号成 1..N，
        否则堆叠行（排在最后）会接着一个错的序号往下写，玩家看到的序号就是乱的。
        """
        rows: list[str] = []
        index = int(start_index)
        for entry in stacked.values():
            rows.append(
                f"{index}. {entry['emoji']}{entry['name']} ×{entry['count']}"
                f"　{_fmt_gold(entry['value'])}金{entry['suffix']}"
            )
            index += 1
        return rows

    @staticmethod
    def _renumber_records(lines: list[str]) -> list[str]:
        """把「``N. 内容``」这类记录行按当前顺序重编号成 1..N（缩进行不动）。

        连钓战报的行号 = 第几条记录。堆叠行是最后统一渲染的，所以先把已列出的
        重新编号，堆叠行再接着往下排 —— 否则序号会跳（``4. 传说 / 5. 锁定 / 1. 鲤鱼 ×3``）。

        ⚠️ 故意不用正则：`re` 没有注入到兄弟模块（本模块也不 import 它），
        用 `re` 会在运行时 NameError。纯字符串判断同样稳。
        """
        out: list[str] = []
        number = 0
        for line in lines:
            head, dot, rest = line.partition(".")
            if dot and head.isdigit() and rest.startswith(" "):
                number += 1
                line = f"{number}.{rest}"
            out.append(line)
        return out

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

            # ⚠️ 这里**先落盘**：上面任何一步（成就表 / 里程碑 / 彩蛋）抛异常都
            #    不该把这条鱼一起带走 —— 以前它们共用一个 try，except 只记日志，
            #    于是玩家付了饵钱、这条鱼却永远不进背包（界面上还看不出来）。
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
            # 兜底：上面已经 `_record_catch` 过了，这里再存一次 ——
            # 万一异常发生在落盘之前，至少把这条鱼（和前面的成就改动）保住。
            logger.error(f"写入渔获失败（玩家 {user_id}）：{e}", exc_info=True)
            try:
                await self._save_player(player)
            except Exception:  # pragma: no cover - 保存都失败就只能记日志了
                pass

    def _collect_bookkeeping(
        self, player: dict[str, Any], drop: dict[str, Any]
    ) -> list[str]:
        """杂物记账（不含成就与存档），返回要追加的提示行。

        单竿走 `_apply_collectible`（再补成就与存档），连钓在循环里只记账、
        最后统一结算——两条路径共用这一份规则，避免各写一份。
        """
        # ⚠️ v1.18.75：杂物**只记进 `collectibles`**，不再往 `items`（道具背包）里塞一份。
        #    以前这里两处都写，于是 `/钓鱼 用` 的「你有：…」会列出一串杂物 id
        #    （`old_boot` / `tin_can`…），玩家看得一头雾水 —— 那些根本不是道具。
        #    （杂物既没有「使用」动作，也不该出现在道具列表里。）
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

