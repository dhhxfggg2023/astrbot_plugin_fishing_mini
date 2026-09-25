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

    def _kb_current_msg_id(self) -> str:
        """当前正在处理的那条入站消息 id（拿不到返回空串）。"""
        return str(getattr(self, "_kb_msg_id", "") or "")

    def _kb_bind_msg(self, event: AstrMessageEvent) -> str:
        """把当前事件的 msg_id 记到 ``self._kb_msg_id``（键盘名额按它记账）。"""
        try:
            msg_id = str(
                getattr(getattr(event, "message_obj", None), "message_id", "") or ""
            )
        except Exception:
            msg_id = ""
        self.__dict__["_kb_msg_id"] = msg_id
        return msg_id

    async def _keyboard_slot_free(self, msg_id: str, *, peek: bool = False) -> bool:
        """这条入站消息还**有没有键盘名额**（v1.18.49）。

        QQ 官方机器人对**一条入站消息**有两道硬限制（官方文档「发送群聊消息」）：
        1. **每个 msg_id 最多被动回复 5 次**，且被动回复要带 msg_id；超了就是
           ``40034128 被动回复时间或者次数超过限制``；
        2. **主动消息不能带键盘**（``post_group_message`` 不带 msg_id 时不支持 keyboard）。

        两者合起来就是一句话：**一条入站消息只有第一条回复能挂键盘**。
        20 连钓要发 20+ 条，第 6 条起全部 40034128 —— 插件这里失败退回纯文本，
        AstrBot 那条兜底改用主动发送才把消息送出去，**但主动发送没有键盘**。
        站长看到「咬钩提示有文字、没有按钮」就是这个：键盘压根没发出去，
        不是被后一条消息顶掉的。

        所以键盘名额按 msg_id **只发一次**：第一处需要按钮的回复拿走它，后面的
        一律走纯文本（照旧能发出去，只是不挂键盘）。名额按 msg_id 记账、上限 512 条，
        过期的惰性清掉。
        """
        if not msg_id:
            # 拿不到 msg_id 就没法记账（也无法被动回复），照样尝试，交给平台判
            return True
        used = self.__dict__.setdefault("_kb_used_msgs", {})
        now = time.time()
        if msg_id in used:
            return False
        if peek:
            # 只看不占（咬钩提示借用名额时用，见 `_send_with_buttons`）
            return True
        used[msg_id] = now
        if len(used) > 512:
            for old in [k for k, ts in used.items() if now - ts >= 1800]:
                used.pop(old, None)
            if len(used) > 1024:
                for old in list(used)[:512]:
                    used.pop(old, None)
        return True

    def _reset_keyboard_slot(self, event: AstrMessageEvent) -> None:
        """**每条新指令开始时**腾出这条入站消息的键盘名额（v1.18.49）。

        ``_keyboard_slot_free`` 按 msg_id 记账「键盘已经挂过没有」，这是为了不撞
        QQ 的「一条入站消息只有第一条回复能挂键盘」。但同一条消息可能是**多次处理**
        的（测试里直接反复调 handler；线上一个 message_id 也可能被重投/重放），
        所以每条指令在入口处把这条 msg_id 的记录清掉 —— 这一轮重新有一个名额。

        ⚠️ 只在**指令入口**调用：连钓跑到一半时后面那几十条回复绝不能重新腾名额，
        否则又会去撞 40034128。
        """
        try:
            msg_id = str(
                getattr(getattr(event, "message_obj", None), "message_id", "") or ""
            )
        except Exception:
            return
        if msg_id:
            self.__dict__.setdefault("_kb_used_msgs", {}).pop(msg_id, None)

    async def _send_with_buttons(
        self, event: AstrMessageEvent, text: str, rows: list[list[dict[str, Any]]],
        *, own: bool = True,
    ) -> bool:
        """发一条带按钮的消息；不支持/失败返回 False（调用方退回纯文本）。

        ``own=False``：照常挂键盘，但**不占**这条入站消息的键盘名额（给咬钩提示用，
        让名额留给这一轮最后那条带按钮的回复，见 `_say` 的说明）。

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

        # ⚠️ 一条入站消息只有**第一条**回复能挂键盘（原因见 `_keyboard_slot_free`）。
        #    名额已经被别的回复用掉时，直接返回 False —— 调用方退回纯文本，
        #    走 AstrBot 正常的发送链路（被动回复用完时它还有主动发送兜底）。
        #    这里**不能**硬发带 keyboard 的请求：那会撞 40034128，
        #    既发不出键盘、又要多烧一条被动回复次数。
        #    ``own=False``（咬钩提示）：名额空着就借用一次、**但不占**（留给后面那条
        #    更值得点的回复）；名额已经被占了就**干脆不挂** —— 同一条消息的键盘是插进去
        #    就不再变的，硬挂一次既白发请求、又会让「填满之后的那条」看着像有按钮但其实是旧的。
        #    所以连钓里只有**第一条**咬钩提示带「拉线！」（那时名额还空着），
        #    之后的时间提示/挣脱提示刻意走 `buttons=False` 保持名额空着，
        #    最后由**连钓战报**拿走名额 —— 战报才是跑完之后最想点的那一排。
        if own:
            if not await self._keyboard_slot_free(msg_id):
                return False
        elif await self._keyboard_slot_free(msg_id, peek=True):
            pass
        else:
            return False

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
                f"机器人没有内联键盘/markdown 权限，"
                f"或**这条入站消息的被动回复次数已经用完**"
                f"（40034128：QQ 对一条消息最多允许回复 5 次，只有第一次能挂键盘）。"
            )
        elif last_error is not None:
            logger.debug(f"带按钮的消息发送失败，改用纯文本：{last_error}")
        return False

    def _sender_name(self, event: AstrMessageEvent) -> str:
        """发送者的显示名（拿不到就返回空串）。多试几个来源，各平台字段不一样。"""
        getters = (
            lambda: event.get_sender_name(),
            lambda: getattr(
                getattr(getattr(event, "message_obj", None), "sender", None),
                "nickname",
                "",
            ),
        )
        for get in getters:
            try:
                name = str(get() or "").strip()
            except Exception:
                name = ""
            if name:
                return name
        return ""

    def _mention_prefix(self, event: AstrMessageEvent) -> str:
        """一条回复开头的「称呼」（昵称 / ``@昵称``），不需要时返回空串。

        配置 ``mention_mode``（默认「昵称」）：

        * ``关闭`` —— 不称呼
        * ``昵称`` —— 在**第一条**回复的开头写上发送者昵称
        * ``@``  —— 写成「@昵称」（**纯文本**，不是可点击的真 @）

        ⚠️ 只对每条指令的第一条回复称呼一次，按 ``message_id`` 记账。
        ⚠️ 之所以放在 `_say` 里统一处理、而不是像旧版那样包在最外层：带按钮的回复是
        **直发**的（`_send_with_buttons` 发完就 return，走不到 yield 那条链），
        包在最外层的写法在 QQ 官方上会被整个跳过 —— 这才是「时灵时不灵」的根。
        """
        mode = str(self.cfg.get("mention_mode") or "昵称").strip().lower()
        if mode in ("关闭", "off", "none", "false", "0", ""):
            return ""
        # 先拿名字：拿不到就**不占**这一次称呼（免得一个空名字白白用掉这条指令的机会）
        name = self._sender_name(event)
        if not name:
            return ""
        try:
            key = str(
                getattr(getattr(event, "message_obj", None), "message_id", "") or ""
            )
        except Exception:
            key = ""
        if not key or key in self._mentioned_msgs:
            return ""
        self._mentioned_msgs[key] = time.time()
        if len(self._mentioned_msgs) > 2048:      # 惰性清理，别让它随消息数无限涨
            now = time.time()
            for old in [
                k for k, ts in self._mentioned_msgs.items() if now - ts >= 3600
            ]:
                self._mentioned_msgs.pop(old, None)
            if len(self._mentioned_msgs) > 4096:
                for old in list(self._mentioned_msgs)[:2048]:
                    self._mentioned_msgs.pop(old, None)
        mark = "@" if mode in ("@", "at", "艾特", "mention") else ""
        return f"{mark}{name}\n"

    async def _say(
        self,
        event: AstrMessageEvent,
        text: str,
        scene: str | None = None,
        values: dict[str, Any] | None = None,
        page: tuple[int, int, str] | None = None,
        again_values: dict[str, str] | None = None,
        *,
        buttons: bool = True,
        keyboard_own: bool = True,
        extra_rows: list[list[dict[str, Any]]] | None = None,
    ):
        """**统一输出出口**：先按场景叠加文案覆盖，再能发按钮就发按钮，否则退回纯文本。

        * ``scene``：回复场景 id（见 _calc.py 的 ``REPLY_SCENES``）。场景自己没配
          按钮时按「显式父场景 → 同组基础场景」依次回退（v1.18.17 起整组都有兜底，
          所以 cast.miss_none / aquarium.full / item.feed_done 这些也都有按钮了）；
          默认没有按钮的场景传进来也只是纯文本。
        * ``values``：文案模板的占位符取值（只有少数场景用到）。``{原文}`` 永远
          等于代码拼好的这段文本，所以站长不改文案时行为完全不变。
        * ``page``：**需要翻页的界面**传 ``(当前页, 总页数, 指令前缀)``，
          就会在按钮末尾多出一排「上一页 / 下一页」（见 ``_views._page_rows``）：
          第一页不给「上一页」、最后一页不给「下一页」，只有一页时整排不出现。
        * ``again_values``：**道具用成功了**传 ``_views._use_again_values(...)`` 的结果
          （``{"道具": …, "参数": …}``），会多出一排「再次使用」按钮；道具用光了
          传进来的是 ``None``，那一排就不出现。

        两排动态按钮的内容都在按钮表里（``page.prev`` / ``page.next`` / ``item.again``），
        站长能像别的按钮一样在「💬 回复」页里改文案、指令和样式。

        ``buttons=False``：**这条回复强制不要按钮**（强制走纯文本，连名额都不占）。
        一般不用它 —— 键盘归属由 `_keyboard_slot_free` 自动分配（QQ 官方一条入站消息
        只有第一条回复能挂键盘，见那里的说明）。它留给「明知挂了也白挂」的场合。

        ``keyboard_own=False``：**这条回复照常带按钮，但不占用键盘名额**（v1.18.52）。
        只给「咬钩提示」用：QQ 一条入站消息只有一个键盘名额，如果咬钩提示占着它，
        连钓战报就永远没按钮（站长报的「连钓只有第一条回复有按钮」）。
        让位之后名额落到**这一轮最后那条带按钮的回复**上 ——
        单竿是「钓到鱼的结果」，连钓是战报，正好是事后最想点的那一排。

        用法：``async for r in self._say(event, text, "bag.list"): yield r``

        ``extra_rows``（v1.18.63）：**调用方现算出来的按钮行**，优先于场景里配的。
        给小插曲用：选项按钮是按这一局的选项现生成的（``_views._event_rows``），
        而 ``story.prompt`` 这个场景本身没配按钮，继承链会兜底到 ``story.result``
        （「继续钓 / 看背包」）—— 于是站长报的「事件的按钮怎么没了」就出现了。
        """
        text = self._scene_text(scene, text, values)
        # 每条指令的**第一条**回复带上「称呼」（昵称 / @昵称）——
        # 放在这里，按钮路径与纯文本路径都会经过（见 _mention_prefix 的说明）。
        # 文本为空时不占这次称呼，留给真正有内容的第一条。
        if text:
            prefix = self._mention_prefix(event)
            if prefix:
                text = prefix + text
        rows: list[list[dict[str, Any]]] = []
        if buttons:
            rows = list(extra_rows or []) or (self._scene_rows(scene) if scene else [])
            # 动态按钮排在最后：先「上一页 / 下一页」，再「再次使用」
            rows = rows + self._page_rows(scene, page) + self._again_rows(scene, again_values)
        if rows and await self._send_with_buttons(event, text, rows, own=keyboard_own):
            return
        yield event.plain_result(text)

    async def _say_msg(
        self,
        event: AstrMessageEvent,
        scene: str | None,
        message: Any,
        values: dict[str, Any] | None = None,
        page: tuple[int, int, str] | None = None,
        again_values: dict[str, str] | None = None,
        *,
        buttons: bool = True,
        keyboard_own: bool = True,
    ):
        """``_say`` 的「消息对象版」：``message`` 一般来自 ``event.plain_result(文字)``。

        存在的意义：让几百处已经在用的 ``yield event.plain_result(...)`` 只需要
        在外面套一层就能带上场景按钮与文案覆盖，不用把里面的文案重写一遍。
        取不到文本时原样交出对象（功能不受影响）。``page`` / ``again_values`` 同 ``_say``，
        ``buttons=False`` / ``keyboard_own=False`` 也同 ``_say``。
        """
        text = _message_text(message)
        if not text:
            yield message
            return
        async for reply in self._say(
            event, text, scene, values, page, again_values,
            buttons=buttons, keyboard_own=keyboard_own,
        ):
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
        if w_max < w_min:
            w_max = w_min
        # ⚠️ 窗口 = 「难度系数 × 天气倍率 × 站长设的基准」，**只做一次夹取**，
        # 夹到站长设的 [window_min, window_max] 里，不做别的加工（v1.18.45）。
        #
        # 以前是「用 min/max 算基准、再按难度做减法、最后 max(2, …) 兜底」：
        #   scaled_max = max(scaled_min+1, round(w_max × (1 − 0.40×diff) × weather))
        # 站长把上下限设成 4~8，实际只得到 3~7（难度还会把它压到 3 秒），
        # **永远到不了他设的 8** —— UI 里那个设置形同虚设。
        #
        # 现在：
        #   * 基准 = w_max（上限）；难度决定「离上限有多远」，最多退到 w_min；
        #   * 天气倍率直接乘上去（再夹回区间）；
        #   * 站长把上下限改大改小都**立刻生效**，没有任何隐含地板。
        # 若站长把上下限设成同一个值（窗口固定），就恒等于该值。
        base = float(w_max)
        if base <= 0:
            base = float(w_min)
        hard_min = float(w_min)
        hard_max = float(w_max)
        # 难度系数：diff = 0（最容易）→ 1.0（贴着上限）；diff = 1（最难）→ w_min/w_max
        if hard_max > hard_min and hard_max > 0:
            easiest, hardest = 1.0, float(hard_min) / float(hard_max)
        else:
            easiest = hardest = 1.0
        curve = _clamp(diff, 0.0, 1.0)
        diff_factor = easiest + (hardest - easiest) * curve
        weather_factor = _clamp(window_mult, 0.0, 10.0)
        center = base * diff_factor * weather_factor
        # 这一竿的区间：以 center 为中心的 ±25% 抖动（同一档鱼也有一点随机）
        scaled_min = _clamp(center * 0.75, hard_min, hard_max)
        scaled_max = _clamp(center * 1.25, hard_min, hard_max)
        if scaled_max < scaled_min:
            scaled_max = scaled_min
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
        # 限定竿「瞬手」（v1.18.63）：拉线一律判「完美」—— 饵不被咬掉、不脱钩。
        # 它是**牺牲换来的**：那根竿手气归零、价值加成也低（见 rod_defs 第 12 段）。
        if _safe_number(spec.get("perfect_pull"), 0.0) > 0:
            return (
                "完美",
                float(self.cfg["perfect_bonus"]),
                float(self.cfg["perfect_escape_factor"]),
                "🎯",
            )
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
        # ⚠️ 注册与这次提示都包在 try 里：
        #    提示要 yield 出去，生成器会**在这里挂起**；消费者在这时取消/关闭生成器
        #    （发送异常、任务取消、插件重载）以前会让下面的 finally 轮不到执行，
        #    于是 _pending_pulls 里永久留一条过期记录。现在注册也在 try 内，
        #    无论从哪条路退出都会清干净（另有 _pull_pending_valid 兜底 deadline）。
        try:
            # ``quiet``：这一条鱼要不要回「✅ 收到，正在收线…」（连钓里逐条弹互动时
            # 每条都回一句就是刷屏，见 ``_pull_is_quiet`` / ``_engine`` 的 quiet_ack）
            quiet = bool(spec.get("quiet_ack"))
            self._pending_pulls[user_id] = {
                "future": future,
                "session": self._session_key(event),
                "deadline": time.monotonic() + window,
                "quiet": quiet,
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
                # 咬钩提示**不占**键盘名额（v1.18.52）：QQ 一条入站消息只有一个键盘，
                # 让位给这一轮最后那条带按钮的回复（单竿 = 钓到鱼的结果 / 连钓 = 战报），
                # 否则连钓里「第一条（咬钩提示）之后全没按钮」—— 站长报的就是这个。
                keyboard_own=False,
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
                        "quiet": quiet,
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
        finally:
            # 正常收尾与「生成器被关闭/取消」都走这里：记录一定被清掉（外层 try 见上）
            self._pending_pulls.pop(user_id, None)

        if not hit:
            # ⚠️ 超时那一刻就把「刚才有鱼咬钩、窗口已经过」记下来（v1.18.45）。
            #    玩家看到这条「超时了」再去发「拉」时，_pending_pulls 早就清了 ——
            #    以前会回一句「现在没有鱼咬钩」，**误导玩家以为压根没鱼**。
            #    记下之后，`_pull_miss_hint()` 就能说清「这一下拉晚了」。
            self._note_pull_window(
                user_id,
                {"deadline": None, "quiet": quiet},
                "timeout",
            )
            if not quiet:
                # ⚠️ v1.18.69：**连钓（quiet_ack）不再逐条播报「吐钩跑了」**。
                #    QQ 官方平台对一条入站消息的被动回复有次数上限（站长说的一次 5 条），
                #    连钓里每跑一条就占掉一次，最后那条**带按钮的渔获/战报**就容易发不出去
                #    （「更有可能没有按钮」）。单竿照旧提示 —— 那边只有一条鱼，不差这一次。
                async for _r in self._say_msg(
                        event, "pull.timeout", event.plain_result(
                            f"💨 超时了——{fish['name']} 吐钩跑了（这一竿的鱼饵已经用掉了）\n"
                            f"　下次在提示的时间内发 /钓鱼 拉 就能拉住它"
                        ), buttons=False, keyboard_own=False):
                    yield _r
            yield {"catch": None, "rating": "失败", "bonus": 0.0}
            return

        # 落点：0~1 的位置，越靠近 center 越准
        pos = _clamp(elapsed / window, 0.0, 1.0) if window > 0 else 0.0
        rating, bonus, factor, mark = self._judge_pull(pos, spec)

        escape = _clamp(spec["escape"] * factor, 0.0, 0.95)
        if random.random() < escape:
            # 记下「这一下确实拉到了、但鱼挣脱了」：之后玩家再补一发「拉」时，
            # 提示会说「这一下拉晚了」而不是「没有鱼咬钩」
            self._note_pull_window(user_id, {"quiet": quiet}, "escape")
            if not quiet:
                # 同上（v1.18.69）：连钓里「挣脱跑了」也不逐条播报，省下被动回复次数
                # 给最后那条带按钮的战报；跑了多少条在战报的「跑掉 N 条」里看得见。
                async for _r in self._say_msg(
                        event, "pull.escape", event.plain_result(
                            f"{mark} {rating}　但线一松——{fish['name']} 挣脱跑了"
                        ), buttons=False, keyboard_own=False):
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
        # 拉上来了：同样记一笔（之后多余的「拉」会得到「鱼已经收上来了」而不是「没鱼」）
        self._note_pull_window(user_id, {"quiet": quiet}, "hit")
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

    def _pull_is_quiet(self, event: AstrMessageEvent) -> bool:
        """这一竿的「拉」要不要回一句「✅ 收到，正在收线…」。

        单竿要回（玩家只拉这一条，回执让人确认指令生效了）；
        **连钓不要**：连钓是逐条弹互动的，玩家得连点好几下，
        每条都回一句「收到」就是刷屏（v1.18.33，站长要求）。

        ⚠️ 必须在 ``_resolve_pull()`` **之前**问 —— 解开等待后这条记录很快就被
        互动收尾清掉了。记录不在（没在等人拉）时返回 False，不影响原有提示。
        """
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            return False
        pending = self._pending_pulls.get(user_id)
        return bool(isinstance(pending, dict) and pending.get("quiet"))

    def _pull_pending_valid(self, user_id: str) -> dict[str, Any] | None:
        """取出该玩家「有效的」拉线等待记录，同时清掉过期的。

        ⚠️ ``deadline`` 以前是**只写不读**的死字段：消费者如果在 ``yield`` 处
        取消/关闭了生成器（发送异常、任务取消、插件重载），注册进去的
        ``_pending_pulls[user_id]`` 就没人清 —— 之后一次 ``/钓鱼 拉`` 会往那个
        早就过期的 future 里 ``set_result``，回一句「✅ 收到，正在收线…」，
        其实根本没有鱼。这里做兜底：过期的直接丢掉。
        """
        pending = self._pending_pulls.get(user_id)
        if not isinstance(pending, dict):
            return None
        deadline = pending.get("deadline")
        if isinstance(deadline, (int, float)) and time.monotonic() > deadline:
            self._pending_pulls.pop(user_id, None)
            # 记下来：这次窗口是**等玩家拉等超时**的，不是「附近根本没有鱼」
            self._note_pull_window(user_id, pending, "timeout")
            return None
        return pending

    def _note_pull_window(
        self, user_id: str, pending: dict[str, Any], outcome: str
    ) -> None:
        """记下最近一次拉线窗口的收尾情况，供 `_pull_miss_hint()` 分辨提示。

        为什么需要它：玩家发「拉」时窗口可能刚刚收尾（提示还在屏幕上、或者点了
        上一条遗留的按钮）。那时候回一句「现在没有鱼咬钩」是**误导** ——
        鱼刚才明明咬过钩，只是这一下晚了。分开说，玩家才知道下次该快一点。
        """
        self._recent_pulls[user_id] = {
            "at": time.monotonic(),
            "deadline": pending.get("deadline"),
            "quiet": bool(pending.get("quiet")),
            "outcome": outcome,
        }

    def _pull_miss_hint(self, user_id: str) -> str:
        """发「拉」但没鱼时，回一句**贴合实情**的话。

        * 刚刚（``PULL_MISS_WINDOW`` 秒内）确实有过一个窗口 -> 说明这一下晚了；
        * 否则就是附近真的没鱼（玩家手滑发的）-> 老提示。
        """
        info = self._recent_pulls.get(user_id)
        if not isinstance(info, dict):
            return ""
        gap = time.monotonic() - float(info.get("at") or 0)
        if gap > PULL_MISS_WINDOW:
            # 过期了就没用了：顺手清掉，别让这张表随「见过的人数」一直长
            self._recent_pulls.pop(user_id, None)
            return ""
        if info.get("outcome") == "hit":
            return "🎣 这一下拉晚了——鱼已经被收上来了（连钓会接着弹下一条）"
        if info.get("outcome") == "escape":
            return "🎣 这一下拉晚了——刚才那条已经挣脱跑了（连钓会接着弹下一条）"
        return (
            "🎣 这一下拉晚了——刚才确实有鱼咬钩，但已经过了窗口/跑了\n"
            "　看到「咬钩了」就**马上**发 /钓鱼 拉（窗口只有几秒）"
        )


    def _resolve_pull(self, event: AstrMessageEvent) -> bool:
        """若该玩家正在等「拉」，唤醒等待。返回是否触发。"""
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            return False
        pending = self._pull_pending_valid(user_id)
        if not pending:
            return False
        session = pending.get("session")
        if session and session != self._session_key(event):
            return False
        future = pending.get("future")
        if future is None or future.done():
            return False
        # 记下「这一下真的拉到了」，供 `_pull_miss_hint()` 分辨「晚了」还是「没鱼」
        self._note_pull_window(user_id, pending, "hit")
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
            # ⚠️ `keyboard_own=False`（v1.18.63）：QQ 一条入站消息只有**第一条**回复能挂键盘，
            #    而这条「前情提要」只是铺垫 —— 名额得留给紧跟其后的**选项**（那才是要点的）。
            #    以前 recap 把名额吃掉，插曲提示就只能退回纯文本，玩家看到「事件没有按钮」。
            async for reply in self._say(event, recap, "story.recap", keyboard_own=False):
                yield reply
        text, _rows = self._event_prompt(
            story, order, text=self._event_text(story, player)
        )
        # ⚠️ 把现算出来的**选项按钮**传进去（v1.18.63 修「事件的按钮怎么没了」）：
        #    以前这里把 _rows 丢掉了，story.prompt 走继承兜底到 story.result，
        #    结果插曲只剩「继续钓 / 看背包」，没有「选项」可点。
        async for reply in self._say(event, text, "story.prompt", extra_rows=_rows):
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

