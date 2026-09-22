# -*- coding: utf-8 -*-
"""交互与推送：按钮 payload、@ 提醒、随机插曲、群播报、拉线小游戏

这些方法是从 main.py 原样搬过来的（缩进未变），可以直接引用 main.py 的常量与
工具函数——共享方式是「main 在模块末尾把自己的全局注入本模块」，详见 main.py
顶部的说明。之所以能这样搬，是为了不改动几百处调用点。

⚠️ 维护约定：
  1. 不要在本模块对共享的模块级常量做重新赋值（`X = ...` 只会改到本模块副本），
     需要改数值请在 main.py 的 `_apply_tunable_config()` 里改。
  2. 本模块的方法通过 `self.` 互相调用，跨模块调用也一样。
"""

from __future__ import annotations


def _message_text(message: Any) -> str:
    """从 AstrBot 的回复对象里取出纯文本（取不到就返回空串）。

    AstrBot v4 的 ``event.plain_result(text)`` 返回的是 ``MessageEventResult``
    （一条 ``MessageChain``，内容是 ``[Plain(text)]``），**没有** ``.text`` 属性；
    单测里的假对象则直接带 ``.text``。两种形态都兼容；取不到就不取，调用方
    会原样把消息交出去，绝不影响原有回复。
    """
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    chain = getattr(message, "chain", None)
    if isinstance(chain, (list, tuple)):
        parts = []
        for item in chain:
            if type(item).__name__ == "Plain":
                value = getattr(item, "text", None)
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)
    return ""


class InteractionsMixin:
    """交互与推送：按钮 payload、@ 提醒、随机插曲、群播报、拉线小游戏（由 FishingPlugin 继承，见 main.py 的类定义）。"""

    # 注：这里曾经有个 `_reply()`「统一回复入口」，但**从来没有调用点**，
    # 还被 test_local 的场景护栏当成合法出口（等于给「绕过场景体系」开了后门）。
    # v1.18.0 删掉：发文本一律走 _say / _say_msg / _push，场景键才守得住。

    # -------------------------------------------------------------------------
    # 玩家数据读写（插件级 KV 存储）
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # QQ 官方机器人：内联键盘（按钮）
    # -------------------------------------------------------------------------
    #
    # AstrBot 的消息组件里没有按钮，所以这里在**平台允许时**直接调用
    # botpy 的原生接口发一条带 keyboard 的消息（官方文档：
    # /v2/groups/{group_openid}/messages 的 keyboard 字段）。
    # 按钮一律用 action.type = 2（指令按钮）：点击后等价于玩家发出 data 里的指令，
    # 这样按钮走的是 AstrBot 正常的指令链路，不需要额外的回调接口。
    # 其它平台 / 发送失败时自动退回纯文本，并在正文里把指令写清楚。

    @staticmethod
    def _btn(label: str, data: str, style: int = 0) -> dict[str, Any]:
        """构造一个官方「指令按钮」。

        ``style`` 就是官网的 ``render_data.style``：0 = 灰色线框、1 = 蓝色线框
        （默认 0，和 ``_calc.BUTTON_STYLE_ALIASES`` 的兜底一致）。
        """
        return {
            "id": f"b{abs(zlib.crc32(data.encode('utf-8'))) % 100000000}",
            "render_data": {
                "label": label[:10],
                "visited_label": label[:10],
                "style": style,
            },
            "action": {
                "type": 2,  # 2 = 指令按钮
                "permission": {"type": 2},  # 2 = 所有人可用
                "data": data,
                "enter": True,   # 单聊里点一下直接发送
                "reply": False,
            },
        }

    @staticmethod
    def _keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any] | None:
        rows = [r for r in rows if r]
        if not rows:
            return None
        return {"content": {"rows": [{"buttons": r} for r in rows]}}

    async def _send_with_buttons(
        self, event: AstrMessageEvent, text: str, rows: list[list[dict[str, Any]]]
    ) -> bool:
        """发一条带按钮的消息；不支持/失败返回 False（调用方退回纯文本）。

        官方文档里「带键盘的消息」示例是 **markdown + keyboard**（msg_type=2），
        但纯文本 + keyboard（msg_type=0）在部分场景也能用，而且 markdown 需要
        额外权限。所以这里按配置 `button_mode` 依次尝试，并把**哪种形态成功**
        （或具体报错）写进日志，方便定位「为什么没有按钮」。
        """
        keyboard = self._keyboard(rows)
        if keyboard is None:
            return False
        mode = str(self.cfg.get("button_mode") or "自动").strip().lower()
        if mode in ("关闭", "off", "none", "false", "0"):
            return False
        try:
            platform = str(event.get_platform_name() or "").lower()
        except Exception:
            return False
        if platform not in ("qq_official", "qq_official_webhook"):
            return False
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        msg_obj = getattr(event, "message_obj", None)
        if api is None or msg_obj is None:
            return False

        raw = getattr(msg_obj, "raw_message", None)
        msg_id = str(getattr(msg_obj, "message_id", "") or "")
        body = (text or "").strip()

        shapes: list[dict[str, Any]] = []
        if mode in ("自动", "auto", "markdown", "md"):
            shapes.append({"msg_type": 2, "markdown": {"content": body}})
        if mode in ("自动", "auto", "text", "纯文本"):
            shapes.append({"msg_type": 0, "content": body})

        group_openid = str(
            getattr(raw, "group_openid", "") or getattr(msg_obj, "group_id", "") or ""
        )
        author = getattr(raw, "author", None)
        openid = str(getattr(author, "user_openid", "") or "")

        last_error: Exception | None = None
        for shape in shapes:
            payload = dict(shape)
            payload["keyboard"] = keyboard
            payload["msg_seq"] = random.randint(1, 99999)
            if msg_id:
                payload["msg_id"] = msg_id
            try:
                if group_openid and hasattr(api, "post_group_message"):
                    await api.post_group_message(group_openid=group_openid, **payload)
                elif openid and hasattr(api, "post_c2c_message"):
                    await api.post_c2c_message(openid=openid, **payload)
                else:
                    return False
                # 成功了：记一次是怎么发出去的，以后排查有据可依
                if not getattr(self, "_button_ok_logged", False):
                    self._button_ok_logged = True
                    logger.info(
                        f"QQ 官方按钮发送成功（msg_type={shape['msg_type']}，"
                        f"{len(rows)} 行按钮）"
                    )
                return True
            except Exception as e:  # 换下一种形态
                last_error = e

        if last_error is not None and not getattr(self, "_button_warned", False):
            self._button_warned = True
            logger.warning(
                f"QQ 官方按钮发送失败，已退回纯文本：{last_error}\n"
                f"　已尝试的形态：{[s['msg_type'] for s in shapes]}；"
                f"可在插件配置里把 button_mode 设为 markdown 或 text 单独试，"
                f"或设为「关闭」不再尝试。常见原因：适配器不支持 keyboard、"
                f"机器人没有内联键盘/markdown 权限。"
            )
        elif last_error is not None:
            logger.debug(f"带按钮的消息发送失败，改用纯文本：{last_error}")
        return False

    def _with_at(self, event: AstrMessageEvent, result: Any) -> Any:
        """给一条回复加上 @发送者（只用在每条指令的第一条消息上）。

        平台支持 At 组件时才加；不支持/出错就原样返回，不影响功能。
        """
        try:
            platform = str(event.get_platform_name() or "").lower()
            if platform not in ("aiocqhttp", "qq_official", "qq_official_webhook"):
                return result
            text = getattr(result, "text", None)
            if not isinstance(text, str) or not text:
                return result
            from astrbot.api.message_components import At, Plain

            uid = str(event.get_sender_id())
            return event.chain_result([At(qq=uid), Plain("\n" + text)])
        except Exception:
            return result

    async def _say(
        self,
        event: AstrMessageEvent,
        text: str,
        scene: str | None = None,
        values: dict[str, Any] | None = None,
    ):
        """**统一输出出口**：先按场景叠加文案覆盖，再能发按钮就发按钮，否则退回纯文本。

        * ``scene``：回复场景 id（见 _calc.py 的 ``REPLY_SCENES``）。场景自己没配
          按钮时按「显式父场景 → 同组基础场景」依次回退（v1.18.17 起整组都有兜底，
          所以 cast.miss_none / aquarium.full / item.feed_done 这些也都有按钮了）；
          默认没有按钮的场景传进来也只是纯文本。
        * ``values``：文案模板的占位符取值（只有少数场景用到）。``{原文}`` 永远
          等于代码拼好的这段文本，所以站长不改文案时行为完全不变。

        用法：``async for r in self._say(event, text, "bag.list"): yield r``
        """
        text = self._scene_text(scene, text, values)
        rows = self._scene_rows(scene) if scene else []
        if rows and await self._send_with_buttons(event, text, rows):
            return
        yield event.plain_result(text)

    async def _say_msg(
        self,
        event: AstrMessageEvent,
        scene: str | None,
        message: Any,
        values: dict[str, Any] | None = None,
    ):
        """``_say`` 的「消息对象版」：``message`` 一般来自 ``event.plain_result(文字)``。

        存在的意义：让几百处已经在用的 ``yield event.plain_result(...)`` 只需要
        在外面套一层就能带上场景按钮与文案覆盖，不用把里面的文案重写一遍。
        取不到文本时原样交出对象（功能不受影响）。
        """
        text = _message_text(message)
        if not text:
            yield message
            return
        async for reply in self._say(event, text, scene, values):
            yield reply

    async def _push(
        self,
        event: AstrMessageEvent,
        scene: str | None,
        text: str,
        values: dict[str, Any] | None = None,
    ) -> bool:
        """主动推送一条消息（不经过 ``yield`` 链：成就、彩蛋、群播报都走这里）。

        失败只记日志，绝不影响主流程；返回是否推成功（发按钮成功也算成功）。
        """
        text = self._scene_text(scene, text, values)
        try:
            rows = self._scene_rows(scene) if scene else []
            if rows and await self._send_with_buttons(event, text, rows):
                return True
            await event.send(event.plain_result(text))
            return True
        except Exception as e:
            logger.warning(f"推送消息失败：{e}")
            return False









    # -------------------------------------------------------------------------
    # 小插曲 / 连载剧情（v1.18.13）
    # -------------------------------------------------------------------------

    def _story_state(self, player: dict[str, Any]) -> dict[str, Any]:
        """拿到（并补全）玩家身上的剧情进度。

        老存档、被扩展写坏过的存档都从这里过一遍，缺什么补什么 ——
        这样后面读 `story["flags"]` 之类的地方不用到处判空。
        """
        story = player.get("story")
        if not isinstance(story, dict):
            story = {}
            player["story"] = story
        story.setdefault("arc", "")
        story.setdefault("ep", 0)
        if not isinstance(story.get("flags"), dict):
            story["flags"] = {}
        story.setdefault("since", 0)
        if not isinstance(story.get("seen"), list):
            story["seen"] = []
        if not isinstance(story.get("done"), list):
            story["done"] = []
        story.setdefault("last", "")
        return story

    @staticmethod
    def _event_available(event_def: dict[str, Any], story: dict[str, Any]) -> bool:
        """这条插曲/这一话现在能不能出现。

        * ``require``：旗标必须都对得上（前面选过什么，这里才演得下去）
        * ``once``  ：演过一次就不再出现
        * 连载演完的线不再从头演（``done``）
        """
        flags = story.get("flags") or {}
        for key, want in (event_def.get("require") or {}).items():
            if flags.get(str(key)) != want:
                return False
        event_id = str(event_def.get("id") or "")
        if event_def.get("once") and event_id in (story.get("seen") or []):
            return False
        chain_id = str(event_def.get("chain") or "")
        if chain_id and chain_id in (story.get("done") or []):
            return False
        return True

    @staticmethod
    def _weighted_pick(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        """按 ``weight`` 权重抽一个（权重非正的条目视为 1）。"""
        if not items:
            return None
        weights = [max(0.0001, _safe_number(e.get("weight"), 1.0)) for e in items]
        total = sum(weights)
        point = random.uniform(0, total)
        acc = 0.0
        for event_def, weight in zip(items, weights):
            acc += weight
            if point < acc:
                return event_def
        return items[-1]

    def _next_story_event(self, player: dict[str, Any]) -> dict[str, Any] | None:
        """这次该演哪一段：优先接着连载，否则开新线或挑一次性插曲。"""
        story = self._story_state(player)
        gap = max(0, _safe_int(self.cfg.get("story_chain_gap"), 6, 0))

        # ---- 1) 正在连载：攒够竿数就演下一话（这就是「连续剧」的接续点）----
        chain_id = str(story.get("arc") or "")
        chain = CHAIN_BY_ID.get(chain_id)
        if chain:
            episodes = list(chain.get("episodes") or [])
            ep = max(0, _safe_int(story.get("ep"), 0, 0))
            if ep >= len(episodes):
                # 上一话就是最后一话：这条线收尾，接着去找新的
                if chain_id not in story["done"]:
                    story["done"].append(chain_id)
                story["arc"] = ""
                story["ep"] = 0
            elif _safe_int(story.get("since"), 0, 0) >= gap:
                return episodes[ep]

        # ---- 2) 开一条新连载，还是来一段一次性小插曲 ----
        chain_chance = _clamp(
            _safe_number(self.cfg.get("story_chain_chance"), 0.5), 0.0, 1.0
        )
        startable = [
            c
            for c in STORY_CHAINS
            if isinstance(c, dict)
            and c.get("episodes")
            and str(c.get("id")) not in story["done"]
            and str(c.get("id")) != str(story.get("arc") or "")
        ]
        if startable and random.random() < chain_chance:
            picked = self._weighted_pick(startable)
            if picked is not None:
                episode = (picked.get("episodes") or [None])[0]
                if isinstance(episode, dict):
                    return episode

        pool = [
            e for e in RANDOM_EVENTS
            if isinstance(e, dict) and self._event_available(e, story)
        ]
        return self._weighted_pick(pool)

    def _event_order(self, event_def: dict[str, Any]) -> list[int]:
        """选项的**显示顺序**（每次随机，免得某个选择永远待在 1 号位）。

        返回的是「显示第 i 个 → 原始第几个选项」的映射，会跟着事件一起存进存档，
        所以提示、按钮、`/钓鱼 事件 N` 三处看到的是同一个顺序。
        """
        count = len(event_def.get("choices") or [])
        order = list(range(count))
        if count > 1 and bool(self.cfg.get("story_shuffle_choices", True)):
            random.shuffle(order)
        return order

    def _maybe_start_event(self, player: dict[str, Any]) -> dict[str, Any] | None:
        """偶尔触发一次小插曲 / 连载的下一话（触发条件不对外说明）。"""
        if player.get("event"):
            return None
        story = self._story_state(player)
        # 每抛一竿算一竿：连载两话之间要有间隔，不然像连播
        story["since"] = max(0, _safe_int(story.get("since"), 0, 0)) + 1
        chance = _clamp(
            _safe_number(self.cfg.get("story_chance"), 0.06), 0.0, 1.0
        )
        if chance <= 0 or random.random() >= chance:
            return None
        return self._next_story_event(player)

    def _apply_rod_pull_bonus(
        self, spec: dict[str, Any] | None, rod: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """把鱼竿的「拉线手感」加成应用到窗口与逃脱率上（v1.18.15）。

        高阶竿（龙纹鲤竿 / 归墟竿）卖的不是纯数值，而是**手感**：
        窗口更长、更不容易跑 —— 这样后期升级不会直接把鱼价再推高一档，
        而是让「拉线」这条玩法在后期更有分量（也顺带压住了收益膨胀）。

        拉线（单竿）与连钓的一次判定都走这里，两条路径口径一致。
        """
        if not spec or not isinstance(rod, dict):
            return spec
        window_bonus = _safe_number(rod.get("window_bonus"), 0.0)
        escape_factor = _safe_number(rod.get("escape_factor"), 1.0)
        if window_bonus:
            spec["window"] = max(
                1.0, _safe_number(spec.get("window"), 0.0) * (1.0 + window_bonus)
            )
        if escape_factor != 1.0:
            spec["escape"] = _clamp(
                _safe_number(spec.get("escape"), 0.0) * escape_factor, 0.0, 0.95
            )
        return spec

    def _interaction_window(
        self, fish: dict[str, Any], weather: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """算出一条鱼的拉线窗口参数；不需要互动则返回 None。

        窗口时长、最佳点位、逃脱率都由鱼种的 ``diff``（难度）与 ``drift``（偏移）决定，
        所以**每种鱼的窗口期时间和位置都不一样**；天气会再整体缩放窗口与逃脱率。
        """
        rarity = fish["rarity"]
        if rarity not in self.interactive_rarities:
            return None

        diff = _clamp(_safe_number(fish.get("diff"), 0.5), 0.0, 1.0)
        drift = _clamp(_safe_number(fish.get("drift"), 0.0), -0.5, 0.5)
        window_mult = _clamp(
            _safe_number((weather or {}).get("window_mult"), 1.0), 0.3, 3.0
        )
        escape_mult = _clamp(
            _safe_number((weather or {}).get("escape_mult"), 1.0), 0.2, 3.0
        )

        w_min = int(self.cfg["window_min"])
        w_max = int(self.cfg["window_max"])
        # 难度越高 -> 越短的窗口
        scaled_min = max(2, int(round(w_min * (1.0 - 0.35 * diff) * window_mult)))
        scaled_max = max(
            scaled_min + 1, int(round(w_max * (1.0 - 0.40 * diff) * window_mult))
        )
        window = random.uniform(float(scaled_min), float(scaled_max))

        # 最佳点位：默认居中，按 drift 偏移，并留出安全边距
        half = self.cfg["sweet_spot_width"] / 2.0
        center = _clamp(0.5 + drift, half + 0.05, 1.0 - half - 0.05)

        base_escape = self.escape_map.get(rarity, DEFAULT_ESCAPE_RATE)
        # 难度对逃脱率的影响权重（v1.18.19，站长问「逃脱率提高生效了吗」时加的）：
        #   factor = (1 − 0.5×w) + w×diff
        # 默认 w = 0.5 → 0.75 + 0.5×diff，**与历史曲线逐字一致**；
        # w = 0 → factor 恒为 1，也就是「配置里写多少，窗口期就是多少」
        #（天气倍率与鱼竿的逃脱率系数仍然照常生效）。
        diff_weight = _clamp(
            _safe_number(self.cfg.get("escape_difficulty_weight"), 0.5), 0.0, 2.0
        )
        diff_factor = (1.0 - 0.5 * diff_weight) + diff_weight * diff
        escape = _clamp(base_escape * diff_factor * escape_mult, 0.0, 0.95)

        return {
            "window": window,
            "center": center,
            "half": half,
            "escape": escape,
        }

    def _judge_pull(
        self, pos: float, spec: dict[str, Any]
    ) -> tuple[str, float, float, str]:
        """根据落点位置判定评价。纯函数，方便单测。

        ``pos`` 是 0~1 的落点（0 = 刚咬钩，1 = 窗口结束）。
        返回 ``(评价, 品质幸运加成, 逃脱率倍数, 标记 emoji)``。
        """
        center = _clamp(_safe_number(spec.get("center"), 0.5), 0.0, 1.0)
        half = _clamp(_safe_number(spec.get("half"), 0.17), 0.01, 0.5)
        drift = abs(_clamp(pos, 0.0, 1.0) - center)

        if drift <= half * 0.5:
            return (
                "完美",
                float(self.cfg["perfect_bonus"]),
                float(self.cfg["perfect_escape_factor"]),
                "🎯",
            )
        if drift <= half * 1.5:
            return "良好", float(self.cfg["good_bonus"]), 1.0, "👍"
        return "偏差", 0.0, float(self.cfg["edge_escape_factor"]), "😅"

    async def _play_minigame(
        self,
        event: AstrMessageEvent,
        user_id: str,
        fish: dict[str, Any],
        bait_id: str,
        spec: dict[str, Any],
    ):
        """拉线互动：提示 -> 等「拉」 -> 按落点评价。

        异步生成器：先 yield 若干提示消息，最后 yield 一个 dict 结果
        （异步生成器不能用带值的 return，所以用最后 yield dict 回传）。
        结果格式：``{"catch": 鱼实例 | None, "rating": "完美/良好/偏差/失败", ...}``
        """
        window = spec["window"]
        center = spec["center"]

        loop = asyncio.get_running_loop()
        future: asyncio.Future[float] = loop.create_future()
        # 先注册等待、再提示玩家，避免「提示已发出但还没开始监听」的竞态
        self._pending_pulls[user_id] = {
            "future": future,
            "session": self._session_key(event),
            "deadline": time.monotonic() + window,
        }

        tips = ["竿尖猛地弯了下去", "浮漂一下子沉进水里", "线被拽得吱吱响",
                "水面炸开一朵水花", "手里的竿传来一股大力"]
        tip = random.choice(tips)
        hook_text = (
            f"{_fish_emoji(fish)} {fish['name']} 咬钩了！{tip}\n"
            f"⚡ {window:.0f} 秒内发 /钓鱼 拉（或点下面的按钮）"
        )
        async for reply in self._say(
            event,
            hook_text,
            "pull.hook",
            values={
                "鱼名": str(fish.get("name") or ""),
                "秒数": f"{window:.0f}",
                "手感": tip,
            },
        ):
            yield reply

        started = time.monotonic()
        # 反应时间护栏（只有连钓会设 spec["min_reaction"]，见 main.MULTI_PULL_MIN_REACTION）：
        # 连钓自动接续下一条，上一条的「余震」（连点 / 消息重投）会落在这条窗口刚开时，
        # 不加护栏就会以落点 ≈0 判成「偏差」，玩家连提示都没看见。这段里的「拉」丢弃后
        # **继续等剩下那点时间**，不是直接判超时。
        min_reaction = _safe_number(spec.get("min_reaction"), 0.0)
        hit = False
        elapsed = window
        try:
            while True:
                remaining = window - (time.monotonic() - started)
                if remaining <= 0:
                    break
                await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
                elapsed = time.monotonic() - started
                if elapsed >= min_reaction:
                    hit = True
                    break
                # 太早了：换一个新 future 接着等（旧的那个已经 done，换掉即可）
                future = loop.create_future()
                self._pending_pulls[user_id] = {
                    "future": future,
                    "session": self._session_key(event),
                    "deadline": time.monotonic() + remaining,
                }
        except asyncio.TimeoutError:
            hit = False
            elapsed = window
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        finally:
            self._pending_pulls.pop(user_id, None)

        if not hit:
            async for _r in self._say_msg(event, "pull.timeout", event.plain_result(
                    f"💨 超时了——{fish['name']} 吐钩跑了（这一竿的鱼饵已经用掉了）\n"
                    f"　下次在提示的时间内发 /钓鱼 拉 就能拉住它"
                )):
                yield _r
            yield {"catch": None, "rating": "失败", "bonus": 0.0}
            return

        # 落点：0~1 的位置，越靠近 center 越准
        pos = _clamp(elapsed / window, 0.0, 1.0) if window > 0 else 0.0
        rating, bonus, factor, mark = self._judge_pull(pos, spec)

        escape = _clamp(spec["escape"] * factor, 0.0, 0.95)
        if random.random() < escape:
            async for _r in self._say_msg(event, "pull.escape", event.plain_result(
                    f"{mark} {rating}　但线一松——{fish['name']} 挣脱跑了"
                )):
                yield _r
            yield {"catch": None, "rating": rating, "bonus": 0.0}
            return

        bait = self.baits.get(bait_id) or {}
        # 手气口径与「不用拉线」那条路径**完全一致**（v1.18.14 修的）：
        #   常驻来源（鱼饵 + 天气 + 鱼竿）走 bait_luck，
        #   一次性/持续 buff（插曲、玉佩）与拉线评价加成走 extra_luck，
        #   最后由 _roll_quality_mult 按档位放大权重（v1.18.22 起：不再「推点数」，
        #   上限也从硬编码 1.0 改成可配的 luck_cap）。
        # 以前这里只传了「鱼饵 + 天气 + 评价」，鱼竿与玉佩/插曲那份被吞了 ——
        # 而收尾照样 _consume_luck()，等于玉佩白扣。
        quality_mult = _roll_quality_mult(
            self.cfg["quality_weights"],
            bait_luck=(
                _safe_number(bait.get("luck"), 0.0)
                + _safe_number(spec.get("weather_luck"), 0.0)
                + _safe_number(spec.get("gear_luck"), 0.0)
            ),
            extra_luck=bonus + _safe_number(spec.get("player_luck"), 0.0),
            cfg=self.cfg,
            floor=_safe_number(spec.get("player_floor"), 0.0),
        )
        catch = _new_instance(
            fish["id"],
            quality_mult,
            variant=spec.get("variant"),
            value_bonus=spec.get("rod_value_bonus", 0.0),
            location_mult=spec.get("location_mult", 1.0),
            codex_mult=spec.get("codex_mult", 1.0),
        )
        yield {"catch": catch, "rating": rating, "bonus": bonus, "mark": mark}

    async def _iter_minigame(self, event, user_id, fish, bait_id, spec):
        """``_play_minigame`` 的流式包装，产出 ``("msg", 回复)`` / ``("result", dict)``。

        **单竿与连钓都走这里**，理由只有一个：`_play_minigame` 是「先 yield 提示、
        后 ``wait_for`` 等玩家拉」，所以调用方必须**把提示当场交出去再等**。

        单竿以前是 `_run_minigame`：先把消息攒成一个 list、等整局跑完才一起 yield ——
        那会让「咬钩了」滞后到窗口结束才出现。只有 QQ 官方那条路看不出来（`_say`
        命中内联键盘时是直发、不走 yield），**走纯文本的平台（aiocqhttp 等）玩家
        永远来不及拉**，只能眼睁睁超时。连钓更严重：整批都会卡在这儿。

        异步生成器不能带值 return，所以结果用最后那条 ``("result", ...)`` 回传。
        """
        async for item in self._play_minigame(event, user_id, fish, bait_id, spec):
            if isinstance(item, dict):
                yield ("result", item)
            else:
                yield ("msg", item)

    async def _run_minigame(self, event, user_id, fish, bait_id, spec):
        """驱动 minigame，返回 (消息列表, 结果 dict)。

        ⚠️ 保留是为了不打断外部调用方（扩展 / 老测试）；插件内部已经改走
        ``_iter_minigame`` —— 这个方法会把提示攒到最后才交出去，用它就会重现
        「提示滞后」的老毛病，新代码别再用。
        """
        messages: list[Any] = []
        result: dict[str, Any] | None = None
        async for kind, payload in self._iter_minigame(
            event, user_id, fish, bait_id, spec
        ):
            if kind == "result":
                result = payload
            else:
                messages.append(payload)
        if result is None:
            result = {"catch": None, "rating": "失败", "bonus": 0.0}
        return messages, result

    @staticmethod
    def _session_key(event: AstrMessageEvent) -> str:
        """当前会话标识，用于避免跨群误触发互动。"""
        try:
            return str(event.unified_msg_origin)
        except Exception:
            try:
                return f"{event.get_platform_name()}:{event.get_group_id()}"
            except Exception:
                return "unknown"

    def _resolve_pull(self, event: AstrMessageEvent) -> bool:
        """若该玩家正在等「拉」，唤醒等待。返回是否触发。"""
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            return False
        pending = self._pending_pulls.get(user_id)
        if not pending:
            return False
        session = pending.get("session")
        if session and session != self._session_key(event):
            return False
        future = pending.get("future")
        if future is None or future.done():
            return False
        future.set_result(time.monotonic())
        return True

    async def _maybe_trigger_story(self, event: AstrMessageEvent, user_id: str):
        """抛竿收尾：偶尔来一段小插曲（触发条件不对外说明）。

        钓鱼与「钓上杂物」两条路径都要走这里，所以单独抽出来，避免复制粘贴。

        v1.18.13：这里同时负责**连载**的推进 ——
        选中的如果是某条线的第 N 话，就把「正在连载这条线、已经演到第 N 话」
        记进存档，下一次（隔几竿）自然接上第 N+1 话。
        """
        player = await self._load_player(user_id)
        story = self._maybe_start_event(player)
        if story is None:
            await self._save_player(player)      # since 计数也要落盘
            return
        order = self._event_order(story)
        player["event"] = {"id": story["id"], "ts": int(time.time()), "order": order}
        chain_id = str(story.get("chain") or "")
        if chain_id:
            state = self._story_state(player)
            state["arc"] = chain_id
            state["ep"] = max(0, _safe_int(story.get("episode"), 1, 1))
            state["since"] = 0
        self._recent_events[self._session_key(event)] = (user_id, time.time())
        await self._save_player(player)
        # 连载：先说一句「上次演到哪」，再抛这一话（单独一条消息，站长可单独配文案）
        recap = self._event_recap(story, player)
        if recap:
            async for reply in self._say(event, recap, "story.recap"):
                yield reply
        text, _rows = self._event_prompt(
            story, order, text=self._event_text(story, player)
        )
        async for reply in self._say(event, text, "story.prompt"):
            yield reply

    async def _broadcast(self, event: AstrMessageEvent, catch: dict[str, Any]) -> bool:
        try:
            sender = event.get_sender_name() or str(event.get_sender_id())
            return await self._push(
                event,
                "broadcast.catch",
                f"📢 {sender} 钓到了 {_instance_line(catch)}！",
                values={"昵称": str(sender), "渔获": _instance_line(catch)},
            )
        except Exception as e:
            logger.warning(f"群播报失败：{e}")

    # -------------------------------------------------------------------------
    # 每日订单
    # -------------------------------------------------------------------------

