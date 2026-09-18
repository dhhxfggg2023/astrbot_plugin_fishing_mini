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

    async def _do_cast(self, event: AstrMessageEvent, user_id: str, bait_name: str):
        """执行一次抛竿（含互动玩法）。"""
        broadcast_catch: dict[str, Any] | None = None
        cfg = self.cfg

        lock = self._lock_for(user_id)
        if lock.locked():
            yield event.plain_result("🎣 手上还捏着竿呢，先 /钓鱼 拉 或等它跑掉")
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

            # --- 选鱼饵 ---
            bait_id = "none"
            bait_note = ""
            if bait_name:
                matched = self._find_bait(bait_name)
                if matched is None:
                    names = "、".join(self.baits[b]["name"] for b in self._bait_list())
                    yield event.plain_result(
                        f"🤔 没有「{bait_name}」这种饵。可买：{names}"
                    )
                    return
                bait_id = matched
                if bait_id != "none":
                    owned = _safe_int((player.get("baits") or {}).get(bait_id), 0, 0)
                    if owned <= 0:
                        # 没货也不打断这一竿：直接用空钩，并说清楚怎么固定成空钩
                        bait_id = "none"
                        bait_note = (
                            f"🎒 {self._bait_label(matched)} 用完了，这一竿改用空钩"
                            f"（/钓鱼 商店 买 {self.baits[matched]['name']} 补货，"
                            f"或 /钓鱼 换饵 空钩 固定用空钩）"
                        )
            else:
                equipped = player.get("equipped_bait", "none")
                if (
                    isinstance(equipped, str)
                    and equipped in self.baits
                    and equipped != "none"
                ):
                    if _safe_int((player.get("baits") or {}).get(equipped), 0, 0) > 0:
                        bait_id = equipped
                    else:
                        # 用完就真的换掉：写进存档，别每竿都偷偷回退一次
                        bait_id = "none"
                        player["equipped_bait"] = "none"
                        bait_note = (
                            f"🎒 {self._bait_label(equipped)} 用完了，"
                            f"已自动换回空钩（/钓鱼 商店 买 {self.baits[equipped]['name']} "
                            f"可补货）"
                        )

            # --- 冷却 ---
            cooldown = int(cfg["cooldown_seconds"])
            if cooldown > 0:
                elapsed = now - _safe_int(player.get("last_fish_time"), 0, 0)
                remain = cooldown - elapsed
                if remain > 0:
                    yield event.plain_result(
                        f"⏳ 钓鱼冷却中：还要等 {int(remain) + 1} 秒"
                        f"（每次下竿间隔 {cooldown} 秒）"
                    )
                    return

            # --- 费用 ---
            # 鱼饵在下竿时只扣库存（买的时候已经付过钱），不再重复收饵钱；
            # 空钩下竿完全免费（fish_cost 默认 0，站长仍可在配置里开启钓费）。
            total_cost = max(0, _safe_int(cfg["fish_cost"], 0, 0))
            if total_cost and _safe_int(player.get("gold"), 0, 0) < total_cost:
                yield event.plain_result(
                    f"💸 下竿需要 {total_cost} 金币，你只有 "
                    f"{_fmt_gold(player.get('gold', 0))}。可 /钓鱼 签到 或 /钓鱼 卖"
                )
                return

            # --- 背包容量检查（限制无限囤货）---
            backpack_cap = _backpack_capacity(player, cfg)
            if len(player.get("inventory") or []) >= backpack_cap:
                yield event.plain_result(
                    f"🎒 背包满了（{backpack_cap}）！先 /钓鱼 卖 或 /钓鱼 水族馆 放，"
                    f"也可以 /钓鱼 商店 买 扩建背包"
                )
                return

            # --- 扣费 / 扣饵（各一次）---
            if total_cost:
                player["gold"] = _safe_int(player.get("gold"), 0, 0) - total_cost
            if bait_id != "none":
                baits = player.setdefault("baits", {})
                baits[bait_id] = max(0, _safe_int(baits.get(bait_id), 0, 0) - 1)

            # ---- 这一竿的结果：中鱼 / 钩上物件 / 空手而归（一竿只出一样）----
            # 顺序见 _roll_cast_outcome：先判中鱼（上鱼率 == 配置的咬钩率 × 钓点系数），
            # 没中鱼才可能钩上杂物；完全免费的空钩不出杂物。
            # 钓点系数 >= 1.0 的地图（默认前 3 张）必出鱼，越深越容易空竿。
            cast_loc = self._location(player)
            outcome, drop = self._roll_cast_outcome(
                bait_id, bait_id != "none" or total_cost > 0, cast_loc.get("id")
            )
            if outcome != "fish":
                player["last_fish_time"] = int(now)
                await self._save_player(player)
                if outcome == "item" and drop is not None:
                    player = await self._load_player(user_id)
                    drop_text = await self._apply_collectible(player, drop, user_id)
                    lines = [t for t in (drop_text, bait_note) if t]
                    if lines:
                        async for reply in self._say(
                            event, "\n".join(lines), self._cast_rows()
                        ):
                            yield reply
                    # 这一竿没钓到鱼：不计渔获、不进图鉴、不刷新最佳纪录、不播报
                    async for reply in self._maybe_trigger_story(event, user_id):
                        yield reply
                    return
                tip = (
                    "🪝 空钩在水里漂了半天，鱼碰了碰就游走了"
                    if bait_id == "none"
                    else f"🎣 咬了一口又吐掉了——{self._bait_label(bait_id)} 白搭了"
                )
                # 深水钓点本来就难：空竿时点一句，别让玩家以为是自己的问题
                # （只描述现象，不带「去哪儿买什么」的教程尾巴）
                factor = self._location_hook_factor(cast_loc.get("id"))
                if factor is not None and factor < 0.75:
                    tip = f"🌊 {cast_loc['emoji']}{cast_loc['name']} 水太深了，鱼不太愿意开口"
                yield event.plain_result(tip)
                return

            # ---- 抽鱼种（按当前钓点的鱼池 + 今日天气）----
            loc = self._location(player)
            rod = self._rod(player)
            weather = self._weather(player)
            fish = self._roll_species(bait_id, loc["id"], weather)
            # 幸运值 = 洗髓丹储备 + 鱼竿幸运，本次抛竿读一次，收尾时消耗储备
            luck = _safe_number(player.get("luck_charges"), 0.0)
            gear_luck = _safe_number(rod.get("luck_bonus"), 0.0)
            player["last_fish_time"] = int(now)
            await self._save_player(player)

            spec = self._interaction_window(fish, weather)
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
                # 把天气/装备/图鉴/变异信息带进互动，供拉线成功后造鱼使用
                spec = dict(spec)
                spec["variant"] = variant
                spec["weather_luck"] = weather_luck
                spec["rod_value_bonus"] = rod_value
                spec["location_mult"] = loc_value
                spec["codex_mult"] = codex_mult
                messages, result = await self._run_minigame(
                    event, user_id, fish, bait_id, spec
                )
                for message in messages:
                    yield message
                player = await self._load_player(user_id)
                catch = result.get("catch")
                rating = result.get("rating")

            # 消耗幸运储备（无论成功与否，抛竿即用掉）
            player["luck_charges"] = 0.0
            await self._save_player(player)

            if catch is None:
                # 鱼跑了：扣费已在前面完成（饵已经消耗掉），
                # 但「饵用完了」这类提示必须照说，否则玩家只看到掉钱。
                if bait_note:
                    yield event.plain_result(bait_note)
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
            async for reply in self._say(event, result_text, self._cast_rows()):
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

            # 奇遇事件（低概率小惊喜，不影响平衡）
            # 先结算彩蛋再查成就，这样「意外之喜」能在当竿立刻解锁
            egg = self._roll_easter_egg()
            egg_text = self._apply_easter_egg(player, egg, bait_id) if egg else ""
            if egg_text:
                coll = player.setdefault("collectibles", {})
                key = f"egg_{egg['id']}"
                coll[key] = _safe_int(coll.get(key), 0, 0) + 1

            new_achievements = self._check_achievements(player, catch)
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
                await event.send(
                    event.plain_result("🎉 " + "；".join(new_achievements))
                )
            if egg_text:
                await event.send(event.plain_result(egg_text))
            if milestone:
                await event.send(event.plain_result(milestone))
            if not saved:
                await event.send(
                    event.plain_result(
                        "⚠️ 数据保存失败，这条记录可能不会保留\n"
                        "　请把这条消息发给管理员核对（日志里有详情）"
                    )
                )
        except Exception as e:
            logger.error(f"写入渔获失败（玩家 {user_id}）：{e}", exc_info=True)

    async def _apply_collectible(
        self, player: dict[str, Any], drop: dict[str, Any], user_id: str
    ) -> str:
        """处理钓上来的杂物：记账、漂流瓶开纸条、值钱的直接折算金币。

        现在杂物与鱼互斥（一竿只出一样），所以文案要明确「这一竿上来的
        不是鱼」，别让玩家以为同时还钓到了鱼。
        """
        try:
            items = player.setdefault("items", {})
            items[drop["id"]] = _safe_int(items.get(drop["id"]), 0, 0) + 1
            coll = player.setdefault("collectibles", {})
            coll[drop["id"]] = _safe_int(coll.get(drop["id"]), 0, 0) + 1

            lines = [
                f"{drop['emoji']} 钩子空了，倒是带上来一个 {drop['name']}（这一竿没有鱼）",
                f"　{drop['desc']}　已收进杂物收藏",
            ]

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

            new_ach = self._check_achievements(player)
            await self._save_player(player)
            if new_ach:
                lines.append("🎉 " + "；".join(new_ach))
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"处理杂物失败（玩家 {user_id}）：{e}", exc_info=True)
            return ""

