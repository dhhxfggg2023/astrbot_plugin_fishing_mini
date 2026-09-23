"""临时探针：复现「连钓拉线时错误提示『此时没有鱼咬钩』」（用完即删）。

做法：让每个钓点只出「传说/神话」（一定需要拉线），开 multi_pull_enabled，
跑 /钓鱼 N，在等待窗口中途发 /钓鱼 拉，看回执到底是什么。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SANDBOX = os.path.join(tempfile.gettempdir(), "fish_pull_probe")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(SANDBOX, exist_ok=True)
os.environ["ASTRBOT_ROOT"] = SANDBOX

spec = importlib.util.spec_from_file_location("fp", ROOT / "main.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

STORE = {}


class SP:
    async def put_async(self, s, sid, k, v):
        STORE[(s, sid, k)] = v

    async def get_async(self, s, sid, k, d=None):
        return STORE.get((s, sid, k), d)

    async def remove_async(self, s, sid, k):
        STORE.pop((s, sid, k), None)


sp = SP()
import astrbot.core  # noqa: E402

astrbot.core.sp = sp
import astrbot.core.star.base as base  # noqa: E402
import astrbot.core.utils.plugin_kv_store as kv  # noqa: E402

kv.sp = sp
base.sp = sp
from astrbot.core.config.astrbot_config import AstrBotConfig  # noqa: E402

schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8-sig"))
CFG = dict(AstrBotConfig(config_path=os.path.join(SANDBOX, "c.json"), schema=schema))


class R:
    def __init__(self, t):
        self.text = t


class Ev:
    def __init__(self, s="", uid="90001", mid="M1"):
        self.message_str = s
        self._uid = uid
        self.unified_msg_origin = "aiocqhttp:group:123456"
        self.message_obj = type("_M", (), {"message_id": mid})()

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return "测试"

    def get_group_id(self):
        return "123456"

    def get_platform_name(self):
        return "aiocqhttp"

    def plain_result(self, t):
        return R(t)

    def make_result(self, t):
        return R(t)

    async def send(self, r):
        pass


class Ctx:
    star_manager = None

    def get_platform(self, name=None):
        return None


async def main():
    cfg = dict(CFG)
    cfg["backup_dir"] = os.path.join(SANDBOX, "bk")
    cfg["stamina_regen_seconds"] = 0
    cfg["multi_pull_enabled"] = True
    cfg["window_min"] = 4
    cfg["window_max"] = 4
    cfg["multi_cast_max"] = 30
    p = mod.FishingPlugin(context=Ctx(), config=cfg)
    p.name = "astrbot_plugin_fishing_mini"
    p.author = "dhhxfggg"
    p.plugin_id = "dhhxfggg/astrbot_plugin_fishing_mini"

    uid = "90001"
    pl = await p._load_player(uid)
    pl["gold"] = 10 ** 9
    pl["baits"] = {"secret": 999}
    pl["equipped_bait"] = "secret"
    await p._save_player(pl)

    # 只出「传说/神话」：一定需要拉线
    big = {f["id"] for f in mod.FISH_POOL if f["rarity"] in ("传说", "神话")}
    print(f"需要拉线的鱼种: {len(big)} 种")
    ori_species = p._roll_species
    p._roll_species = lambda *a, **k: next(
        f for f in mod.FISH_POOL if f["id"] in big
    )

    out: list[str] = []

    async def consume():
        async for r in p.fishing(Ev("/钓鱼 4", uid), "4", "", ""):
            out.append(r.text if hasattr(r, "text") else str(r))

    task = asyncio.create_task(consume())
    # 等第一条注册
    for _ in range(600):
        if uid in p._pending_pulls:
            break
        await asyncio.sleep(0.01)
    print("第 1 条已注册:", uid in p._pending_pulls)

    # 在窗口中途拉
    await asyncio.sleep(1.6)
    print("发 /钓鱼 拉 时 pending =", uid in p._pending_pulls)
    pulled = p._resolve_pull(Ev("拉", uid, "M2"))
    print("_resolve_pull ->", pulled)

    # 看回执
    await asyncio.sleep(0.3)
    print("\n--- 目前收到的回复 ---")
    for line in "\n".join(out).splitlines():
        print("   ", line)

    # 继续把剩下的拉完
    for _ in range(8):
        if task.done():
            break
        if uid in p._pending_pulls:
            await asyncio.sleep(1.6)
            p._resolve_pull(Ev("拉", uid, "M3"))
        await asyncio.sleep(0.2)
    try:
        await asyncio.wait_for(task, timeout=25)
    except asyncio.TimeoutError:
        print("!! 任务没结束")
        task.cancel()

    body = "\n".join(out)
    print("\n=== 完整战报 ===")
    print(body)
    print("\n=== 检查 ===")
    print("  出现「没有鱼咬钩」:", "没有鱼咬钩" in body)
    p._roll_species = ori_species
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    shutil.rmtree(SANDBOX, ignore_errors=True)
    sys.exit(code)
