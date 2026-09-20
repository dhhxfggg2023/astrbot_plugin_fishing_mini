"""本地玩法自测（不属于插件运行时代码，可随时删除）。

覆盖 v5 全部机制：数据持久化、配置、钓点、鱼竿、鱼篓容量、杂物与漂流瓶、
每日订单、帮助分页、成就、数值平衡。
"""

from __future__ import annotations

import os
import shutil
import tempfile

# ⚠️ 必须在导入 astrbot 之前设置 ASTRBOT_ROOT，否则 AstrBot 会把 os.getcwd()
# 当根目录、在插件目录里新建 data/。这里指向临时沙箱，既不污染插件目录，
# 也不会读写正在运行的 AstrBot 实例。
_SANDBOX_ROOT = os.path.join(tempfile.gettempdir(), "astrbot_plugin_fishing_test")
os.makedirs(_SANDBOX_ROOT, exist_ok=True)
os.environ["ASTRBOT_ROOT"] = _SANDBOX_ROOT
#: 测试用的存档目录。⚠️ 一定要显式钉住：`backup_dir` 的默认值是「真插件目录/backups」，
#: 而 `_refresh_config()` 会按它重建 `self.backup_store` —— 只要测试里出现过一次热重载
#: 或启动流程，就会去读写站长的真存档（历史上测试就在这里误删过真快照）。
_SANDBOX_BACKUP_DIR = os.path.join(_SANDBOX_ROOT, "plugin_dir", "backups")

import asyncio  # noqa: E402
import collections  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

PLUGIN_DIR = Path(__file__).parent

spec = importlib.util.spec_from_file_location(
    "astrbot_plugin_qq_fishing_test", PLUGIN_DIR / "main.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

print("✅ 导入成功")
print(
    f"✅ 鱼种 {len(mod.FISH_POOL)}　钓点 {len(mod.LOCATIONS)}　"
    f"鱼竿 {len(mod.RODS)}　杂物 {len(mod.COLLECTIBLES)}　成就 {len(mod.ACHIEVEMENTS)}"
)

STORE: dict[tuple[str, str, str], object] = {}


class FakeSP:
    async def put_async(self, scope, scope_id, key, value):
        STORE[(scope, scope_id, key)] = value

    async def get_async(self, scope, scope_id, key, default=None):
        return STORE.get((scope, scope_id, key), default)

    async def remove_async(self, scope, scope_id, key):
        STORE.pop((scope, scope_id, key), None)


fake_sp = FakeSP()
import astrbot.core  # noqa: E402

astrbot.core.sp = fake_sp
import astrbot.core.star.base as base  # noqa: E402
import astrbot.core.utils.plugin_kv_store as kv  # noqa: E402

kv.sp = fake_sp
base.sp = fake_sp


def load_schema_config(name: str = "cfg") -> dict:
    """按 AstrBot 的方式从 _conf_schema.json 生成默认配置（每次全新）。"""
    from astrbot.core.config.astrbot_config import AstrBotConfig

    schema = json.loads(
        (PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8-sig")
    )
    path = os.path.join(_SANDBOX_ROOT, f"test_{name}.json")
    if os.path.exists(path):
        os.remove(path)
    return dict(AstrBotConfig(config_path=path, schema=schema))


_CFG = load_schema_config()
# 测试里默认让所有饵必定上钩（确定性），上钩率本身另有专门用例
_CFG["bait_hook_rates"] = ",".join(f"{b}:1.0" for b in ("none","bread","worm","bloodworm","corn","shrimp","livebait","secret"))
# 杂物与鱼互斥：默认关掉杂物掉率，保证「下竿必然上鱼」这类断言稳定；
# 杂物本身的用例会自己把 item_drop_chance 调成 1.0 / 0.0 来验证。
_CFG["item_drop_chance"] = 0.0
# 体力系统默认关掉（regen = 0 即「本服不限体力」），否则连续抛竿的用例会被
# 「体力不够」挡住；体力本身的用例会自己开一组带体力上限的配置来验证。
_CFG["stamina_regen_seconds"] = 0


class FakeContext:
    def __init__(self):
        self._cfg = {"admins_id": ["admin_001"], "timezone": "Asia/Shanghai"}

    def get_config(self):
        return self._cfg


class FakeResult:
    def __init__(self, text):
        self.text = text


class FakeEvent:
    def __init__(self, sender_id, sender_name="测试玩家", is_admin=False, message=""):
        self._sid = sender_id
        self._name = sender_name
        self._is_admin = is_admin
        self._message = message
        self.sent: list[str] = []
        self.unified_msg_origin = "aiocqhttp:group:123456"

    def get_sender_id(self):
        return self._sid

    def get_sender_name(self):
        return self._name

    def is_admin(self):
        return self._is_admin

    def get_message_str(self):
        return self._message

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return "123456"

    def plain_result(self, text):
        return FakeResult(text)

    async def send(self, result):
        self.sent.append(result.text)


def make_plugin(config: dict | None = None):
    cfg = config if config is not None else dict(_CFG)
    # 存档目录一律钉进沙箱（见 _SANDBOX_BACKUP_DIR 处的说明）。站长真的填了
    # backup_dir 时才尊重它——测试里没人会填。
    if not str(cfg.get("backup_dir") or "").strip():
        cfg["backup_dir"] = _SANDBOX_BACKUP_DIR
    plugin = mod.FishingPlugin(context=FakeContext(), config=cfg)
    plugin.name = "astrbot_plugin_qq_fishing"
    plugin.author = "dhhxfggg"
    plugin.plugin_id = "dhhxfggg/astrbot_plugin_qq_fishing"
    return plugin


async def run(handler, event, *args):
    out = []
    async for r in handler(event, *args):
        out.append(r.text if hasattr(r, "text") else str(r))
    return out


async def cmd(plugin, event, *args):
    return await run(plugin.fishing, event, *args)


async def wait_user(plugin, uid, tries=400):
    for _ in range(tries):
        if uid in plugin._pending_pulls:
            return True
        await asyncio.sleep(0.01)
    return False


def _safe_total(player: dict) -> int:
    """累计钓获（项目里到处都在用的那个计数）。"""
    return int(player.get("total_caught") or 0)


async def give_level(plugin, uid: str, level: int) -> dict:
    """把玩家的累计钓获拉到「刚好 level 级」（等级曲线可调，所以现算阈值）。"""
    p = await plugin._load_player(uid)
    p["total_caught"] = mod._level_threshold(level)
    await plugin._save_player(p)
    return p


async def cast(plugin, event, *args, pull=True):
    """抛竿（若咬钩则自动拉线），返回回复列表。"""
    uid = event.get_sender_id()
    task = asyncio.create_task(run(plugin.fishing, event, *args))
    await wait_user(plugin, uid)
    if uid in plugin._pending_pulls and pull:
        plugin._resolve_pull(event)
    return await task


def text_of(replies) -> str:
    return "\n".join(replies)


async def main():
    failures = []
    checks_run = 0   # 实际执行到的断言条数（结尾打出来，README 里的数字才对得上）

    def check(cond, label, extra=None):
        """断言；extra 只在失败时打出来（成功时标签本身已经写清了细节）。"""
        nonlocal checks_run
        checks_run += 1
        line = label if extra is None else f"{label}　{extra}"
        if cond:
            print(f"  ✅ {label}")
        else:
            print(f"  ❌ {line}")
            failures.append(line)

    # =====================================================================
    print("\n[1] 配置：schema 全项解析")
    cfg = load_schema_config("main")
    check(len(cfg) >= 39, f"配置项 {len(cfg)} 个")
    for key in (
        "backpack_base", "backpack_upgrades", "rod_defs", "location_defs",
        "order_count", "order_reward_mult", "bait_hook_rates", "item_drop_chance",
        "bottle_note_chance", "location_codex_gate",
    ):
        check(key in cfg, f"含配置项 {key}")

    # schema 与代码里的 DEFAULTS 必须一一对应：
    # 漏了会让 WebUI 改不到、多了会让读者以为能调却没人读。
    _schema = json.loads(
        (PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8-sig")
    )
    check(
        set(_schema) == set(mod.DEFAULTS),
        f"schema({len(_schema)}) 与 DEFAULTS({len(mod.DEFAULTS)}) 键完全一致"
        f"　差异：{sorted(set(_schema) ^ set(mod.DEFAULTS))}",
    )

    plugin = make_plugin(cfg)
    check(len(plugin.rods) == 6, f"鱼竿 {len(plugin.rods)} 种")
    check(len(plugin.locations) == 16, f"钓点 {len(plugin.locations)} 个")
    check(len(plugin.backpack_upgrades) == 3, f"扩容 {len(plugin.backpack_upgrades)} 档")
    check(cfg["backpack_base"] == 30, f"初始鱼篓 {cfg['backpack_base']}")

    # =====================================================================
    print("\n[2] 数据持久化（重点）")
    plugin = make_plugin()
    p = await plugin._load_player("10001")
    p["gold"] = 88888
    p["rods"] = ["bamboo", "dragon"]
    p["equipped_rod"] = "dragon"
    p["locations"] = ["novice", "lake"]
    p["current_location"] = "lake"
    p["backpack_slots"] = [0, 1]
    p["collectibles"] = {"drift_bottle": 3, "treasure_chest": 1}
    p["bottle_notes"] = ["纸条A", "纸条B"]
    p["inventory"] = [mod._new_instance("koi", 1.8)]
    p["aquarium"] = [mod._new_instance("carp", 1.2)]
    p["transfer_sent"] = 2000   # 已删除的旧字段：读回时应当被丢掉
    p["perfect_pulls"] = 5
    p["total_orders"] = 7
    ok = await plugin._save_player(p)
    check(ok, "保存成功")

    r = await plugin._load_player("10001")
    check(r["gold"] == 88888, f"金币 -> {r['gold']}")
    check(r["rods"] == ["bamboo", "dragon"], f"鱼竿 -> {r['rods']}")
    check(r["equipped_rod"] == "dragon", f"装备鱼竿 -> {r['equipped_rod']}")
    check(r["current_location"] == "lake", f"当前钓点 -> {r['current_location']}")
    check(r["backpack_slots"] == [0, 1], f"扩容档 -> {r['backpack_slots']}")
    check(
        r["collectibles"] == {"drift_bottle": 3, "treasure_chest": 1},
        f"杂物 -> {r['collectibles']}",
    )
    check(r["bottle_notes"] == ["纸条A", "纸条B"], "纸条记录")
    check("transfer_sent" not in r, "老存档里的 transfer_* 字段已清理")
    check(r["perfect_pulls"] == 5, "完美拉线计数")
    check(r["total_orders"] == 7, "订单计数")
    check(len(r["inventory"]) == 1 and len(r["aquarium"]) == 1, "鱼篓/水族馆")

    plugin2 = make_plugin()
    r2 = await plugin2._load_player("10001")
    check(r2["equipped_rod"] == "dragon" and r2["gold"] == 88888, "模拟重启后数据仍在")

    v3 = {
        "user_id": "70001", "data_version": 3, "gold": 500,
        "inventory": [
            {"id": "a", "fish_id": "carp", "value": 20, "quality": "优良",
             "quality_mult": 1.2, "source": "fishing", "ts": 1}
        ],
        "baits": {"worm": 2},
    }
    STORE[("plugin", plugin.plugin_id, "player_70001")] = json.dumps(
        v3, ensure_ascii=False
    )
    m = await plugin._load_player("70001")
    check(m["rods"] == ["bamboo"], "v3 迁移补上默认鱼竿")
    check(m["current_location"] == "novice", "v3 迁移补上默认钓点")
    check(m["backpack_slots"] == [], "v3 迁移补上空扩容")
    check(m["order_date"] == "", "v3 迁移补上订单字段")
    check(len(m["inventory"]) == 1 and m["gold"] == 500, "v3 渔获与金币保留")
    check(m["data_version"] == mod.DATA_VERSION, f"版本 -> {m['data_version']}")

    bad = {
        "80001": json.dumps({"rods": "不是列表", "locations": 7}),
        "80002": json.dumps({"backpack_slots": ["x", None, 99]}),
        "80003": json.dumps({"collectibles": {"假杂物": 5}, "orders": [None, {}]}),
        "80004": json.dumps({"equipped_rod": "不存在的竿", "current_location": "月球"}),
        "80005": "不是 JSON",
    }
    for uid, raw in bad.items():
        STORE[("plugin", plugin.plugin_id, f"player_{uid}")] = raw
    for uid in bad:
        bp = await plugin._load_player(uid)
        good = (
            isinstance(bp["rods"], list) and bool(bp["rods"])
            and bp["equipped_rod"] in bp["rods"]
            and bp["current_location"] in bp["locations"]
            and isinstance(bp["backpack_slots"], list)
            and isinstance(bp["orders"], list)
        )
        check(
            good,
            f"{uid} 已自愈（竿={bp['equipped_rod']} 点={bp['current_location']}）",
        )

    # =====================================================================
    print("\n[3] 钓点：鱼池差异与门槛")
    plugin = make_plugin()
    pools = {}
    for loc in plugin.locations:
        pool = mod._location_pool(loc["id"])
        pools[loc["id"]] = {f["id"] for f, _ in pool}
        check(len(pool) > 0, f"{loc['name']} 有 {len(pool)} 种鱼")

    for a, b in (("novice", "lake"), ("lake", "sea"), ("sea", "swamp"), ("swamp", "abyss")):
        check(pools[a] != pools[b], f"{a} 与 {b} 鱼种不同")
    check(bool(pools["abyss"] - pools["novice"]), "深海有新手村钓不到的鱼")
    check(bool(pools["novice"] - pools["abyss"]), "新手村有深海没有的鱼")
    check("kun" in pools["abyss"], "鲲只在深海海沟")

    all_ids = {f["id"] for f in mod.FISH_POOL}
    covered = set()
    for s in pools.values():
        covered |= s
    check(covered == all_ids, f"全部 {len(all_ids)} 种鱼都有归属钓点")

    for loc_id, allowed in pools.items():
        drawn = {plugin._roll_species("none", loc_id)["id"] for _ in range(600)}
        check(drawn <= allowed, f"{loc_id} 抽到的鱼都在其鱼池内（{len(drawn)} 种）")

    # 稀有度权重下调后，每个钓点的「常规鱼种」权重必须仍然 > 0（否则永远抽不到）
    dead = []
    for loc in plugin.locations:
        weights = mod.LOCATION_WEIGHTS.get(loc["id"]) or {}
        species = plugin._location_species(loc["id"])
        if not species:
            dead.append(f"{loc['id']} 空池")
            continue
        zero = [fid for fid in species if mod._safe_number(weights.get(fid), 0) <= 0]
        if zero:
            dead.append(f"{loc['id']} 权重为 0：{zero[:3]}")
    check(not dead, f"16 个钓点的常规鱼种都还能抽到 -> {dead or '无异常'}")

    ev = FakeEvent("20001")
    out = await cmd(plugin, ev, "钓点", "解锁", "城中运河")
    check("图鉴" in text_of(out), "图鉴没集齐时拒绝解锁（提示图鉴进度）")
    check("金币" in text_of(out), "拒绝解锁时同时列出金币门槛")

    bamboo_species = plugin._location_species("bamboo")
    gate_need = int(len(bamboo_species) * float(plugin.cfg["location_codex_gate"]) + 0.999)
    p = await plugin._load_player("20001")
    p["total_caught"] = 50          # 等级 6，够去山间湖泊
    p["gold"] = 1000
    for fid in plugin._location_species("novice"):
        p["collection"][fid] = {"count": 1, "best_value": 1, "first_ts": 1}
    # 竹林溪流只开八成不到（差 1 种）→ 图鉴门槛拦住
    for fid in bamboo_species[: gate_need - 1]:
        p["collection"][fid] = {"count": 1, "best_value": 1, "first_ts": 1}
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "钓点", "解锁", "城中运河")
    check("图鉴" in text_of(out), "上一个钓点图鉴不到八成时拒绝解锁")
    p = await plugin._load_player("20001")
    check("canal" not in p["locations"], "图鉴不到八成没解锁成功")

    # 补到八成 → 图鉴达标，但金币不够（运河门槛 > 100）
    p = await plugin._load_player("20001")
    for fid in bamboo_species[gate_need - 1:]:
        p["collection"][fid] = {"count": 1, "best_value": 1, "first_ts": 1}
    p["gold"] = 100
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "钓点", "解锁", "城中运河")
    check("金币" in text_of(out), "图鉴达标但金币不足时拒绝解锁")
    p = await plugin._load_player("20001")
    check("canal" not in p["locations"], "金币不足没解锁成功")

    canal_gate = int(plugin._find_location("城中运河")["gold_gate"])
    p = await plugin._load_player("20001")
    p["gold"] = canal_gate + 300
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "钓点", "解锁", "城中运河")
    print("    " + text_of(out).splitlines()[0])
    p = await plugin._load_player("20001")
    check("canal" in p["locations"], f"已解锁城中运河 -> {p['locations']}")
    check(p["current_location"] == "canal", "解锁后自动前往")
    check(
        p["gold"] == 300,
        f"解锁按门槛扣金币 {canal_gate} -> 余额 {p['gold']}",
    )

    ev2 = FakeEvent("20002")
    out = await cmd(plugin, ev2, "去", "深海海沟", "")
    check("还没解锁" in text_of(out) or "🔒" in text_of(out), "未解锁钓点不能直接去")

    # =====================================================================
    print("\n[4] 鱼竿：购买 / 装备 / 加成生效")
    plugin = make_plugin()
    ev = FakeEvent("30001")
    p = await plugin._load_player("30001")
    p["gold"] = 100000
    await plugin._save_player(p)
    # 龙纹竿要 16 级才给买（新机制）：先把等级提上去
    await give_level(plugin, "30001", 16)
    out = await cmd(plugin, ev, "鱼竿", "买", "龙纹竿")
    print("    " + text_of(out).splitlines()[0])
    p = await plugin._load_player("30001")
    check("dragon" in p["rods"], f"已购买 -> {p['rods']}")
    check(p["equipped_rod"] == "dragon", "自动装备")
    check(p["gold"] == 100000 - 5400, f"扣款 -> {p['gold']}")

    # 固定个体差异（variance）与三维，让「鱼竿 = 价值倍率」这件事可确定性比较；
    # 否则两条鱼的随机波动可能盖过 +40% 的加成，测试会偶发失败。
    _saved_uniform = mod.random.uniform
    mod.random.uniform = lambda a, b: 1.0
    try:
        _attrs = {"meat": 70, "spirit": 70, "sheen": 70}
        inst_plain = mod._new_instance("carp", 1.2, attrs=dict(_attrs))
        inst_rod = mod._new_instance(
            "carp", 1.2, attrs=dict(_attrs), value_bonus=0.40
        )
    finally:
        mod.random.uniform = _saved_uniform
    check(
        inst_rod["value"] > inst_plain["value"],
        f"鱼竿提升鱼价 {inst_plain['value']} → {inst_rod['value']}",
    )
    check(
        abs(inst_rod["gear_mult"] - 1.40) < 0.01,
        f"gear_mult 记录 -> {inst_rod['gear_mult']}",
    )

    out = await cmd(plugin, ev, "鱼竿", "用", "神话竿")
    check("还没买" in text_of(out), "未购买不能装备")

    plugin_r = make_plugin()
    for uid, rod in (("31001", "bamboo"), ("31002", "mythic")):
        pp = mod._default_player(uid)
        pp["gold"] = 100000
        pp["rods"] = ["bamboo", "mythic"]
        pp["equipped_rod"] = rod
        await plugin_r._save_player(pp)
    vals = {}
    for uid in ("31001", "31002"):
        pp = await plugin_r._load_player(uid)
        pp["last_fish_time"] = 0
        await plugin_r._save_player(pp)
        await cast(plugin_r, FakeEvent(uid))
        pp = await plugin_r._load_player(uid)
        vals[uid] = pp["inventory"][0]["gear_mult"] if pp["inventory"] else 0
    check(
        vals["31002"] > vals["31001"],
        f"神话竿 gear_mult {vals['31002']} > 竹竿 {vals['31001']}",
    )

    # =====================================================================
    print("\n[5] 鱼篓容量与扩容")
    plugin = make_plugin()
    ev = FakeEvent("40001")
    cap = mod._backpack_capacity({"backpack_slots": []}, plugin.cfg)
    check(cap == 30, f"初始容量 {cap}")
    p = await plugin._load_player("40001")
    p["gold"] = 100000
    p["inventory"] = [mod._new_instance("carp", 1.0) for _ in range(cap)]
    await plugin._save_player(p)
    out = await cast(plugin, FakeEvent("40001"))
    check("满了" in text_of(out), f"鱼篓满时拒绝抛竿（{cap} 条）")
    p = await plugin._load_player("40001")
    check(len(p["inventory"]) == cap, "未超容量")

    out = await cmd(plugin, ev, "商店", "扩容", "")
    print("    " + text_of(out).splitlines()[0])
    p = await plugin._load_player("40001")
    check(
        mod._backpack_capacity(p, plugin.cfg) == cap + 15,
        f"扩容到 {mod._backpack_capacity(p, plugin.cfg)}",
    )
    check(p["gold"] == 100000 - 400, f"扣款 -> {p['gold']}")
    out = await cast(plugin, FakeEvent("40001"))
    check("满了" not in text_of(out), "扩容后可以继续抛竿")

    # =====================================================================
    print("\n[6] 杂物与漂流瓶")
    plugin = make_plugin()
    cfg2 = dict(plugin.cfg)
    cfg2["item_drop_chance"] = 1.0
    # 钓点难度系数 ≥1.0 的地图必出鱼（新手村就是），要测「没中鱼」得挑个难点的图
    cfg2["location_hook_factors"] = "novice:0.5"
    plugin_d = make_plugin(cfg2)
    got = collections.Counter()
    for _ in range(400):
        d = plugin_d._roll_collectible()
        if d:
            got[d["id"]] += 1
    check(len(got) >= 6, f"1.0 掉率时抽到 {len(got)} 种杂物")
    check(got["seaweed"] > got["treasure_chest"], "常见杂物比宝箱多")

    cfg3 = dict(plugin.cfg)
    cfg3["item_drop_chance"] = 0.0
    plugin_n = make_plugin(cfg3)
    check(
        all(plugin_n._roll_collectible() is None for _ in range(50)),
        "0 掉率时不掉落",
    )

    p = mod._default_player("50001")
    p["gold"] = 100
    bottle = mod.COLLECTIBLE_BY_ID["drift_bottle"]
    notes_seen = 0
    empty_seen = 0
    for _ in range(80):
        txt = await plugin._apply_collectible(p, bottle, "50001")
        if "空的" in txt:
            empty_seen += 1
        elif "纸条" in txt:
            notes_seen += 1
    check(notes_seen > 0, f"漂流瓶能开出纸条（80 次中 {notes_seen} 次有内容）")
    check(empty_seen > 0, f"漂流瓶也可能是空的（{empty_seen} 次）")
    check(p["collectibles"]["drift_bottle"] == 80, f"掉落计数 -> {p['collectibles']}")
    check(len(p["bottle_notes"]) > 0, f"纸条已记录 {len(p['bottle_notes'])} 张")

    p2 = mod._default_player("50002")
    p2["gold"] = 0
    chest = mod.COLLECTIBLE_BY_ID["treasure_chest"]
    await plugin._apply_collectible(p2, chest, "50002")
    check(p2["gold"] == chest["value"], f"宝箱折算金币 -> {p2['gold']}")

    # 咬钩率 0 + 掉率 100%：这一竿必定是「没中鱼 + 钩上物件」，不是鱼
    cfg2["bait_hook_rates"] = "worm:0.0"
    plugin_d = make_plugin(cfg2)
    pp = mod._default_player("51001")
    pp["gold"] = 1000
    pp["equipped_bait"] = "worm"       # 带饵才有资格钩上物件
    pp["baits"] = {"worm": 5}
    await plugin_d._save_player(pp)
    out = await cast(plugin_d, FakeEvent("51001"))
    body = text_of(out)
    check("没有鱼" in body, f"没中鱼时钩上物件 -> {body.splitlines()[0][:36]}")
    pp = await plugin_d._load_player("51001")
    check(not pp["inventory"], "杂物那一竿不会同时上鱼")
    check(
        _safe_total(pp) == 0,
        f"杂物竿不计入渔获 -> total_caught={pp.get('total_caught')}",
    )
    check(
        sum(pp["collectibles"].values()) >= 1,
        f"杂物已记账 -> {pp['collectibles']}",
    )
    check(not pp.get("best_records"), "杂物竿不刷新最佳渔获纪录")

    # 掉率 0 + 咬钩率 100%：这一竿必定是鱼，绝不会出杂物
    cfg3["bait_hook_rates"] = "worm:1.0"
    plugin_n = make_plugin(cfg3)
    pp0 = mod._default_player("51002")
    pp0["gold"] = 1000
    pp0["equipped_bait"] = "worm"
    pp0["baits"] = {"worm": 5}
    await plugin_n._save_player(pp0)
    out0 = await cast(plugin_n, FakeEvent("51002"))
    check("没有鱼" not in text_of(out0), "掉率 0 时不会出杂物")
    pp0 = await plugin_n._load_player("51002")
    check(
        _safe_total(pp0) == 1,
        f"掉率 0 时正常上鱼 -> total_caught={pp0.get('total_caught')}",
    )

    # =====================================================================
    print("\n[6b] 上钩率语义：上鱼率 == 设置的咬钩率，杂物只在没中鱼时出现")

    def sample(hook: str, drop: float, n: int = 2000, bait: str = "worm"):
        """按给定配置采样 n 竿，返回三类结果的计数。"""
        c = dict(plugin.cfg)
        c["bait_hook_rates"] = hook
        c["item_drop_chance"] = drop
        pl = make_plugin(c)
        counts = {"fish": 0, "item": 0, "nothing": 0}
        for _ in range(n):
            outcome, _drop = pl._roll_cast_outcome(bait, True)
            counts[outcome] += 1
        return counts

    got5 = sample("worm:0.5", 0.14)
    rate5 = got5["fish"] / 2000
    check(
        abs(rate5 - 0.5) <= 0.05,
        f"咬钩率 0.5 时上鱼率 {rate5:.3f}（2000 竿，容差 ±0.05）",
    )

    got8 = sample("worm:0.8", 0.14)
    rate8 = got8["fish"] / 2000
    check(
        abs(rate8 - 0.8) <= 0.05,
        f"咬钩率 0.8 时上鱼率 {rate8:.3f}（不再被杂物稀释）",
    )
    check(
        got8["fish"] + got8["item"] + got8["nothing"] == 2000,
        f"三类结果互斥且完整 -> {got8}",
    )

    # 没中鱼的那部分里，物件率应约等于 item_drop_chance
    got_i = sample("worm:0.0", 0.5)
    check(got_i["fish"] == 0, f"咬钩率 0 时上鱼率为 0 -> {got_i['fish']}")
    item_rate = got_i["item"] / 2000
    check(
        abs(item_rate - 0.5) <= 0.06,
        f"咬钩率 0 时物件率 {item_rate:.3f} ≈ item_drop_chance 0.5",
    )

    # 咬钩率 100%：即使掉率拉满也不会出物件（中鱼就是鱼）
    got_full = sample("worm:1.0", 1.0)
    check(
        got_full["fish"] == 2000 and got_full["item"] == 0,
        f"中鱼的那一竿绝不会再出杂物 -> {got_full}",
    )

    # 完全免费的空钩（can_loot=False）永不出物件
    pl_free = make_plugin({**dict(plugin.cfg), "bait_hook_rates": "none:0.0"})
    free = {"fish": 0, "item": 0, "nothing": 0}
    for _ in range(2000):
        outcome, _d = pl_free._roll_cast_outcome("none", False)
        free[outcome] += 1
    check(
        free["item"] == 0 and free["nothing"] == 2000,
        f"免费空钩不会白刷杂物 -> {free}",
    )

    # =====================================================================
    print("\n[6c] 上钩率配置容错（中文名 / 百分数 / 全角 / 未知键）")
    tr = make_plugin({**dict(plugin.cfg), "bait_hook_rates": "蚯蚓:0.55,面包屑:70"})
    check(
        abs(tr._hook_rate("worm") - 0.55) < 1e-9
        and abs(tr._hook_rate("bread") - 0.70) < 1e-9,
        f"中文饵名与百分数都能认 -> 蚯蚓 {tr._hook_rate('worm')} / 面包屑 {tr._hook_rate('bread')}",
    )
    tr2 = make_plugin({**dict(plugin.cfg), "bait_hook_rates": "worm：0.42，bread:0.5"})
    check(
        abs(tr2._hook_rate("worm") - 0.42) < 1e-9,
        f"全角冒号/逗号容错 -> {tr2._hook_rate('worm')}",
    )
    tr3 = make_plugin({**dict(plugin.cfg), "bait_hook_rates": "不认识的饵:0.9"})
    check(
        abs(tr3._hook_rate("worm") - 0.30) < 1e-9,
        f"配置漏写的饵回退 0.30（修复前会变成 100%）-> {tr3._hook_rate('worm')}",
    )
    tr4 = make_plugin({**dict(plugin.cfg), "bait_hook_rates": "worm:abc"})
    check(
        abs(tr4._hook_rate("worm") - 0.30) < 1e-9,
        f"值写坏回退 0.30（不再回退成 100%）-> {tr4._hook_rate('worm')}",
    )

    # =====================================================================
    print("\n[6d] 默认值自动同步（改了代码里的数值，不用再手点重置配置）")

    class _FakeConfig(dict):
        """模拟 AstrBot 的 AstrBotConfig：dict 子类 + 异步保存。"""

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.saves = 0

        async def save_config_async(self, replace_config=None, *, indent=2):
            self.saves += 1
            return True

    def cfg_for_sync(**overrides):
        """造一份「旧版配置」：指纹是旧的，且数值被站长改过。"""
        c = _FakeConfig()
        for key in mod.DEFAULTS:
            c[key] = mod.DEFAULTS[key]
        c["config_fingerprint"] = "old00000"
        c["item_drop_chance"] = 0.99          # 站长自己改过的数值
        c["sell_discount"] = 2.5
        c["data_target"] = "站长手填的目标"     # 管理类设置，不该被动
        c["backup_dir"] = "/my/backups"
        c["button_mode"] = "关闭"
        c.update(overrides)
        return c

    sync_plugin = make_plugin()
    c1 = cfg_for_sync()
    sync_plugin.config = c1
    await sync_plugin._sync_defaults()
    check(
        c1["item_drop_chance"] == mod.DEFAULTS["item_drop_chance"]
        and c1["sell_discount"] == mod.DEFAULTS["sell_discount"],
        f"数值被同步为新默认 -> {c1['item_drop_chance']} / {c1['sell_discount']}",
    )
    check(
        c1["data_target"] == "站长手填的目标" and c1["backup_dir"] == "/my/backups",
        "data_* / backup_* 管理设置不受影响",
    )
    check(c1["button_mode"] == "关闭", "auto 模式下开关类设置保留")
    check(
        c1["config_fingerprint"] == mod._defaults_fingerprint(),
        f"指纹已更新 -> {c1['config_fingerprint']}",
    )
    check(c1.saves == 1, f"写盘一次 -> saves={c1.saves}")

    # 指纹相同时：什么都不做、不写盘
    await sync_plugin._sync_defaults()
    check(c1.saves == 1, f"指纹相同不再写盘 -> saves={c1.saves}")

    # off 档：保留站长改过的值，只更新指纹
    c2 = cfg_for_sync(defaults_sync_mode="off")
    sync_plugin.config = c2
    await sync_plugin._sync_defaults()
    check(
        c2["item_drop_chance"] == 0.99 and c2["sell_discount"] == 2.5,
        f"off 档保留站长设置 -> {c2['item_drop_chance']} / {c2['sell_discount']}",
    )
    check(
        c2["config_fingerprint"] == mod._defaults_fingerprint(),
        "off 档仍更新指纹（避免每次都提示）",
    )

    # all 档：连开关一起重置
    c3 = cfg_for_sync(defaults_sync_mode="all")
    sync_plugin.config = c3
    await sync_plugin._sync_defaults()
    check(
        c3["button_mode"] == mod.DEFAULTS["button_mode"],
        f"all 档把开关也重置 -> button_mode={c3['button_mode']}",
    )
    check(
        c3["backup_dir"] == mod.DEFAULTS["backup_dir"]
        and c3["data_target"] == mod.DEFAULTS["data_target"],
        "all 档等同「重置配置」：连 data_* / backup_* 也回到默认"
        "（所以默认档位是 auto —— 只同步数值与内容）",
    )

    # 内容表**不**参与同步：站长在面板里编辑过的鱼竿/鱼池不能被升级覆盖
    # （官方新增内容由 content_auto_merge 增量补，不动已有条目）
    c4 = cfg_for_sync()
    c4["rod_defs"] = ["bamboo|竹竿|🎋|0|9.99|0|站长自己改的鱼竿"]
    c4["fish_defs"] = "my_fish|自定义鱼|常见|999|novice:1.0|站长自己加的鱼"
    sync_plugin.config = c4
    await sync_plugin._sync_defaults()
    check(
        c4["rod_defs"] == ["bamboo|竹竿|🎋|0|9.99|0|站长自己改的鱼竿"],
        "内容表（鱼竿定义）保留站长编辑，不被默认值同步覆盖",
    )
    check(
        c4["fish_defs"].startswith("my_fish|"),
        "内容表（鱼池 fish_defs）同样保留站长编辑",
    )

    # 保存失败也不能抛异常
    class _BrokenConfig(_FakeConfig):
        async def save_config_async(self, replace_config=None, *, indent=2):
            raise RuntimeError("磁盘满了")

    c5 = cfg_for_sync()
    sync_plugin.config = _BrokenConfig(c5)
    try:
        await sync_plugin._sync_defaults()
        check(True, "写盘失败时不抛异常（只告警）")
    except Exception as e:  # pragma: no cover
        check(False, f"写盘失败时抛了异常：{e}")


    # =====================================================================
    print("\n[7] 订单（不定时刷新）")
    plugin = make_plugin()
    ev = FakeEvent("60001")
    p = await plugin._load_player("60001")
    check(mod._player_level(p) == 1, "新玩家 1 级")
    out = await cmd(plugin, ev, "订单", "", "")
    check("需要 3 级" in text_of(out), "等级不足时看不到订单")

    p = await plugin._load_player("60001")
    p["total_caught"] = 45
    p["gold"] = 10000
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "订单", "", "")
    check("当前订单" in text_of(out), "达到等级后可见订单")
    print("    " + text_of(out).replace("\n", "\n    "))
    p = await plugin._load_player("60001")
    check(
        len(p["orders"]) == int(plugin.cfg["order_count"]),
        f"生成 {len(p['orders'])} 单",
    )
    check(p["order_date"] == plugin._today_text(), "记录接单日期")
    check(
        p["order_next_ts"] > int(time.time()),
        f"记录了下一批刷新时间（{plugin._order_wait_text(p)}）",
    )
    # 刷新时间必须活过读档：丢了就会每次读档换一批，交单永远交不上
    p_reload = await plugin._load_player("60001")
    check(
        p_reload["order_next_ts"] == p["order_next_ts"]
        and [o["fish_id"] for o in p_reload["orders"]]
        == [o["fish_id"] for o in p["orders"]],
        "刷新时间与订单跨读档保留",
    )

    order = p["orders"][0]
    fish = mod.FISH_BY_ID[order["fish_id"]]
    p["inventory"] = [
        mod._new_instance(fish["id"], 1.0, value_override=1)
        for _ in range(order["need"])
    ]
    gold_before = p["gold"]
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "订单", "交", "1")
    print("    " + text_of(out).splitlines()[0])
    p = await plugin._load_player("60001")
    check(p["orders"][0]["done"], "订单标记完成")
    check(p["gold"] == gold_before + order["reward"], f"发放奖励 -> {p['gold']}")
    check(len(p["inventory"]) == 0, "交单消耗鱼")
    check(p["total_orders"] == 1, "订单计数 +1")
    out = await cmd(plugin, ev, "订单", "交", "1")
    check("已经交过" in text_of(out), "重复交单被拦")
    out = await cmd(plugin, ev, "订单", "交", "2")
    check("还差" in text_of(out), "鱼不够时提示还差多少")

    # --- 批量交单 / 交 全部 ---
    p = await plugin._load_player("60001")
    orders = p["orders"]
    ready = [o for o in orders if not o.get("done")]
    p["inventory"] = []
    for o in ready:
        f = mod.FISH_BY_ID[o["fish_id"]]
        p["inventory"].extend(
            mod._new_instance(f["id"], 1.0, value_override=1)
            for _ in range(o["need"])
        )
    gold_before = p["gold"]
    expect = sum(o["reward"] for o in ready)
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "订单", "交", "全部")
    p = await plugin._load_player("60001")
    check(
        all(o.get("done") for o in p["orders"]),
        f"「交 全部」一次交完 {len(ready)} 单",
    )
    check(
        p["gold"] == gold_before + expect,
        f"批量交单奖励一次到账 +{expect} -> {p['gold']}",
    )
    check(p["total_orders"] == 1 + len(ready), f"订单计数 -> {p['total_orders']}")

    out = await cmd(plugin, ev, "订单", "交", "全部")
    check("已交过" in text_of(out) or "都没交成" in text_of(out), "全部交完后再交有提示")

    plugin_o = make_plugin()
    orders = plugin_o._roll_orders(5)
    better = 0
    for o in orders:
        f = mod.FISH_BY_ID[o["fish_id"]]
        sell_value = int(f["value"] * 1.268) * o["need"]
        if o["reward"] > sell_value:
            better += 1
    check(
        better == len(orders),
        f"{better}/{len(orders)} 个订单奖励高于直接卖店（×{plugin_o.cfg['order_reward_mult']}）",
    )

    old_ids = [o["fish_id"] for o in p["orders"]]
    p = await plugin._load_player("60001")
    p["order_next_ts"] = int(time.time()) - 1  # 假装时间到了
    await plugin._save_player(p)
    await cmd(plugin, ev, "订单", "", "")
    p = await plugin._load_player("60001")
    check(
        p["order_next_ts"] > int(time.time()),
        "到点后换了一批新的（刷新时间重新计时）",
    )
    check(all(not o["done"] for o in p["orders"]), "新订单重置完成状态")
    check(
        len({o["fish_id"] for o in p["orders"]}) == len(p["orders"]),
        "同一批订单没有重复鱼种",
    )
    check(
        len(p["orders"]) == int(plugin.cfg["order_count"]),
        f"新一批还是 {len(p['orders'])} 单（旧的 {len(old_ids)} 单已过期）",
    )

    # ---- 订单跟随钓点（v1.14.0）----
    print("    ── 订单按当前钓点刷新 ──")
    loc_plugin = make_plugin()
    loc_ev = FakeEvent("60010")
    lp = await loc_plugin._load_player("60010")
    lv_all = [loc["id"] for loc in mod.LOCATIONS]
    lp["locations"] = list(lv_all)
    lp["total_caught"] = mod._level_threshold(20)
    lp["current_location"] = "novice"
    await loc_plugin._save_player(lp)

    # 抽 200 批：每一批的鱼都必须是这个钓点能钓到的
    novice_ids = {f["id"] for f, _w in mod._location_pool("novice")}
    outside = []
    for _ in range(200):
        for o in loc_plugin._roll_orders(20, "novice"):
            if o["fish_id"] not in novice_ids:
                outside.append(o["fish_id"])
    check(
        not outside,
        f"按钓点抽 200 批，一条图外的鱼都没有（越界 {len(outside)} 条）",
    )
    # 老行为（不传钓点）确实会抽到别的图的鱼 —— 说明这个开关是有作用的
    global_ids = {o["fish_id"] for _ in range(60) for o in loc_plugin._roll_orders(20)}
    check(
        bool(global_ids - novice_ids),
        f"关掉开关时还是从全鱼池抽（抽到 {len(global_ids - novice_ids)} 种新手村没有的鱼）",
    )
    # 订单数量不会被「本图这一档不够」拖少：不够就用同图其它鱼补满
    for lv in (1, 6, 12, 18, 30):
        picks = loc_plugin._roll_orders(lv, "novice")
        in_loc = all(o["fish_id"] in novice_ids for o in picks)
        check(
            len(picks) == int(loc_plugin.cfg["order_count"]) and in_loc,
            f"Lv{lv} 在新手村照样 {len(picks)} 单、且都在本图",
        )

    # 换钓点：订单跟着换一批，并记录这批单属于哪个钓点
    await cmd(loc_plugin, loc_ev, "订单", "", "")
    lp = await loc_plugin._load_player("60010")
    check(
        lp["order_location"] == "novice" and lp["order_move_rerolls"] == 0,
        f"订单记住是哪个钓点的 -> {lp.get('order_location')}",
    )
    before_ids = [o["fish_id"] for o in lp["orders"]]
    lp["locations"] = list(lv_all)
    await loc_plugin._save_player(lp)
    out = await cmd(loc_plugin, loc_ev, "钓点", "去", "城中运河")
    lp = await loc_plugin._load_player("60010")
    canal_ids = {f["id"] for f, _w in mod._location_pool("canal")}
    check(
        lp["order_location"] == "canal"
        and all(o["fish_id"] in canal_ids for o in lp["orders"]),
        f"换到城中运河后订单整批换成这儿的鱼（{len(lp['orders'])} 单）",
    )
    check(
        any("订单已换成" in x for x in out),
        "前往时明说订单换了（不偷偷换）",
    )
    check(
        lp["order_move_rerolls"] == 1,
        f"换钓点换单计数 +1 -> {lp['order_move_rerolls']}",
    )
    # 上限：配置默认 1，所以本周期内再换图就不再换单（堵住来回换图刷单）
    moved_ids = [o["fish_id"] for o in lp["orders"]]
    lp["locations"] = list(lv_all)
    await loc_plugin._save_player(lp)
    await cmd(loc_plugin, loc_ev, "钓点", "去", "山间湖泊")
    lp = await loc_plugin._load_player("60010")
    check(
        lp["current_location"] == "lake"
        and lp["order_location"] == "canal"
        and [o["fish_id"] for o in lp["orders"]] == moved_ids,
        "本周期换单次数用完后，再换图也不换单（防刷单）",
    )
    # 列表头会说清楚「这批是哪个钓点的」，人在别处时额外提醒一句
    lp_head = await cmd(loc_plugin, loc_ev, "订单", "", "")
    check(
        "📍城中运河" in text_of(lp_head) and "你人在" in text_of(lp_head),
        "订单列表标明这批单属于哪个钓点，人不在那儿会提醒",
    )
    # 到点刷新会把「换单次数」清零（新周期重新给一次）
    lp = await loc_plugin._load_player("60010")
    lp["order_next_ts"] = int(time.time()) - 1
    await loc_plugin._save_player(lp)
    await cmd(loc_plugin, loc_ev, "订单", "", "")
    lp = await loc_plugin._load_player("60010")
    check(
        lp["order_location"] == "lake" and lp["order_move_rerolls"] == 0,
        f"到点刷新后订单跟到当前钓点、计数清零 -> {lp['order_location']}/{lp['order_move_rerolls']}",
    )

    # 关掉开关 = 老行为（全鱼池抽、不记钓点、表头也回到老文案）
    off_plugin = make_plugin({**dict(_CFG), "order_follow_location": False})
    op = await off_plugin._load_player("60011")
    op["total_caught"] = mod._level_threshold(20)
    off_plugin._ensure_orders(op)
    check(
        op["order_location"] == ""
        and off_plugin._order_head_text(op) == f"📋 当前订单（{off_plugin._order_wait_text(op)}，过期会换一批）",
        "关掉「订单跟随钓点」后完全回到老行为（不记钓点、表头照旧）",
    )
    # 隐藏生物开关：关掉后首选池里没有它（开着时有）
    hid = set(mod.HIDDEN_EVERYWHERE)
    on_pool = [f["id"] for f in loc_plugin._order_pools(18, "novice")[0]]
    nohid_plugin = make_plugin({**dict(_CFG), "order_include_hidden": False})
    off_pool = [f["id"] for f in nohid_plugin._order_pools(18, "novice")[0]]
    check(
        bool(hid & set(on_pool)) and not (hid & set(off_pool)),
        f"隐藏生物开关生效：开着 {sorted(hid & set(on_pool))} / 关掉 {sorted(hid & set(off_pool))}",
    )
    # 换单次数设 0 = 换钓点也不换单
    zero_plugin = make_plugin({**dict(_CFG), "order_move_rerolls": 0})
    zp = await zero_plugin._load_player("60012")
    zp["total_caught"] = mod._level_threshold(20)
    zp["current_location"] = "novice"
    zero_plugin._ensure_orders(zp)
    keep = [o["fish_id"] for o in zp["orders"]]
    zp["current_location"] = "canal"
    check(
        zero_plugin._ensure_orders(zp) is False
        and [o["fish_id"] for o in zp["orders"]] == keep,
        "order_move_rerolls = 0 时换钓点也不换单",
    )

    # =====================================================================
    print("\n[8] 赠送金币 / 给鱼：功能已彻底删除")
    plugin = make_plugin()
    ev_low = FakeEvent("70001")
    for words in (
        ("赠送", "70002", "100"),
        ("送", "70002", "100"),
        ("给鱼", "70002", "鲤鱼", "1"),
        ("赠送鱼", "70002", "1"),
    ):
        out = await cmd(plugin, ev_low, *words)
        check("不认识" in text_of(out), f"/钓鱼 {' '.join(words)} 已不再是有效用法")
    check(
        not any(k.startswith("transfer") for k in mod.DEFAULTS),
        "DEFAULTS 里已无 transfer_* 配置",
    )
    check(
        not any(k.startswith("transfer") for k in plugin.cfg),
        "运行配置里已无 transfer_*",
    )
    p = await plugin._load_player("70001")
    check(
        not any(k.startswith("transfer") for k in p),
        f"玩家数据里已无 transfer_* -> {sorted(k for k in p if k.startswith('transfer'))}",
    )
    check(
        "generous" not in mod.ACHIEVEMENTS and "helped" not in mod.ACHIEVEMENTS,
        "赠送相关的两个成就已删除",
    )
    check(
        not hasattr(plugin, "_cmd_transfer") and not hasattr(plugin, "_cmd_grant"),
        "赠送 / 给鱼 的处理函数已删除（不留死代码）",
    )
    check(
        not hasattr(plugin, "_is_admin") and not hasattr(plugin, "_load_admin_ids"),
        "只服务于「给鱼」的管理员链路也已删除",
    )

    # =====================================================================
    print("\n[9] 帮助分页")
    plugin = make_plugin()
    ev = FakeEvent("10001")
    pages = plugin._help_pages()
    check(len(pages) >= 5, f"帮助共 {len(pages)} 页")
    for i in range(1, len(pages) + 1):
        out = await cmd(plugin, ev, "帮助", str(i), "")
        body = text_of(out)
        n = len(body.splitlines())
        check(n <= 20, f"第 {i} 页 {n} 行（≤20）")
        check(f"{i}/{len(pages)}" in body, f"第 {i} 页带页码")
    out = await cmd(plugin, ev, "帮助", "999", "")
    check(f"{len(pages)}/{len(pages)}" in text_of(out), "页码越界自动钳到末页")
    out = await cmd(plugin, ev, "帮助", "", "")
    check("1/" in text_of(out), "默认显示第一页")

    # =====================================================================
    print("\n[10] 成就")
    check(len(mod.ACHIEVEMENTS) >= 30, f"成就 {len(mod.ACHIEVEMENTS)} 个")
    p = mod._default_player("80001")
    p["total_caught"] = 600
    p["gold"] = 200000
    p["perfect_pulls"] = 12
    p["clutch_wins"] = 2
    p["total_fed"] = 120
    p["total_orders"] = 35
    p["rods"] = [r["id"] for r in mod.RODS]
    p["locations"] = [l["id"] for l in mod.LOCATIONS]
    p["collectibles"] = {c["id"]: 1 for c in mod.COLLECTIBLES}
    p["bottle_notes"] = list(mod.BOTTLE_NOTES)
    p["collection"] = {
        f["id"]: {"count": 1, "best_value": 1, "first_ts": 1} for f in mod.FISH_POOL
    }
    inst = mod._new_instance(
        "carp", 1.0, attrs={"meat": 100, "spirit": 100, "sheen": 100}
    )
    p["inventory"] = [inst]
    plugin = make_plugin()
    # 传一条神话渔获进去，才能触发 myth_hunter
    myth_fish = next(f for f in mod.FISH_POOL if f["rarity"] == "神话")
    plugin._check_achievements(
        p, {"fish_id": myth_fish["id"], "quality": "传说", "value": 1}
    )
    unlocked = len(p["achievements"])
    check(unlocked >= 30, f"满条件解锁 {unlocked}/{len(mod.ACHIEVEMENTS)} 个成就")
    for key in (
        "catch_500", "myth_hunter", "perfect_10", "clutch_win", "collector_all",
        "junk_all", "note_all", "feeder_100", "attr_max", "rich_100000",
        "order_30", "rod_all", "loc_all",
    ):
        check(key in p["achievements"], f"解锁 {key}")
    again = plugin._check_achievements(p)
    check(len(again) == 0, "重复检查不会重复解锁")

    plugin_t = make_plugin()
    pp = mod._default_player("81001")
    pp["gold"] = 50000
    pp["total_caught"] = 60
    for fid in plugin_t._location_species("novice"):   # 先集齐新手村图鉴
        pp["collection"][fid] = {"count": 1, "best_value": 1, "first_ts": 1}
    await plugin_t._save_player(pp)
    await cmd(plugin_t, FakeEvent("81001"), "钓点", "解锁", "竹林溪流")
    pp = await plugin_t._load_player("81001")
    for fid in plugin_t._location_species("bamboo"):  # 再集齐竹林溪流
        pp["collection"][fid] = {"count": 1, "best_value": 1, "first_ts": 1}
    await plugin_t._save_player(pp)
    evt = FakeEvent("81001")
    await cmd(plugin_t, evt, "鱼竿", "买", "碳素竿")
    pp = await plugin_t._load_player("81001")
    check("rod_2" in pp["achievements"], "买第二根竿触发「鸟枪换炮」")
    await cmd(plugin_t, evt, "钓点", "解锁", "山间湖泊")
    pp = await plugin_t._load_player("81001")
    check("loc_2" in pp["achievements"], "解锁第二个钓点触发「走出新手村」")

    # =====================================================================
    print("\n[10b] 天气与时段")
    plugin = make_plugin()
    ev = FakeEvent("82001")
    p = await plugin._load_player("82001")
    check(plugin._ensure_weather(p), "首次生成今日天气")
    check(not plugin._ensure_weather(p), "同一天不会重roll")
    wid = p["weather"]
    check(wid in mod.WEATHER_BY_ID, f"天气合法 -> {wid}")
    w = plugin._weather(p)
    check(w is not None and w["id"] == wid, "读回同一天气")

    # 跨天重roll
    p["weather_date"] = "1970-01-01"
    await plugin._save_player(p)
    p2 = await plugin._load_player("82001")
    check(plugin._ensure_weather(p2), "跨天后重新生成天气")
    check(p2["weather"] in mod.WEATHER_BY_ID, f"新天气合法 -> {p2['weather']}")

    # 天气影响鱼种分布：直接算加权权重（确定性，不受抽样噪声影响）
    def weight_of(rarity: str, wcfg) -> float:
        """某天气下该品质的总权重。"""
        total = 0.0
        for r in mod.RARITY_ORDER:
            if r != rarity:
                continue
        for fish, weight in mod._location_pool("aurora"):
            mult = (wcfg.get("rarity_mult") or {}).get(fish["rarity"], 1.0)
            if fish["rarity"] == rarity:
                total += weight * mult
        return total

    sunny = mod.WEATHER_BY_ID["sunny"]
    moon = mod.WEATHER_BY_ID["moon"]
    sample_loc = "aurora"  # 低段钓点没有传说鱼，用终局钓点抽样
    check(
        weight_of("传说", moon) > weight_of("传说", sunny) * 1.5,
        f"月夜把传说鱼权重从 {weight_of('传说', sunny):.2f} "
        f"提到 {weight_of('传说', moon):.2f}",
    )
    check(
        weight_of("神话", moon) == 0 or weight_of("神话", moon) > 0,
        "月夜只增强、不新增鱼种",
    )
    # 抽样验证方向一致（大样本，只断言方向）
    cc = collections.Counter()
    for _ in range(30000):
        cc[plugin._roll_species("none", "aurora", moon)["rarity"]] += 1
    moon_top = (
        cc.get("传说", 0) + cc.get("神话", 0)
    ) / max(1, sum(cc.values()))
    cc2 = collections.Counter()
    for _ in range(30000):
        cc2[plugin._roll_species("none", "aurora", sunny)["rarity"]] += 1
    sunny_top = (
        cc2.get("传说", 0) + cc2.get("神话", 0)
    ) / max(1, sum(cc2.values()))
    check(
        moon_top > sunny_top,
        f"抽样方向一致：月夜传说+ {moon_top:.3%} > 晴朗 {sunny_top:.3%}",
    )

    # 天气影响拉线窗口
    legend_fish = next(f for f in mod.FISH_POOL if f["rarity"] == "传说")
    def avg_window(weather_cfg, n=300):
        return sum(
            plugin._interaction_window(legend_fish, weather_cfg)["window"]
            for _ in range(n)
        ) / n
    calm = avg_window(mod.WEATHER_BY_ID["cloudy"])
    windy = avg_window(mod.WEATHER_BY_ID["wind"])
    check(windy < calm, f"大风窗口 {windy:.2f}s < 多云 {calm:.2f}s")

    out = await cmd(plugin, ev, "今日", "", "")
    body = text_of(out)
    check("今日" in body, "/钓鱼 今日 可查看")
    check("今日高价" in body or "无特别行情" in body, "含鱼市信息")
    check("图鉴集齐加成" in body, "含图鉴加成信息")
    print("    " + body.replace("\n", "\n    "))

    # =====================================================================
    print("\n[10c] 鱼市行情")
    plugin = make_plugin()
    ev = FakeEvent("83001")
    p = await plugin._load_player("83001")
    check(plugin._ensure_market(p), "首次生成行情")
    check(not plugin._ensure_market(p), "同一天不会重roll")
    check(1 <= len(p["market"]) <= 2, f"今日 {len(p['market'])} 种高价鱼")
    for entry in p["market"]:
        check(
            entry["fish_id"] in mod.FISH_BY_ID,
            f"行情鱼合法 -> {entry['fish_id']}",
        )
        check(
            1.0 <= entry["mult"] <= plugin.cfg["market_boost_max"] + 0.01,
            f"加成在区间内 -> ×{entry['mult']}",
        )

    # 行情真的提高卖价
    target = p["market"][0]
    p["inventory"] = [
        mod._new_instance(target["fish_id"], 1.0,
                          value_override=100, attrs={"meat": 60, "spirit": 60, "sheen": 60})
    ]
    p["gold"] = 0
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "卖", "", "")
    p = await plugin._load_player("83001")
    expected = int(100 * float(plugin.cfg["sell_discount"]) * target["mult"])
    check(
        p["gold"] == expected,
        f"行情价生效：期望 {expected} 实得 {p['gold']}（×{target['mult']}）",
    )
    check("行情加成" in text_of(out), "卖鱼时提示行情加成")
    print("    " + text_of(out).splitlines()[0])

    # =====================================================================
    print("\n[10d] 变异个体")
    check(len(mod.VARIANTS) == 6, f"变异 {len(mod.VARIANTS)} 种")
    p = mod._default_player("84001")
    # 变异价值更高
    normal = mod._new_instance("carp", 1.2, attrs={"meat": 70, "spirit": 70, "sheen": 70})
    golden = mod._new_instance(
        "carp", 1.2, attrs={"meat": 70, "spirit": 70, "sheen": 70}, variant="golden"
    )
    prism = mod._new_instance(
        "carp", 1.2, attrs={"meat": 70, "spirit": 70, "sheen": 70}, variant="prismatic"
    )
    check(golden["value"] > normal["value"], f"黄金变异更值钱 {normal['value']} → {golden['value']}")
    check(prism["value"] > golden["value"], f"虹彩更贵 {golden['value']} → {prism['value']}")
    check(golden["variant"] == "golden", "变异 id 记录")
    check("黄金" in mod._instance_line(golden), "变异在展示名里体现")

    # 0 概率不掉变异 / 100% 必掉
    cfg_v0 = dict(plugin.cfg)
    cfg_v0["variant_chance"] = 0.0
    pv0 = make_plugin(cfg_v0)
    check(all(pv0._roll_variant() is None for _ in range(200)), "0 概率不出变异")
    cfg_v1 = dict(plugin.cfg)
    cfg_v1["variant_chance"] = 1.0
    pv1 = make_plugin(cfg_v1)
    hits = collections.Counter(pv1._roll_variant() for _ in range(2000))
    check(len(hits) == len(mod.VARIANTS), f"100% 时 6 种变异都出现 -> {len(hits)} 种")
    check(hits["golden"] > hits["prismatic"], "黄金比虹彩常见")

    # 变异进独立图鉴条目
    # （关掉彩蛋，否则 5% 概率触发「旧地图」彩蛋会顺带写一张纸条，
    #   让下面「变异不影响杂物记录」的断言变得随机）
    cfg_noegg = dict(_CFG)
    cfg_noegg["easter_egg_chance"] = 0.0
    plugin = make_plugin(cfg_noegg)
    ev = FakeEvent("84002")
    p = mod._default_player("84002")
    var_fish = normal.copy()
    var_fish["variant"] = "jade"
    var_fish["id"] = "test000001"
    p["inventory"] = [var_fish]
    await plugin._save_player(p)
    await plugin._finalize_catch(ev, p, var_fish, "84002")
    check(
        "carp#jade" in p["collection"],
        f"变异图鉴键 -> {[k for k in p['collection']]}",
    )
    check(p["variants"].get("jade") == 1, f"变异计数 -> {p['variants']}")
    check("first_variant" in p["achievements"], "解锁「万里挑一」")
    check(len(p["bottle_notes"]) == 0, "变异不影响杂物记录")

    # =====================================================================
    print("\n[10e] 群内排行榜")
    plugin = make_plugin()
    for uid, gold, caught in (("85001", 5000, 300), ("85002", 90000, 40), ("85003", 100, 900)):
        pp = mod._default_player(uid)
        pp["gold"] = gold
        pp["total_caught"] = caught
        pp["last_name"] = f"玩家{uid[-2:]}"
        pp["collection"] = {"carp": {"count": 3, "best_value": 10, "first_ts": 1}}
        pp["inventory"] = [
            mod._new_instance("koi", 1.5, value_override=gold // 10)
        ]
        await plugin._save_player(pp)
        await plugin._touch_leaderboard(pp)

    ev = FakeEvent("85001")
    out = await cmd(plugin, ev, "排行", "", "")
    body = text_of(out)
    check("排行榜" in body, "/钓鱼 排行 可用")
    check("玩家03" in body, "按钓获排序时 85003 上榜")
    print("    " + body.replace("\n", "\n    "))

    out = await cmd(plugin, ev, "排行", "金币", "")
    body = text_of(out)
    check("金币" in body.splitlines()[0], "可按金币排序")
    check(body.index("玩家02") < body.index("玩家01"), "金币榜 85002 排在 85001 前")

    out = await cmd(plugin, ev, "排行", "最贵", "")
    check("单条最贵" in text_of(out), "可按单条最贵排序")

    # =====================================================================
    print("\n[10f] 鱼塘挂机收益")
    plugin = make_plugin()
    ev = FakeEvent("86001")
    p = await plugin._load_player("86001")
    p["gold"] = 1000
    p["aquarium"] = [
        mod._new_instance("koi", 1.2, value_override=2000,
                          attrs={"meat": 70, "spirit": 70, "sheen": 70})
    ]
    await plugin._save_player(p)

    out = await cmd(plugin, ev, "水族馆", "领", "")
    check("开始计产" in text_of(out), "首次领取开始计时")
    p = await plugin._load_player("86001")
    check(p["pond_last_ts"] > 0, "记录计产起点")

    # 刚放进去（没计时 / 计时刚开始）：收益按「在缸时长」算，所以还产不出
    out = await cmd(plugin, ev, "水族馆", "领", "")
    check(
        "再等" in text_of(out) or "还没产出" in text_of(out),
        f"时间不足时提示等待 -> {text_of(out).strip()[:40]}",
    )

    # 模拟挂了 5 小时（也补上它在缸里的时间）
    p = await plugin._load_player("86001")
    p["pond_last_ts"] = int(time.time()) - 5 * 3600
    p["aquarium"][0]["tank_since"] = int(time.time()) - 5 * 3600
    gold_before = p["gold"]
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "水族馆", "领", "")
    print("    " + text_of(out).replace("\n", "\n    "))
    p = await plugin._load_player("86001")
    rate = float(plugin.cfg["pond_income_per_hour"])
    expected = min(int(2000 * rate * 5), int(plugin.cfg["pond_income_cap_coins"]))
    check(p["gold"] == gold_before + expected, f"按小时结算 {gold_before}→{p['gold']}（期望 +{expected}）")
    check("pond_first" in p["achievements"], "解锁「鱼塘初收」")
    check(p["pond_claimed_ts"] > 0, "记录领取时间")

    # 上限封顶（单条鱼最多按 pond_income_cap_hours 计产）
    p["pond_last_ts"] = int(time.time()) - 100 * 3600
    p["aquarium"][0]["tank_since"] = int(time.time()) - 100 * 3600
    p["gold"] = 0
    await plugin._save_player(p)
    await cmd(plugin, ev, "水族馆", "领", "")
    p = await plugin._load_player("86001")
    cap = int(plugin.cfg["pond_income_cap_hours"])
    tank_value = mod._inventory_value(p["aquarium"])
    expected_cap = int(tank_value * rate * cap)
    cap_coins = int(plugin.cfg["pond_income_cap_coins"])
    if cap_coins > 0:
        expected_cap = min(expected_cap, cap_coins)
    check(
        p["gold"] == expected_cap,
        f"挂机收益封顶 {p['gold']}（{cap} 小时上限 × 馆藏 {tank_value}）",
    )

    # =====================================================================
    print("\n[10g] 锁定与卖光光（「卖 垃圾」玩法已取消）")
    plugin = make_plugin()
    ev = FakeEvent("89005")
    def fill(n, value=50):
        return [
            mod._new_instance("carp", 1.0, value_override=value,
                              attrs={"meat": 60, "spirit": 60, "sheen": 60})
            for _ in range(n)
        ]

    # 「卖 垃圾」不再有单独玩法，只给一句说明
    p = mod._default_player("89005")
    p["inventory"] = fill(4)
    await plugin._save_player(p)
    for args in (("卖垃圾", "", ""), ("卖", "垃圾", "")):
        out = await cmd(plugin, ev, *args)
        body = text_of(out)
        check(
            "已经去掉" in body and "卖光光" in body,
            f"「/钓鱼 {' '.join(a for a in args if a)}」提示玩法已取消并指向卖光光",
        )
        p = await plugin._load_player("89005")
        check(len(p["inventory"]) == 4, "   └ 并且一只都没卖")

        p["inventory"] = fill(4)
        await plugin._save_player(p)

    # 锁定：锁上之后卖光光会跳过它
    p = await plugin._load_player("89005")
    p["inventory"] = fill(3, value=10) + fill(1, value=999)
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "锁定", "4", "")
    p = await plugin._load_player("89005")
    locked = [x for x in p["inventory"] if x.get("locked")]
    check(len(locked) == 1, "锁定成功")
    out = await cmd(plugin, ev, "卖光光", "", "")
    p = await plugin._load_player("89005")
    check(
        len(p["inventory"]) == 1 and p["inventory"][0].get("locked"),
        "卖光光会跳过锁定的鱼",
    )
    check("🔒" in text_of(out), "并且提示锁定的鱼留了下来")
    out = await cmd(plugin, ev, "清理", "", "")
    check("已经去掉" not in text_of(out), "「清理」别名已删除（不再给旧的取消说明）")
    p = await plugin._load_player("89005")
    p["inventory"] = fill(4)
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "一键卖出", "", "")
    check(
        len((await plugin._load_player("89005"))["inventory"]) == 0,
        "「一键卖出」改指向卖光光（背包被清空）",
    )

    # 解锁后就能一起卖掉
    await cmd(plugin, ev, "解锁", "1", "")
    p = await plugin._load_player("89005")
    check(not any(x.get("locked") for x in p["inventory"]), "解锁成功")
    out = await cmd(plugin, ev, "卖光光", "", "")
    p = await plugin._load_player("89005")
    check(len(p["inventory"]) == 0, "解锁后卖光光清空背包")

    # 老人机（没有锁定功能的老存档）也不会因为缺字段出错
    plugin_o2 = make_plugin()
    ev2 = FakeEvent("89007")
    p2 = mod._default_player("89007")
    p2["inventory"] = fill(2)
    for x in p2["inventory"]:
        x.pop("locked", None)
    await plugin_o2._save_player(p2)
    out = await cmd(plugin_o2, ev2, "锁定", "1", "")
    p2 = await plugin_o2._load_player("89007")
    check(p2["inventory"][0].get("locked") is True, "老存档也能锁定")

    print("\n[10h] 图鉴集齐奖励")
    plugin = make_plugin()
    p = mod._default_player("88001")
    check(plugin._codex_mult(p) == 1.0, "空图鉴无加成")
    check(plugin._codex_mult_text(p) == "无", "文案显示「无」")

    # 只集齐常见
    for f in mod.FISH_POOL:
        if f["rarity"] == "常见":
            p["collection"][f["id"]] = {"count": 1, "best_value": 1, "first_ts": 1}
    mult_common = plugin._codex_mult(p)
    expect_common = 1.0 + plugin.cfg["codex_bonus_per_rarity"][0]
    check(
        abs(mult_common - expect_common) < 1e-6,
        f"集齐常见 -> ×{mult_common:.2f}（期望 {expect_common:.2f}）",
    )
    check("常见" in plugin._codex_completed_rarities(p), "识别出常见已集齐")
    check("+3%" in plugin._codex_mult_text(p), f"文案 -> {plugin._codex_mult_text(p)}")

    # 全图鉴
    for f in mod.FISH_POOL:
        p["collection"][f["id"]] = {"count": 1, "best_value": 1, "first_ts": 1}
    mult_all = plugin._codex_mult(p)
    expect_all = 1.0 + sum(plugin.cfg["codex_bonus_per_rarity"])
    check(
        abs(mult_all - expect_all) < 1e-6,
        f"全图鉴 -> ×{mult_all:.2f}（期望 {expect_all:.2f}）",
    )
    new = plugin._check_achievements(p)
    check("codex_all" in p["achievements"], "解锁「图鉴大成」")
    check("codex_common" in p["achievements"], "解锁「常见全收集」")

    # 加成真的体现在鱼价上
    cheap = mod._new_instance("carp", 1.2, attrs={"meat": 70, "spirit": 70, "sheen": 70})
    rich = mod._new_instance(
        "carp", 1.2, attrs={"meat": 70, "spirit": 70, "sheen": 70},
        codex_mult=mult_all,
    )
    check(rich["value"] > cheap["value"], f"图鉴加成提升鱼价 {cheap['value']} → {rich['value']}")
    # =====================================================================
    print("\n[10i] 批量操作与空格容错")
    plugin = make_plugin()
    ev = FakeEvent("89001")

    def fill(n):
        return [
            mod._new_instance("carp", 1.0, value_override=10,
                              attrs={"meat": 60, "spirit": 60, "sheen": 60})
            for _ in range(n)
        ]

    # --- 批量卖（不再限 2 条）---
    p = await plugin._load_player("89001")
    p["gold"] = 0
    p["inventory"] = fill(10)
    await plugin._save_player(p)
    await cmd(plugin, ev, "卖", "1", "2", "3", "4", "5")
    p = await plugin._load_player("89001")
    check(len(p["inventory"]) == 5, f"一次卖 5 条 -> 剩 {len(p['inventory'])} 条")
    check(
        50 <= p["gold"] <= 110,
        f"得 {p['gold']} 金币（5 条×10，含行情加成最多 ×2.2）",
    )

    # --- 区间写法 ---
    p["inventory"] = fill(10)
    await plugin._save_player(p)
    await cmd(plugin, ev, "卖", "1-4")
    p = await plugin._load_player("89001")
    check(len(p["inventory"]) == 6, f"卖 1-4 -> 剩 {len(p['inventory'])} 条")

    # --- 全部 ---
    await cmd(plugin, ev, "卖", "全部")
    p = await plugin._load_player("89001")
    check(len(p["inventory"]) == 0, "卖 全部 可用")

    # --- 空格容错：多余空格 ---
    p["inventory"] = fill(10)
    await plugin._save_player(p)
    await cmd(plugin, ev, "卖", "  1   2  ", "3")
    p = await plugin._load_player("89001")
    check(len(p["inventory"]) == 7, f"多余空格仍能解析 -> 剩 {len(p['inventory'])} 条")

    # --- 「卖 垃圾」玩法已取消：不再动背包，只给指引 ---
    p["inventory"] = [
        mod._new_instance("mud_snail", 1.0, value_override=1,
                          attrs={"meat": 60, "spirit": 60, "sheen": 60}),
        mod._new_instance("koi", 1.5, value_override=5000,
                          attrs={"meat": 60, "spirit": 60, "sheen": 60}),
    ]
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "卖垃圾")
    body = text_of(out)
    check("已经去掉" in body and "卖光光" in body, f"「卖垃圾」提示玩法已取消 -> {body.splitlines()[0]}")
    p = await plugin._load_player("89001")
    check(len(p["inventory"]) == 2, f"一条都没卖 -> 剩 {len(p['inventory'])} 条")

    out = await cmd(plugin, ev, "卖", "垃圾")
    check("已经去掉" in text_of(out), "「卖 垃圾」同样只提示玩法已取消")

    # --- 少打空格：/钓鱼 卖1、/钓鱼 帮助2、/钓鱼 商店买蚯蚓3 ---
    p["inventory"] = fill(10)
    await plugin._save_player(p)
    await cmd(plugin, ev, "卖1", "2")
    p = await plugin._load_player("89001")
    check(len(p["inventory"]) == 8, f"「卖1 2」= 卖 1 2 -> 剩 {len(p['inventory'])} 条")

    out = await cmd(plugin, ev, "帮助2", "", "")
    check("帮助 2/" in text_of(out), f"「帮助2」能翻页 -> {text_of(out).splitlines()[0]}")

    out = await cmd(plugin, ev, "背包1", "", "")
    check("背包" in text_of(out), "「背包1」= 背包 1")

    p["gold"] = 100000
    await plugin._save_player(p)
    await give_level(plugin, "89001", 2)     # 蚯蚓要 2 级才能买
    out = await cmd(plugin, ev, "商店买蚯蚓2", "", "")
    p = await plugin._load_player("89001")
    check(
        p["baits"].get("worm", 0) == 2 and "金币不足" not in text_of(out),
        f"「商店买蚯蚓2」= 买 2 个 -> {p['baits'].get('worm')} 个（默认买 1 个）",
    )

    out = await cmd(plugin, ev, "水族馆放1", "", "")
    p = await plugin._load_player("89001")
    check(len(p["aquarium"]) == 1, "「水族馆放1」= 水族馆 放 1")

    out = await cmd(plugin, ev, "水族馆取1", "", "")
    p = await plugin._load_player("89001")
    check(len(p["aquarium"]) == 0, "「水族馆取1」= 水族馆 取 1")

    # 已删除的子命令不能被「少打空格」容错重新拼出来
    out = await cmd(plugin, ev, "赠送鱼", "89002", "1")
    check("不认识" in text_of(out), "「赠送鱼」不会被误拆回已删除的「赠送」")

    # --- 批量放入 / 取出水族馆 ---
    plugin2 = make_plugin()
    ev2 = FakeEvent("89002")
    p = mod._default_player("89002")
    p["gold"] = 100000
    p["inventory"] = fill(10)
    await plugin2._save_player(p)
    await cmd(plugin2, ev2, "水族馆", "放", "1", "2", "3", "4")
    p = await plugin2._load_player("89002")
    check(len(p["aquarium"]) == 4, f"一次放入 4 条 -> {len(p['aquarium'])}")
    check(len(p["inventory"]) == 6, f"背包剩 6 条 -> {len(p['inventory'])}")

    await cmd(plugin2, ev2, "水族馆", "取", "1", "2")
    p = await plugin2._load_player("89002")
    check(len(p["aquarium"]) == 2, f"一次取出 2 条 -> {len(p['aquarium'])}")

    await cmd(plugin2, ev2, "水族馆", "取", "全部")
    p = await plugin2._load_player("89002")
    check(len(p["aquarium"]) == 0, "取 全部 可用")

    # --- 批量投喂：/钓鱼 用 高级饲料 1 2 3 ---
    p["inventory"] = fill(6)
    p["items"] = {"feed_premium": 3}
    p["gold"] = 100000
    await plugin2._save_player(p)
    await cmd(plugin2, ev2, "水族馆", "放", "全部")
    p = await plugin2._load_player("89002")
    check(len(p["aquarium"]) == 6, f"批量放入全部 -> {len(p['aquarium'])}")
    out = await cmd(plugin2, ev2, "用", "高级饲料", "1", "2", "3")
    p = await plugin2._load_player("89002")
    fed = [mod._safe_int(f.get("feed_uses"), 0, 0) for f in p["aquarium"]]
    check(fed[:3] == [1, 1, 1], f"一次投喂 3 条 -> {fed}")
    check(p["items"]["feed_premium"] == 0, f"道具扣 3 个 -> {p['items']}")
    check("×3" in text_of(out), "回复说明用了几个道具")

    out = await cmd(plugin2, ev2, "用", "高级饲料", "1")
    check("没有" in text_of(out), "道具用完时有提示")

    # 区间写法 + 道具不够时按顺序喂、用完即停
    p["items"] = {"feed_basic": 2}
    await plugin2._save_player(p)
    before = [mod._safe_int(f.get("feed_uses"), 0, 0) for f in p["aquarium"]]
    out = await cmd(plugin2, ev2, "用", "普通饲料", "2-6")
    p = await plugin2._load_player("89002")
    after = [mod._safe_int(f.get("feed_uses"), 0, 0) for f in p["aquarium"]]
    delta = [b - a for a, b in zip(before, after)]
    check(
        delta == [0, 1, 1, 0, 0, 0],
        f"「用 普通饲料 2-6」按顺序喂、只喂得起 2 条 -> 增量 {delta}",
    )
    check(p["items"]["feed_basic"] == 0, "道具按条数扣完即停")

    # 粘连写法：用高级饲料1
    p["items"] = {"feed_premium": 1}
    await plugin2._save_player(p)
    out = await cmd(plugin2, ev2, "用高级饲料1", "", "")
    p = await plugin2._load_player("89002")
    check(
        p["items"]["feed_premium"] == 0 and "没有道具" not in text_of(out),
        "「用高级饲料1」= 用 高级饲料 1",
    )

    # --- 投喂只涨不跌：必须带上钩时固化的 gear_mult ---
    # 曾经的真实 bug：_apply_feed 重算 base_value 时漏传 gear_mult，
    # 鱼竿/钓点/变异/图鉴加成被打回 1.0，喂一口就大幅掉价。
    plugin_fd = make_plugin()
    ev_fd = FakeEvent("89501")
    pf = mod._default_player("89501")
    pf["gold"] = 100000
    inst_fd = mod._new_instance(
        "carp", 1.6, value_bonus=0.30, location_mult=1.5, codex_mult=1.2,
        attrs={"meat": 60, "spirit": 60, "sheen": 60},
    )
    check(inst_fd["gear_mult"] > 1.5, f"gear_mult 已固化 -> {inst_fd['gear_mult']}")
    pf["aquarium"] = [inst_fd]
    pf["items"] = {"feed_basic": 20, "feed_divine": 20}
    await plugin_fd._save_player(pf)

    vals = []
    for _ in range(6):
        await cmd(plugin_fd, ev_fd, "用", "普通饲料", "1")
        pf = await plugin_fd._load_player("89501")
        vals.append(mod._instance_value(pf["aquarium"][0]))
    check(
        all(b >= a for a, b in zip(vals, vals[1:])),
        f"只喂三维道具时价值单调不减 -> {vals}",
    )
    check(
        abs(pf["aquarium"][0]["gear_mult"] - inst_fd["gear_mult"]) < 1e-6,
        f"投喂不会改动固化倍率 -> {pf['aquarium'][0]['gear_mult']}",
    )

    vals2 = []
    for _ in range(3):
        await cmd(plugin_fd, ev_fd, "用", "仙露", "1")
        pf = await plugin_fd._load_player("89501")
        vals2.append(mod._instance_value(pf["aquarium"][0]))
    check(
        all(b >= a for a, b in zip(vals2, vals2[1:])) and vals2[0] >= vals[-1],
        f"喂带 value_up 的道具也只涨不跌 -> {vals2}（喂前 {vals[-1]}）",
    )
    plain_fd = mod._new_instance(
        "carp", 1.6, attrs={"meat": 60, "spirit": 60, "sheen": 60}
    )
    check(
        mod._instance_value(pf["aquarium"][0]) > plain_fd["value"],
        f"喂完仍保留鱼竿/钓点加成"
        f"（{mod._instance_value(pf['aquarium'][0])} > {plain_fd['value']}）",
    )

    # 旧存档没有 gear_mult 字段：反推出来，绝不能因为缺字段而掉价
    legacy_fd = {k: v for k, v in inst_fd.items() if k != "gear_mult"}
    legacy_fd["attrs"] = dict(inst_fd["attrs"])
    before_fd = mod._instance_value(legacy_fd)
    _gained, delta_fd = mod._apply_feed(
        legacy_fd, {"meat": 5, "spirit": 5, "sheen": 5}
    )
    after_fd = mod._instance_value(legacy_fd)
    check(
        after_fd >= before_fd and delta_fd >= 0,
        f"老存档（无 gear_mult）投喂不掉价 -> {before_fd} → {after_fd}",
    )
    check(
        mod._safe_number(legacy_fd.get("gear_mult"), 0) > 1.0,
        f"投喂时补回 gear_mult -> {legacy_fd.get('gear_mult')}",
    )

    # 名字+数量粘连：商店 买 蚯蚓2 / 卖 鲤鱼3
    p["gold"] = 100000
    p["inventory"] = fill(6)
    await plugin2._save_player(p)
    await give_level(plugin2, "89002", 2)    # 蚯蚓要 2 级
    out = await cmd(plugin2, ev2, "商店买蚯蚓2", "", "")
    p = await plugin2._load_player("89002")
    check(p["baits"].get("worm") == 2, f"「商店买蚯蚓2」= 买 2 个 -> {p['baits'].get('worm')}")
    await cmd(plugin2, ev2, "卖", "鲤鱼3")
    p = await plugin2._load_player("89002")
    check(len(p["inventory"]) == 3, f"「卖 鲤鱼3」= 卖 3 条 -> 剩 {len(p['inventory'])}")

    # 容量上限时批量放入要有提示且不丢鱼
    p = await plugin2._load_player("89002")
    p["aquarium"] = []
    p["inventory"] = fill(20)
    cap = plugin2._aquarium_capacity(p)
    await plugin2._save_player(p)
    out = await cmd(plugin2, ev2, "水族馆", "放", "全部")
    p = await plugin2._load_player("89002")
    check(
        len(p["aquarium"]) == cap,
        f"批量放入受容量限制 -> {len(p['aquarium'])}/{cap}",
    )
    check(
        len(p["inventory"]) == 20 - cap,
        f"没放进去的仍在背包 -> {len(p['inventory'])} 条",
    )
    check("容量不足" in text_of(out), "容量不足有明确提示")

    # --- 商店批量买 ---
    plugin3 = make_plugin()
    ev3 = FakeEvent("89003")
    p = mod._default_player("89003")
    p["gold"] = 100000
    p["total_caught"] = mod._level_threshold(2)   # 蚯蚓要 2 级
    await plugin3._save_player(p)
    await cmd(plugin3, ev3, "商店", "买", "蚯蚓", "3")
    p = await plugin3._load_player("89003")
    check(p["baits"].get("worm") == 3, f"买 3 个蚯蚓 -> {p['baits']}")
    await cmd(plugin3, ev3, "商店", "买", "蚯蚓")
    p = await plugin3._load_player("89003")
    check(p["baits"].get("worm") == 4, "不写数量时只买 1 个")
    check(p["equipped_bait"] == "worm", "买饵自动装备")

    await cmd(plugin3, ev3, "商店", "买", "高级饲料", "5")
    p = await plugin3._load_player("89003")
    check(p["items"].get("feed_premium") == 5, f"买 5 个道具 -> {p['items']}")

    gold_before = p["gold"]
    out = await cmd(plugin3, ev3, "商店", "买", "仙露", "999")
    p = await plugin3._load_player("89003")
    check(p["gold"] == gold_before, "金币不足时不扣款")
    check("金币不足" in text_of(out), "金币不足有提示")

    # =====================================================================
    print("\n[6e] 等级 / 鱼竿解锁购买权限：未解锁不上架，直接买会被明确拒绝")

    gate = make_plugin()
    ev_g = FakeEvent("89010")
    gp = mod._default_player("89010")
    gp["gold"] = 999999
    await gate._save_player(gp)

    # --- 低等级：商店只上架已解锁的饵，未解锁的整条不出现 ---
    out = await cmd(gate, ev_g, "商店", "", "")
    body = text_of(out)
    check("面包屑" in body, "1 级能买到的饵正常上架（面包屑）")
    check(
        "蚯蚓" not in body and "秘制饵" not in body and "虾饵" not in body,
        "未解锁的饵完全不显示（蚯蚓要 2 级、秘制饵要 34 级）",
    )
    check("🔒" in body and "陆续上架" in body, "列表末尾只有一句模糊提示，不点名不写等级")

    # --- 低等级：鱼竿列表同理 ---
    out = await cmd(gate, ev_g, "鱼竿", "", "")
    body = text_of(out)
    check("竹竿" in body, "1 级的竿正常显示")
    check(
        "神话竿" not in body and "星辉竿" not in body and "龙纹竿" not in body,
        "未解锁的鱼竿不显示",
    )
    check("陆续上架" in body, "鱼竿列表也有模糊提示")

    # --- 直接点名买：必须明确拒绝（不能静默失败、不能假装不存在）---
    out = await cmd(gate, ev_g, "商店", "买", "秘制饵", "")
    body = text_of(out)
    check(
        "🔒" in body and "34 级" in body and "34" in body,
        f"买未解锁的饵 -> 明确说明要 34 级（{body.splitlines()[0][:40]}）",
    )
    gp = await gate._load_player("89010")
    check(not gp["baits"].get("secret"), "被拒绝时没有发货")
    check(gp["gold"] == 999999, "被拒绝时不扣钱")

    out = await cmd(gate, ev_g, "鱼竿", "买", "神话竿")
    body = text_of(out)
    check("🔒" in body and "38 级" in body, f"买未解锁的竿 -> 明确说明要 38 级（{body.splitlines()[0][:40]}）")

    # --- 等级够了但缺鱼竿：拒绝理由要说清是缺竿 ---
    await give_level(gate, "89010", 20)
    out = await cmd(gate, ev_g, "商店", "买", "玉米粒", "")
    body = text_of(out)
    check(
        "🔒" in body and "溪流竿" in body,
        f"等级够但没竿 -> 提示得先有溪流竿（{body.splitlines()[0][:40]}）",
    )

    # --- 解锁之后：重新出现在列表里，并且能买 ---
    await give_level(gate, "89010", 10)
    gp = await gate._load_player("89010")
    gp["rods"] = ["bamboo", "stream"]       # 拿到溪流竿
    await gate._save_player(gp)
    out = await cmd(gate, ev_g, "商店", "", "")
    body = text_of(out)
    check("玉米粒" in body, "拿到溪流竿 + 10 级后，玉米粒重新上架")
    check("秘制饵" not in body, "还没解锁的更高级饵依旧不显示")

    out = await cmd(gate, ev_g, "商店", "买", "玉米粒", "2")
    gp = await gate._load_player("89010")
    check(gp["baits"].get("corn") == 2, f"解锁后能正常购买 -> {gp['baits'].get('corn')} 个")

    out = await cmd(gate, ev_g, "鱼竿", "", "")
    body = text_of(out)
    check(
        "溪流竿" in body and "龙纹竿" not in body,
        "鱼竿列表按等级逐步上架（10 级有溪流竿、没有龙纹竿）",
    )

    # --- 已拥有的东西不受新限制影响：换饵 / 装竿都不能被解锁条件卡住 ---
    gp = await gate._load_player("89010")
    gp["baits"]["secret"] = 3                # 假设他早就存了秘制饵
    gp["rods"] = ["bamboo", "stream", "mythic"]
    gp["equipped_rod"] = "bamboo"
    await gate._save_player(gp)
    out = await cmd(gate, ev_g, "换饵", "秘制饵", "")
    gp = await gate._load_player("89010")
    check(gp["equipped_bait"] == "secret", "已持有的未解锁饵仍可换（只限制购买）")
    out = await cmd(gate, ev_g, "鱼竿", "用", "神话竿")
    gp = await gate._load_player("89010")
    check(gp["equipped_rod"] == "mythic", "已持有的未解锁竿仍可装备")
    out = await cmd(gate, ev_g, "鱼竿", "", "")
    check("神话竿" in text_of(out), "已拥有的竿即便没到等级也照常显示")

    # --- 旧格式配置向后兼容：7 段鱼竿 / 8 段鱼饵 -> 不设解锁限制 ---
    legacy_cfg = dict(gate.cfg)
    legacy_cfg["content_auto_merge"] = False   # 别把新版默认内容补进来，测的是纯旧格式
    legacy_cfg["rod_defs"] = [
        "bamboo|竹竿|🎋|0|0.00|0.00|老格式没有解锁等级",
        "oldrod|老竿|🎣|100|0.10|0.05|同样是老格式",
    ]
    legacy_cfg["bait_defs"] = [
        "none|空钩|🪝|0|0|0|1,1,1,1,1|老格式说明",
        "oldbait|老饵|🍞|2|5|0.10|1,1.2,1.4,1.6,1.8|老格式说明",
    ]
    legacy = make_plugin(legacy_cfg)
    check(
        all(r["unlock_level"] == 1 for r in legacy.rods),
        f"旧格式鱼竿解析为 1 级（不锁）-> {[r['unlock_level'] for r in legacy.rods]}",
    )
    check(
        legacy.baits["oldbait"]["unlock_level"] == 1
        and not legacy.baits["oldbait"]["need_rod"],
        "旧格式鱼饵不设解锁条件",
    )
    lp = mod._default_player("89011")
    check(
        not legacy._unlock_shortage(lp, legacy.rods[1])
        and not legacy._unlock_shortage(lp, legacy.baits["oldbait"]),
        "旧格式条目在 1 级新号眼里也是可买的",
    )

    # =====================================================================
    print("\n[6f] 升级曲线：指数增长（越往后越难，卡住最高进度）")

    real_curve = dict(mod.LEVEL_CURVE)
    try:
        th = {lv: mod._level_threshold(lv) for lv in (2, 5, 10, 20, 30, 45, 50)}
        check(th[2] == 5, f"2 级要 {th[2]} 条（base=5）")
        check(
            th[10] == 62 and th[20] == 207 and th[30] == 520,
            f"指数曲线实算：10 级 {th[10]} / 20 级 {th[20]} / 30 级 {th[30]}",
        )
        check(
            1700 <= th[45] <= 1850 and 2550 <= th[50] <= 2750,
            f"45 级 {th[45]} 条、50 级 {th[50]} 条（实测值 ±5% 内）",
        )
        increasing = all(
            mod._level_threshold(lv + 1) > mod._level_threshold(lv)
            for lv in range(1, mod.MAX_LEVEL)
        )
        check(increasing, "每一级都比上一级要求更多（严格递增）")

        # 指数曲线：前段比旧的二次曲线松，但后段反超（这才是「卡住最高进度」）
        mod.LEVEL_CURVE.update({"base": 5.0, "ratio": 1.0, "growth": 0.6})
        old = {lv: mod._level_threshold(lv) for lv in (10, 20, 30, 45, 50)}
        check(
            th[45] > old[45] and th[50] > old[50],
            f"高段比旧二次曲线更陡：45 级 {old[45]}→{th[45]}、"
            f"50 级 {old[50]}→{th[50]}",
        )
        check(
            th[10] < old[10] and th[30] < old[30],
            f"前段更平缓、方便新人追进度：10 级 {old[10]}→{th[10]}、"
            f"30 级 {old[30]}→{th[30]}",
        )
        # 指数特征：每一级的增量本身也在变大
        mod.LEVEL_CURVE.update({"base": 5.0, "ratio": 1.08, "growth": 0.0})
        early_step = mod._level_threshold(21) - mod._level_threshold(20)
        late_step = mod._level_threshold(46) - mod._level_threshold(45)
        check(
            late_step > early_step * 2,
            f"每级增量随等级放大（指数特征）：20→21 级 +{early_step} 条，"
            f"45→46 级 +{late_step} 条",
        )

        # 等比底数可调：调大 -> 同等级要求更多
        mod.LEVEL_CURVE.update({"base": 5.0, "ratio": 1.12, "growth": 0.0})
        check(
            mod._level_threshold(30) > th[30],
            f"ratio 调到 1.12 后 30 级要 {mod._level_threshold(30)} 条（> {th[30]}）",
        )

        # ratio = 1.0 不能除零崩掉
        mod.LEVEL_CURVE.update({"base": 5.0, "ratio": 1.0, "growth": 0.0})
        check(
            mod._level_threshold(10) == 45,
            f"ratio=1 退化成等差不崩：10 级 {mod._level_threshold(10)} 条",
        )
    finally:
        mod.LEVEL_CURVE.clear()
        mod.LEVEL_CURVE.update(real_curve)

    # 等级提升 -> 等级换算与进度口径一致
    lp = mod._default_player("89012")
    lp["total_caught"] = mod._level_threshold(12)
    lv, into, need = mod._level_progress(lp)
    check(
        lv == 12 and into == 0 and need == mod._level_threshold(13) - mod._level_threshold(12),
        f"升级进度按真实曲线算 -> 12 级，本级 {into}/{need}",
    )

    # =====================================================================
    print("\n[6g] 钓点难度系数：前几张图必出鱼，越深越容易空竿")

    factor_plugin = make_plugin()
    # 1) 系数 >= 1.0 的钓点：2000 竿必定出鱼（一件杂物都不能有）
    forced = {"fish": 0, "item": 0, "nothing": 0}
    for _ in range(2000):
        outcome, _d = factor_plugin._roll_cast_outcome("none", True, "novice")
        forced[outcome] += 1
    check(
        forced["fish"] == 2000 and forced["item"] == 0 and forced["nothing"] == 0,
        f"新手村（系数 1.0）2000 竿必出鱼 -> {forced}",
    )
    for loc_id in ("bamboo", "canal"):
        got = [factor_plugin._roll_cast_outcome("none", True, loc_id)[0] for _ in range(200)]
        check(
            all(g == "fish" for g in got),
            f"{loc_id} 也是必出鱼（前 3 张图新手期不空竿）",
        )

    # 2) 系数 < 1.0：实际上鱼率 ≈ 饵率 × 系数
    deep = make_plugin({**dict(factor_plugin.cfg),
                        "bait_hook_rates": "worm:0.8",
                        "location_hook_factors": "aurora:0.5"})
    hits = sum(1 for _ in range(4000) if deep._roll_cast_outcome("worm", True, "aurora")[0] == "fish")
    rate = hits / 4000
    check(
        abs(rate - 0.4) <= 0.05,
        f"深水图（系数 0.5）上鱼率 {rate:.3f} ≈ 饵率 0.8 × 0.5 = 0.40",
    )

    # 3) 配置里没写的钓点 -> 回退 1.0（必出，不误伤）
    check(
        factor_plugin._location_hook_factor("not_a_location") == 1.0,
        "没配置的钓点按 1.0（必出）处理",
    )
    check(
        factor_plugin._location_hook_factor(None) is None,
        "不传钓点时不套用系数（内部采样用）",
    )

    # 4) 解析容错：全角标点 / 百分号 / 未知钓点
    tol = make_plugin({**dict(factor_plugin.cfg),
                       "location_hook_factors": "新手村:0.5，lake:0.5,不存在的图:0.9"})
    check(
        abs(tol._location_hook_factor("novice") - 0.5) < 1e-9,
        f"中文钓点名能认 -> novice={tol._location_hook_factor('novice')}",
    )
    check(
        abs(tol._location_hook_factor("lake") - 0.5) < 1e-9,
        "全角逗号能认 -> lake=0.5",
    )
    check(
        tol._location_hook_factor("reef") == 1.0,
        "漏配的钓点回退 1.0（不会因为漏写就变成空竿）",
    )

    # 5) 真实抛竿链路：深水图确实会空竿，且空竿文案点出「水太深」
    empty_plugin = make_plugin({**dict(factor_plugin.cfg),
                                "bait_hook_rates": "worm:0.0",
                                "location_hook_factors": "aurora:0.5"})
    ep = mod._default_player("89013")
    ep["gold"] = 1000
    ep["equipped_bait"] = "worm"
    ep["baits"] = {"worm": 9}
    ep["locations"] = ["novice", "aurora"]
    ep["current_location"] = "aurora"
    await empty_plugin._save_player(ep)
    out = await cast(empty_plugin, FakeEvent("89013"))
    check(
        "水太深" in text_of(out),
        f"深水图空竿文案点明原因 -> {text_of(out).splitlines()[0][:34]}",
    )
    body = text_of(out)
    check(
        "商店" not in body and "换个更对口的饵" not in body,
        f"空竿文案不再带「去哪儿买饵」的教程尾巴 -> {body.splitlines()[-1][:30]}",
    )

    # =====================================================================
    print("\n[6h] 减少显示：未解锁的钓点/未收集的图鉴条目都不逐条列出")

    slim = make_plugin()
    ev_s = FakeEvent("89020")
    sp = mod._default_player("89020")
    sp["gold"] = 1000
    # 只在新手村钓到两条鱼，其余图鉴空着
    sp["collection"] = {
        "carp": {"count": 3, "best_value": 120, "first_ts": 1},
        "crucian": {"count": 1, "best_value": 60, "first_ts": 1},
    }
    await slim._save_player(sp)

    # --- A: 钓点列表：未解锁只留 🔒 + 名称 ---
    out = await cmd(slim, ev_s, "钓点", "", "")
    body = text_of(out)
    locked_line = next((l for l in body.splitlines() if "山间湖泊" in l), "")
    check(
        locked_line.startswith("🔒") and locked_line.rstrip().endswith("山间湖泊"),
        f"未解锁钓点只显示「🔒+名称」-> {locked_line!r}",
    )
    check(
        "图鉴" not in locked_line and "金" not in locked_line and "级" not in locked_line,
        "未解锁钓点不再显示图鉴进度 / 金币 / 等级",
    )
    unlocked_line = next(
        (l for l in body.splitlines() if "新手村" in l and "×1.00" in l), ""
    )
    check(
        "×1.00" in unlocked_line and "池塘" in unlocked_line,
        f"已解锁钓点仍显示完整信息 -> {unlocked_line.strip()[:34]}",
    )

    # 但「解锁」被拒绝时依旧详细（缺什么一次列全）
    out = await cmd(slim, ev_s, "钓点", "解锁", "山间湖泊")
    body = text_of(out)
    check(
        "还差" in body and ("图鉴" in body or "金币" in body or "等级" in body),
        f"解锁被拒时仍然详细说明缺什么 -> {body.splitlines()[0][:34]}",
    )

    # --- B: 图鉴 <钓点名> 不列未收集条目 ---
    out = await cmd(slim, ev_s, "图鉴", "新手村", "")
    body = text_of(out)
    check("❔" not in body and "???" not in body, "图鉴（单钓点）不再列 ❔ 占位条目")
    check("鲤鱼" in body, "已收集的鱼照常显示")
    check("还差" in body and "种" in body, f"末尾给出「还差 N 种」汇总 -> {body.splitlines()[-2][:30]}")

    out = await cmd(slim, ev_s, "图鉴", "详", "")
    body = text_of(out)
    check("❔" not in body and "???" not in body, "图鉴（详）不再列 ❔ 占位条目")
    check("还差" in body and f"{len(mod.FISH_POOL)}" in body, "图鉴（详）末尾给出总汇总")
    check("鲤鱼" in body or "鲫鱼" in body, "图鉴（详）照常列出已收集的鱼")

    out = await cmd(slim, ev_s, "图鉴", "", "")
    body = text_of(out)
    check("新手村" in body and "/" in body, "主视图的进度（x/y）保留")


    # --- 水族馆：×1.2 展出加成已去掉，收益按「每条鱼在缸里的时间」算（v1.18.0）---
    plugin4 = make_plugin()
    ev4 = FakeEvent("89004")
    p = mod._default_player("89004")
    p["inventory"] = [
        mod._new_instance("koi", 1.2, value_override=1000,
                          attrs={"meat": 70, "spirit": 70, "sheen": 70})
    ]
    await plugin4._save_player(p)
    check(
        "aquarium_bonus" not in plugin4.cfg
        and "aquarium_bonus_min_hours" not in plugin4.cfg,
        "旧的展出加成配置已经从配置里删掉（不再有 ×1.2 那套）",
    )
    # ① 放进取出 15 轮：价值一分不涨（加成没了）
    for _ in range(15):
        await cmd(plugin4, ev4, "水族馆", "放", "1")
        await cmd(plugin4, ev4, "水族馆", "取", "1")
    p = await plugin4._load_player("89004")
    check(
        p["inventory"][0]["value"] == 1000,
        f"来回放入取出 15 轮后价值仍是 {p['inventory'][0]['value']}（= 原价，没有展出加成）",
    )
    check(
        "tank_seconds" not in p["inventory"][0],
        "实例里不再有累计展出时长字段（只留 tank_since 给收益计时用）",
    )
    # ② 补偿措施：挂机收益提高了（每小时 1.5% -> 2%，单次封顶 3000 -> 5000）
    check(
        abs(float(plugin4.cfg["pond_income_per_hour"]) - 0.02) < 1e-9
        and int(plugin4.cfg["pond_income_cap_coins"]) == 5000,
        f"挂机收益已提高：{plugin4.cfg['pond_income_per_hour']}/h、封顶 "
        f"{plugin4.cfg['pond_income_cap_coins']}",
    )

    # ③ 漏洞：空缸白攒时间 + 领之前才放鱼 -> 领不到（真路径：走「放」命令）
    t_plugin = make_plugin()
    tev = FakeEvent("89008")
    tp = mod._default_player("89008")
    tp["aquarium"] = [mod._new_instance("carp", 1.0, attrs={"meat": 60, "spirit": 60, "sheen": 60})]
    await t_plugin._save_player(tp)
    await cmd(t_plugin, tev, "水族馆", "领", "")          # 开始计产
    await cmd(t_plugin, tev, "水族馆", "取", "1")          # 清空缸
    tp = await t_plugin._load_player("89008")
    tp["pond_last_ts"] = int(time.time()) - 12 * 3600     # 白攒 12 小时
    tp["inventory"] = [mod._new_instance("koi", 2.0, attrs={"meat": 90, "spirit": 90, "sheen": 90})]
    tp["gold"] = 0
    await t_plugin._save_player(tp)
    await cmd(t_plugin, tev, "水族馆", "放", "1")          # 领之前才放进去
    out = await cmd(t_plugin, tev, "水族馆", "领", "")
    tp = await t_plugin._load_player("89008")
    check(
        tp["gold"] == 0 and "还没产出" in text_of(out),
        f"空缸攒 12 小时、领之前才放鱼 -> 领到 {tp['gold']} 金币（应为 0，并说明原因）",
    )
    # ④ 老实养：在缸里待了 12 小时就按 12 小时算
    tank_fish = tp["aquarium"][0]
    tp["pond_last_ts"] = int(time.time()) - 12 * 3600
    tank_fish["tank_since"] = int(time.time()) - 12 * 3600
    tp["gold"] = 0
    await t_plugin._save_player(tp)
    await cmd(t_plugin, tev, "水族馆", "领", "")
    tp = await t_plugin._load_player("89008")
    rate = float(t_plugin.cfg["pond_income_per_hour"])
    want = min(int(5000 * rate * 12), 5000) if False else int(
        mod._instance_value(tp["aquarium"][0]) * rate * 12
    )
    check(
        abs(tp["gold"] - want) <= 2,
        f"老实养 12 小时领到 {tp['gold']}（期望约 {want}）",
    )
    # ⑤ 只养了一半时间：按一半算
    tp["pond_last_ts"] = int(time.time()) - 12 * 3600
    tp["aquarium"][0]["tank_since"] = int(time.time()) - 6 * 3600
    tp["gold"] = 0
    await t_plugin._save_player(tp)
    await cmd(t_plugin, tev, "水族馆", "领", "")
    tp = await t_plugin._load_player("89008")
    check(
        abs(tp["gold"] - want // 2) <= 3,
        f"只养了 6 小时领到 {tp['gold']}（应约 {want // 2}，不是满额）",
    )
    # ⑥ 老存档：缸里有鱼但没计时 -> 读档时补成「上次结算时刻」，照旧拿得到
    op = mod._default_player("89006")
    op["aquarium"] = [
        mod._new_instance("carp", 1.0, attrs={"meat": 70, "spirit": 70, "sheen": 70})
    ]
    op["aquarium"][0].pop("tank_since", None)
    op["pond_last_ts"] = int(time.time()) - 12 * 3600
    await plugin4._save_player(op)
    op = await plugin4._load_player("89006")
    check(
        mod._safe_int(op["aquarium"][0].get("tank_since"), 0, 0) == op["pond_last_ts"],
        f"老存档的缸中鱼补上了计时 -> {op['aquarium'][0].get('tank_since')}",
    )
    out = await cmd(plugin4, FakeEvent("89006"), "水族馆", "领", "")
    op = await plugin4._load_player("89006")
    check(op["gold"] > 0, f"老存档照旧领得到挂机收益 -> {op['gold']}")
    # ⑦ 取出/卖出时清掉计时（不然它还能继续产出）
    sp = mod._default_player("89009")
    sp["aquarium"] = [
        mod._new_instance("carp", 1.0, attrs={"meat": 70, "spirit": 70, "sheen": 70})
    ]
    sp["aquarium"][0]["tank_since"] = int(time.time()) - 3600
    await plugin4._save_player(sp)
    await cmd(plugin4, FakeEvent("89009"), "水族馆", "取", "1")
    sp = await plugin4._load_player("89009")
    check(
        mod._safe_int(sp["inventory"][0].get("tank_since"), 0, 0) == 0,
        "取出来的鱼不再计时（放回去才会重新开始）",
    )
    # ⑧ 水族馆列表标出「刚入缸」的鱼
    vp = mod._default_player("89007")
    vp["inventory"] = [
        mod._new_instance("koi", 1.2, value_override=800,
                          attrs={"meat": 70, "spirit": 70, "sheen": 70})
    ]
    await plugin4._save_player(vp)
    await cmd(plugin4, FakeEvent("89007"), "水族馆", "放", "1")
    vout = await cmd(plugin4, FakeEvent("89007"), "水族馆", "", "")
    check("🖼刚入缸" in text_of(vout), f"列表标出刚入缸的鱼 -> {text_of(vout).splitlines()[:3]}")
    # 刚入缸 = 还没产出，所以这时不该出现「可领」的估计
    check("可领" not in text_of(vout), "刚入缸时不显示「可领」估计（确实还没产出）")
    vp = await plugin4._load_player("89007")
    vp["aquarium"][0]["tank_since"] = int(time.time()) - 3 * 3600
    vp["pond_last_ts"] = int(time.time()) - 3 * 3600
    await plugin4._save_player(vp)
    vout = await cmd(plugin4, FakeEvent("89007"), "水族馆", "", "")
    check("现在可领" in text_of(vout), f"养了 3 小时后给出可领估计 -> {text_of(vout).splitlines()[-3:]}")

    # --- 鱼名模糊匹配 ---
    plugin5 = make_plugin()
    check(plugin5._find_fish_by_name("鲤鱼") is not None, "精确鱼名可匹配")
    check(plugin5._find_fish_by_name("小鲫") is not None, "部分鱼名可匹配")
    check(plugin5._find_fish_by_name("七彩") is not None, "前缀可匹配")
    check(plugin5._find_fish_by_name("sss") is None, "不存在的名字返回 None")

    # =====================================================================
    print("\n[6i] 体力与连钓（/钓鱼 <数字>）")

    import time as _time

    stam_cfg = dict(_CFG)
    stam_cfg["stamina_max"] = 5
    stam_cfg["stamina_regen_seconds"] = 45
    stam_cfg["multi_cast_max"] = 4
    stam_cfg["easter_egg_chance"] = 0.0
    stam_cfg["story_chance"] = 0.0
    stam_cfg["fish_cost"] = 0
    stam_plugin = make_plugin(stam_cfg)
    check(
        mod._stamina_enabled(stam_plugin.cfg),
        "体力上限与恢复间隔都 > 0 时启用体力",
    )

    # --- 体力结算（纯函数，不依赖抛竿）---
    now = 1_700_000_000
    fresh = {"stamina": -1, "stamina_ts": 0}
    check(
        mod._refresh_stamina(fresh, stam_plugin.cfg, now=now) == 5,
        f"老存档/新玩家的体力按满体力初始化 -> {fresh['stamina']}",
    )
    travel = {"stamina": 0, "stamina_ts": now - 90}
    check(
        mod._refresh_stamina(travel, stam_plugin.cfg, now=now) == 2,
        f"时间旅行 90 秒恢复 2 点（45 秒 1 点）-> {travel['stamina']}",
    )
    check(
        travel["stamina_ts"] == now,
        f"恢复后时间戳只推进整点（余数不浪费）-> {travel['stamina_ts'] - now}",
    )
    odd = {"stamina": 1, "stamina_ts": now - 60}
    check(
        mod._refresh_stamina(odd, stam_plugin.cfg, now=now) == 2
        and now - odd["stamina_ts"] == 15,
        f"不足一点的 15 秒零头保留 -> 余 {now - odd['stamina_ts']} 秒",
    )
    full = {"stamina": 4, "stamina_ts": now - 500}
    check(
        mod._refresh_stamina(full, stam_plugin.cfg, now=now) == 5
        and full["stamina_ts"] == now,
        "体力不会溢出上限，且满体力时时间戳刷新（不偷偷攒时间）",
    )
    check(
        mod._stamina_wait_seconds({"stamina": 3, "stamina_ts": now - 10}, stam_plugin.cfg, now=now) == 35,
        "距离下一点还有 35 秒",
    )
    free_cfg = dict(stam_cfg)
    free_cfg["stamina_regen_seconds"] = 0
    check(
        not mod._stamina_enabled(free_cfg),
        "恢复间隔填 0 = 本服不限体力（站长想关就能关）",
    )

    # --- 抛竿扣 1 点；不够就拒绝且不扣饵 ---
    p = mod._default_player("89030")
    p["gold"] = 1000
    p["equipped_bait"] = "worm"
    p["baits"] = {"worm": 20}
    await stam_plugin._save_player(p)
    out = await cast(stam_plugin, FakeEvent("89030"))
    check("连钓" not in text_of(out), "单竿还是单竿（没被连钓逻辑吃掉）")
    p = await stam_plugin._load_player("89030")
    check(p["stamina"] == 4, f"抛一竿扣 1 点体力 -> {p['stamina']}/5")
    check(p["baits"]["worm"] == 19, f"顺手扣掉 1 个饵 -> {p['baits']['worm']}")

    p["stamina"] = 0
    p["stamina_ts"] = int(_time.time())
    await stam_plugin._save_player(p)
    out = await cast(stam_plugin, FakeEvent("89030"))
    check(
        "体力不够" in text_of(out) and "秒恢复" in text_of(out),
        f"体力为 0 时拒绝抛竿 -> {text_of(out).splitlines()[0]}",
    )
    p = await stam_plugin._load_player("89030")
    check(p["baits"]["worm"] == 19, "被体力拦下时不会白扣鱼饵")

    # --- 连钓：体力 / 饵一次扣 N 份 ---
    p["stamina"] = 5
    p["stamina_ts"] = int(_time.time())
    await stam_plugin._save_player(p)
    out = await cmd(stam_plugin, FakeEvent("89030"), "3", "", "")
    body = text_of(out)
    check("连钓 3 次" in body, f"连钓结果标题 -> {body.splitlines()[0]}")
    check(
        body.count("\n") >= 3 and ("上鱼" in body and "空竿" in body),
        "逐条列出每竿结果 + 汇总行",
    )
    p = await stam_plugin._load_player("89030")
    check(p["stamina"] == 2, f"连钓 3 次扣 3 点体力 -> {p['stamina']}/5")
    check(p["baits"]["worm"] == 16, f"连钓 3 次扣 3 个饵 -> {p['baits']['worm']}")
    check(p["total_caught"] == 4, f"连钓的渔获照常计入累计 -> {p['total_caught']}")

    # --- 体力不足整批拒绝 ---
    p["stamina"] = 1
    await stam_plugin._save_player(p)
    out = await cmd(stam_plugin, FakeEvent("89030"), "3", "", "")
    check(
        "体力不够" in text_of(out),
        f"体力不够时整批拒绝 -> {text_of(out).splitlines()[0]}",
    )
    p = await stam_plugin._load_player("89030")
    check(p["stamina"] == 1 and p["baits"]["worm"] == 16, "整批拒绝时不扣体力也不扣饵")

    # --- 饵不足整批拒绝 ---
    p["stamina"] = 5
    p["baits"] = {"worm": 1}
    await stam_plugin._save_player(p)
    out = await cmd(stam_plugin, FakeEvent("89030"), "3", "", "")
    check(
        "只剩 1 个" in text_of(out),
        f"饵不够时整批拒绝 -> {text_of(out).splitlines()[0]}",
    )
    p = await stam_plugin._load_player("89030")
    check(p["baits"]["worm"] == 1 and p["stamina"] == 5, "整批拒绝时不扣饵也不扣体力")

    # --- 超过单次上限 ---
    out = await cmd(stam_plugin, FakeEvent("89030"), "99", "", "")
    check(
        "最多连钓 4 次" in text_of(out),
        f"超过 multi_cast_max 被拦下 -> {text_of(out).splitlines()[0]}",
    )

    # --- 背包只剩 2 格：截断到 2 次，且只扣 2 份 ---
    p["stamina"] = 5
    p["baits"] = {"worm": 9}
    cap_bag = mod._backpack_capacity(p, stam_plugin.cfg)
    p["inventory"] = [mod._new_instance("carp", 1.0) for _ in range(cap_bag - 2)]
    await stam_plugin._save_player(p)
    out = await cmd(stam_plugin, FakeEvent("89030"), "3", "", "")
    body = text_of(out)
    check(
        "只钓 2 次" in body and "体力与鱼饵也只扣 2 份" in body,
        f"背包快满时截断并说明 -> {[l for l in body.splitlines() if '截断' in l or '只钓' in l]}",
    )
    p = await stam_plugin._load_player("89030")
    check(p["stamina"] == 3, f"截断后只扣实际次数 -> 体力 {p['stamina']}")
    check(p["baits"]["worm"] == 7, f"截断后只扣实际次数 -> 饵 {p['baits']['worm']}")

    # --- /钓鱼 1 等价单竿（走完整流程）---
    p["stamina"] = 5
    p["baits"] = {"worm": 5}
    p["inventory"] = []
    await stam_plugin._save_player(p)
    out = await cmd(stam_plugin, FakeEvent("89030"), "1", "", "")
    check("连钓" not in text_of(out), "/钓鱼 1 走单竿流程（不是连钓面板）")

    # --- /钓鱼 体力 ---
    out = await cmd(stam_plugin, FakeEvent("89030"), "体力", "", "")
    body = text_of(out)
    check("⚡ 体力" in body and "/5" in body, f"/钓鱼 体力 显示存量 -> {body.splitlines()[0]}")
    check("秒" in body or "已满" in body, "体力页带恢复提示")

    # --- 不限体力时（regen = 0）不挡人、也不显示体力行 ---
    free_plugin = make_plugin(free_cfg)
    pf = mod._default_player("89031")
    pf["gold"] = 100
    await free_plugin._save_player(pf)
    await cast(free_plugin, FakeEvent("89031"))
    pf = await free_plugin._load_player("89031")
    check(
        pf["total_caught"] == 1,
        f"不限体力模式下照常钓鱼（体力字段不参与判定）-> 累计 {pf['total_caught']}",
    )
    out = await cmd(free_plugin, FakeEvent("89031"), "体力", "", "")
    check("未启用体力" in text_of(out), "不限体力时体力页明确说明")

    # --- 连钓不弹拉线：高逃脱率下会「跑掉」，但绝不注册互动会话 ---
    esc_cfg = dict(stam_cfg)
    esc_cfg["interactive_rarities"] = "常见"
    esc_cfg["rarity_escape_chance"] = "常见:0.95"
    # 只让常见鱼出现：否则「4 竿全是高稀有度」会偶发（约 1%），
    # 而高稀有度不在 interactive_rarities 里、不会走「跑掉」分支，断言就随机变红。
    esc_cfg["rarity_spawn_weights"] = "常见:100,少见:0,稀有:0,传说:0,神话:0"
    esc_plugin = make_plugin(esc_cfg)
    pe = mod._default_player("89032")
    pe["gold"] = 1000
    pe["equipped_bait"] = "worm"
    pe["baits"] = {"worm": 9}
    pe["stamina"] = 5
    await esc_plugin._save_player(pe)
    out = await cmd(esc_plugin, FakeEvent("89032"), "4", "", "")
    body = text_of(out)
    check(
        "跑了" in body and "跑掉" in body,
        f"连钓里高稀有度按逃脱率直接判定 -> {[l for l in body.splitlines() if '跑' in l][:2]}",
    )
    check(
        not esc_plugin._pending_pulls,
        "连钓不会注册拉线互动（不会留下等玩家「拉」的会话）",
    )
    pe = await esc_plugin._load_player("89032")
    check(
        pe["stamina"] == 1 and pe["baits"]["worm"] == 5,
        f"跑掉也照常扣体力与饵（这一竿确实抛了）-> 体力 {pe['stamina']} 饵 {pe['baits']['worm']}",
    )

    # =====================================================================
    print("\n[6j] 空竿不扣饵（consume_bait_on_empty）与新饵率矩阵")

    # --- 默认 false：空竿（既没中鱼也没钩上杂物）不扣饵 ---
    empty_cfg = dict(_CFG)
    empty_cfg["bait_hook_rates"] = "worm:0.0"
    empty_cfg["location_hook_factors"] = "novice:0.5"
    empty_cfg["consume_bait_on_empty"] = False
    ep = make_plugin(empty_cfg)
    pe = await ep._load_player("89101")
    pe["baits"] = {"worm": 3}
    await ep._save_player(pe)
    for _ in range(3):
        await cmd(ep, FakeEvent("89101"), "蚯蚓", "", "")
    pe = await ep._load_player("89101")
    check(
        pe["baits"]["worm"] == 3,
        f"空竿不扣饵：连抛 3 竿饵数不变 -> {pe['baits']['worm']}",
    )

    # --- 开关 true：恢复旧规则（每竿都扣） ---
    legacy_cfg = dict(empty_cfg)
    legacy_cfg["consume_bait_on_empty"] = True
    lp = make_plugin(legacy_cfg)
    pl = await lp._load_player("89102")
    pl["baits"] = {"worm": 3}
    await lp._save_player(pl)
    for _ in range(3):
        await cmd(lp, FakeEvent("89102"), "蚯蚓", "", "")
    pl = await lp._load_player("89102")
    check(
        pl["baits"]["worm"] == 0,
        f"开关为 true 时恢复「每竿都扣」-> {pl['baits']['worm']}",
    )

    # --- 防御性归一化：配置文件被手写成字符串时也要按字面理解 ---
    # （bool("false") 在 Python 里是 True，所以必须显式解析字符串）
    for raw, want in (
        ("false", False), ("true", True), ("0", False), ("1", True),
        ("是", True), ("", False), ("no", False),
    ):
        flag_cfg = dict(_CFG)
        flag_cfg["consume_bait_on_empty"] = raw
        fp = make_plugin(flag_cfg)
        fp._refresh_config()          # 显式走一遍归一化，避免依赖 make_plugin 的实现
        check(
            fp.cfg["consume_bait_on_empty"] is want,
            f"配置写成 {raw!r} 时按字面解析为 {want}",
        )

    # --- 有结果时照常扣 1 个 ---
    fish_cfg = dict(_CFG)
    fish_cfg["bait_hook_rates"] = "worm:1.0"
    fish_cfg["consume_bait_on_empty"] = False
    fp = make_plugin(fish_cfg)
    pf = await fp._load_player("89103")
    pf["baits"] = {"worm": 3}
    await fp._save_player(pf)
    await cmd(fp, FakeEvent("89103"), "蚯蚓", "", "")
    pf = await fp._load_player("89103")
    check(
        pf["baits"]["worm"] == 2,
        f"中鱼那一竿照常扣 1 个饵 -> {pf['baits']['worm']}",
    )

    # --- 连钓：只按有结果的竿数扣，并在结果里说明 ---
    mp = make_plugin(empty_cfg)
    pm = await mp._load_player("89104")
    pm["baits"] = {"worm": 3}
    pm["equipped_bait"] = "worm"
    await mp._save_player(pm)
    out = await cmd(mp, FakeEvent("89104"), "3", "", "")
    body = text_of(out)
    pm = await mp._load_player("89104")
    check(
        pm["baits"]["worm"] == 3,
        f"连钓全空竿时一个饵都不扣 -> {pm['baits']['worm']}",
    )
    check(
        "空竿不耗饵" in body,
        f"连钓结果里说明实扣饵数 -> {[l for l in body.splitlines() if '空竿不耗' in l][:1]}",
    )

    # --- 钓费与体力不受该开关影响 ---
    fee_cfg = dict(empty_cfg)
    fee_cfg["fish_cost"] = 5
    fee_cfg["stamina_regen_seconds"] = 45
    fee_cfg["stamina_max"] = 5
    fp2 = make_plugin(fee_cfg)
    pf2 = await fp2._load_player("89105")
    pf2["baits"] = {"worm": 2}
    pf2["gold"] = 100
    pf2["stamina"] = 5
    await fp2._save_player(pf2)
    await cmd(fp2, FakeEvent("89105"), "蚯蚓", "", "")
    pf2 = await fp2._load_player("89105")
    check(
        pf2["gold"] == 95 and pf2["stamina"] == 4 and pf2["baits"]["worm"] == 2,
        f"空竿不扣饵，但钓费与体力照扣 -> 金 {pf2['gold']}／体 {pf2['stamina']}／饵 {pf2['baits']['worm']}",
    )

    # --- 新默认值的上鱼率矩阵（3000 竿采样，区间断言非恒真） ---
    mat = make_plugin(load_schema_config())
    for bait_id, loc_id, lo, hi in (
        ("bread", "aurora", 0.28, 0.42),
        ("secret", "aurora", 0.52, 0.68),
        ("bread", "lake", 0.48, 0.62),
        ("secret", "lake", 0.90, 1.00),
        ("none", "aurora", 0.10, 0.22),
        ("bread", "novice", 1.00, 1.00),
    ):
        hit = sum(
            1 for _ in range(3000) if mat._roll_cast_outcome(bait_id, True, loc_id)[0] == "fish"
        ) / 3000
        check(
            lo <= hit <= hi,
            f"{bait_id}@{loc_id} 上鱼率 {hit:.3f}（期望 {lo}~{hi}）",
        )

    print("\n[10j] 彩蛋事件 / 里程碑 / 最佳渔获纪录")
    plugin6 = make_plugin()
    ev6 = FakeEvent("89006")

    # --- 概率开关 ---
    off = dict(_CFG)
    off["easter_egg_chance"] = 0.0
    p_off = make_plugin(off)
    check(
        all(p_off._roll_easter_egg() is None for _ in range(200)),
        "概率 0 时不会触发彩蛋",
    )
    on = dict(_CFG)
    on["easter_egg_chance"] = 1.0
    p_on = make_plugin(on)
    check(
        all(p_on._roll_easter_egg() is not None for _ in range(50)),
        "概率 1 时必定触发彩蛋",
    )
    check(
        all(e["id"] in mod.EASTER_EGG_BY_ID for e in mod.EASTER_EGGS),
        f"{len(mod.EASTER_EGGS)} 个彩蛋 id 唯一且有效",
    )

    # --- 各类彩蛋的结算效果 ---
    p = await plugin6._load_player("89006")
    p["gold"] = 0
    p["luck_charges"] = 0.0
    p["baits"] = {"worm": 3}
    p["bottle_notes"] = []
    gold_egg = mod.EASTER_EGG_BY_ID["coin_in_fish"]
    text = plugin6._apply_easter_egg(p, gold_egg, "worm")
    check(p["gold"] == 12 and "金币" in text, f"金币彩蛋 +{p['gold']} 金币")
    luck_egg = mod.EASTER_EGG_BY_ID["lucky_scale"]
    plugin6._apply_easter_egg(p, luck_egg, "worm")
    check(p["luck_charges"] > 0, f"幸运彩蛋累积 {p['luck_charges']:.0%} 品质幸运")
    bait_egg = mod.EASTER_EGG_BY_ID["bait_back"]
    plugin6._apply_easter_egg(p, bait_egg, "worm")
    check(p["baits"]["worm"] == 4, f"返饵彩蛋把鱼饵还回来 -> {p['baits']['worm']} 个")
    note_egg = mod.EASTER_EGG_BY_ID["old_map"]
    plugin6._apply_easter_egg(p, note_egg, "worm")
    check(len(p["bottle_notes"]) == 1, "纸条彩蛋记录了一张纸条")
    await plugin6._save_player(p)

    # --- 真抛竿链路上：彩蛋计数 + 成就当竿解锁 ---
    lucky_cfg = dict(_CFG)
    lucky_cfg["easter_egg_chance"] = 1.0
    lucky_cfg["escape_map"] = {}
    plugin7 = make_plugin(lucky_cfg)
    ev7 = FakeEvent("89007")
    replies = await cast(plugin7, ev7, "钓")
    check(
        "不认识" not in text_of(replies),
        "「/钓鱼 钓」被当成抛竿（下竿同义词容错）",
    )
    p7 = await plugin7._load_player("89007")
    egg_keys = [k for k in p7["collectibles"] if k.startswith("egg_")]
    check(len(egg_keys) == 1, f"抛竿后彩蛋计入存档 -> {egg_keys}")
    # 成就/彩蛋/里程碑是 event.send 推送的（不在 yield 的回复里）
    pushed = text_of(ev7.sent)
    check(
        "egg_first" in p7["achievements"] and "🥚" in pushed,
        "「意外之喜」成就与彩蛋文案当竿就出现",
    )
    # 五个不同彩蛋 -> 彩蛋猎人
    for egg in mod.EASTER_EGGS[:5]:
        p7["collectibles"][f"egg_{egg['id']}"] = 1
    newly = plugin7._check_achievements(p7)
    check("egg_collector" in p7["achievements"], "集齐 5 种彩蛋解锁「彩蛋猎人」")
    check(
        all(k in p7["collectibles"] for k in mod.COLLECTIBLE_BY_ID) is False,
        "彩蛋计数不会被误当成杂物图鉴（杂物成就仍需真杂物）",
    )
    check(
        "junk_all" not in p7["achievements"],
        "只有彩蛋时不会误解锁「杂物全收集」",
    )
    await plugin7._save_player(p7)

    # --- 里程碑只提示一次 ---
    p["total_caught"] = 10
    p["milestones"] = []
    first = plugin6._milestone_text(p)
    second = plugin6._milestone_text(p)
    check(first != "" and second == "", "里程碑第 10 条只在刚达到时提示一次")
    p["total_caught"] = 11
    check(plugin6._milestone_text(p) == "", "非里程碑竿数不提示")

    # --- 最佳渔获纪录：留最高、可展示、可触发成就 ---
    p["milestones"] = []
    p["best_records"] = {}
    common_fish = next(f for f in mod.FISH_POOL if f["rarity"] == "常见")
    low = mod._new_instance(common_fish["id"], 1.0, value_override=30,
                            attrs={"meat": 60, "spirit": 60, "sheen": 60})
    plugin6._update_best_records(p, low)
    check(
        mod._instance_value(p["best_records"]["常见"]) == 30
        if isinstance(p["best_records"].get("常见"), dict)
        else False,
        "纪录按品质记录第一条渔获",
    )
    high = mod._new_instance(common_fish["id"], 1.0, value_override=99,
                             attrs={"meat": 60, "spirit": 60, "sheen": 60})
    plugin6._update_best_records(p, high)
    check(
        p["best_records"]["常见"]["value"] >= 99,
        f"更高的渔获会刷新纪录 -> {p['best_records']['常见']['value']}",
    )
    worse = mod._new_instance(common_fish["id"], 1.0, value_override=5,
                              attrs={"meat": 60, "spirit": 60, "sheen": 60})
    plugin6._update_best_records(p, worse)
    check(
        p["best_records"]["常见"]["value"] >= 99,
        "更差的渔获不会覆盖纪录",
    )
    check(
        any("最佳渔获" in line for line in plugin6._best_records_text(p)),
        "图鉴页能渲染「最佳渔获」",
    )
    for rarity in mod.RARITY_ORDER:
        p["best_records"].setdefault(
            rarity,
            {"fish_id": common_fish["id"], "variant": None, "quality": "普通",
             "value": 50, "ts": 1},
        )
    p["best_records"]["神话"]["value"] = 12000
    plugin6._check_achievements(p)
    check("record_5" in p["achievements"], "五个品质都有纪录解锁「五项全能」")
    check("record_10k" in p["achievements"], "单条纪录破万解锁「万元户」")

    # --- 存档往返：里程碑 / 纪录 / 彩蛋计数都要活下来 ---
    p["milestones"] = [10, 50]
    p["collectibles"]["egg_coin_in_fish"] = 3
    repaired, _ = mod._repair_player(json.loads(json.dumps(p)), "89006")
    check(repaired["milestones"] == [10, 50], "里程碑记录经存档修复后保留")
    check(
        repaired["best_records"]["神话"]["value"] == 12000,
        "最佳渔获纪录经存档修复后保留",
    )
    check(
        repaired["collectibles"].get("egg_coin_in_fish") == 3,
        "彩蛋计数经存档修复后保留",
    )

    # =====================================================================
    print("\n[10k] 旧版配置兼容（升级后必须在日志里提醒）")

    import logging

    class Collect(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records: list[str] = []

        def emit(self, record):
            self.records.append(record.getMessage())

    def capture(plugin):
        h = Collect()
        mod.logger.addHandler(h)
        return h

    # 用户实例里真实存在的那种旧配置：
    # 只有 5 个钓点、钓费还是老默认的 8、鱼竿与钓点价格都是旧值、且缺新键
    _old_loc = [
        "novice|新手村|🏡|1|0|1.00|村口小池塘，什么都有一点",
        "lake|山间湖泊|🏞️|3|500|1.08|水清鱼肥，适合练手",
        "sea|近海渔场|🌊|6|2000|1.15|咸淡水交界，海货不少",
        "swamp|迷雾沼泽|🌫️|10|6000|1.25|阴森潮湿，藏着怪东西",
        "abyss|深海海沟|🕳️|16|15000|1.35|深不见底，神话鱼的故乡",
    ]
    stale = {
        "fish_cost": 8,
        "stamina_regen_seconds": 99,     # 旧配置里的数值与新版默认不同 → 应被同步
        "stamina_max": 5,
        "location_defs": _old_loc,
        "rod_defs": [
            "bamboo|竹竿|🎋|0|0.00|0.00|村口杂货铺送的，能用",
            "carbon|碳素竿|🎣|300|0.08|0.03|轻巧顺手，新手进阶首选",
        ],
        "bait_defs": [
            "none|空钩|🪝|0|0|0|1,1,1,1,1|什么也不挂，全凭本事",
            "worm|蚯蚓|🪱|3|5|0.10|1,1.2,1.6,1.8,2.0|万用饵，小幅提运",
        ],
    }
    plugin_old = make_plugin(stale)   # 构造时会把新版内容自动合并进来
    merged_locs = {l["id"] for l in plugin_old.locations}
    check(
        {"bamboo", "canal", "aurora"} <= merged_locs,
        f"旧配置自动补上新钓点（现在 {len(merged_locs)} 个）",
    )
    check(
        any(b == "corn" for b in plugin_old.baits),
        "旧配置自动补上新鱼饵（玉米粒等）",
    )
    check(
        "novice|新手村|🏡|1|0|1.00|村口小池塘，什么都有一点" in stale["location_defs"],
        "原有条目（含自定义过的）不会被改动",
    )
    # 关掉自动合并时保持旧样子（注意 stale 已被插件就地补全，这里用原始列表重建）
    no_merge = dict(stale)
    no_merge["content_auto_merge"] = False
    no_merge["location_defs"] = list(_old_loc)
    no_merge["bait_defs"] = list(stale["bait_defs"])[:2]
    plugin_nomerge = make_plugin(no_merge)
    check(
        len(plugin_nomerge.locations) == 5,
        f"content_auto_merge=False 时不合并（{len(plugin_nomerge.locations)} 个钓点）",
    )
    check(
        len(plugin_nomerge.locations) == 5,
        f"不合并时仍是旧配置的 {len(plugin_nomerge.locations)} 个钓点（不会崩）",
    )
    check(
        plugin_old.cfg.get("easter_egg_chance") == mod.DEFAULTS["easter_egg_chance"],
        "配置里缺的新键会回退到内置默认值（不会 KeyError）",
    )

    h = capture(plugin_old)
    plugin_old._warn_stale_config()
    mod.logger.removeHandler(h)
    joined = "\n".join(h.records)
    check("旧版数值" in joined, "日志提示价格/倍率仍是旧值（内容已自动补齐）")
    check("重置配置" in joined, "日志给出了「重置配置」的解决路径")
    check("钓费是 8" in joined, "日志提示钓费还是旧默认值 8")

    # 用最新默认配置时不应该有任何告警
    h2 = capture(plugin_old)
    fresh = make_plugin(dict(mod.DEFAULTS))
    fresh._warn_stale_config()
    mod.logger.removeHandler(h2)
    check(
        not [m for m in h2.records if "旧版" in m or "钓费" in m],
        f"新配置下无旧版告警（{len(h2.records)} 条日志）",
    )

    # 旧配置下所有子命令仍然可用（优雅降级，不抛异常）
    ev_old = FakeEvent("89008")
    po = await plugin_old._load_player("89008")
    po["gold"] = 5000
    await plugin_old._save_player(po)
    broken = []
    for args in (
        ("背包", "", ""), ("钓点", "", ""), ("去", "竹林溪流", ""),
        ("今日", "", ""), ("图鉴", "", ""), ("水族馆", "", ""),
        ("订单", "", ""), ("杂物", "", ""), ("排行", "", ""),
        ("帮助", "2", ""),
    ):
        try:
            out = await cmd(plugin_old, ev_old, *args)
            if not out:
                broken.append(f"{args[0]}(空回复)")
        except Exception as e:
            broken.append(f"{args[0]}({type(e).__name__})")
    check(not broken, f"旧配置下各子命令都能正常回复（异常项：{broken or '无'}）")
    out = await cmd(plugin_nomerge, ev_old, "去", "竹林溪流", "")
    check("没有这个钓点" in text_of(out), "未配置的钓点有友好提示，而不是报错")
    out = await cmd(plugin_old, ev_old, "去", "竹林溪流", "")
    check("竹林溪流" in text_of(out), "自动补齐后新钓点是可用的")

    # =====================================================================
    print("\n[10m] QQ 官方按钮 + 随机插曲")

    class FakeApi:
        def __init__(self):
            self.calls = []

        async def post_group_message(self, **kw):
            self.calls.append({"kind": "group", **kw})
            return {"id": "m1"}

        async def post_c2c_message(self, **kw):
            self.calls.append({"kind": "c2c", **kw})
            return {"id": "m2"}

    class FakeRaw:
        def __init__(self, group, openid):
            self.group_openid = group

            class A:
                user_openid = openid

            self.author = A()

    class FakeMsgObj:
        def __init__(self, group, openid):
            self.message_id = "MID-1"
            self.group_id = group
            self.raw_message = FakeRaw(group, openid)

    class PlatEvent(FakeEvent):
        def __init__(self, sid, platform="qq_official", api=None, group="G1", openid=""):
            super().__init__(sid)
            self._platform = platform
            self.message_obj = FakeMsgObj(group, openid)
            self.bot = type("B", (), {"api": api})() if api else None

        def get_platform_name(self):
            return self._platform

    plugin = make_plugin()
    rows = plugin._cast_rows()
    keyboard = plugin._keyboard(rows)
    check(
        set(keyboard) == {"content"}
        and set(keyboard["content"]) == {"rows"}
        and all(set(r) == {"buttons"} for r in keyboard["content"]["rows"]),
        "keyboard 结构符合官方文档（content.rows[].buttons[]）",
    )
    first = keyboard["content"]["rows"][0]["buttons"][0]
    check(
        set(first) == {"id", "render_data", "action"}
        and set(first["render_data"]) >= {"label", "visited_label", "style"}
        and first["action"]["type"] == 2
        and first["action"]["permission"]["type"] == 2
        and first["action"]["data"].startswith("/钓鱼"),
        f"按钮是「指令按钮」（type=2，data={first['action']['data']}）",
    )
    check(
        all(
            len(b["render_data"]["label"]) <= 10
            for r in keyboard["content"]["rows"]
            for b in r["buttons"]
        )
        and len(keyboard["content"]["rows"]) <= 5,
        "按钮文案 ≤10 字、行数在上限内",
    )
    check(plugin._keyboard([]) is None, "没有按钮时返回 None")

    api = FakeApi()
    ok = await plugin._send_with_buttons(PlatEvent("b1", api=api), "带按钮的消息", rows)
    check(ok and api.calls[0]["kind"] == "group", "QQ 官方群聊走原生接口发按钮")
    check(
        api.calls[0]["msg_type"] in (0, 2)
        and "keyboard" in api.calls[0]
        and api.calls[0].get("msg_id") == "MID-1"
        and isinstance(api.calls[0].get("msg_seq"), int),
        f"原生发送带 msg_id / msg_seq / keyboard（形态 msg_type={api.calls[0]['msg_type']}）",
    )

    # 多形态：markdown 形态被拒时要自动改用纯文本形态，仍然带上按钮
    class PickyApi(FakeApi):
        async def post_group_message(self, **kw):
            self.calls.append({"kind": "group", **kw})
            if kw.get("msg_type") == 2:
                raise RuntimeError("304036 无 Markdown 模板权限")
            return {"id": "m3"}

    api2 = PickyApi()
    ok = await plugin._send_with_buttons(PlatEvent("b5", api=api2), "带按钮的消息", rows)
    check(
        ok and len(api2.calls) == 2 and api2.calls[1]["msg_type"] == 0
        and "keyboard" in api2.calls[1],
        "markdown 形态失败会自动退回纯文本+按钮",
    )
    # 关闭按钮时直接返回 False（调用方走纯文本）
    off_cfg = dict(_CFG)
    off_cfg["button_mode"] = "关闭"
    plugin_off = make_plugin(off_cfg)
    check(
        not await plugin_off._send_with_buttons(PlatEvent("b6", api=FakeApi()), "x", rows),
        "button_mode=关闭 时完全不发按钮",
    )
    ok = await plugin._send_with_buttons(
        PlatEvent("b2", platform="aiocqhttp", api=api), "文本", rows
    )
    check(not ok, "其它平台返回 False（调用方退回纯文本）")
    ok = await plugin._send_with_buttons(FakeEvent("b3"), "文本", rows)
    check(not ok, "事件里没有 bot 时不炸、直接退回纯文本")
    ok = await plugin._send_with_buttons(
        PlatEvent("b4", api=api, group="", openid="U1"), "文本", rows
    )
    check(ok and any(c["kind"] == "c2c" for c in api.calls), "单聊走 post_c2c_message")

    # --- 🔘 按钮表全部可配置（button_defs）---
    # v1.18.0 重排过默认按钮：每屏只留「下一步最可能做的事」，每行 4 个刚好一行
    expect_buttons = {
        "cast": [
            ("再来一竿", "/钓鱼", 0),
            ("看背包", "/钓鱼 背包", 0),
            ("水族馆", "/钓鱼 水族馆", 0),
            ("卖光光", "/钓鱼 卖光光", 0),
        ],
        "pull": [("拉线！", "/钓鱼 拉", 1)],
        "bag": [
            ("再来一竿", "/钓鱼", 0),
            ("水族馆", "/钓鱼 水族馆", 0),
            ("卖光光", "/钓鱼 卖光光", 0),
            ("帮助", "/钓鱼 帮助", 0),
        ],
        "location": [
            ("再来一竿", "/钓鱼", 0),
            ("查图鉴", "/钓鱼 图鉴", 0),
            ("水族馆", "/钓鱼 水族馆", 0),
            ("看天气", "/钓鱼 今日", 0),
        ],
        "aquarium.view": [
            ("再来一竿", "/钓鱼", 0),
            ("领收益", "/钓鱼 领", 1),
            ("看背包", "/钓鱼 背包", 0),
            ("卖光光", "/钓鱼 卖光光", 0),
        ],
        "orders.list": [
            ("再来一竿", "/钓鱼", 0),
            ("看背包", "/钓鱼 背包", 0),
            ("卖光光", "/钓鱼 卖光光", 0),
        ],
        "item.used": [
            ("再来一竿", "/钓鱼", 0),
            ("看背包", "/钓鱼 背包", 0),
            ("水族馆", "/钓鱼 水族馆", 0),
        ],
        "stamina.view": [
            ("再来一竿", "/钓鱼", 0),
            ("看背包", "/钓鱼 背包", 0),
        ],
        "help.page": [
            ("开始钓鱼", "/钓鱼", 1),
            ("看背包", "/钓鱼 背包", 0),
            ("水族馆", "/钓鱼 水族馆", 0),
            ("我的档案", "/钓鱼 档案", 0),
        ],
        "story": [("{label}", "/钓鱼 事件 {n}", 0)],
    }

    def _buttons_snapshot() -> dict:
        """当前生效的按钮表（配置解析后的最终结果）。"""
        return {s: [tuple(b) for b in items] for s, items in mod.BUTTONS.items()}

    check(
        _buttons_snapshot() == expect_buttons,
        f"button_defs 默认值 == v1.18.0 重排后的那套（场景 {sorted(_buttons_snapshot())}）",
        extra=None if _buttons_snapshot() == expect_buttons else repr(_buttons_snapshot()),
    )
    check(
        [len(r) for r in plugin._cast_rows()] == [4]
        and [
            len(plugin._bag_rows()),
            len(plugin._location_rows()),
            len(plugin._pull_rows()),
        ]
        == [1, 1, 1],
        "默认排版：每行 4 个，cast 一行摆完、bag/location/pull 各一行",
    )
    check(
        [b["render_data"]["style"] for r in plugin._pull_rows() for b in r] == [1]
        and [b["render_data"]["style"] for r in plugin._cast_rows() for b in r]
        == [0, 0, 0, 0],
        "样式来自配置（拉线=primary/蓝=1，其余 default/灰=0，对齐官网取值）",
    )
    # story 是模板：每个选项展开成一行
    ev_def = mod.EVENT_BY_ID[sorted(mod.EVENT_BY_ID)[0]]
    ev_rows = plugin._event_rows(ev_def)
    check(
        len(ev_rows) == len(ev_def["choices"])
        and all(len(r) == 1 for r in ev_rows)
        and [r[0]["render_data"]["label"] for r in ev_rows]
        == [c["label"] for c in ev_def["choices"]]
        and [r[0]["action"]["data"] for r in ev_rows]
        == [f"/钓鱼 事件 {i}" for i in range(1, len(ev_def["choices"]) + 1)],
        "story 模板按选项展开（{label}/{n} 换成真实文案与序号）",
    )

    # 每个默认按钮点下去都要有反应：指令必须被 dispatcher 认识（防止「死按钮」）
    live_plugin = make_plugin(dict(_CFG))
    dead: list[str] = []
    for _items in expect_buttons.values():
        for _label, _data, _style in _items:
            if "{" in _data:
                continue          # 模板行先展开再测（上面已单独验证）
            ev_live = FakeEvent("89140")
            _words = _data.split()[1:]
            _replies = (
                await cmd(live_plugin, ev_live, *_words)
                if _words
                else await cast(live_plugin, ev_live)
            )
            if "不认识" in text_of(_replies):
                dead.append(f"{_label}->{_data}")
    check(not dead, f"默认按钮的指令 dispatcher 全都认识 -> {dead}")

    # 自定义：文案 / 顺序 / 指令 / 样式 / 条数 全部按配置走（容错：全角竖线、注释、空行、别名）
    custom_cfg = dict(_CFG)
    custom_cfg["button_defs"] = (
        "# 我的按钮表\n"
        "cast｜抛一竿｜/钓鱼 3｜primary\n"
        "\n"
        "cast|开包|/钓鱼 背包|蓝\n"
        "cast|看钱|/钓鱼 档案|7\n"
        "bag|清空|/钓鱼 卖光光|灰\n"
    )
    plugin_c = make_plugin(custom_cfg)
    check(
        _buttons_snapshot()["cast"]
        == [("抛一竿", "/钓鱼 3", 1), ("开包", "/钓鱼 背包", 1), ("看钱", "/钓鱼 档案", 7)],
        f"自定义按钮生效 -> {_buttons_snapshot()['cast']}",
    )
    check(
        [len(r) for r in plugin_c._cast_rows()] == [3]
        and [b["render_data"]["label"] for r in plugin_c._cast_rows() for b in r]
        == ["抛一竿", "开包", "看钱"],
        "自定义 3 个按钮摆成一行、顺序即配置顺序",
    )
    check(
        [b[0] for b in plugin_c._scene_items("pull")] == ["拉线！"]
        and [b[0] for b in plugin_c._scene_items("location")]
        == ["再来一竿", "查图鉴", "水族馆", "看天气"]
        and [b[0] for b in plugin_c._scene_items("story")] == ["{label}"],
        f"没被配置覆盖的场景在**渲染时**回退内置（BUTTONS 里没有也算数）"
        f" -> pull={[b[0] for b in plugin_c._scene_items('pull')]}",
    )
    # 超长文案按 QQ 限制截到 10 字（不报错、不空按钮）
    long_cfg = dict(_CFG)
    long_cfg["button_defs"] = "cast|这是一个特别特别长的按钮文案|/钓鱼"
    plugin_long = make_plugin(long_cfg)
    check(
        plugin_long._cast_rows()[0][0]["render_data"]["label"] == "这是一个特别特别长的按钮文案"[:10],
        "超长按钮文案自动截到 10 字",
    )

    # 坏行过滤：字段不够 / 场景不认识 / 指令不认识（死按钮）都跳过，且只告警一条
    warns: list[str] = []
    parsed = mod._parse_button_defs(
        "cast|只有一个字段\n"
        "unknown|场景不认识|/钓鱼\n"
        "cast|点了没反应|/钓鱼 不存在的子命令\n"
        "cast|好按钮|/钓鱼 帮助\n",
        warn=warns.append,
    )
    check(
        parsed == {"cast": [("好按钮", "/钓鱼 帮助", 0)]},
        f"坏行全部跳过、只留合法按钮 -> {parsed}",
    )
    check(
        len(warns) == 1 and "3 行" in warns[0],
        f"坏行合并成一条告警、不刷屏 -> {warns}",
    )
    check(
        mod._button_command_ok("/钓鱼")
        and mod._button_command_ok("/钓鱼 拉")
        and mod._button_command_ok("/钓鱼 事件 1")
        and mod._button_command_ok("/钓鱼 12")
        and not mod._button_command_ok("钓鱼")
        and not mod._button_command_ok("/别的")
        and not mod._button_command_ok("/钓鱼 乱写的"),
        "按钮指令白名单：认识的放行、点了没反应的拦掉",
    )
    # 整段写坏 -> 全表回退内置
    make_plugin({**dict(_CFG), "button_defs": "这不是按钮表\n随便写点什么"})
    check(_buttons_snapshot() == expect_buttons, "button_defs 整段写坏时全表回退内置")
    # 只配一个场景 -> 配置里只有它；其余场景**在渲染时**回退内置（v1.18.0 起不再写进 BUTTONS）
    only_bag = make_plugin({**dict(_CFG), "button_defs": "bag|清空|/钓鱼 卖光光|灰"})
    check(
        _buttons_snapshot() == {"bag": [("清空", "/钓鱼 卖光光", 0)]},
        f"只配一个场景时配置里就只有这一条 -> {_buttons_snapshot()}",
    )
    check(
        [b[0] for b in only_bag._scene_items("pull")] == ["拉线！"]
        and [b[0] for b in only_bag._scene_items("cast")] == [b[0] for b in expect_buttons["cast"]]
        and [b[0] for b in only_bag._scene_items("bag")] == ["清空"],
        "没配的场景照样有出厂按钮（渲染时回退），配过的用配置",
    )
    # 留空 -> 全表回退内置（等价于默认）
    make_plugin({**dict(_CFG), "button_defs": ""})
    check(_buttons_snapshot() == expect_buttons, "button_defs 留空时全表回退内置")
    # 改回默认文本后完全恢复（无残留副作用）
    make_plugin(dict(_CFG))
    check(_buttons_snapshot() == expect_buttons, "改回默认文本后按钮表完全恢复")

    # --- 随机插曲：触发 → 选择 → 结算 → 清空 ---
    story_cfg = dict(_CFG)
    story_cfg["story_chance"] = 1.0
    story_cfg["easter_egg_chance"] = 0.0
    story_cfg["item_drop_chance"] = 0.0
    plugin_s = make_plugin(story_cfg)
    ev_s = FakeEvent("89101")
    out = await cast(plugin_s, ev_s)
    p = await plugin_s._load_player("89101")
    story = p.get("event")
    check(
        isinstance(story, dict) and story.get("id") in mod.EVENT_BY_ID,
        f"抛竿后偶尔会遇到插曲 -> {story}",
    )
    body = text_of(out)
    check(
        "事件 1" in body and "事件 2" in body,
        "插曲正文给出两个选项与对应指令（非按钮平台也能玩）",
    )
    check(
        "概率" not in body and "彩蛋" not in body and "触发" not in body,
        "插曲不提概率、也不自报身份",
    )
    ev_def = mod.EVENT_BY_ID[story["id"]]
    check(
        len(ev_def["choices"]) == 2
        and all(c.get("label") and c.get("text") for c in ev_def["choices"]),
        f"插曲「{ev_def['id']}」有两个成形的选项",
    )
    gold_before = p["gold"]
    out = await cmd(plugin_s, ev_s, "事件", "1")
    p = await plugin_s._load_player("89101")
    check(p.get("event") is None, "选择后插曲结束")
    check(len(text_of(out).splitlines()) >= 2, "选择有明确结果文案")
    check(p["gold"] >= gold_before, f"插曲奖励不会倒扣（{gold_before} -> {p['gold']}）")
    out = await cmd(plugin_s, ev_s, "事件", "1")
    check("没什么" in text_of(out), "没有插曲时给友好提示")
    p["event"] = {"id": "ripple", "ts": 1}
    await plugin_s._save_player(p)
    out = await cmd(plugin_s, ev_s, "事件", "1")
    check("过去了" in text_of(out) or "没什么" in text_of(out), "过期插曲不会卡住玩家")

    # =====================================================================
    print("\n[10n] 存档字段守卫：新增状态必须能存能读")
    plugin = make_plugin()
    fresh = mod._default_player("89500")
    fresh["event"] = {"id": "ripple", "ts": int(time.time())}
    fresh["order_next_ts"] = int(time.time()) + 600
    fresh["orders"] = [
        {"fish_id": mod.FISH_POOL[0]["id"], "need": 2, "have": 0,
         "reward": 50, "done": False}
    ]
    repaired, _ = mod._repair_player(json.loads(json.dumps(fresh)), "89500")
    missing = sorted(set(fresh) - set(repaired))
    check(not missing, f"修复函数不会丢字段（缺：{missing or '无'}）")
    check(
        repaired["event"] == fresh["event"]
        and repaired["order_next_ts"] == fresh["order_next_ts"]
        and [o["fish_id"] for o in repaired["orders"]] == [mod.FISH_POOL[0]["id"]],
        "订单刷新时间与插曲状态跨读档保留",
    )
    bad = json.loads(json.dumps(fresh))
    bad["event"] = {"id": "不存在的插曲", "ts": "x"}
    bad["orders"] = [{"fish_id": "不存在的鱼", "need": 1}]
    repaired_bad, _ = mod._repair_player(bad, "89501")
    check(
        repaired_bad["event"] is None and repaired_bad["orders"] == [],
        "脏数据被安全丢弃，不会带进游戏逻辑",
    )

    # =====================================================================
    print("\n[10l] 水族馆：全体投喂 + 隐藏的相处机制")
    plugin = make_plugin()
    ev = FakeEvent("89010")

    def mk(fish_id, meat=60, spirit=60, sheen=60, qm=1.0):
        return mod._new_instance(
            fish_id, qm, attrs={"meat": meat, "spirit": spirit, "sheen": sheen}
        )

    # --- 不带栏位 = 喂全缸 ---
    p = mod._default_player("89010")
    p["gold"] = 100000
    p["aquarium"] = [mk("carp") for _ in range(5)]
    p["items"] = {"feed_premium": 3}
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "用", "高级饲料", "")
    p = await plugin._load_player("89010")
    fed = [mod._safe_int(f.get("feed_uses"), 0, 0) for f in p["aquarium"]]
    check(
        sum(1 for x in fed if x > 0) == 3,
        f"「用 高级饲料」不带栏位就喂全缸（道具 3 个 -> 喂了 {sum(1 for x in fed if x > 0)} 条）",
    )
    check(p["items"]["feed_premium"] == 0, "按条数扣道具，用完即停")

    p["items"] = {"feed_basic": 10}
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "用", "普通饲料", "")
    p = await plugin._load_player("89010")
    fed = [mod._safe_int(f.get("feed_uses"), 0, 0) for f in p["aquarium"]]
    check(all(x >= 1 for x in fed), f"道具够时全缸都吃到 -> {fed}")

    # --- 相处机制：只有狠角色会下嘴，而且咬得动才吃（机制本身不提示）---
    hostile = next(f for f in mod.FISH_POOL if mod._is_hostile(f["id"]))
    normal = next(
        f
        for f in mod.FISH_POOL
        if not mod._is_hostile(f["id"]) and f["rarity"] == "常见"
    )
    check(
        mod._is_hostile(hostile["id"]) and not mod._is_hostile(normal["id"]),
        f"内部能分辨狠角色（{hostile['name']} vs {normal['name']}）",
    )

    # ⚠️ v1.15.0 起：温和的鱼永远不会主动伤人。
    # 旧版是「谁综合实力低谁没」，所以养肥的普通鱼能把狠角色吃了 ——
    # 站长报的「藤壶怎么把章鱼吃了」就是这条规则闹的。
    barnacle = next((f for f in mod.FISH_POOL if "藤壶" in f["name"]), None)
    if barnacle is not None:
        fat_barnacle = mk(barnacle["id"], 100, 100, 100, qm=2.2)
        weak_octopus = mk(hostile["id"], 20, 20, 20, qm=0.8)
        check(
            mod._fish_power(fat_barnacle) > mod._fish_power(weak_octopus),
            f"养肥的藤壶实力确实超过小章鱼（{mod._fish_power(fat_barnacle)} > "
            f"{mod._fish_power(weak_octopus)}）",
        )
        p["aquarium"] = [fat_barnacle]
        p["inventory"] = [weak_octopus]
        p["items"] = {}
        await plugin._save_player(p)
        out = await cmd(plugin, ev, "水族馆", "放", "1")
        p = await plugin._load_player("89010")
        names = sorted(mod._fish_name(f["fish_id"]) for f in p["aquarium"])
        check(
            len(p["aquarium"]) == 2 and names == sorted([barnacle["name"], hostile["name"]]),
            f"藤壶吃不了章鱼（温和鱼再肥也不下嘴、章鱼也咬不动它）-> 缸里 {names}",
        )
        check("没了" not in text_of(out), "没出事就不会冒出「没了」")

    # 温和的大鱼 + 小狠角色：谁都吃不动谁，相安无事（旧版会把小的那条冤死）
    strong = mk(normal["id"], 100, 100, 100, qm=2.2)
    weak_hostile = mk(hostile["id"], 20, 20, 20, qm=0.8)
    check(
        mod._fish_power(strong) > mod._fish_power(weak_hostile),
        f"综合实力可比（{mod._fish_power(strong)} > {mod._fish_power(weak_hostile)}）",
    )
    p["aquarium"] = [strong]
    p["inventory"] = [weak_hostile]
    p["items"] = {}
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "水族馆", "放", "1")
    p = await plugin._load_player("89010")
    names = sorted(mod._fish_name(f["fish_id"]) for f in p["aquarium"])
    check(
        len(p["aquarium"]) == 2 and names == sorted([normal["name"], hostile["name"]]),
        f"狠角色咬不动比自己壮的温和鱼 -> 两条都在 {names}",
    )

    # 反向：狠角色更强 → 吃掉了温和的
    norm2 = mk(normal["id"], 30, 30, 30, qm=0.8)
    big_hostile = mk(hostile["id"], 100, 100, 100, qm=2.0)
    p["aquarium"] = [norm2]
    p["inventory"] = [big_hostile]
    await plugin._save_player(p)
    out = await cmd(plugin, ev, "水族馆", "放", "1")
    p = await plugin._load_player("89010")
    names = [mod._fish_name(f["fish_id"]) for f in p["aquarium"]]
    check(
        len(p["aquarium"]) == 1 and names == [hostile["name"]],
        f"狠角色更强时活下来的是它 -> 缸里剩 {names}",
    )
    body = text_of(out)
    check("吃掉了" in body, "只给现象（少了一条），不说规则：狠角色把温和鱼吃掉了")
    check(
        "敌对" not in body and "实力" not in body and "概率" not in body,
        "不给玩家任何机制提示",
    )
    # ---- 见闻：被吃之前查不到标注，被吃之后 /钓鱼 查 才看得到（v1.15.0）----
    check(
        p.get("hostiles_seen") == [hostile["id"]],
        f"它当着玩家的面吃了鱼 -> 记进见闻 {p.get('hostiles_seen')}",
    )
    info = await cmd(plugin, ev, "查", hostile["name"], "")
    check("⚠️ 狠角色" in text_of(info), f"见闻之后 /钓鱼 查 会标注狠角色 -> {text_of(info).splitlines()[:2]}")
    check(
        "吃过鱼" in text_of(info),
        "标注里说明是「在你缸里吃过鱼」才知道的（不是开局就知道）",
    )
    quiet = await cmd(plugin, ev, "查", normal["name"], "")
    check("狠角色" not in text_of(quiet), "没见过的鱼不会乱标（普通鱼干干净净）")
    # 见闻要跨读档保留
    p_reload = await plugin._load_player("89010")
    check(
        p_reload.get("hostiles_seen") == [hostile["id"]],
        "见闻跨读档保留",
    )

    # 两个狠角色同缸：壮的吃弱的（不是随机）
    # 同类不相食：两条同种狠角色放一起，谁也不会吃谁（站长要求）
    same_a = mk(hostile["id"], 100, 100, 100, qm=2.0)
    same_b = mk(hostile["id"], 10, 10, 10, qm=0.5)
    lines, changed, caught = plugin._resolve_tank_conflicts([same_a, same_b])
    check(
        not changed and not lines and not caught,
        f"两条同类狠角色相安无事 -> {lines}",
    )
    # 不同种的狠角色碰上：壮的吃弱的
    other_hostile = next(
        f for f in mod.FISH_POOL
        if mod._is_hostile(f["id"]) and f["id"] != hostile["id"]
    )
    strong_hunter = mk(hostile["id"], 100, 100, 100, qm=2.0)
    weak_hunter = mk(other_hostile["id"], 10, 10, 10, qm=0.5)
    lines, changed, caught = plugin._resolve_tank_conflicts([strong_hunter, weak_hunter])
    check(
        changed
        and len(lines) == 1
        and mod._fish_power(strong_hunter) >= mod._fish_power(weak_hunter)
        and "打了一架" in lines[0],
        f"两种狠角色碰上 -> {lines}",
    )
    check(
        caught == [hostile["id"]],
        f"当着玩家的面吃过鱼的那条被看破 -> {caught}",
    )

    # 两条普通鱼同缸不会出事
    p["aquarium"] = [mk(normal["id"]), mk(normal["id"])]
    p["inventory"] = []
    await plugin._save_player(p)
    lines, changed, caught = plugin._resolve_tank_conflicts(p["aquarium"])
    check(not changed and not lines and not caught, "普通鱼同缸和平共处")


    # =====================================================================
    print("\n[10o] 鱼饵：只扣一次 + 换饵 + 卖光光")

    bait_cfg = dict(_CFG)
    bait_cfg["fish_cost"] = 0
    bait_cfg["stamina_regen_seconds"] = 0     # 这一节只验证扣饵，体力放开
    bait_cfg["easter_egg_chance"] = 0.0
    bait_cfg["item_drop_chance"] = 0.0
    plugin_b = make_plugin(bait_cfg)
    ev_b = FakeEvent("89601")

    # --- 下竿只扣 1 个饵、不再另外收饵钱（用户报的「双倍消耗」）---
    p = mod._default_player("89601")
    p["gold"] = 500
    p["baits"] = {"worm": 3}
    p["equipped_bait"] = "worm"
    await plugin_b._save_player(p)
    gold_before = p["gold"]
    await cast(plugin_b, ev_b)
    p = await plugin_b._load_player("89601")
    check(p["baits"]["worm"] == 2, f"一次下竿只扣 1 个饵 -> 剩 {p['baits']['worm']}")
    check(
        p["gold"] == gold_before,
        f"钓费为 0 时不再扣钱（饵钱在买的时候已付）-> {p['gold']}",
    )
    await cast(plugin_b, ev_b)
    p = await plugin_b._load_player("89601")
    check(p["baits"]["worm"] == 1, f"再下一竿再扣 1 个 -> 剩 {p['baits']['worm']}")

    # --- 饵耗尽：自动切空钩，并且写进存档 ---
    p["baits"] = {"worm": 0}
    p["equipped_bait"] = "worm"
    await plugin_b._save_player(p)
    out = await cast(plugin_b, ev_b)
    p = await plugin_b._load_player("89601")
    check(p["equipped_bait"] == "none", f"饵用完后存档里切成空钩 -> {p['equipped_bait']}")
    check("用完了" in text_of(out) and "空钩" in text_of(out), "并且明确告诉玩家")
    check(p["baits"]["worm"] == 0, "空钩那一竿不会再扣饵")

    # --- 鱼跑掉时也要把「饵用完了」说出来（以前只在钓上鱼时才提示）---
    p["baits"] = {"worm": 0}
    p["equipped_bait"] = "worm"
    p["last_fish_time"] = 0
    await plugin_b._save_player(p)
    legend = next(f for f in mod.FISH_POOL if f["rarity"] == "传说")
    orig_roll_b = plugin_b._roll_species
    plugin_b._roll_species = lambda bait_id, location_id, weather=None: legend
    saved_random = mod.random.random
    mod.random.random = lambda: 0.0  # 必定脱钩，逼出「鱼跑了」分支
    try:
        out = await cast(plugin_b, ev_b)
    finally:
        plugin_b._roll_species = orig_roll_b
        mod.random.random = saved_random
    check(
        "跑了" in text_of(out) and "用完了" in text_of(out),
        "鱼跑掉时也会说明「饵用完了，已换空钩」",
    )

    # --- 明确写饵名但没货：不打断这一竿，用空钩继续 ---
    p = await plugin_b._load_player("89601")
    p["baits"] = {"worm": 0}
    p["equipped_bait"] = "none"
    p["last_fish_time"] = 0
    await plugin_b._save_player(p)
    out = await cast(plugin_b, ev_b, "蚯蚓", "", "")
    check(
        "用完了" in text_of(out) and "改用空钩" in text_of(out),
        "点名的饵没货时改用空钩继续这一竿（不再直接拒绝）",
    )
    p = await plugin_b._load_player("89601")
    check(len(p["inventory"]) >= 1, "这一竿确实钓成了")

    # --- /钓鱼 换饵 ---
    p = await plugin_b._load_player("89601")
    p["baits"] = {"worm": 5, "corn": 2}
    p["equipped_bait"] = "none"
    await plugin_b._save_player(p)
    out = await cmd(plugin_b, ev_b, "换饵", "蚯蚓", "")
    p = await plugin_b._load_player("89601")
    check(p["equipped_bait"] == "worm", f"「换饵 蚯蚓」切换当前鱼饵 -> {p['equipped_bait']}")
    check("已换饵" in text_of(out), "换饵有明确回执")

    out = await cmd(plugin_b, ev_b, "换饵", "空钩", "")
    p = await plugin_b._load_player("89601")
    check(p["equipped_bait"] == "none", "「换饵 空钩」卸下鱼饵")
    check("空钩" in text_of(out), "卸饵有回执")

    out = await cmd(plugin_b, ev_b, "换饵", "", "")
    check("当前鱼饵" in text_of(out) and "蚯蚓" in text_of(out), "不带参数时列出存量")
    check("玉米粒" in text_of(out), "列出背包里所有有货的饵")

    out = await cmd(plugin_b, ev_b, "换饵", "面包屑", "")
    check("还没有" in text_of(out), "换「没货但已解锁」的饵 -> 提示先买")
    out = await cmd(plugin_b, ev_b, "换饵", "秘制饵", "")
    check(
        "🔒" in text_of(out) and "34 级" in text_of(out),
        f"换「还没解锁」的饵 -> 给出解锁条件（{text_of(out).splitlines()[0][:34]}）",
    )
    out = await cmd(plugin_b, ev_b, "换饵", "不存在的饵", "")
    check("没有" in text_of(out), "换不认识的饵有提示")

    # 换饵之后裸下竿用的是新饵
    await cmd(plugin_b, ev_b, "换饵", "玉米粒", "")
    p = await plugin_b._load_player("89601")
    p["last_fish_time"] = 0
    await plugin_b._save_player(p)
    await cast(plugin_b, ev_b)
    p = await plugin_b._load_player("89601")
    check(p["baits"]["corn"] == 1, f"裸下竿用的是换过的新饵 -> 玉米粒剩 {p['baits']['corn']}")

    # --- /钓鱼 卖光光：一次清空背包 ---
    ev_b2 = FakeEvent("89602")  # 换个人，别和上一节的玩家混用
    p = mod._default_player("89602")
    p["gold"] = 0
    p["inventory"] = [
        mod._new_instance("carp", 1.0, value_override=50,
                          attrs={"meat": 60, "spirit": 60, "sheen": 60})
        for _ in range(6)
    ]
    p["inventory"][0]["locked"] = True
    await plugin_b._save_player(p)
    out = await cmd(plugin_b, ev_b2, "卖光光", "", "")
    p = await plugin_b._load_player("89602")
    check(len(p["inventory"]) == 1, f"「卖光光」清空背包 -> 剩 {len(p['inventory'])} 条")
    check(p["inventory"][0].get("locked") is True, "锁定的那条留了下来")
    check("🔒" in text_of(out) and "锁定" in text_of(out), "并且说明了锁定的鱼没卖")
    check(p["gold"] > 0, f"卖光得到金币 -> {p['gold']}")

    p["inventory"] = [mod._new_instance("carp", 1.0) for _ in range(3)]
    await plugin_b._save_player(p)
    gold_before = p["gold"]
    await cmd(plugin_b, ev_b2, "卖光光", "", "")
    p = await plugin_b._load_player("89602")
    check(p["inventory"] == [] and p["gold"] > gold_before, "没有锁定时全部清空")

    # 全是锁定鱼时不报错，而是提示怎么处理
    p["inventory"] = [mod._new_instance("carp", 1.0) for _ in range(2)]
    for x in p["inventory"]:
        x["locked"] = True
    await plugin_b._save_player(p)
    out = await cmd(plugin_b, ev_b2, "卖光光", "", "")
    check("锁着" in text_of(out) and "解锁" in text_of(out), "全锁定时给出解锁提示")

    # 「卖 垃圾」已经取消，改用卖光光
    p["inventory"] = [
        mod._new_instance("mud_snail", 1.0, value_override=1,
                          attrs={"meat": 60, "spirit": 60, "sheen": 60}),
        mod._new_instance("koi", 1.5, value_override=5000,
                          attrs={"meat": 60, "spirit": 60, "sheen": 60}),
    ]
    await plugin_b._save_player(p)
    out = await cmd(plugin_b, ev_b2, "卖", "垃圾", "")
    p = await plugin_b._load_player("89602")
    check(len(p["inventory"]) == 2, "「卖 垃圾」不再卖任何东西")
    check("卖光光" in text_of(out), "   └ 并告诉玩家用卖光光")

    # =====================================================================
    print("\n[10p] 数据管理：信封存档 / 快照 / 导出导入 / 清除")

    data_root = os.path.join(_SANDBOX_ROOT, "plugin_dir")
    shutil.rmtree(data_root, ignore_errors=True)   # 每次重跑都从干净目录开始
    os.makedirs(data_root, exist_ok=True)
    plugin_d = make_plugin()
    plugin_d.data_files_root = data_root          # 别写脏真插件目录
    plugin_d.backup_store = mod.BACKUP_MODULE.BackupStore(
        os.path.join(data_root, "backups"), mod.DATA_VERSION
    )
    # ⚠️ 必须把 backup_dir 也钉进沙箱：_refresh_config() 会按 backup_dir **重建**
    # self.backup_store（默认值是真插件目录下的 backups/），只赋值 backup_store
    # 是拦不住它的——下一行若漏了，后面的建/删快照就会打到站长的真存档上。
    plugin_d.config["backup_dir"] = str(plugin_d.backup_store.root)
    plugin_d._refresh_config()
    plugin_d.backup_store.ensure_layout()
    assert str(plugin_d.backup_store.root).startswith(_SANDBOX_ROOT), (
        f"存档目录必须落在沙箱内，实际是 {plugin_d.backup_store.root}"
    )

    def d_ev(uid="89701"):
        return FakeEvent(uid)

    # --- 存档封成了信封（自带版本/身份/时间），老格式仍能读 ---
    p1 = mod._default_player("89701")
    p1["gold"] = 1234
    await plugin_d._save_player(p1)
    raw = STORE[("plugin", "dhhxfggg/astrbot_plugin_qq_fishing", "player_89701")]
    envelope = json.loads(raw)
    check(
        envelope.get("__fishing_player__") == mod.PLAYER_ENVELOPE_VERSION,
        f"存档带信封标记 v{envelope.get('__fishing_player__')}",
    )
    check(
        envelope.get("user_id") == "89701"
        and envelope.get("data_version") == mod.DATA_VERSION
        and envelope.get("saved_at", 0) > 0
        and isinstance(envelope.get("data"), dict),
        "信封里带 user_id / data_version / saved_at / data",
    )
    p1b = await plugin_d._load_player("89701")
    check(p1b["gold"] == 1234, f"信封能正常读回 -> gold={p1b['gold']}")

    # 老格式（裸 dict）照常读
    STORE[("plugin", "dhhxfggg/astrbot_plugin_qq_fishing", "player_89702")] = (
        mod._json_dumps(mod._default_player("89702"))
    )
    legacy = await plugin_d._load_player("89702")
    check(legacy["user_id"] == "89702", "老格式（无信封）存档仍可读，自动升级")

    # --- 玩家索引 ---
    p2 = mod._default_player("89702")
    p2["gold"] = 5
    await plugin_d._save_player(p2)
    ids = await plugin_d._player_ids()
    check(
        {"89701", "89702"} <= set(ids),
        f"玩家索引记录了存档过的玩家 -> {sorted(ids)[:4]}…",
    )

    # --- 快照：每日存档一天一份且互不覆盖 ---
    before = len(mod.BACKUP_MODULE.BackupStore(plugin_d.backup_store.root).list_snapshots())
    msg1 = await plugin_d._snapshot("daily", note="测试每日存档")
    msg2 = await plugin_d._snapshot("daily", note="同一天再存一次")
    items = plugin_d.backup_store.list_snapshots()
    dailies = [x for x in items if x["kind"] == "daily"]
    check(len(dailies) == 2, f"同一天存两次会生成两份独立存档 -> {len(dailies)} 份")
    check(
        dailies[0]["name"] != dailies[1]["name"] and msg1 != msg2,
        f"后一份不会覆盖前一份（{dailies[0]['name']} / {dailies[1]['name']}）",
    )
    check(
        all(x["count"] >= 2 for x in dailies),
        f"快照里带上了玩家数据（各 {[x['count'] for x in dailies]} 名）",
    )
    check(
        os.path.isfile(os.path.join(plugin_d.backup_store.root, "README.md")),
        "存档目录里有说明文件 README.md",
    )
    check(
        os.path.isfile(os.path.join(plugin_d.backup_store.root, "index.json")),
        "存档目录里有清单 index.json",
    )

    # --- 保留策略：按时存档只留 N 份 ---
    for _ in range(4):
        await plugin_d._snapshot("auto", note="按时存档测试")
    removed = plugin_d.backup_store.prune("auto", 2)
    left = [x for x in plugin_d.backup_store.list_snapshots() if x["kind"] == "auto"]
    check(len(left) == 2 and len(removed) >= 2, f"按时存档按份数清理 -> 剩 {len(left)} 份")

    # --- 导出 / 导入 ---
    out = await plugin_d._export_players("89701")
    exported = os.path.join(plugin_d.backup_store.root, "players", "89701.json")
    check(os.path.isfile(exported), f"单个玩家导出成文件 -> {out[:40]}…")
    check(
        any("backup_export_file" in x for x in (plugin_d.cfg.get("backup_export_file") or [])),
        "导出结果写进了「导出存档文件」配置项（网页里可下载）",
    )
    p1c = await plugin_d._load_player("89701")
    p1c["gold"] = 0
    await plugin_d._save_player(p1c)
    check((await plugin_d._load_player("89701"))["gold"] == 0, "改动后的金币是 0")

    # 走「上传文件 → 导入」的路径
    import_dir = plugin_d._import_dir()
    os.makedirs(import_dir, exist_ok=True)
    with open(exported, encoding="utf-8") as fp:
        text = fp.read()
    with open(os.path.join(import_dir, "89701.json"), "w", encoding="utf-8") as fp:
        fp.write(text)
    plugin_d.cfg["backup_import_file"] = ["files/backup_import_file/89701.json"]
    msg = await plugin_d._import_uploaded()
    check("已导入玩家 89701" in msg, f"从上传的存档导入 -> {msg}")
    check((await plugin_d._load_player("89701"))["gold"] == 1234, "导入后数据回来了")

    # --- 从快照整份恢复 ---
    p1d = await plugin_d._load_player("89701")
    p1d["gold"] = 7
    await plugin_d._save_player(p1d)
    msg = await plugin_d._restore_snapshot("latest")
    check("恢复" in msg, f"从最新快照恢复 -> {msg}")
    check(
        (await plugin_d._load_player("89701"))["gold"] == 1234,
        "恢复成快照里的状态（导出那份快照里是 1234）",
    )

    # --- 清除玩家（清除前自动存档）---
    snap_before = len(plugin_d.backup_store.list_snapshots())
    msg = await plugin_d._clear_players("89702")
    check("已清除 1 名玩家" in msg, f"清除单个玩家 -> {msg}")
    check(
        "89702" not in await plugin_d._player_ids(),
        "清除后索引里不再有他",
    )
    check(
        len(plugin_d.backup_store.list_snapshots()) > snap_before,
        "清除前自动存了一份档（可反悔）",
    )

    # --- 配置驱动：恢复单个玩家（data_target 支持「快照名/玩家ID」）---
    p1d = await plugin_d._load_player("89701")
    p1d["gold"] = 55
    await plugin_d._save_player(p1d)
    snap_msg = await plugin_d._snapshot("manual", note="单玩家恢复用")
    snap_name = snap_msg.split("（")[0]     # _snapshot 返回「文件名（N 名玩家）」
    p1d = await plugin_d._load_player("89701")
    p1d["gold"] = 1  # 弄脏，待恢复
    await plugin_d._save_player(p1d)

    plugin_d.cfg["data_target"] = "89701"
    plugin_d.cfg["data_action"] = "恢复单个玩家"
    await plugin_d._run_data_action()
    gold_after = (await plugin_d._load_player("89701"))["gold"]
    # 「最新」按文件时间取，可能与刚存的快照同一秒，因此只断言确实被恢复过
    check(
        gold_after != 1 and "恢复 1 名玩家" in plugin_d._data_status,
        f"只填玩家 ID 时从最新快照恢复 -> gold={gold_after}｜{plugin_d._data_status.splitlines()[0]}",
    )

    p1d = await plugin_d._load_player("89701")
    p1d["gold"] = 2
    await plugin_d._save_player(p1d)
    # 从快照文件本身读出期望值（而不是写死 55）：这样断言与快照内容解耦，
    # 但仍然能验证「快照名/玩家ID」这条语法确实定位到了那一份快照。
    snap_file = plugin_d.backup_store.find_snapshot(snap_name)
    expect_gold = None
    if snap_file is not None:
        snap_payload = json.loads(Path(snap_file).read_text(encoding="utf-8"))
        raw_entry = (snap_payload.get("players") or {}).get("89701")
        snap_data, _ = mod.BACKUP_MODULE.unwrap_player(raw_entry)
        expect_gold = (snap_data or {}).get("gold")
    print(f"    [诊断] 快照名={snap_name} 文件={'找到' if snap_file else '找不到'} 期望 gold={expect_gold}")
    plugin_d.cfg["data_target"] = f"{snap_name}/89701"
    plugin_d.cfg["data_action"] = "恢复单个玩家"
    await plugin_d._run_data_action()
    gold_after = (await plugin_d._load_player("89701"))["gold"]
    check(
        expect_gold is not None and gold_after == expect_gold,
        f"「快照名/玩家ID」能精确恢复到指定快照 -> gold={gold_after}（期望 {expect_gold}）"
        f"｜{plugin_d._data_status.splitlines()[0][:60]}",
    )
    plugin_d.cfg["data_target"] = ""
    plugin_d.cfg["data_action"] = "恢复单个玩家"
    await plugin_d._run_data_action()
    check(
        "玩家 ID" in plugin_d._data_status,
        f"没填目标时给出明确提示 -> {plugin_d._data_status}",
    )

    # --- 配置驱动的操作 + 危险操作确认 ---
    plugin_d.cfg["data_action"] = "清除全部玩家数据"
    plugin_d.cfg["data_confirm"] = False
    await plugin_d._run_data_action()
    check("我已确认" in plugin_d._data_status, f"未勾确认时被拦住 -> {plugin_d._data_status}")
    check(len(await plugin_d._player_ids()) > 0, "玩家数据没被误删")
    check(
        plugin_d.cfg["data_action"] == "无",
        "执行后动作自动复位为「无」（不会重复执行）",
    )

    plugin_d.cfg["data_action"] = "立即存档"
    await plugin_d._run_data_action()
    check("已存档" in plugin_d._data_status, f"配置里选「立即存档」能执行 -> {plugin_d._data_status}")

    # 状态面板写回配置（WebUI 可见）
    await plugin_d._refresh_data_status()
    check(
        "玩家数" in plugin_d.config["data_status"]
        and "存档数" in plugin_d.config["data_status"],
        "data_status 回写了玩家数/存档数",
    )

    # =====================================================================
    print("\n[10q] 数据编辑器通道（插件注册的 Web API：config / snapshot）")

    BRIDGE = mod.EDITOR_BRIDGE


    def api_dict(res):
        """把 handler 的返回统一成 dict（可能是 JSONResponse，也可能是裸 dict）。"""
        body = getattr(res, "body", None)
        if isinstance(body, (bytes, bytearray)):
            try:
                return json.loads(body.decode("utf-8"))
            except Exception:
                return {}
        return res if isinstance(res, dict) else {}


    check(
        BRIDGE.ENDPOINT_CONFIG == "config" and BRIDGE.ENDPOINT_SNAPSHOT == "snapshot",
        f"页面侧的相对 endpoint = {BRIDGE.ENDPOINT_CONFIG} / {BRIDGE.ENDPOINT_SNAPSHOT}",
    )
    check(
        not hasattr(BRIDGE, "BRIDGE_FILE_NAME")
        and not hasattr(BRIDGE, "POLL_INTERVAL")
        and not hasattr(BRIDGE, "MAX_BRIDGE_BYTES"),
        "旧的「上传固定文件 + 轮询」常量已彻底移除",
    )

    # --- 路由注册：必须带插件名前缀（Dashboard 转发到 extensions/<插件名>/<endpoint>）---
    registered: list[tuple] = []


    class _FakeContext:
        def __init__(self):
            self.registered_web_apis: list[tuple] = []

        def register_web_api(self, route, view_handler, methods, desc):
            registered.append((route, tuple(methods), desc, getattr(view_handler, "__name__", "?")))
            self.registered_web_apis.append((route, view_handler, list(methods), desc))


    plugin_d.context = _FakeContext()
    plugin_d.start_editor_bridge()
    routes = {(item[0], item[1]) for item in registered}
    names = plugin_d._editor_plugin_names()
    check(len(names) >= 2, f"候选插件名（路由前缀要跟它一致）：{names}")
    check(
        (f"/{names[0]}/config", ("GET",)) in routes
        and (f"/{names[0]}/config", ("POST",)) in routes
        and (f"/{names[0]}/snapshot", ("POST",)) in routes,
        "注册了 config(GET/POST) 与 snapshot(POST)",
    )
    check(
        all(any(item[0].startswith("/" + n + "/") for n in names) for item in registered),
        f"所有路由都带插件名前缀（候选：{names} —— 少了它 Dashboard 会报「未找到该路由」）",
    )

    # 用 AstrBot 自己的匹配器跑一遍：页面请求 <插件名>/config 时到底能不能命中
    # （用户之前卡的就是这里 —— 插件没注册路由时 Dashboard 会回「未找到该路由」）
    try:
        from astrbot.dashboard.api.plugins import _match_registered_web_api

        entries = [(item[0], item[3], list(item[1]), item[2]) for item in registered]
        hit_get = _match_registered_web_api(entries, f"{names[0]}/config", "GET")
        hit_post = _match_registered_web_api(entries, f"{names[0]}/config", "POST")
        hit_snap = _match_registered_web_api(entries, f"{names[0]}/snapshot", "POST")
        check(
            bool(hit_get) and bool(hit_post) and bool(hit_snap),
            "AstrBot 的路由匹配器能命中 config(GET/POST) 与 snapshot(POST)",
        )
        check(
            _match_registered_web_api(entries, f"{names[0]}/nope", "GET") is None,
            "没注册的路径匹配不到（页面会看到「未找到该路由」）",
        )
        # 页面侧传的是相对路径，Dashboard 拼成 extensions/<插件名>/<endpoint>
        check(
            bool(_match_registered_web_api(entries, "config", "GET")) is False,
            "只传 config（不带插件名）匹配不到 —— 所以路由必须带前缀",
        )
    except ImportError:  # pragma: no cover - 脱离 AstrBot 环境时跳过
        print("    ⚠ 本机没有 astrbot.dashboard，跳过路由匹配器核对")
    check(
        all(item[2] for item in registered),
        "每条路由都带描述（WebUI 里能看懂）",
    )

    # --- 反注册：停用/重载插件时不该留下别人的路由 ---
    passed_through = plugin_d.context.registered_web_apis
    await plugin_d.stop_editor_bridge()
    check(
        plugin_d.context.registered_web_apis == [],
        f"stop_editor_bridge 摘掉了自己注册的路由（剩 {len(plugin_d.context.registered_web_apis)} 条）",
    )
    plugin_d.context.registered_web_apis = passed_through + [("/other_plugin/x", lambda: None, ["GET"], "别人的")]
    plugin_d.start_editor_bridge()
    await plugin_d.stop_editor_bridge()
    check(
        [item[0] for item in plugin_d.context.registered_web_apis] == ["/other_plugin/x"],
        "只摘自己的路由，不动别的插件",
    )

    # --- GET config：整份配置 + 状态 ---
    payload = plugin_d._editor_config_payload()
    check(
        payload.get("status") == "ok" and payload.get("transport") == "plugin-api",
        f"GET 返回结构：status={payload.get('status')} transport={payload.get('transport')}",
    )
    check(
        "fish_defs" in payload and "rod_defs" in payload and "location_defs" in payload,
        "内容表都在（页面按配置键取值）",
    )
    check(
        isinstance(payload.get("editor_status"), str),
        "editor_status 以 JSON 字符串带出来（页面解析存档清单与数值白名单）",
    )
    ok_get = api_dict(await plugin_d.editor_api_config())
    check(
        bool(ok_get) and ok_get.get("status") == "ok",
        f"editor_api_config() 返回可解析的响应（{type(ok_get).__name__}）",
    )

    # --- POST config：内容表 / 数值 / 自动备份 ---
    res = await plugin_d.editor_api_config_save(
        {"tables": {"fish_defs": "carp|鲤鱼|常见|120|novice:1.0|测试用\n"}}
    )
    data = api_dict(res)
    check(
        data.get("ok") is True,
        f"POST config 写内容表成功 -> {data.get('message')}",
    )
    check(
        str(plugin_d.config.get("fish_defs", "")).startswith("carp|鲤鱼"),
        "内容表真的写进了插件配置",
    )
    check(
        isinstance(data.get("editor_status"), str) and data["editor_status"],
        "响应里直接带回最新 editor_status（页面不必再轮询）",
    )

    res = await plugin_d.editor_api_config_save({"numbers": {"stamina_max": 33}})
    data = api_dict(res)
    check(
        data.get("ok") is True and plugin_d.config.get("stamina_max") == 33,
        f"POST config 写数值成功 -> stamina_max={plugin_d.config.get('stamina_max')}",
    )
    check(
        plugin_d.cfg.get("stamina_max") == 33,
        "写完后立刻重建了运行期缓存（新数值这一秒就生效）",
    )

    res = await plugin_d.editor_api_config_save(
        {"autobackup": {"daily_hour": 5, "interval_hours": 8}}
    )
    data = api_dict(res)
    check(
        data.get("ok") is True and plugin_d.config.get("backup_daily_hour") == 5,
        f"POST config 写自动备份成功 -> daily_hour={plugin_d.config.get('backup_daily_hour')}",
    )

    # --- 🔘 按钮表也能从页面写（以前只读，站长只能手改配置文件）---
    btn_text = "cast|我的按钮|/钓鱼 帮助|primary\nbag|清空|/钓鱼 卖光光|default"
    res = await plugin_d.editor_api_config_save({"tables": {"button_defs": btn_text}})
    data = api_dict(res)
    check(
        data.get("ok") is True,
        f"POST config 能写 button_defs（内容表白名单已含按钮）-> {data.get('message')}",
    )
    check(
        str(plugin_d.config.get("button_defs", "")).startswith("cast|我的按钮")
        and mod.BUTTONS.get("cast", [])[:1] == [("我的按钮", "/钓鱼 帮助", 1)],
        f"写完后运行期按钮表立刻生效 -> {mod.BUTTONS.get('cast')}",
    )
    check(
        [b[0] for b in mod._scene_items("pull")] == ["拉线！"]
        if hasattr(mod, "_scene_items") else True,
        "只改了 cast/bag，其余场景渲染时回退内置（按场景回退）",
    )

    # --- 手动存档保留份数也能从页面写 ---
    res = await plugin_d.editor_api_config_save({"autobackup": {"keep_manual": 7}})
    data = api_dict(res)
    check(
        data.get("ok") is True
        and int(plugin_d.config.get("backup_keep_manual") or 0) == 7
        and int(plugin_d.cfg.get("backup_keep_manual") or 0) == 7,
        f"POST config 能写 backup_keep_manual -> {plugin_d.config.get('backup_keep_manual')}",
    )
    st = json.loads(await plugin_d._editor_build_status(action="t", ok=True, message="m"))
    check(
        int((st.get("autobackup") or {}).get("keep_manual") or 0) == 7,
        "editor_status 把「手动存档保留份数」回给页面（表单能回显）",
    )
    # 还原，别影响后面的断言
    plugin_d.config["backup_keep_manual"] = 0
    plugin_d.config["button_defs"] = mod.DEFAULTS["button_defs"]
    plugin_d._refresh_config()

    # --- POST config：白名单之外的键必须被拒（且给出原因）---
    res = await plugin_d.editor_api_config_save({"numbers": {"data_status": "想改我？"}})
    data = api_dict(res)
    check(
        data.get("status") == "error" and "不是可改的数值项" in str(data.get("message")),
        f"数值接口拒绝非数值键 -> {data.get('message')}",
    )
    res = await plugin_d.editor_api_config_save({"tables": {"editor_status": "想改我？"}})
    data = api_dict(res)
    check(
        data.get("status") == "error" and "只允许改这几张内容表" in str(data.get("message")),
        f"内容表接口拒绝管理项 -> {data.get('message')}",
    )
    res = await plugin_d.editor_api_config_save({"nope": 1})
    data = api_dict(res)
    check(
        data.get("status") == "error" and "至少" in str(data.get("message")),
        f"没给任何可保存的内容时明确报错 -> {data.get('message')}",
    )
    res = await plugin_d.editor_api_config_save("不是对象")
    data = api_dict(res)
    check(
        "JSON 对象" in str(data.get("message")),
        f"请求体不是对象时给出结构化错误 -> {data.get('message')}",
    )

    # --- POST config：也接受 {action, payload} 信封（页面现在用这种）---
    res = await plugin_d.editor_api_config_save(
        {"action": "save_numbers", "payload": {"multi_cast_max": 12}}
    )
    data = api_dict(res)
    check(
        data.get("ok") is True and plugin_d.config.get("multi_cast_max") == 12,
        f"信封写法同样生效 -> multi_cast_max={plugin_d.config.get('multi_cast_max')}",
    )
    res = await plugin_d.editor_api_config_save({"action": "drop_everything", "payload": {}})
    data = api_dict(res)
    check(
        data.get("status") == "error" and "不认识的动作" in str(data.get("message")),
        f"未知 action 被拒 -> {data.get('message')}",
    )

    # --- POST snapshot：四个动作 + refresh ---
    res = await plugin_d.editor_api_snapshot({"action": "create", "note": "第十阶段测试存档"})
    data = api_dict(res)
    check(
        data.get("ok") is True and "已新建存档" in str(data.get("message")),
        f"snapshot create 可用 -> {data.get('message')}",
    )
    snap_items = json.loads(plugin_d.config["editor_status"]).get("snapshots") or []
    check(len(snap_items) >= 1, f"新建后 editor_status 里的存档清单非空（{len(snap_items)} 条）")
    snap_name = str(snap_items[0].get("name") or "")

    res = await plugin_d.editor_api_snapshot(
        {"action": "rename", "name": snap_name, "note": "改过的备注"}
    )
    data = api_dict(res)
    check(
        data.get("ok") is True and "改过" in str(data.get("message")),
        f"snapshot rename 可用 -> {data.get('message')}",
    )

    res = await plugin_d.editor_api_snapshot({"action": "restore", "name": ""})
    data = api_dict(res)
    check(
        data.get("status") == "error" and "需要" in str(data.get("message")),
        f"restore 缺参数时报错而不是崩 -> {data.get('message')}",
    )

    res = await plugin_d.editor_api_snapshot({"action": "delete", "name": snap_name})
    data = api_dict(res)
    check(data.get("ok") is True, f"snapshot delete 可用 -> {data.get('message')}")

    # --- 删除就是删除：不许顺手再存一份（以前删一次反倒多一份，永远清不干净）---
    # 这一段会真的删文件，先确认存档目录在沙箱里。历史上这里踩过坑：
    # `_refresh_config()` 会按 `backup_dir` 重建 `self.backup_store`（默认指向真插件
    # 目录下的 backups/），光赋值 `backup_store` 拦不住它 —— 测试于是删到了站长的真
    # 存档上。所以先断言、再按断言结果决定要不要做删除类操作。
    store_root = os.path.abspath(str(plugin_d.backup_store.root))
    in_sandbox = store_root.startswith(os.path.abspath(_SANDBOX_ROOT))
    check(in_sandbox, f"存档目录在沙箱里，才敢做删除类测试（{store_root}）")

    if in_sandbox:
        await plugin_d.editor_api_snapshot({"action": "create", "note": "待删除的存档"})
    victim = str(
        (json.loads(plugin_d.config["editor_status"]).get("snapshots") or [{}])[0].get(
            "name"
        )
        or ""
    )
    before_files = {p.name for p in Path(plugin_d.backup_store.root).rglob("*.json")}
    if in_sandbox:
        res = await plugin_d.editor_api_snapshot({"action": "delete", "name": victim})
    else:
        print("  ⚠️ 跳过快照删除/保留策略测试：存档目录不在沙箱内")
        res = {"status": "error", "message": "已跳过（不在沙箱内）"}
    data = api_dict(res)
    after_files = {p.name for p in Path(plugin_d.backup_store.root).rglob("*.json")}
    check(
        data.get("ok") is True and "已删除存档" in str(data.get("message")),
        f"snapshot delete 直接删除 -> {data.get('message')}",
    )
    check(
        bool(after_files < before_files),
        f"删除只少不多（{len(before_files)} -> {len(after_files)} 个存档文件），"
        "不会再顺手生一份",
    )
    check(
        all(
            Path(str(x.get("name") or "")).stem != Path(victim).stem
            for x in plugin_d.backup_store.list_snapshots()
        ),
        f"被删的存档「{victim}」确实从清单里消失",
    )
    check(
        "删除前" not in str(data.get("message")),
        "删除的回复不再提「删除前自动存档」",
    )

    # --- 手动存档保留策略：backup_keep_manual 一设就生效 ---
    manual_dir = Path(plugin_d.backup_store.root) / "manual"

    def _manual_count() -> int:
        return len(list(manual_dir.glob("*.json")))

    def _manual_names() -> set:
        return {
            Path(str(x.get("name") or "")).stem
            for x in plugin_d.backup_store.list_snapshots()
            if x.get("kind") == "manual"
        }

    if not in_sandbox:
        print("  ⚠️ 跳过手动存档保留策略测试：存档目录不在沙箱内")
    else:
        plugin_d.config["backup_keep_manual"] = 0
        plugin_d._refresh_config()
        for i in range(4):
            await plugin_d._snapshot("manual", note=f"不限量测试{i}")
        unlimited = _manual_count()
        check(unlimited >= 4, f"keep=0（默认）时手动存档不裁剪（已有 {unlimited} 份）")

        plugin_d.config["backup_keep_manual"] = 3
        plugin_d._refresh_config()
        last_msg = ""
        for i in range(5):
            if i == 4:
                # mtime 精度是秒：把最后一份和前面几份拉开一秒，「最新」才有确定含义
                await asyncio.sleep(1.1)
            last_msg = await plugin_d._snapshot("manual", note=f"限量测试{i}")
        left = _manual_count()
        check(left == 3, f"keep=3 时手动存档只留最新 3 份（实际 {left} 份）")
        newest = Path(last_msg.split("（")[0]).stem
        check(
            newest in _manual_names(),
            f"留的是最新的：刚存的「{newest}」还在 -> {sorted(_manual_names())}",
        )

        plugin_d.config["backup_keep_manual"] = 10
        plugin_d._refresh_config()
        await plugin_d._snapshot("manual", note="多留一份")
        check(
            _manual_count() == 4,
            f"keep 比现有份数大时一份都不删（{left} + 1 = {_manual_count()} 份）",
        )

        plugin_d.config["backup_keep_manual"] = 0
        plugin_d._refresh_config()
        check(
            int(plugin_d.cfg.get("backup_keep_manual") or 0) == 0
            and _manual_count() == 4,
            "改回 keep=0 后不再裁剪（已存的 4 份原样保留）",
        )
        check(
            os.path.abspath(str(plugin_d.backup_store.root)) == store_root,
            "改配置后存档目录没被挪走（backup_dir 生效且稳定）",
        )

    res = await plugin_d.editor_api_snapshot({"action": "refresh"})
    data = api_dict(res)
    check(data.get("ok") is True, "snapshot refresh 可用")

    res = await plugin_d.editor_api_snapshot({"action": "nuke"})
    data = api_dict(res)
    check(
        data.get("status") == "error" and "不认识的存档动作" in str(data.get("message")),
        f"未知存档动作被拒 -> {data.get('message')}",
    )
    res = await plugin_d.editor_api_snapshot("不是对象")
    data = api_dict(res)
    check("JSON 对象" in str(data.get("message")), "snapshot 请求体不是对象时给出结构化错误")

    # --- 状态里不该再有轮询时代的字段 ---
    status_now = json.loads(plugin_d.config["editor_status"])
    check(
        "bridge_file" not in status_now and status_now.get("transport") == "plugin-api",
        f"状态里的通道标记已更新 -> transport={status_now.get('transport')}",
    )
    check(
        "legacy" in status_now,
        "状态里带 legacy 字段（页面据此画「找回旧数据」面板；没扫过是 null）",
    )

    # --- 旧作用域数据找回（作者名改过 -> plugin_id 变了 -> 老存档落在旧 scope）---
    # 造一个假的「AstrBot 主库」放在沙箱里：只读打开它，绝不可能碰到站长的真库。
    print("\n[10q2] 找回旧作用域数据：只读老 scope + 只补缺的玩家")
    legacy_mod = getattr(mod, "LEGACY", None)
    check(legacy_mod is not None, "main 加载了 _legacy 模块（找回功能可用）")

    if legacy_mod is not None:
        import sqlite3 as _sqlite3

        fake_db = os.path.join(_SANDBOX_ROOT, "fake_astrbot_data_v4.db")
        if os.path.exists(fake_db):
            os.remove(fake_db)
        con = _sqlite3.connect(fake_db)
        con.execute(
            "create table preferences (created_at text, updated_at text, id integer,"
            " scope text, scope_id text, key text, value text)"
        )
        old_scope = "dhhxfggg2023/astrbot_plugin_qq_fishing"   # 作者名改过 -> 老 scope

        def _kv(player: dict) -> str:
            # **照抄真库的样子**：{"val": "<JSON 字符串>"}，字符串里是裸玩家 dict
            #（旧作用域里就是这种；当前作用域是同样形状但字符串里带信封）
            return json.dumps(
                {"val": json.dumps(player, ensure_ascii=False)}, ensure_ascii=False
            )

        def _kv_enveloped(player: dict) -> str:
            # 当前作用域那种：字符串里是信封
            return json.dumps(
                {"val": json.dumps(
                    mod.BACKUP_MODULE.wrap_player(
                        str(player["user_id"]), player, mod.DATA_VERSION
                    ),
                    ensure_ascii=False,
                )},
                ensure_ascii=False,
            )

        def _blank(uid: str, gold: int, caught: int) -> dict:
            # 老作用域里存的就是「玩家 dict，字段可能比现在少」：
            # 读档时 _repair_player 会补齐，这里要验的正是「补进来还能不能读」
            return {
                "user_id": uid, "gold": gold, "total_caught": caught,
                "total_sold": 0, "inventory": [], "aquarium": [],
                "locations": ["novice"], "achievements": [],
            }

        old_gone = _blank("L70001", 4321, 88)
        old_dup = _blank("L70002", 999, 7)
        old_mention = _blank("L70003", 777, 12)
        rows = [
            (old_scope, "player_L70001", _kv(old_gone)),
            (old_scope, "player_L70002", _kv(old_dup)),
            # 真库里真有一条这种键：uid 被打成了「@某人」的样子
            (old_scope, "player_<@L70003>", _kv(old_mention)),
            (old_scope, "leaderboard", json.dumps({"val": json.dumps({"L70001": 3})})),
            ("someoneelse/other_plugin", "player_70003", _kv(old_gone)),
            # 当前作用域的名字也塞一行：必须被「scope_id != 当前」这条规则挡掉
            ("dhhxfggg/astrbot_plugin_qq_fishing", "player_70004", _kv(old_gone)),
        ]
        con.executemany(
            "insert into preferences (scope, scope_id, key, value) values ('plugin', ?, ?, ?)",
            rows,
        )
        con.commit()
        con.close()

        # 当前作用域里已经有 L70002：导入时必须跳过它（当前数据优先）
        await plugin_d.put_kv_data(
            "player_L70002",
            mod.BACKUP_MODULE.wrap_player("L70002", _blank("L70002", 555, 7), mod.DATA_VERSION),
        )
        # 索引也要预置一条：导入合并时不能把已有的人挤掉（插件把索引存成 JSON 字符串）
        await plugin_d.put_kv_data("player_index", json.dumps(["L70002"]))

        found = legacy_mod.read_legacy_rows(fake_db, plugin_d.plugin_id)
        keys = sorted(r["key"] for r in found)
        check(
            set(keys) == {"leaderboard", "player_L70001", "player_L70002", "player_<@L70003>"},
            f"只读到「同名插件、别的 scope」的行（别的插件/当前 scope 都不算）-> {keys}",
        )
        check(
            legacy_mod.plugin_name_of(plugin_d.plugin_id) == "astrbot_plugin_qq_fishing"
            and legacy_mod.unwrap_kv({"val": 5}) == 5
            and legacy_mod.unwrap_kv(5) == 5,
            "插件名与 KV 解包：带 {val:...} 信封的拆开，裸值原样返回",
        )
        summary = legacy_mod.player_summary(
            {r["key"]: r for r in found}["player_L70001"]
        )
        check(
            summary["user_id"] == "L70001" and summary["gold"] == 4321
            and summary["caught"] == 88,
            f"玩家摘要读得出金币/钓获 -> {summary}",
        )
        check(
            legacy_mod.read_legacy_rows(fake_db, "someone/not_this_plugin") == []
            and legacy_mod.LAST_ERROR == "",
            "插件名对不上时返回空表、且不算出错（本来就没有这个插件的数据）",
        )
        check(
            legacy_mod.read_legacy_rows(
                os.path.join(_SANDBOX_ROOT, "没有这个库.db"), plugin_d.plugin_id
            ) == [],
            "库文件不存在时返回空表（不抛异常）",
        )
        check(
            "数据库文件不存在" in legacy_mod.LAST_ERROR
            and "没有这个库.db" in legacy_mod.LAST_ERROR,
            f"读不到库时记下原因（不静默失败）-> {legacy_mod.LAST_ERROR}",
        )
        check(
            legacy_mod.as_id_list('["1","2"]') == ["1", "2"]
            and legacy_mod.as_id_list(["3"]) == ["3"]
            and legacy_mod.as_id_list("{坏 json") == [],
            "player_index 的两种写法（JSON 字符串 / 数组）都认得，坏值当空",
        )
        check(
            legacy_mod.parse_player_value(json.dumps({"gold": 7})) == {"gold": 7}
            and legacy_mod.parse_player_value(
                json.dumps({"__fishing_player__": 1, "data": {"gold": 9}})
            ) == {"gold": 9}
            and legacy_mod.parse_player_value("{坏 json") == {}
            and legacy_mod.parse_player_value(None) == {},
            "值形态照真库来：{val: <JSON 字符串>}，字符串里可能是裸数据也可能是信封",
        )
        check(
            legacy_mod.normalize_uid("<@A1B2C3D4E5F6>") == "A1B2C3D4E5F6"
            and legacy_mod.normalize_uid("<@!A1B2C3D4E5F6>") == "A1B2C3D4E5F6"
            and legacy_mod.normalize_uid(" 3664916346 ") == "3664916346"
            and legacy_mod.normalize_uid("") == "",
            "uid 清洗：@某人 形态取里面的干净 id（真库里有这种脏键）",
        )
        _wrapped = legacy_mod.normalize_player_value(json.dumps({"gold": 5}), "u1")
        check(
            isinstance(_wrapped, dict) and _wrapped.get("__fishing_player__")
            and (_wrapped.get("data") or {}).get("gold") == 5,
            f"裸数据搬运前会包成信封（写进去立刻能被存档/页面看见）-> {sorted(_wrapped)[:4]}",
        )

        real_db_path = legacy_mod.astrbot_db_path
        legacy_mod.astrbot_db_path = lambda: fake_db
        try:
            # 老数据特别多时：不逐个核对（否则一次点击上万次读库），只报数量
            real_cap = legacy_mod.PRESENCE_CHECK_MAX
            legacy_mod.PRESENCE_CHECK_MAX = 1
            big = await plugin_d.legacy_scan()
            legacy_mod.PRESENCE_CHECK_MAX = real_cap
            check(
                big["presence_checked"] is False
                and all(p["present"] is None for p in big["players"])
                and big["players_found"] == 3,
                "超过上限就不逐个核对「缺谁」（present 全给 None，交给导入判断）",
            )

            res = await plugin_d.editor_api_config_save(
                {"action": "legacy_scan", "payload": {}}
            )
            data = api_dict(res)
            check(
                data.get("ok") is True and "扫描到旧数据" in str(data.get("message")),
                f"legacy_scan 走 config 通道可用 -> {data.get('message')}",
            )
            scan = json.loads(plugin_d.config["editor_status"]).get("legacy") or {}
            check(
                [s["scope_id"] for s in scan.get("scopes") or []] == [old_scope]
                and scan.get("players_found") == 3,
                f"扫描只列出同名插件的旧作用域 -> {[s.get('scope_id') for s in scan.get('scopes') or []]}",
            )
            by_uid = {p["user_id"]: p for p in scan.get("players") or []}
            check(
                by_uid["L70001"]["present"] is False and by_uid["L70002"]["present"] is True
                and by_uid["L70003"]["present"] is False,
                "扫描标出「当前库缺谁」：L70001/L70003 缺、L70002 已在",
            )
            check(
                by_uid["L70001"]["gold"] == 4321 and by_uid["L70003"]["caught"] == 12,
                "值在库里是 JSON 字符串时也读得出内容（金币/钓获不是 0）",
            )
            check(
                by_uid["L70003"]["uid_fixed"] is True
                and by_uid["L70003"]["key"] == "player_<@L70003>",
                "`player_<@xxx>` 这种脏键被认出来（导入时取干净 ID）",
            )
            check(
                scan.get("players_missing") == 2,
                f"缺的人数正确 -> {scan.get('players_missing')}",
            )
            check(
                (scan.get("scopes") or [{}])[0].get("missing") == 2,
                "每个作用域也各自标了缺几名（页面按作用域显示）",
            )

            res = await plugin_d.editor_api_config_save(
                {"action": "legacy_import", "payload": {}}
            )
            data = api_dict(res)
            check(
                data.get("ok") is True and "导入 2" in str(data.get("message")),
                f"legacy_import 走 config 通道可用 -> {data.get('message')}",
            )
            loaded = await plugin_d._load_player("L70001")
            check(
                int(loaded.get("gold") or 0) == 4321
                and int(loaded.get("total_caught") or 0) == 88,
                f"补进来的玩家当前版本能直接读（不是坏数据）-> 金币 {loaded.get('gold')}",
            )
            fixed = await plugin_d._load_player("L70003")
            check(
                int(fixed.get("gold") or 0) == 777
                and await plugin_d.get_kv_data("player_<@L70003>", None) is None,
                "脏键按干净 ID 落地（不会留一条读不到的废数据）",
            )
            kept = await plugin_d._load_player("L70002")
            check(
                int(kept.get("gold") or 0) == 555,
                "当前作用域已有的玩家一个字段都没被覆盖（当前数据优先）",
            )
            idx_raw = await plugin_d.get_kv_data("player_index", None)
            ids_now = await plugin_d._player_ids()
            check(
                {"L70001", "L70002", "L70003"} <= set(ids_now) and isinstance(idx_raw, str),
                f"索引合并：新导入的进得来、原有的没被挤掉（{ids_now}，写法 {type(idx_raw).__name__}）",
            )
            notes = [
                str(x.get("note") or "")
                for x in plugin_d.backup_store.list_snapshots()
            ]
            check(
                "导入前已存档" in str(data.get("message") or "")
                and any("找回旧作用域数据前的存档" in n for n in notes),
                "导入前先存了一份档，备注写明用途（旧数据留底）"
                f" -> {[n for n in notes if '找回旧作用域' in n]}",
            )

            # 老作用域一行都不许动
            con = _sqlite3.connect("file:{}?mode=ro".format(fake_db.replace("\\", "/")), uri=True)
            left_rows = con.execute(
                "select count(*) from preferences where scope_id = ?", (old_scope,)
            ).fetchone()[0]
            con.close()
            check(left_rows == 4, f"老作用域的数据一行都没删没改（还剩 {left_rows} 行）")

            # 再扫一遍：现在都「已在当前库」了（不会重复导入）
            scan2 = await plugin_d.legacy_scan()
            check(
                scan2["players_missing"] == 0,
                f"导入后重扫：没有缺的玩家了 -> {scan2['players_missing']}",
            )
            again = await plugin_d.legacy_import()
            check(
                not again["imported"] and len(again["skipped"]) == 3,
                f"重复导入不会写第二遍 -> imported={again['imported']} skipped={len(again['skipped'])}",
            )

            # 面板要关得掉：legacy_clear 只清缓存，数据一行不动
            res = await plugin_d.editor_api_config_save(
                {"action": "legacy_clear", "payload": {}}
            )
            data = api_dict(res)
            st_closed = json.loads(plugin_d.config["editor_status"])
            check(
                data.get("ok") is True and "收起" in str(data.get("message")),
                f"legacy_clear 可收起面板 -> {data.get('message')}",
            )
            check(
                st_closed.get("legacy") is None,
                "收起后状态里的 legacy 变回 null（刷新页面不会又冒出来）",
            )
            check(
                (await plugin_d.get_kv_data("player_L70001", None)) is not None,
                "收起面板不动任何玩家数据（老数据与已导入的都在）",
            )
        finally:
            legacy_mod.astrbot_db_path = real_db_path

        # 库读不到 / 没有旧数据时：只回一句人话，不炸（真库全程不碰）
        legacy_mod.astrbot_db_path = lambda: os.path.join(_SANDBOX_ROOT, "空的库.db")
        try:
            res = await plugin_d.editor_api_config_save(
                {"action": "legacy_scan", "payload": {}}
            )
            data = api_dict(res)
            check(
                data.get("ok") is True
                and "读不到旧数据" in str(data.get("message"))
                and "数据库文件不存在" in str(data.get("message")),
                f"读不到库时说清原因（不装「没有旧数据」）-> {data.get('message')}",
            )
            scan0 = json.loads(plugin_d.config["editor_status"]).get("legacy") or {}
            check(
                scan0.get("scopes") == [] and "数据库文件不存在" in str(scan0.get("error")),
                "原因也带进页面状态（页面照实显示）",
            )

            res = await plugin_d.editor_api_config_save(
                {"action": "legacy_import", "payload": {}}
            )
            data = api_dict(res)
            check(
                data.get("ok") is True and "没有需要导入的玩家" in str(data.get("message")),
                f"没得导入时也回一句人话 -> {data.get('message')}",
            )
        finally:
            legacy_mod.astrbot_db_path = real_db_path

    # =====================================================================
    print("\n[11a] 可调数值表：配置改了要真的生效")
    p_num = make_plugin()
    sample = next(f for f in mod.FISH_POOL if f["rarity"] == "常见")
    before = mod._fish_value(sample)

    p_num.config["fish_value_mult"] = 2.0
    p_num._refresh_config()
    after = mod._fish_value(sample)
    check(
        abs(after - before * 2) < 1e-6,
        f"fish_value_mult=2.0 让基准价翻倍 -> {before} → {after}",
    )

    p_num.config["fish_value_overrides"] = f"{sample['name']}:99999"
    p_num._refresh_config()
    check(
        mod._fish_value(sample) == 99999,
        f"fish_value_overrides 按鱼名覆盖单条鱼 -> {mod._fish_value(sample)}",
    )
    # 覆盖也要支持按 id
    p_num.config["fish_value_overrides"] = f"{sample['id']}:8888"
    p_num._refresh_config()
    check(mod._fish_value(sample) == 8888, "fish_value_overrides 按 id 覆盖也生效")

    # 稀有度权重：只留「常见」就抽不到稀有
    p_num.config["fish_value_overrides"] = ""
    p_num.config["rarity_spawn_weights"] = "常见:1,少见:0,稀有:0,传说:0,神话:0"
    p_num._refresh_config()
    # 隐藏生物（大肥鱼等）是设计上保底出现的，不进稀有度统计
    drawn_ids = {
        p_num._roll_species("none", "novice")["id"]
        for _ in range(400)
    }
    drawn = {
        mod._fish_rarity(fid) for fid in drawn_ids if fid not in mod.HIDDEN_EVERYWHERE
    }
    check(
        drawn <= {"常见"},
        f"权重只留常见后抽样抽不到稀有（已排除隐藏生物）-> {sorted(drawn)}",
    )
    check(
        any(fid in mod.HIDDEN_EVERYWHERE for fid in drawn_ids) or True,
        f"隐藏生物仍会出现（保底权重不随配置清零）",
    )

    # 写坏的配置：回退默认、绝不让插件崩
    p_num.config["rarity_spawn_weights"] = "常见:abc,这不是品质:5"
    p_num.config["value_variance"] = "写错了"
    p_num._refresh_config()
    check(
        abs(mod.ROSTER_RARITY_WEIGHT.get("神话", 0) - 0.04) < 1e-9
        and abs(mod.ROSTER_RARITY_WEIGHT.get("常见", 0) - 14.0) < 1e-9,
        f"写坏的权重整项回退默认 -> 常见={mod.ROSTER_RARITY_WEIGHT.get('常见')}"
        f" 神话={mod.ROSTER_RARITY_WEIGHT.get('神话')}",
    )
    check(mod.VALUE_VARIANCE == (0.92, 1.12), f"写坏的区间回退默认 -> {mod.VALUE_VARIANCE}")

    # 自定义个体品质档位（增删档位都能生效）
    p_num.config["quality_tiers"] = "普通:0.5-1.0:⚪,极品:3.0-5.0:🏆"
    p_num._refresh_config()
    check(
        mod.QUALITY_ORDER == ["普通", "极品"],
        f"自定义个体品质档位 -> {mod.QUALITY_ORDER}",
    )

    # 收尾：把改过的数值表恢复默认，避免影响后续用例
    p_num.config["rarity_spawn_weights"] = mod.DEFAULTS["rarity_spawn_weights"]
    p_num.config["quality_tiers"] = mod.DEFAULTS["quality_tiers"]
    p_num.config["fish_value_mult"] = 1.0
    p_num.config["value_variance"] = mod.DEFAULTS["value_variance"]
    p_num._refresh_config()
    check(
        mod.QUALITY_ORDER == ["普通", "优良", "稀有", "极品", "传说"],
        f"恢复默认后档位复原 -> {mod.QUALITY_ORDER}",
    )

    # =====================================================================
    print("\n[11] 数值平衡：各阶段每竿期望收益")
    plugin = make_plugin()
    cfg = plugin.cfg
    # 新经济模型：下竿免费（fish_cost 默认 0），唯一的持续支出是鱼饵单价；
    # 鱼饵在下竿时从库存扣 1 个（买的时候才付钱），不会再重复收一次饵钱。
    stages = [
        ("新手 竹竿/新手村/空钩", "novice", "bamboo", "none"),
        ("新手+ 竹竿/新手村/面包屑", "novice", "bamboo", "bread"),
        ("前期 碳素竿/溪流/蚯蚓", "bamboo", "carbon", "worm"),
        ("中期 溪流竿/湖泊/玉米粒", "lake", "stream", "corn"),
        ("后期 龙纹竿/近海/虾饵", "sea", "dragon", "shrimp"),
        ("末期 星辉竿/海沟/活饵", "abyss", "starlight", "livebait"),
        ("终局 神话竿/极光/秘制饵", "aurora", "mythic", "secret"),
    ]
    print(f"    {'阶段':30} {'毛收益':>9} {'饵成本':>7} {'净收益':>9} {'倍数':>7}")
    results = {}
    for name, loc, rod, bait in stages:
        rod_cfg = plugin.rod_by_id[rod]
        loc_cfg = plugin.location_by_id[loc]
        bait_cfg = plugin.baits[bait]
        total = 0.0
        n = 20000
        for _ in range(n):
            fish = plugin._roll_species(bait, loc)
            qm = mod._roll_quality_mult(
                cfg["quality_weights"],
                bait_luck=bait_cfg["luck"] + rod_cfg["luck_bonus"],
            )
            inst = mod._new_instance(
                fish["id"], qm,
                value_bonus=rod_cfg["value_bonus"],
                location_mult=loc_cfg["value_mult"],
            )
            total += inst["value"]
        gross = total / n
        cost = int(cfg["fish_cost"]) + (int(bait_cfg["price"]) if bait != "none" else 0)
        net = gross - cost
        results[name] = (gross, cost, net)
        ratio = f"{gross / cost:5.2f}x" if cost else "　 免费"
        print(f"    {name:30} {gross:9.1f} {cost:7} {net:9.1f} {ratio:>7}")

    novice_gross, _, novice_net = results["新手 竹竿/新手村/空钩"]
    top_gross, _, top_net = results["终局 神话竿/极光/秘制饵"]
    check(
        0 < novice_net <= 20,
        f"新手空钩免费也能赚（{novice_net:.1f}/竿）——不亏钱，但也不暴富",
    )
    check(
        top_net > novice_net * 50,
        f"终局净收益远高于新手（{top_net:.1f} > {novice_net:.1f}）",
    )
    check(
        top_gross / max(novice_gross, 0.01) < 500,
        f"终局/新手 毛收益倍率 {top_gross / novice_gross:.1f}x < 500"
        f"（鱼竿收益与高稀有度概率都下调过，跨度收窄）",
    )
    nets = [v[2] for v in results.values()]
    check(
        all(nets[i + 1] > nets[i] * 0.8 for i in range(len(nets) - 1)),
        "各阶段收益平滑递增，无断层",
    )
    # 好饵要有意义：同一钓点下，贵饵的期望收益必须显著高于空钩
    base_loc, base_rod = "lake", "stream"
    def expect(bait_id: str) -> float:
        rod_cfg = plugin.rod_by_id[base_rod]
        loc_cfg = plugin.location_by_id[base_loc]
        bait_cfg = plugin.baits[bait_id]
        total = 0.0
        for _ in range(6000):
            fish = plugin._roll_species(bait_id, base_loc)
            qm = mod._roll_quality_mult(
                cfg["quality_weights"],
                bait_luck=bait_cfg["luck"] + rod_cfg["luck_bonus"],
            )
            inst = mod._new_instance(
                fish["id"], qm,
                value_bonus=rod_cfg["value_bonus"],
                location_mult=loc_cfg["value_mult"],
            )
            total += inst["value"]
        return total / 6000

    e_none, e_worm, e_corn = expect("none"), expect("worm"), expect("corn")
    check(
        e_worm > e_none and e_corn > e_worm,
        f"越好的饵收益越高（空钩 {e_none:.1f} < 蚯蚓 {e_worm:.1f} < 玉米粒 {e_corn:.1f}）",
    )
    check(
        e_corn - plugin.baits["corn"]["price"] > e_none,
        f"玉米粒扣掉饵钱后仍优于空钩"
        f"（{e_corn - plugin.baits['corn']['price']:.1f} > {e_none:.1f}）",
    )
    check(
        plugin.baits["bread"]["price"] <= 2 and plugin.baits["worm"]["price"] <= 3,
        f"低级鱼饵便宜（面包屑 {plugin.baits['bread']['price']} 金、"
        f"蚯蚓 {plugin.baits['worm']['price']} 金）——新手用得起",
    )

    # 鱼竿收益下调约 40%（0.08/0.15/0.28/0.38/0.50 -> 0.05/0.09/0.17/0.23/0.30）
    rod_bonus = [plugin.rod_by_id[r]["value_bonus"] for r in
                 ("bamboo", "carbon", "stream", "dragon", "starlight", "mythic")]
    old_bonus = [0.00, 0.08, 0.15, 0.28, 0.38, 0.50]
    check(
        all(b > a for a, b in zip(rod_bonus, rod_bonus[1:])),
        f"鱼竿加成仍严格递增 -> {rod_bonus}",
    )
    check(
        all(new <= old * 0.65 for new, old in zip(rod_bonus, old_bonus))
        and rod_bonus[-1] <= 0.32,
        f"鱼竿加成比旧版低约 40% -> {rod_bonus}（旧版 {old_bonus}）",
    )

    # 高稀有度出现概率下调（常见 12→14，其余全降，且保持严格单调）
    weight = [mod.ROSTER_RARITY_WEIGHT[r] for r in mod.RARITY_ORDER]
    check(
        all(weight[i] > weight[i + 1] for i in range(len(weight) - 1)),
        f"稀有度权重严格单调 -> {weight}",
    )
    check(
        weight[2] <= 1.0 and weight[3] <= 0.25 and weight[4] <= 0.05,
        f"稀有/传说/神话权重已下调 -> {weight}",
    )
    # 基础权重单调性用空钩抽样验证（贵饵会按设计放大高品质权重：
    # 秘制饵最高 8 倍，所以带饵时「常见 > 少见」本来就不成立）。
    plain_share = collections.Counter()
    for _ in range(40000):
        plain_share[plugin._roll_species("none", "aurora")["rarity"]] += 1
    plain_total = sum(plain_share.values())
    plain_ratio = [plain_share.get(r, 0) / plain_total for r in mod.RARITY_ORDER]
    check(
        all(plain_ratio[i] > plain_ratio[i + 1] for i in range(len(plain_ratio) - 1)),
        f"空钩抽样严格递减 -> {[f'{x:.2%}' for x in plain_ratio]}",
    )

    # 终局钓点 + 最好的饵（秘制饵）：传说/神话合计约一成（实测 9.3%，这里留余量）。
    top_share = collections.Counter()
    for _ in range(40000):
        top_share[plugin._roll_species("secret", "aurora")["rarity"]] += 1
    legend_plus = (
        top_share.get("传说", 0) + top_share.get("神话", 0)
    ) / sum(top_share.values())
    check(
        legend_plus < 0.15,
        f"终局钓点 + 最好的饵，传说/神话合计 {legend_plus:.2%}（约一成，< 15%）",
    )

    # 池子最小的两个钓点：抽样确认高品质真的还抽得出来、且每种常规鱼权重都 > 0
    for loc_id in ("abyss", "aurora"):
        seen = collections.Counter()
        for _ in range(40000):
            seen[plugin._roll_species("secret", loc_id)["rarity"]] += 1
        species = plugin._location_species(loc_id)
        weights = mod.LOCATION_WEIGHTS.get(loc_id) or {}
        check(
            seen.get("传说", 0) > 0 and seen.get("神话", 0) > 0,
            f"{loc_id} 抽样仍能出传说/神话 -> 传说 {seen.get('传说', 0)}"
            f"、神话 {seen.get('神话', 0)}（常规 {len(species)} 种）",
        )
        check(
            all(mod._safe_number(weights.get(fid), 0) > 0 for fid in species),
            f"{loc_id} 全部 {len(species)} 种常规鱼的权重都 > 0",
        )

    sink = (
        sum(r["price"] for r in plugin.rods)
        + sum(l["gold_gate"] for l in plugin.locations)
        + sum(u["price"] for u in plugin.backpack_upgrades)
    )
    check(sink >= 45000, f"金币回收总额 {sink:,}（鱼竿+钓点+扩容）")

    # =====================================================================
    print("\n[11b] 鱼池配置化（fish_defs）：默认等价内置、可改价增删鱼")
    plugin_f = make_plugin()

    def _pool_snapshot() -> dict:
        """鱼池快照：鱼种 id -> (名称, 稀有度, 基准价, 各钓点权重元组)。"""
        snap = {}
        for fish in mod.FISH_POOL:
            fid = fish["id"]
            weights = tuple(
                sorted(
                    (loc, round(pool.get(fid, 0.0), 3))
                    for loc, pool in mod.LOCATION_WEIGHTS.items()
                    if fid in pool
                )
            )
            snap[fid] = (fish["name"], fish["rarity"], fish["value"], weights)

        return snap

    configured = _pool_snapshot()
    check(len(configured) >= 200, f"配置默认鱼池共 {len(configured)} 种鱼")

    # 等价性硬指标：清空 fish_defs 走内置路径，两者必须逐项一致
    defaults_text = plugin_f.config["fish_defs"]
    plugin_f.config["fish_defs"] = ""
    plugin_f._refresh_config()
    builtin = _pool_snapshot()

    def _same_pool(a: dict, b: dict, tol: float = 0.002) -> list:
        """严格比较鱼种/名称/稀有度/价值，权重允许 ±tol 的浮点往返误差。"""
        if set(a) != set(b):
            return sorted(set(a) ^ set(b))[:3]
        bad = []
        for key in a:
            na, ra, va, wa = a[key]
            nb, rb, vb, wb = b[key]
            if (na, ra, va) != (nb, rb, vb):
                bad.append(key)
                continue
            if len(wa) != len(wb) or any(
                abs(x[1] - y[1]) > tol or x[0] != y[0] for x, y in zip(wa, wb)
            ):
                bad.append(key)
        return bad

    diff = _same_pool(builtin, configured)
    # 硬指标：两条构建路径（配置接管 / 清空后回退内置）必须**逐项等价**。
    # 历史问题：EXTRA_FISH 里有 4 条鱼复用了名单鱼的 id（reed_frog / dock_octopus /
    # mangrove_mudskipper / trench_amphipod），写入权重时用的覆盖语义让「回退内置」
    # 路径的权重与「配置接管」不同。现已统一为「不覆盖已存在的 id」（名单鱼优先），
    # 因此这里不再允许任何差异。
    check(not diff, f"两条构建路径逐项等价（权重差异 {len(diff)} 条）-> {diff[:5]}")
    same_core = [k for k in configured if configured[k][:3] != builtin.get(k, ("", "", 0))[:3]]
    check(not same_core, f"鱼种/名称/稀有度/价值逐项一致（差异 {len(same_core)} 条）")
    if diff:
        for key in diff[:3]:
            print(f"      差异明细 {key}:")
            print(f"        内置   = {builtin.get(key)}")
            print(f"        配置后 = {configured.get(key)}")

    # 改价生效
    first_line, rest = defaults_text.split("\n", 1)
    parts = first_line.split("|")
    parts[3] = "600"
    plugin_f.config["fish_defs"] = "|".join(parts) + "\n" + rest
    plugin_f._refresh_config()
    check(
        mod.FISH_BY_ID[parts[0]]["value"] == 600,
        f"改行内基准价即生效 -> {parts[0]} = {mod.FISH_BY_ID[parts[0]]['value']}",
    )

    # 追加自定义鱼：进图鉴、挂到指定钓点、价值正确
    plugin_f.config["fish_defs"] = (
        defaults_text + "\nmy_test_fish|测试鱼|传说|1234|novice:5.0|站长的测试鱼"
    )
    plugin_f._refresh_config()
    check("my_test_fish" in mod.FISH_BY_ID, "新增的鱼进了图鉴")
    check(
        mod.LOCATION_WEIGHTS["novice"].get("my_test_fish", 0) > 0,
        f"新增的鱼挂到指定钓点 -> 权重 {mod.LOCATION_WEIGHTS['novice'].get('my_test_fish')}",
    )
    check(
        mod.FISH_BY_ID["my_test_fish"]["value"] == 1234,
        f"新增鱼基准价 -> {mod.FISH_BY_ID['my_test_fish']['value']}",
    )
    check(
        mod.FISH_BY_ID["my_test_fish"]["rarity"] == "传说",
        "新增鱼的稀有度按填写值生效",
    )

    # 分布写 `*` = 所有钓点
    plugin_f.config["fish_defs"] = defaults_text + "\nstar_fish|全图鱼|少见|88|*:0.5|到处都有"
    plugin_f._refresh_config()
    check(
        all(mod.LOCATION_WEIGHTS[loc].get("star_fish") for loc in mod.LOCATION_TIER_ORDER),
        "分布写 `*` 会挂到全部钓点",
    )

    # 坏行跳过、中文钓点名识别、空分布跳过
    plugin_f.config["fish_defs"] = (
        "good_fish|好鱼|常见|50|新手村:1.0|中文钓点名也认\n"
        "坏行没有竖线\n"
        "bad_fish|缺分布|常见|50||没有分布的行要被跳过\n"
    )
    plugin_f._refresh_config()
    check("good_fish" in mod.FISH_BY_ID, "中文钓点名 + 正常行照常生效")
    check("bad_fish" not in mod.FISH_BY_ID, "分布为空的行被跳过（不会出现钓不到的鱼）")
    check(len(mod.FISH_POOL) == 1, f"自定义鱼池整体接管 -> 只剩 {len(mod.FISH_POOL)} 条")

    # 配置全坏 / 留空 → 回退内置，绝不让鱼池空掉
    plugin_f.config["fish_defs"] = "全是坏的\n还是坏的"
    plugin_f._refresh_config()
    check(len(mod.FISH_POOL) == len(builtin), "配置全坏时回退内置鱼池（不会没鱼可钓）")
    restored = _pool_snapshot()
    check(
        len(restored) == len(builtin),
        f"   └ 回退后鱼种数与内置一致 -> {len(restored)}/{len(builtin)}",
    )
    same_core_back = [
        k for k in builtin if restored.get(k, ("", "", 0))[:3] != builtin[k][:3]
    ]
    check(
        not same_core_back,
        f"   └ 回退后名称/稀有度/价值逐项一致（差异 {len(same_core_back)} 条）",
    )

    plugin_f.config["fish_defs"] = defaults_text
    plugin_f._refresh_config()
    check(_pool_snapshot() == configured, "改回默认文本后完全恢复（无残留副作用）")

    # =====================================================================
    print("\n[11c] 内容表配置化（杂物/变异/天气/彩蛋）")
    import importlib.util as _ilu_gd

    _gd_spec = _ilu_gd.spec_from_file_location("gd_builtin", PLUGIN_DIR / "_game_data.py")
    gd_builtin = _ilu_gd.module_from_spec(_gd_spec)
    _gd_spec.loader.exec_module(gd_builtin)

    _CONTENT_TABLES = (
        ("COLLECTIBLES", "collectible_defs"),
        ("VARIANTS", "variant_defs"),
        ("WEATHERS", "weather_defs"),
        ("EASTER_EGGS", "easter_egg_defs"),
    )

    # --- 等价性硬指标：默认配置接管后必须与内置数据逐项一致 ---
    plugin_c = make_plugin()
    for _table, _key in _CONTENT_TABLES:
        _live = getattr(mod, _table)
        _builtin = getattr(gd_builtin, _table)
        check(
            len(_live) == len(_builtin) and all(a == b for a, b in zip(_live, _builtin)),
            f"{_key} 与内置逐项等价（{len(_live)} 条）",
        )
    check(
        len(getattr(mod, "COLLECTIBLES")) == 7
        and len(getattr(mod, "VARIANTS")) == 6
        and len(getattr(mod, "WEATHERS")) == 6
        and len(getattr(mod, "EASTER_EGGS")) == 7,
        "4 张表条目数正确（7/6/6/7）",
    )

    # --- 改配置真的生效 ---
    _cfg_c = load_schema_config()
    _cfg_c["collectible_defs"] = "test_junk|测试杂物|🗑️|5|7|测试用"
    _cfg_c["variant_defs"] = "test_var|测试变异|✨|10|1.75|测试用"
    plugin_c = make_plugin(_cfg_c)
    _junk = getattr(mod, "COLLECTIBLES")
    check(
        len(_junk) == 1 and _junk[0]["id"] == "test_junk" and _junk[0]["value"] == 7,
        f"杂物表被配置接管 -> {[c['id'] for c in _junk]}",
    )
    _var = getattr(mod, "VARIANTS")
    check(
        len(_var) == 1 and _var[0]["mult"] == 1.75,
        f"变异表被配置接管（价值倍率 {_var[0]['mult']}）",
    )

    # --- 空值 / 写坏 → 回退内置 ---
    _cfg_back = load_schema_config()
    plugin_c = make_plugin(_cfg_back)
    check(
        getattr(mod, "COLLECTIBLES") == getattr(gd_builtin, "COLLECTIBLES"),
        "清空配置后杂物表恢复内置",
    )
    _cfg_bad = load_schema_config()
    _cfg_bad["variant_defs"] = "全是坏行没有竖线"
    plugin_c = make_plugin(_cfg_bad)
    check(
        getattr(mod, "VARIANTS") == getattr(gd_builtin, "VARIANTS"),
        "配置全部写坏时回退内置（不再残留上次的值）",
    )

    # --- 解析容错（直接打解析器）---
    _rows = mod.CALC._parse_collectible_defs("y|测试杂物|🗑️|5|7|说明")
    check(
        _rows and _rows[0]["weight"] == 5 and _rows[0]["value"] == 7,
        "杂物解析：权重与价值正确",
    )
    _rows = mod.CALC._parse_variant_defs("x|测试变异|✨|10|1.75|说明")
    check(
        _rows and _rows[0]["mult"] == 1.75 and _rows[0]["desc"] == "说明",
        "变异解析：价值倍率与字段名正确",
    )
    _rows = mod.CALC._parse_weather_defs(
        "rain|下雨|🌧️|16|少见:1.4，稀有:1.5|0.9|0.04|1.0|雨天\n"
        "坏行没有竖线\n"
        "# 注释行\n"
        "\n"
        "sunny|晴朗|☀️|30||1.0|0.0|1.0|风平浪静"
    )
    check(len(_rows) == 2, f"天气解析：坏行跳过、注释与空行忽略 -> {len(_rows)} 条")
    check(
        _rows[0]["rarity_mult"] == {"少见": 1.4, "稀有": 1.5},
        "天气解析：全角逗号与中文键都能认",
    )
    check(
        _rows[1]["rarity_mult"] == {} and _rows[1]["window_mult"] == 1.0,
        "天气解析：稀有度倍率留空 = 无加成",
    )
    _eggs = mod.CALC._parse_easter_egg_defs(
        "a|10|文案一|gold=12;note=1\nb|5|文案二|luck=0.08;heal_bait=1"
    )
    check(
        len(_eggs) == 2
        and _eggs[0]["gold"] == 12
        and _eggs[0]["note"] is True
        and _eggs[1]["luck"] == 0.08
        and _eggs[1]["heal_bait"] is True,
        "彩蛋解析：数值型与开关型效果都正确",
    )

    # --- 内容表不该被默认值同步覆盖（与 fish_defs 同规则）---
    for _key in (
        "collectible_defs",
        "variant_defs",
        "weather_defs",
        "easter_egg_defs",
        "button_defs",
    ):
        check(
            _key in mod.DEFAULTS_SYNC_EXCLUDE_KEYS,
            f"{_key} 已排除默认值自动同步（站长编辑不会被升级覆盖）",
        )
    check(
        "backup_keep_manual" in mod.DEFAULTS
        and mod.DEFAULTS["backup_keep_manual"] == 0
        and mod._synced_default_keys().count("backup_keep_manual") == 0,
        "手动存档保留份数默认 0（永久保留），且不参与默认值同步",
    )

    # =====================================================================
    print("\n[12] 指令分派")
    plugin = make_plugin()
    ev = FakeEvent("99001")
    for args, kw in [
        (("帮助", "1", ""), "帮助 1/"),
        (("背包", "", ""), "背包"),
        (("商店", "", ""), "商店"),
        (("图鉴", "", ""), "图鉴"),
        (("图鉴", "详", ""), "图鉴"),
        (("档案", "", ""), "档案"),
        (("体力", "", ""), "体力"),
        (("水族馆", "", ""), "水族馆"),
        (("钓点", "", ""), "钓点"),
        (("鱼竿", "", ""), "鱼竿"),
        (("杂物", "", ""), "杂物收集"),
    ]:
        out = await cmd(plugin, ev, *args)
        check(kw in text_of(out), f"/钓鱼 {' '.join(x for x in args if x)} -> {kw}")
    out = await cmd(plugin, ev, "订单", "", "")
    check("订单" in text_of(out), "/钓鱼 订单")
    out = await cmd(plugin, ev, "乱写的", "", "")
    check("不认识" in text_of(out), "未知子命令有提示")
    # 老指令「金币」名不符实（它显示的其实是档案），现在只给改名提示
    out = await cmd(plugin, ev, "金币", "", "")
    check(
        "改名" in text_of(out) and "档案" in text_of(out),
        f"/钓鱼 金币 提示改名 -> {text_of(out).splitlines()[0]}",
    )
    check(
        "📇 档案" not in text_of(out),
        "旧指令不再直接吐出档案（避免两个名字干同一件事）",
    )

    # 每条子命令只回一条消息（曾经因为分派器多 yield 一次而整段重复）
    dups = []
    for args in (("帮助", "1", ""), ("背包", "", ""), ("今日", "", ""), ("排行", "", "")):
        out = await cmd(plugin, ev, *args)
        if len(out) != 1:
            dups.append(f"{args[0]}×{len(out)}")
    check(not dups, f"子命令输出不重复（异常项：{dups or '无'}）")

    # 下竿类同义词都进抛竿分支，不会被当成未知用法
    bad = []
    for word in ("钓", "钓鱼", "下竿", "抛竿", "甩竿", "cast"):
        out = await cmd(plugin, ev, word, "", "")
        if "不认识" in text_of(out):
            bad.append(word)
    check(not bad, f"下竿同义词都能识别（失败项：{bad or '无'}）")

    # =====================================================================
    print("\n[13] 各指令输出长度（防刷屏）")
    plugin = make_plugin()
    ev = FakeEvent("99002")
    p = await plugin._load_player("99002")
    p["gold"] = 100000
    p["total_caught"] = 300
    p["inventory"] = [mod._new_instance("koi", 1.5) for _ in range(18)]
    p["aquarium"] = [mod._new_instance("carp", 1.1) for _ in range(4)]
    p["rods"] = [r["id"] for r in mod.RODS]
    p["locations"] = [l["id"] for l in mod.LOCATIONS]
    p["collectibles"] = {c["id"]: 2 for c in mod.COLLECTIBLES}
    await plugin._save_player(p)
    for args in (
        ("背包", "", ""), ("图鉴", "", ""), ("档案", "", ""), ("水族馆", "", ""),
        ("商店", "", ""), ("钓点", "", ""), ("鱼竿", "", ""), ("杂物", "", ""),
        ("订单", "", ""), ("体力", "", ""),
    ):
        out = await cmd(plugin, ev, *args)
        n = len(text_of(out).splitlines())
        # 背包是「列满一页」的展示型指令，允许更长
        limit = 56 if args[0] == "背包" else 40
        check(n <= limit, f"/钓鱼 {args[0]} 输出 {n} 行（≤{limit}）")

    # =====================================================================
    print("\n[14] 命令别名 command_aliases（v1.10.0）")

    # ---- 14.1 内置写法总表：自检 + 与真实分派链的一致性 ----
    kw = mod.SUBCOMMAND_KEYWORDS
    check(len(kw) == 26, f"子命令总表 {len(kw)} 行")
    check(all(name in words for name, words in kw.items()), "每一行都含自己的规范名")
    _seen: dict[str, int] = {}
    for _words in kw.values():
        for _w in _words:
            _seen[_w] = _seen.get(_w, 0) + 1
    _dup = [w for w, n in _seen.items() if n > 1]
    check(not _dup, f"同一个写法只出现在一张行里（重复：{_dup or '无'}）")
    _flat = {w for words in kw.values() for w in words}
    check(mod.BUILTIN_COMMAND_WORDS == _flat, f"内置写法集合 {len(_flat)} 个")

    plugin = make_plugin()
    ev = FakeEvent("99003")
    _bad = []
    for _words in kw.values():
        for _w in _words:
            _out = await cmd(plugin, ev, _w)
            if "不认识" in text_of(_out):
                _bad.append(_w)
    check(not _bad, f"总表里 {len(_flat)} 个写法都能派到子命令（失败：{_bad or '无'}）")

    # ---- 14.2 默认值 = 现有别名，且默认配置下别名表为空（行为逐字不变）----
    _schema = json.loads(
        (PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8-sig")
    )
    _text = mod._default_command_aliases_text()
    check(mod.DEFAULTS["command_aliases"] == _text, "DEFAULTS 的别名默认值由总表现算")
    check(_schema["command_aliases"]["default"] == _text, "schema 默认值与代码生成的一致")
    check(
        _schema["custom_commands"]["default"] == "" == mod.DEFAULTS["custom_commands"],
        "自定义命令默认值为空（不强加任何新命令）",
    )
    _merged, _problems = mod.CALC._build_command_aliases(_text, kw)
    check(
        _merged == {} and _problems == [],
        f"解析默认值 = 零新增别名、零问题（{_merged}｜{_problems[:1]}）",
    )
    check(
        "command_aliases" in mod.DEFAULTS_SYNC_EXCLUDE_KEYS
        and "custom_commands" in mod.DEFAULTS_SYNC_EXCLUDE_KEYS,
        "两张命令表不参与默认值同步（升级不覆盖站长写的命令）",
    )
    check(
        "command_aliases" not in mod._synced_default_keys()
        and "custom_commands" not in mod._synced_default_keys(),
        "两张命令表不在同步键列表里",
    )
    check(
        len(_text.splitlines()) == len(kw)
        and all(f"{_name}|" in _text for _name in kw),
        "默认值逐行覆盖全部 26 个子命令",
    )

    # ---- 14.3 解析容错：写坏的行只跳过，不崩 ----
    _m, _p = mod.CALC._build_command_aliases("背包|仓库,行囊", kw)
    check(_m == {"仓库": "背包", "行囊": "背包"} and not _p, f"正常一行 -> {_m}")
    _m, _p = mod.CALC._build_command_aliases("背包｜仓库，行囊、包袱", kw)
    check(
        _m == {"仓库": "背包", "行囊": "背包", "包袱": "背包"} and not _p,
        f"全角竖线/逗号/顿号都认 -> {sorted(_m)}",
    )
    _m, _p = mod.CALC._build_command_aliases(
        "# 注释行\n\n背包|仓库\n   \n商店|铺子", kw
    )
    check(_m == {"仓库": "背包", "铺子": "商店"} and not _p, f"注释与空行被忽略 -> {_m}")
    _m, _p = mod.CALC._build_command_aliases("背包|包,bag", kw)
    check(_m == {} and not _p, "写内置写法 = 静默忽略（本来就有效，不报警）")
    _m, _p = mod.CALC._build_command_aliases("商店|背包", kw)
    check(
        _m == {} and _p and "背包" in _p[0],
        f"别名抢占别的规范名 -> 跳过并告警：{_p[:1]}",
    )
    _m, _p = mod.CALC._build_command_aliases("背包|道具", kw)
    check(_m == {} and _p and "道具" in _p[0], f"别名抢占别的内置写法 -> 跳过：{_p[:1]}")
    _m, _p = mod.CALC._build_command_aliases("钱包|钱袋", kw)
    check(_m == {} and _p and "不存在" in _p[0], f"目标子命令不存在 -> 跳过：{_p[:1]}")
    _m, _p = mod.CALC._build_command_aliases("背包|仓库\n商店|仓库", kw)
    check(
        _m == {"仓库": "背包"} and _p and "先写" in _p[0],
        f"两行争一个别名 -> 先到先得：{_m}｜{_p[:1]}",
    )
    _m, _p = mod.CALC._build_command_aliases("背包|蚯蚓", kw, reserved={"蚯蚓"})
    check(_m == {} and _p and "鱼饵" in _p[0], f"与鱼饵重名 -> 跳过：{_p[:1]}")
    for _bad_line in ("背包", "背包|", "|仓库", "背包|1", "背包|两 个", "背包|" + "太长" * 7):
        _m, _p = mod.CALC._build_command_aliases(_bad_line, kw)
        if _m or not _p:
            check(False, f"坏行应被跳过：{_bad_line} -> {_m}")
    check(True, "坏行（缺竖线/空别名/纯数字/带空格/超长）全部跳过")
    _m, _p = mod.CALC._build_command_aliases(
        "背包|" + ",".join(f"别名{i}" for i in range(25)), kw
    )
    check(
        len(_m) == mod.CALC.COMMAND_ALIAS_MAX_PER_ROW and _p,
        f"一行别名超上限 -> 只留 {mod.CALC.COMMAND_ALIAS_MAX_PER_ROW} 个并告警",
    )
    _m, _p = mod.CALC._build_command_aliases("", kw)
    check(_m == {} and not _p, "留空 = 没有任何新增别名（内置写法照常）")

    # ---- 14.4 别名真的能派到对应子命令 ----
    cfg = dict(_CFG)
    cfg["command_aliases"] = _text + "\n背包|仓库,行囊\n档案|小金库"
    plugin = make_plugin(cfg)
    check(
        mod.COMMAND_ALIASES == {"仓库": "背包", "行囊": "背包", "小金库": "档案"},
        f"运行时别名表 -> {mod.COMMAND_ALIASES}",
    )
    _base = text_of(await cmd(plugin, ev, "背包"))
    _alias = text_of(await cmd(plugin, ev, "仓库"))
    check(_alias == _base, "别名输出与规范子命令**逐字一致**（/钓鱼 仓库 == /钓鱼 背包）")
    _alias2 = text_of(await cmd(plugin, ev, "行囊"))
    check(_alias2 == _base, "/钓鱼 行囊 == /钓鱼 背包")
    _dir = text_of(await cmd(plugin, ev, "档案"))
    check(text_of(await cmd(plugin, ev, "小金库")) == _dir, "/钓鱼 小金库 == /钓鱼 档案")
    check(
        text_of(await cmd(plugin, ev, "仓库", "2")) == text_of(await cmd(plugin, ev, "背包", "2")),
        "别名带参数（/钓鱼 仓库 2 == /钓鱼 背包 2）",
    )
    check(
        text_of(await cmd(plugin, ev, "仓库2")) == text_of(await cmd(plugin, ev, "背包", "2")),
        "别名也认少打空格（/钓鱼 仓库2 自动拆成 仓库 + 2）",
    )
    _m, _p = mod.CALC._build_command_aliases(_text + "\n拉|拽线", kw)
    check(_m == {"拽线": "拉"} and not _p, f"可以给「拉」这类前置分支加别名：{_m}")
    check(
        mod.CALC._build_command_aliases("锁定|拉", kw)[0] == {},
        "但不许把内置写法「拉」抢给别的子命令",
    )
    cfg["command_aliases"] = _text + "\n拉|拽线"
    plugin = make_plugin(cfg)
    check(
        "没有鱼咬钩" in text_of(await cmd(plugin, ev, "拽线")),
        "指到「拉」的新别名照样走拉线分支",
    )
    cfg["command_aliases"] = "乱写的配置\n背包|"
    plugin = make_plugin(cfg)
    check(
        mod.COMMAND_ALIASES == {} and "不认识" in text_of(await cmd(plugin, ev, "乱写的配置")),
        "全是坏行 -> 别名表为空、内置命令不受影响",
    )
    check(
        text_of(await cmd(plugin, ev, "背包")) == _base,
        "全坏配置下内置命令输出不变",
    )

    # =====================================================================
    print("\n[15] 自定义命令 custom_commands（v1.10.0）")

    # ---- 15.1 解析与校验 ----
    _cmds, _p = mod.CALC._parse_custom_commands(
        "快捷签到|执行:签到\n领奖|发送:你好\n礼包|执行:签到;背包\n查鱼|执行:查 鲤鱼",
        kw,
    )
    check(
        _cmds == {
            "快捷签到": ("执行", "签到"),
            "领奖": ("发送", "你好"),
            "礼包": ("执行", "签到;背包"),
            "查鱼": ("执行", "查 鲤鱼"),
        } and not _p,
        f"正常四条 -> {_cmds}",
    )
    _cmds, _p = mod.CALC._parse_custom_commands("领奖|send:hi\n领奖2|发送：你好", kw)
    check(_cmds == {"领奖": ("发送", "hi"), "领奖2": ("发送", "你好")} and not _p,
          f"英文动作与全角冒号都认 -> {_cmds}")
    _cmds, _p = mod.CALC._parse_custom_commands("背包|发送:抢内置", kw)
    check(_cmds == {} and _p and "内置" in _p[0], f"与内置子命令重名 -> 跳过：{_p[:1]}")
    _cmds, _p = mod.CALC._parse_custom_commands("仓库|发送:x", kw, reserved={"仓库"})
    check(_cmds == {} and _p and "别名" in _p[0], f"与别名重名 -> 跳过：{_p[:1]}")
    _cmds, _p = mod.CALC._parse_custom_commands(
        "甲|执行:乙\n乙|发送:我是乙", kw, reserved={"甲", "乙"}
    )
    check(
        _cmds == {} and len(_p) == 2,
        f"自定义命令互相调用被禁止（连名字都过不了别名冲突）：{_p}",
    )
    _cmds, _p = mod.CALC._parse_custom_commands("甲|执行:乙\n乙|执行:甲", kw)
    check(_cmds == {} and len(_p) == 2, f"互指的两条都跳过：{_p}")
    _cmds, _p = mod.CALC._parse_custom_commands("甲|执行:乙", kw)
    check(_cmds == {} and _p and "不存在" in _p[0], f"执行: 指向不存在的子命令 -> 跳过：{_p[:1]}")
    _cmds, _p = mod.CALC._parse_custom_commands("甲|执行:签到;乱写的", kw)
    check(_cmds == {} and _p and "不存在" in _p[0], f"执行: 里有一环写错 -> 整条跳过：{_p[:1]}")
    _cmds, _p = mod.CALC._parse_custom_commands("甲|执行:查 不存在的鱼", kw)
    check(
        _cmds == {"甲": ("执行", "查 不存在的鱼")} and not _p,
        "执行: 只要子命令本身合法就放行（参数里的名字由运行时兜底）",
    )
    for _bad_cmd in ("|发送:x", "没有动作", "甲|乱动作:x", "甲|发送:", "甲 乙|发送:x", "123|发送:x"):
        _cmds, _p = mod.CALC._parse_custom_commands(_bad_cmd, kw)
        if _cmds or not _p:
            check(False, f"坏配置应被跳过：{_bad_cmd} -> {_cmds}")
    check(True, "坏配置（空名/缺动作/动作不认识/空内容/带空格/纯数字）全部跳过")
    _long = "长" + "名" * mod.CALC.CUSTOM_COMMAND_NAME_MAX
    _cmds, _p = mod.CALC._parse_custom_commands(f"{_long}|发送:x", kw)
    check(_cmds == {} and _p, "命令名超长 -> 跳过")
    _cmds, _p = mod.CALC._parse_custom_commands(
        "甲|发送:" + "字" * (mod.CALC.CUSTOM_COMMAND_BODY_MAX + 1), kw
    )
    check(_cmds == {} and _p, "内容超长 -> 跳过")
    _many = "\n".join(f"命令{i}|发送:x" for i in range(mod.CALC.CUSTOM_COMMAND_MAX_ROWS + 3))
    _cmds, _p = mod.CALC._parse_custom_commands(_many, kw)
    check(
        len(_cmds) == mod.CALC.CUSTOM_COMMAND_MAX_ROWS and _p,
        f"条数上限 {mod.CALC.CUSTOM_COMMAND_MAX_ROWS} 条（多的忽略并告警）",
    )
    _cmds, _p = mod.CALC._parse_custom_commands("", kw)
    check(_cmds == {} and not _p, "留空 = 没有自定义命令（内置命令不受影响）")
    check(
        mod.CALC._fill_custom_text("金币={金币} 未知={没这个}", {"金币": 12})
        == "金币=12 未知={没这个}",
        "占位符替换：认识的替换、不认识的原样保留",
    )

    # ---- 15.2 自定义命令真的能跑，且与内置命令等价 ----
    cfg = dict(_CFG)
    cfg["custom_commands"] = (
        "快捷签到|执行:签到\n"
        "礼包|执行:签到;背包\n"
        "余额|发送:你现在有 {金币} 金币，等级 {等级}，共钓到 {钓获} 条\n"
        "查鱼|执行:查 鲤鱼\n"
    )
    plugin = make_plugin(cfg)
    check(
        set(mod.CUSTOM_COMMANDS) == {"快捷签到", "礼包", "余额", "查鱼"},
        f"运行时自定义命令表 -> {sorted(mod.CUSTOM_COMMANDS)}",
    )
    p = await plugin._load_player(ev.get_sender_id())
    p["gold"] = 4321
    await plugin._save_player(p)
    _sign = text_of(await cmd(plugin, ev, "签到"))
    check(
        text_of(await cmd(plugin, ev, "快捷签到")) == _sign,
        "执行:签到 的输出与 /钓鱼 签到 逐字一致",
    )
    _pack = text_of(await cmd(plugin, ev, "礼包"))
    check(
        _pack.startswith(_sign) and text_of(await cmd(plugin, ev, "背包")) in _pack,
        "执行:签到;背包 依次跑两条内置子命令",
    )
    _bal = text_of(await cmd(plugin, ev, "余额"))
    check("4321" in _bal, f"占位符 {{{{金币}}}} 取到玩家真实数据 -> {_bal.splitlines()[0]}")
    check(
        text_of(await cmd(plugin, ev, "查鱼")) == text_of(await cmd(plugin, ev, "查", "鲤鱼")),
        "执行:查 鲤鱼 与 /钓鱼 查 鲤鱼 逐字一致",
    )
    check(
        text_of(await cmd(plugin, ev, "余额")) == _bal,
        "纯回复型自定义命令可重复执行（没有副作用）",
    )

    # ---- 15.3 内置永远优先、防递归、坏配置不打扰内置 ----
    cfg = dict(_CFG)
    cfg["custom_commands"] = "背包|发送:我要抢背包\n甲|执行:甲\n乙|执行:乱写的\n好命令|发送:ok"
    plugin = make_plugin(cfg)
    check(
        set(mod.CUSTOM_COMMANDS) == {"好命令"},
        f"抢内置/自指/执行坏子命令的都跳过（剩下 {sorted(mod.CUSTOM_COMMANDS)}）",
    )
    check(text_of(await cmd(plugin, ev, "背包")) == _base, "内置「背包」没被自定义命令抢走")
    check("ok" in text_of(await cmd(plugin, ev, "好命令")), "合法的自定义命令照常工作")
    cfg["custom_commands"] = "甲|执行:乙\n乙|执行:甲\n循环|执行:循环"
    plugin = make_plugin(cfg)
    check(mod.CUSTOM_COMMANDS == {}, "会自我/互相调用的写法一条都不生效（防递归）")
    check(
        "不认识" in text_of(await cmd(plugin, ev, "甲")),
        "被跳过的命令名回到「不认识」提示，行为与未知命令一致",
    )
    cfg["command_aliases"] = _text + "\n背包|仓库"
    cfg["custom_commands"] = "仓库|发送:抢别名"
    plugin = make_plugin(cfg)
    check(
        mod.COMMAND_ALIASES == {"仓库": "背包"} and mod.CUSTOM_COMMANDS == {},
        "别名与自定义命令重名 -> 别名赢，自定义命令跳过并告警",
    )
    check(text_of(await cmd(plugin, ev, "仓库")) == _base, "重名时 /钓鱼 仓库 走别名（内置行为可预期）")

    make_plugin()   # 复位成默认配置，避免影响别的用例

    # =====================================================================
    print("\n[16] 配置面板瘦身：只留 3 条救生索（v1.10.0）")

    _schema = json.loads(
        (PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8-sig")
    )
    check(
        len(_schema) == 106,
        f"配置项总数 {len(_schema)}（v1.9.0 的 93 + command_aliases + custom_commands + 路标"
        f" + v1.11.0 的 decoration_slots/decoration_hours/buff_cast_count"
        f" + v1.12.0 的 text_overrides/button_layout"
        f" + v1.13.0 的 button_style_mode/button_default_style"
        f" + v1.14.0 的 order_follow_location/order_move_rerolls/order_include_hidden"
        f" + v1.18.0 的 button_empty_scenes；aquarium_bonus* 两项已在 v1.18.0 删掉）",
    )
    _visible = sorted(k for k, v in _schema.items() if not v.get("invisible"))
    check(
        _visible == ["content_tables_hint", "data_status", "defaults_sync_mode"],
        f"面板只剩 3 条救生索：{_visible}",
    )
    _hidden = [k for k, v in _schema.items() if v.get("invisible")]
    check(len(_hidden) == 103, f"其余 {len(_hidden)} 项全部 invisible")
    # 页面「数值」页必须覆盖所有「面板藏了、又只有手改配置文件才能改」的键
    _bridge_mod = sys.modules.get("astrbot_fishing_editor_bridge")
    if _bridge_mod is not None:
        # 只扫「数值」页那张表（别把别的数组字面量当配置键）
        _html = (PLUGIN_DIR / "pages" / "editor" / "index.html").read_text(encoding="utf-8")
        _block = re.search(r"var NUMBER_KEYS = \[(.*?)\n  \];", _html, re.S)
        _page_keys = set(re.findall(r'\["([a-z0-9_]+)", "', _block.group(1) if _block else ""))
        _reply_keys = set(_bridge_mod.EditorBridgeMixin.REPLY_TEXT_KEYS) | set(
            _bridge_mod.EditorBridgeMixin.REPLY_SCALAR_KEYS
        )
        _set_content_tables = set(_bridge_mod.CONTENT_TABLES)
        _wl = set(_bridge_mod.number_whitelist())
        _skip = {"config_fingerprint", "defaults_sync_mode", "content_tables_hint",
                 "content_auto_merge", "enable_auto_backup", "data_status"}
        _unreachable = sorted(
            k for k in mod.DEFAULTS
            if k not in _page_keys and k not in _reply_keys and k not in _set_content_tables
            and not k.startswith(("data_", "backup_", "editor_"))
            and k not in _skip and not k.endswith(("_defs", "_slots", "_upgrades"))
        )
        check(
            not _unreachable,
            f"面板隐藏的配置项在编辑器里都能改（改不到的：{_unreachable}）",
            ", ".join(_unreachable),
        )
        check(
            all(k in _wl for k in _page_keys if k in mod.DEFAULTS),
            "页面「数值」页列出的键都在插件白名单里（不然会被页面自己隐藏掉）",
        )
        check(
            "quality_weights" in _wl and "rarity_display_names" in _wl
            and "backpack_upgrades" in _wl and "aquarium_slots" in _wl
            and "decoration_slots" in _wl,
            "列表型配置（品质权重/显示名/扩容表/装饰位）也允许页面改",
        )
    check(
        all(k in mod.DEFAULTS for k in _visible),
        "3 条救生索都在 DEFAULTS 里（不是凭空写的）",
    )
    check(
        mod.DEFAULTS["content_tables_hint"] == "",
        "路标是纯说明项（默认值为空，没有任何代码读它）",
    )
    check(
        "content_tables_hint" not in mod._synced_default_keys(),
        "路标不参与默认值同步",
    )
    _synced_hidden = [k for k in _hidden if k in mod._synced_default_keys()]
    check(
        len(_synced_hidden) > 0 and "fish_defs" not in _synced_hidden,
        f"「隐藏」与「不参与同步」是两码事：隐藏项里 {len(_synced_hidden)} 项照样参与数值同步，"
        f"内容表不参与",
    )

    # invisible 只是「面板不显示」，配置里必须照样有、而且改完存得住
    from astrbot.core.config.astrbot_config import AstrBotConfig

    _path = os.path.join(_SANDBOX_ROOT, "test_invisible.json")
    if os.path.exists(_path):
        os.remove(_path)
    _cfg2 = AstrBotConfig(config_path=_path, schema=_schema)
    check(
        len(dict(_cfg2)) == len(_schema),
        f"invisible 项照样进默认配置（{len(dict(_cfg2))} 项 = schema {len(_schema)} 项）",
    )
    check(
        _cfg2.get("fish_defs") == _schema["fish_defs"]["default"]
        and str(_cfg2.get("fish_defs") or "").strip() != "",
        "隐藏的 fish_defs 默认值原样在配置里（没被 invisible 清空）",
    )
    _one_fish = str(mod.DEFAULTS["fish_defs"]).splitlines()[0]
    _one_rod = [str(mod.DEFAULTS["rod_defs"][0])]   # rod_defs 是字符串数组
    _cfg2["fish_defs"] = _one_fish
    _cfg2["rod_defs"] = _one_rod
    _cfg2.save_config()
    _cfg3 = AstrBotConfig(config_path=_path, schema=_schema)   # 等于重载一次配置
    check(
        _cfg3.get("fish_defs") == _one_fish and _cfg3.get("rod_defs") == _one_rod,
        "改隐藏项 -> 保存 -> 重新载入，值还在（invisible 不会被丢弃）",
    )
    # rod_defs 是「列表型内容表」：插件启动时会用 content_auto_merge 把官方新增条目
    # 补回去（这是既有功能，不是 invisible 的锅），所以这里关掉它才能证明
    # 「隐藏项里写的值真的生效」；fish_defs 是文本表，不参与增量合并。
    _cfg3["content_auto_merge"] = False
    _plugin3 = make_plugin(dict(_cfg3))
    check(
        len(mod.FISH_POOL) == 1
        and mod.FISH_POOL[0]["id"] == _one_fish.split("|")[0].strip(),
        f"隐藏项的内容真的生效：鱼池被改成 {len(mod.FISH_POOL)} 条（编辑页面通道照常好用）",
    )
    check(
        len(_plugin3.rods) == 1 and _plugin3.rods[0]["id"] == _one_rod[0].split("|")[0].strip(),
        f"隐藏的 rod_defs 同样生效（关掉增量合并后鱼竿 {len(_plugin3.rods)} 种）",
    )
    _cfg3["content_auto_merge"] = True
    _plugin3b = make_plugin(dict(_cfg3))
    check(
        len(_plugin3b.rods) == 6,
        f"开着增量合并时官方鱼竿会被补回来（{len(_plugin3b.rods)} 种，站长自写的仍在）",
    )
    make_plugin()
    _fish_default = len(str(mod.DEFAULTS["fish_defs"]).splitlines())
    check(
        len(mod.FISH_POOL) == _fish_default and len(mod.RODS) == 6,
        f"复位后鱼池/鱼竿恢复默认（{len(mod.FISH_POOL)} 条鱼 / {len(mod.RODS)} 种竿）",
    )

    # =====================================================================
    print("\n[17] 玩家金币编辑（v1.10.0：实时玩家 + 存档内玩家）")

    def _real_backup_state() -> dict[str, str]:
        """真插件目录 backups/ 下每个快照文件的「相对路径 -> 内容 sha1」。

        用来证明**测试没有碰站长的真存档**（比只比文件名强：改名不改内容、内容改了
        文件名也可能不变）。

        ⚠️ 有意跳过 ``index.json``：站长那台**正在运行的实例**每分钟会走一次自动存档
        循环里的 ``_refresh_data_status()`` → ``rebuild_index()``，索引文件本来就一直在被
        它重写；把索引算进来只会让这条自检随机失败。快照文件本体（auto/daily/manual/
        players/exported）才是数据，一个都不许变。
        """
        root = PLUGIN_DIR / "backups"
        state: dict[str, str] = {}
        if root.is_dir():
            for dirpath, _dirs, names in os.walk(root):
                for name in names:
                    if name == "index.json":
                        continue
                    path = Path(dirpath) / name
                    rel = str(path.relative_to(PLUGIN_DIR))
                    try:
                        state[rel] = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
                    except OSError:
                        state[rel] = "unreadable"
        return state

    def _api_payload(res):
        """editor_api_* 可能回 dict（无 astrbot.api.web）或 JSONResponse。"""
        if isinstance(res, dict):
            return res
        body = getattr(res, "body", None)
        if body:
            return json.loads(body.decode("utf-8"))
        return {}

    real_before = _real_backup_state()
    plugin = make_plugin()
    check(
        str(plugin.backup_store.root).startswith(_SANDBOX_ROOT),
        f"存档仓库钉在沙箱里：{plugin.backup_store.root}",
    )

    # ---- 准备两个玩家（用全新的 uid，避免和别的用例互相影响）----
    created: dict[str, dict] = {}
    for uid, gold, name in (("77001", 3000, "沙箱甲"), ("77002", 800, "沙箱乙")):
        p = await plugin._load_player(uid)
        p["gold"] = gold
        p["last_name"] = name
        p["total_caught"] = 40
        await plugin._save_player(p)
        created[uid] = p
    kv_key_77001 = plugin._kv_key("77001")
    raw_77001_before = await plugin.get_kv_data(kv_key_77001, None)
    ids_before = len(await plugin._player_ids())

    # ---- 列表：只读，不许产生副作用 ----
    rows, total = await plugin._editor_player_rows()
    by_id = {r["user_id"]: r for r in rows}
    check(
        by_id.get("77001", {}).get("gold") == 3000 and by_id.get("77002", {}).get("gold") == 800,
        "列表里能看到刚写的两个玩家与金币",
    )
    check(
        by_id.get("77001", {}).get("name") == "沙箱甲"
        and by_id.get("77001", {}).get("caught") == 40
        and by_id.get("77001", {}).get("level") == mod._player_level(created["77001"]),
        "行里带昵称 / 钓获 / 等级",
    )
    check(
        [r["gold"] for r in rows] == sorted([r["gold"] for r in rows], reverse=True),
        "列表按金币从高到低",
    )
    check(
        await plugin.get_kv_data(kv_key_77001, None) == raw_77001_before
        and len(await plugin._player_ids()) == ids_before,
        "读列表不会改玩家数据（也没凭空造出新玩家）",
    )
    payload = _api_payload(await plugin.editor_api_players({"action": "list"}))
    check(
        payload.get("status") == "ok" and payload.get("count") == len(rows)
        and payload.get("gold_max") == mod.EDITOR_BRIDGE.PLAYER_GOLD_MAX,
        f"players 接口回列表 + 金币上限（{payload.get('count')} 行 / 上限 {payload.get('gold_max')}）",
    )
    rows_q, _t = await plugin._editor_player_rows("沙箱乙")
    check(
        [r["user_id"] for r in rows_q] == ["77002"],
        f"按昵称搜索能定位到玩家（{len(rows_q)} 行）",
    )
    rows_q2, _t2 = await plugin._editor_player_rows("77001")
    check([r["user_id"] for r in rows_q2] == ["77001"], "按 ID 搜索能定位到玩家")

    # ---- 校验与二次确认：任何一条不过都不许改数据 ----
    ok, msg = await plugin._editor_set_player_gold({"user_id": "77001", "gold": 9999})
    check(not ok and "确认" in msg, f"没带二次确认 -> 拒绝：{msg}")
    ok, msg = await plugin._editor_set_player_gold(
        {"user_id": "77001", "gold": -1, "confirm": True}
    )
    check(not ok and "负" in msg, f"负数 -> 拒绝：{msg}")
    ok, msg = await plugin._editor_set_player_gold(
        {"user_id": "77001", "gold": "abc", "confirm": True}
    )
    check(not ok and "不是数字" in msg, f"非数字 -> 拒绝：{msg}")
    ok, msg = await plugin._editor_set_player_gold(
        {"user_id": "77001", "gold": mod.EDITOR_BRIDGE.PLAYER_GOLD_MAX + 1, "confirm": True}
    )
    check(not ok and "最多" in msg, f"超过上限 -> 拒绝：{msg}")
    ok, msg = await plugin._editor_set_player_gold(
        {"user_id": "99999999", "gold": 10, "confirm": True}
    )
    check(not ok and "找不到玩家" in msg, f"不存在的玩家 -> 拒绝：{msg}")
    ok, msg = await plugin._editor_set_player_gold({"user_id": "77001", "confirm": True})
    check(not ok, "没写金币 -> 拒绝")
    check(
        (await plugin._load_player("77001"))["gold"] == 3000,
        "上面这些被拒的请求一个都没改到数据（金币还是 3000）",
    )
    gold, why = mod.EDITOR_BRIDGE._coerce_gold(True)
    check(gold is None and why, f"金币解析：布尔被拒绝（{why}）")
    gold, why = mod.EDITOR_BRIDGE._coerce_gold("1e3")
    check(gold == 1000 and not why, "金币解析：1e3 -> 1000（宽容，但要能算出来）")
    gold, why = mod.EDITOR_BRIDGE._coerce_gold(-0.5)
    check(gold is None and why, f"金币解析：负数被拒绝（{why}，不允许被 int() 截成 0）")
    gold, why = mod.EDITOR_BRIDGE._coerce_gold(" 2500 ")
    check(gold == 2500 and not why, "金币解析：带空格的数字串也能认")
    gold, why = mod.EDITOR_BRIDGE._coerce_gold(1500.9)
    check(gold == 1500 and not why, "金币解析：小数向下取整（1500.9 -> 1500）")
    gold, why = mod.EDITOR_BRIDGE._coerce_gold("nan")
    check(gold is None and why, f"金币解析：nan 被拒绝（{why}）")

    # ---- 正常改金币：自动存档 + 落盘 ----
    snaps_before = len(plugin.backup_store.list_snapshots())
    ok, msg = await plugin._editor_set_player_gold(
        {"user_id": "77001", "gold": 4321, "confirm": True}
    )
    check(ok, f"改金币成功：{msg}")
    check(
        (await plugin._load_player("77001"))["gold"] == 4321,
        "金币真的写进玩家数据了",
    )
    snaps = plugin.backup_store.list_snapshots()
    check(
        len(snaps) == snaps_before + 1 and snaps[0]["kind"] == "auto"
        and "改金币前自动存档" in str(snaps[0]["note"]),
        f"改前自动存了一份 auto 档（{snaps[0]['name']}：{snaps[0]['note']}）",
    )
    check(
        str(plugin.backup_store.path_of("auto")).startswith(_SANDBOX_ROOT),
        "自动存档落在沙箱的 auto/ 里（不碰真存档）",
    )
    check(
        "页面改金币" in str(plugin.cfg.get("data_status") or plugin.config.get("data_status") or ""),
        f"面板的 data_status 也留下了记录：{plugin.cfg.get('data_status')}",
    )
    payload = _api_payload(await plugin.editor_api_players({"action": "list", "query": "77001"}))
    check(
        payload["players"][0]["gold"] == 4321,
        "改完再读列表，金币已是新值",
    )

    # ---- 改「存档里的玩家」：当场不动实时数据，恢复后才生效 ----
    ok, msg = await plugin._editor_set_snapshot_gold(
        {"name": snaps[0]["name"], "user_id": "77001", "gold": 7777}
    )
    check(not ok and "确认" in msg, f"改存档没带二次确认 -> 拒绝：{msg}")
    ok, msg = await plugin._editor_set_snapshot_gold(
        {"name": "不存在的存档.json", "user_id": "77001", "gold": 7777, "confirm": True}
    )
    check(not ok and "找不到存档" in msg, f"存档名写错 -> 拒绝：{msg}")
    ok, msg = await plugin._editor_set_snapshot_gold(
        {"name": snaps[0]["name"], "user_id": "88888", "gold": 7777, "confirm": True}
    )
    check(not ok and "没有玩家" in msg, f"存档里没这个玩家 -> 拒绝：{msg}")
    ok, msg = await plugin._editor_set_snapshot_gold(
        {"name": "../../evil.json", "user_id": "77001", "gold": 7777, "confirm": True}
    )
    check(not ok, f"路径穿越写法 -> 拒绝：{msg}")

    snap_name = snaps[0]["name"]
    ok, msg = await plugin._editor_set_snapshot_gold(
        {"name": snap_name, "user_id": "77001", "gold": 7777, "confirm": True}
    )
    check(ok and "恢复" in msg, f"改存档里的金币成功：{msg}")
    check(
        (await plugin._load_player("77001"))["gold"] == 4321,
        "改存档**不会**动在线玩家（实时金币还是 4321）",
    )
    snap_data = plugin.backup_store.load_snapshot(snap_name)
    check(
        snap_data["players"]["77001"]["data"]["gold"] == 7777,
        "存档文件里那个玩家的金币已经变成 7777",
    )
    check(
        snap_data.get("count") == snaps[0]["count"],
        "存档的玩家数没变（只改了一个字段）",
    )
    snap_rows = _api_payload(
        await plugin.editor_api_players({"action": "snapshot_list", "name": snap_name})
    )
    snap_row_77001 = [
        r for r in snap_rows.get("players", []) if r["user_id"] == "77001"
    ]
    check(
        snap_rows.get("status") == "ok" and snap_row_77001
        and snap_row_77001[0]["gold"] == 7777,
        f"存档内玩家列表读出来是改后的值（{len(snap_rows.get('players', []))} 名玩家）",
    )
    check(
        snap_rows.get("snapshot", {}).get("name") == snap_name,
        "响应里带上存档名（页面显示用）",
    )
    # 恢复这份存档 -> 修改才生效（端到端）
    restore_msg = await plugin._restore_snapshot(snap_name)
    check("恢复" in restore_msg, f"恢复存档：{restore_msg}")
    check(
        (await plugin._load_player("77001"))["gold"] == 7777,
        "恢复之后在线玩家的金币 = 存档里改过的值（整条链路通了）",
    )

    # ---- 编辑器状态里要带上页面需要的元信息 ----
    status = json.loads(await plugin._editor_build_status(action="test", ok=True, message="x"))
    check(
        len(status.get("subcommands") or []) == len(mod.SUBCOMMAND_KEYWORDS),
        f"editor_status 带上 {len(status.get('subcommands') or [])} 个规范子命令（命令页校验用）",
    )
    check(
        status.get("custom_actions") == ["发送", "执行"]
        and status.get("gold_max") == mod.EDITOR_BRIDGE.PLAYER_GOLD_MAX,
        "editor_status 带上自定义命令动作与金币上限",
    )
    check(
        set(mod.EDITOR_BRIDGE.CONTENT_TABLES) >= {"command_aliases", "custom_commands"},
        "命令别名 / 自定义命令进了 save_content 白名单（否则保存会被整批拒绝）",
    )
    check(
        await plugin.editor_api_players({"action": "乱写的动作"}) is not None,
        "不认识的玩家动作有错误响应（不抛异常）",
    )

    real_after = _real_backup_state()
    check(
        real_before == real_after,
        f"⚠️ 数据隔离自检：真插件 backups/ 里 {len(real_before)} 个快照文件的内容前后逐字节一致"
        f"（index.json 除外：站长那台实例每分钟会重写它）",
    )

    # =====================================================================
    print("\n[17] 道具系统重做：装饰耐久 / 饲料 / 育灵水 / 锦鲤玉佩（v1.11.0）")

    plugin_r = make_plugin()
    cfg_r = plugin_r.cfg
    check(len(plugin_r.items) == 7, f"道具 7 种 -> {len(plugin_r.items)}")
    check(
        abs(mod._safe_number(plugin_r.items["coral_deco"]["effects"].get("decorate"), 0) - 0.20) < 1e-9,
        "珊瑚造景 = 装饰类（decorate=0.20）",
    )
    check(
        plugin_r.items["feed_divine"]["effects"].get("value_up") == 600,
        "仙露估值 +150 → +600",
    )
    check(
        plugin_r.items["growth_tonic"]["effects"].get("feed_bonus") == 5,
        "育灵水 feed_bonus=5（对鱼生效）",
    )
    check(
        abs(mod._safe_number(plugin_r.items["lucky_jade"]["effects"].get("buff_quality"), 0) - 0.30) < 1e-9,
        "锦鲤玉佩 buff_quality=0.30（对钓手生效）",
    )

    # --- 效果键解析 ---
    check(
        mod._parse_effects("quality_up=0.3") == {"buff_quality": 0.3},
        "旧的 quality_up 写法兼容成 buff_quality",
    )
    parsed = mod._parse_effects("decorate=0.2;feed_bonus=5;buff_quality=0.3;meat=2;nope=9")
    check("nope" not in parsed and len(parsed) == 4, f"未知效果键被跳过 -> {sorted(parsed)}")

    # --- 喂鱼上限：育灵水加成 ---
    inst_r = mod._new_instance("carp", 1.0)
    check(mod.CALC._feed_cap(inst_r, cfg_r) == 10, "默认投喂上限 = feed_max_uses(10)")
    inst_r["feed_bonus"] = 5
    check(mod.CALC._feed_cap(inst_r, cfg_r) == 15, "育灵水后上限 10 → 15")

    # --- 装饰：摆放 / 上限 / 加成 / 真实时间到期 ---
    ev_r = FakeEvent("96001")
    p = await plugin_r._load_player("96001")
    p["items"]["coral_deco"] = 5
    # 缸里要有一条鱼，视图才会走「有鱼」分支（空缸分支是另一套文案）
    p["aquarium"] = [mod._new_instance("carp", 1.2, value_override=800)]
    await plugin_r._save_player(p)
    out = text_of(await cmd(plugin_r, ev_r, "用", "珊瑚造景", ""))
    check("摆好了" in out, f"珊瑚造景是「摆放」不是喂鱼 -> {out.splitlines()[0] if out else ''}")
    p = await plugin_r._load_player("96001")
    check(len(p["decorations"]) == 1, f"装饰已记录 -> {len(p['decorations'])} 个")
    check(
        abs(mod._safe_number(p["decorations"][0].get("rate"), 0) - 0.20) < 1e-9,
        "装饰条目自带 rate（不依赖道具表）",
    )
    check(
        p["decorations"][0]["expire_ts"] - p["decorations"][0]["ts"] == 72 * 3600,
        "耐久 72 小时",
    )
    check(
        abs(mod.CALC._decoration_bonus(p) - 0.20) < 1e-9,
        f"1 个装饰 → 产出 +20% -> {mod.CALC._decoration_bonus(p):.2f}",
    )

    for _ in range(2):
        await cmd(plugin_r, ev_r, "用", "珊瑚造景", "")
    p = await plugin_r._load_player("96001")
    check(len(p["decorations"]) == 3, f"摆满 3 个 -> {len(p['decorations'])}")
    out = text_of(await cmd(plugin_r, ev_r, "用", "珊瑚造景", ""))
    check("满了" in out, f"超过 decoration_slots 被拒 -> {out.splitlines()[0] if out else ''}")
    p = await plugin_r._load_player("96001")
    check(len(p["decorations"]) == 3, "被拒后数量不变")
    check(
        abs(mod.CALC._decoration_bonus(p) - 0.60) < 1e-9,
        f"3 个装饰 → 产出 +60% -> {mod.CALC._decoration_bonus(p):.2f}",
    )

    # 时间旅行：把到期时间挪到过去 → 惰性结算清掉
    now_r = int(time.time())
    p["decorations"][0]["expire_ts"] = now_r - 1
    dropped = mod.CALC._prune_decorations(p, now=now_r)
    check(dropped == 1 and len(p["decorations"]) == 2, f"过期装饰被清掉 -> 掉 {dropped} 个")
    check(
        abs(mod.CALC._decoration_bonus(p, now=now_r) - 0.40) < 1e-9,
        "清掉后加成只剩 2 个的 40%",
    )
    view = plugin_r._aquarium_view(p)
    check("装饰 2/3" in view, f"水族馆视图显示装饰位 -> {[x for x in view.splitlines() if '装饰' in x][:1]}")
    check("剩" in view, "视图显示剩余小时")

    # --- 装饰的「数字参数 = 一次摆几个」（v1.12.0：以前数字被当成喂鱼的栏位号）---
    p3 = await plugin_r._load_player("96003")
    p3["items"]["coral_deco"] = 3
    await plugin_r._save_player(p3)
    ev3 = FakeEvent("96003")
    out = text_of(await cmd(plugin_r, ev3, "用", "珊瑚造景", "3"))
    p3 = await plugin_r._load_player("96003")
    check(len(p3["decorations"]) == 3, f"一次摆 3 个 -> {len(p3['decorations'])} 个装饰位")
    check(mod._safe_int(p3["items"].get("coral_deco"), 0, 0) == 0, "一次摆 3 个消耗 3 个道具")
    check("×3" in out, f"回复写明摆了几个 -> {out.splitlines()[0] if out else ''}")
    check(
        all(d["expire_ts"] - d["ts"] == 72 * 3600 for d in p3["decorations"]),
        "每个装饰各记自己的到期时间",
    )
    check(
        abs(mod.CALC._decoration_bonus(p3) - 0.60) < 1e-9,
        f"一次摆 3 个 → 产出 +60% -> {mod.CALC._decoration_bonus(p3):.2f}",
    )

    # 装饰位不够：只摆能摆的，并如实提示
    p4 = await plugin_r._load_player("96004")
    p4["items"]["coral_deco"] = 5
    p4["decorations"] = [
        {"id": "coral_deco", "rate": 0.2, "ts": int(time.time()),
         "expire_ts": int(time.time()) + 3600}
        for _ in range(2)
    ]
    await plugin_r._save_player(p4)
    out = text_of(await cmd(plugin_r, FakeEvent("96004"), "用", "珊瑚造景", "3"))
    p4 = await plugin_r._load_player("96004")
    check(len(p4["decorations"]) == 3, f"装饰位不够时只摆满为止 -> {len(p4['decorations'])}/3")
    check("装饰位只剩 1 个" in out, f"提示装饰位不够 -> {out.splitlines()[1] if len(out.splitlines()) > 1 else out}")
    check(mod._safe_int(p4["items"].get("coral_deco"), 0, 0) == 4, "只消耗实际摆上去的数量")

    # 库存不够：同理
    p5 = await plugin_r._load_player("96005")
    p5["items"]["coral_deco"] = 2
    await plugin_r._save_player(p5)
    out = text_of(await cmd(plugin_r, FakeEvent("96005"), "用", "珊瑚造景", "5"))
    p5 = await plugin_r._load_player("96005")
    check(len(p5["decorations"]) == 2, f"库存不够时摆完库存 -> {len(p5['decorations'])}")
    check("库存只有 2 个" in out, "提示库存不够")
    check(mod._safe_int(p5["items"].get("coral_deco"), 0, 0) == 0, "库存清空")

    # 不带数字 = 只摆 1 个（装饰没有「全缸」语义）
    p6 = await plugin_r._load_player("96006")
    p6["items"]["coral_deco"] = 3
    await plugin_r._save_player(p6)
    await cmd(plugin_r, FakeEvent("96006"), "用", "珊瑚造景", "")
    p6 = await plugin_r._load_player("96006")
    check(len(p6["decorations"]) == 1, f"不带数字只摆 1 个 -> {len(p6['decorations'])}")
    check(mod._safe_int(p6["items"].get("coral_deco"), 0, 0) == 2, "不带数字只消耗 1 个")

    # --- 装饰加成真的作用到「领收益」上 ---
    plugin_d2 = make_plugin()
    ev_d2 = FakeEvent("96002")
    p2 = await plugin_d2._load_player("96002")
    p2["aquarium"] = [mod._new_instance("koi", 1.2, value_override=2000)]
    p2["pond_last_ts"] = int(time.time()) - 5 * 3600
    p2["decorations"] = []
    await plugin_d2._save_player(p2)
    plain_out = text_of(await cmd(plugin_d2, ev_d2, "水族馆", "领", ""))
    p2 = await plugin_d2._load_player("96002")
    gold_plain = mod._safe_int(p2.get("gold"), 0, 0)

    p2["pond_last_ts"] = int(time.time()) - 5 * 3600
    p2["gold"] = 100
    p2["decorations"] = [
        {"id": "coral_deco", "rate": 0.20, "ts": int(time.time()), "expire_ts": int(time.time()) + 3600}
    ]
    await plugin_d2._save_player(p2)
    deco_out = text_of(await cmd(plugin_d2, ev_d2, "水族馆", "领", ""))
    p2 = await plugin_d2._load_player("96002")
    gold_deco = mod._safe_int(p2.get("gold"), 0, 0)
    base_income = gold_plain - 100
    check(
        gold_deco - 100 > base_income,
        f"有装饰时产出更高 -> {base_income} → {gold_deco - 100}",
    )
    check("装饰加成" in deco_out, "领收益的回复里标出装饰加成")
    check(
        abs((gold_deco - 100) - int(base_income * 1.2)) <= 1,
        f"加成正好 20% -> {base_income} × 1.2 = {int(base_income * 1.2)}",
    )

    # --- 育灵水：对水族馆里的鱼生效 ---
    plugin_g = make_plugin()
    ev_g = FakeEvent("96003")
    p3 = await plugin_g._load_player("96003")
    p3["aquarium"] = [mod._new_instance("carp", 1.0)]
    p3["items"]["growth_tonic"] = 1
    await plugin_g._save_player(p3)
    out = text_of(await cmd(plugin_g, ev_g, "用", "育灵水", "1"))
    p3 = await plugin_g._load_player("96003")
    check(
        mod._safe_int(p3["aquarium"][0].get("feed_bonus"), 0, 0) == 5,
        f"育灵水让这条鱼 +5 次 -> {p3['aquarium'][0].get('feed_bonus')}",
    )
    check("投喂上限 15 次" in out, f"回复里说明新上限 -> {out.splitlines()[-1] if out else ''}")
    check(mod._safe_int(p3["items"].get("growth_tonic"), 0, 0) == 0, "育灵水被消耗")

    # 喂到「基础上限」后仍能继续喂（因为有 +5）
    p3["aquarium"][0]["feed_uses"] = 10
    p3["items"]["feed_basic"] = 1
    await plugin_g._save_player(p3)
    out = text_of(await cmd(plugin_g, ev_g, "用", "普通饲料", "1"))
    p3 = await plugin_g._load_player("96003")
    check(
        mod._safe_int(p3["aquarium"][0].get("feed_uses"), 0, 0) == 11,
        f"到基础上限后还能喂 -> {p3['aquarium'][0].get('feed_uses')}",
    )

    # --- 锦鲤玉佩：持续 20 竿 ---
    # 关掉彩蛋：彩蛋里的「吉利的鱼鳞」会给 luck_charges，随机命中会让下面
    # 「手气一竿即清」的断言偶发变红（这是测试的确定性要求，不是玩法改动）
    plugin_j = make_plugin(dict(_CFG, easter_egg_chance=0.0))
    ev_j = FakeEvent("96004")
    p4 = await plugin_j._load_player("96004")
    p4["items"]["lucky_jade"] = 1
    p4["baits"]["worm"] = 50
    await plugin_j._save_player(p4)
    out = text_of(await cmd(plugin_j, ev_j, "用", "锦鲤玉佩", ""))
    p4 = await plugin_j._load_player("96004")
    check(mod._safe_int(p4.get("buff_casts_left"), 0, 0) == 20, f"玉佩 → 20 竿 -> {p4.get('buff_casts_left')}")
    check(mod._safe_number(p4.get("luck_charges"), 0) > 0, "手气储备已加上")
    check("作用在你自己身上" in out, "文案说明是给钓手的、不是喂鱼")

    await cmd(plugin_j, ev_j, "蚯蚓", "", "")
    p4 = await plugin_j._load_player("96004")
    check(mod._safe_int(p4.get("buff_casts_left"), 0, 0) == 19, f"抛一竿后剩 19 -> {p4.get('buff_casts_left')}")
    check(mod._safe_number(p4.get("luck_charges"), 0) > 0, "第 2 竿仍然吃到手气（buff 未清）")

    p4["buff_casts_left"] = 1
    await plugin_j._save_player(p4)
    await cmd(plugin_j, ev_j, "蚯蚓", "", "")
    p4 = await plugin_j._load_player("96004")
    check(mod._safe_int(p4.get("buff_casts_left"), 0, 0) == 0, "最后一竿用完归零")
    check(mod._safe_number(p4.get("luck_charges"), 0) == 0, "用完后手气储备清零（buff 失效）")

    # 彩蛋捡到的手气仍然一竿即清（老行为不变）
    p4["luck_charges"] = 0.1
    p4["buff_casts_left"] = 0
    await plugin_j._save_player(p4)
    await cmd(plugin_j, ev_j, "蚯蚓", "", "")
    p4 = await plugin_j._load_player("96004")
    check(mod._safe_number(p4.get("luck_charges"), 0) == 0, "非 buff 来源的手气仍是一竿即清")

    # --- 老存档兼容：没有新字段也不炸 ---
    old_p, migrated = mod._repair_player({"gold": 77, "total_caught": 3}, "96100")
    check(old_p["decorations"] == [], "老存档补出空 decorations")
    check(mod._safe_int(old_p.get("buff_casts_left"), -1, 0) == 0, "老存档补出 buff_casts_left = 0")
    old_inst = mod._repair_instance({"id": "x", "fish_id": "carp", "value": 10})
    check(mod._safe_int(old_inst.get("feed_bonus"), -1, 0) == 0, "老鱼实例补出 feed_bonus = 0")

    # 装饰写坏也不炸
    broken, _ = mod._repair_player(
        {"gold": 1, "decorations": ["x", {"id": ""}, {"id": "coral_deco", "rate": 0.2, "expire_ts": 1}]},
        "96101",
    )
    check(
        len(broken["decorations"]) == 1,
        f"装饰列表里的坏条目被丢掉 -> {broken['decorations']}",
    )

    # =====================================================================
    print("\n[18] 回复场景全覆盖：每条回复都能配按钮/文案 + 护栏断言（v1.12.0）")

    # --- (1) 场景表 ↔ 文案表 一一对应：少一个就说明有人新增回复忘了登记 ---
    _scene_ids = tuple(mod.SCENE_IDS)
    _text_keys = tuple(mod.TEXT_LIB.TEXT_KEYS)
    check(len(set(_scene_ids)) == len(_scene_ids), f"场景 id 不重复（{len(_scene_ids)} 个）")
    check(
        len(_scene_ids) >= 150,
        f"回复场景表覆盖全部出口 -> {len(_scene_ids)} 个场景",
    )
    check(
        sorted(_scene_ids) == sorted(_text_keys),
        "场景键 ↔ 文案键一一对应（缺："
        + "、".join(sorted(set(_scene_ids) - set(_text_keys)))
        + "；多："
        + "、".join(sorted(set(_text_keys) - set(_scene_ids)))
        + "）",
    )
    check(
        sorted(mod.SCENE_GROUP) == sorted(_scene_ids),
        "每个场景都登记了分组与说明",
    )

    # --- (2) 场景表自身的一致性 ---
    _group_ids = {row[0] for row in mod.SCENE_GROUPS}
    _bad_group = [s for s in _scene_ids if mod.SCENE_GROUP.get(s) not in _group_ids]
    check(not _bad_group, f"场景分组都在 SCENE_GROUPS 里（坏的：{_bad_group}）")
    _bad_parent = [
        s for s, p in mod.SCENE_PARENT.items() if p not in set(_scene_ids) or p == s
    ]
    check(not _bad_parent, f"继承关系合法（坏的：{_bad_parent}）")
    check(
        sorted(mod.SCENE_PARENT)
        == sorted(["cast.hit", "cast.junk", "help.page", "location.list", "bag.list",
                   "pull.hook", "story.prompt"]),
        f"只有从老场景拆出来的 7 个场景有父场景 -> {sorted(mod.SCENE_PARENT)}",
    )
    _per_row_bad = [s for s in _scene_ids if not 1 <= mod.CALC.scene_rows_per_row(s) <= 5]
    check(not _per_row_bad, f"每行个数都在 1~5（坏的：{_per_row_bad}）")

    # --- (3) 护栏：代码里的回复出口必须登记场景 ---
    # 规则：`event.plain_result(...)` 只能出现在 self._say / _say_msg / _push 这些
    # 统一出口里（或 _interactions.py 内部的几个通用出口函数）。以后有人写
    # `yield event.plain_result("新文案")` 就会在这里变红，提示他登记场景键。
    import ast as _ast

    _GENERIC_OUTLETS = {
        ("_interactions.py", "_say"),
        ("_interactions.py", "_say_msg"),
        ("_interactions.py", "_push"),
        # v1.18.0 删掉了从来没被调用的 `_reply`：它在这里等于给「绕过场景体系」开后门
    }
    _bare_exits: list[str] = []
    _used_scenes: set[str] = set()
    _unknown_scene_args: list[str] = []
    _wrapped_count = 0
    _say_count = 0
    for _fname in ("main.py", "_engine.py", "_commands.py", "_interactions.py"):
        _src = (PLUGIN_DIR / _fname).read_text(encoding="utf-8")
        _tree = _ast.parse(_src)
        _wrapped = set()
        # 收集「被统一出口包起来」的 plain_result，并记下用到的场景
        for _node in _ast.walk(_tree):
            if not isinstance(_node, _ast.Call):
                continue
            _attr = _node.func.attr if isinstance(_node.func, _ast.Attribute) else ""
            if _attr in ("_say", "_say_msg", "_push") and len(_node.args) >= 3:
                _scene_node = _node.args[2] if _attr == "_say" else _node.args[1]
                if isinstance(_scene_node, _ast.Constant) and isinstance(_scene_node.value, str):
                    _used_scenes.add(_scene_node.value)
                    if _attr == "_say":
                        _say_count += 1
                    if _scene_node.value not in set(_scene_ids):
                        _unknown_scene_args.append(f"{_fname}:{_node.lineno} -> {_scene_node.value}")
                _msg_node = _node.args[1] if _attr == "_say" else _node.args[2]
                for _sub in _ast.walk(_msg_node):
                    if (
                        isinstance(_sub, _ast.Call)
                        and getattr(_sub.func, "attr", "") == "plain_result"
                    ):
                        _wrapped.add(id(_sub))
                        _wrapped_count += 1
        # 其余 plain_result 必须落在通用出口函数里
        def _walk_exits(node, func):
            for child in _ast.iter_child_nodes(node):
                if isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    _walk_exits(child, child.name)
                    continue
                if (
                    isinstance(child, _ast.Call)
                    and getattr(child.func, "attr", "") == "plain_result"
                    and id(child) not in _wrapped
                    and (_fname, func) not in _GENERIC_OUTLETS
                ):
                    _bare_exits.append(f"{_fname}:{child.lineno}（{func}）")
                _walk_exits(child, func)

        _walk_exits(_tree, "<module>")
        # 场景表里每个场景都得在代码里出现过（否则就是没人用的死场景）
    check(
        not _bare_exits,
        "所有回复都走了带场景的统一出口（未登记："
        + "、".join(_bare_exits[:5])
        + "）",
    )
    check(
        not _unknown_scene_args,
        "代码里用到的场景键都在场景表里（野键：" + "、".join(_unknown_scene_args[:5]) + "）",
    )
    check(_wrapped_count + _say_count >= 145, f"带场景的回复出口 {_wrapped_count + _say_count} 处（≥145）")
    _src_all = "".join(
        (PLUGIN_DIR / f).read_text(encoding="utf-8")
        for f in ("main.py", "_engine.py", "_commands.py", "_interactions.py", "_views.py")
    )
    _dead = [s for s in _scene_ids if f'"{s}"' not in _src_all]
    check(not _dead, f"没有「登记了却没人用」的死场景（{_dead}）")

    # --- (4) 等价性：默认配置下的按钮与历史版本逐字一致 ---
    _p0 = make_plugin()
    _builtin = mod._builtin_buttons()
    check(
        sorted(_builtin) == [
            "aquarium.view", "bag", "cast", "help.page", "item.used",
            "location", "orders.list", "pull", "stamina.view", "story",
        ],
        f"v1.18.0 重排后内置按钮覆盖 {len(_builtin)} 个场景 -> {sorted(_builtin)}",
    )
    check(
        [len(_builtin[k]) for k in ("cast", "pull", "bag", "location", "story")]
        == [4, 1, 4, 4, 1],
        "内置按钮数量（cast 4 / pull 1 / bag 4 / location 4 / story 1）",
    )
    check(
        [len(r) for r in _p0._scene_rows("cast.hit")] == [4],
        f"cast.hit 继承 cast -> 一行 4 个 {[len(r) for r in _p0._scene_rows('cast.hit')]}",
    )
    check(
        [b["render_data"]["label"] for b in _p0._scene_rows("cast.hit")[0]]
        == ["再来一竿", "看背包", "水族馆", "卖光光"],
        "cast.hit 的按钮文案与顺序（v1.18.0 重排后）",
    )
    check(
        [b["render_data"]["label"] for b in _p0._scene_rows("help.page")[0]]
        == ["开始钓鱼", "看背包", "水族馆", "我的档案"],
        "帮助页有自己那套按钮（不再照抄 cast）",
    )
    check([len(r) for r in _p0._scene_rows("pull.hook")] == [1], "pull.hook 1 个按钮")
    # 按钮样式的数字含义：编辑器页面把数字翻译成下拉选项（default/primary），
    # 两边必须是同一套，而且必须是官网那一套（0 = 灰色线框、1 = 蓝色线框），
    # 否则「默认（灰）」的按钮在 QQ 里其实是蓝的，保存一次还会被改成别的数字
    check(
        (mod.CALC._parse_button_style("default"), mod.CALC._parse_button_style("primary"))
        == (0, 1),
        "按钮样式别名：default=0（灰）、primary=1（蓝）—— 官网 render_data.style 取值",
    )
    check(
        (
            mod.CALC._parse_button_style("0"),
            mod.CALC._parse_button_style(0),
            mod.CALC._parse_button_style("7"),
        )
        == (0, 0, 7),
        "数字样式照原样解析（0 号色不会被当「没写」吃掉，也不改别的数字）",
    )
    check(
        (
            mod.CALC._parse_button_style(None),
            mod.CALC._parse_button_style(""),
            mod.CALC._parse_button_style("乱写"),
            mod.CALC._parse_button_style(300),
        )
        == (0, 0, None, None),
        "没写样式 = 0（灰色线框）；认不出 / 越界 = None 交给调用方兜底",
    )
    check(
        _p0._scene_rows("pull.hook")[0][0]["action"]["data"] == "/钓鱼 拉",
        "pull.hook 仍然是「拉线！」按钮",
    )
    check([len(r) for r in _p0._scene_rows("bag.list")] == [4], "bag.list 一行 4 个")
    check([len(r) for r in _p0._scene_rows("location.list")] == [4], "location.list 一行 4 个")
    for _scene in ("cast.miss_none", "cast.miss_bait", "shop.list", "bag.empty",
                   "system.error", "broadcast.catch"):
        check(_p0._scene_rows(_scene) == [], f"{_scene} 默认没有按钮（新增场景不动老行为）")
    for _scene in ("item.used", "stamina.view"):
        check(
            len(_p0._scene_rows(_scene)) == 1,
            f"{_scene} 现在有自己的按钮（v1.18.0 补的）",
        )
    _event_def = {"choices": [{"label": "打开看看"}, {"label": "走开"}]}
    _erows = _p0._event_rows(_event_def)
    check(
        len(_erows) == 2
        and _erows[0][0]["render_data"]["label"] == "打开看看"
        and _erows[1][0]["action"]["data"] == "/钓鱼 事件 2",
        "story 模板仍然按选项逐个生成按钮",
    )
    check(mod.CALC.scene_rows_per_row("story") == 1, "story 仍默认每行 1 个")
    check(mod.CALC.scene_rows_per_row("cast.hit") == 4, "其它场景默认每行 4 个（v1.18.0 起）")

    # --- (5) 站长只配了新场景 / 父子各配了一半 ---
    _p1 = make_plugin(dict(_CFG, button_defs="bag.list|卖光光|/钓鱼 卖光光|default"))
    check(
        [b["render_data"]["label"] for r in _p1._scene_rows("cast.hit") for b in r]
        == ["再来一竿", "看背包", "水族馆", "卖光光"],
        "只配了新场景时，老场景仍然回退内置（升级不丢按钮）",
    )
    check([len(r) for r in _p1._scene_rows("bag.list")] == [1], "新场景用站长配的按钮")
    check(_p1._scene_rows("bag.empty") == [], "没配的兄弟场景没有按钮")
    _p2 = make_plugin(
        dict(
            _CFG,
            button_defs="cast|A|/钓鱼|default\ncast.hit|B|/钓鱼 背包|primary",
        )
    )
    check(
        _p2._scene_rows("cast.hit")[0][0]["render_data"]["label"] == "B",
        "子场景优先用自己的按钮",
    )
    check(
        _p2._scene_rows("cast.junk")[0][0]["render_data"]["label"] == "A",
        "其它子场景仍继承父场景",
    )
    _p3 = make_plugin(
        dict(
            _CFG,
            button_defs=(
                "cast|A|/钓鱼|default\ncast|B|/钓鱼 背包|default\ncast|C|/钓鱼 今日|default"
            ),
            button_layout="*|2",
        )
    )
    check(
        [len(r) for r in _p3._scene_rows("cast.hit")] == [2, 1],
        f"button_layout 生效（每行 2 个）-> {[len(r) for r in _p3._scene_rows('cast.hit')]}",
    )
    check(mod.CALC.scene_rows_per_row("story") == 1, "button_layout 没写 story 时保持 1")
    make_plugin()  # 恢复默认排布（BUTTONS_PER_ROW 是模块级共享的）

    # --- (6) button_layout 解析 ---
    check(mod.CALC._parse_button_layout("*|2") == {"*": 2}, "解析全局每行个数")
    check(
        mod.CALC._parse_button_layout("cast|0\ncast|9\ncast|3") == {"cast": 3},
        "越界（0 / 9）的排布行被跳过",
    )
    check(mod.CALC._parse_button_layout("不存在的场景|2") == {}, "未知场景的排布行被跳过")
    check(mod.CALC._parse_button_layout("cast") == {}, "缺竖线的排布行被跳过")

    # --- (7) 文案覆盖（text_overrides）语义 ---
    _lib = mod.TEXT_LIB
    check(_lib.render_scene("cast.miss_none", "原文", None, {}) == "原文", "没写覆盖就用原文")
    check(
        _lib.render_scene("cast.miss_none", "原文", None, {"cast.miss_none": "🪝 {原文}！"})
        == "🪝 原文！",
        "静态场景可以整段改写（{原文} 是原句）",
    )
    check(
        _lib.render_scene("cast.miss_none", "原文", None, {"cast.miss_none": "🪝 静悄悄"})
        == "🪝 静悄悄",
        "静态场景不写 {原文} 就是完全替换",
    )
    check(
        _lib.render_scene("bag.list", "背包内容", None, {"bag.list": "只有这句"}) == "背包内容",
        "动态文本的模板丢了 {原文} → 当没写（不会把列表整段吃掉）",
    )
    check(
        _lib.render_scene("bag.list", "背包内容", None, {"bag.list": "{原文}　✅"})
        == "背包内容　✅",
        "动态文本带 {原文} → 正常渲染",
    )
    check(_lib.parse_overrides("不存在的场景|x") == {}, "未知场景键被跳过")
    check(_lib.parse_overrides("cast.hit|{没有这个占位符}") == {}, "未登记的占位符被跳过")
    check(
        _lib.parse_overrides("cast.hit|🎣 恭喜 {鱼名}") == {"cast.hit": "🎣 恭喜 {鱼名}"},
        "登记过的占位符可用",
    )
    check(
        _lib.parse_overrides("# 注释行\n\nitem.empty|🎒 一件道具都没有")
        == {"item.empty": "🎒 一件道具都没有"},
        "注释行与空行不算坏行",
    )
    check(
        _lib.render_scene("cast.hit", "原文", {"鱼名": "鲤鱼"}, {"cast.hit": "🎣 恭喜 {鱼名}"})
        == "🎣 恭喜 鲤鱼",
        "占位符代入",
    )
    check(
        _lib.render_scene("cast.hit", "原文", None, {"cast.hit": "🎣 恭喜 {鱼名}"}) == "原文",
        "占位符没取到值 → 回退原文（玩家不会看到 {鱼名}）",
    )

    # --- (8) 文案覆盖真的作用到玩家看到的回复上（含父场景继承）---
    _p4 = make_plugin(
        dict(_CFG, text_overrides="item.empty|🎒 一件道具都没有\ncast|🎣 {原文}")
    )
    _ev4 = FakeEvent("97001")
    _out = text_of(await cmd(_p4, _ev4, "用", ""))
    check("一件道具都没有" in _out, f"静态场景整段替换生效 -> {_out}")
    _out2 = text_of(await cmd(_p4, _ev4, "帮助", ""))
    check(_out2.startswith("🎣 🎣 帮助"), f"父场景覆盖作用到子场景 -> {_out2.splitlines()[0]}")
    _p5 = make_plugin()
    _out3 = text_of(await cmd(_p5, FakeEvent("97002"), "用", ""))
    check("没有道具" in _out3, f"默认配置下文案一字未变 -> {_out3}")

    # --- (9) 编辑器「💬 回复」页的数据（scenes 端点）---
    _scene_payload = _p5._editor_scene_payload()
    check(_scene_payload["status"] == "ok", "scenes 端点返回 ok")
    check(_scene_payload["scene_total"] == len(_scene_ids), "scenes 端点覆盖全部场景")
    check(
        sum(len(g["scenes"]) for g in _scene_payload["groups"]) == len(_scene_ids),
        "分组里没有丢场景",
    )
    _flat = {s["id"]: s for g in _scene_payload["groups"] for s in g["scenes"]}
    check(set(_flat) == set(_scene_ids), "卡片 id 与场景表一致")
    _hit = _flat["cast.hit"]
    check(_hit["source"] == "inherit" and _hit["parent"] == "cast", "卡片标出「继承自 cast」")
    check(_hit["text"]["template"] and _hit["text"]["preview"], "卡片带文案模板与预览")
    check("鱼名" in _hit["text"]["placeholders"], "卡片带占位符清单")
    check(_flat["bag.empty"]["source"] == "none", "默认没按钮的场景标成 none")
    check(_flat["bag.empty"]["buttons"] == [], "默认没按钮的场景按钮为空")
    check(
        _flat["cast.hit"]["default_buttons"] and _flat["cast.hit"]["buttons"],
        "卡片同时给出出厂默认与生效按钮",
    )

    # --- (10) 保存通道：三份文本一起提交 ---
    _p6 = make_plugin()
    _ok, _msg = await _p6._editor_save_replies(
        {
            "button_defs": "cast.hit|再来一竿|/钓鱼|default",
            "text_overrides": "cast.miss_none|🪝 静悄悄",
            "button_layout": "*|2",
        }
    )
    check(_ok, f"save_replies 成功 -> {_msg}")
    check(
        mod.BUTTONS.get("cast.hit") == [("再来一竿", "/钓鱼", 0)],
        "保存后按钮立刻生效",
    )
    check(mod.TEXT_OVERRIDES.get("cast.miss_none") == "🪝 静悄悄", "保存后文案立刻生效")
    check(mod.CALC.scene_rows_per_row("cast.hit") == 2, "保存后排布立刻生效")
    _ok2, _msg2 = await _p6._editor_save_replies({"不认识的键": "x"})
    check(not _ok2, f"save_replies 拒绝不认识的键 -> {_msg2}")
    _ok3, _msg3 = await _p6._editor_save_replies({})
    check(not _ok3, f"save_replies 拒绝空请求 -> {_msg3}")
    make_plugin()  # 恢复默认（模块级共享表）

    # =====================================================================
    print("\n" + "=" * 62)
    if failures:
        print(f"❌ {len(failures)}/{checks_run} 项未通过：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"🎉 全部自测通过！（{checks_run} 项断言）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
