"""v1.13.0 回归测试：按钮样式统一 + 效果注册表 + extensions 扩展点。

不属于插件运行时代码。跑法（用 AstrBot 自带的解释器）::

    .venv\\Scripts\\python.exe test_effects_ext.py

铁律：**绝不碰真插件目录的 backups/**。本文件把 ASTRBOT_ROOT 指到临时沙箱、
把 backup_dir 钉在沙箱里，并在开头/结尾对真 backups/ 目录做一次哈希自检。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

_SANDBOX = os.path.join(tempfile.gettempdir(), "astrbot_fishing_ext_test")
os.makedirs(_SANDBOX, exist_ok=True)
os.environ["ASTRBOT_ROOT"] = _SANDBOX

PLUGIN_DIR = Path(__file__).parent
REAL_BACKUPS = PLUGIN_DIR / "backups"

PASS = 0
FAIL: list[str] = []


def check(ok: bool, label: str) -> bool:
    global PASS
    if ok:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL.append(label)
        print(f"  ❌ {label}")
    return bool(ok)


def backups_fingerprint() -> str:
    """真 backups/ 目录的指纹（文件名 + 大小 + mtime），用来确认测试没碰它。"""
    if not REAL_BACKUPS.is_dir():
        return "<没有真 backups 目录>"
    rows = []
    for item in sorted(os.listdir(REAL_BACKUPS)):
        path = REAL_BACKUPS / item
        try:
            stat = path.stat()
            rows.append(f"{item}:{stat.st_size}:{int(stat.st_mtime)}")
        except OSError:
            rows.append(f"{item}:?")
    return hashlib.sha1("|".join(rows).encode("utf-8")).hexdigest()


BEFORE = backups_fingerprint()

spec = importlib.util.spec_from_file_location("astrbot_fishing_ext_test", PLUGIN_DIR / "main.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

FX = mod.EFFECT_REG
CALC = mod.CALC

print("=" * 62)
print("v1.13.0 回归：按钮样式统一 / 效果注册表 / 扩展点")
print("=" * 62)

# =====================================================================
print("\n[1] 效果注册表：内置 8 键与历史白名单逐字一致（升级零变化）")
_LEGACY = ("meat", "spirit", "sheen", "value_up", "decorate", "feed_bonus", "buff_quality", "heal")
check(CALC.EFFECT_ALLOWED == _LEGACY, f"_calc 白名单 == 历史白名单（{len(CALC.EFFECT_ALLOWED)} 键）")
check(tuple(FX.effect_keys()) == _LEGACY, f"注册表键序 == 历史白名单：{FX.effect_keys()}")
check(
    [s.key for s in FX.BUILTIN_EFFECTS] == list(_LEGACY),
    "BUILTIN_EFFECTS 顺序 == 历史白名单（页面上也是这个顺序）",
)
check(
    FX.EFFECT_ALIASES == {"quality_up": "buff_quality"},
    "旧写法 quality_up 仍按 buff_quality 处理",
)
check(
    FX.parse_effects("meat=2;quality_up=0.3;不认识=9;heal=5") ==
    {"meat": 2.0, "buff_quality": 0.3, "heal": 5.0},
    "解析：内置键照常、旧写法转正、未知键跳过",
)
check(FX.parse_effects("") == {} and FX.parse_effects("乱写") == {}, "空串/乱写都返回空（不炸）")
check(
    all(spec.source == FX.BUILTIN_SOURCE for spec in FX.BUILTIN_EFFECTS),
    "内置键的来源都标成「内置」",
)
check(
    all(spec.scope in FX.EFFECT_SCOPES for spec in FX.BUILTIN_EFFECTS),
    "内置键的作用范围都在 EFFECT_SCOPES 里（页面提示不会露英文）",
)

# =====================================================================
print("\n[2] 页面拿到的效果清单来自注册表（页面不再写死）")
_table = FX.effect_table()
check(len(_table) >= 8, f"effect_table() 有 {len(_table)} 行")
check(
    [row["key"] for row in _table][:8] == list(_LEGACY),
    f"前 8 行 == 内置 8 键（实际 {[row['key'] for row in _table][:10]}）",
)
check(
    all({"key", "label", "scope", "scope_label", "unit", "source"} <= set(row) for row in _table),
    "每行都带 键/中文说明/范围/单位/来源（页面直接用）",
)
check("meat" in FX.effect_hint() and "鱼肉" in FX.effect_hint(), "effect_hint() 是页面 tooltip 的文本")

# =====================================================================
print("\n[3] extensions/example_effect.py：随插件一起加载")
_report = FX.load_extensions(str(PLUGIN_DIR))
check(len(_report) >= 1, f"扫到 {len(_report)} 个扩展文件")
_ex = [row for row in _report if row["file"] == "example_effect.py"]
check(bool(_ex) and _ex[0]["ok"], f"example_effect.py 加载成功：{_ex[0] if _ex else '没扫到'}")
check("lucky_token" in FX.EFFECTS and "aroma" in FX.EFFECTS, "扩展声明的两个新效果键已注册")
check(
    FX.EFFECTS["lucky_token"].source == "扩展:example_effect.py",
    f"扩展键的来源标注正确：{FX.EFFECTS['lucky_token'].source}",
)
check("lucky_token" in FX.EXT_HANDLERS, "扩展的处理函数已收进 EXT_HANDLERS")
check(CALC.EFFECT_ALLOWED[-1] in ("lucky_token", "aroma"), "同步后扩展键进了 _calc 白名单")
check(
    FX.parse_effects("lucky_token=0.3") == {"lucky_token": 0.3},
    "扩展键现在能写进 item_defs（解析认它）",
)
check(
    all(row["source"] != FX.BUILTIN_SOURCE for row in FX.effect_table() if row["key"] == "aroma"),
    "扩展键在页面上能看出是扩展带来的",
)

# =====================================================================
print("\n[4] 扩展效果真正生效（含异常隔离）")


class _Stub:
    cfg = {"buff_cast_count": 12}


_player = {"luck_charges": 0.2, "items": {"x": 1}}
_lines = FX.apply_extension_effects(_Stub(), _player, "x", {"lucky_token": 0.3, "meat": 5})
check(len(_lines) == 1 and "幸运符" in _lines[0], f"有处理函数的扩展键被调用：{_lines}")
check(abs(_player["luck_charges"] - 0.5) < 1e-9, f"处理函数能改玩家数据：{_player['luck_charges']}")
check(_player["buff_casts_left"] == 12, "处理函数能复用插件已有字段（buff_casts_left）")
check(
    FX.apply_extension_effects(_Stub(), _player, "x", {"meat": 5}) == [],
    "内置键不会被扩展重复处理（喂鱼仍走原逻辑）",
)

_pocket = {}
_lines = FX.apply_extension_effects(_Stub(), _pocket, "x", {"aroma": 3})
check(_pocket.get("ext_effects", {}).get("aroma") == 3, "声明了但没写处理函数的键：数值记进 ext_effects")
check(len(_lines) == 1, "这类键也会给玩家一行提示（不会静默）")
FX.apply_extension_effects(_Stub(), _pocket, "x", {"aroma": 2})
check(_pocket["ext_effects"]["aroma"] == 5, "同一个键多次使用会累加")


def _boom(**_kw):
    raise RuntimeError("扩展自己炸了")


FX.EXT_HANDLERS["lucky_token"] = _boom
_p2 = {"luck_charges": 0.0}
_lines = FX.apply_extension_effects(_Stub(), _p2, "x", {"lucky_token": 0.3})
check(len(_lines) == 1 and "报错" in _lines[0], f"处理函数抛异常：只提示不崩 -> {_lines}")
check(_p2["luck_charges"] == 0.0, "抛异常时不会留下半截数据")
FX.load_extensions(str(PLUGIN_DIR))       # 复原真扩展

# =====================================================================
print("\n[5] 坏扩展不影响插件（失败隔离）")
# 临时扩展目录放在插件目录里（沙箱可能不允许在系统临时目录里再建子目录），
# 名字以 _ 开头：扩展加载器只扫 extensions/*.py，不会误扫到它；跑完删掉。
_tmp = str(PLUGIN_DIR / "_ext_test_tmp")
_ext_dir = os.path.join(_tmp, "extensions")
shutil.rmtree(_tmp, ignore_errors=True)
os.makedirs(_ext_dir, exist_ok=True)
io.open(os.path.join(_ext_dir, "good_one.py"), "w", encoding="utf-8").write(
    "EFFECTS = {'good_key': '好的扩展'}\nHANDLERS = {}\n"
)
io.open(os.path.join(_ext_dir, "broken.py"), "w", encoding="utf-8").write(
    "EFFECTS = {'bad_key': '写坏的扩展'}\nraise RuntimeError('故意炸')\n"
)
io.open(os.path.join(_ext_dir, "_disabled.py"), "w", encoding="utf-8").write(
    "EFFECTS = {'off_key': '停用的扩展'}\n"
)
io.open(os.path.join(_ext_dir, "not_python.txt"), "w", encoding="utf-8").write("忽略我\n")
_warns: list[str] = []
_report = FX.load_extensions(_tmp, warn=_warns.append)
_by_file = {row["file"]: row for row in _report}
check(len(_report) == 2, f"只扫 .py 且跳过 _ 开头：{[r['file'] for r in _report]}")
check(_by_file["good_one.py"]["ok"], "好的扩展照常加载")
check(not _by_file["broken.py"]["ok"] and "RuntimeError" in _by_file["broken.py"]["error"],
      f"坏的扩展被隔离：{_by_file['broken.py']['error']}")
check(bool(_warns) and "broken.py" in _warns[0], f"坏扩展有告警：{_warns[:1]}")
check("good_key" in FX.EFFECTS, "坏扩展不影响同一个目录里的好扩展")
check("bad_key" not in FX.EFFECTS and "off_key" not in FX.EFFECTS, "坏/停用的扩展没有注册任何键")
check(
    any("broken.py" in line for line in FX.report_lines()),
    f"报告里能看到坏扩展（页面也显示这个）：{FX.report_lines()}",
)
check(FX.load_extensions(str(PLUGIN_DIR)) != [], "重新扫描真扩展目录后恢复正常（重载安全）")
shutil.rmtree(_tmp, ignore_errors=True)
check(not os.path.exists(_tmp), "临时扩展目录已清理（没留在插件目录里）")

# =====================================================================
print("\n[6] 扩展不能覆盖内置效果")
_ok, why = FX.register_effect("meat", "想改掉喂鱼", source="扩展:捣乱的.py")
check(not _ok and "内置" in why, f"覆盖内置键被拒绝：{why}")
check(FX.EFFECTS["meat"].label.startswith("投喂：鱼肉"), "拒绝之后内置说明原样保留")
_ok, _ = FX.register_effect("my_own_key", "我的效果", source="扩展:我的.py")
check(_ok and "my_own_key" in FX.EFFECTS, "新增（不改内置）是允许的")
FX.load_extensions(str(PLUGIN_DIR))

# =====================================================================
print("\n[7] 按钮样式统一设置（默认行为不变 + 统一生效）")
_CFG_DEF = dict(mod.DEFAULTS)
check(_CFG_DEF["button_style_mode"] == "按按钮表", f"默认策略 = 按按钮表（{_CFG_DEF['button_style_mode']}）")
check(_CFG_DEF["button_default_style"] == "default", f"默认兜底样式 = default（{_CFG_DEF['button_default_style']}）")
check(CALC._parse_button_style_mode(None) == "table", "没配置时按按钮表（= 历史行为）")
check(CALC._parse_button_style_mode("统一") == "uniform", "「统一」能识别")
check(CALC._parse_button_style_mode("全部统一") == "uniform", "「全部统一」也能识别")
check(CALC._parse_button_style_mode("乱写") == "table", "乱写回退按按钮表（不会把按钮全变样）")

_rows = CALC._parse_button_defs(mod.DEFAULTS["button_defs"])
check(
    CALC._apply_button_style_policy(_rows, "按按钮表", "primary") == _rows,
    "策略=按按钮表：样式一行都不动（升级后逐字不变）",
)
check(
    CALC._parse_button_defs("pull|拉线|/钓鱼 拉|primary") == {"pull": [("拉线", "/钓鱼 拉", 4)]},
    "没传 default_style 时解析结果 == 历史行为",
)
check(
    CALC._parse_button_defs("pull|拉线|/钓鱼 拉", default_style="primary") == {"pull": [("拉线", "/钓鱼 拉", 4)]},
    "没写样式时用 button_default_style",
)
check(
    CALC._parse_button_defs("pull|拉线|/钓鱼 拉|乱写", default_style="primary") == {"pull": [("拉线", "/钓鱼 拉", 4)]},
    "样式写坏了也用兜底样式",
)
check(
    CALC._parse_button_defs("pull|拉线|/钓鱼 拉", default_style="乱写") == {"pull": [("拉线", "/钓鱼 拉", 1)]},
    "兜底样式也写坏时回到 1（灰）",
)
_uniform = CALC._apply_button_style_policy(_rows, "统一", "primary")
check(
    {style for items in _uniform.values() for _l, _d, style in items} == {4},
    "策略=统一：所有场景所有按钮都换成统一样式",
)
check(
    [(a, b) for a, b, _s in _uniform.get("pull", [])] == [(a, b) for a, b, _s in _rows.get("pull", [])],
    "统一只改样式，文案与指令一个字都不动",
)
check(
    {style for items in CALC._apply_button_style_policy(_rows, "统一", "乱写").values()
     for _l, _d, style in items} == {1},
    "统一样式写坏时回到 1（不会出现非法样式）",
)
check(
    "无" not in str(mod.BUTTON_STYLE_MODE) and mod.BUTTON_DEFAULT_STYLE == "default",
    f"main 里的全局样式设置已初始化：{mod.BUTTON_STYLE_MODE}/{mod.BUTTON_DEFAULT_STYLE}",
)

# =====================================================================
print("\n[8] 编辑器接口把新设置和效果清单发给页面")
_schema = mod.json.loads((PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8-sig"))
check("button_style_mode" in _schema and "button_default_style" in _schema, "两个新配置项都进了 _conf_schema")
check(
    all(_schema[k].get("invisible") for k in ("button_style_mode", "button_default_style")),
    "两个新项在面板里默认隐藏（都在编辑器页面里改）",
)
check(
    all(k in mod.DEFAULTS for k in ("button_style_mode", "button_default_style")),
    "两个新项都在 DEFAULTS 里（有出厂默认值）",
)


class _Bridge(mod.EDITOR_BRIDGE.EditorBridgeMixin, mod.VIEWS.ViewsMixin):
    """编辑器接口的最小宿主：真插件是「视图 + 命令 + 桥」几个 mixin 拼起来的。"""

    def __init__(self) -> None:
        # ⚠️ backup_dir 一定要钉在沙箱里：编辑器接口的保存路径会刷新运行期配置，
        #    历史上就是在这里误碰过真存档目录（见 test_local.py 的同款注释）。
        self.config = dict(
            mod.DEFAULTS,
            backup_dir=os.path.join(_SANDBOX, "plugin_dir", "backups"),
        )
        self.cfg = self.config


def _decode(res: Any) -> dict:
    """web 接口可能直接返回 dict，也可能返回 JSONResponse —— 都解成 dict。"""
    if isinstance(res, dict):
        return res
    raw = getattr(res, "body", None)
    if raw is None:
        raw = getattr(res, "data", None)
    if raw is None and hasattr(res, "get_data"):
        raw = res.get_data()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        return json.loads(raw)
    raise TypeError(f"不认识的返回类型：{type(res)}")


async def _payload() -> dict:
    return await _Bridge().editor_api_config()


async def _scenes() -> dict:
    return await _Bridge().editor_api_scenes()


try:
    _data = _decode(asyncio.run(_payload()))
    check(_data.get("button_style_mode") == "按按钮表", "config 接口带上了当前样式策略（页面读它）")
    check(_data.get("button_default_style") == "default", "config 接口带上了当前兜底/统一样式")
    check(_data.get("effect_keys") and len(_data["effect_keys"]) >= 8,
          f"config 接口带上了效果清单（{len(_data.get('effect_keys') or [])} 行，道具页直接用）")
    check(
        [row["key"] for row in _data["effect_keys"]][:8] == list(_LEGACY),
        "效果清单顺序与注册表一致",
    )
    check(_data.get("effect_hint"), "接口带上了效果列的 tooltip 文本")
    check(bool(_data.get("extensions")), f"接口带上了扩展加载报告：{_data.get('extensions')}")
    # scenes 接口是 {status, message, data} 信封，真正的负载在 data 里
    _sc_raw = _decode(asyncio.run(_scenes()))
    _inner = _sc_raw.get("data")
    _sc = _inner if isinstance(_inner, dict) else _sc_raw
    check(
        [m["value"] for m in _sc.get("button_style_modes") or []] == ["按按钮表", "统一"],
        f"scenes 接口带上了样式策略选项：{[m['value'] for m in _sc.get('button_style_modes') or []]}"
        f"（data 类型 {type(_inner).__name__}，message {_sc_raw.get('message')}）",
    )
    check(_sc.get("button_style_mode") == "按按钮表", "scenes 接口带上了当前策略")
    check(_sc.get("button_default_style") == "default", "scenes 接口带上了当前兜底/统一样式")
    check(len(_sc.get("effect_keys") or []) >= 8, "scenes 接口也带上了效果清单（回复页用）")
except Exception as e:          # pragma: no cover - 真出问题要看得见
    check(False, f"编辑器接口能跑通（{type(e).__name__}: {e}）")

# =====================================================================
print("\n[9] save_replies 能写两个新设置（并且挡住乱写的值）")


async def _save(payload: dict) -> tuple[bool, str]:
    bridge = _Bridge()
    return await bridge._editor_save_replies(payload)


try:
    _ok, _msg = asyncio.run(_save({"button_style_mode": "统一", "button_default_style": "primary"}))
    check(_ok, f"保存样式策略与统一样式：{_msg}")
    _ok, _msg = asyncio.run(_save({"button_style_mode": "乱写"}))
    check(not _ok and "样式策略" in _msg, f"乱写的策略被拒绝：{_msg}")
    _ok, _msg = asyncio.run(_save({"button_default_style": "紫色"}))
    check(not _ok and "样式" in _msg, f"乱写的样式被拒绝：{_msg}")
    _ok, _msg = asyncio.run(_save({"button_layout": "*|2"}))
    check(_ok, f"老三样照旧能写：{_msg}")
except Exception as e:          # pragma: no cover
    check(False, f"save_replies 能跑通（{type(e).__name__}: {e}）")

# =====================================================================
print("\n[10] 数据安全：没有碰真插件目录的 backups/")
AFTER = backups_fingerprint()
check(BEFORE == AFTER, f"真 backups/ 指纹不变（{BEFORE[:12]}）")
check(os.environ.get("ASTRBOT_ROOT") == _SANDBOX, "ASTRBOT_ROOT 指向临时沙箱")
check(str(mod.DEFAULTS.get("backup_dir", "")).find(str(PLUGIN_DIR)) != 0, "DEFAULTS 里的备份目录没有落在插件目录")

# =====================================================================
print("\n" + "=" * 62)
if FAIL:
    print(f"❌ {len(FAIL)} 项未通过：")
    for line in FAIL:
        print("   -", line)
    sys.exit(1)
print(f"✅ v1.13.0 回归全部通过（{PASS} 项）")
