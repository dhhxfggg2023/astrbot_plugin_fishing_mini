"""临时探针 2：量「拉」到达时 `_pending_pulls` 的状态，找假报错的时点（用完即删）。"""

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
SANDBOX = os.path.join(tempfile.gettempdir(), "fish_pull_probe2")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(SANDBOX, exist_ok=True)
os.environ["ASTRBOT_ROOT"] = SANDBOX

spec = importlib.util.spec_from_file_location("fp2", ROOT / "main.py")
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


async def scenario(delay: float, label: str) -> None:
    """在提示出现后 delay 秒发「拉」，记录 pending 状态与回执。"""
    cfg = dict(CFG)
    cfg["backup_dir"] = os.path.join(SANDBOX, f"bk_{label}")
    cfg["stamina_regen_seconds"] = 0
    cfg["multi_pull_enabled"] = True
    cfg["window_min"] = 3
    cfg["window_max"] = 3
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
    p._roll_species = lambda *a, **k: next(
        f for f in mod.FISH_POOL if f["rarity"] in ("传说", "神话")
    )

    out: list[str] = []
    marks: list[tuple[float, str, bool]] = []

    async def consume():
        async for r in p.fishing(Ev("/钓鱼 3", uid), "3", "", ""):
            txt = r.text if hasattr(r, "text") else str(r)
            out.append(txt)
            marks.append((time.monotonic(), txt[:24], uid in p._pending_pulls))

    task = asyncio.create_task(consume())
    # 等到出现「咬钩了」再等 delay
    for _ in range(800):
        if any("咬钩了" in t for t in out):
            break
        await asyncio.sleep(0.005)
    await asyncio.sleep(delay)
    before = uid in p._pending_pulls
    ok = p._resolve_pull(Ev("拉", uid, "M9"))
    await asyncio.sleep(0.15)
    tail = [t for t in out if "没有鱼咬钩" in t or "收到，正在收线" in t]
    print(f"[{label}] delay={delay:<5} 拉之前 pending={before!s:<5} _resolve_pull={ok!s:<5} "
          f"回执={tail}")
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def main():
    # 覆盖各种时点：刚弹出、护栏期内、窗口中途、窗口刚过
    for delay, label in (
        (0.0, "立刻"), (0.05, "50ms"), (0.2, "200ms"), (0.3, "护栏内"),
        (1.5, "中途"), (2.9, "快到点"), (3.3, "刚超时"),
    ):
        await scenario(delay, label)
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    shutil.rmtree(SANDBOX, ignore_errors=True)
    sys.exit(code)
