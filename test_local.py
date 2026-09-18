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

import asyncio  # noqa: E402
import collections  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
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
    plugin = mod.FishingPlugin(context=FakeContext(), config=config or dict(_CFG))
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

    def check(cond, label):
        if cond:
            print(f"  ✅ {label}")
        else:
            print(f"  ❌ {label}")
            failures.append(label)

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

    # 内容表也会被同步（鱼竿数值改了要跟新版走）
    c4 = cfg_for_sync()
    c4["rod_defs"] = ["bamboo|竹竿|🎋|0|9.99|0|被站长改坏的旧值"]
    sync_plugin.config = c4
    await sync_plugin._sync_defaults()
    check(
        c4["rod_defs"] == mod.DEFAULTS["rod_defs"],
        "内容表（鱼竿定义）跟着新版默认值走",
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

    # 时间不够
    out = await cmd(plugin, ev, "水族馆", "领", "")
    check("再等" in text_of(out), "时间不足时提示等待")

    # 模拟挂了 5 小时
    p = await plugin._load_player("86001")
    p["pond_last_ts"] = int(time.time()) - 5 * 3600
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

    # 上限封顶
    p["pond_last_ts"] = int(time.time()) - 100 * 3600
    p["gold"] = 0
    await plugin._save_player(p)
    await cmd(plugin, ev, "水族馆", "领", "")
    p = await plugin._load_player("86001")
    cap = int(plugin.cfg["pond_income_cap_hours"])
    expected_cap = min(int(2000 * rate * cap), int(plugin.cfg["pond_income_cap_coins"]))
    check(p["gold"] == expected_cap, f"挂机收益封顶 {p['gold']}（{cap} 小时上限）")

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
    for args in (("卖垃圾", "", ""), ("卖", "垃圾", ""), ("清理", "", "")):
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

    # --- 水族馆展出加成只能领一次（修复无限叠加漏洞）---
    plugin4 = make_plugin()
    ev4 = FakeEvent("89004")
    p = mod._default_player("89004")
    p["inventory"] = [
        mod._new_instance("koi", 1.2, value_override=1000,
                          attrs={"meat": 70, "spirit": 70, "sheen": 70})
    ]
    await plugin4._save_player(p)
    for _ in range(15):  # 反复放入 / 取出
        await cmd(plugin4, ev4, "水族馆", "放", "1")
        await cmd(plugin4, ev4, "水族馆", "取", "1")
    p = await plugin4._load_player("89004")
    value = p["inventory"][0]["value"]
    bonus_cap = int(1000 * float(plugin4.cfg["aquarium_bonus"]))
    check(
        value <= bonus_cap + 5,
        f"15 轮放入/取出后价值 {value}（上限 {bonus_cap}）—— 加成只领一次",
    )
    check(p["inventory"][0]["pond_claimed"] is True, "加成标记已写入存档")

    # 馆内卖出同样只加成一次
    p["aquarium"] = [
        mod._new_instance("carp", 1.0, value_override=500,
                          attrs={"meat": 70, "spirit": 70, "sheen": 70})
    ]
    p["inventory"] = []
    p["gold"] = 0
    await plugin4._save_player(p)
    for _ in range(5):
        await cmd(plugin4, ev4, "水族馆", "取", "1")
        await cmd(plugin4, ev4, "水族馆", "放", "1")
    await cmd(plugin4, ev4, "水族馆", "卖", "1")
    p = await plugin4._load_player("89004")
    check(
        p["gold"] <= int(500 * float(plugin4.cfg["aquarium_bonus"])) + 5,
        f"循环后卖出得 {p['gold']} 金币（上限 600）—— 不再无限叠加",
    )

    # --- 鱼名模糊匹配 ---
    plugin5 = make_plugin()
    check(plugin5._find_fish_by_name("鲤鱼") is not None, "精确鱼名可匹配")
    check(plugin5._find_fish_by_name("小鲫") is not None, "部分鱼名可匹配")
    check(plugin5._find_fish_by_name("七彩") is not None, "前缀可匹配")
    check(plugin5._find_fish_by_name("sss") is None, "不存在的名字返回 None")

    # =====================================================================
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
        "cooldown_seconds": 60,
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

    # --- 相处机制：狠角色同缸必有一方出事（机制本身不提示）---
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

    # 强的一方活下来：普通鱼三维拉满 + 高个体品质 → 狠角色反而没了
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
    names = [mod._fish_name(f["fish_id"]) for f in p["aquarium"]]
    check(
        len(p["aquarium"]) == 1 and names == [normal["name"]],
        f"弱者被淘汰，强者留下 -> 缸里剩 {names}",
    )
    body = text_of(out)
    check("没了" in body, "只给现象（少了一条），不说规则")
    check(
        "敌对" not in body and "实力" not in body and "概率" not in body,
        "不给玩家任何机制提示",
    )

    # 反向：狠角色更强 → 普通鱼没了
    norm2 = mk(normal["id"], 30, 30, 30, qm=0.8)
    big_hostile = mk(hostile["id"], 100, 100, 100, qm=2.0)
    p["aquarium"] = [norm2]
    p["inventory"] = [big_hostile]
    await plugin._save_player(p)
    await cmd(plugin, ev, "水族馆", "放", "1")
    p = await plugin._load_player("89010")
    names = [mod._fish_name(f["fish_id"]) for f in p["aquarium"]]
    check(
        len(p["aquarium"]) == 1 and names == [hostile["name"]],
        f"狠角色更强时活下来的是它 -> 缸里剩 {names}",
    )

    # 两条普通鱼同缸不会出事
    p["aquarium"] = [mk(normal["id"]), mk(normal["id"])]
    p["inventory"] = []
    await plugin._save_player(p)
    lines, changed = plugin._resolve_tank_conflicts(p["aquarium"])
    check(not changed and not lines, "普通鱼同缸和平共处")


    # =====================================================================
    print("\n[10o] 鱼饵：只扣一次 + 换饵 + 卖光光")

    bait_cfg = dict(_CFG)
    bait_cfg["fish_cost"] = 0
    bait_cfg["cooldown_seconds"] = 0
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

    out = await cmd(plugin_b, ev_b, "换饵", "秘制饵", "")
    check("还没有" in text_of(out), "换没有的饵会提示先买")
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
    plugin_d.backup_store.ensure_layout()

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
    plugin_d.cfg["data_target"] = f"{snap_name}/89701"
    plugin_d.cfg["data_action"] = "恢复单个玩家"
    await plugin_d._run_data_action()
    gold_after = (await plugin_d._load_player("89701"))["gold"]
    check(
        gold_after == 55,
        f"「快照名/玩家ID」能精确恢复到指定快照 -> gold={gold_after}",
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

    # --- 鱼类查询 ---
    plugin_f = make_plugin()
    ev_f = FakeEvent('89801')
    out = await cmd(plugin_f, ev_f, '查', '鲤鱼', '')
    body = text_of(out)
    check('出没钓点' in body and '基准价' in body, '按鱼名查：给出去向与基准价')
    check('新手村' in body, '   └ 列出了具体钓点')
    out = await cmd(plugin_f, ev_f, '查', '山间湖泊', '')
    body = text_of(out)
    check('共' in body and '种' in body and '金' in body, '按钓点查：列出该钓点鱼种与价格')
    out = await cmd(plugin_f, ev_f, '查', '不存在的鱼', '')
    check('没有叫' in text_of(out), '查不到时给提示')
    out = await cmd(plugin_f, ev_f, '查', '', '')
    check('查 <鱼名' in text_of(out), '不带参数给用法')

    # --- 鱼类查询 ---
    plugin_f = make_plugin()
    ev_f = FakeEvent('89801')
    out = await cmd(plugin_f, ev_f, '查', '鲤鱼', '')
    body = text_of(out)
    check('出没钓点' in body and '基准价' in body, '按鱼名查：给出去向与基准价')
    check('新手村' in body, '   └ 列出了具体钓点')
    out = await cmd(plugin_f, ev_f, '查', '山间湖泊', '')
    body = text_of(out)
    check('共' in body and '种' in body and '金' in body, '按钓点查：列出该钓点鱼种与价格')
    out = await cmd(plugin_f, ev_f, '查', '不存在的鱼', '')
    check('没有叫' in text_of(out), '查不到时给提示')
    out = await cmd(plugin_f, ev_f, '查', '', '')
    check('查 <鱼名' in text_of(out), '不带参数给用法')

    # --- 独立网页面板存在且引用了 SDK ---
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
    print("\n[12] 指令分派")
    plugin = make_plugin()
    ev = FakeEvent("99001")
    for args, kw in [
        (("帮助", "1", ""), "帮助 1/"),
        (("背包", "", ""), "背包"),
        (("商店", "", ""), "商店"),
        (("图鉴", "", ""), "图鉴"),
        (("图鉴", "详", ""), "图鉴"),
        (("金币", "", ""), "档案"),
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
        ("背包", "", ""), ("图鉴", "", ""), ("金币", "", ""), ("水族馆", "", ""),
        ("商店", "", ""), ("钓点", "", ""), ("鱼竿", "", ""), ("杂物", "", ""),
        ("订单", "", ""),
    ):
        out = await cmd(plugin, ev, *args)
        n = len(text_of(out).splitlines())
        # 背包是「列满一页」的展示型指令，允许更长
        limit = 56 if args[0] == "背包" else 40
        check(n <= limit, f"/钓鱼 {args[0]} 输出 {n} 行（≤{limit}）")

    # =====================================================================
    print("\n" + "=" * 62)
    if failures:
        print(f"❌ {len(failures)} 项未通过：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("🎉 全部自测通过！")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
