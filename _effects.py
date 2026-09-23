"""道具效果注册表 + ``extensions/`` 扩展加载器（v1.13.0）。

这个文件是「效果键 == 功能」的**唯一映射处**。以前同一个效果键散落在四个地方：

1. ``_calc._parse_effects`` 的白名单（哪种写法算合法）
2. ``_calc._apply_feed`` / ``_commands._cmd_use_item`` 的分支（真正干什么）
3. 编辑器页面 ``ITEM_EFFECT_KEYS``（页面上的中文说明）
4. ``_game_data.ITEM_DEFS_DEFAULT`` 的内容行（哪件道具用它）

只要想加一个新效果，就得同时改 4 处，漏一处就出现「页面标红 / 写了没反应」。
v1.13.0 起改成：**只在这里加一行 ``EffectSpec``**，其余三处都从这张表读：

* 解析白名单：``sync_to_calc()`` 把键名同步给 ``_calc``（解析实现仍在 ``_calc``，
  数值写法与历史版本逐字一致，保证升级后行为不变）
* 页面展示：``effect_table()`` 通过插件 Web API 发给编辑器页面（页面不再写死清单）
* 扩展：``extensions/*.py`` 里写 ``EFFECTS`` / ``HANDLERS`` 即可注册新键，
  甚至不必改插件本体（见 ``extensions/example_effect.py``）

设计红线（和插件其它部分一致）：
* **默认行为零变化**：内置 8 个键的名称、含义、处理位置全部照旧，
  默认配置下 ``EFFECT_ALLOWED`` 与历史白名单逐字相同（回归测试卡这一点）
* **坏扩展不能拖垮插件**：加载失败只告警 + 记在报告里，插件照常跑
* 扩展**不能覆盖**内置键（防止站长装个扩展就把喂鱼逻辑改了）
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Callable

#: 内置来源标记（扩展注册的键都会带上「扩展:文件名」，便于页面与日志区分）
BUILTIN_SOURCE = "内置"

#: 效果的作用范围（决定页面上怎么提示站长）
EFFECT_SCOPES: dict[str, str] = {
    "feed": "喂鱼（一次性、永久加成）",
    "decor": "鱼缸装饰（耐久内持续加成挂机产出）",
    "cast": "作用在钓手身上（手气 / 竿数）",
    "ext": "扩展自定义（插件只登记数值，由扩展自己解释）",
}


@dataclass(frozen=True)
class EffectSpec:
    """一个效果键的完整说明（页面上的一行）。"""

    key: str            #: 写进 item_defs 第 6 段的键名，例如 ``meat``
    label: str          #: 中文说明（页面 tooltip / 表格用）
    scope: str = "ext"  #: 见 ``EFFECT_SCOPES``
    unit: str = ""      #: 数值含义，例如「整数」「加成比例」
    handler: str = ""   #: 内置处理位置（注释用，扩展键留空）
    source: str = BUILTIN_SOURCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "scope": self.scope,
            "scope_label": EFFECT_SCOPES.get(self.scope, self.scope),
            "unit": self.unit,
            "handler": self.handler,
            "source": self.source,
            # 旧写法（例如 quality_up）：解析器照收，页面也得跟着认，
            # 否则会误报「插件不认识的效果键」（站长报过）
            "aliases": aliases_of(self.key),
        }


# -----------------------------------------------------------------------------
# 内置效果键（v1.13.0 之前散落在 _calc/_commands/页面里的那 8 个，含义逐字照搬）
#   顺序 = 页面上的展示顺序，也 = _calc 兜底白名单的顺序（回归测试比对「前 8 项」）
#   ⚠️ 新增的内置键**追加在末尾**，不要插进前 8 个里 —— 那会破坏
#   「历史 8 键在前」这条约定（test_effects_ext / test_editor_ui 都会卡）。
#   v1.18.0 新增 quality_reroll（洗髓丹）：站长原来的洗髓丹写的是旧别名 quality_up，
#   效果和锦鲤玉佩一模一样，等于买了个重复道具 —— 现在给它一个自己的效果。
# -----------------------------------------------------------------------------
BUILTIN_EFFECTS: tuple[EffectSpec, ...] = (
    EffectSpec("meat", "投喂：鱼肉 +N（永久）", "feed", "整数", "_calc._apply_feed"),
    EffectSpec("spirit", "投喂：鱼灵 +N（永久）", "feed", "整数", "_calc._apply_feed"),
    EffectSpec("sheen", "投喂：鱼光 +N（永久）", "feed", "整数", "_calc._apply_feed"),
    EffectSpec("value_up", "投喂：估值 +N（永久）", "feed", "整数", "_calc._apply_feed"),
    EffectSpec(
        "decorate", "鱼缸装饰：value 是加成比例，持续 decoration_hours 小时", "decor",
        "比例（0.2 = +20%）", "_commands._cmd_use_item（decorate 分支）",
    ),
    EffectSpec(
        "feed_bonus", "投喂：这条鱼的投喂上限 +N 次", "feed", "整数",
        "_commands._cmd_use_item（feed_bonus 分支）",
    ),
    EffectSpec(
        "buff_quality", "钓手手气：value 是倍率，生效 buff_cast_count 竿", "cast", "倍率",
        "_commands._cmd_use_item（buff_quality 分支）",
    ),
    EffectSpec("heal", "预留：回复体力（插件暂未启用）", "ext", "整数", ""),
    EffectSpec(
        "quality_reroll",
        "洗髓丹：重掷这条鱼的个体品质，取更好的那次（值 = 掷几次）", "feed", "次数",
        "_commands._cmd_use_item（quality_reroll 分支）",
    ),
    EffectSpec(
        "buff_casts", "钓手手气：这件道具持续几竿（省略 = buff_cast_count）", "cast", "竿数",
        "_commands._cmd_use_item（buff_quality 分支）",
    ),
    # v1.18.23：品质保底。站长选了这条方案 —— 手气修好之后「+20% 手气」对收入的影响
    # 只剩 +5% 左右，撑不起 12000 金的大件；改成「接下来 N 竿品质不低于 X」之后
    # 效果一眼能懂、收益也够（玉佩 = 20 竿保底珍品 ≈ +32% 收入）。
    EffectSpec(
        "quality_floor",
        "钓手保底：接下来 N 竿的品质倍率不低于 value（2.0 = 至少珍品、3.5 = 至少绝品）",
        "cast", "倍率", "_commands._cmd_use_item（quality_floor 分支）",
    ),
)

#: 旧写法 -> 正式键名（``quality_up`` 是 v1.9 之前的写法，老配置照常可用）
EFFECT_ALIASES: dict[str, str] = {"quality_up": "buff_quality"}

#: 效果键 -> 说明（运行时会按需重建，扩展注册的键也会进来）
EFFECTS: dict[str, EffectSpec] = {spec.key: spec for spec in BUILTIN_EFFECTS}

#: 扩展注册的处理函数：``效果键 -> handler(plugin, player, item_id, key, value)``
EXT_HANDLERS: dict[str, Callable[..., Any]] = {}

#: 最近一次扩展加载的报告（页面「扩展」区与日志用）
EXT_REPORT: list[dict[str, Any]] = []


# -----------------------------------------------------------------------------
# 注册 / 查询
# -----------------------------------------------------------------------------
def register_effect(
    key: str,
    label: str = "",
    scope: str = "ext",
    unit: str = "",
    handler: str = "",
    source: str = BUILTIN_SOURCE,
) -> tuple[bool, str]:
    """注册（或更新）一个效果键。

    返回 ``(是否成功, 失败原因)``。扩展只能**新增**键：想覆盖内置键会被拒绝，
    这样站长装任何扩展都不会把喂鱼 / 装饰这些既有玩法改掉。
    """
    name = str(key or "").strip()
    if not name:
        return False, "效果键不能为空"
    if not name.replace("_", "").isalnum():
        return False, f"效果键「{name}」只能写字母数字下划线"
    current = EFFECTS.get(name)
    if current is not None and current.source == BUILTIN_SOURCE and source != BUILTIN_SOURCE:
        return False, f"「{name}」是内置效果，扩展不能覆盖"
    if scope not in EFFECT_SCOPES:
        scope = "ext"
    EFFECTS[name] = EffectSpec(
        key=name,
        label=str(label or name).strip(),
        scope=scope,
        unit=str(unit or "").strip(),
        handler=str(handler or "").strip(),
        source=source,
    )
    return True, ""


def register_effects(specs: Any, source: str) -> tuple[list[str], list[str]]:
    """批量注册扩展声明的效果键。

    ``specs`` 支持两种写法（怎么省事怎么写）::

        EFFECTS = {"fragrance": "香气：示例键，插件只登记数值"}
        EFFECTS = {"fragrance": {"label": "香气…", "scope": "ext", "unit": "整数"}}

    返回 ``(成功的键, 被拒绝的原因)``。
    """
    ok: list[str] = []
    bad: list[str] = []
    if not isinstance(specs, dict):
        return ok, ["EFFECTS 必须是 {键: 说明} 形式的字典"]
    for raw_key, spec in specs.items():
        if isinstance(spec, dict):
            fine, why = register_effect(
                raw_key,
                label=spec.get("label") or raw_key,
                scope=spec.get("scope") or "ext",
                unit=spec.get("unit") or "",
                handler=spec.get("handler") or "",
                source=source,
            )
        else:
            fine, why = register_effect(raw_key, label=str(spec or raw_key), source=source)
        if fine:
            ok.append(str(raw_key))
        else:
            bad.append(why)
    return ok, bad


def _reset_extensions() -> None:
    """清掉上一次扩展注册的键与处理函数（重新加载插件时用，内置键不动）。"""
    for key in [k for k, spec in EFFECTS.items() if spec.source != BUILTIN_SOURCE]:
        EFFECTS.pop(key, None)
    EXT_HANDLERS.clear()


def effect_keys() -> list[str]:
    """当前所有合法效果键（内置在前，扩展按注册顺序在后）。"""
    return list(EFFECTS)


def aliases_of(key: str) -> list[str]:
    """这个键的所有旧写法（没有就是空表）。"""
    return sorted(a for a, target in EFFECT_ALIASES.items() if target == key)


def effect_aliases() -> dict[str, str]:
    """全部旧写法 -> 正式键名（页面校验用）。"""
    return dict(EFFECT_ALIASES)


def effect_table() -> list[dict[str, Any]]:
    """页面用的一张表：键 / 中文说明 / 范围 / 来源 / 旧写法。"""
    return [spec.as_dict() for spec in EFFECTS.values()]


def effect_hint() -> str:
    """页面「效果」列的表头提示（一行一个键）。"""
    lines = []
    for spec in EFFECTS.values():
        lines.append(f"{spec.key}　{spec.label}")
        for alias in aliases_of(spec.key):
            lines.append(f"{alias}　（旧写法，等同于 {spec.key}）")
    return "\n".join(lines)


def sync_to_calc(calc_module: Any) -> tuple[int, int]:
    """把注册表同步给 ``_calc``（解析白名单 + 旧写法别名）。

    解析实现仍在 ``_calc._parse_effects``，这里只是把「哪些键合法」告诉它，
    于是扩展注册的键立刻能写进 ``item_defs``。返回 ``(键数, 别名数)``。
    """
    allowed = tuple(effect_keys())
    aliases = dict(EFFECT_ALIASES)
    try:
        calc_module.EFFECT_ALLOWED = allowed
        calc_module.EFFECT_ALIASES_EXT = aliases
    except Exception:       # pragma: no cover - 理论上不会发生（_calc 一定有这两个名字）
        return 0, 0
    return len(allowed), len(aliases)


# -----------------------------------------------------------------------------
# 解析（转发给 _calc，保证数值写法与历史版本逐字一致）
# -----------------------------------------------------------------------------
def calc_module() -> Any:
    """拿到**正在生效**的 ``_calc`` 模块（找不到返回 None）。

    main.py 用 ``_load_sibling`` 把兄弟模块注册成 ``astrbot_fishing_calc``，
    所以必须先查 ``sys.modules``：裸 ``import _calc`` 会另起一个模块实例，
    白名单里没有扩展键，扩展效果就解析不出来了。
    """
    for name in ("astrbot_fishing_calc", "_calc"):
        module = sys.modules.get(name)
        if module is not None:
            return module
    try:
        import _calc as module      # 单测直接导入时走这里
        return module
    except Exception:
        return None


def parse_effects(text: str) -> dict[str, float]:
    """解析 ``meat=2;spirit=1``；未知键跳过。实现见 ``_calc._parse_effects``。"""
    calc = calc_module()
    if calc is None:                # pragma: no cover - 单测里 _calc 一定在
        return {}
    return calc._parse_effects(text)


# -----------------------------------------------------------------------------
# extensions/ 扩展加载
# -----------------------------------------------------------------------------
def extensions_dir(root_dir: str) -> str:
    return os.path.join(str(root_dir or "."), "extensions")


def load_extensions(root_dir: str, *, warn: Any = None) -> list[dict[str, Any]]:
    """扫描 ``extensions/*.py``，读取其中的 ``EFFECTS`` / ``HANDLERS`` 并合并。

    * 文件名以 ``_`` 开头的跳过（想临时停用某个扩展，改个名字就行）
    * 每个文件独立 try/except：坏文件只写进报告 + 告警，插件照常启动
    * 可以重复调用（插件重载时会把上次注册的扩展键清掉再重新加载）
    * 扩展模块以独立名字放进 ``sys.modules``，卸载/重载时不会互相污染

    返回报告：``[{"file", "ok", "keys", "handlers", "error"}, ...]``。
    """
    report: list[dict[str, Any]] = []
    _reset_extensions()
    EXT_REPORT.clear()
    folder = extensions_dir(root_dir)
    if not os.path.isdir(folder):
        return report
    try:
        names = sorted(os.listdir(folder))
    except OSError as e:          # pragma: no cover - 权限异常
        if warn:
            warn(f"extensions 目录读不出来：{e}")
        return report

    for name in names:
        if not name.endswith(".py") or name.startswith("_"):
            continue
        path = os.path.join(folder, name)
        mod_name = "astrbot_fishing_ext_" + os.path.splitext(name)[0]
        row: dict[str, Any] = {"file": name, "ok": False, "keys": [], "handlers": 0, "error": ""}
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("无法创建模块加载器")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module      # 先入表：扩展内部互相 import 才不会炸
            spec.loader.exec_module(module)
            source = f"扩展:{name}"
            keys, bad = register_effects(getattr(module, "EFFECTS", None) or {}, source)
            row["keys"] = keys
            handlers = getattr(module, "HANDLERS", None) or {}
            if isinstance(handlers, dict):
                for key, func in handlers.items():
                    if not callable(func):
                        bad.append(f"{key} 的处理函数不是可调用的")
                        continue
                    EXT_HANDLERS[str(key)] = func
                    row["handlers"] += 1
            if bad:
                row["error"] = "；".join(bad)
            row["ok"] = True
        except Exception as e:
            sys.modules.pop(mod_name, None)
            row["error"] = f"{type(e).__name__}: {e}"
            if warn:
                warn(f"扩展 {name} 加载失败：{row['error']}")
        if row["ok"] and row["error"] and warn:
            warn(f"扩展 {name} 部分内容被跳过：{row['error']}")
        report.append(row)

    EXT_REPORT.extend(report)
    # 顺手把新键同步给 _calc 的解析白名单（主程序也会显式调一次；这里做是为了
    # 单独加载扩展的场景 —— 比如单测、或者站长只想 reload 扩展）
    _sync_to_calc_if_loaded()
    return report


def _sync_to_calc_if_loaded() -> None:
    """如果 _calc 已经加载，就把效果键同步过去（找不到就静默跳过）。"""
    calc = calc_module()
    if calc is not None:
        sync_to_calc(calc)


def apply_extension_effects(
    plugin: Any, player: dict[str, Any], item_id: str, effects: dict[str, float]
) -> list[str]:
    """把「扩展效果键」应用到玩家身上，返回要显示的行（内置键不在这里处理）。

    约定：
    * 有 ``HANDLERS[key]`` -> 调用它（签名 ``(plugin, player, item_id, key, value)``），
      函数可以直接改 ``player``，也可以返回一行文字给玩家看
    * 没有处理函数 -> 数值累加到 ``player["ext_effects"][key]``（扩展之后自己读），
      这样「声明了但还没写逻辑」的键不会静默失效
    * 处理函数抛异常 -> 记日志 + 给玩家一行提示，**不影响**这次使用的其它效果
    """
    lines: list[str] = []
    if not isinstance(effects, dict) or not effects:
        return lines
    for key, value in effects.items():
        spec = EFFECTS.get(key)
        if spec is None or spec.source == BUILTIN_SOURCE:
            continue                      # 内置键走插件原本的实现，这里不插手
        label = spec.label or key
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        handler = EXT_HANDLERS.get(key)
        if handler is None:
            pocket = player.setdefault("ext_effects", {})
            try:
                pocket[key] = round(float(pocket.get(key) or 0.0) + number, 6)
            except (TypeError, ValueError):
                pocket[key] = number
            lines.append(f"　{label}")
            continue
        try:
            message = handler(plugin=plugin, player=player, item_id=item_id, key=key, value=number)
        except Exception as e:
            _log_error(f"扩展效果「{key}」处理失败：{type(e).__name__}: {e}")
            _log_error(traceback.format_exc())
            lines.append(f"　{label}：扩展报错，已跳过（{type(e).__name__}）")
            continue
        if message:
            lines.append(str(message))
    return lines


def report_lines() -> list[str]:
    """把加载报告整理成人看的几行（启动日志 / 页面「扩展」区）。"""
    if not EXT_REPORT:
        return ["没有 extensions/*.py（可以照 extensions/example_effect.py 自己加）"]
    out: list[str] = []
    for row in EXT_REPORT:
        head = f"{'✅' if row['ok'] else '❌'} {row['file']}"
        if row["ok"]:
            head += f"：效果键 {len(row['keys'])} 个、处理函数 {row['handlers']} 个"
        if row["error"]:
            head += f"（{row['error']}）"
        out.append(head)
    return out


def log_info(message: str) -> None:
    """容错日志：拿不到 AstrBot logger 时退回标准 logging。"""
    _log("info", message)


def log_error(message: str) -> None:
    """容错日志（错误级别）。"""
    _log("error", message)


def _log(level: str, message: str) -> None:
    try:
        from astrbot.api import logger as _logger
    except Exception:                 # pragma: no cover - 仅单测环境
        import logging
        _logger = logging.getLogger(__name__)
    try:
        getattr(_logger, level)(message)
    except Exception:                 # pragma: no cover - logger 被换掉的极端情况
        pass


def _log_error(message: str) -> None:
    """内部用：错误级别日志。"""
    _log("error", message)
