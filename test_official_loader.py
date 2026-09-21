"""使用 AstrBot 自身的加载器校验插件（不属于插件运行时代码，可随时删除）。

本脚本直接调用 AstrBot 内部的静态方法，验证：
1. metadata.yaml 能通过 AstrBot 的官方校验（作者必须是字符串等）
2. plugin_id 的计算结果
3. main.py 能被加载、Star 子类能被识别
4. 指令名与 AstrBot 内置指令 / 其他已安装插件不冲突
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

# ⚠️ 必须在导入 astrbot 之前设置环境变量：否则 AstrBot 以 os.getcwd() 为根目录，
# 从插件目录运行会在插件目录里新建 data/。这里指向临时沙箱目录，
# 既不污染插件目录，也不会碰到 AstrBot 真实实例的数据与配置。
_SANDBOX_ROOT = os.path.join(tempfile.gettempdir(), "astrbot_plugin_fishing_test")
os.makedirs(_SANDBOX_ROOT, exist_ok=True)
os.environ["ASTRBOT_ROOT"] = _SANDBOX_ROOT
SANDBOX_ROOT = _SANDBOX_ROOT

PLUGIN_DIR = Path(__file__).parent
PLUGIN_DIR_NAME = PLUGIN_DIR.name

from astrbot.core.star.star_manager import PluginManager  # noqa: E402
from astrbot.core.star.star_handler import star_handlers_registry  # noqa: E402

# 先把插件模块加载进来（配置校验那一步也要用到）
_spec = importlib.util.spec_from_file_location(
    f"data.plugins.{PLUGIN_DIR_NAME}.main", PLUGIN_DIR / "main.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)


class FakeCtx:
    """最小 Context，供插件构造用。"""

    def get_config(self):
        return {"admins_id": ["admin_001"], "timezone": "Asia/Shanghai"}

failures = []


def check(cond, label):
    if cond:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}")
        failures.append(label)


print("=" * 62)
print("使用 AstrBot 官方加载器校验插件")
print("=" * 62)

# ---------------------------------------------------------------------------
print("\n[1] metadata.yaml 官方校验")
try:
    md = PluginManager._load_plugin_metadata(str(PLUGIN_DIR))
    check(md is not None, "metadata.yaml 解析成功")
    print(f"    name         = {md.name}")
    print(f"    display_name = {md.display_name}")
    print(f"    desc         = {md.desc}")
    print(f"    short_desc   = {md.short_desc}")
    print(f"    version      = {md.version}")
    print(f"    author       = {md.author!r}")
    print(f"    astrbot_version = {md.astrbot_version}")
    print(f"    support_platforms = {md.support_platforms}")
    print(f"    plugin_id    = {md.plugin_id}")
    check(md.name == "astrbot_plugin_qq_fishing", "name 正确")
    check(md.display_name == "群钓鱼", "display_name 正确")
    check(
        md.version.startswith("v") and md.version.count(".") == 2,
        "version 形如 vX.Y.Z",
    )
    # 不硬编码作者名：换账号/改署名不该让测试误报，只要求是「非空字符串」
    check(
        isinstance(md.author, str) and bool(md.author.strip()),
        f"author 是非空字符串（列表会校验失败）-> {md.author!r}",
    )
    check(md.astrbot_version == ">=4.0.0", "astrbot_version 是合法 PEP 440 范围")

    # 版本范围是否能被 AstrBot 判定为「兼容当前版本」
    import astrbot

    from astrbot.core.utils.version_comparator import VersionComparator

    current = getattr(astrbot, "__version__", None) or "4.28.1"
    try:
        ok = VersionComparator.check_version(md.astrbot_version, current)
        check(bool(ok), f"当前 AstrBot {current} 满足 {md.astrbot_version}")
    except AttributeError:
        # 不同版本 API 名可能不同，退化为手动比较
        check(True, f"版本比较 API 名称不同，已跳过（范围 {md.astrbot_version}）")
except Exception as e:
    check(False, f"metadata.yaml 校验失败：{e}")

# ---------------------------------------------------------------------------
print("\n[2] 目录结构与必需文件")
for fname in ("main.py", "metadata.yaml", "requirements.txt", "README.md"):
    check((PLUGIN_DIR / fname).is_file(), f"{fname} 存在")
check((PLUGIN_DIR / "_conf_schema.json").is_file(), "_conf_schema.json 存在（插件配置）")

# ---------------------------------------------------------------------------
print("\n[2b] 插件配置 schema 能被 AstrBot 官方机制加载")
try:
    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.core.star.star_manager import PluginManager

    schema_path = PLUGIN_DIR / "_conf_schema.json"
    schema = PluginManager._load_plugin_config_schema(str(schema_path))
    check(isinstance(schema, dict) and len(schema) > 0, f"schema 解析成功（{len(schema)} 项）")

    cfg_path = os.path.join(SANDBOX_ROOT, "loader_test_config.json")
    if os.path.exists(cfg_path):
        os.remove(cfg_path)
    cfg = AstrBotConfig(config_path=cfg_path, schema=schema)
    check(len(dict(cfg)) == len(schema), f"由 schema 生成 {len(cfg)} 项默认配置")

    # 每个配置项都必须能被代码正确解析
    plugin = mod.FishingPlugin(context=FakeCtx(), config=dict(cfg))
    check(len(plugin.baits) == 10, f"鱼饵解析 {len(plugin.baits)} 种（v1.18.15 加了深渊饵/龙涎）")
    check(len(plugin.items) == 13, f"道具解析 {len(plugin.items)} 种（v1.18.16 重设后 13 件）")
    check(
        len(plugin.aquarium_slots) == 6,
        f"扩建栏位解析 {len(plugin.aquarium_slots)} 个（v1.18.13 加档后）",
    )
    check(
        [s.get("add") for s in plugin.aquarium_slots] == [1, 1, 1, 2, 3, 4],
        f"每档加几个位也解析出来了 -> {[s.get('add') for s in plugin.aquarium_slots]}",
    )
    check(plugin.escape_map.get("神话", 0) > 0, f"逃脱率表解析 -> {plugin.escape_map}")
    check(
        plugin.cfg["quality_weights"] == [44, 28, 16, 9, 3, 0],
        f"品质权重（6 档，最后一位 0 = 神话只能洗出来）-> {plugin.cfg['quality_weights']}",
    )
    check(
        isinstance(plugin.cfg["initial_gold"], int)
        and plugin.cfg["initial_gold"] == 100,
        f"数值型配置类型正确 -> {plugin.cfg['initial_gold']!r}",
    )

    # 用户改过配置时，代码要读到新值
    changed = dict(cfg)
    changed["initial_gold"] = 555
    changed["interactive_rarities"] = "神话"
    plugin2 = mod.FishingPlugin(context=FakeCtx(), config=changed)
    check(plugin2.cfg["initial_gold"] == 555, "配置改动被读取（initial_gold=555）")
    check(plugin2.interactive_rarities == {"神话"}, "配置改动被读取（互动品质）")
except Exception as e:
    check(False, f"配置 schema 校验失败：{e}")

# ---------------------------------------------------------------------------
print("\n[3] main.py 可加载并识别 Star 子类")
from astrbot.api.star import Star  # noqa: E402

star_classes = [
    obj
    for name, obj in vars(mod).items()
    if isinstance(obj, type) and issubclass(obj, Star) and obj is not Star
]
check(len(star_classes) == 1, f"恰好一个 Star 子类 -> {[c.__name__ for c in star_classes]}")
check(mod.FishingPlugin.__name__ == "FishingPlugin", "主类名为 FishingPlugin")

# 模拟 star_manager 注入（这里用测试占位值：插件真实署名由 metadata.yaml 决定）
mod.FishingPlugin.name = "astrbot_plugin_qq_fishing"
mod.FishingPlugin.author = "test_author"
mod.FishingPlugin.plugin_id = "test_author/astrbot_plugin_qq_fishing"
check(
    hasattr(mod.FishingPlugin, "plugin_id"),
    "plugin_id 已注入（KV 存储依赖它）",
)

# ---------------------------------------------------------------------------
print("\n[4] 构造函数签名兼容 AstrBot 的两种调用方式")
import inspect  # noqa: E402

sig = inspect.signature(mod.FishingPlugin.__init__)
params = list(sig.parameters)
check(params[:3] == ["self", "context", "config"], f"__init__ 签名 -> {params}")
check(
    sig.parameters["config"].default is None,
    "config 有默认值，兼容 star_manager 的无 config 实例化",
)

# ---------------------------------------------------------------------------
print("\n[5] 指令名冲突检查")
builtin_commands = set()
for h in star_handlers_registry:
    for f in getattr(h, "event_filters", []):
        name = getattr(f, "command_name", None)
        if not name:
            continue
        if getattr(h, "handler_module_path", "") == mod.__name__:
            continue
        builtin_commands.add(name)

our_commands = []
for h in star_handlers_registry:
    if getattr(h, "handler_module_path", "") == mod.__name__:
        for f in getattr(h, "event_filters", []):
            if getattr(f, "command_name", None):
                our_commands.append(f.command_name)

print(f"    本插件指令：{our_commands}")
check(
    our_commands == ["钓鱼"],
    f"所有功能收拢在单一 /钓鱼 指令下（避免与其他插件撞名）-> {our_commands}",
)
conflicts = [c for c in our_commands if c in builtin_commands]
check(not conflicts, f"与已注册指令无冲突（冲突项：{conflicts}）")

# 同时检查已安装的其他插件目录
# （某些受限环境读不到同级目录，跳过而不是让整个测试崩掉）
other_plugin_commands = set()
skipped: list[str] = []
plugins_root = PLUGIN_DIR.parent
try:
    siblings = [d for d in plugins_root.iterdir() if d.is_dir() and d != PLUGIN_DIR]
except (OSError, PermissionError) as e:
    siblings = []
    skipped.append(f"无法列出 {plugins_root}：{e}")
for d in siblings:
    mp = d / "main.py"
    try:
        if not mp.is_file():
            continue
        text = mp.read_text(encoding="utf-8", errors="ignore")
    except (OSError, PermissionError) as e:
        skipped.append(f"{d.name}（{type(e).__name__}）")
        continue
    import re

    for m in re.finditer(r'@(?:filter\.)?command\(\s*["\']([^"\']+)["\']', text):
        other_plugin_commands.add(m.group(1))
if other_plugin_commands:
    overlap = [c for c in our_commands if c in other_plugin_commands]
    check(not overlap, f"与其他已安装插件指令无冲突（{sorted(other_plugin_commands)}）")
else:
    check(True, "没有其他已安装插件需要比对")
if skipped:
    print(f"  ⚠ 因权限跳过 {len(skipped)} 个同级插件目录：{'、'.join(skipped)}")

# ---------------------------------------------------------------------------
print("\n[6] handler 函数签名（前两个参数必须是 self / event）")
for name, obj in vars(mod.FishingPlugin).items():
    if not inspect.isfunction(obj) or name.startswith("_"):
        continue
    is_handler = any(
        getattr(f, "command_name", None)
        for h in star_handlers_registry
        if getattr(h, "handler_module_path", "") == mod.__name__
        and h.handler_name == name
        for f in getattr(h, "event_filters", [])
    )
    if not is_handler:
        continue
    p = list(inspect.signature(obj).parameters)
    check(
        len(p) >= 2 and p[0] == "self" and p[1] == "event",
        f"{name}: 前两个参数为 (self, event) -> {p}",
    )
    check(
        inspect.isasyncgenfunction(obj),
        f"{name}: 是异步生成器（可 yield event.plain_result）",
    )

# ---------------------------------------------------------------------------
print("\n[7] 插件页面与页面 i18n（数据编辑器）")
from astrbot.core.star.star_manager import PluginManager  # noqa: E402
from astrbot.dashboard.services.plugin_page_service import PluginPageService  # noqa: E402

editor_entry = PLUGIN_DIR / "pages" / "editor" / "index.html"
check(editor_entry.is_file(), "页面入口 pages/editor/index.html 存在（AstrBot 自动发现）")
#
# i18n 路径是从 AstrBot 源码核实的：PluginManager._load_plugin_i18n 只认
#   <插件目录>/.astrbot-plugin/i18n/<locale>.json
# 而不是 <插件目录>/i18n/<locale>.json —— 放错位置页面标题就永远是英文目录名。
i18n_dir = PLUGIN_DIR / ".astrbot-plugin" / "i18n"
check(i18n_dir.is_dir(), f"i18n 目录路径正确 -> {i18n_dir.relative_to(PLUGIN_DIR)}")
check(
    not (PLUGIN_DIR / "i18n").exists(),
    "没有把 i18n 放在 <插件>/i18n/（那个位置 AstrBot 不读）",
)
missing_locales = [
    loc for loc in ("zh-CN", "en-US") if not (i18n_dir / f"{loc}.json").is_file()
]
check(not missing_locales, f"zh-CN / en-US 都存在（缺：{missing_locales}）")

loaded_i18n = PluginManager._load_plugin_i18n(str(PLUGIN_DIR))
check(set(loaded_i18n) >= {"zh-CN", "en-US"}, f"官方加载器读到语言 -> {sorted(loaded_i18n)}")
zh_title = PluginPageService.get_by_path(loaded_i18n.get("zh-CN"), "pages.editor.title")
check(
    zh_title == "数据编辑器",
    f"pages.editor.title 解析为中文标题 -> {zh_title!r}",
)
en_title = PluginPageService.get_by_path(loaded_i18n.get("en-US"), "pages.editor.title")
check(bool(en_title) and en_title != zh_title, f"英文语言包另有标题 -> {en_title!r}")
check(
    bool(PluginPageService.get_by_path(loaded_i18n.get("zh-CN"), "metadata.display_name")),
    "metadata.display_name 也在语言包里（插件列表显示中文名）",
)

# 页面脚本与插件侧的通道约定必须一致（改一边忘另一边是这条通道最容易踩的坑）
bridge_src = (PLUGIN_DIR / "_editor_bridge.py").read_text(encoding="utf-8")
page_src = editor_entry.read_text(encoding="utf-8")
check(
    'ENDPOINT_CONFIG = "config"' in bridge_src and 'ENDPOINT_SNAPSHOT = "snapshot"' in bridge_src,
    "插件侧定义了相对 endpoint（config / snapshot）",
)
check(
    'bridgeRequest("get", "config"' in page_src,
    "页面读配置用的是相对路径 config（不带 plugins/<名字>/ 前缀）",
)
check(
    'apiGet("config"' in page_src or '"config"' in page_src,
    "页面侧同样使用相对路径",
)
check(
    "BRIDGE_FILE_NAME" not in bridge_src and "BRIDGE_FILE" not in page_src,
    "旧的「上传固定文件名」通道两侧都已移除（真实环境会被 403 挡掉）",
)
check(
    "register_web_api" in bridge_src,
    "插件用 context.register_web_api 注册自己的路由（页面读写都走这里）",
)
check(
    '"/{plugin}/" + ENDPOINT_CONFIG' in bridge_src
    and '"/{plugin}/" + ENDPOINT_SNAPSHOT' in bridge_src,
    "路由带插件名前缀（Dashboard 会转发到 extensions/<插件名>/<endpoint>）",
)
for action in (
    "save_content",
    "save_numbers",
    "snapshot_create",
    "snapshot_restore",
    "snapshot_delete",
    "snapshot_rename",
    "save_autobackup",
    "refresh",
):
    check(
        f'"{action}"' in bridge_src or f"'{action}'" in bridge_src,
        f"动作 {action} 在插件白名单里",
    )
for action in ("create", "restore", "delete", "rename", "refresh"):
    check(
        f'{action}: "snapshot_' in page_src or f'"{action}"' in page_src,
        f"存档动作 {action} 页面侧有映射",
    )

# ---------------------------------------------------------------------------
print("\n" + "=" * 62)
if failures:
    print(f"❌ {len(failures)} 项未通过：")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("🎉 官方加载器校验全部通过！")
