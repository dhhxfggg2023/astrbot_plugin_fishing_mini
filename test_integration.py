"""真实 AstrBot 事件链集成测试（不属于插件运行时代码，可随时删除）。

使用 **AstrBot 真实的** AstrMessageEvent / AstrBotMessage / MessageEventResult /
CommandFilter，完整走一遍：
  收到群消息 -> 唤醒判定 -> 匹配 /钓鱼 -> 解析参数 -> 执行 handler -> 生成回复

重点验证：
- 只注册 1 个指令名，且所有子命令都能被真实 CommandFilter 匹配并正确分派
- 多参数子命令的参数解析符合 AstrBot 行为
- 拉线互动在真实事件链下能等待与唤醒（含跨群保护）
- 普通聊天不被吞掉（不干扰 LLM）
"""

from __future__ import annotations

import os
import tempfile

# ⚠️ 必须在导入 astrbot 之前设置，避免 AstrBot 把 cwd 当根目录而污染插件目录
_SANDBOX_ROOT = os.path.join(tempfile.gettempdir(), "astrbot_plugin_fishing_test")
os.makedirs(_SANDBOX_ROOT, exist_ok=True)
os.environ["ASTRBOT_ROOT"] = _SANDBOX_ROOT

import asyncio  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

PLUGIN_DIR = Path(__file__).parent

spec = importlib.util.spec_from_file_location(
    "astrbot_plugin_qq_fishing_itest", PLUGIN_DIR / "main.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

# ---------------------------------------------------------------------------
import astrbot.core  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.message.message_event_result import MessageEventResult  # noqa: E402
from astrbot.core.platform import (  # noqa: E402
    AstrBotMessage,
    AstrMessageEvent,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.core.platform.astrbot_message import Group  # noqa: E402
from astrbot.core.star.filter.command import CommandFilter  # noqa: E402
from astrbot.core.star.star_handler import star_handlers_registry  # noqa: E402

STORE: dict[tuple[str, str, str], object] = {}


class FakeSP:
    async def put_async(self, scope, scope_id, key, value):
        STORE[(scope, scope_id, key)] = value

    async def get_async(self, scope, scope_id, key, default=None):
        return STORE.get((scope, scope_id, key), default)

    async def remove_async(self, scope, scope_id, key):
        STORE.pop((scope, scope_id, key), None)


fake_sp = FakeSP()
astrbot.core.sp = fake_sp
import astrbot.core.star.base as base  # noqa: E402
import astrbot.core.utils.plugin_kv_store as kv  # noqa: E402

kv.sp = fake_sp
base.sp = fake_sp


def load_config() -> dict:
    from astrbot.core.config.astrbot_config import AstrBotConfig

    schema = json.loads((PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8-sig"))
    path = os.path.join(_SANDBOX_ROOT, "itest_fishing_config.json")
    if os.path.exists(path):
        os.remove(path)
    config = dict(AstrBotConfig(config_path=path, schema=schema))
    config["bait_hook_rates"] = CFG_HOOK_1   # 集成测试固定必定上钩
    # 杂物与鱼互斥（一竿只出一样）：这条链路要验证的是「上鱼」，
    # 所以把杂物掉率关掉，否则有 14% 的概率这一竿出的是杂物，断言会偶发失败。
    config["item_drop_chance"] = 0.0
    return config


class FakeContext:
    def __init__(self):
        self._cfg = {"admins_id": ["admin_001"], "timezone": "Asia/Shanghai"}

    def get_config(self):
        return self._cfg


PLATFORM_META = PlatformMetadata(name="aiocqhttp", description="QQ 个人号", id="aiocqhttp")


CFG_HOOK_1 = ",".join(
    f"{b}:1.0" for b in ("none","bread","worm","bloodworm","corn","shrimp","livebait","secret")
)


def make_event(message_str, user_id, nickname, role="member", group_id="123456"):
    msg = AstrBotMessage()
    msg.type = MessageType.GROUP_MESSAGE
    msg.self_id = "3889000000"
    msg.session_id = f"group_{group_id}"
    msg.message_id = "msg_1"
    msg.group = Group(group_id=group_id, group_name="测试群")
    msg.sender = MessageMember(user_id=user_id, nickname=nickname)
    msg.message = [Plain(text=message_str)]
    msg.message_str = message_str
    msg.raw_message = {"post_type": "message", "raw": message_str}

    event = AstrMessageEvent(
        message_str=message_str,
        message_obj=msg,
        platform_meta=PLATFORM_META,
        session_id=f"group_{group_id}",
    )
    event.role = role
    return event


def apply_waking_stage(event, wake_prefixes=("/",)) -> None:
    """复刻 AstrBot WakingCheckStage：去唤醒前缀 + 置 is_at_or_wake_command。"""
    event.message_str = event.message_str.strip()
    for prefix in wake_prefixes:
        if event.message_str.startswith(prefix):
            event.is_at_or_wake_command = True
            event.is_wake = True
            event.message_str = event.message_str[len(prefix) :].strip()
            return
    if event.is_private_chat():
        event.is_at_or_wake_command = True
        event.is_wake = True


def find_cmd_filter():
    for h in star_handlers_registry:
        if (
            getattr(h, "handler_module_path", None) == mod.__name__
            and h.handler_name == "fishing"
        ):
            f = next(
                (x for x in h.event_filters if isinstance(x, CommandFilter)), None
            )
            f.init_handler_md(h)
            return f
    raise AssertionError("未找到 /钓鱼 指令注册")


async def wait_for_pending(plugin, user_id, tries=500):
    """按 user_id 精确等待互动注册（避免命中上一轮的残留条目）。"""
    for _ in range(tries):
        if user_id in plugin._pending_pulls:
            return True
        await asyncio.sleep(0.01)
    return False


async def send(plugin, message, user_id, nickname="群友", role="member", group_id="123456"):
    event = make_event(message, user_id, nickname, role, group_id)
    apply_waking_stage(event)
    cmd_filter = find_cmd_filter()
    if not cmd_filter.filter(event, FakeContext().get_config()):
        return [], False, event
    params = event.get_extra("parsed_params", default={}) or {}
    replies = []
    async for result in plugin.fishing(event, **params):
        if isinstance(result, MessageEventResult):
            replies.append(result.get_plain_text())
    return replies, True, event


async def send_auto(plugin, message, user_id, nickname="群友", role="member"):
    """发消息；若进入拉线等待，立刻替玩家发「拉」。

    注意：这里会**临时把逃脱率清零**，让「拉了就一定上鱼」成为确定行为，
    否则断言会因随机逃脱而抖动。（逃脱行为在 test_local.py 里用可控参数专测。）
    """
    saved_escape = dict(plugin.escape_map)
    plugin.escape_map = {k: 0.0 for k in plugin.escape_map}
    try:
        event = make_event(message, user_id, nickname, role)
        apply_waking_stage(event)
        cmd_filter = find_cmd_filter()
        if not cmd_filter.filter(event, FakeContext().get_config()):
            return [], False, event
        params = event.get_extra("parsed_params", default={}) or {}
        replies = []

        async def drive():
            async for result in plugin.fishing(event, **params):
                if isinstance(result, MessageEventResult):
                    replies.append(result.get_plain_text())

        task = asyncio.create_task(drive())
        await wait_for_pending(plugin, user_id)
        if user_id in plugin._pending_pulls:
            plugin._resolve_pull(event)
        await task
        return replies, True, event
    finally:
        plugin.escape_map = saved_escape


def text_of(replies):
    return "\n".join(replies)


async def main():
    cfg = load_config()
    plugin = mod.FishingPlugin(context=FakeContext(), config=cfg)
    plugin.name = "astrbot_plugin_qq_fishing"
    plugin.author = "dhhxfggg"
    plugin.plugin_id = "dhhxfggg/astrbot_plugin_qq_fishing"

    failures = []

    def check(cond, label):
        if cond:
            print(f"  ✅ {label}")
        else:
            print(f"  ❌ {label}")
            failures.append(label)

    print("=" * 64)
    print("真实 AstrMessageEvent + 真实 CommandFilter 集成测试")
    print("=" * 64)

    # -----------------------------------------------------------------
    print("\n[1] 只注册 1 个指令名")
    names = []
    for h in star_handlers_registry:
        if getattr(h, "handler_module_path", "") == mod.__name__:
            for f in getattr(h, "event_filters", []):
                if getattr(f, "command_name", None):
                    names.append(f.command_name)
    check(names == ["钓鱼"], f"只注册 /钓鱼（避免撞名）-> {names}")

    # -----------------------------------------------------------------
    print("\n[2] /钓鱼 下竿")
    replies, matched, _ = await send_auto(plugin, "/钓鱼", "90001", "群友甲")
    check(matched, "/钓鱼 被 CommandFilter 匹配")
    check(len(replies) >= 1, f"有回复（{len(replies)} 条）")
    for line in text_of(replies).splitlines():
        print(f"    {line}")
    p = await plugin._load_player("90001")
    # 新经济：下竿免费（fish_cost 默认 0），空钩不花任何钱；
    # 只有挂了鱼饵才扣 1 个库存（买的时候已经付过钱，不再重复收费）。
    fee = int(plugin.cfg["fish_cost"])
    if fee <= 0:
        check(p["gold"] >= 100, f"空钩下竿不收费 -> {p['gold']}")
    else:
        check(p["gold"] <= 100 - fee, f"已扣费（钓费 {fee}）-> {p['gold']}")
    check(len(p["inventory"]) == 1, "鱼已入包")

    # -----------------------------------------------------------------
    print("\n[3] 全部子命令可被真实匹配并分派")
    subs = [
        ("/钓鱼 帮助", "帮助 1/"),
        ("/钓鱼 背包", "背包"),
        ("/钓鱼 商店", "商店"),
        ("/钓鱼 图鉴", "图鉴"),
        ("/钓鱼 水族馆", "水族馆"),
        ("/钓鱼 金币", "档案"),
        ("/钓鱼 签到", "签到"),
    ]
    for cmd, kw in subs:
        replies, matched, _ = await send(plugin, cmd, "90001", "群友甲")
        check(matched, f"{cmd} 被匹配")
        check(kw in text_of(replies), f"{cmd} -> 含「{kw}」")

    # 单条消息类子命令只应回一条：曾经因为分派器重复 yield 导致整段刷两遍
    dupes = []
    for cmd in ("/钓鱼 帮助", "/钓鱼 背包", "/钓鱼 排行榜", "/钓鱼 今日"):
        replies, matched, _ = await send(plugin, cmd, "90001", "群友甲")
        if len(replies) != 1:
            dupes.append(f"{cmd}×{len(replies)}")
    check(not dupes, f"子命令只回一条消息（异常项：{dupes or '无'}）")

    # 下竿类同义词（空格/用词容错）不能被当成未知用法
    unknown = []
    for word in ("钓", "钓鱼", "下竿", "抛竿", "甩竿", "下钩", "cast", "fish"):
        replies, matched, _ = await send(plugin, f"/钓鱼 {word}", "90001", "群友甲")
        if not matched or "不认识" in text_of(replies):
            unknown.append(word)
    check(not unknown, f"下竿同义词都能识别（失败项：{unknown or '无'}）")

    # -----------------------------------------------------------------
    print("\n[4] 多参数子命令参数解析")
    p = await plugin._load_player("90002")
    p["gold"] = 5000
    p["inventory"] = [
        mod._new_instance("carp", 1.0, value_override=100,
                          attrs={"meat": 60, "spirit": 60, "sheen": 60}),
        mod._new_instance("koi", 1.5, value_override=900,
                          attrs={"meat": 80, "spirit": 80, "sheen": 80}),
    ]
    await plugin._save_player(p)

    replies, matched, _ = await send(plugin, "/钓鱼 卖 白条", "90002", "群友乙")
    check(matched, "/钓鱼 卖 <鱼名> 被匹配")
    check("没有" in text_of(replies), "不存在的鱼有提示")

    replies, matched, _ = await send(plugin, "/钓鱼 卖 鲤鱼", "90002", "群友乙")
    check(matched, "/钓鱼 卖 鲤鱼 被匹配")
    p = await plugin._load_player("90002")
    check(len(p["inventory"]) == 1, f"卖掉鲤鱼 -> 剩 {len(p['inventory'])}")

    replies, matched, _ = await send(plugin, "/钓鱼 商店 买 高级饲料", "90002", "群友乙")
    check(matched, "/钓鱼 商店 买 <道具> 被匹配")
    p = await plugin._load_player("90002")
    check(p["items"].get("feed_premium") == 1, f"买到道具 -> {p['items']}")

    # 水族馆放 + 投喂（三步参数）
    replies, matched, _ = await send(plugin, "/钓鱼 水族馆 放 1", "90002", "群友乙")
    check(matched, "/钓鱼 水族馆 放 1 被匹配")
    p = await plugin._load_player("90002")
    check(len(p["aquarium"]) == 1, f"放入水族馆 -> {len(p['aquarium'])}")

    replies, matched, _ = await send(plugin, "/钓鱼 用 高级饲料 1", "90002", "群友乙")
    check(matched, "/钓鱼 用 <道具> <栏位> 被匹配")
    print("    " + text_of(replies).replace("\n", "\n    "))
    p = await plugin._load_player("90002")
    check(p["aquarium"][0]["feed_uses"] == 1, "投喂成功")

    replies, matched, _ = await send(plugin, "/钓鱼 水族馆 取 1", "90002", "群友乙")
    check(matched, "/钓鱼 水族馆 取 1 被匹配")
    p = await plugin._load_player("90002")
    check(len(p["aquarium"]) == 0 and len(p["inventory"]) == 1,
        f"取出回背包（水族馆 {len(p['aquarium'])}，背包 {len(p['inventory'])}）",
    )

    # 批量参数（用户反馈「一次只能卖两条 / 只能放一条」）
    p = await plugin._load_player("90002")
    p["inventory"] = [
        mod._new_instance("carp", 1.0, value_override=20,
                          attrs={"meat": 60, "spirit": 60, "sheen": 60})
        for _ in range(6)
    ]
    p["aquarium"] = []
    await plugin._save_player(p)

    replies, matched, _ = await send(plugin, "/钓鱼 卖 1 2 3", "90002", "群友乙")
    check(matched, "/钓鱼 卖 1 2 3 被匹配")
    p = await plugin._load_player("90002")
    check(len(p["inventory"]) == 3, f"一次卖 3 条 -> 剩 {len(p['inventory'])} 条")

    replies, matched, _ = await send(plugin, "/钓鱼 水族馆 放 1 2", "90002", "群友乙")
    check(matched, "/钓鱼 水族馆 放 1 2 被匹配")
    p = await plugin._load_player("90002")
    check(
        len(p["aquarium"]) == 2 and len(p["inventory"]) == 1,
        f"一次放 2 条 -> 馆 {len(p['aquarium'])} / 包 {len(p['inventory'])}",
    )

    replies, matched, _ = await send(plugin, "/钓鱼 水族馆 取 全部", "90002", "群友乙")
    check(matched, "/钓鱼 水族馆 取 全部 被匹配")
    p = await plugin._load_player("90002")
    check(len(p["aquarium"]) == 0, "取 全部 清空水族馆")

    # 少打空格的写法在真实事件链里也要能用
    replies, matched, _ = await send(plugin, "/钓鱼 卖1 2", "90002", "群友乙")
    check(matched, "/钓鱼 卖1 2 被匹配（粘连写法）")
    check("不认识" not in text_of(replies), "粘连写法没有被当成未知用法")
    p = await plugin._load_player("90002")
    check(len(p["inventory"]) == 1, f"粘连写法同样卖掉 2 条 -> 剩 {len(p['inventory'])}")

    # -----------------------------------------------------------------
    print("\n[5] 拉线互动：真实事件链下的等待与唤醒")
    legend = next(f for f in mod.FISH_POOL if f["rarity"] == "传说")
    orig_roll = mod._roll_species if hasattr(mod, "_roll_species") else None
    orig = plugin._roll_species
    plugin._roll_species = lambda bait_id, location_id, weather=None: legend
    saved_escape = dict(plugin.escape_map)
    plugin.escape_map = {k: 0.0 for k in plugin.escape_map}  # 关掉逃脱，保证确定性
    try:
        p = await plugin._load_player("90003")
        p["gold"] = 5000
        p["last_fish_time"] = 0
        await plugin._save_player(p)

        event = make_event("/钓鱼", "90003", "欧皇")
        apply_waking_stage(event)
        find_cmd_filter().filter(event, FakeContext().get_config())
        params = event.get_extra("parsed_params", default={}) or {}
        replies = []

        async def drive():
            async for r in plugin.fishing(event, **params):
                if isinstance(r, MessageEventResult):
                    replies.append(r.get_plain_text())

        task = asyncio.create_task(drive())
        registered = await wait_for_pending(plugin, "90003")
        check(registered, "传说鱼触发拉线等待")

        pull_replies, pull_matched, _ = await send(plugin, "/钓鱼 拉", "90003", "欧皇")
        check(pull_matched, "/钓鱼 拉 被真实 CommandFilter 匹配")
        check("收线" in text_of(pull_replies), "返回「收线中」")

        await asyncio.wait_for(task, timeout=10)
        joined = text_of(replies)
        check("咬钩" in joined, "提示过玩家拉线")
        check("上鱼" in joined or "钓" in joined, "拉上来后成功上鱼")
        print("    " + joined.replace("\n", "\n    "))
        p = await plugin._load_player("90003")
        check(len(p["inventory"]) == 1, "传说鱼已入包")

        # 超时 -> 逃脱
        p["gold"] = 5000
        p["last_fish_time"] = 0
        await plugin._save_player(p)
        saved_win = plugin.cfg["window_min"], plugin.cfg["window_max"]
        plugin.cfg["window_min"] = 1
        plugin.cfg["window_max"] = 1
        try:
            replies2, _, _ = await send(plugin, "/钓鱼", "90003", "欧皇")
            joined2 = text_of(replies2)
            check("跑" in joined2, "超时后鱼逃脱")
        finally:
            plugin.cfg["window_min"], plugin.cfg["window_max"] = saved_win
        check(not plugin._pending_pulls, "超时后等待表已清理")
    finally:
        plugin._roll_species = orig
        plugin.escape_map = saved_escape

    # -----------------------------------------------------------------
    print("\n[6] 跨群保护")
    plugin._roll_species = lambda bait_id, location_id, weather=None: legend
    plugin.escape_map = {k: 0.0 for k in plugin.escape_map}
    try:
        p = await plugin._load_player("90004")
        p["gold"] = 5000
        p["last_fish_time"] = 0
        await plugin._save_player(p)

        event = make_event("/钓鱼", "90004", "跨群", group_id="123456")
        apply_waking_stage(event)
        find_cmd_filter().filter(event, FakeContext().get_config())
        params = event.get_extra("parsed_params", default={}) or {}

        async def drive2():
            async for _ in plugin.fishing(event, **params):
                pass

        task = asyncio.create_task(drive2())
        check(await wait_for_pending(plugin, "90004"), "已进入拉线等待")

        await send(plugin, "/钓鱼 拉", "90004", "跨群", group_id="999999")
        check("90004" in plugin._pending_pulls, "别的群发「拉」不误触发")

        plugin._resolve_pull(event)
        await asyncio.wait_for(task, timeout=10)
        check(not plugin._pending_pulls, "本群发「拉」才生效")
    finally:
        plugin._roll_species = orig
        plugin.escape_map = saved_escape

    # -----------------------------------------------------------------
    print("\n[7] 不干扰 LLM：普通聊天不被匹配")
    for text in ("今天天气不错", "钓鱼", "我想去钓鱼", "/钓", "/钓鱼x"):
        replies, matched, _ = await send(plugin, text, "90001", "群友甲")
        check(not matched, f"「{text}」不被匹配")
    # 注：「/钓鱼 帮助x」会被 AstrBot 判为 /钓鱼 指令 + 参数「帮助x」（这是它一贯的
    # 前缀匹配行为，任何插件都一样），不属于「吞掉普通聊天」。
    replies, matched, _ = await send(plugin, "/钓鱼 帮助x", "90001", "群友甲")
    check(matched, "「/钓鱼 帮助x」按指令处理（AstrBot 参数解析惯例）")
    check("不认识" in text_of(replies), "  └ 未知子命令有友好提示")

    # -----------------------------------------------------------------
    print("\n[8] 已删除的指令：赠送金币 / 给鱼")
    for text in (
        "/钓鱼 给鱼 90005 鲲 2",
        "/钓鱼 赠送 90001 100",
        "/钓鱼 送 90001 100",
    ):
        replies, _, _ = await send(plugin, text, "90001", "群友甲")
        check("不认识" in text_of(replies), f"「{text}」提示不认识（功能已删除）")
    p5 = await plugin._load_player("90005")
    check(not p5["inventory"], "没人能再凭空拿到鱼（给鱼已删除）")

    # -----------------------------------------------------------------
    print("\n[9] 持久化（真实事件链之后数据仍在）")
    plugin2 = mod.FishingPlugin(context=FakeContext(), config=cfg)
    plugin2.plugin_id = plugin.plugin_id
    p2 = await plugin2._load_player("90001")
    check(p2["total_caught"] >= 1, f"90001 的渔获记录保留 -> {p2['total_caught']}")
    check(len(p2["inventory"]) >= 1, f"新实例仍能读到背包 -> {len(p2['inventory'])} 条")

    print("\n" + "=" * 64)
    if failures:
        print(f"❌ {len(failures)} 项未通过：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("🎉 集成测试全部通过！")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
