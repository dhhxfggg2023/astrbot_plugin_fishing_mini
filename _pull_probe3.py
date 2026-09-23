"""临时探针 3：验证「拉晚了」与「真的没鱼」的提示能区分（用完即删）。"""

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
SANDBOX = os.path.join(tempfile.gettempdir(), "fish_pull_probe3")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(SANDBOX, exist_ok=True)
os.environ["ASTRBOT_ROOT"] = SANDBOX

spec = importlib.util.spec_from_file_location("fp3", ROOT / "main.py")
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


async def cmd(p, ev, *a):
    out = []
    async for r in p.fishing(ev, *a):
        out.append(r.text if hasattr(r, "text") else str(r))
    return out


async def main():
    cfg = dict(CFG)
    cfg["backup_dir"] = os.path.join(SANDBOX, "bk")
    cfg["stamina_regen_seconds"] = 0
    cfg["multi_pull_enabled"] = True
    cfg["window_min"] = 3
    cfg["window_max"] = 3
    p = mod.FishingPlugin(context=Ctx(), config=cfg)
    p.name = "astrbot_plugin_fishing_mini"
    p.author = "dhhxfggg"
    p.plugin_id = "dhhxfggg/astrbot_plugin_fishing_mini"
    uid = "90001"

    print("=== 场景 A：从没玩过，直接发「拉」 ===")
    out = await cmd(p, Ev("拉", uid, "A1"), "拉", "", "")
    print("   ", (out[0] if out else "").replace("\n", " / ")[:90])
    print("    含「没有鱼咬钩」:", "没有鱼咬钩" in "".join(out))

    print("\n=== 场景 B：窗口等超时后 0.1 秒再发「拉」 ===")
    pl = await p._load_player(uid)
    pl["gold"] = 10 ** 9
    pl["baits"] = {"secret": 99}
    pl["equipped_bait"] = "secret"
    await p._save_player(pl)
    p._roll_species = lambda *a, **k: next(
        f for f in mod.FISH_POOL if f["rarity"] in ("传说", "神话")
    )
    out2: list[str] = []

    async def consume():
        async for r in p.fishing(Ev("/钓鱼 2", uid), "2", "", ""):
            out2.append(r.text if hasattr(r, "text") else str(r))

    task = asyncio.create_task(consume())
    for _ in range(800):
        if uid in p._pending_pulls:
            break
        await asyncio.sleep(0.005)
    # 硬等过期（窗口 3 秒）
    await asyncio.sleep(3.4)
    got = p._resolve_pull(Ev("拉", uid, "B1"))
    print("    过期后 _resolve_pull ->", got)
    out3 = await cmd(p, Ev("拉", uid, "B2"), "拉", "", "")
    msg = (out3[0] if out3 else "").replace("\n", " / ")
    print("   ", msg[:110])
    print("    分辨出「拉晚了」:", "拉晚了" in msg)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    shutil.rmtree(SANDBOX, ignore_errors=True)
    sys.exit(code)
