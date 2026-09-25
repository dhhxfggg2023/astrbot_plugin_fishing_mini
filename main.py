"""AstrBot QQ 群钓鱼小游戏插件（深度养成版）。

所有功能都挂在 ``/钓鱼`` 这一条指令下，避免与其他插件撞名：

- ``/钓鱼``              下竿（每次消耗 1 点体力，体力会随时间攒回来）
- ``/钓鱼 10``           连钓 10 次（扣 10 点体力 + 10 个饵）
- ``/钓鱼 拉``           拉线（只有最高两档鱼种需要，见「互动玩法」）
- ``/钓鱼 背包``         背包
- ``/钓鱼 卖 …``         卖鱼
- ``/钓鱼 图鉴``         收集进度
- ``/钓鱼 水族馆 …``     水族馆 / 投喂 / 领取收益 / 扩建
- ``/钓鱼 鱼竿 …``       鱼竿店（买竿 / 换竿）
- ``/钓鱼 道具 …``       道具店（饲料 / 仙露 / 育灵水 / 洗髓丹 / 玉佩 / 造景）
- ``/钓鱼 鱼饵 …``       鱼饵店（面包屑 / 蚯蚓 / 红虫 / 玉米粒…）
  （v1.18.13 起 ``/钓鱼 商店`` 已拆成上面三家，老写法只会回一句指路）
- ``/钓鱼 体力``         看体力
- ``/钓鱼 档案 / 签到``  个人档案与签到
- ``/钓鱼 帮助``         简洁说明

================================ 数值设计 ================================

【两套独立的「品质」概念，别混淆】

1. **鱼种品质（固有属性，不可改变）**
   常见 < 少见 < 稀有 < 传说 < 神话，共 5 档。
   钓到的那一刻就定了，任何道具都改不了，决定基础价值区间与稀有度。

2. **个体品质（可提升）**
   ⚪凡品 🟢良品 💎精品 🏆珍品 👑绝品 🔱神品，共 6 档，
   （故意和鱼种稀有度「常见/少见/稀有/传说/神话」用两套名字，免得混淆）
   由「品质加成倍率 quality_mult」决定，影响售价倍率。
   投喂饲料不会改变它（饲料只加个体数值），
   但「锦鲤玉佩」可以提高掷出好个体的概率——所以个体是可以养出来的。
   **神品**是例外：自然上钩永远掷不到（quality_weights 里权重 0），
   只能靠洗髓丹洗出来（概率 quality_myth_chance，默认每次重掷 0.25%）；
   同一条鱼每天最多吃 reroll_daily_limit 颗（**默认 0 = 不限**，填 N 则吃满当天会「厌恶」）。

【个体三维数值】
   肉质 meat / 灵性 spirit / 光泽 sheen，0~100，钓上来时按鱼种品质随机生成。
   售价 = 基准价 x 属性系数 x 个体品质倍率 x 个体差异
   属性系数 = 1 + (肉质x0.45 + 灵性x0.30 + 光泽x0.25 - 60)/100
   所以「同一条鱼」也会因为三维不同而价格差很多，投喂能实打实涨价。

【互动玩法：只有最高两档需要拉线】
   常见/少见/稀有 —— 直接上钩，不折腾玩家。
   传说/神话     —— 咬钩后进入拉线窗口：
     - 每条鱼的窗口时长、最佳点位、逃脱率都不同（由 difficulty 决定）
     - 窗口内有「完美区间」，落点越靠中间评价越高：完美 > 良好 > 偏差
     - 超时 = 鱼跑了（这一竿的鱼饵已经用掉了）

================================ 数据存储 ================================

插件级 KV 存储（``get_kv_data`` / ``put_kv_data``），key = ``player_<user_id>``，
每个玩家一条独立记录，底层落在 AstrBot 的 SQLite，重启不丢。
旧版本数据（v1/v2/v3）在首次读取时自动迁移。

================================ 可配置项 ================================

``_conf_schema.json`` 里的所有项都能在 WebUI「插件 → 群钓鱼 → 配置」里直接改，
改完点重载即可生效。代码里读取配置统一走 ``self.cfg``。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import random
import sys
import time
import uuid
import weakref
import zlib
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


# =============================================================================
# 模块化：加载拆出去的兄弟模块
# =============================================================================
#
# 说明（两个方向都要通）：
#   * main → 兄弟模块：_load_sibling 按路径加载并注册 sys.modules；
#     纯函数模块（_calc）的成员直接注入 main 的命名空间，因此几十处调用点
#     一行都不用改。
#   * 兄弟模块 → main：模块末尾 _expose_globals_all() 把 main 的全局
#     （常量、工具函数）注入各 mixin 模块，拆出去的方法可以照常引用它们。


def _load_sibling(name: str, alias: str) -> Any:
    """按路径加载同目录模块；失败返回 None（插件其余功能照常可用）。"""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{name}.py")
        spec = importlib.util.spec_from_file_location(alias, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法为 {name}.py 创建加载器")
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module  # 先注册再执行，模块内可互相引用
        spec.loader.exec_module(module)
        return module
    except Exception as e:  # pragma: no cover
        logger.warning(f"模块 {name}.py 加载失败，相关功能降级：{e}")
        return None


CALC: Any = _load_sibling("_calc", "astrbot_fishing_calc")
if CALC is not None:
    # 把纯函数搬进 main 的命名空间（调用点保持原样）
    globals().update(
        {key: value for key, value in vars(CALC).items() if not key.startswith("__")}
    )

DATA_ADMIN: Any = _load_sibling("_data_admin", "astrbot_fishing_data_admin")
VIEWS: Any = _load_sibling("_views", "astrbot_fishing_views")
COMMANDS: Any = _load_sibling("_commands", "astrbot_fishing_commands")
INTERACTIONS: Any = _load_sibling("_interactions", "astrbot_fishing_interactions")
ENGINE: Any = _load_sibling("_engine", "astrbot_fishing_engine")
EDITOR_BRIDGE: Any = _load_sibling("_editor_bridge", "astrbot_fishing_editor_bridge")
#: 效果注册表 + 扩展加载器（v1.13.0）：效果键的**唯一**映射处，详见 _effects.py
EFFECT_REG: Any = _load_sibling("_effects", "astrbot_fishing_effects")
#: 文案表（text_overrides 的默认值与渲染规则）；加载失败时下面的兜底会顶上
TEXT_LIB: Any = _load_sibling("_texts", "astrbot_fishing_texts")
#: 旧作用域数据找回（作者名改过 -> plugin_id 变过 -> 老存档留在别的 scope 里）
LEGACY: Any = _load_sibling("_legacy", "astrbot_fishing_legacy")
#: 大鱼乐彩票的奖表解析 / 抽奖 / 期望值模型（v1.18.51）
LOTTERY: Any = _load_sibling("_lottery", "astrbot_fishing_lottery")
#: 玩家数据编辑器（v1.18.56）：全字段清单 + 逐条改鱼 + 原始 JSON
EDITOR_PLAYER: Any = _load_sibling("_editor_player", "astrbot_fishing_editor_player")


class _MissingMixin:
    """兄弟模块加载失败时的占位基类（保证插件仍能启动）。"""


class PlayerLoadError(Exception):
    """严格读档失败（``_load_player(strict=True)``）。

    存在的唯一理由：**读档失败绝不能被当成「新玩家」然后写回去**。
    KV 读是可能瞬时失败的，而瞬时失败 + 随后的 `_save_player` = 玩家存档被空账号覆盖。
    会写回的路径（抛竿 / 连钓 / 拉线）一律用 strict 读，失败就中止这次操作。
    """



# =============================================================================
# 一·零、子命令写法总表（命令别名 / 自定义命令的校验依据）
# =============================================================================
#
# 这张表是「玩家能打出来的子命令写法」的**唯一清单**，用途有三：
#   1. 生成 command_aliases 的默认值（站长照着它加别名）
#   2. 校验配置：别名不许抢占内置写法、`执行:` 只能指向内置子命令
#   3. README 里那份「当前可用子命令清单」就是它
#
# ⚠️ 真正干活的分派逻辑仍在 fishing() 里（PULL_WORDS / 帮助 / CAST_WORDS /
#    钓点 / elif 分派链），这张表只是它的**镜像**：
#    * 行的顺序 = 实际匹配优先级
#    * 同一个写法只写在**第一张**认它的行里（例如 `fish` 属于「下竿」，
#      所以「查」那行没有它 —— 这就是真实行为，不是漏写）
#    * test_local.py 会逐个写法实跑一遍，确认不会回「不认识」，以此卡住两者一致
SUBCOMMAND_KEYWORDS: dict[str, tuple[str, ...]] = {
    # ---- 分派之前就处理掉的写法（先匹配先生效）----
    "拉": ("拉", "拉线", "收", "收线", "提", "提竿", "拽", "pull", "p"),
    "帮助": ("帮助", "help", "?", "？", "菜单", "指令"),
    "下竿": (
        "下竿", "钓", "钓鱼", "抛竿", "甩竿", "下钩", "抛", "钓鱼吧",
        "cast", "fish", "fishing",
    ),
    "去": ("去", "前往", "go"),
    # ---- 子命令分派链（顺序与 fishing() 里的 elif 完全一致）----
    "背包": ("背包", "包", "bag", "鱼篓"),
    "卖": (
        "卖", "卖鱼", "sell", "卖垃圾", "卖光光", "卖光", "清空", "全卖",
        "空背包", "sellall", "一键卖出",
    ),
    "图鉴": ("图鉴", "收集", "collection"),
    # v1.18.30：把「一步到位」短写法（v1.18.0）并回本表。它们本来就由分派链
    # 末尾的软别名分支处理，但没登记在这里 —— 于是 `BUILTIN_COMMAND_WORDS`
    # 数不到它们，别名与自定义命令就能**悄悄抢走** `/钓鱼 喂` `/钓鱼 放` 这些写法
    # （校验放行、运行时却被内置分支先接走，站长的配置静默失效）。
    "水族馆": (
        "水族馆", "馆", "aquarium", "缸",
        "放", "放入", "养", "取", "取出", "拿", "领", "收租", "收益",
    ),
    "扩建背包": ("扩建背包", "扩容", "背包扩容", "鱼篓扩容"),
    "锁定": ("锁定", "锁", "lock"),
    "解锁": ("解锁", "解", "unlock"),
    "今日": ("今日", "天气", "行情", "today", "weather", "market"),
    "排行": ("排行", "排行榜", "rank", "top", "榜"),
    "商店": ("商店", "铺子", "shop"),
    "鱼饵": ("鱼饵", "鱼饵店", "饵店", "饵"),
    "道具": ("道具", "道具店", "物品", "item", "items"),
    # 「买」本身也是一条子命令（智能买：按名字自动认它在哪家店）。
    # 把它登记成内置写法的副作用正好是我们想要的：**别名抢不走它** ——
    # 老配置里的 `商店|鱼饵,道具,shop,买` 会让 `买` 变成「商店」的别名，
    # 于是 /钓鱼 买 蚯蚓 被路由到「商店已拆成三家」的指路提示，玩家就买不到东西了。
    "买": ("买", "购买", "buy"),
    # 喂/投喂/洗/洗髓 是免打「用」的短写法（见分派链的短写法分支）
    "用": ("用", "使用", "道具用", "use", "喂", "投喂", "洗", "洗髓"),
    "查": ("查", "查询", "鱼", "鱼查", "资料", "info", "lookup"),
    "事件": ("事件", "插曲", "选择", "event"),
    "换饵": ("换饵", "换鱼饵", "装备饵", "上饵", "bait", "equip_bait"),
    "体力": ("体力", "体力值", "活力", "stamina"),
    "档案": ("档案", "profile", "me"),
    "金币": ("金币", "gold"),
    "签到": ("签到", "sign"),
    # 交/交单/交货 是免打「订单」的短写法（见分派链的短写法分支）
    "订单": ("订单", "任务", "order", "orders", "交", "交单", "交货"),
    "钓点": ("钓点", "地点", "地图", "map", "location"),
    # 装备/换竿/换鱼竿 是免打「鱼竿 用」的短写法（见分派链的短写法分支）。
    # ⚠️ 别把「换鱼竿」误当成换饵的写法 —— 分派链里它是换竿。
    "鱼竿": ("鱼竿", "竿", "rod", "装备", "换竿", "换鱼竿"),
    "杂物": ("杂物", "漂流瓶", "收集品", "collect"),
    # v1.18.17：自动补给设置 / 称号（后期金币回收）/ 香火供奉
    "自动": ("自动", "自动补给", "auto"),
    "称号": ("称号", "头衔", "title"),
    "供奉": ("供奉", "香火", "上香", "offering"),
    # 大鱼乐（v1.18.51）：现实彩票玩法。别名挑的是玩家真会打的词
    "大鱼乐": ("大鱼乐", "彩票", "抽奖", "lottery", "lotto", "买彩票", "乐透"),
}

#: 全部内置写法（含规范名本身）：自定义命令不许与它们重名（内置永远优先）
BUILTIN_COMMAND_WORDS: frozenset[str] = frozenset(
    word for words in SUBCOMMAND_KEYWORDS.values() for word in words
)

#: 按钮白名单与「子命令总表」保持同步（v1.18.17）：凡是路由认识的写法，
#: 按钮都能指向它。以前两份表各写各的，于是 `/钓鱼 称号` 这类新命令的按钮
#: 会被当成「点了没反应的死按钮」丢掉（写按钮表时还看不到任何报错）。
CALC.BUTTON_COMMAND_WORDS.update(BUILTIN_COMMAND_WORDS)

#: 运行时的「别名 → 规范子命令」表：**只装站长新加的别名**，
#: 内置写法一个都不装（分派链自己认），所以默认配置下它是空的、行为零变化。
COMMAND_ALIASES: dict[str, str] = {}
#: 运行时的自定义命令表：``命令名(小写) -> (动作, 内容)``
CUSTOM_COMMANDS: dict[str, tuple[str, str]] = {}


def _default_command_aliases_text() -> str:
    """生成 ``command_aliases`` 的默认值：把现有的内置别名整表写出来。

    站长照着它加行最省事。表里每一行都是「本来就已经能用」的写法，
    所以默认值**不改变任何行为**（解析后新增别名表仍然是空的）。
    """
    owner: dict[str, str] = {}
    for canonical, words in SUBCOMMAND_KEYWORDS.items():
        owner.setdefault(canonical, canonical)
        for word in words:
            owner.setdefault(word, canonical)
    lines: list[str] = []
    for canonical, words in SUBCOMMAND_KEYWORDS.items():
        aliases = [
            word for word in words
            if word != canonical and owner.get(word) == canonical
        ]
        if aliases:
            lines.append(f"{canonical}|{','.join(aliases)}")
    return "\n".join(lines)


# =============================================================================
# 一、内置默认值
# =============================================================================
#
# 这些是「配置缺省时的兜底」。运行时会优先读 _conf_schema.json 生成的配置，
# 所以想改数值请优先去 WebUI 插件配置页改，而不是改这里的常量。

DEFAULTS: dict[str, Any] = {
    # 配置面板里的「路标」：面板只留这一条 + data_status + defaults_sync_mode 可见，
    # 其余内容/数值都在「数据编辑器」页面里改（见 _conf_schema.json 的 invisible）
    "content_tables_hint": "",
    # 默认值指纹（插件回写，只读参考）：代码里的数值一变，这个指纹就变
    "config_fingerprint": "",
    # 数值同步档位：auto = 同步数值与内容 / all = 连开关一起重置 / off = 不同步
    "defaults_sync_mode": "auto",
    # 站长自己改过的配置键（编辑器每次保存都会记一笔）：升级时这些键不会被新版默认值覆盖
    "user_edited_keys": [],
    "initial_gold": 100,
    "fish_cost": 0,
    # 体力：每钓一次消耗 1 点，攒着最多 stamina_max 点；每 stamina_regen_seconds 秒回 1 点。
    # 任一项填 0 = 本服不限体力（相当于关掉这套机制）。
    # 鱼池定义（一行一条鱼）：留空 = 用内置鱼池；默认值在下方由 _fish_data 注入
    "fish_defs": "",
    # 以下 4 张内容表同样可以在配置面板里改（默认值来自 _game_data.py）
    "collectible_defs": "",
    "variant_defs": "",
    "weather_defs": "",
    "easter_egg_defs": "",
    # 回复里那些按钮（QQ 官方内联键盘）：一行一个按钮，场景|文案|指令|样式
    "button_defs": "",
    # 每条回复的文案模板：一行一条，场景|模板（{原文} = 代码原本拼好的那段文字）
    "text_overrides": "",
    # 按钮排布：一行一条，场景|每行几个（* = 其余所有场景的默认值）
    "button_layout": "",
    # 按钮样式策略：按按钮表（每行自己写，默认）/ 统一（所有按钮都用下面这个样式）
    "button_style_mode": "按按钮表",
    # 统一样式 / 按钮表里没写样式时的兜底（default=灰、primary=蓝，也可以写数字）
    "button_default_style": "default",
    # 明确「就是不要按钮」的场景（逗号分隔场景名）：连「继承父场景」也断掉（v1.18.0）
    "button_empty_scenes": "",
    "stamina_max": 20,
    "stamina_regen_seconds": 45,
    # 一次最多连钓几次（/钓鱼 <数字>），防止 /钓鱼 9999 之类把机器人卡住
    "multi_cast_max": 20,
    "sign_reward": 30,
    "sell_discount": 1.0,
    "enable_group_broadcast": True,
    "backpack_base": 30,
    # 背包扩容档位：`加多少格|价格`，按顺序买（v1.18.13 从 3 档加到 7 档：
    # 40 → 100 格只够玩到中期，后面越买越贵，最后几档是长期目标）
    "backpack_upgrades": [
        "15|400", "25|1100", "30|2700", "40|6500",
        "55|15000", "75|34000", "100|75000",
    ],
    "aquarium_capacity": 8,
    "interactive_rarities": "传说,神话",
    "window_min": 4,
    "window_max": 8,
    "sweet_spot_width": 0.34,
    "perfect_bonus": 0.30,
    "good_bonus": 0.10,
    "perfect_escape_factor": 0.3,
    "edge_escape_factor": 1.6,
    #: 各品质的基础逃脱率（拉线窗口里跑掉的概率）。
    #: ⚠️ v1.18.26：站长报「降低各钓点的逃脱率，太高了」—— 实测（他的配置）：
    #:   传说 30% / 神话 49%（良好评价）、完美 15% / 偏差 78%、**连钓里神话 95%（封顶必跑）**。
    #:   现在砍到 20% / 28%，配合难度权重 0.4 与连钓系数 2.0：
    #:   传说 20% / 神话 32%（良好）、完美 9.5% / 偏差 51%、连钓神话 63%。
    #:   想要更狠（传说 16% / 神话 25%、连钓 44%）就把这里改成 传说:0.16,神话:0.22。
    "rarity_escape_chance": "传说:0.20,神话:0.28",
    # 连钓里要拉线的鱼：不弹拉线，按「逃脱率 × 这个系数」一次判定。
    # 1.0 = 旧行为（连钓里这些鱼几乎不会跑，比单竿还稳）；
    # v1.18.10 起默认 2.5（「连钓不能比单竿还稳」），v1.18.26 降到 2.0 ——
    # 2.5 会让深水图的神话鱼 95% 必跑，玩家体感就是「连钓等于白钓」。
    # ⚠️ v1.18.28 起这项**只在 multi_pull_enabled 关掉时**才生效。
    "multi_escape_mult": 2.0,
    # v1.18.28：连钓里要拉线的鱼**逐条弹拉线互动**（默认开）。
    # 开 = 和单竿同一套窗口/评价/逃脱判定，一条一条拉；
    # 关 = 回到老行为（不弹互动，按 multi_escape_mult 一次判定）。
    "multi_pull_enabled": True,
    "feed_max_uses": 10,
    "quality_weights": [44, 28, 16, 9, 3, 0],
    # v1.18.22：手气改成「按档位放大权重」，两个旋钮见 _roll_quality_mult。
    #   luck_weight_step = 每 1.0 手气把相邻档的权重比放大多少（1.0 = 第 i 档 ×(1+手气)^i）
    #     0 = 手气完全不影响品质（只按 quality_weights 掷），想关掉手气就填 0
    #   luck_cap = 手气合计的上限（以前硬编码 1.0，导致后期鱼饵+鱼竿就把手气顶满，
    #     玉佩/玉髓灯一点效果都没有 —— 站长报的「不用玉佩也大部分是绝品」）
    "luck_weight_step": 1.0,
    "luck_cap": 2.0,
    # 洗髓丹：每条鱼每天最多吃几颗（吃满了当天「厌恶」，第二天恢复）
    #   **默认 0 = 不限**（v1.18.29 起）；想恢复「一条鱼一天最多 3 颗」就填 3
    "reroll_daily_limit": 0,
    # 育灵水 / 珍珠梳 / 任何带「喂鱼上限 +N 次」的道具（v1.18.62 加，v1.18.64 改口径）：
    #   feed_bonus_lifetime_limit = **同一条鱼一辈子最多能用几次**（默认 2）；
    #     同类道具（育灵水 +5、珍珠梳 +10、以后自己加的）**一起算**，喂满就不再给喂。
    #     ⚠️ 键名 v1.18.62 叫 feed_bonus_daily_limit（当时按天重置）—— v1.18.65 起
    #     只保留这一个键，_refresh_config 会把老键的值搬过来并删掉老键。
    #     填 0 = 不限（只受 feed_bonus_cap 约束）。
    #   feed_bonus_mode = add 每次叠加 / best 只取最好的一次
    "feed_bonus_lifetime_limit": 2,
    "feed_bonus_mode": "add",
    # 单条鱼的投喂上限加成最多堆到多少（育灵水 +5、珍珠梳 +10 都堆在这一项上）
    "feed_bonus_cap": 20,
    # 洗髓丹洗出「神品」的概率 —— **每次重掷**独立判定（一颗丹默认重掷 3 次），
    # 所以只能靠洗髓丹拿到，自然上钩永远不出（quality_weights 最后一位是 0）
    "quality_myth_chance": 0.0025,
    "rarity_display_names": ["常见", "少见", "稀有", "传说", "神话"],
    "item_drop_chance": 0.14,
    "bottle_note_chance": 0.30,
    "order_count": 3,
    # v1.18.17：订单价改成**跟着玩家的收入走**（站长报「订单价格太低」）——
    #   订单单价 = 鱼基准价 × 1.3 × order_reward_mult × 动态系数
    #   动态系数 = 钓点价值倍率 × (1 + 鱼竿价值加成) × (1 + order_level_growth × (等级-1))，
    #   再钳到 [1.0, order_factor_max]。
    # 基准倍率因此从 2.2 降到 1.6（1 级新手仍然是「比卖店赚一倍」），
    # 后期靠动态系数追上来：62 级龙宫 + 归墟竿 ≈ 卖店的 4~5 倍。
    "order_reward_mult": 1.6,
    "order_level_growth": 0.02,
    "order_factor_max": 6.0,
    "order_unlock_level": 3,
    "order_refresh_min_hours": 3,
    "order_refresh_max_hours": 6,
    # 订单只点「当前钓点钓得到的鱼」；换钓点跟着换一批（每个刷新周期最多 order_move_rerolls 次）
    "order_follow_location": True,
    "order_move_rerolls": 1,
    # 隐藏生物（大肥鱼）能不能被点单；默认能 = v1.14.0 之前的行为
    "order_include_hidden": True,
    # ---- 后期金币回收（v1.18.17）：站长说终局金币溢出，得给钱找个去处 ----
    # 称号：纯炫耀、无属性（不会推高收益），一次性买断、可随时换着戴。
    # 格式：id|名称|emoji|价格|说明（越往后越贵，给后期玩家一个「花得掉」的目标）
    "title_defs": [
        "novice|钓鱼新手|🎣|0|刚拿到竿子的第一天",
        "regular|常客|🪑|20000|老板已经记住你的脸了",
        "veteran|老钓手|🧢|80000|一坐就是一下午",
        "tycoon|一掷千金|💰|300000|钱对你来说只是数字",
        "deep_lord|深海领主|🔱|1200000|海沟以下都归你管",
        "void_master|归墟之主|🕳️|5000000|连声音都被吞掉的地方，你去过",
    ],
    # 供奉香火：花一笔大钱换 24 小时的挂机产出与手气加成（可重复买，天天供奉天天强）。
    # 这是**主动**的回收口：越到后期越划算，钱多的人有地方花，钱少的人不买也不亏。
    "offering_price": 200000,
    "offering_hours": 24,
    "offering_income_bonus": 0.5,
    "offering_luck_bonus": 0.05,
    # ---- 每日额度（v1.18.18）：站长问「玩家从早玩到晚，道具你不做一些限制吗」----
    # 一天之内能白嫖（或花钱买）的「强度」必须有天花板，否则装置就形同虚设：
    # 手气道具全天挂着 = 品质分布被顶穿、回体力道具无限喝 = 体力限制失效。
    # 0 = 不限（想放开就填 0）。跨天 0 点自动重置，档案里能看到今天用了多少。
    "buff_daily_cast_limit": 120,   # 手气道具每天合计最多生效多少竿
    "hot_soup_daily_limit": 5,      # 回体力类道具（姜汤）每天最多喝几次
    "reroll_daily_total": 0,        # 洗髓丹每天最多用几颗（0 = 不限；每鱼每天还有一层上限）
    "offering_daily_limit": 1,      # 香火供奉每天最多几次
    # 鱼竿：id|名称|emoji|价格|价值加成|幸运加成|解锁等级|描述（解锁等级 = 能买的等级）
    # 后两段（可选）是**拉线手感**：拉线窗口加成 / 逃脱率系数（见 _calc._parse_rod_defs）
    # v1.18.15 加了两档后期竿：不堆数值，改卖「手感 + 收金币」——
    # 价值加成只 +0.03，但窗口更长、更不容易跑，让拉线在后期有分量。
    "rod_defs": [
        "bamboo|竹竿|🎋|0|0.00|0.00|1|村口杂货铺送的，能用",
        "carbon|碳素竿|🎣|400|0.05|0.03|4|轻巧顺手，新手进阶首选",
        "stream|溪流竿|🪝|1600|0.09|0.05|9|韧性好，适合溪流与湖泊",
        "dragon|龙纹竿|🐉|5400|0.17|0.11|16|竿身刻龙，专治大鱼",
        "starlight|星辉竿|✨|11000|0.23|0.16|26|夜里会泛微光，深海也用得上",
        "mythic|神话竿|🌈|22000|0.30|0.22|38|传说钓具，据说能引来神话之鱼",
        "koi_dragon|龙纹鲤竿|🐲|300000|0.33|0.26|56|"
        "竿身缠着一条活鲤纹，握上去就知道什么叫稳|0.15|1.00",
        "void_rod|归墟竿|🕳️|1200000|0.36|0.30|62|"
        "竿梢细得几乎看不见，鱼线却再也挣不断|0.10|0.90",
        # ---- v1.18.63：大鱼乐**限定竿**（新做的四根，不卖、只抽）----
        # ⚠️ 设计口径（站长定的）：
        #   · 数值**一律低于**同级金币竿（价值加成 ≤12%），靠「异化」区分，不靠堆数值；
        #   · 每根都用**牺牲一块**换一个特权 —— 不牺牲就等于白送，会数值膨胀；
        #   · 第 11 段 = 1（限定竿，不进商店 / 不算「鱼竿买齐」成就）；
        #   · 第 12 段 = 特殊效果，键见 _calc.SPECIAL_KEYS。
        # 段位：id|名称|emoji|价格|价值加成|幸运加成|解锁等级|描述|窗口加成|逃脱系数|限定|特殊效果
        "tide_rod|潮汐竿|🌊|0|0.10|0.08|1|异色猎手：异色判定多掷 2 次（0.5%→约 1.5%）。代价：价值只有顶配竿的 1/3|0.10|0.95|1|variant_extra=2",
        "star_rod|星陨竿|☄️|0|0.12|0.00|1|星陨：每 12 竿有 1 竿的个体品质直接是神品。代价：手气归零，只有 12% 价值加成|0.00|1.00|1|myth_every=12",
        "quick_rod|瞬手竿|🪶|0|0.08|0.00|1|瞬手：拉线一律判「完美」，饵不被咬掉、绝不脱钩。代价：手气归零、价值很低|0.25|0.85|1|perfect",
        "twin_rod|双尾竿|🎣|0|0.05|0.06|1|双尾：一次成功上钩算两条。代价：价值垫底 + 更容易跑（逃脱×1.08）|0.00|1.08|1|double",
    ],
    # 钓点定义由 LOCATIONS 生成（见文件下方 _location_def_lines()），此处留空占位
    "location_defs": [],
    "enable_weather": True,
    "enable_market": True,
    "market_boost_min": 1.5,
    "market_boost_max": 2.2,
    "variant_chance": 0.005,
    "easter_egg_chance": 0.05,
    "story_chance": 0.06,
    # ---- 小插曲 / 连载剧情（v1.18.13）----
    # 这次插曲走「连载的下一话/新开一条线」的概率，其余情况演一次性小插曲
    "story_chain_chance": 0.5,
    # 连载两话之间至少隔几竿（免得像连播；一次性插曲不受它影响）
    "story_chain_gap": 6,
    # 插曲/连载的选项**位置每次随机**（免得同一个选择永远待在 1 号位）
    "story_shuffle_choices": True,
    # ---- 数据管理（都在 WebUI 里操作，不用发指令）----
    "level_xp_base": 5.0,           # 升级曲线：每级基础竿数
    "level_xp_ratio": 1.08,         # 升级曲线：等比底数（指数项，越大越陡）
    "level_xp_growth": 0.0,         # 升级曲线：二次增长系数（默认 0，留着做微调）
    # 上钩率：空钩基本靠运气，带饵才容易上鱼（"id:概率" 逗号分隔，站长可调）
    # 钓点难度系数：≥1.0 = 这个钓点必出鱼；<1.0 = 上钩率 × 系数（越深越容易空竿）
    #
    # ⚠️ 站长两次要求「提高」这张表：
    #   v1.18.16 原来从 1.0 一路滑到龙宫 0.54 —— 拿最好的饵在深层也只有 54% 中鱼，
    #     他报「倒数第四个钓点用最好的配置依旧相当于一半空杆」→ 改成 1.0 → 0.82；
    #   v1.18.27 他说「我让你提高这个」→ 再抬到 **1.0 → 0.92**：
    #     最深的地图（龙宫）拿最好的饵 92% 中鱼（空竿 8%），拿蚯蚓这种中档饵 64%；
    #     前 3 张图仍然必出鱼。想要「一张图都不空竿」就把最后几项填 1.0。
    "location_hook_factors": (
        "novice:1.0,bamboo:1.0,canal:1.0,lake:0.99,reed:0.98,sea:0.97,dock:0.96,night"
        ":0.95,mangrove:0.95,swamp:0.94,cave:0.94,ruins:0.93,abyss:0.93,trench:0.93"
        ",glacier:0.92,aurora:0.92,starfall:0.92,void_sea:0.92,dragon_palace:0.92"
    ),
    # 前往下一个钓点需要上一个钓点图鉴开到多少比例
    "location_codex_gate": 0.8,
    # 空竿是否也消耗鱼饵：false = 空竿不扣饵（默认）
    "consume_bait_on_empty": False,
    # ⚠️ v1.18.70：限定饵也要有上钩率，否则每次抛竿都刷一句「没有 abyss_secret 的上钩率，
    #    暂时按 30% 处理」。限定饵的定位是「特权换强度」，上钩率给个中上水平（0.90）。
    "bait_hook_rates": "none:0.25,bread:0.58,worm:0.70,bloodworm:0.80,corn:0.88,shrimp:0.94,livebait:0.97,secret:1.0,abyss_bait:1.0,dragon_bait:1.0,abyss_secret:0.90,vip_bait:0.90",
    "content_auto_merge": True,     # 旧配置自动合并新版内容（钓点/鱼饵/鱼竿/道具）
    # =========================================================================
    # 三·九、大鱼乐（v1.18.51）：现实彩票的玩法搬进游戏
    # =========================================================================
    # 花金币买票，即时开奖；奖品可以是鱼（含正常钓不到的神话 / 神品）、金币、
    # 道具、鱼饵。**彩票的经济学是「期望回报 < 票价」**，所以：
    #   * 基础奖表的期望回报约 94%（庄家优势 6%），票钱是**纯金币出口**；
    #   * 默认有每日限购与连输保底，挡住「有钱就一次买两万张」。
    # 奖表每一行：``id|概率(可写 0.05 这种百分比数)|类型|参数|数量|说明``
    #   类型 fish  参数 = ``稀有度[:品质[:变异id]]``，稀有度写 all = 任意
    #   类型 gold  参数留空，数量 = 给多少金币
    #   类型 item  参数 = 道具 id，数量 = 给几个
    #   类型 bait  参数 = 鱼饵 id，数量 = 给几个
    #   类型 baitpack 参数 = `id:数量,id:数量`（鱼饵包）
    #   类型 reward 参数 = ``id:数量,id:数量``（道具包）
    #   类型 none  谢谢惠顾（什么都不给）
    # 「概率」用的是**相对权重**（不必凑够 100，代码按总和归一化），所以站长想调
    # 爆率、想加档、想删档、想把奖品换成任何鱼/道具/鱼饵/金币，都只改这一张表 ——
    # **没有任何一档是写死在代码里的**（连"头奖是哪一个"都由
    # ``lottery_jackpot_prize`` 指定）。改完可以发 ``/钓鱼 大鱼乐 概率`` 自查
    # 实际概率与长期期望（期望必须小于票价，否则测试会红）。
    "lottery_prizes": "\n".join([
        "jackpot|0.001|fish|all:神品|1|神话鱼 · 神品　🐉 头奖",
        "first|0.01|fish|神话:绝品|1|神话鱼 · 绝品　🌈 一等奖",
        "second|0.03|fish|传说:绝品|1|传说鱼 · 绝品　✨ 二等奖",
        "gold_big|2.8|gold||50000|现金 50,000 金　💰 三等奖",
        "gold_mid|11.3|gold||15000|现金 15,000 金　💵 四等奖",
        "gold_small|8|gold||5000|回本 5,000 金　🪙 五等奖",
        "fish_rare|3|fish|稀有:珍品|1|稀有鱼 · 珍品　🐡 六等奖",
        "bait_pack|2|baitpack|worm:20,bread:30|1|鱼饵包：蚯蚓 ×20 + 面包屑 ×30　🪱",
        "tide_rod_pass|0.8|rod|tide_rod_pass|1|🌊 潮汐竿（异色猎手，20 次）",
        "quick_rod_pass|0.25|rod|quick_rod_pass|1|🪶 瞬手竿（必完美拉线，25 次）",
        "abyss_bait_pass|1.5|bait|abyss_bait_pass|1|🕳️ 深渊秘饵（全图鱼口，8 次）",
        "vip_bait_pass|0.05|bait|vip_bait_pass|1|👑 贵客饵（只抽传说，6 次）",
        "star_rod_pass|0.012|rod|star_rod_pass|1|☄️ 星陨竿（每 12 竿一次神品，30 次）",
        "twin_rod_pass|0.007|rod|twin_rod_pass|1|🎣 双尾竿（一竿两条，20 次）",
        "blank|52|none||0|谢谢惠顾　（牌子翻过来写着「再来一张」）",
    ]),
    #: **哪一个奖级算「头奖」**（填奖表行首的 id；留空 = 不设头奖）。
    #: 只有它享受两件特殊待遇：额外送 ``lottery_jackpot_gold``、开了
    #: ``lottery_announce`` 时群播报。站长把奖品换掉/改档时记得同步这个 id。
    "lottery_jackpot_prize": "jackpot",
    #: 头奖额外送的金币（写 0 = 头奖只给奖品不给钱）
    "lottery_jackpot_gold": 500000,
    #: **每张票多少金币**（站长说「价格怎么不能配置」—— 它一直是可配的，但入口不好找，
    #: 所以 v1.18.52 起在 🎰 大鱼乐 页顶部也写明了它在哪，并给了一键跳转）。
    "lottery_ticket_price": 5000,
    # ⚠️ v1.18.71：**奖池里的鱼要乘系数**（站长：「奖池的鱼应该和订单鱼一样乘上系数啊，
    # 不然太低了」）。奖池发的鱼现在按「订单那套动态系数」估价（当前钓点倍率 ×
    # 鱼竿价值加成 × 等级成长，封顶 order_factor_max），再乘这一项。
    # 1.0 = 和订单鱼同价；嫌太肥就往下调（0.5 = 一半），调完记得发 /钓鱼 大鱼乐 概率 核对期望。
    "lottery_fish_factor": 1.0,
    "lottery_daily_limit": 50,          # 每天最多买几张（0 = 不限）
    "lottery_max_per_call": 30,         # 一次最多连抽多少张（挡消息过长）
    "lottery_pity_count": 15,           # 连输多少张后保底给一张（0 = 不保底）
    "lottery_pity_prize": "gold_small",  # 保底给哪一行奖（填奖表的行首 id）
    "lottery_announce": False,          # 中头奖是否在群里播报
    # ---- 功能开关（v1.18.52）：站长调试用，全部可在编辑器里改 ----
    #: 大鱼乐**总开关**：关掉就整个玩法停摆（指令只回一句「关着」，不扣钱不发奖）。
    #: 想临时停一个功能不用改代码，也不用把奖表清空。
    "lottery_enabled": True,
    #: 鱼奖能不能出**异色**（默认开）。开着 = 和普通钓鱼用**同一个** ``variant_chance``
    #: 掷异色（站长要求「概率和普通钓鱼一样」）；关掉 = 鱼奖永远普通个体，方便对概率。
    "lottery_allow_variant": True,
    #: **调试用**：填了奖级 id（例如 ``jackpot``）= 每次开奖都强制出这一档。
    #: ⚠️ 它会让期望回报彻底失控（必中头奖），只适合调试演出/截图，用完记得清空；
    #: 开着的时候日志与 /钓鱼 大鱼乐 概率 都会红着脸提醒。
    "lottery_force_prize": "",
    "button_mode": "自动",          # QQ 官方按钮发送形态：自动/markdown/text/关闭
    # 回复开头怎么称呼发送者：关闭 / 昵称 / @（默认「昵称」）。只加在每条指令的
    # 第一条回复上，按钮路径与纯文本路径都会加（见 _interactions._mention_prefix）。
    "mention_mode": "昵称",
    "data_status": "",              # 插件回写的状态面板（人看）
    "data_action": "无",            # 要执行的数据操作（执行后自动复位）
    "data_target": "",              # 目标玩家ID / 快照文件名
    "data_confirm": False,          # 危险操作二次确认
    # 编辑器页面（pages/editor/）读它拿存档清单/玩家数等状态；由插件回写，见 _editor_bridge.py
    "editor_status": "",
    "backup_import_file": [],       # 上传存档（导入用，type=file）
    "backup_export_file": [],       # 导出存档（下载用，type=file）
    "enable_auto_backup": True,
    "backup_daily_hour": 4,
    "backup_interval_hours": 6,
    "backup_keep_daily": 30,
    "backup_keep_interval": 20,
    # 手动存档保留份数（0 = 永久保留；>0 时每次新建手动存档后只留最近 N 份）
    "backup_keep_manual": 0,
    "backup_dir": "",
    "codex_bonus_per_rarity": [0.03, 0.05, 0.08, 0.12, 0.2],
    # 挂机收益（水族馆的核心玩法）：每条鱼按**各自在缸里的时间**产出（v1.18.0）。
    # 原来的「取出/卖出 ×1.2 展出加成」已经去掉，补偿就是把这几个数值提高：
    #   每小时 1.5% -> 2%
    # v1.18.15：单次封顶 5000 -> 10 万。5000 那个数字在 30 级以后等于零
    #（龙宫一竿就 3000+ 金，攒满 12 小时才 5000），整套养鱼玩法直接失去意义；
    # 现在改成「馆藏越值钱，挂机越多」，同时 10 万/次 ≈ 终局 30 竿，只是补充不是捷径
    #（站长要的是「越往后越慢」，所以仍然保留封顶，不让挂机替代主动钓鱼）。
    "pond_income_per_hour": 0.02,
    # v1.18.17：单次最多累计 12 -> 24 小时（睡一觉 + 上一天班回来都还在攒），
    # 真正的天花板仍然是下面的金币封顶，不是小时数。
    "pond_income_cap_hours": 24,
    "pond_income_cap_coins": 100000,
    # 水族馆装饰：同时可摆几个、每个耐久多少小时（到点自动失效）
    "decoration_slots": 3,
    "decoration_hours": 72,
    # 钓手本人的手气 buff 持续多少竿
    "buff_cast_count": 20,
    # ---- 自动补给（v1.18.17，站长要的「用完了自动花钱补」）----
    # 下竿时如果当前鱼饵用光了：按单价自动买 1 个继续钓（金币不够就退回空钩）。
    "auto_supply_bait": True,
    # 没挂饵但背包里有饵时，自动挂上「手气最高的那款」——只在你**从没选过饵**
    # （equipped_bait 为空）时生效；自己选过空钩的人不会被偷偷换成花钱的饵。
    "auto_equip_bait": True,
    # 手气道具（玉佩/潮汐香/玉髓灯）用完了自动买 + 自动激活：
    # 要先让玩家自己指定用哪一件（`/钓鱼 自动 锦鲤玉佩`），插件不替他挑，
    # 免得一觉醒来被自动买掉一个 1.2 万的玉髓灯。
    "auto_supply_buff": True,
    # 水族馆：`名字|价格` 或 `名字|价格|加几个位`（第三段省略 = 1 个）。
    # v1.18.13 从 3 档加到 6 档：后面几档一次加 2~4 个位，价格也成倍往上走。
    # v1.18.17 再加 3 档终局缸（一次 +5/+6/+8 位）：站长说后期金币溢出，
    # 这几档就是专门给「钱多到没处花」的人准备的（39 个位要全买 ≈ 300 万）。
    "aquarium_slots": [
        "精致缸|1600", "生态缸|5400", "深海缸|16000",
        "珊瑚宫殿|38000|2", "龙宫别苑|88000|3", "水晶宫|190000|4",
        "琉璃龙宫|420000|5", "星海神殿|900000|6", "归墟海眼|1800000|8",
    ],
    # 鱼饵：id|名称|emoji|单价|一组数量|品质幸运|稀有度权重|解锁等级|需要鱼竿|说明
    # 「需要鱼竿」填鱼竿 id 或名称，表示**拥有**那根竿才能买；解锁等级是新号也能用的门槛
    "bait_defs": [
        "none|空钩|🪝|0|0|0|1,1,1,1,1|1||什么也不挂，全凭本事（免费）",
        "bread|面包屑|🍞|1|10|0.06|1,1.1,1.3,1.4,1.5|1||厨房剩的，便宜大碗",
        "worm|蚯蚓|🪱|2|5|0.14|1,1.2,1.6,1.8,2.0|2||万用饵，两块钱一钩",
        "bloodworm|红虫|🪰|4|5|0.22|1,1.4,2.0,2.4,3.0|5||小鱼最爱，上钩快",
        "corn|玉米粒|🌽|6|5|0.30|1,1.5,2.3,3.0,4.0|9|stream|素饵之王，草鱼克星",
        "shrimp|虾饵|🦐|12|3|0.40|1,1.8,2.6,3.6,4.6|15|dragon|肉食鱼最爱，稀有度明显上升",
        "livebait|活饵小鱼|🐟|22|2|0.52|1,2.0,3.2,4.6,6.0|22|starlight|活蹦乱跳，专勾大鱼",
        "secret|秘制饵|🍯|45|1|0.68|1,2.0,4.0,6.0,8.0|34|mythic|祖传配方，闻着就不一样",
        # v1.18.15 的两款后期饵：不拼「整体更好」，改拼**专精**——
        # 常见/少见的权重往下压，换传说/神话成倍往上翻，
        # 于是「用哪款饵」第一次真的变成取舍（想刷图鉴/卖钱就别用它）。
        "abyss_bait|深渊饵|🕳️|120|1|0.72|0.5,1.0,3.0,7.0,10.0|50|mythic|"
        "深海里捞上来的东西，腥得吓人，专招大物",
        "dragon_bait|龙涎|🐉|300|1|0.78|0.3,0.8,2.0,6.0,14.0|62|void_rod|"
        "龙宫檐下凝的一滴，寻常鱼闻了不敢靠近",
        # ---- v1.18.63：大鱼乐**限定饵**（新做的两种，不卖、只抽）----
        # 第 11 段 = 特殊效果；带特殊效果的饵**不进商店**（_shop_visible_baits）。
        # ⚠️ 牺牲换特权：深渊秘饵稀有度权重全是 1（比面包屑还差），
        #    贵客饵只抽传说但品质按自然爆率算 —— 都不是「更强的龙涎」。
        "abyss_secret|深渊秘饵|🕳️|0|1|0.00|1,1,1,1,1|1||"
        "大鱼乐限定：这一竿从全地图鱼池抽（哪里都能出大物），但稀有度权重全 1|all_pool=1",
        "vip_bait|贵客饵|👑|0|1|0.00|1,1,1,1,1|1||"
        "大鱼乐限定：这一竿只从「传说」里抽（品质仍看脸）|legend_only=1",
    ],
    # 道具：id|名称|emoji|单价|说明|效果|解锁等级（见 _parse_effects）
    #   meat/spirit/sheen/value_up = 喂鱼（一次性，永久加成）
    #   decorate      = 摆进水族馆的装饰（耐久内持续加成挂机产出）
    #   feed_bonus    = 提升这条鱼的投喂上限
    #   buff_quality  = 作用于钓手本人的手气（持续 buff_cast_count 竿）
    #   quality_reroll= 重掷这条鱼的个体品质（取更好的那次）
    #   heal          = 回复体力（体力没开时用不了；满了不扣道具）
    #
    # v1.18.16 重设过一遍：道具是**一口价**，但它的收益随鱼价水涨船高 ——
    # 所以每一档都配了等级门槛，让「什么时候买才划算」说得清楚：
    #   1 级买的饲料只适合喂贵鱼；后期道具（玉佩/仙露/造景）都卡在海沟之后。
    #   锦鲤玉佩也从 +30%/500 金削成 +20%/6000 金（原来 20 竿能多赚 3 万，
    #   只要 500 金，等于后期白送）。
    "item_defs": [
        "feed_basic|普通饲料|🌾|20|喂鱼：肉+2、灵+1（永久）|meat=2;spirit=1|1",
        "feed_premium|高级饲料|🍖|90|喂鱼：肉+5、灵+4、光+3（永久）|meat=5;spirit=4;sheen=3|6",
        "feed_divine|仙露|💧|420|喂鱼：三维各 +10，估值 +600（永久）|"
        "meat=10;spirit=10;sheen=10;value_up=600|14",
        "growth_tonic|育灵水|🌱|800|喂鱼：这条鱼的投喂上限 +5 次|feed_bonus=5|18",
        "pill_quality|洗髓丹|🔮|4000|重掷这条鱼的个体品质（取更好的那次，不影响三维）；"
        "极小概率直接洗出「神品」|quality_reroll=3|20",
        "coral_deco|珊瑚造景|🪸|3000|摆进鱼缸：72 小时内挂机产出 +20%|decorate=0.20|27",
        "lucky_jade|锦鲤玉佩|🎐|12000|带在身上：接下来 20 竿品质不低于「珍品」|"
        "quality_floor=2.0|31",
        "tide_incense|潮汐香|🕯️|23000|带在身上：接下来 40 竿品质不低于「珍品」|"
        "quality_floor=2.0;buff_casts=40|36",
        "jade_lantern|玉髓灯|🏮|27000|带在身上：接下来 15 竿品质不低于「绝品」|"
        "quality_floor=3.5;buff_casts=15|40",
        "feed_mythic|龙涎饲料|🐲|1500|喂鱼：三维各 +18，估值 +2000（永久）|"
        "meat=18;spirit=18;sheen=18;value_up=2000|45",
        "pearl_comb|珍珠梳|🪮|9000|喂鱼：这条鱼的投喂上限 +10 次|feed_bonus=10|50",
        "coral_king|珊瑚王座|👑|40000|摆进鱼缸：72 小时内挂机产出 +45%|decorate=0.45|52",
        "hot_soup|姜汤|🍲|400|喝一口：回复体力（体力已满时不消耗）|heal=10|3",
        # ---- v1.18.63：大鱼乐**限定竿**的发奖载体（商店不卖，只能抽）----
        # 第 9 段 = 限用次数；第 10 段 = 它顶替哪根竿。
        # 「体验版 / 完整版」= 同一个机制、不同次数与不同竿。
        "tide_rod_pass|潮汐竿·凭证|🌊|0|大鱼乐限定：顶替当前鱼竿（限用 20 次，用完消失）|"
        "|1|0|20|tide_rod|",
        "star_rod_pass|星陨竿·凭证|☄️|0|大鱼乐限定：顶替当前鱼竿（限用 30 次，用完消失）|"
        "|1|0|30|star_rod|",
        "quick_rod_pass|瞬手竿·凭证|🪶|0|大鱼乐限定：顶替当前鱼竿（限用 25 次，用完消失）|"
        "|1|0|25|quick_rod|",
        "twin_rod_pass|双尾竿·凭证|🎣|0|大鱼乐限定：顶替当前鱼竿（限用 20 次，用完消失）|"
        "|1|0|20|twin_rod|",
        "abyss_bait_pass|深渊秘饵·凭证|🕳️|0|大鱼乐限定：限用 8 次（每一竿都按那种饵的特权抽）|"
        "|1|0|8||abyss_secret",
        "vip_bait_pass|贵客饵·凭证|👑|0|大鱼乐限定：限用 6 次（每一竿都按那种饵的特权抽）|"
        "|1|0|6||vip_bait",
    ],
    # ---- 可调数值表：想改物价 / 爆率 / 属性范围，改这里（或 WebUI）即可 ----
    # 鱼种品质：出现权重（越大越常见）、价值倍数、拉线难度、三维范围
    "rarity_spawn_weights": "常见:14,少见:4.6,稀有:0.95,传说:0.2,神话:0.04",
    "rarity_value_factors": "常见:1.0,少见:2.6,稀有:6.0,传说:15.0,神话:34.0",
    "rarity_difficulty": "常见:0,少见:0,稀有:0.22,传说:0.5,神话:0.82",
    "rarity_attr_ranges": "常见:40-78,少见:48-85,稀有:55-92,传说:62-97,神话:70-100",
    # 16 个钓点各自的「常见」鱼基准价（按钓点顺序）
    "tier_base_values": "5,6,7,10,12,14,18,23,26,32,41,49,60,73,90,113,138,168,205",
    # 同一条鱼每次上钩的个体差异区间
    "value_variance": "0.92-1.12",
    # 三维属性：售价权重、展示名、及格线
    "attr_weights": "meat:0.45,spirit:0.3,sheen:0.25",
    "attr_labels": "meat:肉质,spirit:灵性,sheen:光泽",
    "attr_par": 60.0,
    # 个体品质档位（名称:下限-上限:emoji，从低到高）
    # v1.18.22：把**顶部拉开**了（珍品 1.8-2.5→2.0-3.5、绝品 2.5-4.0→3.5-6.0、神品 4.0-6.0→6.0-10.0）。
    # 原因：品质曲线修好之后绝品从「后期 93%」掉到 15% 上下，如果档位价差还那么平，
    # 手气（鱼饵/鱼竿/玉佩）对收入的影响会小到没意义 —— 拉开以后「钓到绝品」才真的值钱。
    "quality_tiers": "凡品:0.8-1.0:⚪,良品:1.0-1.4:🟢,精品:1.4-2.0:💎,珍品:2.0-3.5:🏆,绝品:3.5-6.0:👑,神品:6.0-10.0:🔱",
    # 上钩率解析失败时的兜底值、未列出品质的默认逃脱率
    "hook_rate_fallback": 0.30,
    "default_escape_rate": 0.25,
    # 难度对「拉线逃脱率」的影响权重（v1.18.19）：
    #   窗口逃脱率 = rarity_escape_chance × ((1−0.5w) + w×鱼种难度) × 天气逃脱倍率 × 鱼竿逃脱率系数
    # 0.5 是历史曲线（0.75 + 0.5×难度）：难度 0.5 的鱼 = 配置值，越难越容易跑。
    # v1.18.26 降到 0.4（0.8 + 0.4×难度）：难鱼仍然更会跑，但不再把神话鱼推到
    # 「良好评价也一半跑掉」的地步。填 0 = 配置里写多少就是多少；想更极端就填 1~2。
    "escape_difficulty_weight": 0.4,
    # 物价总开关：全局倍率 + 单条覆盖（鱼名或 id 均可）
    "fish_value_mult": 1.0,
    "fish_value_overrides": "",
    # 命令别名：`规范子命令|别名,别名`，一行一个。默认值 = 现有的内置别名整表，
    # 解析后「新增别名表」是空的，所以默认配置下的行为与升级前逐字一致。
    "command_aliases": _default_command_aliases_text(),
    # 自定义命令：`命令名|发送:文本` 或 `命令名|执行:子命令;子命令 参数`
    "custom_commands": "",
}

# -----------------------------------------------------------------------------
# 默认值自动同步：改了代码里的数值，站长不用再手点「重置配置」
# -----------------------------------------------------------------------------

#: 这些前缀的配置项属于「站长的个人设置 / 管理操作」，永不被自动同步覆盖
#: （editor_* 是编辑器页面通道回写的状态：跟着指纹同步会把它清空）
DEFAULTS_SYNC_EXCLUDE_PREFIXES: tuple[str, ...] = ("data_", "backup_", "editor_")
#: 这些键同上（开关类与管理项，跟着指纹一起变但没有意义）
DEFAULTS_SYNC_EXCLUDE_KEYS: frozenset[str] = frozenset(
    {
        "button_mode",
        "content_auto_merge",
        "defaults_sync_mode",
        "config_fingerprint",
        # 站长自己改过的键清单：由编辑器页面维护，跟着升级同步就白做了
        "user_edited_keys",
        # 面板路标：纯说明文字，同步它没有任何意义
        "content_tables_hint",
    }
)
#: 内容表（鱼池/钓点/鱼竿/鱼饵/道具…）**不参与默认值同步**：
#: 站长自己编辑过的内容不能被升级覆盖；官方新增内容由 content_auto_merge 增量补。
#:
#: ⚠️ `aquarium_slots` / `backpack_upgrades` **不在**这个名单里（v1.18.16 修的）：
#: 它们虽然也是「内容」，但由 `DEFAULTS_MERGE_LIST_KEYS` 的**合并**逻辑负责
#: （保留站长已有的档位、只补官方新增的尾巴）。以前把它们排除在同步之外，
#: 于是合并逻辑永远不会被执行 —— 官方加了 4 档扩容、3 档鱼缸扩建，
#: 站长的配置里**一档都没多**（游戏内和编辑器里都看不到），这就是站长报的
#: 「扩容并没有生效」。凡是进了 `DEFAULTS_MERGE_*` 的键，都**不能**再排除。
DEFAULTS_SYNC_EXCLUDE_KEYS = DEFAULTS_SYNC_EXCLUDE_KEYS | frozenset(
    {
        "fish_defs",
        "collectible_defs",
        "variant_defs",
        "weather_defs",
        "easter_egg_defs",
        "button_defs",
        "location_defs",
        "rod_defs",
        "bait_defs",
        "item_defs",
        # 称号表（v1.18.17 的后期金币回收口）：内容表，站长改过的称号不许被升级覆盖
        "title_defs",
        # 命令别名 / 自定义命令也是「站长自己写的内容」，同样不许被升级覆盖
        "command_aliases",
        "custom_commands",
        # 大鱼乐奖表（v1.18.51）：站长会自己调爆率/换奖品，不能被升级重置
        "lottery_prizes",
        # 按钮排布与回复文案同理：站长改过的排版/文案不能被升级重置
        "button_layout",
        "text_overrides",
    }
)

#: 「一整段多行文本」形态的内容表：升级时按行**追加**官方新增的条目
#: （保留站长的行序、他改过的行、他自己加的条目）。
#: ⚠️ 这一类表既不进 `CONTENT_LIST_KEYS`（那是给 list 形态的）也在同步排除名单里，
#: 所以必须由 `_merge_text_content_rows()` 专门兜住 —— 漏了就会出现
#: 「官方加了新杂物/新天气，老配置里永远看不到」（`fish_defs` 当年就是这个坑）。
CONTENT_TEXT_KEYS: tuple[str, ...] = (
    "fish_defs",
    "collectible_defs",
    "variant_defs",
    "weather_defs",
    "easter_egg_defs",
    # 按钮表（v1.18.17 加）：官方给每个回复场景都补了按钮，老配置里**缺的那些场景**
    # 必须能自己补进来。按「场景 id」去重，所以站长自己配过的场景一行都不动
    # （他的排版、样式、文案优先级全都保留），只补他完全没有的场景。
    "button_defs",
    # 大鱼乐奖表（v1.18.51）：按行首 id 去重，官方新增奖级能补进老配置，
    # 站长改过的那几行原样保留。
    "lottery_prizes",
)


def _synced_default_keys() -> list[str]:
    """参与「数值同步」的配置键（数值 + 内容表，排除管理类设置）。"""
    return [
        key
        for key in DEFAULTS
        if not key.startswith(DEFAULTS_SYNC_EXCLUDE_PREFIXES)
        and key not in DEFAULTS_SYNC_EXCLUDE_KEYS
    ]


def _defaults_fingerprint() -> str:
    """把参与同步的默认值 + **迁移表**做稳定序列化后取哈希前 8 位。

    只要代码里任何一个数值/内容表变了，指纹就会变，插件启动时据此
    把新默认值写回配置——不需要人工维护版本号。

    ⚠️ **必须把迁移表也算进来**（v1.18.27 修的坑）：
    ``DEFAULTS_VALUE_FIXES`` / ``LOCAL_CONTENT_ROW_FIXES`` 是「官方改过哪些旧值」的登记表，
    但它们本身**不是 DEFAULTS**。以前只哈希 DEFAULTS，于是「只加了一条迁移、没动默认值」的
    版本（比如 v1.18.24 补的深水空竿率阶梯迁移）启动时 `stored == current` → 直接
    early-return，**迁移永远不会执行** —— 站长那边看到的就是「代码改了、配置一个字没变」。
    现在迁移表一变，指纹就变，启动时必然跑一遍同步。
    内容表迁移（`LOCAL_CONTENT_ROW_FIXES`）走的是另一条路（每次启动都跑），
    这里一起算进去只是为了让它更早被复核。

    ⚠️ 必须**调用时现算**：模块初始化后半段会重写 ``DEFAULTS["location_defs"]``
    （由钓点常量生成），提前算出来的指纹会和实际默认值对不上，导致每次启动
    都误判成「数值变了」。
    """
    payload = {
        "defaults": {key: DEFAULTS[key] for key in _synced_default_keys()},
        "value_fixes": {
            key: [[str(old), str(new)] for old, new in pairs]
            for key, pairs in DEFAULTS_VALUE_FIXES.items()
        },
        "row_fixes": [[str(k), str(o), str(n)] for k, o, n in LOCAL_CONTENT_ROW_FIXES],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


# -----------------------------------------------------------------------------
# 升级时**不覆盖站长改过的配置**（v1.18.8）
# -----------------------------------------------------------------------------
# 站长反馈：「更新插件不要把我以前的配置重置，我老是要去改」。
# 做法：
#   1. 编辑器页面每次保存都会把改过的键记进 ``user_edited_keys``，升级时这些键跳过；
#   2. 「容器类」配置（钓点系数表 / 各档基准价 / 品质权重…）改成**合并**而不是覆盖，
#      这样新增钓点、新增品质档位能进老配置，站长自己调过的条目也不会被抹掉；
#   3. 官方改过默认值的「整串」配置（例如品质档位表换了名字）登记在
#      ``DEFAULTS_VALUE_FIXES``：只有当前值**逐字等于旧默认**时才替换；
#   4. 第一次启用这个机制时（配置里还没有 ``user_edited_keys``）把「当前值 ≠ 新默认」
#      的键统统当成站长改过的记下来，**这次升级一个都不覆盖**。

#: 这些键是「键:值」形式的映射表：升级时保留站长已有的条目，只补上官方新增的键
DEFAULTS_MERGE_MAP_KEYS: tuple[str, ...] = (
    "location_hook_factors",
    "bait_hook_rates",
    "rarity_spawn_weights",
    "rarity_value_factors",
    "rarity_difficulty",
    "rarity_attr_ranges",
    "rarity_escape_chance",
    "attr_weights",
    "attr_labels",
)

#: 这些键是真正的列表：升级时保留站长已有的项，只在**比新默认短**时补尾巴
DEFAULTS_MERGE_LIST_KEYS: tuple[str, ...] = (
    "quality_weights",
    "rarity_display_names",
    "codex_bonus_per_rarity",
    "backpack_upgrades",
    "aquarium_slots",
    "title_defs",
)

#: 这些键是「,」分隔的数字文本（列表型）：同样按长度补尾巴
DEFAULTS_MERGE_NUMBER_TEXT_KEYS: tuple[str, ...] = ("tier_base_values",)

#: 官方改过默认值的整串配置：``键 -> ((旧默认, 新默认), ...)``
#: 只有配置里的值**逐字等于旧默认**才替换（站长自己改过的绝不碰）
DEFAULTS_VALUE_FIXES: dict[str, tuple[tuple[Any, Any], ...]] = {
    # v1.18.8：个体品质改名（原来叫 普通/优良/稀有/极品/传说/神话，
    # 和鱼种稀有度撞名 → 改成 凡品/良品/精品/珍品/绝品/神品）
    "quality_tiers": (
        # v1.18.22：拉开顶部（绝品 2.5-4.0 → 3.5-6.0、神品 4.0-6.0 → 6.0-10.0），
        # 配合新的品质曲线 —— 绝品从「后期 93%」掉到 15% 上下，档位价差不拉开的话
        # 手气（鱼饵/鱼竿/玉佩）对收入几乎没影响。只有逐字等于旧默认才替换。
        (
            "凡品:0.8-1.0:⚪,良品:1.0-1.35:🟢,精品:1.35-1.8:💎,珍品:1.8-2.5:🏆,绝品:2.5-4.0:👑,神品:4.0-6.0:🔱",
            "凡品:0.8-1.0:⚪,良品:1.0-1.4:🟢,精品:1.4-2.0:💎,珍品:2.0-3.5:🏆,绝品:3.5-6.0:👑,神品:6.0-10.0:🔱",
        ),
        (
            "普通:0.8-1.0:⚪,优良:1.0-1.35:🟢,稀有:1.35-1.8:💎,极品:1.8-2.5:🏆,传说:2.5-4.0:👑",
            "凡品:0.8-1.0:⚪,良品:1.0-1.4:🟢,精品:1.4-2.0:💎,珍品:2.0-3.5:🏆,绝品:3.5-6.0:👑,神品:6.0-10.0:🔱",
        ),
        (
            "普通:0.8-1.0:⚪,优良:1.0-1.35:🟢,稀有:1.35-1.8:💎,极品:1.8-2.5:🏆,传说:2.5-4.0:👑,神话:4.0-6.0:🔱",
            "凡品:0.8-1.0:⚪,良品:1.0-1.4:🟢,精品:1.4-2.0:💎,珍品:2.0-3.5:🏆,绝品:3.5-6.0:👑,神品:6.0-10.0:🔱",
        ),
    ),
    # v1.18.15：挂机单次封顶 5000 -> 10 万（5000 在 30 级以后等于零，整套养鱼玩法失去意义）。
    # 只在站长没动过这个值时替换 —— 他自己填过别的数就照他的来。
    "pond_income_cap_coins": ((5000, 100000),),
    # v1.18.24：**深水空竿率阶梯**。v1.18.16 把阶梯从「1.0 → 0.54」改成「1.0 → 0.82」
    # （他当时报「倒数第四个钓点用最好的配置依旧相当于一半空杆」），但改的时候**没登记整串迁移**，
    # 而这个键是「键:值 映射表」（`DEFAULTS_MERGE_MAP_KEYS`）—— 升级只补**缺的项**，
    # 19 个钓点一个不缺，于是老配置里那套 0.54 的旧阶梯永远留着。站长这次问
    # 「一半空竿的问题解决没有，为什么好像还是没变」，就是代码改了、配置没改。
    # 现在按「逐字等于旧默认才替换」补上：他自己调过任何一个数，这条整个不生效。
    "location_hook_factors": (
        (
            "novice:1.0,bamboo:1.0,canal:1.0,lake:0.95,reed:0.92,sea:0.89,dock:0.86,"
            "night:0.83,mangrove:0.80,swamp:0.77,cave:0.74,ruins:0.71,abyss:0.68,"
            "trench:0.65,glacier:0.62,aurora:0.60,starfall:0.58,void_sea:0.56,"
            "dragon_palace:0.54",
            DEFAULTS["location_hook_factors"],
        ),
        # v1.18.27：站长「我让你提高这个」—— 0.82 那一版也得跟着抬到 0.92。
        # 已经在 v1.18.16 之后升过级的配置落在这条上（同样只在逐字相等时替换）。
        (
            "novice:1.0,bamboo:1.0,canal:1.0,lake:0.96,reed:0.95,sea:0.94,dock:0.92,"
            "night:0.91,mangrove:0.90,swamp:0.89,cave:0.88,ruins:0.87,abyss:0.87,"
            "trench:0.86,glacier:0.85,aurora:0.85,starfall:0.84,void_sea:0.83,"
            "dragon_palace:0.82",
            DEFAULTS["location_hook_factors"],
        ),
    ),
    # v1.18.26：站长报「降低各钓点的逃脱率，太高了」。实测他的配置：
    #   传说 30% / 神话 49%（良好评价）、**连钓里的神话 95%（封顶必跑）**、
    #   偏差评价 78% —— 深水图钓到大鱼基本等于看它跑。
    # 三个键一起降：基础逃脱率、难度权重、连钓系数。同样只在逐字等于旧默认时替换。
    "rarity_escape_chance": (("传说:0.30,神话:0.42", DEFAULTS["rarity_escape_chance"]),),
    "escape_difficulty_weight": ((0.5, DEFAULTS["escape_difficulty_weight"]),),
    "multi_escape_mult": ((2.5, DEFAULTS["multi_escape_mult"]),),
    # v1.18.29：洗髓丹的两层上限默认放开成「不限」（站长要的是「别限我次数」）。
    # 只在配置里**逐字还是旧默认**（每条鱼 3 颗 / 全缸 30 颗）时替换成 0 ——
    # 他自己填过别的数就照他的来。走这张表而不是靠「值≠新默认就同步」，
    # 是因为首次启用同步机制的机器会把「值≠新默认」当成「站长改过」而保住旧值，
    # 登记在这里能让升级路径也覆盖到那批配置。
    "reroll_daily_limit": ((3, DEFAULTS["reroll_daily_limit"]),),
    "reroll_daily_total": ((30, DEFAULTS["reroll_daily_total"]),),
    # v1.18.62：加了「每条鱼每天最多用几次」的道具上限（默认 2）。老配置里没有这一项，
    # 走 DEFAULTS 补上即可；这里登记是为了让 DEFAULTS 里那个 2 也写进 schema。
    "feed_bonus_cap": ((20, DEFAULTS["feed_bonus_cap"]),),
}

#: 「官方改过的内容行」：`(配置键, 旧整行, 新整行)`，只有配置里那一行**逐字等于旧行**
#: 才替换（站长自己动过的行一律不碰）。用法与 `_game_data.CONTENT_ROW_FIXES` 完全一样。
#:
#: 为什么不写进 `_game_data.py`：**内容表的默认值在哪个文件，迁移就写在哪个文件** ——
#: `item_defs` / `rod_defs` / `bait_defs` 的默认值就在这个文件里（DEFAULTS），
#: 改它们的人顺手在这里登记，不用跨文件找。
LOCAL_CONTENT_ROW_FIXES: tuple[tuple[str, str, str], ...] = (
    # v1.18.16：道具整套重定价格 + 新增「解锁等级」那一段（13 件道具的大改版）。
    # 七件老道具逐条登记，老配置里没被改过的那一行会自动升级到新价/新数值。
    (
        "item_defs",
        "feed_basic|普通饲料|🌾|20|喂鱼：肉+2、灵+1（永久）|meat=2;spirit=1",
        "feed_basic|普通饲料|🌾|20|喂鱼：肉+2、灵+1（永久）|meat=2;spirit=1|1",
    ),
    (
        "item_defs",
        "feed_premium|高级饲料|🍖|80|喂鱼：肉+5、灵+4、光+3（永久）|meat=5;spirit=4;sheen=3",
        "feed_premium|高级饲料|🍖|90|喂鱼：肉+5、灵+4、光+3（永久）|meat=5;spirit=4;sheen=3|6",
    ),
    (
        "item_defs",
        "feed_divine|仙露|💧|300|喂鱼：三维各 +10，估值 +600（永久）|"
        "meat=10;spirit=10;sheen=10;value_up=600",
        "feed_divine|仙露|💧|420|喂鱼：三维各 +10，估值 +600（永久）|"
        "meat=10;spirit=10;sheen=10;value_up=600|14",
    ),
    (
        "item_defs",
        "growth_tonic|育灵水|🌱|500|喂鱼：这条鱼的投喂上限 +5 次|feed_bonus=5",
        "growth_tonic|育灵水|🌱|800|喂鱼：这条鱼的投喂上限 +5 次|feed_bonus=5|18",
    ),
    (
        "item_defs",
        "pill_quality|洗髓丹|🔮|1200|重掷这条鱼的个体品质（取更好的那次，不影响三维）；"
        "极小概率直接洗出「神品」|quality_reroll=3",
        "pill_quality|洗髓丹|🔮|2500|重掷这条鱼的个体品质（取更好的那次，不影响三维）；"
        "极小概率直接洗出「神品」|quality_reroll=3|20",
    ),
    (
        "item_defs",
        "lucky_jade|锦鲤玉佩|🎐|500|带在身上：接下来 20 竿手气更好|buff_quality=0.30",
        "lucky_jade|锦鲤玉佩|🎐|6000|带在身上：接下来 20 竿手气更好|buff_quality=0.20|31",
    ),
    (
        "item_defs",
        "coral_deco|珊瑚造景|🪸|260|摆进鱼缸：72 小时内挂机产出 +20%|decorate=0.20",
        "coral_deco|珊瑚造景|🪸|3000|摆进鱼缸：72 小时内挂机产出 +20%|decorate=0.20|27",
    ),
    # v1.18.19：站长问「道具的价格是不是太便宜了，你自己考虑一下吧」——
    # 拿真实收益算了一遍 ROI（收益 ÷ 价格），把这五件**明显偏低**的调上去
    # （目标 ROI 约 1.8：买它划算，但要付出代价）：
    #   潮汐香 8.4x / 玉佩 4.1x / 玉髓灯 2.7x / 洗髓丹 2.8x / 珊瑚王座 3.0x
    # 仙露（1.5x）、龙涎饲料（1.7x）、育灵水、珍珠梳、造景（1.7x）本来就合理，不动。
    (
        "item_defs",
        "lucky_jade|锦鲤玉佩|🎐|6000|带在身上：接下来 20 竿手气更好|buff_quality=0.20|31",
        "lucky_jade|锦鲤玉佩|🎐|12000|带在身上：接下来 20 竿手气更好|buff_quality=0.20|31",
    ),
    (
        "item_defs",
        "tide_incense|潮汐香|🕯️|4500|带在身上：接下来 40 竿手气小幅提升|buff_quality=0.15|36",
        "tide_incense|潮汐香|🕯️|18000|带在身上：接下来 40 竿手气小幅提升|"
        "buff_quality=0.15;buff_casts=40|36",
    ),
    (
        "item_defs",
        "jade_lantern|玉髓灯|🏮|12000|带在身上：接下来 15 竿手气大幅提升|buff_quality=0.35|40",
        "jade_lantern|玉髓灯|🏮|18000|带在身上：接下来 15 竿手气大幅提升|"
        "buff_quality=0.35;buff_casts=15|40",
    ),
    (
        "item_defs",
        "pill_quality|洗髓丹|🔮|2500|重掷这条鱼的个体品质（取更好的那次，不影响三维）；"
        "极小概率直接洗出「神品」|quality_reroll=3|20",
        "pill_quality|洗髓丹|🔮|4000|重掷这条鱼的个体品质（取更好的那次，不影响三维）；"
        "极小概率直接洗出「神品」|quality_reroll=3|20",
    ),
    (
        "item_defs",
        "coral_king|珊瑚王座|👑|26000|摆进鱼缸：72 小时内挂机产出 +45%|decorate=0.45|52",
        "coral_king|珊瑚王座|👑|40000|摆进鱼缸：72 小时内挂机产出 +45%|decorate=0.45|52",
    ),
    # v1.18.21：站长自己改过的那三行（he 把描述也改成了自己的话），他明确要求
    # 「这三个给我同步修改了」—— 逐字登记他那三行 -> 新价新数值（保留他的描述）。
    # ⚠️ 为什么要登记他的行：直接改他的配置文件会被**正在运行的插件**用内存里的旧配置
    # 覆盖回去（v1.18.19 就踩了这个坑：改完文件，插件一存盘又变回 80/300/260），
    # 只有走插件自己的迁移链路才稳。
    (
        "item_defs",
        "feed_premium|高级饲料|🍖|80|营养均衡，长得快|meat=5;spirit=4;sheen=3",
        "feed_premium|高级饲料|🍖|90|营养均衡，长得快|meat=5;spirit=4;sheen=3|6",
    ),
    (
        "item_defs",
        "feed_divine|仙露|💧|300|传说中的养鱼圣品|meat=10;spirit=10;sheen=10;value_up=150",
        "feed_divine|仙露|💧|420|传说中的养鱼圣品|"
        "meat=10;spirit=10;sheen=10;value_up=600|14",
    ),
    (
        "item_defs",
        "coral_deco|珊瑚造景|🪸|260|水族馆装饰，提升馆藏价值|decorate=0.20",
        "coral_deco|珊瑚造景|🪸|3000|水族馆装饰，提升馆藏价值|decorate=0.20|27",
    ),
    # v1.18.22：品质曲线修好之后，手气道具的**边际收益**从 +33% 掉到 +4~10%
    # （老算法一过 0.53 手气就把概率全倒进绝品，所以那时它们又强又假），
    # 按同一个 ROI 1.8 的口径重算，价格必须跟着下来，否则就是明摆着的坑：
    #   玉佩 4077 收益 / 12000 = 0.34x（新价 2400 → 1.7x）
    #   潮汐香 5613 / 18000 = 0.31x（新价 3200 → 1.75x）
    #   玉髓灯 4960 / 18000 = 0.28x（新价 2800 → 1.77x）
    (
        "item_defs",
        "lucky_jade|锦鲤玉佩|🎐|12000|带在身上：接下来 20 竿手气更好|buff_quality=0.20|31",
        "lucky_jade|锦鲤玉佩|🎐|2400|带在身上：接下来 20 竿手气更好|buff_quality=0.20|31",
    ),
    (
        "item_defs",
        "tide_incense|潮汐香|🕯️|18000|带在身上：接下来 40 竿手气小幅提升|"
        "buff_quality=0.15;buff_casts=40|36",
        "tide_incense|潮汐香|🕯️|3200|带在身上：接下来 40 竿手气小幅提升|"
        "buff_quality=0.15;buff_casts=40|36",
    ),
    (
        "item_defs",
        "jade_lantern|玉髓灯|🏮|18000|带在身上：接下来 15 竿手气大幅提升|"
        "buff_quality=0.35;buff_casts=15|40",
        "jade_lantern|玉髓灯|🏮|2800|带在身上：接下来 15 竿手气大幅提升|"
        "buff_quality=0.35;buff_casts=15|40",
    ),
    # 他自己那三行（v1.18.21 登记的）也会跟着走到新价：迁移是**按顺序逐条套用**的
    # （6000 → v1.18.19 的 12000 → v1.18.22 的 2400），所以不用为他的旧价单独再写一条。
    # v1.18.23：站长选了「品质保底」方案 —— 三件道具从「+手气」改成「接下来 N 竿品质保底」，
    # 价格也回到大件的档位（口径同样是 ROI ≈ 1.8，终局每竿 3300 金）：
    #   玉佩   20 竿保底珍品（+32%）→ 收益 21186，价格 12000（ROI 1.77）
    #   潮汐香 40 竿保底珍品（+32%）→ 收益 42372，价格 23000（ROI 1.84）
    #   玉髓灯 15 竿保底绝品（+100%）→ 收益 49350，价格 27000（ROI 1.83）
    (
        "item_defs",
        "lucky_jade|锦鲤玉佩|🎐|2400|带在身上：接下来 20 竿手气更好|buff_quality=0.20|31",
        "lucky_jade|锦鲤玉佩|🎐|12000|带在身上：接下来 20 竿品质不低于「珍品」|"
        "quality_floor=2.0|31",
    ),
    (
        "item_defs",
        "tide_incense|潮汐香|🕯️|3200|带在身上：接下来 40 竿手气小幅提升|"
        "buff_quality=0.15;buff_casts=40|36",
        "tide_incense|潮汐香|🕯️|23000|带在身上：接下来 40 竿品质不低于「珍品」|"
        "quality_floor=2.0;buff_casts=40|36",
    ),
    (
        "item_defs",
        "jade_lantern|玉髓灯|🏮|2800|带在身上：接下来 15 竿手气大幅提升|"
        "buff_quality=0.35;buff_casts=15|40",
        "jade_lantern|玉髓灯|🏮|27000|带在身上：接下来 15 竿品质不低于「绝品」|"
        "quality_floor=3.5;buff_casts=15|40",
    ),
)

#: 「站长已经同意交还给插件」的配置键（v1.18.21）：
#: 他改过这些值，但**明确采用了我给的新值**（在对话里勾选的），所以启动时把它们从
#: ``user_edited_keys`` 里摘掉，让默认值同步照常把它们推到新版 —— 以后版本升级也能继续跟着走。
#: ⚠️ 光改配置文件是不够的：正在运行的插件会用内存里的旧配置覆盖回去（踩过）。
#: 想反悔：把键名从这张表里删掉，再自己去编辑器里改一遍（编辑一次就又回到「他改过的」）。
CONSENTED_DEFAULT_KEYS: tuple[str, ...] = (
    "order_reward_mult",       # 2.2 -> 1.6（订单价改成动态系数，基准倍率必须跟着降）
    "pond_income_cap_hours",   # 12 -> 24（睡一觉回来还在攒）
    "feed_max_uses",           # 5 -> 10（投喂上限翻倍）
    "pond_income_per_hour",    # 0.03 -> 0.02（回到出厂费率）
)


def _merge_map_text(old: Any, new: str) -> Any:
    """「键:值,键:值」形式的配置：保留 old 里已有的键值，补上 new 里缺的键。"""
    if not isinstance(old, str) or not old.strip():
        return new
    parts = [piece.strip() for piece in old.split(",") if piece.strip()]
    seen = {piece.split(":", 1)[0].strip() for piece in parts}
    for piece in str(new).split(","):
        piece = piece.strip()
        if not piece:
            continue
        key = piece.split(":", 1)[0].strip()
        if key not in seen:
            parts.append(piece)
            seen.add(key)
    return ",".join(parts)


def _merge_list_tail(old: Any, new: list[Any]) -> Any:
    """列表型配置：老值比新默认短就补尾巴（站长已有的项一个不动）。"""
    if not isinstance(old, list):
        return new
    if len(old) >= len(new):
        return old
    return list(old) + list(new[len(old):])


def _merge_number_text(old: Any, new: str) -> Any:
    """「1,2,3」形式的数字文本：按个数补尾巴。"""
    if not isinstance(old, str) or not old.strip():
        return new
    parts = [piece.strip() for piece in old.split(",") if piece.strip()]
    fresh = [piece.strip() for piece in str(new).split(",") if piece.strip()]
    if len(parts) >= len(fresh):
        return old
    return ",".join(parts + fresh[len(parts):])

# =============================================================================
# 二、鱼种品质（固有属性，5 档）
# =============================================================================

#: 品质顺序（从低到高），下标即稀有度等级
RARITY_ORDER = ["常见", "少见", "稀有", "传说", "神话"]
RARITY_RANK = {name: idx for idx, name in enumerate(RARITY_ORDER)}
RARITY_EMOJI = {
    "常见": "🐟",
    "少见": "🐠",
    "稀有": "🐡",
    "传说": "✨",
    "神话": "🌈",
}
#: 高稀有度鱼种更贵，个体数值区间也更高
RARITY_VALUE_RANGE = {          # 每单位图鉴基准价的最终价格倍数参考（仅用于说明）
    "常见": (0.8, 1.3),
    "少见": (0.9, 1.4),
    "稀有": (1.0, 1.5),
    "传说": (1.1, 1.7),
    "神话": (1.2, 2.0),
}
#: 钓上来时三维数值的生成区间
RARITY_ATTR_RANGE = {
    "常见": (40, 78),
    "少见": (48, 85),
    "稀有": (55, 92),
    "传说": (62, 97),
    "神话": (70, 100),
}

# =============================================================================
# 三、鱼类图鉴
# =============================================================================
#
# 字段说明：
#   id      唯一标识，写进玩家存档，**不要随意修改或删除**
#   name    展示名
#   rarity  鱼种品质（固有）
#   value   基准价值
#   weight  基础权重（概率 = weight / 总权重）
#   flavor  图鉴风味描述（简短）
#   diff    难度 0~1：越高 -> 拉线窗口越短、越容易跑。只对需要互动的品质有意义
#   drift   最佳点位偏移 -0.5~0.5：0 = 正中间，负数偏前，正数偏后
#
# 参考了常见钓鱼游戏，除了鱼还有虾蟹贝类、水母、海龟、甚至匣子与许愿瓶。

FISH_POOL: list[dict[str, Any]] = [
    # ================= 常见（浅水小鱼小虾）= 9 种 =================
    {"id": "white_bait", "name": "白条", "rarity": "常见", "value": 3,
     "weight": 9, "diff": 0.0, "drift": 0.0, "flavor": "细长的小鱼，抢饵最积极"},
    {"id": "small_crucian", "name": "小鲫鱼", "rarity": "常见", "value": 4,
     "weight": 8, "diff": 0.0, "drift": 0.0, "flavor": "最常见的鱼，脾气很好"},
    {"id": "river_shrimp", "name": "河虾", "rarity": "常见", "value": 3,
     "weight": 8, "diff": 0.0, "drift": 0.0, "flavor": "半透明的小家伙，弹跳力惊人"},
    {"id": "mud_snail", "name": "田螺", "rarity": "常见", "value": 1,
     "weight": 8, "diff": 0.0, "drift": 0.0, "flavor": "慢吞吞地贴在钩上，懒得挣扎"},
    {"id": "loach", "name": "泥鳅", "rarity": "常见", "value": 4,
     "weight": 7, "diff": 0.0, "drift": 0.0, "flavor": "滑不溜手，抓它得有点耐心"},
    {"id": "crucian", "name": "河鲫", "rarity": "常见", "value": 5,
     "weight": 7, "diff": 0.0, "drift": 0.0, "flavor": "银光闪闪，成群结队"},
    {"id": "carp", "name": "鲤鱼", "rarity": "常见", "value": 7,
     "weight": 6, "diff": 0.0, "drift": 0.0, "flavor": "力气不小，尾巴拍水很响"},
    {"id": "topmouth_culter", "name": "翘嘴鲌", "rarity": "常见", "value": 8,
     "weight": 6, "diff": 0.0, "drift": 0.0, "flavor": "喜欢在水面追着小鱼跑"},
    {"id": "grass_carp", "name": "草鱼", "rarity": "常见", "value": 11,
     "weight": 5, "diff": 0.0, "drift": 0.0, "flavor": "吃草长大的，肉厚实"},

    # ================= 少见 = 7 种 =================
    {"id": "catfish", "name": "鲶鱼", "rarity": "少见", "value": 24,
     "weight": 4, "diff": 0.0, "drift": 0.0, "flavor": "躲在淤泥里，胡须很灵敏"},
    {"id": "snakehead", "name": "黑鱼", "rarity": "少见", "value": 31,
     "weight": 4, "diff": 0.0, "drift": 0.0, "flavor": "水中恶霸，牙齿很锋利"},
    {"id": "river_crab", "name": "河蟹", "rarity": "少见", "value": 27,
     "weight": 3, "diff": 0.0, "drift": 0.0, "flavor": "横着走，钳子夹人很疼"},
    {"id": "bass", "name": "鳜鱼", "rarity": "少见", "value": 36,
     "weight": 3, "diff": 0.0, "drift": 0.0, "flavor": "肉食性，专挑小鱼下手"},
    {"id": "bighead_carp", "name": "鳙鱼", "rarity": "少见", "value": 32,
     "weight": 3, "diff": 0.0, "drift": 0.0, "flavor": "脑袋特别大，游得慢"},
    {"id": "softshell", "name": "甲鱼", "rarity": "少见", "value": 46,
     "weight": 2, "diff": 0.0, "drift": 0.0, "flavor": "咬住就不松口，小心取钩"},
    {"id": "eel", "name": "河鳗", "rarity": "少见", "value": 51,
     "weight": 2, "diff": 0.0, "drift": 0.0, "flavor": "细长有力，缠线一把好手"},

    # ================= 稀有 = 6 种 =================
    {"id": "mandarin_fish", "name": "鳜花鱼", "rarity": "稀有", "value": 85,
     "weight": 2, "diff": 0.0, "drift": 0.0, "flavor": "花纹华美，肉嫩刺少"},
    {"id": "giant_salamander", "name": "娃娃鱼", "rarity": "稀有", "value": 131,
     "weight": 1.5, "diff": 0.0, "drift": 0.0, "flavor": "叫声像婴儿，脾气却很大"},
    {"id": "pearl_shell", "name": "珍珠贝", "rarity": "稀有", "value": 108,
     "weight": 1.5, "diff": 0.0, "drift": 0.0, "flavor": "壳里偶尔藏着好东西"},
    {"id": "seahorse", "name": "海马", "rarity": "稀有", "value": 119,
     "weight": 1.5, "diff": 0.0, "drift": 0.0, "flavor": "游泳姿势相当优雅"},
    {"id": "cuttlefish", "name": "墨鱼", "rarity": "稀有", "value": 136,
     "weight": 1.5, "diff": 0.0, "drift": 0.0, "flavor": "被抓住就喷你一脸墨"},
    {"id": "sturgeon", "name": "中华鲟", "rarity": "稀有", "value": 170,
     "weight": 1.0, "diff": 0.0, "drift": 0.0, "flavor": "活化石级别的大家伙"},

    # ================= 传说（需要拉线）================= 5 种 =================
    {"id": "koi", "name": "锦鲤", "rarity": "传说", "value": 294,
     "weight": 1.2, "diff": 0.30, "drift": 0.0, "flavor": "据说见到它会有好运气"},
    {"id": "golden_turtle", "name": "金龟", "rarity": "传说", "value": 363,
     "weight": 1.0, "diff": 0.40, "drift": -0.06, "flavor": "背甲泛着金光，慢吞吞地划水"},
    {"id": "discus", "name": "七彩神仙鱼", "rarity": "传说", "value": 431,
     "weight": 0.9, "diff": 0.50, "drift": 0.06, "flavor": "水族箱里的活宝石"},
    {"id": "moon_jellyfish", "name": "月华水母", "rarity": "传说", "value": 500,
     "weight": 0.8, "diff": 0.62, "drift": 0.14, "flavor": "半透明伞盖泛着淡蓝月光"},
    {"id": "arowana", "name": "金龙鱼", "rarity": "传说", "value": 624,
     "weight": 0.6, "diff": 0.72, "drift": -0.12, "flavor": "鳞片如金甲，游动雍容华贵"},

    # ================= 神话（需要拉线，最难）================= 4 种 =================
    {"id": "deep_anglerfish", "name": "深海鮟鱇", "rarity": "神话", "value": 907,
     "weight": 0.35, "diff": 0.70, "drift": 0.0, "flavor": "额前挂着一盏幽蓝小灯"},
    {"id": "dragon_koi", "name": "龙鲤", "rarity": "神话", "value": 1247,
     "weight": 0.30, "diff": 0.82, "drift": -0.16, "flavor": "传说跃过龙门就会化龙"},
    {"id": "abyss_whale", "name": "深渊鲸", "rarity": "神话", "value": 1701,
     "weight": 0.20, "diff": 0.90, "drift": 0.18, "flavor": "深海巨影，一次摆尾掀起暗流"},
    {"id": "kun", "name": "鲲", "rarity": "神话", "value": 2552,
     "weight": 0.15, "diff": 0.97, "drift": 0.0, "flavor": "北冥有鱼，其名为鲲"},

    # ================= 彩蛋鱼（各钓点专属，出现率极低）=================
    # 它们也是正常鱼种：进图鉴、需要拉线、能喂能卖，只是概率特别低。
    {"id": "boot_carp", "name": "靴子鲤", "rarity": "少见", "value": 60,
     "weight": 0.5, "diff": 0.0, "drift": 0.0,
     "flavor": "有人把破靴子扔进池塘，它就在里面安了家"},
    {"id": "bamboo_shrimp", "name": "竹节虾", "rarity": "稀有", "value": 320,
     "weight": 0.4, "diff": 0.0, "drift": 0.0,
     "flavor": "一节一节的花纹，藏在竹影下面"},
    {"id": "lantern_fish", "name": "灯笼鱼", "rarity": "稀有", "value": 400,
     "weight": 0.4, "diff": 0.0, "drift": 0.0,
     "flavor": "成群挂在堤坝边，像一串小灯笼"},
    {"id": "ghost_jelly", "name": "幽灵水母", "rarity": "传说", "value": 900,
     "weight": 0.3, "diff": 0.55, "drift": 0.10,
     "flavor": "半透明得像一缕烟，雾天才会出现"},
    {"id": "loach_king", "name": "泥鳅王", "rarity": "传说", "value": 1100,
     "weight": 0.25, "diff": 0.60, "drift": -0.15,
     "flavor": "传说泥鳅活过百年就会长出龙须"},
    {"id": "pearl_lume", "name": "夜明珠贝", "rarity": "传说", "value": 1550,
     "weight": 0.15, "diff": 0.42, "drift": 0.0,
     "flavor": "壳缝里透出微光，月夜里最亮"},
    {"id": "golden_hook", "name": "金钩鱼", "rarity": "神话", "value": 3400,
     "weight": 0.10, "diff": 0.88, "drift": 0.15,
     "flavor": "嘴里叼着一枚金色鱼钩——上一任主人的吧"},
]

# =============================================================================
# 三·四、鱼种扩充：从同目录 _fish_data.py 读入「16 个钓点 × 12 种」的名单
# =============================================================================
#
# 名单文件只放内容（id/名字/品质/风味），数值一律在这里算：
#   基准价 = 档位基准 × 品质系数 × 稳定抖动（同一个 id 每次启动都一样）
# 与老图鉴**按名字去重**：同名直接复用老 id，老存档里的鱼不会变成「未知」。

#: 钓点档位顺序（决定新鱼的价值档位；与 _fish_data.py 的 key 顺序一致）
LOCATION_TIER_ORDER: tuple[str, ...] = (
    "novice", "bamboo", "canal", "lake", "reed", "sea", "dock", "night",
    "mangrove", "swamp", "cave", "ruins", "abyss", "trench", "glacier", "aurora",
    "starfall", "void_sea", "dragon_palace",
)

#: 各档「常见」鱼的基准价（每档约 ×1.22，是整条价值曲线的主干）
TIER_BASE_VALUE: tuple[float, ...] = (
    5, 6, 7, 10, 12, 14, 18, 23, 26, 32, 41, 49, 60, 73, 90, 113,
    138, 168, 205,
)

#: 品质价值系数（相对同档「常见」）
ROSTER_RARITY_FACTOR: dict[str, float] = {
    "常见": 1.0, "少见": 2.6, "稀有": 6.0, "传说": 15.0, "神话": 34.0,
}

#: 品质基础权重（同一钓点内每种鱼的相对出现率）
ROSTER_RARITY_WEIGHT: dict[str, float] = {
    "常见": 14.0, "少见": 4.6, "稀有": 0.95, "传说": 0.20, "神话": 0.04,
}

#: 互动难度（只有 传说/神话 真的会进拉线互动，其余留 0）
ROSTER_RARITY_DIFF: dict[str, float] = {
    "常见": 0.0, "少见": 0.0, "稀有": 0.22, "传说": 0.50, "神话": 0.82,
}


def _stable_jitter(key: str, low: float = 0.88, high: float = 1.16) -> float:
    """按 id 生成稳定抖动（同一 id 每次启动结果一致，不会让价格漂移）。"""
    span = high - low
    return low + (zlib.crc32(key.encode("utf-8")) % 1000) / 1000.0 * span


def _load_roster_data() -> dict[str, list[dict[str, Any]]]:
    """读取同目录 _fish_data.py 里的鱼种名单；读不到就返回空表。"""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fish_data.py")
        spec = importlib.util.spec_from_file_location("astrbot_fishing_roster", path)
        if spec is None or spec.loader is None:
            return {}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        data = getattr(module, "LOCATION_ROSTERS", None)
        if isinstance(data, dict) and data:
            return data
        logger.warning("鱼种名单为空，使用内置图鉴")
    except Exception as e:  # pragma: no cover - 只在名单文件损坏时触发
        logger.warning(f"鱼种名单加载失败，使用内置图鉴：{e}")
    return {}




# -----------------------------------------------------------------------------
# 内容表接入（杂物 / 变异 / 天气 / 彩蛋）
# 与 fish_defs 同构：配置接管 → 空值或写坏则回退内置（首调用时记录的内置快照）。
# -----------------------------------------------------------------------------
#: 表名 -> (配置键, 解析器名)
CONTENT_TABLE_MAP: dict[str, tuple[str, str]] = {
    "COLLECTIBLES": ("collectible_defs", "_parse_collectible_defs"),
    "VARIANTS": ("variant_defs", "_parse_variant_defs"),
    "WEATHERS": ("weather_defs", "_parse_weather_defs"),
    "EASTER_EGGS": ("easter_egg_defs", "_parse_easter_egg_defs"),
}

#: 内置快照：首次应用时记录，之后回退一直用它（避免配置写坏后残留上一次的值）
_BUILTIN_CONTENT: dict[str, list[dict[str, Any]]] = {}


def _builtin_content(table: str, current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """取内置快照；第一次调用时用当前值建立快照。"""
    if table not in _BUILTIN_CONTENT:
        _BUILTIN_CONTENT[table] = [dict(item) for item in current]
    return _BUILTIN_CONTENT[table]


def _restore_content(table: str, target: list[dict[str, Any]]) -> None:
    """把模块级常量恢复成内置快照（原地替换，保持对象引用不变）。"""
    target[:] = [dict(item) for item in _builtin_content(table, target)]


def _load_content_defaults() -> dict[str, str]:
    """读取 _game_data.py 里的 4 个 `*_DEFS_DEFAULT`，作为配置项默认值。"""
    result: dict[str, str] = {}
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_game_data.py")
        spec = importlib.util.spec_from_file_location("astrbot_fishing_content_defs", path)
        if spec is None or spec.loader is None:
            return result
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for table, (key, _parser) in CONTENT_TABLE_MAP.items():
            const = table[:-1] + "_DEFS_DEFAULT" if table.endswith("S") else ""
            # 去掉三引号常量末尾的换行，保证与 schema 的默认值逐字节一致
            text = str(getattr(module, const, "") or "").strip("\n")
            if text:
                result[key] = text
        # 按钮表也放在 _game_data.py 里（大块文本不撑大 main.py）
        button_text = str(getattr(module, "BUTTON_DEFS_DEFAULT", "") or "").strip("\n")
        if button_text:
            result["button_defs"] = button_text
    except Exception as e:  # pragma: no cover - 只在数据文件损坏时触发
        logger.warning(f"读取内容表默认值失败，改用内置数据：{e}")
    return result


def _apply_content_tables(cfg: dict[str, Any]) -> None:
    """用配置接管 4 张内容表；留空或写坏则回退内置。"""
    for table, (key, parser_name) in CONTENT_TABLE_MAP.items():
        target = globals().get(table)
        if not isinstance(target, list):
            continue
        raw = _cfg_str(cfg, key).strip()
        if not raw:
            _restore_content(table, target)
            continue
        parser = getattr(CALC, parser_name, None)
        if parser is None:
            _restore_content(table, target)
            continue
        rows = parser(raw, warn=lambda msg, _k=key: _tunable_warn(_k, msg))
        if not rows:
            _tunable_warn(key, "没有解析出有效内容，已回退内置")
            _restore_content(table, target)
            continue
        target[:] = rows
#: 按钮表：场景 -> [(文案, 指令, 样式), ...]（可被 button_defs 配置接管，原地更新）
BUTTONS: dict[str, list[tuple[str, str, int]]] = {}
#: 按钮样式全局设置（v1.13.0）：table = 每行各写各的（历史行为）/ uniform = 全部统一
BUTTON_STYLE_MODE: str = "table"
#: 「统一」时用的样式名；也是按钮表里没写样式时的兜底
BUTTON_DEFAULT_STYLE: str = "default"
#: 明确不要按钮的场景（原地更新，兄弟模块共享同一个对象）
BUTTON_EMPTY_SCENES: set[str] = set()
#: 上一次扩展加载的摘要（用来避免每次应用配置都重复打日志）
EXT_REPORT_ROWS: list[str] = []

#: 内置快照：首次应用时从默认文本解析一次（**没应用「统一样式」策略**，保持原味）
_BUILTIN_BUTTONS_RAW: dict[str, list[tuple[str, str, int]]] = {}
#: 渲染时真正用的内置表 = `_BUILTIN_BUTTONS_RAW`（套上「统一样式」策略后的结果）
_BUILTIN_BUTTONS: dict[str, list[tuple[str, str, int]]] = {}


def _builtin_buttons() -> dict[str, list[tuple[str, str, int]]]:
    """内置按钮表（解析 button_defs 的默认文本，只做一次；不含样式策略）。"""
    if not _BUILTIN_BUTTONS_RAW:
        parsed = CALC._parse_button_defs(DEFAULTS.get("button_defs") or "")
        _BUILTIN_BUTTONS_RAW.update(parsed)
    return {scene: list(items) for scene, items in _BUILTIN_BUTTONS_RAW.items()}


def _refresh_builtin_buttons() -> None:
    """把「统一样式」策略应用到内置表。

    ⚠️ v1.18.19 修的：以前只对**配置里的行**套策略，于是站长把
    ``button_style_mode`` 设成「统一」之后，凡是用出厂按钮的场景
    （没配过的场景、以及「同组兜底」拿到的那些）在 QQ 上仍然是原样式 ——
    表现就是「全局按钮效果没生效」。内置表也同样要过一遍策略。

    原地更新（clear + update）：兄弟模块引用的是同一个 dict。
    """
    styled = CALC._apply_button_style_policy(
        _builtin_buttons(), BUTTON_STYLE_MODE, BUTTON_DEFAULT_STYLE
    )
    _BUILTIN_BUTTONS.clear()
    _BUILTIN_BUTTONS.update(styled)


def _apply_extensions() -> None:
    """扫描 ``extensions/*.py``（v1.13.0 扩展点）。

    * 每次应用配置都重扫一遍：站长加/改扩展后保存一次配置就生效，不用重启
    * 坏扩展只告警 + 记进报告（编辑器页面能看到），插件照常跑
    * 加载完立刻把效果键同步给 ``_calc`` 的解析白名单，于是扩展声明的新效果
      可以马上写进 item_defs，编辑器页面的效果清单也会自动出现它
    """
    global EXT_REPORT_ROWS
    rows = EFFECT_REG.load_extensions(
        os.path.dirname(os.path.abspath(__file__)),
        warn=lambda msg: _tunable_warn("extensions", msg),
    )
    EFFECT_REG.sync_to_calc(CALC)
    summary = [f"{r['file']}:{r['ok']}:{r['error']}" for r in rows]
    if summary != EXT_REPORT_ROWS:
        EXT_REPORT_ROWS = summary
        for line in EFFECT_REG.report_lines():
            EFFECT_REG.log_info(f"[钓鱼] 扩展 {line}")


def _apply_button_defs(cfg: dict[str, Any]) -> None:
    """用配置接管按钮表；留空或写坏则回退内置；某场景被过滤光也回退该场景。

    v1.13.0 多了两个**全局**样式设置（编辑器「💬 回复 → 🔘 按钮总览 → 全局设置」）：

    * ``button_default_style``：按钮表里没写样式时用哪个（默认 ``default``）
    * ``button_style_mode``：写 ``统一`` 时所有按钮都用这个样式；默认
      ``按按钮表`` 即每行各写各的 —— 历史行为，升级后逐字不变
    """
    global BUTTON_STYLE_MODE, BUTTON_DEFAULT_STYLE
    BUTTON_STYLE_MODE = CALC._parse_button_style_mode(cfg.get("button_style_mode"))
    BUTTON_DEFAULT_STYLE = _cfg_str(cfg, "button_default_style").strip() or "default"
    # 「明确不要按钮」的场景：原地更新（重新赋值会让兄弟模块拿到旧对象）
    BUTTON_EMPTY_SCENES.clear()
    for _scene in _cfg_str(cfg, "button_empty_scenes").replace("，", ",").split(","):
        _scene = _scene.strip().lower()
        if _scene:
            BUTTON_EMPTY_SCENES.add(_scene)
    builtin = _builtin_buttons()
    rows: dict[str, list[tuple[str, str, int]]] = {}
    raw = _cfg_str(cfg, "button_defs").strip()
    if raw:
        rows = CALC._parse_button_defs(
            raw,
            warn=lambda msg: _tunable_warn("button_defs", msg),
            default_style=BUTTON_DEFAULT_STYLE,
        )
    if not rows:
        if raw:
            _tunable_warn("button_defs", "没有解析出有效按钮，已回退内置")
        rows = {scene: list(items) for scene, items in builtin.items()}
    # ⚠️ 这里**故意不做**「某个场景空了就回退内置」：站长要能把某个场景的按钮全删掉
    #（v1.18.0 起）。整表解析失败时上面已经整体回退，所以不会出现「配置写坏 -> 全没按钮」。
    # 想连「继承父场景」也断掉，就把场景名写进 button_empty_scenes。
    rows = CALC._apply_button_style_policy(rows, BUTTON_STYLE_MODE, BUTTON_DEFAULT_STYLE)
    BUTTONS.clear()
    BUTTONS.update(rows)
    # 内置表也要过一遍同一个策略（见 _refresh_builtin_buttons 的说明）
    _refresh_builtin_buttons()


def _apply_button_layout(cfg: dict[str, Any]) -> None:
    """用配置接管按钮排布（每行几个）；留空或写坏就用内置默认。

    dict 是**就地更新**的：拆分出去的 _views.py 引用的是同一个对象，
    所以改完立刻生效，不需要重新注入全局。
    """
    layout = dict(CALC.BUILTIN_BUTTONS_PER_ROW)
    raw = _cfg_str(cfg, "button_layout").strip()
    if raw:
        parsed = CALC._parse_button_layout(
            raw, warn=lambda msg: _tunable_warn("button_layout", msg)
        )
        for scene, number in parsed.items():
            layout[scene] = number
    BUTTONS_PER_ROW.clear()
    BUTTONS_PER_ROW.update(layout)


#: 生效的文案覆盖（场景 id -> 模板）；就地更新，_views.py 看到的是同一份
TEXT_OVERRIDES: dict[str, str] = {}


def _apply_text_overrides(cfg: dict[str, Any]) -> None:
    """用配置接管回复文案（text_overrides）：``场景|模板``。

    留空 = 全部用插件内置文案（所以默认行为与没这个功能时逐字一致）；
    未知场景 / 占位符写错的行由解析器跳过并告警，绝不会把 ``{占位符}`` 发给玩家。
    """
    TEXT_OVERRIDES.clear()
    raw = _cfg_str(cfg, "text_overrides").strip()
    if not raw or TEXT_LIB is None:
        if raw and TEXT_LIB is None:
            _tunable_warn("text_overrides", "文案模块 _texts.py 未加载，已全部用内置文案")
        return
    TEXT_OVERRIDES.update(
        TEXT_LIB.parse_overrides(raw, warn=lambda msg: _tunable_warn("text_overrides", msg))
    )


def _command_reserved_words(cfg: dict[str, Any]) -> set[str]:
    """会被「更靠前的分支」先接走的词（小写）：鱼饵的 id 与名字。

    ``/钓鱼 蚯蚓`` 会直接拿去下竿（见 ``_find_bait``），所以别名/自定义命令
    不能叫这个名字，否则玩家永远打不到它——配置阶段就跳过并告警。

    （``买``/``购买`` 的保护不在这里：它们是内置子命令，见 SUBCOMMAND_KEYWORDS
    的「买」那一行 —— 内置写法本来就不许被别名抢走。）
    """
    reserved: set[str] = set()
    try:
        for bait_id, bait in CALC._parse_bait_defs(_cfg_str(cfg, "bait_defs")).items():
            reserved.add(str(bait_id).strip().lower())
            name = str((bait or {}).get("name") or "").strip().lower()
            if name:
                reserved.add(name)
    except Exception as e:  # pragma: no cover - 解析器本身有兜底，这里只防意外
        logger.debug(f"读取鱼饵表用于命令名校验失败（已忽略）：{e}")
    return reserved


def _apply_command_config(cfg: dict[str, Any]) -> None:
    """用配置接管命令别名与自定义命令（两张表都只能「加」，不能改内置行为）。

    规则（详见 ``_calc._build_command_aliases`` / ``_calc._parse_custom_commands``）：

    * 别名：内置写法永远保留，配置只能追加新别名；冲突/写坏的行跳过并告警一次
    * 自定义命令：只在「内置子命令都不认识」时匹配；``执行:`` 只能指向内置子命令，
      因此自定义命令之间无法互相调用（防递归）
    * 留空 = 不生效（别名表为空、自定义命令为空），内置命令完全不受影响
    """
    reserved = _command_reserved_words(cfg)

    # ⚠️ 别名表的坏行是**逐行跳过**的（其余别名照常生效），所以不能用
    # `_tunable_warn` 那句「已回退默认值」——站长看到会以为整张表被扔了。
    _alias_warned = {"done": False}

    def _alias_warn(msg: str) -> None:
        if _alias_warned["done"]:
            return
        _alias_warned["done"] = True
        logger.warning(f"[配置] command_aliases {msg}（这一行跳过，其余别名照常生效）")

    aliases, alias_problems = CALC._build_command_aliases(
        _cfg_str(cfg, "command_aliases"),
        SUBCOMMAND_KEYWORDS,
        reserved=reserved,
        warn=_alias_warn,
    )
    COMMAND_ALIASES.clear()
    COMMAND_ALIASES.update(aliases)
    if alias_problems:
        logger.info(f"[配置] command_aliases 跳过了 {len(alias_problems)} 处：{alias_problems[:3]}")

    custom, custom_problems = CALC._parse_custom_commands(
        _cfg_str(cfg, "custom_commands"),
        SUBCOMMAND_KEYWORDS,
        reserved=reserved | set(aliases),
        warn=lambda msg: _tunable_warn("custom_commands", msg),
    )
    CUSTOM_COMMANDS.clear()
    CUSTOM_COMMANDS.update(custom)
    if custom_problems:
        logger.info(f"[配置] custom_commands 跳过了 {len(custom_problems)} 条：{custom_problems[:3]}")

    # 新别名也要享受「少打空格」容错：/钓鱼 仓库3 -> /钓鱼 仓库 3
    if not _BUILTIN_SUBCOMMAND_WORDS:
        _BUILTIN_SUBCOMMAND_WORDS.update(SUBCOMMAND_WORDS)
    SUBCOMMAND_WORDS.clear()
    SUBCOMMAND_WORDS.update(_BUILTIN_SUBCOMMAND_WORDS)
    SUBCOMMAND_WORDS.update(COMMAND_ALIASES)
    # 按钮也能指向站长的自定义命令 / 别名（否则那些行会被当「死按钮」丢掉）
    CALC.BUTTON_COMMAND_EXTRA.clear()
    CALC.BUTTON_COMMAND_EXTRA.update(COMMAND_ALIASES)
    CALC.BUTTON_COMMAND_EXTRA.update(CUSTOM_COMMANDS)
    if custom:
        logger.info(
            "[配置] 自定义命令 %d 条、新增别名 %d 个已生效", len(custom), len(aliases)
        )


#: 内置的「少打空格」写法快照（首次应用配置时拍下，避免把新别名误当内置）
_BUILTIN_SUBCOMMAND_WORDS: set[str] = set()


def _load_fish_defs_default() -> str:
    """读取 `_fish_data.py` 里的 `FISH_DEFS_DEFAULT`，作为 `fish_defs` 的配置默认值。

    放在独立数据文件里，是为了不让 232 行数据把 main.py 撑大（站长要求主文件瘦身）。
    """
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fish_data.py")
        spec = importlib.util.spec_from_file_location("astrbot_fishing_fish_defs", path)
        if spec is None or spec.loader is None:
            return ""
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return str(getattr(module, "FISH_DEFS_DEFAULT", "") or "")
    except Exception as e:  # pragma: no cover - 只在数据文件损坏时触发
        logger.warning(f"读取 fish_defs 默认值失败，改用内置鱼池：{e}")
        return ""


def _apply_fish_defs(cfg: dict[str, Any]) -> None:
    """用 `fish_defs` 接管鱼池：鱼种、基准价、各钓点权重都能在配置面板里改。

    优先级：`fish_value_overrides`（单条改价）> `fish_defs` 行内基准价 ×
    `fish_value_mult`（全局闸门）> 内置鱼池。
    行内权重是「基础权重」，运行时会再乘 `rarity_spawn_weights` 的稀有度倍数，
    所以站长既能逐条微调，也能整体调稀有度分布。留空则完全不介入（保持内置）。
    """
    global FISH_LOCATION_POOLS

    def _restore_builtin() -> None:
        """恢复内置鱼池：用模块加载时的快照（含追加鱼种与隐藏鱼的正确权重），
        再按当前稀有度权重缩放一遍。"""
        FISH_POOL[:] = [dict(_f) for _f in BUILTIN_FISH_POOL]
        FISH_BY_ID.clear()
        FISH_BY_ID.update({fish["id"]: fish for fish in FISH_POOL})
        FISH_LOCATION_POOLS = dict(BUILTIN_FISH_LOCATION_POOLS)
        # 权重表按模块加载时的同一套步骤重建，保证与「出厂内置」逐项一致：
        # ① 名单静态权重 × 稀有度倍数 ② 追加鱼种 ③ 隐藏生物保底
        LOCATION_WEIGHTS.clear()
        LOCATION_WEIGHTS.update(_rebuild_location_weights())
        for _fish in EXTRA_FISH:
            _pool = LOCATION_WEIGHTS.setdefault(_fish["home"], {})
            # ⚠️ 用 setdefault：追加鱼种与名单鱼 id 撞车时（4 条历史遗留）
            # 必须让名单鱼优先，否则「回退内置」与「配置接管」两条路径权重不一致。
            _pool.setdefault(
                _fish["id"], round(ROSTER_RARITY_WEIGHT[_fish["rarity"]] * 0.7, 3)
            )
        for _loc in list(LOCATION_WEIGHTS):
            for _fid in HIDDEN_EVERYWHERE:
                LOCATION_WEIGHTS[_loc].setdefault(_fid, HIDDEN_WEIGHT)

    raw = _cfg_str(cfg, "fish_defs").strip()
    if not raw:
        _restore_builtin()
        return
    rows = CALC._parse_fish_defs(
        raw,
        location_ids=LOCATION_TIER_ORDER,
        name_to_id=_LOCATION_NAME_TO_ID,
        rarity_order=tuple(RARITY_ORDER),
        warn=lambda msg: _tunable_warn("fish_defs", msg),
    )
    if not rows:
        _tunable_warn("fish_defs", "没有解析出有效鱼，已回退内置鱼池")
        _restore_builtin()
        return

    pool: list[dict[str, Any]] = []
    weights: dict[str, dict[str, float]] = {loc: {} for loc in LOCATION_TIER_ORDER}
    home: dict[str, list[str]] = {}
    for row in rows:
        fid = row["id"]
        rarity = row["rarity"]
        old = FISH_BY_ID.get(fid) or {}
        # 行内权重是「基础权重」（导出时已除掉稀有度权重），这里乘回**当前**的
        # 稀有度权重：默认配置下与内置权重逐项相同，站长调 rarity_spawn_weights
        # 时也照常生效。隐藏生物走独立保底权重，不参与稀有度缩放。
        if fid in HIDDEN_EVERYWHERE:
            rarity_weight = 1.0
        else:
            rarity_weight = float(ROSTER_RARITY_WEIGHT.get(rarity, 0.0) or 0.0)
        value = int(round(row["value"] * max(0.0, FISH_VALUE_MULT)))
        override = FISH_VALUE_OVERRIDES.get(fid)
        if override is None:
            override = FISH_VALUE_OVERRIDES.get(row["name"])
        if override:
            value = int(round(float(override)))
        pool.append(
            {
                "id": fid,
                "name": row["name"],
                "rarity": rarity,
                "value": max(1, value),
                "weight": round(max((w for _l, w in row["dist"]), default=1.0), 3),
                "diff": _safe_number(old.get("diff"), 0.0),
                "drift": _safe_number(old.get("drift"), 0.0),
                "flavor": row["flavor"] or str(old.get("flavor") or ""),
            }
        )
        for loc, weight in row["dist"]:
            scaled = (
                HIDDEN_WEIGHT
                if fid in HIDDEN_EVERYWHERE
                else weight * max(0.0, rarity_weight)
            )
            weights[loc][fid] = round(weights[loc].get(fid, 0.0) + scaled, 3)
            home.setdefault(fid, [])
            if loc not in home[fid]:
                home[fid].append(loc)

    # 全部原地替换：其它模块是通过命名空间注入引用这些对象的，重新赋值会失联
    FISH_POOL[:] = pool
    FISH_BY_ID.clear()
    FISH_BY_ID.update({fish["id"]: fish for fish in pool})
    FISH_LOCATION_POOLS = {fid: tuple(locs) for fid, locs in home.items()}
    LOCATION_WEIGHTS.clear()
    LOCATION_WEIGHTS.update({loc: w for loc, w in weights.items() if w})


def _build_roster() -> tuple[
    list[dict[str, Any]], dict[str, dict[str, float]], dict[str, str]
]:
    """展开名单：返回（新增鱼种列表, 各钓点权重表, 鱼种 -> 归属钓点）。"""
    rosters = _load_roster_data()
    known = {fish["name"]: fish for fish in FISH_POOL}
    new_fish: list[dict[str, Any]] = []
    weights: dict[str, dict[str, float]] = {}
    home_of: dict[str, str] = {}
    # 老图鉴里的鱼被名单复用时，按它**最早出现**的档位重算价值：
    # 同一种鱼在所有钓点价格一致，且不会出现「新鱼贵、老鱼烂」的断层。
    reused_tier: dict[str, tuple[int, str]] = {}
    for tier, loc_id in enumerate(LOCATION_TIER_ORDER):
        base = TIER_BASE_VALUE[min(tier, len(TIER_BASE_VALUE) - 1)]
        pool = weights.setdefault(loc_id, {})
        for entry in rosters.get(loc_id) or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            rarity = str(entry.get("rarity") or "常见")
            if not name or rarity not in ROSTER_RARITY_FACTOR:
                continue
            entry_id = str(entry.get("id") or name).strip()
            weight = ROSTER_RARITY_WEIGHT[rarity] * _stable_jitter(entry_id, 0.85, 1.15)
            existing = known.get(name)
            if existing is not None:
                # 老图鉴里已经有这条鱼：复用老 id，只把它加进这个钓点
                pool[existing["id"]] = pool.get(existing["id"], 0.0) + weight
                home_of.setdefault(existing["id"], loc_id)
                # 同一种鱼只保留一个品质：取它**最早出现**的那个档位的设定
                prev = reused_tier.get(existing["id"])
                if prev is None or tier < prev[0]:
                    reused_tier[existing["id"]] = (tier, rarity)
                continue
            if not entry_id or entry_id in known:
                continue
            diff = ROSTER_RARITY_DIFF[rarity]
            value = max(
                1,
                int(round(base * ROSTER_RARITY_FACTOR[rarity] * _stable_jitter(entry_id))),
            )
            fish = {
                "id": entry_id,
                "name": name,
                "rarity": rarity,
                "value": value,
                "weight": round(weight, 3),
                "diff": round(
                    diff + (_stable_jitter(entry_id, -0.05, 0.05) if diff else 0.0), 3
                ),
                "drift": round(
                    _stable_jitter(entry_id, -0.12, 0.12) if diff else 0.0, 3
                ),
                "flavor": str(entry.get("flavor") or "")[:24],
            }
            known[name] = fish
            home_of[entry_id] = loc_id
            new_fish.append(fish)
            pool[entry_id] = pool.get(entry_id, 0.0) + weight
    for fish_id, (tier, rarity) in reused_tier.items():
        fish = FISH_BY_ID_PENDING.get(fish_id)
        if fish is None:
            continue
        base = TIER_BASE_VALUE[min(tier, len(TIER_BASE_VALUE) - 1)]
        diff = ROSTER_RARITY_DIFF.get(rarity, 0.0)
        fish["rarity"] = rarity
        fish["value"] = max(
            1, int(round(base * ROSTER_RARITY_FACTOR[rarity] * _stable_jitter(fish_id)))
        )
        fish["diff"] = round(
            diff + (_stable_jitter(fish_id, -0.05, 0.05) if diff else 0.0), 3
        )
        fish["drift"] = round(_stable_jitter(fish_id, -0.12, 0.12) if diff else 0.0, 3)
    return new_fish, weights, home_of


#: 临时索引：_build_roster() 里要用它回写老鱼的价值（FISH_BY_ID 稍后才建）
FISH_BY_ID_PENDING: dict[str, dict[str, Any]] = {
    fish["id"]: fish for fish in FISH_POOL
}

ROSTER_FISH, ROSTER_WEIGHTS, ROSTER_HOME = _build_roster()
FISH_POOL.extend(ROSTER_FISH)


#: 追加鱼种：以普通/少见为主，让每个钓点更耐钓
EXTRA_FISH: list[dict[str, Any]] = [
    {"id": "redfin", "name": "赤眼鳟", "rarity": "常见",
     "flavor": "眼睛红红的，喜欢顶水游", "home": "novice"},
    {"id": "pond_snail", "name": "塘螺", "rarity": "常见",
     "flavor": "壳上裹着一层滑溜溜的苔", "home": "novice"},
    {"id": "stone_carp", "name": "石斑吻鰕", "rarity": "常见",
     "flavor": "贴在石头上啃青苔", "home": "bamboo"},
    {"id": "bamboo_shrimp2", "name": "青虾", "rarity": "少见",
     "flavor": "通体透明，夜里会发一点青", "home": "bamboo"},
    {"id": "canal_guppy", "name": "孔雀鱼", "rarity": "常见",
     "flavor": "尾鳍颜色比运河水还鲜艳", "home": "canal"},
    {"id": "canal_eel", "name": "运河黄鳝", "rarity": "少见",
     "flavor": "从水泥缝里钻出来的老住户", "home": "canal"},
    {"id": "silver_carp", "name": "鲢鱼", "rarity": "常见",
     "flavor": "成群游动，尾巴一摆一片白", "home": "lake"},
    {"id": "lake_perch", "name": "湖鲈", "rarity": "少见",
     "flavor": "追着小鱼跑，咬钩很凶", "home": "lake"},
    {"id": "reed_loach", "name": "芦苇鳅", "rarity": "常见",
     "flavor": "藏在苇根下，身上带花纹", "home": "reed"},
    {"id": "reed_frog", "name": "苇塘蛙", "rarity": "少见",
     "flavor": "蹲在叶子上装石头", "home": "reed"},
    {"id": "sea_sardine", "name": "沙丁鱼", "rarity": "常见",
     "flavor": "密密麻麻挤成一片银云", "home": "sea"},
    {"id": "sea_bream", "name": "海鲷", "rarity": "少见",
     "flavor": "牙口好，能把钩咬断", "home": "sea"},
    {"id": "dock_goby", "name": "虾虎鱼", "rarity": "常见",
     "flavor": "趴在旧船底一动不动", "home": "dock"},
    {"id": "dock_octopus", "name": "码头章鱼", "rarity": "稀有",
     "flavor": "钻进铁桶就不肯出来", "home": "dock"},
    {"id": "night_moth_fish", "name": "夜蛾鱼", "rarity": "常见",
     "flavor": "被灯光引来，围着堤坝转", "home": "night"},
    {"id": "night_ray", "name": "月鳐", "rarity": "传说",
     "flavor": "在月光下像一片会飞的黑影", "home": "night"},
    {"id": "mangrove_mudskipper", "name": "弹涂鱼", "rarity": "常见",
     "flavor": "在泥地上用胸鳍走路", "home": "mangrove"},
    {"id": "mangrove_crab", "name": "招潮蟹", "rarity": "少见",
     "flavor": "举着一只特别大的钳子", "home": "mangrove"},
    {"id": "swamp_leech", "name": "水蛭", "rarity": "常见",
     "flavor": "黏糊糊地挂在钩上", "home": "swamp"},
    {"id": "swamp_eel", "name": "沼泽电鳗", "rarity": "传说",
     "flavor": "摸一下能麻半天", "home": "swamp"},
    {"id": "cave_blind_shrimp", "name": "盲虾", "rarity": "常见",
     "flavor": "眼睛退化了，全靠触须", "home": "cave"},
    {"id": "cave_whitefish", "name": "白化鳅", "rarity": "少见",
     "flavor": "通体雪白，怕光", "home": "cave"},
    {"id": "ruins_porgy", "name": "遗迹鲷", "rarity": "常见",
     "flavor": "在锈铁皮之间穿来穿去", "home": "ruins"},
    {"id": "ruins_lobster", "name": "船蛆虾", "rarity": "少见",
     "flavor": "住在烂木头里，钳子很硬", "home": "ruins"},
    {"id": "abyss_snailfish", "name": "深渊狮子鱼", "rarity": "稀有",
     "flavor": "半透明的身体里有微光", "home": "abyss"},
    {"id": "abyss_vampire", "name": "吸血乌贼", "rarity": "传说",
     "flavor": "张开像一把黑伞", "home": "abyss"},
    {"id": "trench_amphipod", "name": "深海钩虾", "rarity": "常见",
     "flavor": "在万米水压里照样活蹦乱跳", "home": "trench"},
    {"id": "trench_grenadier", "name": "深渊鼠尾鳕", "rarity": "稀有",
     "flavor": "尾巴细得像鞭子", "home": "trench"},
    {"id": "glacier_icefish", "name": "冰鱼", "rarity": "常见",
     "flavor": "血是透明的，贴着冰面游", "home": "glacier"},
    {"id": "glacier_char", "name": "北极红点鲑", "rarity": "传说",
     "flavor": "身上洒满红色小点", "home": "glacier"},
    {"id": "aurora_crystal_fish", "name": "极光晶鱼", "rarity": "稀有",
     "flavor": "鳞片折出的光一直在变色", "home": "aurora"},
    {"id": "aurora_ghost_whale", "name": "极光幽灵鲸", "rarity": "神话",
     "flavor": "游过时整片冰面都亮了一下", "home": "aurora"},
]

#: 每个钓点都藏的隐藏生物：原型是现实中的 DeepSeek 模型
HIDDEN_FISH: list[dict[str, Any]] = [
    {
        "id": "big_fat_fish",
        "name": "大肥鱼",
        "rarity": "稀有",
        "value": 360,
        "weight": 0.35,
        "diff": 0.30,
        "drift": 0.0,
        "flavor": "原型据说是现实里的 DeepSeek 模型：问它什么都肯答，"
                  "答得又稳又长，就是偶尔会想很久",
    },
]

for _loc_index, _loc_id in enumerate(LOCATION_TIER_ORDER):
    _base = TIER_BASE_VALUE[min(_loc_index, len(TIER_BASE_VALUE) - 1)]
    for _entry in EXTRA_FISH:
        if _entry["home"] != _loc_id:
            continue
        _rarity = _entry["rarity"]
        _fish = {
            "id": _entry["id"],
            "name": _entry["name"],
            "rarity": _rarity,
            "value": max(
                1,
                int(round(_base * ROSTER_RARITY_FACTOR[_rarity]
                          * _stable_jitter(_entry["id"]))),
            ),
            "weight": round(ROSTER_RARITY_WEIGHT[_rarity] * 0.7, 3),
            "diff": ROSTER_RARITY_DIFF[_rarity],
            "drift": 0.0,
            "flavor": _entry["flavor"],
        }
        FISH_POOL.append(_fish)

FISH_POOL.extend(HIDDEN_FISH)

#: 这些鱼在每个钓点都会出现（出现率极低，属于「藏起来的小惊喜」）
HIDDEN_EVERYWHERE: tuple[str, ...] = tuple(f["id"] for f in HIDDEN_FISH)
#: 追加鱼种 id（它们的权重规则是「稀有度权重 × 0.7」，与模块加载时保持一致）
EXTRA_FISH_IDS: frozenset[str] = frozenset(f["id"] for f in EXTRA_FISH)

# 去重：同 id 只保留第一条（内容表/追加表可能重复写同一种鱼，重复会让图鉴永远集不齐）
_seen_fish_ids: set[str] = set()
FISH_POOL = [
    _f for _f in FISH_POOL
    if not (_f["id"] in _seen_fish_ids or _seen_fish_ids.add(_f["id"]))
]
del _seen_fish_ids

FISH_BY_ID: dict[str, dict[str, Any]] = {fish["id"]: fish for fish in FISH_POOL}

# =============================================================================
# 三·五、钓点（每个钓点的鱼种不同，且有等级 + 金币门槛）
# =============================================================================
#
# 解锁 = level_gate（等级）+ 上一个钓点图鉴 80% + gold_gate（一次性金币），买了永久解锁。
# 价值倍率让后期钓点单位时间收益更高，形成
# 「花金币解锁钓点 -> 收益更高 -> 攒钱解锁下一个」的正循环（也是主要金币回收口）。

LOCATIONS: list[dict[str, Any]] = [
    {"id": "novice", "name": "新手村", "emoji": "🏡", "level_gate": 1,
     "gold_gate": 0, "value_mult": 1.00,
     "desc": "村口小池塘，水浅鱼小，胜在稳定"},
    {"id": "bamboo", "name": "竹林溪流", "emoji": "🎋", "level_gate": 2,
     "gold_gate": 200, "value_mult": 1.03,
     "desc": "溪水从竹林间穿过，水浅但很活"},
    {"id": "canal", "name": "城中运河", "emoji": "🏙️", "level_gate": 3,
     "gold_gate": 500, "value_mult": 1.12,
     "desc": "两岸是水泥堤，水里什么都有"},
    {"id": "lake", "name": "山间湖泊", "emoji": "🏞️", "level_gate": 4,
     "gold_gate": 1000, "value_mult": 1.10,
     "desc": "水清鱼肥，鲶鱼黑鱼都在这儿"},
    {"id": "reed", "name": "芦苇荡", "emoji": "🌾", "level_gate": 6,
     "gold_gate": 1500, "value_mult": 1.13,
     "desc": "密不透风的芦苇，蛙声一片"},
    {"id": "sea", "name": "近海渔场", "emoji": "🌊", "level_gate": 8,
     "gold_gate": 3500, "value_mult": 1.17,
     "desc": "咸淡水交界，海货不少"},
    {"id": "dock", "name": "废弃码头", "emoji": "⚓", "level_gate": 10,
     "gold_gate": 5000, "value_mult": 1.21,
     "desc": "生锈的栈桥下藏着大鱼"},
    {"id": "night", "name": "月下堤坝", "emoji": "🌉", "level_gate": 13,
     "gold_gate": 8000, "value_mult": 1.25,
     "desc": "夜里灯火通明，夜行鱼会靠岸"},
    {"id": "mangrove", "name": "红树林", "emoji": "🌴", "level_gate": 16,
     "gold_gate": 14000, "value_mult": 1.29,
     "desc": "盘根错节的根系间全是小生命"},
    {"id": "swamp", "name": "迷雾沼泽", "emoji": "🌫️", "level_gate": 19,
     "gold_gate": 21000, "value_mult": 1.34,
     "desc": "阴森潮湿，藏着些怪东西"},
    {"id": "cave", "name": "地下暗河", "emoji": "🕳️", "level_gate": 23,
     "gold_gate": 33000, "value_mult": 1.40,
     "desc": "终年不见光，鱼都白了眼"},
    {"id": "ruins", "name": "沉船遗迹", "emoji": "🚢", "level_gate": 27,
     "gold_gate": 65000, "value_mult": 1.46,
     "desc": "锈迹斑斑的船舱成了鱼窝"},
    {"id": "abyss", "name": "深海海沟", "emoji": "🌑", "level_gate": 31,
     "gold_gate": 95000, "value_mult": 1.53,
     "desc": "深不见底，神话鱼的故乡"},
    {"id": "trench", "name": "无光深渊", "emoji": "🌊", "level_gate": 35,
     "gold_gate": 145000, "value_mult": 1.60,
     "desc": "压力大得能把人挤扁的地方"},
    {"id": "glacier", "name": "极地冰湖", "emoji": "🧊", "level_gate": 40,
     "gold_gate": 230000, "value_mult": 1.68,
     "desc": "冰层下别有洞天，只有最老的钓手敢来"},
    {"id": "aurora", "name": "极光冰渊", "emoji": "❄️", "level_gate": 45,
     "gold_gate": 450000, "value_mult": 1.78,
     "desc": "极光下的裂隙，据说通着别处"},
    # ---- v1.18.8 新增：给已经打到终局的钓手再往上留三级 ----
    {"id": "starfall", "name": "星陨湖", "emoji": "🌠", "level_gate": 50,
     "gold_gate": 700000, "value_mult": 1.86,
     "desc": "陨石砸出来的环形湖，夜里水面浮着星光"},
    {"id": "void_sea", "name": "万水归墟", "emoji": "🌀", "level_gate": 56,
     "gold_gate": 1100000, "value_mult": 1.95,
     "desc": "天下水流的尽头，连声音都被吞掉"},
    {"id": "dragon_palace", "name": "龙宫", "emoji": "🐉", "level_gate": 62,
     "gold_gate": 1800000, "value_mult": 2.05,
     "desc": "琉璃为瓦珊瑚为梁，龙王的水晶宫"},
]

#: 钓点定义字符串（配置项 `location_defs` 的默认值）由 LOCATIONS 生成，
#: 避免「代码里一套、配置里另一套」的漂移。格式：
#: ``id|名称|emoji|解锁等级|解锁金币|价值倍率|说明``
def _loc_def_lines() -> list[str]:
    return [
        f"{loc['id']}|{loc['name']}|{loc['emoji']}|{loc['level_gate']}|"
        f"{loc['gold_gate']}|{loc['value_mult']:.2f}|{loc['desc']}"
        for loc in LOCATIONS
    ]


DEFAULTS["location_defs"] = _loc_def_lines()

#: 等级上限：由最后一档钓点的门槛决定，不再是写死的 30 级
MAX_LEVEL: int = max(loc["level_gate"] for loc in LOCATIONS) + 5

DEFAULT_LOCATION = "novice"
LOCATION_BY_ID: dict[str, dict[str, Any]] = {loc["id"]: loc for loc in LOCATIONS}
#: 钓点 id -> 名称（写日志/提示用，即使该钓点没被配置进来也能显示中文名）
LOCATION_NAME_BY_ID: dict[str, str] = {loc["id"]: loc["name"] for loc in LOCATIONS}

#: 鱼种 -> 能钓到它的钓点（保证每种鱼都至少属于一个池子，图鉴可集齐）
FISH_LOCATION_POOLS: dict[str, tuple[str, ...]] = {
    "white_bait": ("novice",),
    "small_crucian": ("novice",),
    "mud_snail": ("novice",),
    "river_shrimp": ("novice",),
    "loach": ("novice",),
    "crucian": ("novice",),
    "carp": ("novice",),
    "topmouth_culter": ("novice",),
    "grass_carp": ("novice", "lake"),
    "catfish": ("novice", "lake"),
    "river_crab": ("novice", "lake"),
    "snakehead": ("lake",),
    "bighead_carp": ("lake",),
    "bass": ("lake", "sea"),
    "eel": ("lake", "sea", "swamp"),
    "softshell": ("lake", "sea", "swamp"),
    "mandarin_fish": ("lake", "sea", "swamp"),
    "koi": ("lake", "sea", "swamp", "abyss"),
    "seahorse": ("sea",),
    "cuttlefish": ("sea",),
    "pearl_shell": ("sea",),
    "sturgeon": ("sea",),
    "arowana": ("sea", "abyss"),
    "giant_salamander": ("swamp",),
    "golden_turtle": ("swamp", "abyss"),
    "moon_jellyfish": ("swamp", "abyss"),
    "discus": ("swamp", "abyss"),
    "dragon_koi": ("swamp", "abyss"),
    "deep_anglerfish": ("abyss",),
    "abyss_whale": ("abyss",),
    "kun": ("abyss",),
    # 彩蛋鱼
    "boot_carp": ("novice",),
    "bamboo_shrimp": ("bamboo",),
    "lantern_fish": ("night",),
    "ghost_jelly": ("swamp", "night"),
    "loach_king": ("novice", "bamboo"),
    "pearl_lume": ("sea", "night"),
    "golden_hook": ("glacier",),
}

#: 每个钓点内的鱼种权重（决定该钓点的收益水平）。
#: 新手村约与钓费持平，之后逐级放大：约 1x / 5x / 16x / 45x / 142x。
# 名单里的鱼补上「属于哪个钓点」（兜底用，正常走 LOCATION_WEIGHTS）
for _fish_id, _home in ROSTER_HOME.items():
    FISH_LOCATION_POOLS.setdefault(_fish_id, (_home,))
# 追加鱼种也要登记归属，否则会被当成「无家可归」塞进第一个钓点
for _entry in EXTRA_FISH:
    # setdefault：名单鱼已登记过归属时保持名单的（避免 id 冲突时归属跳变）
    FISH_LOCATION_POOLS.setdefault(_entry["id"], (_entry["home"],))

LOCATION_WEIGHTS: dict[str, dict[str, float]] = {
    "novice": {
        "white_bait": 12, "small_crucian": 12, "river_shrimp": 11, "loach": 11,
        "crucian": 12, "mud_snail": 7, "carp": 13, "topmouth_culter": 11,
        "grass_carp": 8, "catfish": 11, "river_crab": 2,
        # 彩蛋：靴子鲤（新手村专属，出现率很低）
        "boot_carp": 0.5,
    },
    "lake": {
        "catfish": 20, "river_crab": 18, "grass_carp": 16, "snakehead": 14,
        "bighead_carp": 12, "bass": 8, "mandarin_fish": 6, "eel": 4,
        "softshell": 1.5, "koi": 0.5,
    },
    "sea": {
        "bass": 20, "eel": 17, "mandarin_fish": 16, "seahorse": 14,
        "cuttlefish": 12, "pearl_shell": 10, "softshell": 8, "sturgeon": 2.5,
        "arowana": 1, "koi": 0.5,
    },
    "swamp": {
        "eel": 20, "softshell": 17, "giant_salamander": 15, "golden_turtle": 12,
        "moon_jellyfish": 10, "koi": 12, "discus": 7, "mandarin_fish": 4,
        "dragon_koi": 0.8,
    },
    "abyss": {
        "discus": 22, "koi": 17, "golden_turtle": 15, "moon_jellyfish": 14,
        "arowana": 12, "dragon_koi": 8, "deep_anglerfish": 6, "abyss_whale": 2,
        "kun": 0.6,
    },
    # 竹林溪流：低等级的第二站，草鱼鲤鱼 + 彩蛋竹节虾
    "bamboo": {
        "topmouth_culter": 20, "grass_carp": 20, "carp": 18, "crucian": 14,
        "river_shrimp": 12, "loach": 12, "catfish": 10, "river_crab": 8,
        "bass": 5, "bamboo_shrimp": 0.6, "loach_king": 0.15,
    },
    # 月下堤坝：夜行鱼 + 灯笼鱼 + 夜明珠贝
    "night": {
        "catfish": 18, "eel": 18, "snakehead": 16, "bass": 14, "softshell": 12,
        "mandarin_fish": 10, "cuttlefish": 8, "sturgeon": 4, "koi": 3,
        "moon_jellyfish": 2, "lantern_fish": 0.6, "ghost_jelly": 0.2,
        "pearl_lume": 0.15,
    },
    # 极地冰湖：终局钓点，冰属性 + 金钩鱼
    "glacier": {
        "sturgeon": 20, "arowana": 18, "discus": 16, "moon_jellyfish": 14,
        "golden_turtle": 12, "deep_anglerfish": 8, "dragon_koi": 6,
        "abyss_whale": 3, "kun": 1.5, "golden_hook": 0.3,
    },
}

# ---------------------------------------------------------------------------
# 主池重建：名单里的鱼构成每个钓点的主要鱼群，老图鉴里「名单外」的鱼
# （最初的隐藏鱼）按品质给一个小权重塞回原来的钓点，保证图鉴仍能集齐。
# 这样每个钓点的品质分布是统一算出来的，不会有的地方全是传说。
# ---------------------------------------------------------------------------
LEGACY_EXTRA_WEIGHT: dict[str, float] = {
    "常见": 3.0, "少见": 2.0, "稀有": 0.7, "传说": 0.10, "神话": 0.04,
}


# -----------------------------------------------------------------------------
# 内置默认基准（唯一真相 = DEFAULTS 里的字符串）
# -----------------------------------------------------------------------------
#
# 为什么需要它：配置写坏时要回退到「内置默认」而不是「上次生效的值」，
# 否则一次写错会永久污染后续解析。统一从 DEFAULTS 现算，避免两处硬编码。

_RARITY_TEMPLATE_ZERO = {name: 0.0 for name in RARITY_ORDER}
#: 钓点难度系数的模板：没配置的钓点一律按 1.0（= 必出鱼）处理
_LOCATION_TEMPLATE_ONE = {loc_id: 1.0 for loc_id in LOCATION_TIER_ORDER}
#: 中文钓点名 -> 钓点 id（解析前先把中文名换回 id，站长写「新手村:1.0」也认）
_LOCATION_NAME_TO_ID = {
    str(loc.get("name")): loc["id"] for loc in LOCATIONS if loc.get("name")
}
#: 按名字长度倒序，避免短名字先替换把长名字切坏
_LOCATION_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(_LOCATION_NAME_TO_ID.items(), key=lambda kv: -len(kv[0]))
)


def _normalize_location_keys(raw: str) -> str:
    """把配置串里的中文钓点名换成钓点 id（只做整词替换，认不出就原样保留）。"""
    text = raw or ""
    for name, loc_id in _LOCATION_ALIASES:
        if name and name in text:
            text = text.replace(name, loc_id)
    return text


def _builtin_defaults() -> dict[str, Any]:
    """从 DEFAULTS 解析出各项内置默认值（解析稳定，不受运行时改动影响）。"""
    return {
        "rarity_spawn_weights": _parse_named_floats(
            str(DEFAULTS.get("rarity_spawn_weights") or ""),
            _RARITY_TEMPLATE_ZERO,
            "内置默认",
        ),
        "rarity_value_factors": _parse_named_floats(
            str(DEFAULTS.get("rarity_value_factors") or ""),
            {name: 1.0 for name in RARITY_ORDER},
            "内置默认",
        ),
        "rarity_difficulty": _parse_named_floats(
            str(DEFAULTS.get("rarity_difficulty") or ""),
            _RARITY_TEMPLATE_ZERO,
            "内置默认",
        ),
        "rarity_attr_ranges": _parse_named_ranges(
            str(DEFAULTS.get("rarity_attr_ranges") or ""),
            {name: (0.0, 100.0) for name in RARITY_ORDER},
            "内置默认",
        ),
        "attr_weights": _parse_named_floats(
            str(DEFAULTS.get("attr_weights") or ""),
            {key: 0.0 for key in ("meat", "spirit", "sheen")},
            "内置默认",
        ),
        "attr_labels": _parse_named_texts(
            str(DEFAULTS.get("attr_labels") or ""),
            {key: key for key in ("meat", "spirit", "sheen")},
            "内置默认",
        ),
        "quality_tiers": _parse_quality_tiers(
            str(DEFAULTS.get("quality_tiers") or ""), [], "内置默认"
        ),
        "tier_base_values": _parse_number_list(
            str(DEFAULTS.get("tier_base_values") or ""),
            [0.0] * len(LOCATION_TIER_ORDER),
            "内置默认",
        ),
        "value_variance": _parse_number_range(
            str(DEFAULTS.get("value_variance") or ""), (0.92, 1.12), "内置默认"
        ),
        "hook_rate_fallback": _safe_number(
            DEFAULTS.get("hook_rate_fallback"), 0.30
        ),
        "default_escape_rate": _safe_number(
            DEFAULTS.get("default_escape_rate"), 0.25
        ),
        "location_hook_factors": _parse_named_floats(
            str(DEFAULTS.get("location_hook_factors") or ""),
            _LOCATION_TEMPLATE_ONE,
            "内置默认",
        ),
    }


DEFAULTS["fish_defs"] = _load_fish_defs_default() or DEFAULTS["fish_defs"]
for _ckey, _ctext in _load_content_defaults().items():
    DEFAULTS[_ckey] = _ctext or DEFAULTS.get(_ckey, "")
BUILTIN: dict[str, Any] = _builtin_defaults()


#: 隐藏生物在每个钓点的保底权重（出现率极低，且不随稀有度配置变化）
HIDDEN_WEIGHT = 0.15


def _rebuild_location_weights() -> dict[str, dict[str, float]]:
    """重建各钓点主池：名单静态权重 × 稀有度配置乘子，并回收名单外的老鱼。

    ⚠️ 这里必须乘上 `rarity_spawn_weights` 相对内置基准的倍数，否则站长调
    「稀有度权重」只会影响追加鱼种，主池（192 种）纹丝不动——那是个真实缺陷。
    默认配置下乘子恒为 1.0，因此权重与历史版本完全一致。
    """
    baseline = BUILTIN.get("rarity_spawn_weights") or {}
    current = ROSTER_RARITY_WEIGHT
    result: dict[str, dict[str, float]] = {}
    for loc, pool in ROSTER_WEIGHTS.items():
        scaled: dict[str, float] = {}
        for fid, weight in pool.items():
            fish = FISH_BY_ID.get(fid)
            if fish is None:
                continue
            rarity = fish["rarity"]
            base = float(baseline.get(rarity, 0.0) or 0.0)
            mult = (float(current.get(rarity, 0.0) or 0.0) / base) if base > 0 else 1.0
            scaled[fid] = round(float(weight) * max(0.0, mult), 3)
        result[loc] = scaled
    for fish in FISH_POOL:
        fid = fish["id"]
        if any(fid in pool for pool in result.values()):
            continue
        if fid in HIDDEN_EVERYWHERE:
            # 隐藏生物：设计上每个钓点保底出现一点，不参与稀有度缩放
            for loc in result:
                result[loc][fid] = HIDDEN_WEIGHT
            continue
        homes = FISH_LOCATION_POOLS.get(fid) or ()
        home = next((h for h in homes if h in result), LOCATION_TIER_ORDER[0])
        if fid in EXTRA_FISH_IDS:
            # 追加鱼种：与模块加载时同一条规则（稀有度权重 × 0.7），
            # 否则站长一调稀有度权重，这批鱼的权重就会跳变到旧保底值
            base_weight = ROSTER_RARITY_WEIGHT.get(fish["rarity"], 1.0) * 0.7
        else:
            base_weight = LEGACY_EXTRA_WEIGHT.get(fish["rarity"], 1.0)
        rarity = fish["rarity"]
        base = float(baseline.get(rarity, 0.0) or 0.0)
        mult = (float(current.get(rarity, 0.0) or 0.0) / base) if base > 0 else 1.0
        result.setdefault(home, {})[fid] = round(base_weight * max(0.0, mult), 3)
    return result


LOCATION_WEIGHTS = _rebuild_location_weights()

# 追加鱼种并入各自钓点
for _fish in EXTRA_FISH:
    _pool = LOCATION_WEIGHTS.setdefault(_fish["home"], {})
    # 与名单鱼 id 冲突的追加鱼种不覆盖（见 _restore_builtin 里的同一处理）
    _pool.setdefault(
        _fish["id"], round(ROSTER_RARITY_WEIGHT[_fish["rarity"]] * 0.7, 3)
    )

# 隐藏生物：每个钓点都塞一份（出现率极低）
for _loc_id in list(LOCATION_WEIGHTS):
    for _fid in HIDDEN_EVERYWHERE:
        LOCATION_WEIGHTS[_loc_id].setdefault(_fid, HIDDEN_WEIGHT)

#: 内置鱼池快照：`fish_defs` 为空或写坏时用它恢复，
#: 保证站长把配置改坏也绝不会出现「没有鱼可钓」。
BUILTIN_FISH_POOL: list[dict[str, Any]] = [dict(_f) for _f in FISH_POOL]
BUILTIN_FISH_LOCATION_POOLS: dict[str, tuple[str, ...]] = dict(FISH_LOCATION_POOLS)
BUILTIN_LOCATION_WEIGHTS: dict[str, dict[str, float]] = {
    _loc: dict(_pool) for _loc, _pool in LOCATION_WEIGHTS.items()
}


def _location_pool(location_id: str) -> list[tuple[dict[str, Any], float]]:
    """某个钓点的 (鱼种, 权重) 列表。"""
    weights = LOCATION_WEIGHTS.get(location_id)
    if weights:
        result = []
        for fish_id, weight in weights.items():
            fish = FISH_BY_ID.get(fish_id)
            if fish is not None and weight > 0:
                result.append((fish, float(weight)))
        if result:
            return result
    # 兜底：按映射关系生成等权池
    return [
        (fish, float(fish["weight"]))
        for fish in FISH_POOL
        if location_id in FISH_LOCATION_POOLS.get(fish["id"], ())
    ]


# 名单里的鱼并入各钓点权重（与老池子共存，老玩家的鱼照样能钓到）
for _loc_id, _pool in ROSTER_WEIGHTS.items():
    LOCATION_WEIGHTS.setdefault(_loc_id, {}).update(
        {fid: w for fid, w in _pool.items() if fid in FISH_BY_ID}
    )


# =============================================================================
# 三·六、鱼竿（越贵越强，主要提升价值与幸运）
# =============================================================================

#: ⚠️ 这是 `rod_defs` 为空/写坏时的**出厂兜底**，数值必须与
#: `DEFAULTS["rod_defs"]` 逐项一致 —— 以前两处各写一套（价格更便宜、手气更低），
#: 一旦站长清空 rod_defs，鱼竿数值就会悄悄换一套（`test_local.py` 的 [6n] 钉住了这条）。
RODS: list[dict[str, Any]] = [
    {"id": "bamboo", "name": "竹竿", "emoji": "🎋", "price": 0,
     "value_bonus": 0.00, "luck_bonus": 0.00, "unlock_level": 1,
     "window_bonus": 0.0, "escape_factor": 1.0,
     "desc": "村口杂货铺送的，能用"},
    {"id": "carbon", "name": "碳素竿", "emoji": "🎣", "price": 400,
     "value_bonus": 0.05, "luck_bonus": 0.03, "unlock_level": 4,
     "window_bonus": 0.0, "escape_factor": 1.0,
     "desc": "轻巧顺手，新手进阶首选"},
    {"id": "stream", "name": "溪流竿", "emoji": "🪝", "price": 1600,
     "value_bonus": 0.09, "luck_bonus": 0.05, "unlock_level": 9,
     "window_bonus": 0.0, "escape_factor": 1.0,
     "desc": "韧性好，适合溪流与湖泊"},
    {"id": "dragon", "name": "龙纹竿", "emoji": "🐉", "price": 5400,
     "value_bonus": 0.17, "luck_bonus": 0.11, "unlock_level": 16,
     "window_bonus": 0.0, "escape_factor": 1.0,
     "desc": "竿身刻龙，专治大鱼"},
    {"id": "starlight", "name": "星辉竿", "emoji": "✨", "price": 11000,
     "value_bonus": 0.23, "luck_bonus": 0.16, "unlock_level": 26,
     "window_bonus": 0.0, "escape_factor": 1.0,
     "desc": "夜里会泛微光，深海也用得上"},
    {"id": "mythic", "name": "神话竿", "emoji": "🌈", "price": 22000,
     "value_bonus": 0.30, "luck_bonus": 0.22, "unlock_level": 38,
     "window_bonus": 0.0, "escape_factor": 1.0,
     "desc": "传说钓具，据说能引来神话之鱼"},
    {"id": "koi_dragon", "name": "龙纹鲤竿", "emoji": "🐲", "price": 300000,
     "value_bonus": 0.33, "luck_bonus": 0.26, "unlock_level": 56,
     "window_bonus": 0.15, "escape_factor": 1.0,
     "desc": "竿身缠着一条活鲤纹，握上去就知道什么叫稳"},
    {"id": "void_rod", "name": "归墟竿", "emoji": "🕳️", "price": 1200000,
     "value_bonus": 0.36, "luck_bonus": 0.30, "unlock_level": 62,
     "window_bonus": 0.10, "escape_factor": 0.90,
     "desc": "竿梢细得几乎看不见，鱼线却再也挣不断"},
    # v1.18.63：大鱼乐的四根限定竿（`uses=1` = 限定竿：不进商店、不算「鱼竿买齐」）。
    # ⚠️ 数值必须与 DEFAULTS["rod_defs"] 逐项一致（[6n] 钉住了）；special 也要照抄。
    {"id": "tide_rod", "name": "潮汐竿", "emoji": "🌊", "price": 0,
     "value_bonus": 0.10, "luck_bonus": 0.08, "unlock_level": 1,
     "window_bonus": 0.10, "escape_factor": 0.95, "uses": 1,
     "special": {"variant_extra": 2.0},
     "desc": "异色猎手：异色判定多掷 2 次（0.5%→约 1.5%）"},
    {"id": "star_rod", "name": "星陨竿", "emoji": "☄️", "price": 0,
     "value_bonus": 0.12, "luck_bonus": 0.00, "unlock_level": 1,
     "window_bonus": 0.00, "escape_factor": 1.00, "uses": 1,
     "special": {"myth_every": 12.0},
     "desc": "星陨：每 12 竿有 1 竿的个体品质直接是神品"},
    {"id": "quick_rod", "name": "瞬手竿", "emoji": "🪶", "price": 0,
     "value_bonus": 0.08, "luck_bonus": 0.00, "unlock_level": 1,
     "window_bonus": 0.25, "escape_factor": 0.85, "uses": 1,
     "special": {"perfect": 1.0},
     "desc": "瞬手：拉线一律判「完美」，饵不会被咬掉、绝不脱钩"},
    {"id": "twin_rod", "name": "双尾竿", "emoji": "🎣", "price": 0,
     "value_bonus": 0.05, "luck_bonus": 0.06, "unlock_level": 1,
     "window_bonus": 0.00, "escape_factor": 1.08, "uses": 1,
     "special": {"double": 1.0},
     "desc": "双尾：一次成功上钩算两条"},
]
ROD_BY_ID: dict[str, dict[str, Any]] = {rod["id"]: rod for rod in RODS}
DEFAULT_ROD = "bamboo"

# =============================================================================
# 三·七、钓上来的杂物与漂流瓶
# =============================================================================
#
# 抛竿时有一定概率钓上杂物（不是鱼，不进背包，直接进道具栏，也算一种收集）。
# 漂流瓶里可能有一张随机纸条，也可能空空如也。

# =============================================================================
# 二·五、内容数据：从同目录 _game_data.py 读取（改内容不用碰逻辑）
# =============================================================================


def _load_backup_module() -> Any:
    """加载同目录的 _backup.py（存档仓库）；读不到就返回 None（数据功能降级）。"""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backup.py")
        spec = importlib.util.spec_from_file_location("astrbot_fishing_backup", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:  # pragma: no cover
        logger.warning(f"存档模块加载失败，数据管理功能不可用：{e}")
        return None


BACKUP_MODULE: Any = _load_backup_module()

#: 玩家存档信封版本（换格式时 +1，读档自动兼容老数据）
PLAYER_ENVELOPE_VERSION: int = (
    getattr(BACKUP_MODULE, "ENVELOPE_VERSION", 1) if BACKUP_MODULE else 1
)


def _load_data_module() -> dict[str, Any]:
    """读取内容数据模块；读不到就返回空表，由下面的兜底值接管。"""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_game_data.py")
        spec = importlib.util.spec_from_file_location("astrbot_fishing_content", path)
        if spec is None or spec.loader is None:
            return {}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {
            name: getattr(module, name)
            for name in _CONTENT_FALLBACK
            if getattr(module, name, None)
        }
    except Exception as e:  # pragma: no cover - 只在数据文件损坏时触发
        logger.warning(f"内容数据加载失败，使用内置兜底：{e}")
        return {}


#: 数据文件缺失时的兜底：保证插件在「只有一个 main.py」的情况下也能跑
_CONTENT_FALLBACK: dict[str, Any] = {
    "COLLECTIBLES": [],
    "BOTTLE_NOTES": [],
    "VARIANTS": [],
    "WEATHERS": [],
    "EASTER_EGGS": [],
    "RANDOM_EVENTS": [],
    "STORY_CHAINS": [],
    "MILESTONES": {},
    "ACHIEVEMENTS": {},
    # 官方改过的默认行（老配置迁移用）；⚠️ 必须列在这里，
    # 因为 _load_data_module() 只挑 _CONTENT_FALLBACK 里有的名字（漏了就等于没登记）
    "CONTENT_ROW_FIXES": [],
}

_CONTENT: dict[str, Any] = {**_CONTENT_FALLBACK, **_load_data_module()}

COLLECTIBLES: list[dict[str, Any]] = _CONTENT["COLLECTIBLES"]
BOTTLE_NOTES: list[str] = _CONTENT["BOTTLE_NOTES"]
VARIANTS: list[dict[str, Any]] = _CONTENT["VARIANTS"]
WEATHERS: list[dict[str, Any]] = _CONTENT["WEATHERS"]
EASTER_EGGS: list[dict[str, Any]] = _CONTENT["EASTER_EGGS"]
RANDOM_EVENTS: list[dict[str, Any]] = _CONTENT["RANDOM_EVENTS"]
STORY_CHAINS: list[dict[str, Any]] = _CONTENT["STORY_CHAINS"]
MILESTONES: dict[int, str] = _CONTENT["MILESTONES"]
ACHIEVEMENTS: dict[str, str] = _CONTENT["ACHIEVEMENTS"]

COLLECTIBLE_BY_ID: dict[str, dict[str, Any]] = {c["id"]: c for c in COLLECTIBLES}

#: 漂流瓶里的小纸条（有概率什么都没有）

# =============================================================================
# 三·八、每日订单（收益高于直接卖店，鼓励做任务）
# =============================================================================

#: 订单索要的鱼种品质随等级开放
ORDER_RARITY_BY_LEVEL: list[tuple[int, tuple[str, ...]]] = [
    (1, ("常见", "少见")),
    (6, ("常见", "少见", "稀有")),
    (12, ("少见", "稀有", "传说")),
    (18, ("稀有", "传说", "神话")),
]

# =============================================================================
# 三·九、等级（由累计钓获换算，用于解锁钓点 / 订单 / 交互品质门槛）
# =============================================================================

#: 等级曲线由 LEVEL_CURVE（base/ratio/growth）决定，见 _calc.py 的 _level_threshold：
#: 升到 L 级需要的累计钓获 = base × (ratio^(L-1) − 1) / (ratio − 1) + growth × (L-1)²
#: 等级上限


# =============================================================================
# 三·十、天气与时段
# =============================================================================
#
# 每天（按配置时区的自然日）随机一种天气，全天不变——每天上线都有新鲜感。
# rarity_mult 按鱼种品质给权重加倍；window_mult 影响拉线窗口长短；
# luck 是额外的品质幸运（好天气更容易出好个体）。

WEATHER_BY_ID: dict[str, dict[str, Any]] = {w["id"]: w for w in WEATHERS}
DEFAULT_WEATHER = "sunny"

# =============================================================================
# 三·十一、变异个体（异色）
# =============================================================================
#
# 钓上来的鱼有小概率是变异体：同种鱼、**独立图鉴条目**、价值翻数倍。
# 变异是纯运气，无法用金币获得——天然的群内炫耀素材。

VARIANT_BY_ID: dict[str, dict[str, Any]] = {v["id"]: v for v in VARIANTS}

#: 图鉴里变异体的独立条目后缀
VARIANT_CODEX_SUFFIX = "#"

# =============================================================================
# 三·十二、奇遇事件与里程碑
# =============================================================================
#
# 低概率触发的「小惊喜」：正常上鱼之后追加一段风味文案，并给一点小奖励。
# 奖励都很小，不影响平衡，目的是让重复抛竿不那么枯燥。
#   gold       立刻获得的额外金币
#   luck       累积到「品质幸运储备」（影响下一次抛竿的个体品质）
#   note       塞一张纸条进收集（复用漂流瓶的纸条池）
#   heal_bait  返还 1 个当前鱼饵

EASTER_EGG_BY_ID: dict[str, dict[str, Any]] = {e["id"]: e for e in EASTER_EGGS}

#: 钓鱼时的小插曲：给两个选择，结果由选择 + 一点随机决定，奖励都很小。
#: **不对外说明触发条件**，玩家只会偶尔撞见一次。
#:
#: v1.18.13 起还多了「连载」：见 _game_data.STORY_CHAINS。
#: 每一话都被登记成一条「事件」（id = ``<线名>#<第几话>``），
#: 所以提示、按钮、`/钓鱼 事件 N` 的解析全都走同一条路，不用另写一套。
#: 玩家身上的进度只有 ``story``（演到哪条线的第几话 + 攒了哪些旗标），
#: 下一话因此天然接得上上一话的结局。

def _chain_episode_id(chain_id: str, index: int) -> str:
    """连载第 ``index``（从 0 数）话的事件 id。"""
    return f"{chain_id}#{index + 1}"


CHAIN_BY_ID: dict[str, dict[str, Any]] = {
    str(c.get("id")): c for c in STORY_CHAINS if isinstance(c, dict) and c.get("id")
}

_EPISODE_EVENTS: dict[str, dict[str, Any]] = {}
for _chain_def in STORY_CHAINS:
    if not isinstance(_chain_def, dict):
        continue
    for _ep_index, _episode in enumerate(_chain_def.get("episodes") or []):
        if not isinstance(_episode, dict):
            continue
        _ep_id = _chain_episode_id(str(_chain_def["id"]), _ep_index)
        _episode.setdefault("id", _ep_id)
        _episode.setdefault("chain", str(_chain_def["id"]))
        _episode.setdefault("episode", _ep_index + 1)
        _episode.setdefault("chain_name", str(_chain_def.get("name") or _chain_def["id"]))
        _EPISODE_EVENTS[_ep_id] = _episode

EVENT_BY_ID: dict[str, dict[str, Any]] = {e["id"]: e for e in RANDOM_EVENTS}
EVENT_BY_ID.update(_EPISODE_EVENTS)


#: 累计钓获达到这些数量时给一句里程碑文案（只提示一次）







# =============================================================================
# 四、个体品质（6 档，可提升）
#    凡品 → 良品 → 精品 → 珍品 → 绝品 → **神品**
#    神话只能靠洗髓丹洗出来（自然上钩的权重是 0），概率见 quality_myth_chance
#
#    手气（鱼饵 + 鱼竿 + 天气 + 玉佩/插曲/供奉）不掷「点数」，而是**按档位放大权重**
#    （v1.18.22）：第 i 档权重 ×(1 + 手气 × luck_weight_step)^i。
#    所以手气越高，整条分布越往高档走，但**永远到不了权重 0 的档**，也不会像老算法那样
#    一旦手气超过 0.53 就把全部概率倒进最后一档（后期 93% 绝品就是这么来的）。
# =============================================================================
#
# 名称, 加成下限, 加成上限, emoji

QUALITY_TIERS: list[tuple[str, float, float, str]] = [
    ("凡品", 0.80, 1.00, "⚪"),
    ("良品", 1.00, 1.40, "🟢"),
    ("精品", 1.40, 2.00, "💎"),
    ("珍品", 2.00, 3.50, "🏆"),
    ("绝品", 3.50, 6.00, "👑"),
    # 神品：自然上钩永远不出（quality_weights 最后一位 0），只能靠洗髓丹洗出来
    ("神品", 6.00, 10.00, "🔱"),
]
QUALITY_ORDER = [name for name, _, _, _ in QUALITY_TIERS]
QUALITY_RANK = {name: idx for idx, name in enumerate(QUALITY_ORDER)}
QUALITY_EMOJI = {name: emoji for name, _, _, emoji in QUALITY_TIERS}

#: 三维属性在售价里的权重（肉质最重要）
ATTR_WEIGHTS = {"meat": 0.45, "spirit": 0.30, "sheen": 0.25}
#: 三维属性标签
ATTR_LABELS = {"meat": "肉质", "spirit": "灵性", "sheen": "光泽"}
#: 属性的「及格线」：加权平均等于该值时，价格系数正好是 1.0
ATTR_PAR = 60.0
#: 鱼的自然个体差异
VALUE_VARIANCE = (0.92, 1.12)

# =============================================================================
# 五、成就
# =============================================================================



# =============================================================================
# 六、通用小工具
# =============================================================================




















def _fish_emoji(fish: dict[str, Any]) -> str:
    return RARITY_EMOJI.get(fish.get("rarity", ""), "🐟")


def _fish_name(fish_id: str) -> str:
    fish = FISH_BY_ID.get(fish_id)
    return fish["name"] if fish else "未知"


def _fish_rarity(fish_id: str) -> str:
    fish = FISH_BY_ID.get(fish_id)
    return fish["rarity"] if fish else "常见"


def _bar(value: float, width: int = 10) -> str:
    """把 0~100 的属性画成小进度条，比纯数字直观。"""
    filled = int(_clamp(value, 0, 100) / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def _luck_stars(value: float, top: float = 0.8) -> str:
    """把「概率类」数值转成星级。

    设计上**不把概率写给玩家看**（几率的数字一露出来，玩家就会去算而不是去玩），
    所以鱼饵/鱼竿/天气的这些加成统一用星级表达。
    """
    ratio = _clamp(_safe_number(value, 0.0) / top, 0.0, 1.0)
    stars = int(round(ratio * 5))
    stars = max(1, min(5, stars)) if value > 0 else 0
    return "★" * stars + "☆" * (5 - stars)


def _weather_hint(weather: dict[str, Any]) -> str:
    """天气的定性说法（不暴露窗口/逃脱/幸运的具体数值）。"""
    window = _safe_number(weather.get("window_mult"), 1.0)
    escape = _safe_number(weather.get("escape_mult"), 1.0)
    luck = _safe_number(weather.get("luck"), 0.0)
    parts: list[str] = []
    if window >= 1.15:
        parts.append("鱼咬得久，好拉")
    elif window <= 0.9:
        parts.append("窗口很短，手要快")
    if escape >= 1.15:
        parts.append("容易脱钩")
    elif escape <= 0.9:
        parts.append("不太容易跑")
    if luck >= 0.05:
        parts.append("容易出好东西")
    return "　".join(parts) if parts else "平平无奇的一天"






def safe_handler(func):
    """给指令处理函数套异常边界，任何异常都记日志 + 友好回复，不让插件崩。"""

    @wraps(func)
    async def wrapper(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            async for result in func(self, event, *args, **kwargs):
                yield result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"钓鱼插件 {func.__name__} 异常: {e}", exc_info=True)
            try:
                async for _r in self._say_msg(event, "system.error", event.plain_result(
                        "😵 操作没有成功（已记录到日志）\n"
                        "　可以再试一次；如果一直失败，请把这条消息发给管理员"
                    )):
                    yield _r
            except Exception:
                logger.error("回复失败消息时再次异常，已忽略。", exc_info=True)

    return wrapper


# =============================================================================
# 七、配置解析
# =============================================================================
#
# 配置来自 WebUI 插件配置页（_conf_schema.json）。为了能在界面上编辑，
# 表格类的数据用「竖线分隔的字符串列表」表达，例如：
#   "worm|蚯蚓|🪱|15|5|0.10|1,1.2,1.6,1.8,2.0|万用饵"
# 这里负责把它们解析成结构化字典，并做健壮的兜底（任何一项写坏都不影响启动）。










HOOK_RATE_FALLBACK: float = 0.30

# =============================================================================
# 二·五、可调数值表（配置 → 模块级常量）
# =============================================================================
#
# 这些数值被几十处代码直接引用，逐个改成 self.cfg 既容易漏也容易写错，
# 所以统一在 _refresh_config() 里「配置 → 常量」写回一次：
#   * dict 就地 clear/update，list 用切片赋值 —— 引用它们的代码自动看到新值
#   * tuple / float 只能重新赋值，因此 refresh 之后要把全局重新注入各 mixin 模块
#     （见 _expose_globals_all()），否则拆出去的模块会拿到旧值

#: 逃脱率兜底（品质没写在 rarity_escape_chance 里时用它）
DEFAULT_ESCAPE_RATE: float = 0.25
#: 连钓「逐条拉线」时，每条窗口开头这段（秒）收到的「拉」一律丢弃（v1.18.28）。
#:
#: 为什么需要它：连钓是「这条判完立刻注册下一条」，所以玩家点第 N 条的那一下
#: （手快连点、或平台把消息重投一次）很容易撞在第 N+1 条的窗口刚开的时候 ——
#: 那条会以落点 ≈0 被判成「偏差」（中心在 0.5 的位置），逃脱率还要再乘
#: edge_escape_factor，而玩家根本还没看见提示。落在这段里的输入不可能是冲着
#: 这条鱼来的，丢掉、继续等剩下那点时间即可。
#: 真人来不及在提示刚出现的瞬间反应，所以不会误伤正常操作。
#: 单竿不设这道护栏（spec 里没有 min_reaction 就是 0）：它没有「上一条的余震」，
#: 一次过早的点击是玩家自己的选择，不该被吞掉。
MULTI_PULL_MIN_REACTION: float = 0.25
#: 「用 <道具> <栏位>×次数」一次最多重复几次（v1.18.65）——
#: 防手滑写个天文数字把 CPU 与道具一次抽干。
MAX_REPEAT: int = 999
#: 发「拉」落空后，多久之内还认为「这是刚才那个窗口晚了一步」
#: （连钓逐条弹提示，玩家点了上一条遗留的按钮很常见；超过这个时间就别再提它了）
PULL_MISS_WINDOW: float = 8.0
#: 鱼基准价的全局倍率与单条覆盖
FISH_VALUE_MULT: float = 1.0
FISH_VALUE_OVERRIDES: dict[str, float] = {}
#: 各钓点的上钩难度系数：>=1.0 = 必出鱼，<1.0 = 上钩率乘这个系数
LOCATION_HOOK_FACTORS: dict[str, float] = {}
#: 解析告警去重（同一配置项只提示一次，避免刷日志）
_TUNABLE_WARNED: set[str] = set()


def _tunable_warn(key: str, detail: str) -> None:
    """配置写坏时提示一次（不抛异常、不影响启动）。"""
    if key in _TUNABLE_WARNED:
        return
    _TUNABLE_WARNED.add(key)
    logger.warning(f"[配置] {key} {detail}——已回退默认值（本项只提示一次）")


#: 内容表（道具 / 鱼饵 / 鱼竿…）解析出坏行时，每张表只提示一次
_CONTENT_WARNED: set[str] = set()


def _content_warn_for(key: str):
    """给 `_calc` 的内容解析器用的 ``warn`` 回调：同一张表只提示一次。

    坏行被**不告警地**跳过，正是「加了新内容、游戏里却看不到」这类问题的根 ——
    这个回调把它变成一条日志（``[内容] item_defs：…``），一眼就能定位到坏在哪一行。
    """
    def _warn(message: str) -> None:
        if key in _CONTENT_WARNED:
            return
        _CONTENT_WARNED.add(key)
        logger.warning(f"[内容] {key}：{message}（本项只提示一次）")
    return _warn




























def _apply_tunable_config(cfg: dict[str, Any]) -> None:
    """把「可调数值表」配置写回模块级常量，并重建依赖它们的派生表。"""
    # 扩展点：先扫 extensions/（新效果键要赶在解析 item_defs 之前进白名单）
    _apply_extensions()
    global ATTR_PAR, VALUE_VARIANCE, TIER_BASE_VALUE, HOSTILE_KEYWORDS
    global HOOK_RATE_FALLBACK, DEFAULT_ESCAPE_RATE, FISH_VALUE_MULT
    global FISH_VALUE_OVERRIDES, LOCATION_WEIGHTS

    weights = _parse_named_floats(
        _cfg_str(cfg, "rarity_spawn_weights"), BUILTIN["rarity_spawn_weights"],
        "rarity_spawn_weights"
    )
    ROSTER_RARITY_WEIGHT.clear()
    ROSTER_RARITY_WEIGHT.update(weights)

    factors = _parse_named_floats(
        _cfg_str(cfg, "rarity_value_factors"), BUILTIN["rarity_value_factors"],
        "rarity_value_factors"
    )
    ROSTER_RARITY_FACTOR.clear()
    ROSTER_RARITY_FACTOR.update(factors)

    diffs = _parse_named_floats(
        _cfg_str(cfg, "rarity_difficulty"), BUILTIN["rarity_difficulty"],
        "rarity_difficulty"
    )
    ROSTER_RARITY_DIFF.clear()
    ROSTER_RARITY_DIFF.update(diffs)

    ranges = _parse_named_ranges(
        _cfg_str(cfg, "rarity_attr_ranges"), BUILTIN["rarity_attr_ranges"],
        "rarity_attr_ranges"
    )
    RARITY_ATTR_RANGE.clear()
    RARITY_ATTR_RANGE.update(ranges)

    attr_weights = _parse_named_floats(
        _cfg_str(cfg, "attr_weights"), BUILTIN["attr_weights"], "attr_weights"
    )
    ATTR_WEIGHTS.clear()
    ATTR_WEIGHTS.update(attr_weights)

    labels = _parse_named_texts(_cfg_str(cfg, "attr_labels"), BUILTIN["attr_labels"], "attr_labels")
    ATTR_LABELS.clear()
    ATTR_LABELS.update(labels)

    par = _try_float(cfg.get("attr_par"))
    if par is not None:
        ATTR_PAR = _clamp(par, 1.0, 100.0)

    tiers = _parse_quality_tiers(
        _cfg_str(cfg, "quality_tiers"), BUILTIN["quality_tiers"], "quality_tiers"
    )
    QUALITY_TIERS[:] = tiers
    QUALITY_ORDER[:] = [name for name, _, _, _ in tiers]
    QUALITY_RANK.clear()
    QUALITY_RANK.update({name: idx for idx, name in enumerate(QUALITY_ORDER)})
    QUALITY_EMOJI.clear()
    QUALITY_EMOJI.update({name: emoji for name, _, _, emoji in tiers})

    values = _parse_number_list(
        _cfg_str(cfg, "tier_base_values"), BUILTIN["tier_base_values"],
        "tier_base_values"
    )
    TIER_BASE_VALUE = tuple(values)

    VALUE_VARIANCE = _parse_number_range(
        _cfg_str(cfg, "value_variance"), BUILTIN["value_variance"], "value_variance"
    )

    fallback = _try_float(cfg.get("hook_rate_fallback"))
    if fallback is not None:
        HOOK_RATE_FALLBACK = _clamp(fallback, 0.0, 1.0)

    escape = _try_float(cfg.get("default_escape_rate"))
    if escape is not None:
        DEFAULT_ESCAPE_RATE = _clamp(escape, 0.0, 0.95)

    factors = _parse_named_floats(
        _normalize_location_keys(_cfg_str(cfg, "location_hook_factors")),
        BUILTIN["location_hook_factors"],
        "location_hook_factors",
    )
    LOCATION_HOOK_FACTORS.clear()
    LOCATION_HOOK_FACTORS.update(factors)

    mult = _try_float(cfg.get("fish_value_mult"))
    if mult is not None:
        FISH_VALUE_MULT = _clamp(mult, 0.0, 100.0)
    FISH_VALUE_OVERRIDES = _parse_name_values(
        _cfg_str(cfg, "fish_value_overrides"), "fish_value_overrides"
    )

    # 派生表：鱼池权重依赖稀有度权重/价值因子/属性范围，必须重建
    LOCATION_WEIGHTS = _rebuild_location_weights()

    # 站长自定义的鱼池优先级最高：整体接管鱼种 / 基准价 / 各钓点权重
    _apply_fish_defs(cfg)
    _apply_content_tables(cfg)
    # ⚠️ 别名/自定义命令必须在按钮表**之前**解析：按钮那一列要认得它们
    #（不然「/钓鱼 领奖」这种指向自定义命令的按钮会被当死按钮丢掉）
    _apply_command_config(cfg)
    _apply_button_defs(cfg)
    _apply_button_layout(cfg)
    _apply_text_overrides(cfg)











# =============================================================================
# 八、玩家数据模型
# =============================================================================

KV_KEY_PREFIX = "player_"
#: 数据版本：1/2 = 早期版本，3 = 鱼实例列表，4 = 三维属性 + 道具 + 水族馆扩建
DATA_VERSION = 4
#: 单次投喂上限（相对鱼种品质）


#: 升级曲线：base × (ratio^(L-1) − 1) / (ratio − 1) + growth × (L-1)²
#: 指数项负责"越往后越难"（卡住最高进度玩家），二次项默认 0，留着做微调
LEVEL_CURVE: dict[str, float] = {"base": 5.0, "ratio": 1.08, "growth": 0.0}












HOSTILE_KEYWORDS: tuple[str, ...] = (
    "鳄", "鲨", "蛇", "鳗", "鳝", "乌贼", "章鱼", "食人", "水虎", "龙鱼",
    "巨齿", "利维坦", "归墟", "古龙", "鲸", "鮟鱇", "电鳗", "鳄雀",
)










def _roll_quality_mult(
    weights: list[float],
    bait_luck: float = 0.0,
    extra_luck: float = 0.0,
    cfg: dict[str, Any] | None = None,
    floor: float = 0.0,
) -> float:
    """掷个体品质倍率。手气把**权重**往高档推（v1.18.22 重写）。

    ``weights`` 支持通过插件配置调整（``quality_weights``）。

    算法：第 i 档的权重 ×(1 + 手气 × ``luck_weight_step``)^i，再归一化掷点。

    ⚠️ **老算法（v1.18.21 及以前）是个坑**，站长报「不用玉佩，钓到的鱼也大部分是绝品」
    就是这么来的：手气直接加到累计点数上（``point = rand*total + 手气*total*0.9``），
    累计总量只有 100，所以手气一过 0.53，点数就整段越过前四档、全部质量倒进绝品：

    * 中期（虾饵+龙纹竿，手气 0.55）绝品已经 **52%**
    * 终局（龙涎+归墟竿，手气 1.14）绝品 **93%**，而且手气被硬钳在 1.0 ——
      玉佩（+0.20）、玉髓灯（+0.35）、香火供奉（+0.05）**一点效果都没有**

    新算法把每条来源都变得有意义：手气 0 时逐字等于 ``quality_weights``（历史行为不变），
    手气越高分布越往上走，但权重 0 的档（神品）乘出来永远是 0，
    「神品只能洗髓丹洗」这条规则照旧成立。

    ``luck_weight_step`` = 0 时手气完全不影响品质；``luck_cap`` 是手气合计的上限
    （默认 2.0，老版本硬编码 1.0 —— 后期鱼饵+鱼竿就顶满，付费道具全成废纸）。

    ``floor``（v1.18.23）= 品质保底：这一竿的品质倍率**不低于**它
    （``quality_floor`` 道具给的，例如 2.0 = 至少珍品）。
    它跟手气是两件事：手气是「更容易出好的」，保底是「不会出垃圾的」。
    """
    if not weights or sum(weights) <= 0:
        weights = list(DEFAULTS["quality_weights"])
    source = cfg if isinstance(cfg, dict) else {}
    step = max(
        0.0,
        _safe_number(
            source.get("luck_weight_step"), DEFAULTS["luck_weight_step"]
        ),
    )
    cap = max(
        0.0,
        _safe_number(source.get("luck_cap"), DEFAULTS["luck_cap"]),
    )
    luck = _clamp(float(bait_luck) + float(extra_luck), 0.0, cap)
    ratio = 1.0 + luck * step
    weighted: list[float] = []
    for i, weight in enumerate(weights):
        # 权重 0 的档（神品）乘出来还是 0 —— 自然上钩永远掷不到
        weighted.append(float(weight) * (ratio**i) if weight > 0 else 0.0)
    total = sum(weighted)
    if total <= 0:                      # 配置被写坏时退回原始权重
        weighted = [float(w) for w in weights]
        total = sum(weighted)
    point = random.random() * total
    # 默认落在「最后一个有权重的档」——不是最后一个档（否则会出现 0 权重档）
    idx = max(
        (i for i, weight in enumerate(weighted) if weight > 0),
        default=0,
    )
    cumulative = 0.0
    for i, weight in enumerate(weighted):
        if weight <= 0:
            continue                     # 0 权重 = 自然上钩永远不出这一档
        cumulative += weight
        if point < cumulative:
            idx = i
            break
    if idx >= len(QUALITY_TIERS):
        idx = len(QUALITY_TIERS) - 1
    _, low, high, _ = QUALITY_TIERS[idx]
    value = random.uniform(low, high)
    # 品质保底：抬到 floor（保底值就是某一档的下限，所以档位名也跟着对得上）。
    # 自然掷骰那条「权重 0 的档永远不出」的规则不受影响，这里只是抬一个下限 ——
    # 站长要是给某件道具写 quality_floor=6.0（= 神品），那也是他明确配的，照办。
    guard = _clamp(float(floor or 0.0), 0.0, _quality_ceil())
    if guard > value:
        value = guard
    return value










# =============================================================================
# 九、展示辅助
# =============================================================================


def _quality_tag(instance: dict[str, Any]) -> str:
    """个体品质标签，例如 ``🏆珍品``。"""
    quality = instance.get("quality", QUALITY_TIERS[0][0])
    return f"{QUALITY_EMOJI.get(quality, '⚪')}{quality}"


def _rarity_tag(fish_id: str) -> str:
    """鱼种品质标签，例如 ``✨传说``。"""
    rarity = _fish_rarity(fish_id)
    return f"{RARITY_EMOJI.get(rarity, '🐟')}{rarity}"


def _fish_line(fish_id: str) -> str:
    """一行鱼名 + 鱼种品质，例如 ``✨ 锦鲤（传说）``。"""
    fish = FISH_BY_ID.get(fish_id)
    if fish is None:
        return "❔ 未知"
    return f"{_fish_emoji(fish)} {fish['name']}（{fish['rarity']}）"






def _attrs_bar(instance: dict[str, Any]) -> str:
    """带进度条的三维展示，用于单条鱼详情（简短版）。"""
    attrs = instance.get("attrs") or {}
    return "　".join(
        f"{label}{_safe_int(attrs.get(key), 0, 0)} {_bar(_safe_int(attrs.get(key), 0, 0), 6)}"
        for key, label in ATTR_LABELS.items()
    )




# =============================================================================
# 十、插件主体
# =============================================================================

class FishingPlugin(
    Star,
    getattr(DATA_ADMIN, "DataAdminMixin", _MissingMixin),
    getattr(VIEWS, "ViewsMixin", _MissingMixin),
    getattr(COMMANDS, "CommandsMixin", _MissingMixin),
    getattr(INTERACTIONS, "InteractionsMixin", _MissingMixin),
    getattr(ENGINE, "EngineMixin", _MissingMixin),
    getattr(EDITOR_BRIDGE, "EditorBridgeMixin", _MissingMixin),
    getattr(LEGACY, "LegacyDataMixin", _MissingMixin),
):
    """QQ 群钓鱼小游戏插件。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        # 热路径用的配置缓存（每次 _refresh_config 刷新）
        self.cfg: dict[str, Any] = {}
        self.baits: dict[str, dict[str, Any]] = {}
        self.items: dict[str, dict[str, Any]] = {}
        self.aquarium_slots: list[dict[str, Any]] = []
        self.escape_map: dict[str, float] = {}
        #: 鱼饵 -> 咬钩率（_refresh_config 解析一次）
        self.bait_hook_map: dict[str, float] = {}
        #: 已经就「上钩率缺项」告警过的鱼饵，避免刷屏
        self._hook_warned: set[str] = set()
        self._hook_factor_warned: set[str] = set()
        self.interactive_rarities: set[str] = set()
        # 新增：鱼竿 / 钓点 / 背包扩容（可由配置覆盖）
        self.rods: list[dict[str, Any]] = []
        self.rod_by_id: dict[str, dict[str, Any]] = {}
        self.locations: list[dict[str, Any]] = []
        self.location_by_id: dict[str, dict[str, Any]] = {}
        self.backpack_upgrades: list[dict[str, Any]] = []
        self._refresh_config()

        # 每个玩家一把异步锁 & 互动等待表
        # ⚠️ _player_locks 用 **弱引用字典**（v1.18.37）：锁只在「有协程正拿着它」时
        #    存活，用完自动被回收 —— 否则长期运行的机器人会见一个 uid 建一把锁、
        #    永不释放，字典随「见过的用户数」单调增长。
        self._player_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._pending_pulls: dict[str, dict[str, Any]] = {}
        #: 玩家 -> 最近一次拉线窗口的收尾情况（时间/结局）：发「拉」落空时用来分辨
        #: 「这一下晚了」还是「附近真的没鱼」，别一律回「现在没有鱼咬钩」（v1.18.45）
        self._recent_pulls: dict[str, dict[str, Any]] = {}
        #: 保护 player_index 的读-改-写（两个玩家同时首次落盘会互相覆盖、丢一个 id）
        self._index_lock = asyncio.Lock()
        #: 记录最近见到的平台名（只在启动日志里提示按钮可用性）
        self._recent_platforms: dict[str, str] = {}
        #: 会话 -> (插曲归属玩家, 时间)：用来挡住「抢别人的插曲」
        self._recent_events: dict[str, tuple[str, float]] = {}
        #: 已经「称呼」过的消息（按 message_id）：每条指令的第一条回复只称呼一次
        self._mentioned_msgs: dict[str, float] = {}
        self._button_warned = False
        self._button_ok_logged = False
        #: 正在执行自定义命令的「执行:」：期间不再匹配自定义命令（防递归）
        self._custom_running = False
        self._tz = self._load_timezone()

    # -------------------------------------------------------------------------
    # 配置
    # -------------------------------------------------------------------------

    def _cfg_value(self, key: str) -> Any:
        """读配置项，取不到或为 None 时回退到内置默认值。"""
        try:
            value = self.config.get(key) if hasattr(self.config, "get") else None
        except Exception:
            value = None
        if value is None:
            return DEFAULTS.get(key)
        return value

    def _refresh_config(self) -> None:
        """把插件配置解析成热路径可直接使用的结构。

        任何一项配置写坏都不会导致加载失败——全部有兜底。
        """
        # ⚠️ 每次重新解析配置都**清空告警去重表**（v1.18.54）：站长改完配置保存时
        #    插件会走一遍 `_refresh_config`，如果去重表不清，他刚修好的问题、
        #    或者刚写坏的行，都不会再出现在日志里 —— 排查时只能看到「本项只提示一次」
        #    却不知道是哪一行。清空之后「保存 -> 看日志」是可靠的自查回路。
        _TUNABLE_WARNED.clear()
        _CONTENT_WARNED.clear()
        self._merge_content_defaults()   # 旧配置先补齐新版内容，再解析
        cfg: dict[str, Any] = {}
        for key, default in DEFAULTS.items():
            cfg[key] = self._cfg_value(key)

        # 可调数值表：把配置写回模块级常量（含派生表重建），既有使用点自动生效
        _apply_tunable_config(cfg)
        # 拆出去的 mixin 模块也要看到最新数值（tuple/float 是重新赋值）
        _expose_globals_all()

        # 数值型做范围收敛，避免玩家把游戏改崩
        cfg["initial_gold"] = max(0, _safe_int(cfg["initial_gold"], 100, 0))
        cfg["fish_cost"] = max(0, _safe_int(cfg["fish_cost"], 0, 0))
        cfg["stamina_max"] = int(
            _clamp(_safe_int(cfg.get("stamina_max"), 20, 0), 0, 999)
        )
        cfg["stamina_regen_seconds"] = int(
            _clamp(_safe_int(cfg.get("stamina_regen_seconds"), 45, 0), 0, 86400)
        )
        cfg["multi_cast_max"] = int(
            _clamp(_safe_int(cfg.get("multi_cast_max"), 20, 1), 1, 100)
        )
        # v1.18.28：连钓里要拉线的鱼是否逐条弹拉线互动（默认开）
        cfg["multi_pull_enabled"] = _cfg_bool(cfg, "multi_pull_enabled", True)

        # 防御性归一化：配置面板给的是真 bool，但手改配置文件可能写成字符串。
        # ⚠️ 不能只用 bool()：Python 里 bool("false") 是 True（非空字符串为真），
        # 所以字符串必须显式按字面解析。统一走 _cfg_bool。
        cfg["consume_bait_on_empty"] = _cfg_bool(cfg, "consume_bait_on_empty", False)
        cfg["sign_reward"] = max(0, _safe_int(cfg["sign_reward"], 20, 0))
        cfg["sell_discount"] = _clamp(_safe_number(cfg["sell_discount"], 1.0), 0.0, 5.0)
        cfg["enable_group_broadcast"] = bool(cfg["enable_group_broadcast"])
        cfg["aquarium_capacity"] = int(
            _clamp(_safe_int(cfg["aquarium_capacity"], 8, 1), 1, 64)
        )
        # 旧的「展出加成」配置（aquarium_bonus / aquarium_bonus_min_hours）已废弃：
        # 从配置字典里删掉，免得留着让人以为还有用
        cfg.pop("aquarium_bonus", None)
        cfg.pop("aquarium_bonus_min_hours", None)
        cfg["window_min"] = int(_clamp(_safe_int(cfg["window_min"], 4, 1), 1, 60))
        cfg["window_max"] = int(_clamp(_safe_int(cfg["window_max"], 8, 1), 1, 90))
        if cfg["window_max"] < cfg["window_min"]:
            cfg["window_max"] = cfg["window_min"]
        cfg["sweet_spot_width"] = _clamp(
            _safe_number(cfg["sweet_spot_width"], 0.34), 0.05, 0.95
        )
        cfg["perfect_bonus"] = _clamp(_safe_number(cfg["perfect_bonus"], 0.45), 0.0, 3.0)
        cfg["good_bonus"] = _clamp(_safe_number(cfg["good_bonus"], 0.15), 0.0, 3.0)
        cfg["perfect_escape_factor"] = _clamp(
            _safe_number(cfg["perfect_escape_factor"], 0.3), 0.0, 5.0
        )
        cfg["edge_escape_factor"] = _clamp(
            _safe_number(cfg["edge_escape_factor"], 1.6), 0.0, 5.0
        )
        cfg["feed_max_uses"] = int(
            _clamp(_safe_int(cfg["feed_max_uses"], 10, 0), 0, 999)
        )
        # 新增数值项
        cfg["backpack_base"] = int(
            _clamp(_safe_int(cfg["backpack_base"], 30, 1), 1, 500)
        )
        cfg["item_drop_chance"] = _clamp(
            _safe_number(cfg["item_drop_chance"], 0.14), 0.0, 1.0
        )
        cfg["bottle_note_chance"] = _clamp(
            _safe_number(cfg["bottle_note_chance"], 0.30), 0.0, 1.0
        )
        cfg["order_count"] = int(_clamp(_safe_int(cfg["order_count"], 3, 1), 1, 8))
        cfg["order_reward_mult"] = _clamp(
            _safe_number(cfg["order_reward_mult"], 1.6), 1.0, 10.0
        )
        # 订单的动态系数（v1.18.17）：等级成长 + 封顶
        cfg["order_level_growth"] = _clamp(
            _safe_number(cfg.get("order_level_growth"), 0.02), 0.0, 1.0
        )
        cfg["order_factor_max"] = _clamp(
            _safe_number(cfg.get("order_factor_max"), 6.0), 1.0, 100.0
        )
        # 自动补给开关 / 供奉数值（v1.18.17）
        cfg["auto_supply_bait"] = _cfg_bool(cfg, "auto_supply_bait", True)
        cfg["auto_equip_bait"] = _cfg_bool(cfg, "auto_equip_bait", True)
        cfg["auto_supply_buff"] = _cfg_bool(cfg, "auto_supply_buff", True)
        cfg["offering_price"] = int(
            _clamp(_safe_int(cfg.get("offering_price"), 200000, 0), 0, 1_000_000_000)
        )
        cfg["offering_hours"] = int(
            _clamp(_safe_int(cfg.get("offering_hours"), 24, 1), 1, 720)
        )
        cfg["offering_income_bonus"] = _clamp(
            _safe_number(cfg.get("offering_income_bonus"), 0.5), 0.0, 20.0
        )
        cfg["offering_luck_bonus"] = _clamp(
            _safe_number(cfg.get("offering_luck_bonus"), 0.05), 0.0, 5.0
        )
        # 每日额度（v1.18.18）：0 = 不限；上限给得比较宽，只是挡住「全天无上限」
        cfg["buff_daily_cast_limit"] = int(
            _clamp(_safe_int(cfg.get("buff_daily_cast_limit"), 120, 0), 0, 100000)
        )
        cfg["hot_soup_daily_limit"] = int(
            _clamp(_safe_int(cfg.get("hot_soup_daily_limit"), 5, 0), 0, 10000)
        )
        cfg["reroll_daily_total"] = int(
            _clamp(_safe_int(cfg.get("reroll_daily_total"), 0, 0), 0, 100000)
        )
        # v1.18.29：洗髓丹的**每条鱼**额度也走归一化（以前只在 _calc 读取时兜底，
        # 编辑器里填了脏值要等到用时才暴露）。0 = 不限，语义与 _calc._reroll_daily_cap 一致。
        cfg["reroll_daily_limit"] = int(
            _clamp(_safe_int(cfg.get("reroll_daily_limit"), 0, 0), 0, 100000)
        )
        # v1.18.62：育灵水/珍珠梳这类「加投喂上限」道具的两层限制
        #   · 同一条鱼每天最多用几次（0 = 不限，回到老行为）
        #   · 单条鱼的投喂上限加成上限（喂鱼上限 = feed_max_uses + 这个值）
        cfg["feed_bonus_daily_limit"] = int(
            _clamp(_safe_int(cfg.get("feed_bonus_daily_limit"), 2, 0), 0, 10000)
        )
        # v1.18.64：终身上限的**新键**（老键 feed_bonus_daily_limit 继续读，见 _calc）——
        # 没写过新键的人就跟着老键走，写过的人以新键为准。
        if "feed_bonus_lifetime_limit" in cfg:
            cfg["feed_bonus_lifetime_limit"] = int(
                _clamp(_safe_int(cfg.get("feed_bonus_lifetime_limit"), 2, 0), 0, 10000)
            )
        cfg["feed_bonus_cap"] = int(
            _clamp(_safe_int(cfg.get("feed_bonus_cap"), 20, 0), 0, 1000)
        )
        # v1.18.65：**只保留一个键**（feed_bonus_lifetime_limit）。
        # 老键 feed_bonus_daily_limit 是 v1.18.62 的「每天几次」，语义已经变成终身上限 ——
        # 站长明确不要「旧键 / 新键两个都摆着、还有一个只读」那种烂摊子。
        # 这里做一次性迁移：老键有值就搬到新键（新键为准），然后把老键**从配置里删掉**。
        _old_fb = cfg.get("feed_bonus_daily_limit")
        if _old_fb is not None:
            if cfg.get("feed_bonus_lifetime_limit") is None:
                cfg["feed_bonus_lifetime_limit"] = _old_fb
            cfg.pop("feed_bonus_daily_limit", None)
        cfg["feed_bonus_lifetime_limit"] = int(
            _clamp(_safe_int(cfg.get("feed_bonus_lifetime_limit"), 2, 0), 0, 10000)
        )
        cfg["feed_bonus_mode"] = (
            "best" if str(cfg.get("feed_bonus_mode") or "").strip().lower() in ("best", "max", "只取最好")
            else "add"
        )
        # 奖池里的鱼乘多少系数（v1.18.71）：0 = 不乘（回到老行为），1 = 和订单鱼同价
        cfg["lottery_fish_factor"] = _clamp(
            _safe_number(cfg.get("lottery_fish_factor"), 1.0), 0.0, 100.0
        )
        cfg["offering_daily_limit"] = int(
            _clamp(_safe_int(cfg.get("offering_daily_limit"), 1, 0), 0, 1000)
        )
        cfg["order_refresh_min_hours"] = int(
            _clamp(_safe_int(cfg.get("order_refresh_min_hours"), 3, 1), 1, 72)
        )
        cfg["order_refresh_max_hours"] = int(
            _clamp(
                _safe_int(cfg.get("order_refresh_max_hours"), 6, 1),
                cfg["order_refresh_min_hours"],
                72,
            )
        )
        cfg["order_unlock_level"] = int(
            _clamp(_safe_int(cfg["order_unlock_level"], 3, 1), 1, MAX_LEVEL)
        )
        # 订单跟随钓点（v1.14.0）：只点当前钓点钓得到的鱼 + 换钓点跟着换一批
        cfg["order_follow_location"] = _cfg_bool(cfg, "order_follow_location", True)
        cfg["order_include_hidden"] = _cfg_bool(cfg, "order_include_hidden", True)
        cfg["order_move_rerolls"] = int(
            _clamp(_safe_int(cfg.get("order_move_rerolls"), 1, 0), 0, 99)
        )
        # 天气 / 行情 / 变异 / 垃圾阈值 / 图鉴奖励 / 鱼塘
        cfg["enable_weather"] = bool(cfg["enable_weather"])
        cfg["enable_market"] = bool(cfg["enable_market"])
        cfg["market_boost_min"] = _clamp(
            _safe_number(cfg["market_boost_min"], 1.5), 1.0, 10.0
        )
        cfg["market_boost_max"] = _clamp(
            _safe_number(cfg["market_boost_max"], 2.2), 1.0, 20.0
        )
        if cfg["market_boost_max"] < cfg["market_boost_min"]:
            cfg["market_boost_max"] = cfg["market_boost_min"]
        cfg["variant_chance"] = _clamp(
            _safe_number(cfg["variant_chance"], 0.005), 0.0, 1.0
        )
        # ---- 数据管理 ----
        cfg["level_xp_base"] = _clamp(
            _safe_number(cfg.get("level_xp_base"), 5.0), 1.0, 100.0
        )
        # 等比底数：>1 才有指数增长（1.0 = 退化成线性，方便站长自己试手感）
        cfg["level_xp_ratio"] = _clamp(
            _safe_number(cfg.get("level_xp_ratio"), 1.08), 1.0, 2.0
        )
        cfg["level_xp_growth"] = _clamp(
            _safe_number(cfg.get("level_xp_growth"), 0.0), 0.0, 20.0
        )
        LEVEL_CURVE["base"] = cfg["level_xp_base"]
        LEVEL_CURVE["ratio"] = cfg["level_xp_ratio"]
        LEVEL_CURVE["growth"] = cfg["level_xp_growth"]
        cfg["content_auto_merge"] = bool(cfg.get("content_auto_merge", True))
        cfg["button_mode"] = str(cfg.get("button_mode") or "自动")
        cfg["data_status"] = str(cfg.get("data_status") or "")
        cfg["data_action"] = str(cfg.get("data_action") or "无") or "无"
        cfg["data_target"] = str(cfg.get("data_target") or "").strip()
        cfg["data_confirm"] = bool(cfg.get("data_confirm"))
        cfg["enable_auto_backup"] = bool(cfg.get("enable_auto_backup", True))
        cfg["backup_daily_hour"] = int(
            _clamp(_safe_int(cfg.get("backup_daily_hour"), 4, 0), 0, 23)
        )
        cfg["backup_interval_hours"] = int(
            _clamp(_safe_int(cfg.get("backup_interval_hours"), 6, 0), 0, 72)
        )
        cfg["backup_keep_daily"] = int(
            _clamp(_safe_int(cfg.get("backup_keep_daily"), 30, 0), 0, 3650)
        )
        cfg["backup_keep_interval"] = int(
            _clamp(_safe_int(cfg.get("backup_keep_interval"), 20, 0), 0, 1000)
        )
        cfg["backup_keep_manual"] = int(
            _clamp(_safe_int(cfg.get("backup_keep_manual"), 0, 0), 0, 10000)
        )
        cfg["backup_dir"] = str(cfg.get("backup_dir") or "").strip()
        self.backup_dir = (
            cfg["backup_dir"]
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
        )
        self.backup_store = (
            BACKUP_MODULE.BackupStore(self.backup_dir, DATA_VERSION)
            if BACKUP_MODULE
            else None
        )
        cfg["story_chance"] = _clamp(
            _safe_number(cfg.get("story_chance"), 0.06), 0.0, 1.0
        )
        # 连载相关的三个开关（v1.18.13）：概率、间隔竿数、选项是否打乱
        cfg["story_chain_chance"] = _clamp(
            _safe_number(cfg.get("story_chain_chance"), 0.5), 0.0, 1.0
        )
        cfg["story_chain_gap"] = int(
            _clamp(_safe_int(cfg.get("story_chain_gap"), 6, 0), 0, 999)
        )
        cfg["story_shuffle_choices"] = _cfg_bool(
            cfg, "story_shuffle_choices", True
        )
        cfg["easter_egg_chance"] = _clamp(
            _safe_number(cfg["easter_egg_chance"], 0.05), 0.0, 1.0
        )
        cfg["pond_income_per_hour"] = _clamp(
            _safe_number(cfg["pond_income_per_hour"], 0.02), 0.0, 1.0
        )
        cfg["pond_income_cap_hours"] = int(
            _clamp(_safe_int(cfg["pond_income_cap_hours"], 12, 1), 1, 168)
        )
        # 0 = 不封顶（站长想放开就填 0）
        cfg["pond_income_cap_coins"] = max(
            0, _safe_int(cfg["pond_income_cap_coins"], 5000, 0)
        )
        cfg["decoration_slots"] = int(
            _clamp(_safe_int(cfg.get("decoration_slots"), 3, 0), 0, 20)
        )
        cfg["decoration_hours"] = int(
            _clamp(_safe_int(cfg.get("decoration_hours"), 72, 1), 1, 8760)
        )
        cfg["buff_cast_count"] = int(
            _clamp(_safe_int(cfg.get("buff_cast_count"), 20, 1), 1, 999)
        )
        raw_bonus = cfg.get("codex_bonus_per_rarity")
        if isinstance(raw_bonus, list) and len(raw_bonus) == len(RARITY_ORDER):
            cfg["codex_bonus_per_rarity"] = [
                _clamp(_safe_number(v, 0.0), 0.0, 2.0) for v in raw_bonus
            ]
        else:
            cfg["codex_bonus_per_rarity"] = list(
                DEFAULTS["codex_bonus_per_rarity"]
            )

        # 展示名（5 档鱼种品质）
        names = cfg.get("rarity_display_names")
        if isinstance(names, list) and len(names) == len(RARITY_ORDER):
            cleaned = [str(n) for n in names if isinstance(n, (str, int, float))]
            if len(cleaned) == len(RARITY_ORDER):
                cfg["rarity_display_names"] = cleaned
            else:
                cfg["rarity_display_names"] = list(DEFAULTS["rarity_display_names"])
        else:
            cfg["rarity_display_names"] = list(DEFAULTS["rarity_display_names"])

        # 品质权重
        weights = cfg.get("quality_weights")
        if isinstance(weights, list) and len(weights) == len(QUALITY_TIERS):
            parsed = [_safe_number(w, 0) for w in weights]
            if sum(parsed) <= 0:
                parsed = list(DEFAULTS["quality_weights"])
            cfg["quality_weights"] = parsed
        else:
            cfg["quality_weights"] = list(DEFAULTS["quality_weights"])

        # 互动品质集合
        raw_interactive = cfg.get("interactive_rarities")
        if isinstance(raw_interactive, str):
            names_set = {
                token.strip() for token in raw_interactive.split(",") if token.strip()
            }
        elif isinstance(raw_interactive, list):
            names_set = {str(t).strip() for t in raw_interactive if str(t).strip()}
        else:
            names_set = set()
        cfg["interactive_rarities"] = names_set or {"传说", "神话"}

        self.cfg = cfg
        self.interactive_rarities = set(cfg["interactive_rarities"])
        self.baits = _parse_bait_defs(
            cfg.get("bait_defs"), warn=_content_warn_for("bait_defs")
        )
        self.items = _parse_item_defs(
            cfg.get("item_defs"), warn=_content_warn_for("item_defs")
        )
        self.aquarium_slots = _parse_aquarium_slots(cfg.get("aquarium_slots"))
        self.escape_map = _parse_escape_map(cfg.get("rarity_escape_chance"))
        # 上钩率：解析一次缓存成查表（容错见 _parse_hook_rates），不再每竿重算
        self.bait_hook_map, unknown_hook_keys = _parse_hook_rates(
            cfg.get("bait_hook_rates"), self.baits
        )
        if unknown_hook_keys:
            available = "/".join(b for b in self.baits)
            logger.warning(
                f"bait_hook_rates 里的键 {'、'.join(unknown_hook_keys)} 不认识，"
                f"已忽略；可用键：{available}（也可以直接写中文饵名）"
            )
        # 鱼竿 / 钓点 / 背包扩容
        self.rods = _parse_rod_defs(
            cfg.get("rod_defs"), warn=_content_warn_for("rod_defs")
        )
        self.rod_by_id = {r["id"]: r for r in self.rods}
        self.locations = _parse_location_defs(cfg.get("location_defs"))
        # 统一排序：所有界面/逻辑都用同一顺序（等级门槛 → 金币门槛）
        self.locations.sort(
            key=lambda l: (_safe_int(l.get("level_gate"), 1, 1),
                           _safe_int(l.get("gold_gate"), 0, 0))
        )
        self.location_by_id = {l["id"]: l for l in self.locations}
        self.backpack_upgrades = _parse_backpack_upgrades(cfg.get("backpack_upgrades"))
        # 让 _backpack_capacity 这种纯函数也能拿到扩容表
        cfg["_backpack_upgrades"] = self.backpack_upgrades
        # 称号表（v1.18.17）：后期金币回收口，纯炫耀；解析结果按价格升序
        self.titles = _parse_titles(cfg.get("title_defs"))
        self.title_by_id = {t["id"]: t for t in self.titles}

    def _item_list(self) -> list[str]:
        """**商店里**的道具 id 列表（按单价升序，界面上先看到便宜的）。

        ``uses > 0`` 的限用道具不进这张表 —— 它们不是买来的（见
        `_calc._shop_visible_items` 的说明）。用 `self.items` 仍能拿到全部道具。
        """
        return sorted(
            _shop_visible_items(self.items),
            key=lambda iid: (_safe_int(self.items[iid].get("price"), 0, 0), iid),
        )

    def _limited_use_text(self, player: dict[str, Any], item_id: str) -> str:
        """限用道具在背包里那行「还剩几次」的说明（v1.18.70）。"""
        spec = self.items.get(str(item_id)) or {}
        total = _safe_int(spec.get("uses"), 0, 0)
        left = self._limited_item_left(player, str(item_id))
        target = str(spec.get("rod") or spec.get("bait") or "").strip()
        thing = "鱼竿" if str(spec.get("rod") or "").strip() else ("鱼饵" if target else "道具")
        where = ""
        if str(spec.get("rod") or "").strip():
            rod = self.rod_by_id.get(target) or {}
            where = str(rod.get("name") or target)
        elif target:
            where = str((self.baits.get(target) or {}).get("name") or target)
        return (
            f"限用 {left}/{total} 次（每抛一竿 -1）"
            + (f"　它生效时用「{where}」{thing}的特权" if where else "")
        )

    def _limited_item_list(self) -> list[str]:
        """只能抽到、不能买的限用道具（大鱼乐奖池里那些「特殊道具」）。"""
        return sorted(
            iid for iid, spec in self.items.items()
            if _safe_int((spec or {}).get("uses"), 0, 0) > 0
        )

    def _bait_list(self) -> list[str]:
        """**鱼饵店里**能买到的饵 id（带特殊效果的限定饵不进这张表，v1.18.63）。

        用 `self.baits` 仍能拿到全部饵；这里只给「货架 / 可购买」用。
        """
        return _shop_visible_baits(self.baits)


    def _aquarium_capacity(self, player: dict[str, Any]) -> int:
        """水族馆总容量 = 基础容量 + 已解锁扩建栏位**各自加的位置数**。

        v1.18.13 起每一档可以一次加多个位（`名字|价格|加几个位`），
        所以这里按 `add` 求和，不再是一个档位一格。
        """
        unlocked = player.get("aquarium_slots") or []
        if not isinstance(unlocked, list):
            unlocked = []
        count = sum(
            max(1, _safe_int(slot.get("add"), 1, 1))
            for slot in self.aquarium_slots
            if slot["name"] in unlocked
        )
        return int(self.cfg["aquarium_capacity"]) + count

    # -------------------------------------------------------------------------
    # AstrBot 环境读取
    # -------------------------------------------------------------------------

    def _load_timezone(self):
        """读 AstrBot 时区，失败回退 UTC+8。"""
        fallback = timezone(timedelta(hours=8))
        try:
            get_config = getattr(self.context, "get_config", None)
            if not callable(get_config):
                return fallback
            cfg = get_config()
            if not isinstance(cfg, dict):
                return fallback
            tz_name = cfg.get("timezone")
            if not isinstance(tz_name, str) or not tz_name.strip():
                return fallback
            from zoneinfo import ZoneInfo

            return ZoneInfo(tz_name.strip())
        except Exception as e:
            logger.warning(f"读取时区失败，使用 UTC+8：{e}")
            return fallback

    def _today_text(self) -> str:
        try:
            return datetime.now(self._tz).strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    def _platform_name(self, event: AstrMessageEvent) -> str:
        """当前平台适配器名，例如 ``aiocqhttp`` / ``qq_official``。"""
        try:
            return str(event.get_platform_name() or "")
        except Exception:
            return ""
    def _kv_key(self, user_id: str) -> str:
        return f"{KV_KEY_PREFIX}{user_id}"

    def _lock_for(self, user_id: str) -> asyncio.Lock:
        lock = self._player_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._player_locks[user_id] = lock
            # 顺手清掉过期的「小插曲归属」记录（只在 600 秒内有用，见 _cmd_event）
            if len(self._recent_events) > 512:
                now = time.time()
                for key in [
                    k for k, (_owner, ts) in self._recent_events.items()
                    if now - ts >= 3600
                ]:
                    self._recent_events.pop(key, None)
            # _recent_platforms 只是启动日志用的诊断数据：超量就按插入顺序丢最早的
            if len(self._recent_platforms) > 2048:
                for key in list(self._recent_platforms)[:1024]:
                    self._recent_platforms.pop(key, None)
        return lock

    async def _load_player(
        self, user_id: str, *, strict: bool = False
    ) -> dict[str, Any]:
        """读取并修复玩家数据（含旧版本自动迁移、信封拆包）。

        Args:
            strict: **会写回的路径必须传 True**。KV 读取是可能瞬时失败的
                （数据库忙 / 磁盘故障），而失败时这个方法只能返回一个「新账号」。
                一旦调用方拿着这个空账号 `_save_player`，真存档就被静默清空了 ——
                所以 strict=True 时改为抛 ``PlayerLoadError``，让调用方中止这次操作
                （见 ``_do_cast`` 开头）。默认 False 保持老行为：
                「只是想看一眼」的调用点拿到空账号也无害。
        """
        try:
            raw = await self.get_kv_data(self._kv_key(user_id), None)
        except Exception as e:
            if strict:
                logger.error(
                    f"读取玩家 {user_id} 数据失败，已中止本次操作（不会覆盖存档）：{e}"
                )
                raise PlayerLoadError(str(e)) from e
            logger.error(f"读取玩家 {user_id} 数据失败，使用初始数据：{e}")
            raw = None

        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"玩家 {user_id} 的存储内容不是合法 JSON，已重置。")
                raw = None

        # 信封拆包：新格式带 __fishing_player__，老格式（裸 dict）照常读
        if BACKUP_MODULE and isinstance(raw, dict):
            raw, _enveloped = BACKUP_MODULE.unwrap_player(raw)

        player, migrated = _repair_player(raw, user_id)
        if not isinstance(raw, dict):
            # 全新玩家：初始金币走配置（默认 100）
            player["gold"] = _safe_int(self.cfg.get("initial_gold"), 100, 0)
        # 老存档：缸里已在养的鱼补上入缸时间（一次性），否则它们会因为「没有计时」
        # 永远不产出 —— 收益是严格按计时算的
        migrated = self._migrate_tank_clocks(player) or migrated
        if migrated:
            logger.info(f"玩家 {user_id} 数据已迁移到 v{DATA_VERSION}")
            await self._save_player(player)
        return player

    def _migrate_tank_clocks(self, player: dict[str, Any]) -> bool:
        """给「已经在缸里但没有入缸时间」的鱼补计时（老存档迁移，返回是否有改动）。

        补成 ``max(1, 上次结算时刻)``：等于承认「它上次结算时就在缸里」，
        升级不会把玩家攒着的那一段收益吃掉；之后新放进去的鱼一律走真实计时。
        """
        last = _safe_int(player.get("pond_last_ts"), 0, 0)
        changed = False
        for instance in player.get("aquarium") or []:
            if not isinstance(instance, dict):
                continue
            if _safe_int(instance.get("tank_since"), 0, 0) > 0:
                continue
            instance["tank_since"] = max(1, last)
            changed = True
        return changed

    async def _save_player(self, player: dict[str, Any]) -> bool:
        """写回玩家数据（包成信封，自带版本/时间/身份）。失败只记日志。"""
        user_id = str(player.get("user_id", "unknown"))
        try:
            payload: Any = player
            if BACKUP_MODULE:
                payload = BACKUP_MODULE.wrap_player(user_id, player, DATA_VERSION)
            await self.put_kv_data(self._kv_key(user_id), _json_dumps(payload))
            await self._remember_player(user_id)
            return True
        except Exception as e:
            logger.error(f"保存玩家 {user_id} 数据失败：{e}", exc_info=True)
            return False

    # -------------------------------------------------------------------------
    # 玩家索引（全量存档/导出要知道有哪些玩家）
    # -------------------------------------------------------------------------

    async def _remember_player(self, user_id: str) -> None:
        """把玩家 ID 记进索引（KV 存不了「列出全部 key」，所以自己记一份）。

        ⚠️ 读-改-写之间隔着 ``await``，两个玩家同时首次落盘会互相覆盖、丢掉一个 id
        （只影响备份/导出能否枚举到人，但既然别的写入都上了锁，这里也补一把）。
        """
        async with self._index_lock:
            try:
                index = await self.get_kv_data("player_index", None)
                if isinstance(index, str):
                    index = json.loads(index)
                ids = list(index) if isinstance(index, list) else []
                uid = str(user_id)
                if uid in ids:
                    return
                ids.append(uid)
                await self.put_kv_data("player_index", _json_dumps(ids[-5000:]))
            except Exception as e:
                logger.debug(f"更新玩家索引失败：{e}")

    async def _player_ids(self) -> list[str]:
        """已知的全部玩家 ID = KV 里的索引 ∪ 数据库里实际有的 ``player_*`` 行。

        ⚠️ **不能只认索引**（v1.18.43 修）：``player_index`` 是插件自己攒的，
        一旦它漏登记（改名换 scope、手工恢复存档、进程在写索引前被杀……），
        那些玩家就只剩库里那几行 —— 数据明明在，编辑器和「导出全部」却永远列不出来，
        站长看到的就是「我的人只剩两个了」。
        库这一侧只按**当前作用域**查，不会串到别的插件/旧作用域去。
        """
        ids: list[str] = []
        try:
            index = await self.get_kv_data("player_index", None)
            if isinstance(index, str):
                index = json.loads(index)
            if isinstance(index, list):
                ids = [str(x) for x in index if str(x).strip()]
        except Exception as e:
            logger.debug(f"读取玩家索引失败：{e}")
        try:
            found = await asyncio.to_thread(
                LEGACY.discover_player_ids,
                LEGACY.astrbot_db_path(),
                str(self.plugin_id or ""),
            )
        except Exception as e:  # 发现失败就只用索引，不影响正常功能
            logger.debug(f"按作用域发现玩家失败：{e}")
            found = []
        added = [uid for uid in found if uid not in ids]
        if added:
            ids.extend(added)
            logger.info(
                f"玩家索引缺 {len(added)} 名（库里查到的），已补进索引："
                f"{'、'.join(added[:6])}{'…' if len(added) > 6 else ''}"
            )
            try:
                await self.put_kv_data("player_index", _json_dumps(ids[-5000:]))
            except Exception as e:
                logger.debug(f"回写补全后的索引失败：{e}")
        return ids

    async def _dump_all_players(self) -> dict[str, Any]:
        """把索引里的玩家逐个读出来（信封原样带上）。"""
        players: dict[str, Any] = {}
        for uid in await self._player_ids():
            try:
                raw = await self.get_kv_data(self._kv_key(uid), None)
            except Exception:
                continue
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            if isinstance(raw, dict):
                players[uid] = raw
        return players

    # -------------------------------------------------------------------------
    # 成就
    # -------------------------------------------------------------------------

    def _check_achievements(
        self, player: dict[str, Any], catch: dict[str, Any] | None = None
    ) -> list[str]:
        """检查并解锁新成就，返回本次新解锁的成就文案。"""
        newly: list[str] = []
        owned: list[str] = player.setdefault("achievements", [])
        total_caught = _safe_int(player.get("total_caught"), 0, 0)
        gold = _safe_int(player.get("gold"), 0, 0)
        collection = player.get("collection") or {}
        collectibles = player.get("collectibles") or {}
        notes = player.get("bottle_notes") or []
        rod_ids = player.get("rods") or []
        loc_ids = player.get("locations") or []
        inventory = player.get("inventory") or []
        aquarium = player.get("aquarium") or []
        rarity = _fish_rarity(catch.get("fish_id", "")) if catch else ""
        quality = catch.get("quality", "") if catch else ""

        def unlock(key: str) -> None:
            if key in ACHIEVEMENTS and key not in owned:
                owned.append(key)
                newly.append(ACHIEVEMENTS[key])

        # ---- 渔获数量 ----
        for need, key in (
            (1, "first_fish"), (10, "catch_10"), (50, "catch_50"),
            (200, "catch_200"), (500, "catch_500"),
        ):
            if total_caught >= need:
                unlock(key)
        # ---- 稀有度 ----
        if rarity == "稀有":
            unlock("rare_hunter")
        if rarity == "传说":
            unlock("legend_hunter")
        if rarity == "神话":
            unlock("myth_hunter")
        # ---- 个体品质（凡品→良品→精品→珍品→绝品→神品；和鱼种稀有度是两套名字）----
        if quality == "珍品":
            unlock("perfect_one")
        if quality == "绝品":
            unlock("mythic_one")
        if quality == "神品":
            unlock("divine_one")
        top_quality = sum(
            1
            for x in inventory + aquarium
            if QUALITY_RANK.get(x.get("quality", ""), 0) >= QUALITY_RANK["珍品"]
        )
        if top_quality >= 3:
            unlock("perfect_three")
        # ---- 拉线技巧 ----
        if _safe_int(player.get("perfect_pulls"), 0, 0) >= 10:
            unlock("perfect_10")
        if _safe_int(player.get("clutch_wins"), 0, 0) >= 1:
            unlock("clutch_win")
        # ---- 图鉴与收集 ----
        kinds = sum(
            1
            for e in collection.values()
            if isinstance(e, dict) and _safe_int(e.get("count"), 0, 0) > 0
        )
        if kinds >= 10:
            unlock("collector_10")
        if kinds >= 20:
            unlock("collector_20")
        if kinds >= len(FISH_POOL):
            unlock("collector_all")
        # 只统计真正的杂物，彩蛋计数（egg_*）不算在内
        junk = sum(
            1
            for cid in COLLECTIBLE_BY_ID
            if _safe_int(collectibles.get(cid), 0, 0) > 0
        )
        if junk >= 5:
            unlock("junk_5")
        if junk >= len(COLLECTIBLES):
            unlock("junk_all")
        if len(notes) >= 5:
            unlock("note_5")
        if len(notes) >= len(BOTTLE_NOTES):
            unlock("note_all")
        # ---- 养成 ----
        if len(aquarium) >= self._aquarium_capacity(player):
            unlock("aquarist")
        total_fed = _safe_int(player.get("total_fed"), 0, 0)
        if total_fed >= 20:
            unlock("feeder")
        if total_fed >= 100:
            unlock("feeder_100")
        for inst in inventory + aquarium:
            attrs = inst.get("attrs") or {}
            if any(_safe_int(attrs.get(k), 0, 0) >= 100 for k in ATTR_WEIGHTS):
                unlock("attr_max")
                break
        # ---- 经营 ----
        if gold >= 1000:
            unlock("rich_1000")
        if gold >= 10000:
            unlock("rich_10000")
        if gold >= 100000:
            unlock("rich_100000")
        orders = _safe_int(player.get("total_orders"), 0, 0)
        if orders >= 1:
            unlock("first_order")
        if orders >= 30:
            unlock("order_30")
        # ---- 装备与地图 ----
        if len(rod_ids) >= 2:
            unlock("rod_2")
        # ⚠️ 「鱼竿买齐」只算**能买到的**竿（v1.18.63）：限定竿（潮汐竿/星陨竿）
        #    不卖、只能抽，算进去会让这个成就永远拿不到。
        if len(rod_ids) >= len(_shop_visible_rods(self.rods)):
            unlock("rod_all")
        if len(loc_ids) >= 2:
            unlock("loc_2")
        if len(loc_ids) >= len(self.locations):
            unlock("loc_all")
        # ---- 背包 ----
        if len(inventory) >= _backpack_capacity(player, self.cfg):
            unlock("full_bag")
        # ---- 变异 ----
        variants = player.get("variants") or {}
        variant_kinds = sum(
            1 for vid, cnt in variants.items() if _safe_int(cnt, 0, 0) > 0
        )
        if variant_kinds >= 1:
            unlock("first_variant")
        if variant_kinds >= 3:
            unlock("variant_3")
        if variant_kinds >= len(VARIANTS):
            unlock("variant_all")
        # ---- 天气 ----
        weather_id = player.get("weather")
        if weather_id == "wind" and rarity == "传说":
            unlock("bad_weather")
        if weather_id == "moon" and rarity == "神话":
            unlock("moon_hunter")
        # ---- 图鉴集齐奖励 ----
        done_rarities = self._codex_completed_rarities(player)
        if "常见" in done_rarities:
            unlock("codex_common")
        if "稀有" in done_rarities:
            unlock("codex_rare")
        if len(done_rarities) >= len(RARITY_ORDER):
            unlock("codex_all")
        # ---- 鱼塘 ----
        if _safe_int(player.get("pond_claimed_ts"), 0, 0) > 0:
            unlock("pond_first")
        if _safe_int(player.get("pond_best_income"), 0, 0) >= 1000:
            unlock("pond_big")
        # ---- 行情 ----
        if _safe_int(player.get("market_best_bonus"), 0, 0) >= 2000:
            unlock("market_master")
        # ---- 彩蛋 ----
        eggs = [
            k
            for k in (player.get("collectibles") or {})
            if isinstance(k, str) and k.startswith("egg_")
        ]
        if eggs:
            unlock("egg_first")
        if len(eggs) >= 5:
            unlock("egg_collector")
        # ---- 长期目标 ----
        records = player.get("best_records") or {}
        if isinstance(records, dict) and len(records) >= len(RARITY_ORDER):
            unlock("record_5")
        for rec in (records.values() if isinstance(records, dict) else []):
            if isinstance(rec, dict) and _safe_int(rec.get("value"), 0, 0) >= 10000:
                unlock("record_10k")
                break

        player["achievements"] = owned
        return newly

    # -------------------------------------------------------------------------
    # 钓鱼核心
    # -------------------------------------------------------------------------

    def _roll_species(
        self,
        bait_id: str,
        location_id: str,
        weather: dict[str, Any] | None = None,
        *,
        all_pool: bool = False,
        legend_only: bool = False,
    ) -> dict[str, Any]:
        """在指定钓点按权重抽鱼种。

        鱼饵改变各稀有度的命中权重；天气在此基础上再叠一层加成。

        ``all_pool`` / ``legend_only`` 是**限定饵**的特权（v1.18.63）：
        前者从全图鱼池抽（不受当前钓点限制），后者只抽「传说」档。
        """
        bait = self.baits.get(bait_id) or self.baits.get("none") or {}
        mults: dict[str, float] = bait.get("rarity_mult") or {}
        weather_mults: dict[str, float] = (weather or {}).get("rarity_mult") or {}

        if all_pool:
            source = [(fish, float(fish.get("weight") or 1.0)) for fish in FISH_POOL]
        else:
            source = list(_location_pool(location_id))
        if legend_only:
            only = [item for item in source if str(item[0].get("rarity")) == "传说"]
            if only:
                source = only
            else:
                # 这张图一条传说都没有：退回**全图**里的传说（不然贵客饵会白烧）
                source = [
                    (fish, float(fish.get("weight") or 1.0))
                    for fish in FISH_POOL
                    if str(fish.get("rarity")) == "传说"
                ] or source

        weighted: list[tuple[dict[str, Any], float]] = []
        for fish, weight in source:
            mult = _safe_number(mults.get(fish["rarity"]), 1.0)
            mult *= _safe_number(weather_mults.get(fish["rarity"]), 1.0)
            weighted.append((fish, float(weight) * max(0.0, mult)))

        total = sum(w for _, w in weighted)
        if total <= 0:
            pool = source or _location_pool(location_id)
            return pool[0][0] if pool else FISH_POOL[0]
        point = random.uniform(0, total)
        cumulative = 0.0
        for fish, weight in weighted:
            cumulative += weight
            if point < cumulative:
                return fish
        return weighted[-1][0]

    def _limited_item_left(self, player: dict[str, Any], item_id: str) -> int:
        """这件「限用道具」还剩几次（没这件 / 不是限用道具 -> 0）。"""
        pocket = player.get("items")
        if not isinstance(pocket, dict) or _safe_int(pocket.get(item_id), 0, 0) <= 0:
            return 0
        spec = self.items.get(str(item_id)) or {}
        total = _safe_int(spec.get("uses"), 0, 0)
        if total <= 0:
            return 0
        state = player.get("limited_uses")
        state = state if isinstance(state, dict) else {}
        left = state.get(str(item_id))
        return total if left is None else max(0, _safe_int(left, total, 0))

    def _limited_bait_left(self, player: dict[str, Any], bait_id: str) -> int:
        """这种**限定饵**现在还能用几次 = 对应凭证道具的剩余次数（v1.18.70）。

        限定饵在货架上买不到，`player["baits"]` 里那个库存永远是 0；
        真正管次数的是 ``items`` 里的凭证（``<bait>_pass``）+ ``limited_uses``。
        没有凭证就返回 0（玩家没用这张）。
        """
        want = str(bait_id or "").strip()
        if not want:
            return 0
        pocket = player.get("items")
        if not isinstance(pocket, dict):
            return 0
        total_left = 0
        for item_id in pocket:
            spec = self.items.get(str(item_id)) or {}
            if str(spec.get("bait") or "").strip() != want:
                continue
            total_left += self._limited_item_left(player, str(item_id))
        return total_left

    def _active_limited_bait(self, player: dict[str, Any]) -> str:
        """玩家现在**生效的限定饵** id（没有就返回 ""，v1.18.70）。

        和限定竿一个口径：抽到「深渊秘饵 / 贵客饵」的凭证后，只要还有次数，
        这一竿就用它那种特权饵 —— 抽到就能用，**不用先手动装备**。
        多张凭证都有次数时，取**剩余次数最多**的那张（先用完一张再换下一张）。
        """
        pocket = player.get("items")
        if not isinstance(pocket, dict) or not pocket:
            return ""
        best: tuple[int, str] | None = None
        for item_id in pocket:
            spec = self.items.get(str(item_id)) or {}
            bait_id = str(spec.get("bait") or "").strip()
            if not bait_id or bait_id not in self.baits:
                continue
            if not self._bait_is_limited(bait_id):
                continue
            left = self._limited_item_left(player, str(item_id))
            if left <= 0:
                continue
            if best is None or left > best[0]:
                best = (left, bait_id)
        return best[1] if best else ""

    def _bait_is_limited(self, bait_id: str) -> bool:
        """这种饵是不是「买不到、只能抽」的限定饵（``bait_defs`` 带特殊效果）。"""
        spec = self.baits.get(str(bait_id)) or {}
        return bool(spec.get("special"))

    def _purchasable_fallback_bait(self, prefer: str = "") -> str:
        """能给限定饵当替身的**可购买**鱼饵 id（v1.18.70）。

        挑法：先看玩家原来装/点名的那款（能买就用它），否则挑**店里手气最高**的
        那款。返回 "" 表示店里一款都没有（那就只能空钩）。
        """
        shop = [b for b in self._bait_list() if b != "none"]
        if not shop:
            return ""
        want = str(prefer or "").strip()
        if want and want in shop:
            return want
        shop.sort(key=lambda b: -_safe_number((self.baits[b] or {}).get("luck"), 0.0))
        return shop[0]

    def _active_limited_rod(self, player: dict[str, Any]) -> dict[str, Any] | None:
        """玩家背包里「还有剩余次数」的限定竿（v1.18.63），没有就返回 None。

        站长要的「金币买不到的特殊鱼竿（体验版/完整版、只能用几次）」：
        它们由大鱼乐抽到，在背包里、还有次数期间**顶替**当前装备的竿
        （所以抽到就能用，不用先装备）；次数用完就消失、自动换回原来的竿。
        同时只认**一根**：多根都有次数时按「价值加成高的优先」（体验版先消耗掉）。
        """
        pocket = player.get("items")
        if not isinstance(pocket, dict) or not pocket:
            return None
        state = player.get("limited_uses")
        state = state if isinstance(state, dict) else {}
        best: tuple[float, dict[str, Any]] | None = None
        for item_id, count in pocket.items():
            if _safe_int(count, 0, 0) <= 0:
                continue
            spec = self.items.get(str(item_id)) or {}
            rod_id = str(spec.get("rod") or "").strip()
            if not rod_id:
                continue
            total = _safe_int(spec.get("uses"), 0, 0)
            if total <= 0:
                continue
            left = state.get(str(item_id))
            left = total if left is None else _safe_int(left, total, 0)
            if left <= 0:
                continue
            rod = self.rod_by_id.get(rod_id)
            if rod is None:
                continue          # 竿被站长从表里删了：这件道具就当没有
            if best is None or _safe_number(rod.get("value_bonus"), 0.0) > best[0]:
                best = (_safe_number(rod.get("value_bonus"), 0.0), rod)
        return best[1] if best else None

    def _rod(self, player: dict[str, Any]) -> dict[str, Any]:
        """玩家当前**生效**的鱼竿配置（限定竿有剩余次数时优先）。"""
        limited = self._active_limited_rod(player)
        if limited is not None:
            return limited
        rod_id = player.get("equipped_rod")
        rod = self.rod_by_id.get(rod_id) if isinstance(rod_id, str) else None
        if rod is None:
            rod = self.rod_by_id.get(DEFAULT_ROD) or (
                self.rods[0] if self.rods else {"id": "bamboo", "name": "竹竿",
                                                "emoji": "🎋", "price": 0,
                                                "value_bonus": 0.0, "luck_bonus": 0.0,
                                                "desc": ""}
            )
        return rod


    def _location(self, player: dict[str, Any]) -> dict[str, Any]:
        """玩家当前所在钓点配置。"""
        loc_id = player.get("current_location")
        loc = self.location_by_id.get(loc_id) if isinstance(loc_id, str) else None
        return loc or self.location_by_id.get(DEFAULT_LOCATION) or {
            "id": DEFAULT_LOCATION, "name": "新手村", "emoji": "🏡",
            "level_gate": 1, "gold_gate": 0, "value_mult": 1.0, "desc": "",
        }


    def _gear_mult(self, player: dict[str, Any]) -> float:
        """鱼竿价值加成 x 钓点价值倍率。"""
        rod = self._rod(player)
        loc = self._location(player)
        return (1.0 + max(0.0, _safe_number(rod.get("value_bonus"), 0.0))) * max(
            0.1, _safe_number(loc.get("value_mult"), 1.0)
        )

    def _roll_collectible(self) -> dict[str, Any] | None:
        """按配置概率抽一个杂物；没抽中返回 None。"""
        if random.random() >= float(self.cfg["item_drop_chance"]):
            return None
        total = sum(c["weight"] for c in COLLECTIBLES)
        if total <= 0:
            return None
        point = random.uniform(0, total)
        cumulative = 0.0
        for item in COLLECTIBLES:
            cumulative += item["weight"]
            if point < cumulative:
                return item
        return COLLECTIBLES[-1]

    # -------------------------------------------------------------------------
    # 天气 / 鱼市行情（按自然日缓存，全天不变）
    # -------------------------------------------------------------------------

    def _ensure_weather(self, player: dict[str, Any]) -> bool:
        """确保今天的天气已确定。返回 True 表示数据有变化。"""
        if not self.cfg["enable_weather"]:
            return False
        today = self._today_text()
        if player.get("weather_date") == today and player.get("weather"):
            return False
        total = sum(w["weight"] for w in WEATHERS)
        point = random.uniform(0, total)
        cumulative = 0.0
        chosen = WEATHERS[0]
        for weather in WEATHERS:
            cumulative += weather["weight"]
            if point < cumulative:
                chosen = weather
                break
        player["weather_date"] = today
        player["weather"] = chosen["id"]
        return True

    def _weather(self, player: dict[str, Any]) -> dict[str, Any] | None:
        """当前天气配置（未启用 / 未确定时返回 None）。"""
        if not self.cfg["enable_weather"]:
            return None
        wid = player.get("weather")
        if not isinstance(wid, str):
            return None
        return WEATHER_BY_ID.get(wid)


    def _ensure_market(self, player: dict[str, Any]) -> bool:
        """确保今天的鱼市行情已生成。返回 True 表示数据有变化。"""
        if not self.cfg["enable_market"]:
            return False
        today = self._today_text()
        if player.get("market_date") == today and player.get("market"):
            return False
        candidates = [f for f in FISH_POOL if 3 <= f["value"] <= 200]
        if not candidates:
            candidates = list(FISH_POOL)
        count = 1 if len(candidates) < 3 else random.randint(1, 2)
        picked = random.sample(candidates, min(count, len(candidates)))
        lo = float(self.cfg["market_boost_min"])
        hi = float(self.cfg["market_boost_max"])
        player["market_date"] = today
        player["market"] = [
            {"fish_id": f["id"], "mult": round(random.uniform(lo, hi), 3)}
            for f in picked
        ]
        return True

    def _market_mult(self, player: dict[str, Any], fish_id: str) -> float:
        """某个鱼种今天的行情加成（1.0 表示无加成）。"""
        for entry in player.get("market") or []:
            if isinstance(entry, dict) and entry.get("fish_id") == fish_id:
                return max(1.0, _safe_number(entry.get("mult"), 1.0))
        return 1.0


    def _roll_variant(self) -> str | None:
        """掷变异；未命中返回 None。"""
        if random.random() >= float(self.cfg["variant_chance"]):
            return None
        total = sum(v["weight"] for v in VARIANTS)
        point = random.uniform(0, total)
        cumulative = 0.0
        for variant in VARIANTS:
            cumulative += variant["weight"]
            if point < cumulative:
                return variant["id"]
        return VARIANTS[-1]["id"]

    # -------------------------------------------------------------------------
    # 奇遇事件与里程碑
    # -------------------------------------------------------------------------

    def _roll_easter_egg(self) -> dict[str, Any] | None:
        """按配置概率抽一个奇遇事件；没抽中返回 None。"""
        chance = float(self.cfg.get("easter_egg_chance", 0.05))
        if chance <= 0 or random.random() >= chance:
            return None
        total = sum(e["weight"] for e in EASTER_EGGS)
        if total <= 0:
            return None
        point = random.uniform(0, total)
        cumulative = 0.0
        for egg in EASTER_EGGS:
            cumulative += egg["weight"]
            if point < cumulative:
                return egg
        return EASTER_EGGS[-1]

    def _apply_easter_egg(
        self, player: dict[str, Any], egg: dict[str, Any], bait_id: str
    ) -> str:
        """结算奇遇奖励，返回要追加展示的文案（失败返回空串）。"""
        try:
            lines = [egg["text"]]
            if egg.get("gold"):
                gold = int(egg["gold"])
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + gold
                lines.append(f"　💰 意外之财 +{_fmt_gold(gold)} 金币")
            if egg.get("luck"):
                gain = _safe_number(egg["luck"], 0.0)
                player["luck_charges"] = _clamp(
                    _safe_number(player.get("luck_charges"), 0.0) + gain, 0.0, 2.0
                )
                once = _safe_number(player["luck_charges"], 0.0)
                lines.append(f"　🔮 下一竿手气：{_luck_stars(gain, 0.3)}（累计 {once:.0%}）")
                # 玉佩在手时把「这一竿的合计」写出来：两者是不同来源，**叠加**
                if _safe_int(player.get("buff_casts_left"), 0, 0) > 0:
                    pendant = _safe_number(player.get("buff_quality"), 0.0)
                    if pendant > 0:
                        lines.append(
                            f"　（玉佩 +{pendant:.0%} 叠加，下一竿共 +{once + pendant:.0%}，"
                            f"之后回到 +{pendant:.0%}）"
                        )
            if egg.get("heal_bait") and bait_id != "none":
                baits = player.setdefault("baits", {})
                baits[bait_id] = _safe_int(baits.get(bait_id), 0, 0) + 1
                lines.append(f"　{self._bait_label(bait_id)} 返回了 1 个")
            if egg.get("note"):
                note = random.choice(BOTTLE_NOTES)
                notes = player.setdefault("bottle_notes", [])
                if note not in notes:
                    notes.append(note)
                    player["bottle_notes"] = notes[-30:]
                lines.append(f"　📜 上面写着：{note}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"结算奇遇事件失败：{e}")
            return ""
    async def _save_with_notices(
        self, player: dict[str, Any], lines: list[str] | None = None
    ) -> bool:
        """**存档事务的收尾**：查成就 → 存档 → 把结果写进 lines。"""
        new_ach = self._check_achievements(player)
        saved = await self._save_player(player)
        if lines is not None:
            if new_ach:
                lines.append("🎉 " + "；".join(new_ach))
            if not saved:
                lines.append("⚠️ 保存失败（这条记录可能不保留）")
        return saved
    def _codex_completed_rarities(self, player: dict[str, Any]) -> list[str]:
        """已经集齐全部鱼种的品质列表。"""
        collection = player.get("collection") or {}
        done: list[str] = []
        for rarity in RARITY_ORDER:
            group = [f for f in FISH_POOL if f["rarity"] == rarity]
            if not group:
                continue
            if all(
                isinstance(collection.get(f["id"]), dict)
                and _safe_int(collection[f["id"]].get("count"), 0, 0) > 0
                for f in group
            ):
                done.append(rarity)
        return done

    def _codex_mult(self, player: dict[str, Any]) -> float:
        """图鉴集齐带来的永久价值加成（1.0 表示无）。"""
        bonuses = self.cfg.get("codex_bonus_per_rarity") or []
        total = 0.0
        for rarity in self._codex_completed_rarities(player):
            idx = RARITY_RANK.get(rarity, 0)
            if 0 <= idx < len(bonuses):
                total += _safe_number(bonuses[idx], 0.0)
        return 1.0 + total

    # ---------------------------------------------------------------------
    # 群内排行榜（存一份轻量索引，避免遍历所有玩家）
    # ---------------------------------------------------------------------

    @staticmethod
    def _leaderboard_key() -> str:
        return "leaderboard"

    async def _touch_leaderboard(self, player: dict[str, Any]) -> None:
        """把玩家的关键指标写入排行榜索引。失败不影响主流程。"""
        try:
            user_id = str(player.get("user_id", ""))
            if not user_id:
                return
            index = await self.get_kv_data(self._leaderboard_key(), {})
            if not isinstance(index, dict):
                index = {}
            holdings = list(player.get("inventory") or []) + list(
                player.get("aquarium") or []
            )
            kinds = sum(
                1
                for e in (player.get("collection") or {}).values()
                if isinstance(e, dict) and _safe_int(e.get("count"), 0, 0) > 0
            )
            best = 0
            best_fish = ""
            for inst in holdings:
                value = _instance_value(inst)
                if value > best:
                    best = value
                    best_fish = inst.get("fish_id", "")
            entry = index.get(user_id)
            if not isinstance(entry, dict):
                entry = {}
            entry["gold"] = _safe_int(player.get("gold"), 0, 0)
            entry["caught"] = _safe_int(player.get("total_caught"), 0, 0)
            entry["kinds"] = max(_safe_int(entry.get("kinds"), 0, 0), kinds)
            entry["best"] = max(_safe_int(entry.get("best"), 0, 0), best)
            if best and best_fish:
                entry["best_fish"] = best_fish
            name = player.get("last_name")
            if isinstance(name, str) and name:
                entry["name"] = name
            index[user_id] = entry
            if len(index) > 300:  # 防止无限增长
                index = dict(
                    sorted(
                        index.items(),
                        key=lambda kv: -_safe_int((kv[1] or {}).get("caught"), 0, 0),
                    )[:300]
                )
            await self.put_kv_data(self._leaderboard_key(), index)
        except Exception as e:
            logger.debug(f"更新排行榜索引失败：{e}")

    def _location_hook_factor(self, loc_id: str | None) -> float | None:
        """钓点难度系数。

        - ``>= 1.0`` → 这个钓点**必出鱼**（前几张图，新手期绝不空竿）
        - ``< 1.0``  → 实际上钩率 = 鱼饵上钩率 × 系数（越深越容易空竿）
        - 配置里没写的钓点 → 回退 1.0（当必出处理，避免漏配误伤）

        ``loc_id=None`` 表示「不套用钓点系数」（只给单测/内部按裸概率采样用），
        返回 None 让调用方跳过这一步。
        """
        if not loc_id:
            return None
        table = LOCATION_HOOK_FACTORS or {}
        if loc_id in table:
            return _clamp(_safe_number(table.get(loc_id), 1.0), 0.0, 5.0)
        if loc_id not in self._hook_factor_warned:
            self._hook_factor_warned.add(loc_id)
            logger.warning(
                f"location_hook_factors 里没有钓点 {loc_id} 的难度系数，"
                f"按 1.0（必出鱼）处理；想让它变难就加一项，例如 {loc_id}:0.7"
            )
        return 1.0

    def _roll_cast_outcome(
        self, bait_id: str, can_loot: bool, loc_id: str | None = None
    ) -> tuple[str, dict[str, Any] | None]:
        """这一竿的结果：``("fish", None)`` / ``("item", 杂物)`` / ``("nothing", None)``。

        判定顺序就是玩家直觉里的顺序（用户明确要求）：
        1. 先看有没有中鱼 —— 中了就是鱼，**不会被杂物抢走**，
           所以「上鱼率 == bait_hook_rates 里设置的值 × 钓点系数」；
        2. 没中鱼才看钩子上有没有带物件（``item_drop_chance``）；
        3. 都没有就是空手而归。

        钓点系数（``location_hook_factors``）在这里生效：``>= 1.0`` 的钓点
        （默认前 3 张图）直接必出鱼，``< 1.0`` 的按比例压低上钩率 ——
        越往后的地图越容易空竿。

        ``can_loot=False``（完全免费的空钩）时不出物件，避免零成本白刷杂物。
        抽成独立方法是为了让三段概率可被单测稳定采样。
        """
        hook = self._hook_rate(bait_id)
        factor = self._location_hook_factor(loc_id)
        if factor is not None:
            if factor >= 1.0:
                return "fish", None          # 该钓点必出鱼
            hook = hook * factor
        if hook >= 1.0 or random.random() < hook:
            return "fish", None
        if can_loot:
            drop = self._roll_collectible()
            if drop is not None:
                return "item", drop
        return "nothing", None

    def _hook_rate(self, bait_id: str) -> float:
        """这一竿的咬钩率（查缓存的表，空钩很低，带饵才高）。

        兜底是 :data:`HOOK_RATE_FALLBACK`（0.30），**不会**回退成 1.0 ——
        以前配置里漏写某个鱼饵会让它变成 100% 上钩，是这个 bug 的根源。
        """
        table = getattr(self, "bait_hook_map", None) or {}
        if bait_id in table:
            return table[bait_id]
        if bait_id not in self._hook_warned:
            self._hook_warned.add(bait_id)
            logger.warning(
                f"bait_hook_rates 里没有鱼饵 {bait_id} 的上钩率，"
                f"暂时按 {HOOK_RATE_FALLBACK:.0%} 处理；"
                f"想改就在配置里加一项，例如 {bait_id}:0.60"
            )
        return HOOK_RATE_FALLBACK

    def _bait_cost(self, bait_id: str) -> int:
        """单个鱼饵的售价（只在购买时收钱；下竿消耗库存，不再重复收费）。"""
        bait = self.baits.get(bait_id)
        return int(bait.get("price", 0)) if bait else 0

    # -------------------------------------------------------------------------
    # 参数解析工具（统一做空格容错 + 专有名词保留）
    # -------------------------------------------------------------------------

    @staticmethod
    def _tokens(*parts: Any) -> list[str]:
        """把形参拼成 token 列表。

        这样 `/钓鱼 卖 1 2 3`、`/钓鱼 卖  1   2`、`/钓鱼 卖 1 2 3 4 5`
        都能得到干净的 token，多余/缺少空格都不会出错。
        """
        out: list[str] = []
        for part in parts:
            if part is None:
                continue
            for token in str(part).replace(",", " ").replace("，", " ").split():
                if token:
                    out.append(token)
        return out

    @staticmethod
    def _is_index_token(token: str) -> bool:
        """是否为「序号」token（纯数字）。"""
        return token.isdigit()

    def _split_name_count(self, token: str) -> tuple[str, str] | None:
        """把「蚯蚓2」「鲤鱼3」拆成 ``(名字, 数量)``；没有数字后缀返回 ``None``。

        对应「少打一个空格」的写法：``/钓鱼 商店 买 蚯蚓2``、``/钓鱼 卖 鲤鱼3``。
        """
        token = (token or "").strip()
        if len(token) < 2:
            return None
        head = token.rstrip("0123456789")
        if not head or head == token:
            return None
        return head, token[len(head) :]

    def _peel_subcommand(self, text: str) -> tuple[str, str] | None:
        """拆「少打空格」的子命令写法：``卖1`` -> ``("卖", "1")``。

        为避免把 ``帮助x`` 这种打错的输入悄悄变成「帮助」，
        残留部分必须是「像参数的东西」：以数字/区间符号开头，
        或者该子命令本身就是接名字的（商店、用、钓点……）。
        """
        peeled = self._peel_action(text, SUBCOMMAND_WORDS)
        if not peeled:
            return None
        word, remain = peeled
        if remain[:1].isdigit() or remain[:1] in ("-", "~", "－", "全", "所", "空"):
            return word, remain
        if word in NAME_ARG_WORDS:
            return word, remain
        return None

    def _peel_action(
        self, text: str, words: tuple[str, ...] | list[str] | set[str]
    ) -> tuple[str, str] | None:
        """把「少打一个空格」的写法拆开，例如 ``放1`` -> ``("放", "1")``。

        只在 ``text`` 以某个动作词开头、且后面还有内容时才拆；
        拆不开返回 ``None``（调用方保持原样）。长词优先，
        这样 ``水族馆 扩建2`` 不会被 ``扩`` 抢先匹配。
        """
        text = (text or "").strip()
        if not text:
            return None
        for word in sorted(words, key=len, reverse=True):
            if word and text != word and text.startswith(word):
                return word, text[len(word) :]
        return None

    @staticmethod
    def _command_args(event: AstrMessageEvent) -> list[str]:
        """从原始消息里取「指令名之后的全部参数」——**不限个数**（v1.18.17）。

        AstrBot 的 ``CommandFilter`` 是「一个形参吃一个 token」的：handler 声明到
        ``a6``，那么第 7 个及以后的 token 会被**直接丢掉**。于是
        ``/钓鱼 水族馆 放 1 2 3 4 5`` 只能放进 4 条鱼（站长报的那个「一次最多四条」），
        ``/钓鱼 卖 1 2 3 4 5 6 7`` 也卖不全。

        这里绕开形参，直接从 ``event.get_message_str()`` 里切：唤醒前缀（``/``）
        在 waking 阶段已经被 AstrBot 去掉了，所以消息形如 ``钓鱼 水族馆 放 1 2 3``。
        第一个 token 含「钓鱼」就丢掉它，剩下的就是子命令 + 参数。

        拿不到消息文本时返回空列表，调用方回退到形参（单测直接调 handler 的情况）。
        """
        try:
            text = str(event.get_message_str() or "")
        except Exception:  # pragma: no cover - 极少数事件没有消息文本
            return []
        text = " ".join(text.split())
        if not text:
            return []
        tokens = [t for t in text.split(" ") if t]
        if tokens and "钓鱼" in tokens[0]:
            tokens = tokens[1:]
        return tokens

    def _parse_indices(self, spec: str, pool: list[Any]) -> list[int]:
        """把 ``"1 3 5"`` / ``"1-5"`` / ``"全部"`` 解析成 1-based 序号列表。

        返回去重且升序的合法序号；``pool`` 用来判断越界。
        """
        tokens = self._tokens(spec)
        if not tokens:
            return []
        total = len(pool)
        if any(t in ("全部", "所有", "all", "全") for t in tokens):
            return list(range(1, total + 1))

        result: list[int] = []
        for token in tokens:
            if "-" in token or "~" in token or "－" in token:
                # 区间写法：1-5
                sep = "-" if "-" in token else ("~" if "~" in token else "－")
                left, _, right = token.partition(sep)
                start, end = _to_int(left, 0), _to_int(right, 0)
                if start and end and start <= end:
                    result.extend(range(start, end + 1))
                continue
            if token.isdigit():
                result.append(int(token))
        # 去重、排序、过滤越界
        return sorted({i for i in result if 1 <= i <= total})

    def _parse_repeat_spec(self, spec: str, pool: list[Any]) -> list[tuple[int, int]]:
        """把 ``"1×20"`` / ``"1*20"`` / ``"1x20"`` 解析成 ``[(1, 20)]``（v1.18.65）。

        站长：「水族馆可以使用的道具增加一次性多次使用道具功能，比如说一次喂鱼十几包
        龙涎饲料这种」。这就是给「用 <道具> <栏位>」加一个**重复次数**。

        * 不写次数 = 1 次（``1 2 3`` 等价于 ``1×1 2×1 3×1``）；
        * 区间写法 ``1-3`` 展开成 1、2、3，各 1 次；``1-3×5`` = 三条各 5 次；
        * 写 ``全部`` = 全缸各 1 次；
        * 次数上限 ``MAX_REPEAT``（防止手滑写个 999999 把 CPU 和道具一次抽干）；
        * 同一个栏位写多次会**合并**（``1×5 1×3`` = 1 号鱼 8 次）。
        """
        tokens = self._tokens(spec)
        if not tokens:
            return []
        total = len(pool)
        if any(t in ("全部", "所有", "all", "全") for t in tokens):
            return [(i, 1) for i in range(1, total + 1)]

        merged: dict[int, int] = {}
        for token in tokens:
            body = token.replace("×", "*").replace("✕", "*").replace("✖", "*")
            body = body.replace("x", "*").replace("X", "*").replace("ｘ", "*")
            times = 1
            if "*" in body:
                head, _, tail = body.partition("*")
                body = head
                times = max(1, min(_to_int(tail, 1), MAX_REPEAT))
            if "-" in body or "~" in body or "－" in body:
                sep = "-" if "-" in body else ("~" if "~" in body else "－")
                left, _, right = body.partition(sep)
                start, end = _to_int(left, 0), _to_int(right, 0)
                if start and end and start <= end:
                    for i in range(start, end + 1):
                        if 1 <= i <= total:
                            merged[i] = min(MAX_REPEAT, merged.get(i, 0) + times)
                continue
            if body.isdigit():
                i = int(body)
                if 1 <= i <= total:
                    merged[i] = min(MAX_REPEAT, merged.get(i, 0) + times)
        return sorted(merged.items())

    def _find_fish_by_name(self, name: str) -> dict[str, Any] | None:
        """按名字找鱼，支持模糊匹配。

        顺序：完全相等 -> id 相等 -> 去掉「鱼/儿」等后缀 -> 包含关系。
        这样 `/钓鱼 卖 鲤鱼`、`/钓鱼 卖 小鲫`、`/钓鱼 卖 七彩` 都能命中。
        """
        name = (name or "").strip()
        if not name:
            return None
        lowered = name.lower()

        # 1) 精确
        for fish in FISH_POOL:
            if fish["name"] == name or fish["id"].lower() == lowered:
                return fish
        # 2) 去掉常见后缀再比
        stripped = name.rstrip("鱼儿苗")
        if stripped and stripped != name:
            for fish in FISH_POOL:
                if fish["name"] == stripped:
                    return fish
        # 3) 包含（取最短的名字，避免「鲫鱼」命中「小鲫鱼」之外的歧义）
        candidates = [
            fish for fish in FISH_POOL if name in fish["name"] or fish["name"] in name
        ]
        if candidates:
            candidates.sort(key=lambda f: len(f["name"]))
            return candidates[0]
        return None

    async def _finalize_sale(
        self,
        event: AstrMessageEvent,
        player: dict[str, Any],
        sold: list[tuple[dict[str, Any], int]],
        title: str,
        bonus_income: int = 0,
        skipped_locked: int = 0,
        skipped_order: int = 0,
    ):
        """统一的卖出结算与文案（背包卖鱼共用）。"""
        income = sum(price for _, price in sold)
        player["gold"] = _safe_int(player.get("gold"), 0, 0) + income
        player["total_sold"] = _safe_int(player.get("total_sold"), 0, 0) + len(sold)
        if bonus_income > 0:
            player["market_best_bonus"] = max(
                _safe_int(player.get("market_best_bonus"), 0, 0), bonus_income
            )
        new_ach = self._check_achievements(player)
        saved = await self._save_player(player)
        await self._touch_leaderboard(player)

        lines = [f"{title} {len(sold)} 条 → {_fmt_gold(income)} 金币"]
        if bonus_income > 0:
            lines.append(f"　📈 其中行情加成 +{_fmt_gold(bonus_income)}")
        for instance, price in sorted(sold, key=lambda x: -x[1])[:5]:
            lines.append(f"　{_instance_line(instance)} → {_fmt_gold(price)}")
        if len(sold) > 5:
            lines.append(f"　… 其余 {len(sold) - 5} 条已一并卖出")
        if skipped_locked or skipped_order:
            lines.append(
                f"　🔒跳过锁定 {skipped_locked} 条　📋跳过订单需要 {skipped_order} 条"
            )
        lines.append(f"💰 余额 {_fmt_gold(player['gold'])}")
        if new_ach:
            lines.append("🎉 " + "；".join(new_ach))
        if not saved:
            lines.append("⚠️ 保存失败")
        async for _r in self._say_msg(event, "sell.result", event.plain_result("\n".join(lines))):
            yield _r



    def _find_bait(self, text: str) -> str | None:
        text = (text or "").strip()
        if not text:
            return None
        lowered = text.lower()
        for bait_id, bait in self.baits.items():
            if text == bait.get("name") or lowered == bait_id:
                return bait_id
        return None

    def _find_item(self, text: str) -> str | None:
        text = (text or "").strip()
        if not text:
            return None
        lowered = text.lower()
        for item_id, item in self.items.items():
            if text == item.get("name") or lowered == item_id:
                return item_id
        return None

    def _match_species(self, text: str) -> dict[str, Any] | None:
        text = (text or "").strip()
        if not text:
            return None
        lowered = text.lower()
        for fish in FISH_POOL:
            if fish["name"] == text or fish["id"].lower() == lowered:
                return fish
        return None
    def _find_location(self, text: str) -> dict[str, Any] | None:
        text = (text or "").strip()
        if not text:
            return None
        lowered = text.lower()
        for loc in self.locations:
            if text == loc["name"] or lowered == loc["id"]:
                return loc
        return None

    def _find_rod(self, text: str) -> dict[str, Any] | None:
        text = (text or "").strip()
        if not text:
            return None
        lowered = text.lower()
        for rod in self.rods:
            if text == rod["name"] or lowered == rod["id"]:
                return rod
        return None

    # -------------------------------------------------------------------------
    # 等级解锁购买权限（鱼竿 / 鱼饵）
    # -------------------------------------------------------------------------

    def _unlock_shortage(
        self, player: dict[str, Any], item: dict[str, Any]
    ) -> list[str]:
        """返回「还差什么才能买」的文案列表（空列表 = 可以买）。

        - 解锁等级：``unlock_level``（鱼竿与鱼饵都有）
        - 需要鱼竿：鱼饵的 ``need_rod``，填鱼竿 id 或名称，**拥有**即可（不要求装备）
        已经拥有的东西不受影响（只限制购买，不回收、不禁用）。
        """
        lacks: list[str] = []
        need_level = max(1, _safe_int(item.get("unlock_level"), 1, 1))
        level = _player_level(player)
        if level < need_level:
            lacks.append(f"{need_level} 级（你现在 {level} 级）")
        rod_ref = str(item.get("need_rod") or "").strip()
        if rod_ref:
            rod = self.rod_by_id.get(rod_ref) or self._find_rod(rod_ref)
            owned: list[str] = player.get("rods") or [DEFAULT_ROD]
            if rod is not None and rod["id"] not in owned:
                lacks.append(f"先有 {rod['emoji']}{rod['name']}")
            elif rod is None:
                # 配了一个不存在的鱼竿：当没要求，别把玩家卡死
                logger.warning(
                    f"{item.get('id')} 的「需要鱼竿」写的是 {rod_ref!r}，没有这款鱼竿，已忽略"
                )
        return lacks

    def _unlock_refuse_text(self, player: dict[str, Any], item: dict[str, Any]) -> str:
        """买不了时的完整拒绝文案（空串 = 可以买）。

        玩家直接点名买未解锁的东西时要给出**明确原因**，
        不能静默失败、也不能假装这东西不存在。
        """
        name = str(item.get("name") or "这个")
        need_level = max(1, _safe_int(item.get("unlock_level"), 1, 1))
        level = _player_level(player)
        if level < need_level:
            return f"🔒 {name}要 {need_level} 级才能买，你现在 {level} 级"
        rod_ref = str(item.get("need_rod") or "").strip()
        if rod_ref:
            rod = self.rod_by_id.get(rod_ref) or self._find_rod(rod_ref)
            owned: list[str] = player.get("rods") or [DEFAULT_ROD]
            if rod is not None and rod["id"] not in owned:
                return f"🔒 {name}得先有 {rod['emoji']}{rod['name']}"
        return ""

    def _rod_need_text(self, item: dict[str, Any]) -> str:
        """解锁条件摘要（给鱼竿/鱼饵列表用，不管玩家当前等级）。"""
        bits: list[str] = []
        need_level = max(1, _safe_int(item.get("unlock_level"), 1, 1))
        if need_level > 1:
            bits.append(f"{need_level} 级")
        rod_ref = str(item.get("need_rod") or "").strip()
        if rod_ref:
            rod = self.rod_by_id.get(rod_ref) or self._find_rod(rod_ref)
            bits.append(f"需 {rod['name'] if rod else rod_ref}")
        return "、".join(bits)

    #: 权重低于此值的算「隐藏生物」，不计入钓点图鉴完成度
    CODEX_HIDDEN_WEIGHT = 0.5

    def _location_species(self, loc_id: str) -> list[str]:
        """某钓点的「常规鱼种」（排除隐藏生物，它们太稀少不该卡进度）。"""
        pool = LOCATION_WEIGHTS.get(loc_id) or {}
        return [
            fid
            for fid, weight in pool.items()
            if weight >= self.CODEX_HIDDEN_WEIGHT
            and fid in FISH_BY_ID
            and fid not in HIDDEN_EVERYWHERE   # 隐藏生物（大肥鱼等）不计入
        ]

    def _location_codex_progress(self, player: dict[str, Any], loc_id: str) -> tuple[int, int]:
        """返回 (已收集, 需要收集)。"""
        collection = player.get("collection") or {}
        species = self._location_species(loc_id)
        got = sum(
            1
            for fid in species
            if isinstance(collection.get(fid), dict)
            and _safe_int(collection[fid].get("count"), 0, 0) > 0
        )
        return got, len(species)

    def _prev_location_id(self, loc_id: str) -> str | None:
        """按门槛顺序排在前一个的钓点 id。"""
        order = sorted(
            self.locations,
            key=lambda l: (_safe_int(l.get("level_gate"), 1, 1),
                           _safe_int(l.get("gold_gate"), 0, 0)),
        )
        ids = [l["id"] for l in order]
        if loc_id not in ids:
            return None
        index = ids.index(loc_id)
        return ids[index - 1] if index > 0 else None
    @filter.command("钓鱼")
    @safe_handler
    async def fishing(
        self,
        event: AstrMessageEvent,
        a1: str = "",
        a2: str = "",
        a3: str = "",
        a4: str = "",
        a5: str = "",
        a6: str = "",
    ):
        """钓鱼小游戏。发 /钓鱼 帮助 看说明。

        所有子命令都挂在 /钓鱼 下，只注册 1 个指令名，避免与别的插件撞名。

        **参数个数不限**（v1.18.17）：参数是从原始消息里现取的，不是 AstrBot 填的
        形参（形参只有 a1~a6，多出来的会被框架丢掉）。所以
        `/钓鱼 卖 1 2 3 4 5 6 7 8`、`/钓鱼 水族馆 放 1-20` 这类写法全都认；
        形参仍然保留，纯粹是给「直接调用 handler」的单测当兜底。
        """
        user_id = str(event.get_sender_id())
        # ---- 键盘名额：每条新指令开始时腾出这条入站消息的名额 ----
        # QQ 官方一条入站消息只有**第一条**回复能挂键盘（被动回复上限 5 次 + 主动发送
        # 不支持 keyboard，见 `_interactions._keyboard_slot_free`）。名额按 msg_id
        # 记账，而同一个 message_id 可能被重复处理（重投 / 单测直接反复调 handler），
        # 所以在指令入口清一次 —— 之后这一轮里只有第一处需要按钮的回复能拿到键盘。
        self._reset_keyboard_slot(event)
        # ---- v1.18.17：参数直接读原始消息，**不再受形参个数限制** ----
        # AstrBot 的 CommandFilter 按形参个数逐个塞参数，多出来的 token 会被直接丢掉：
        # `/钓鱼 水族馆 放 1 2 3 4 5` 永远只能放 4 条（站长报的「一次最多四条」就是它）。
        # 这里从 message_str 里取「指令名之后的全部 token」，想写多少个就写多少个；
        # 拿不到原始消息时（单测直接调 handler）退回形参 a1~a6，行为与以前一致。
        raw_args = self._command_args(event)
        if raw_args:
            args = raw_args
        else:
            args = [
                str(x).strip()
                for x in (a1, a2, a3, a4, a5, a6)
                if str(x or "").strip()
            ]
        a1 = args[0] if args else ""
        # ---- 少打空格的容错：/钓鱼 卖1、/钓鱼 帮助2 ----
        # 把粘连的写法拆成「子命令 + 参数」，并整体后移写回参数列表，
        # 这样各子命令拿到的参数与正常写法完全一致。
        # （`/钓鱼 卖1 2` 会被拆成 a1="卖"、参数 ["1", "2"]）
        peeled = self._peel_subcommand(a1)
        rest = args[1:]
        if peeled:
            a1, glued = peeled
            rest = [glued] + rest
        # 各子命令的形参（a2~a6）与 rest 对齐：a2 = 子命令后的第一个参数……
        # 分派链里不少 handler 直接吃 `a2`（例如 `_cmd_rods(event, a2, after_first)`），
        # 所以这里必须把「粘连写法拆出来的那一段」也写回去。
        a2 = rest[0] if len(rest) > 0 else ""
        a3 = rest[1] if len(rest) > 1 else ""
        a4 = rest[2] if len(rest) > 2 else ""
        a5 = rest[3] if len(rest) > 3 else ""
        a6 = rest[4] if len(rest) > 4 else ""
        key = a1.lower()
        # ---- 命令别名归一化（配置 command_aliases，见 _apply_command_config）----
        # 表里**只有站长新加的别名**：内置写法原样流下去走老分派链，
        # 所以默认配置下这一行是恒等变换，升级前后行为逐字一致。
        key = COMMAND_ALIASES.get(key, key)
        # after_sub：子命令之后的「全部」参数（a2~a6），给「卖 / 水族馆 / 锁定」这类
        after_sub = " ".join(x for x in rest if x.strip())
        # after_first：再往后一个参数（a3~a6），给「用 / 商店 / 订单」这类
        # 已经把 a2 当作第一个参数单独传下去的子命令，避免重复。
        after_first = " ".join(x for x in rest[1:] if x.strip())

        # ---- 拉线（互动）----
        if key in PULL_WORDS:
            # 连钓里逐条弹拉线时不回「✅ 收到，正在收线…」：连钓要连点好几下，
            # 每条都回一句就是刷屏（v1.18.33）。先问再解，解开后记录就没了。
            quiet = self._pull_is_quiet(event)
            if self._resolve_pull(event):
                if not quiet:
                    async for _r in self._say_msg(event, "pull.confirm", event.plain_result("✅ 收到，正在收线…")):
                        yield _r
                return
            async for _r in self._say_msg(event, "pull.none", event.plain_result(
                    self._pull_miss_hint(str(user_id)) or
                    "🤔 现在没有鱼咬钩。直接发 /钓鱼 下竿，"
                    "等提示「咬钩了」再发 /钓鱼 拉"
                )):
                yield _r
            return

        # ---- 帮助（分页：/钓鱼 帮助 2）----
        if key in ("帮助", "help", "?", "？", "菜单", "指令"):
            # 页码先夹到合法区间：按钮要按「第几页 / 共几页」决定给不给上一页、下一页
            total_pages = max(1, len(self._help_pages()))
            page_no = int(_clamp(_to_int(after_sub, 1), 1, total_pages))
            help_text = self._help_text(page_no)
            async for reply in self._say(
                event, help_text, "help.page",
                page=(page_no, total_pages, "/钓鱼 帮助"),
            ):
                yield reply
            return

        # ---- 无参数：下竿 ----
        if not a1:
            async for result in self._do_cast(event, user_id, ""):
                yield result
            return
        # ---- 显式写「下竿」类同义词也算抛竿（空格/用词容错）----
        if key in CAST_WORDS:
            async for result in self._do_cast(event, user_id, ""):
                yield result
            return
        # ---- /钓鱼 <数字> = 连钓 N 次（1 等价于单竿，走完整流程含拉线）----
        if a1.isdigit():
            times = _to_int(a1, 0)
            if times <= 0:
                async for _r in self._say_msg(event, "cast.multi_bad_times", event.plain_result("🤔 连钓次数要写正整数，例如 /钓鱼 10")):
                    yield _r
                return
            if times == 1:
                async for result in self._do_cast(event, user_id, ""):
                    yield result
                return
            async for result in self._do_multi_cast(event, user_id, times):
                yield result
            return
        # ---- 指定鱼饵下竿（支持「/钓鱼 蚯蚓」）----
        bait_id = self._find_bait(a1)
        if bait_id is not None:
            async for result in self._do_cast(event, user_id, a1):
                yield result
            return
        # ---- 直接「去某钓点」----
        if key in ("去", "前往", "go"):
            async for result in self._cmd_locations(event, user_id, "去", after_sub):
                yield result
            return

        # ---- 子命令分派 ----
        handler = None
        tokens = self._tokens(*rest)
        if key in ("背包", "包", "bag", "鱼篓"):
            handler = self._cmd_bag(event, user_id, after_sub)
        elif key in (
            "卖", "卖鱼", "sell", "卖垃圾",
            "卖光光", "卖光", "清空", "全卖", "空背包", "sellall", "一键卖出",
        ):
            # 「卖光光」= 清空背包（锁定的留着）；旧词「卖垃圾/清理」不再有单独玩法
            if key in (
                "卖光光", "卖光", "清空", "全卖", "空背包", "sellall", "一键卖出"
            ) and not tokens:
                tokens = ["光光"]
            elif key == "卖垃圾" and not tokens:
                tokens = ["__junk_removed__"]
            handler = self._cmd_sell(event, user_id, *tokens)
        elif key in ("图鉴", "收集", "collection"):
            handler = self._cmd_collection(event, user_id, after_sub)
        elif key in ("水族馆", "馆", "aquarium", "缸"):
            handler = self._cmd_aquarium(event, user_id, a2, after_first)
        # 背包扩容（排在「商店」之前，否则「商店 扩容」会被商店吞掉）
        elif key in ("扩建背包", "扩容", "背包扩容", "鱼篓扩容"):
            handler = self._cmd_backpack_upgrade(event, user_id)
        elif key in ("锁定", "锁", "lock"):
            handler = self._cmd_lock(event, user_id, *tokens)
        elif key in ("解锁", "解", "unlock"):
            # 智能解锁：写钓点名 = 解锁钓点，写序号/鱼名 = 解锁背包里的鱼
            #（以前 /钓鱼 解锁 山间湖泊 会被当成「背包里没这条鱼」，很劝退）
            if self._find_location(a2 + " " + after_first) is not None or \
                    self._find_location(a2) is not None:
                handler = self._cmd_locations(event, user_id, "解锁", after_sub)
            else:
                handler = self._cmd_unlock(event, user_id, *tokens)
        elif key in ("今日", "天气", "行情", "today", "weather", "market"):
            handler = self._cmd_today(event, user_id)
        elif key in ("排行", "排行榜", "rank", "top", "榜"):
            handler = self._cmd_leaderboard(event, user_id, after_sub)
        # 商店拆成三家（v1.18.13）：老写法「商店」只回一句指路，不再当货架用
        elif key in ("商店", "铺子", "shop"):
            handler = self._cmd_shop_moved(event, user_id, a2)
        elif key in ("鱼饵店", "饵店", "鱼饵", "饵"):
            handler = self._cmd_bait_shop(event, user_id, a2, after_first)
        elif key in ("道具店", "道具", "物品", "item", "items"):
            handler = self._cmd_item_shop(event, user_id, a2, after_first)
        elif key in ("用", "使用", "道具用", "use"):
            handler = self._cmd_use_item(event, user_id, a2, after_first)
        elif key in ("查", "查询", "鱼", "鱼查", "资料", "fish", "info", "lookup"):
            handler = self._cmd_fish_info(event, user_id, after_sub)
        elif key in ("事件", "插曲", "选择", "event"):
            handler = self._cmd_event(event, user_id, a2)
        elif key in ("换饵", "换鱼饵", "装备饵", "上饵", "bait", "equip_bait"):
            handler = self._cmd_equip_bait(event, user_id, a2, after_first)
        elif key in ("体力", "体力值", "活力", "stamina"):
            handler = self._cmd_stamina(event, user_id)
        elif key in ("档案", "profile", "me"):
            handler = self._cmd_profile(event, user_id)
        elif key in ("金币", "gold"):
            # 老名字：它显示的一直是「档案」，名不符实，现在只给迁移提示
            handler = self._cmd_gold_renamed(event, user_id)
        elif key in ("签到", "sign"):
            handler = self._cmd_sign(event, user_id)
        elif key in ("订单", "任务", "order", "orders"):
            handler = self._cmd_orders(event, user_id, a2, after_first)
        elif key in ("钓点", "地点", "地图", "map", "location"):
            handler = self._cmd_locations(event, user_id, a2, after_first)
        elif key in ("鱼竿", "竿", "rod"):
            handler = self._cmd_rods(event, user_id, a2, after_first)
        elif key in ("杂物", "漂流瓶", "收集品", "collect"):
            handler = self._cmd_collectibles(event, user_id)
        elif key in ("大鱼乐", "彩票", "抽奖", "lottery", "lotto", "买彩票", "乐透"):
            # a2 是张数，after_first 是「概率 / 记录」这类子参数
            handler = self._cmd_lottery(event, user_id, a2, after_first)

        # ---- 常用操作的「一步到位」短写法（v1.18.0）---------------------------------
        # 让玩家少打字：把「水族馆 取 1」「商店 买 蚯蚓」「竿 用 星辉竿」这类嵌套写法
        # 压缩成 /钓鱼 取 1、/钓鱼 买 蚯蚓、/钓鱼 装备 星辉竿。
        # 参数一律按各子命令原本的约定传（别自己拼字符串），老写法逐字照旧。
        # ⚠️ 参数要用 after_sub（= a2 + after_first）：短写法里 a2 就是第一个参数，
        # 各子命令要的「子命令之后的全部参数」正好等于它。
        elif handler is None and key in ("放", "放入", "养"):
            handler = self._cmd_aquarium(event, user_id, "放", after_sub)
        elif handler is None and key in ("取", "取出", "拿"):
            handler = self._cmd_aquarium(event, user_id, "取", after_sub)
        elif handler is None and key in ("领", "收租", "收益"):
            handler = self._cmd_aquarium(event, user_id, "领", "")
        elif handler is None and key in ("喂", "投喂"):
            # /钓鱼 喂 高级饲料 1 == /钓鱼 用 高级饲料 1
            handler = self._cmd_use_item(event, user_id, a2, after_first)
        elif handler is None and key in ("洗", "洗髓"):
            # /钓鱼 洗 2 == /钓鱼 用 洗髓丹 2
            handler = self._cmd_use_item(event, user_id, "洗髓丹", after_sub)
        elif handler is None and key in ("交", "交单", "交货"):
            # /钓鱼 交 1 2 == /钓鱼 订单 交 1 2
            handler = self._cmd_orders(event, user_id, "交", after_sub)
        elif handler is None and key in ("买", "购买", "buy"):
            # 智能买：按名字自动认它是鱼竿 / 道具 / 鱼饵（三店各买各的）
            # （以前 /钓鱼 买 星辉竿 会被商店回一句「没这个货」）
            if not str(a2 or "").strip():
                handler = self._cmd_buy_usage(event, user_id)
            elif self._find_rod(a2) is not None:
                handler = self._cmd_rods(event, user_id, "买", after_sub)
            elif self._find_item(a2) is not None:
                handler = self._cmd_item_shop(event, user_id, "买", after_sub)
            else:
                handler = self._cmd_bait_shop(event, user_id, "买", after_sub)
        elif handler is None and key in ("装备", "换竿", "换鱼竿"):
            # /钓鱼 装备 星辉竿 == /钓鱼 竿 用 星辉竿
            # v1.18.17：不带名字（`/钓鱼 装备`）= **自动换上最好的竿**（已拥有里
            # 价值加成最高的那根）—— 按钮没法带名字，得有这么一条无参写法。
            handler = self._cmd_rods(event, user_id, "用", after_sub)
        elif handler is None and key in ("自动", "自动补给", "auto"):
            # /钓鱼 自动 锦鲤玉佩 = 手气道具用完自动买 + 自动用；/钓鱼 自动 关 = 关掉
            handler = self._cmd_auto_supply(event, user_id, after_sub)
        elif handler is None and key in ("称号", "头衔", "title"):
            # 称号（v1.18.17 的后期金币回收口）：/钓鱼 称号 [买|戴 <名字>]
            handler = self._cmd_titles(event, user_id, after_sub)
        elif handler is None and key in ("供奉", "香火", "上香", "offering"):
            # 香火供奉：花大钱换 24 小时的挂机产出与手气加成
            handler = self._cmd_offering(event, user_id)

        if handler is None:
            # ---- 自定义命令（配置 custom_commands）----
            # 放在最后：内置子命令永远优先，自定义命令只在「都不认识」时才认。
            # _custom_running 是防递归保险：执行: 的目标已被限定为内置子命令，
            # 这里再兜一层，任何情况下都不会出现「命令套命令」。
            if key in CUSTOM_COMMANDS and not self._custom_running:
                async for result in self._run_custom_command(event, user_id, key):
                    yield result
                return
            async for _r in self._say_msg(event, "help.unknown", event.plain_result(
                    f"🤔 不认识「{a1}」这个用法\n"
                    f"　发 /钓鱼 帮助 1 看全部指令"
                    f"（共 {len(self._help_pages())} 页）\n"
                    f"　最常用：/钓鱼 下竿 ｜ /钓鱼 背包 ｜ /钓鱼 卖 ｜ /钓鱼 今日"
                )):
                yield _r
            return
        async for result in handler:
            yield result

    # -------------------------------------------------------------------------
    # 自定义命令（配置 custom_commands）
    # -------------------------------------------------------------------------

    def _custom_text_values(
        self, player: dict[str, Any], event: AstrMessageEvent
    ) -> dict[str, str]:
        """自定义命令文本里 ``{占位符}`` 的取值表（都取自玩家当前数据）。"""
        location = LOCATION_BY_ID.get(str(player.get("current_location") or "")) or {}
        name = str(player.get("last_name") or "")
        if not name:
            try:
                name = str(event.get_sender_name() or "")
            except Exception:
                name = ""
        return {
            "金币": _safe_int(player.get("gold"), 0, 0),
            "等级": _player_level(player),
            "钓获": _safe_int(player.get("total_caught"), 0, 0),
            "卖出": _safe_int(player.get("total_sold"), 0, 0),
            "背包": len(player.get("inventory") or []),
            "图鉴": len(player.get("collection") or {}),
            "杂物": len(player.get("collectibles") or {}),
            "昵称": name,
            "钓点": str(location.get("name") or "未知"),
        }

    async def _run_custom_command(
        self, event: AstrMessageEvent, user_id: str, name: str
    ):
        """执行一条自定义命令（只在 fishing 分派末尾、内置都不认识时调用）。

        * ``发送:`` → 直接回复一段文本，替换 ``{金币}`` 之类占位符
        * ``执行:`` → 依次把每条子命令**当成一次正常输入**再走一遍分派（一层）

        目标在配置解析阶段就已被限定为内置子命令，所以这里不会再命中
        自定义命令；``_custom_running`` 再兜一层，彻底杜绝递归。
        """
        action, body = CUSTOM_COMMANDS.get(name, ("", ""))
        if action == "发送":
            player = await self._load_player(user_id)
            text = CALC._fill_custom_text(body, self._custom_text_values(player, event))
            async for _r in self._say_msg(event, "custom.send", event.plain_result(text)):
                yield _r
            return
        if action != "执行":
            return
        self._custom_running = True
        try:
            for piece in CALC._split_command_pieces(body):
                tokens = self._tokens(piece)
                if not tokens:
                    continue
                async for result in self.fishing(event, *tokens[:6]):
                    yield result
        finally:
            self._custom_running = False

    async def _sync_defaults(self) -> None:
        """把代码里的新默认数值同步进插件配置 —— **不覆盖站长自己改过的项**。

        站长痛点：每次插件升级改了数值，都得手改回来一遍（v1.18.8 他明确要求别再重置）。
        这里的规则：

        1. ``user_edited_keys`` 里的键（编辑器每次保存都会记）**一律跳过**；
        2. 容器类配置**合并**：新钓点系数 / 各档基准价 / 品质权重这些，
           保留站长已有的条目，只把官方新增的补进去；
        3. ``DEFAULTS_VALUE_FIXES`` 里的整串修正：只有当前值逐字等于旧默认才替换；
        4. 第一次启用（配置里还没有 ``user_edited_keys``）时，把「当前值 ≠ 新默认」的键
           全当成站长改过的记下来，这次升级**一个都不覆盖**；
        5. ``defaults_sync_mode``：``auto``（默认，按上面来）/ ``all``（强制全部重置）/
           ``off``（什么都不动，只更新指纹）。
        """
        try:
            current = _defaults_fingerprint()
            stored = str(self.config.get("config_fingerprint") or "").strip()
            if stored == current:
                return
            mode = str(self.config.get("defaults_sync_mode") or "auto").strip().lower()
            if mode not in ("auto", "all", "off"):
                mode = "auto"

            changed: dict[str, Any] = {}
            edited: list[str] = []
            if mode != "off":
                try:
                    edited = [
                        str(x) for x in (self.config.get("user_edited_keys") or [])
                        if str(x).strip()
                    ]
                except Exception:
                    edited = []
                edited_set = set(edited)
                # 站长**明确同意交还给插件**的键（CONSENTED_DEFAULT_KEYS）：从「他改过的」
                # 名单里摘掉，让下面的同步把它们推到新版（他勾选采用了我给的值）。
                _consented = [k for k in CONSENTED_DEFAULT_KEYS if k in edited_set]
                if _consented:
                    edited = [k for k in edited if k not in set(CONSENTED_DEFAULT_KEYS)]
                    edited_set = set(edited)
                    changed["user_edited_keys"] = edited
                    logger.info(
                        f"配置同步：站长已同意改用新版数值的 {len(_consented)} 项"
                        f"（{'、'.join(_consented)}）交还给默认值同步"
                    )
                # 「还没记录过任何站长改过的键」= 第一次启用这套机制：
                # 此时无法区分「站长改过」和「官方这次改了默认值」，一律按前者处理
                # （宁可少同步，也不要把他的话改回去）；官方确实改了默认值的那些键
                # 走上面的 DEFAULTS_VALUE_FIXES / 容器合并，照样能到老配置。
                first_run = not edited
                keys = list(DEFAULTS) if mode == "all" else _synced_default_keys()
                kept_for_user: list[str] = []

                for key in keys:
                    if key in ("config_fingerprint", "defaults_sync_mode", "user_edited_keys"):
                        continue
                    now = self.config.get(key)
                    want = DEFAULTS[key]

                    # 3) 官方改过的整串（例如品质档位表改名）：只有逐字等于旧默认才替换
                    fixed = False
                    for old_value, new_value in DEFAULTS_VALUE_FIXES.get(key, ()):  # type: ignore[arg-type]
                        if now == old_value:
                            if now != new_value:
                                changed[key] = new_value
                            fixed = True
                            break
                    if fixed:
                        continue

                    # 2) 容器类：合并（保留站长条目，补官方新增）
                    if key in DEFAULTS_MERGE_MAP_KEYS and isinstance(want, str):
                        merged = _merge_map_text(now, want)
                        if merged != now:
                            changed[key] = merged
                        continue
                    if key in DEFAULTS_MERGE_LIST_KEYS and isinstance(want, list):
                        merged_list = _merge_list_tail(now, want)
                        if merged_list != now:
                            changed[key] = merged_list
                        continue
                    if key in DEFAULTS_MERGE_NUMBER_TEXT_KEYS and isinstance(want, str):
                        merged_text = _merge_number_text(now, want)
                        if merged_text != now:
                            changed[key] = merged_text
                        continue

                    # 1) 站长改过的：一个字节都不动（模式 all 时例外，那是明确要重置）
                    if mode == "auto" and key in edited_set:
                        kept_for_user.append(key)
                        continue

                    # 4) 第一次启用：值和新默认不一样的，当成站长改过的保住它
                    if mode == "auto" and first_run and now != want:
                        kept_for_user.append(key)
                        continue

                    if now != want:
                        changed[key] = want

                if mode == "auto":
                    # 记下这次保住的所有键 + 之前的记录（以后也不会再被覆盖）
                    merged_edited = sorted(set(edited) | set(kept_for_user))
                    if merged_edited != sorted(set(edited)) or first_run:
                        changed["user_edited_keys"] = merged_edited
                    if kept_for_user:
                        logger.info(
                            f"配置同步：保留站长自己改过的 {len(kept_for_user)} 项"
                            f"（{'、'.join(kept_for_user[:6])}"
                            f"{'…' if len(kept_for_user) > 6 else ''}）"
                        )

            changed["config_fingerprint"] = current

            self.config.update(changed)
            if not await self._save_plugin_config():
                logger.warning("默认值同步写盘失败（本次启动仍按内存里的新值运行）")

            if mode == "off":
                logger.info(
                    f"默认值同步已关闭（defaults_sync_mode=off），"
                    f"仅更新指纹为 {current}，配置值保持站长自己的设置"
                )
            else:
                logger.info(
                    f"配置已同步 {len(changed) - 1} 项到新版默认值"
                    f"（模式 {mode}，指纹 {current}）"
                )
        except Exception as e:  # pragma: no cover - 绝不能让插件启动失败
            logger.warning(f"同步默认配置失败（不影响使用）：{e}")

    async def _save_plugin_config(self) -> bool:
        """把 self.config 落盘。优先异步 API，退回同步 API，取不到就只留在内存。"""
        saver = getattr(self.config, "save_config_async", None)
        if callable(saver):
            try:
                await saver()
                return True
            except Exception as e:
                logger.warning(f"save_config_async 失败：{e}")
        saver = getattr(self.config, "save_config", None)
        if callable(saver):
            try:
                saver()
                return True
            except Exception as e:  # pragma: no cover
                logger.warning(f"save_config 失败：{e}")
        return False

    async def initialize(self) -> None:
        """插件激活时自检 + 打印配置摘要。"""
        # 先把新版默认数值同步进配置，再按同步后的配置重建缓存
        try:
            await self._sync_defaults()
            self._refresh_config()
        except Exception as e:  # pragma: no cover - 不影响插件继续运行
            logger.warning(f"配置同步/重载失败（继续用启动时读到的配置）：{e}")
        try:
            order = " < ".join(self._rarity_name(r) for r in RARITY_ORDER)
            interactive = "、".join(
                sorted(self.interactive_rarities, key=lambda r: RARITY_RANK.get(r, 0))
            )
            logger.info(
                f"群钓鱼插件已加载：{len(FISH_POOL)} 种鱼、{len(self.baits) - 1} 种鱼饵、"
                f"{len(self.items)} 种道具、{len(ACHIEVEMENTS)} 个成就；"
                f"品质档位 {order}；需拉线：{interactive}"
            )
            weights = self.cfg["quality_weights"]
            total = sum(weights) or 1
            logger.info(
                "个体品质分布："
                + "　".join(
                    f"{QUALITY_ORDER[i]} {weights[i] / total * 100:.0f}%"
                    for i in range(len(QUALITY_ORDER))
                )
            )
            self._warn_stale_config()
            self._init_data_management()
        except Exception as e:  # pragma: no cover
            logger.error(f"钓鱼插件初始化自检失败：{e}")
        # 编辑器页面的上传→轮询通道（整段自带 try/except，失败也不影响启动）
        self.start_editor_bridge()


    def _merge_content_defaults(self) -> bool:
        """把新版默认内容合并进旧配置（缺什么补什么，已有的不动）。

        AstrBot 保存过的配置项会**原样保留**，所以升级插件后老站长会一直
        用着旧的钓点/鱼饵表。这里按 id 比对：配置里没有的条目才追加，
        站长的自定义条目一律保留。可用 `content_auto_merge` 关掉。
        """
        try:
            if not bool(self.config.get("content_auto_merge", True)):
                return False
        except Exception:
            pass
        changed = False
        changed = self._apply_content_row_fixes() or changed
        # 「一整段多行文本」形态的内容表（鱼池/杂物/变异/天气/彩蛋）：
        # 单独走追加逻辑 —— 漏了它 = 官方新增的内容永远进不了老配置
        #（v1.18.8 的三个新钓点就是这么空掉的，v1.18.16 把其余四张表也补上）
        for key in CONTENT_TEXT_KEYS:
            changed = self._merge_text_content_rows(key) or changed
        for key in self.CONTENT_LIST_KEYS:
            default = DEFAULTS.get(key)
            if not isinstance(default, list) or not default:
                continue
            current = self.config.get(key)
            if not isinstance(current, list) or not current:
                continue  # 空/损坏的交给配置兜底逻辑处理
            # ⚠️ **补齐「半截行」**：合并只按 id 判「有没有」，所以**早期版本生成过的行
            #    永远补不上后来新增的字段**。实测站长配置里那几条限定竿/饵凭证只有 7 段
            #    （`uses` / `rod` / `bait` 全缺），于是 `uses` 读成 0 ——
            #    「抽到限定饵/竿」根本不生效，还会报「鱼饵 xxx 不存在」。
            #    规则：**这一行的段数比新默认少**就整行换成新默认（多出来的段一律是我们
            #    后加的字段，不存在「站长自己删过字段」的合法情况）。
            _default_by_id = {
                str(line).split("|", 1)[0].strip(): str(line)
                for line in default
                if isinstance(line, str)
            }
            _fixed = 0
            _new_current: list[Any] = []
            for line in current:
                if not isinstance(line, str):
                    _new_current.append(line)
                    continue
                _want = _default_by_id.get(line.split("|", 1)[0].strip())
                if _want is not None and line.count("|") < _want.count("|"):
                    _new_current.append(_want)
                    _fixed += 1
                else:
                    _new_current.append(line)
            if _fixed:
                self.config[key] = _new_current
                current = _new_current
                changed = True
                logger.info(
                    f"配置自动升级：{key} 有 {_fixed} 行是早期版本的半截数据，已按新版默认"
                    "补齐字段（缺字段会让新功能直接失效）"
                )
            have = {
                str(line).split("|", 1)[0].strip()
                for line in current
                if isinstance(line, str)
            }
            added = [
                line
                for line in default
                if isinstance(line, str)
                and str(line).split("|", 1)[0].strip() not in have
            ]
            if not added:
                continue
            self.config[key] = list(current) + added
            changed = True
            names = "、".join(str(x).split("|", 1)[1] for x in added[:6] if "|" in x)
            logger.info(
                f"配置自动升级：{key} 补上 {len(added)} 条新版默认内容（{names}…）"
            )
        if changed:
            save = getattr(self.config, "save_config", None)
            if callable(save):
                try:
                    save()
                except Exception as e:  # pragma: no cover
                    logger.warning(f"配置自动升级落盘失败：{e}")
        return changed

    def _apply_content_row_fixes(self) -> bool:
        """把「官方改过的默认行」安全地推到老配置里（只在那一行没被改过时才改）。

        `content_auto_merge` 只补**缺的**行，改过的默认行永远到不了老配置
        （洗髓丹就是活例子：它的效果写的是旧别名 ``quality_up``，和锦鲤玉佩重复，
        v1.18.0 给它换了新效果，但站长配置里那一行不会自己变）。
        所以官方改一行时，在 ``_game_data.CONTENT_ROW_FIXES`` 里登记
        「旧整行 -> 新整行」：只有配置里那一行**逐字等于旧默认**才替换 ——
        站长自己动过的行一律不碰。
        """
        try:
            fixes = list(_CONTENT.get("CONTENT_ROW_FIXES") or [])
        except Exception:
            fixes = []
        # 本文件自己管的内容表（道具/鱼竿/鱼饵）的迁移也一起走这里
        fixes.extend(LOCAL_CONTENT_ROW_FIXES)
        changed = False
        for fix in fixes:
            if not (isinstance(fix, (list, tuple)) and len(fix) >= 3):
                continue
            key, old_line, new_line = str(fix[0]), str(fix[1]), str(fix[2])
            current = self.config.get(key)
            # 两种存法都要认：location_defs 这类是 list，
            # fish_defs 是「一整段多行文本」（v1.18.12 之前字符串直接被跳过，
            # 所以鱼池里官方改过的行永远推不到老配置）
            if isinstance(current, list):
                lines = [str(x) for x in current]
                join_with = None
            elif isinstance(current, str):
                lines = current.splitlines()
                join_with = "\n"
            else:
                continue
            if old_line not in lines or new_line in lines:
                continue
            lines = [new_line if line == old_line else line for line in lines]
            self.config[key] = lines if join_with is None else join_with.join(lines)
            changed = True
            row_id = old_line.split("|", 1)[0]
            logger.info(f"配置自动升级：{key} 的「{row_id}」按新版默认更新了效果")
        return changed

    def _merge_text_content_rows(self, key: str) -> bool:
        """把「一整段多行文本」形态的内容表补齐（只按 id 追加缺的行）。

        为什么单独写一条：这类表和 location_defs/rod_defs 那几张表不一样 ——

        1. 它们是多行**文本**（不是 list），通用合并只认 list，所以一直被跳过；
        2. `fish_defs` 更狠：**非空就整体接管鱼池**（见 `_apply_fish_defs`）。

        两条加起来就是一个很难自己发现的坑：站长配置里那份是某个旧版本的快照，
        那以后官方新增的内容**一条都进不来**。v1.18.8 的三个新钓点正是这么变成
        **空池**的（钓点靠 location_defs 的 list 合并补上了，鱼却卡在这里）；
        杂物 / 变异 / 天气 / 彩蛋四张表同理 —— 官方加了新天气，老配置里永远看不到。

        只按 id 追加缺的行：站长的行序、他改过的行、他自己加的内容一律不动。

        ⚠️ 两条判据，先宽松后保守：

        1. 配置里的行**全是官方条目的 id** → 它就是某个旧版本的快照（行数少也一样，
           老版本的官方表本来就短），补齐即可；
        2. 否则（夹了自定义内容）才看比例：官方 id 不到一半就当成「站长从头手配的
           小表」，**一个字都不改** —— 否则官方 200 多条鱼会直接淹没他的几只鱼
           （`test_local.py` 的 [11b] 钉着这条）。

        第 1 条是 v1.18.16 补的：以前只有比例判据，于是「旧版本的 3 条杂物」
        （3/7 < 50%）永远补不上官方新增的 4 条 —— 站长怎么升级都看不到新内容。
        """
        default = DEFAULTS.get(key)
        if not isinstance(default, str) or not default.strip():
            return False
        current = self.config.get(key)
        if current is None or (isinstance(current, str) and not current.strip()):
            # 空的/没配 → 走「内置内容」那条路，不需要补（内置已经是最新的）
            return False
        if not isinstance(current, (str, list)):
            return False

        is_list = isinstance(current, list)
        items = [str(x) for x in current] if is_list else str(current).splitlines()
        have = {
            item.split("|", 1)[0].strip()
            for item in items
            if item.strip() and "|" in item
        }
        official_ids = {
            line.split("|", 1)[0].strip()
            for line in default.splitlines()
            if line.strip() and "|" in line
        }
        if have and have <= official_ids:
            # 清一色官方条目：某个旧版本的快照（哪怕只留了几条），补齐就行
            pass
        elif have and len(have & official_ids) * 5 >= len(have) * 4:
            # 他自己的行里有 **≥80% 是官方条目**：说明这份表本来就是「官方表 + 少量自己的改动」
            # （典型：把 story.prompt 换成自己的静态按钮）。这种也该补齐缺的官方行 ——
            # v1.18.21 加的判据：站长那份 button_defs 有 12 个场景、其中 story.prompt 是他自己配的，
            # 按老规矩（全官方才算快照）就永远补不上其余 100 个场景的按钮。
            pass
        elif len(have & official_ids) * 2 < len(official_ids):
            # 手工内容居多：不动（官方新增的条目不该淹没站长的自定义）
            return False

        rows = [
            line.strip()
            for line in default.splitlines()
            if line.strip() and not line.strip().startswith("#") and "|" in line
        ]
        added = [line for line in rows if line.split("|", 1)[0].strip() not in have]
        if not added:
            return False
        if is_list:
            self.config[key] = list(current) + added
        else:
            self.config[key] = str(current).rstrip("\n") + "\n" + "\n".join(added)

        names = "、".join(line.split("|")[1] for line in added[:6] if "|" in line)
        logger.info(
            f"配置自动升级：{key} 补上 {len(added)} 条新版内容"
            f"（{names}{'…' if len(added) > 6 else ''}）"
        )
        return True

    def _merge_fish_defs(self) -> bool:
        """兼容旧调用点：鱼池的补齐（实现见 `_merge_text_content_rows`）。"""
        return self._merge_text_content_rows("fish_defs")

    def _warn_stale_config(self) -> None:
        """旧版配置残留体检。

        AstrBot 只在配置文件里**缺键**时补默认值，**已存在的键会原样保留**。
        所以升级插件后，旧配置里的 `fish_cost`、`location_defs`、`rod_defs`
        仍然是旧数值：钓点会少几个、部分鱼永远钓不到、收益也不是新标定的。
        这里主动把差异打出来，省得管理员对着「怎么少了个钓点」排查半天。
        """
        try:
            hint = (
                "　多半是旧版配置残留——请在 WebUI 插件配置页点「重置配置」，"
                "或删除 data/config/<插件目录名>_config.json 后重载插件。"
            )
            configured = [loc["id"] for loc in self.locations]
            missing = [lid for lid in LOCATION_WEIGHTS if lid not in configured]
            if missing:
                names = "、".join(
                    LOCATION_NAME_BY_ID.get(lid, lid) for lid in missing
                )
                stuck = [
                    f["name"]
                    for f in FISH_POOL
                    if (
                        {lid for lid, pool in LOCATION_WEIGHTS.items() if f["id"] in pool}
                        - set(configured)
                    )
                    and not (
                        {lid for lid, pool in LOCATION_WEIGHTS.items() if f["id"] in pool}
                        & set(configured)
                    )
                ]
                logger.warning(
                    f"检测到旧版配置：配置里只有 {len(configured)} 个钓点，"
                    f"缺少「{names}」；这 {len(stuck)} 种鱼将无法钓到"
                    f"（{'、'.join(stuck[:5])}），图鉴也集不齐。" + hint
                )

            # 鱼池快照是否落后：fish_defs 非空时它会**整体接管鱼池**，
            # 于是「配置里那份还是旧快照」就等于**官方新增的鱼一种都没有** ——
            # 空池的钓点表现是「进得去、钓不到任何东西、图鉴也是空的」（v1.18.12 修的）。
            # 正常情况下 _merge_fish_defs 已经把缺的补上了，这里只在
            # content_auto_merge=false（或补写盘失败）时才会喊人。
            try:
                raw_defs = _cfg_str(self.config, "fish_defs").strip()
            except Exception:
                raw_defs = ""
            if raw_defs:
                have = {
                    line.split("|", 1)[0].strip()
                    for line in raw_defs.splitlines()
                    if line.strip() and "|" in line
                }
                # 只对「官方鱼池的旧快照」喊人；手工配的小鱼池是站长的自由，不啰嗦
                official_ids = {f["id"] for f in BUILTIN_FISH_POOL}
                is_snapshot = len(have & official_ids) * 2 >= len(official_ids)
                # ⚠️ 要和**官方内置鱼池**比，不能和当前运行中的 FISH_POOL 比 ——
                # fish_defs 已经接管了鱼池，拿它自己比自己永远是「一个都不缺」。
                missing_fish = (
                    [f for f in BUILTIN_FISH_POOL if f["id"] not in have]
                    if is_snapshot
                    else []
                )
                if missing_fish:
                    empty_locs = [
                        loc["name"]
                        for loc in self.locations
                        if not _location_pool(loc["id"])
                    ]
                    logger.warning(
                        f"检测到旧版鱼池：配置里的 fish_defs 是旧快照，"
                        f"比当前版本少 {len(missing_fish)} 种鱼"
                        f"（{'、'.join(f['name'] for f in missing_fish[:5])}…）——"
                        + (
                            f"这些钓点会一条鱼都钓不到：{'、'.join(empty_locs)}。"
                            if empty_locs
                            else "这些鱼将无法钓到，图鉴也集不齐。"
                        )
                        + "　把 content_auto_merge 打开（默认就是开的）并重载插件即可自动补上。"
                        + hint
                    )

            # 价格/倍率是否还是旧值（钓点与鱼竿）
            default_locs = {
                loc["id"]: loc for loc in _parse_location_defs(DEFAULTS["location_defs"])
            }
            drift = [
                loc["name"]
                for loc in self.locations
                if loc["id"] in default_locs
                and (
                    int(loc["gold_gate"]) != int(default_locs[loc["id"]]["gold_gate"])
                    or abs(
                        float(loc["value_mult"])
                        - float(default_locs[loc["id"]]["value_mult"])
                    )
                    > 1e-6
                )
            ]
            default_rods = {
                rod["id"]: rod for rod in _parse_rod_defs(DEFAULTS["rod_defs"])
            }
            rod_drift = [
                rod["name"]
                for rod in self.rods
                if rod["id"] in default_rods
                and int(rod["price"]) != int(default_rods[rod["id"]]["price"])
            ]
            if drift or rod_drift:
                logger.warning(
                    f"检测到旧版数值：{len(drift)} 个钓点、{len(rod_drift)} 档鱼竿"
                    f"的价格/倍率与新版默认值不同"
                    f"（{'、'.join((drift + rod_drift)[:4])}…）——"
                    f"当前收益不是重新标定过的那一套。"
                f"（新内容——比如新钓点/新鱼饵——已经自动补上了；"
                f"要把价格也换成新版，请点「重置配置」）" + hint
                )
            fee = _safe_int(self.cfg.get("fish_cost"), 0, 0)
            if fee == 8:
                logger.warning(
                    "当前钓费是 8 金币/竿（旧版默认值）：新版默认**免费下竿**"
                    "（空钩不花钱，花钱只在买鱼饵），重置配置即可恢复。" + hint
                )

            # 内容表体检：官方默认里有、配置里却没有的条目（「加了新道具却看不到」）
            self._warn_missing_content_rows()
            # schema 与 DEFAULTS 的内容表是否一致（「只改了一处」时提前喊出来）
            self._warn_content_schema_drift()
        except Exception as e:  # pragma: no cover
            logger.debug(f"旧配置体检失败：{e}")

    #: 会被「缺条目体检」覆盖的内容表（都是 list 形态、每行 `id|…`）
    _CONTENT_CHECK_KEYS: tuple[str, ...] = (
        "item_defs", "bait_defs", "rod_defs", "title_defs",
    )

    def _warn_missing_content_rows(self) -> None:
        """点名「官方默认里有、但配置里没有」的内容条目。

        「加了新道具 / 新鱼饵，游戏里却看不到」最常见的两条根因：① 只改了
        ``_conf_schema.json`` 的 default、没同步改代码里的 ``DEFAULTS``（自动补内容
        只认 ``DEFAULTS``，于是新行永远进不了已有配置）；② ``content_auto_merge``
        被关了 / 补写盘失败。这里主动把「官方有、配置没有」的条目列出来。

        只在配置**基本就是官方表**（官方 id 占比过半）时才提醒 —— 站长从头手写的
        自定义表一个字都不动（同 `_merge_text_content_rows` 的判据）。
        """
        for key in self._CONTENT_CHECK_KEYS:
            try:
                default = DEFAULTS.get(key)
                current = self.config.get(key)
                if not isinstance(default, list) or not default:
                    continue
                if not isinstance(current, list) or not current:
                    continue
                name_of: dict[str, str] = {}
                for row in default:
                    parts = str(row).split("|")
                    rid = parts[0].strip()
                    if rid:
                        name_of[rid] = parts[1].strip() if len(parts) > 1 else rid
                have = {str(x).split("|", 1)[0].strip() for x in current}
                missing = [rid for rid in name_of if rid not in have]
                if not missing:
                    continue
                official_hit = sum(1 for rid in have if rid in name_of)
                if official_hit * 2 < len(have):
                    continue        # 手工小表，别多嘴
                names = "、".join(name_of.get(rid, rid) for rid in missing[:6])
                logger.warning(
                    f"检测到配置里少了 {len(missing)} 条官方内容（{key}：{names}…）"
                    f"——多半是改了 _conf_schema.json 却没同步改代码里的 DEFAULTS，"
                    f"或 content_auto_merge 被关掉了；把内容补进 DEFAULTS 并重载插件"
                    f"即可自动补上"
                )
            except Exception as e:  # pragma: no cover
                logger.debug(f"内容表缺条目体检失败（{key}）：{e}")

    def _warn_content_schema_drift(self) -> None:
        """比对 ``_conf_schema.json`` 与代码 ``DEFAULTS`` 的内容表，不一致就提醒。

        运行时配置由 schema 生成，而「升级补内容」只认 DEFAULTS —— 两边内容表不一致
        时，新装的服务器和老站长的服务器会看到不同的内容表。加内容只改了一处时，
        这里在启动日志里直接点名（比等玩家来报「道具没了」早得多）。
        """
        try:
            path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "_conf_schema.json"
            )
            with open(path, "r", encoding="utf-8-sig") as fh:
                schema = json.load(fh)
        except Exception as e:  # pragma: no cover - schema 缺失/损坏不该拦住启动
            logger.debug(f"读取 _conf_schema.json 自检失败（已忽略）：{e}")
            return

        def ids_of(value: Any) -> set[str]:
            out: set[str] = set()
            if isinstance(value, list):
                for row in value:
                    head = str(row).split("|", 1)[0].strip()
                    if head:
                        out.add(head)
            elif isinstance(value, str):
                for line in value.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "|" in line:
                        out.add(line.split("|", 1)[0].strip())
            return out

        for key in (
            "item_defs", "bait_defs", "rod_defs", "location_defs", "title_defs",
            "fish_defs", "collectible_defs", "variant_defs", "weather_defs",
            "easter_egg_defs", "button_defs",
        ):
            try:
                entry = schema.get(key)
                if not isinstance(entry, dict):
                    continue
                schema_ids = ids_of(entry.get("default"))
                default_ids = ids_of(DEFAULTS.get(key))
                if not schema_ids or not default_ids:
                    continue
                only_schema = schema_ids - default_ids
                only_default = default_ids - schema_ids
                if not (only_schema or only_default):
                    continue
                logger.warning(
                    f"⚠️ {key} 在 _conf_schema.json 与代码 DEFAULTS 里不一致："
                    f"只在 schema 里：{'、'.join(sorted(only_schema)[:4]) or '无'}；"
                    f"只在 DEFAULTS 里：{'、'.join(sorted(only_default)[:4]) or '无'}。"
                    f"新装服务器与老站长的内容表会不同 —— 加内容时请两处同步改。"
                )
            except Exception as e:  # pragma: no cover
                logger.debug(f"内容表一致性自检失败（{key}）：{e}")

    async def terminate(self) -> None:
        """停用/重载：清理内存状态并取消悬挂的互动等待。"""
        try:
            task = getattr(self, "_backup_task", None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._backup_task = None
            # 编辑器页面轮询任务（同样要停干净，否则重载会叠任务）
            await self.stop_editor_bridge()
            for pending in list(self._pending_pulls.values()):
                future = pending.get("future")
                if future is not None and not future.done():
                    future.cancel()
            self._pending_pulls.clear()
            self._player_locks.clear()
            logger.info("群钓鱼插件已卸载，内存已清理。")
        except Exception as e:  # pragma: no cover
            logger.error(f"钓鱼插件卸载清理失败：{e}")


#: 互动玩法里等价于「拉」的输入
PULL_WORDS = {"拉", "拉线", "收", "收线", "提", "提竿", "拽", "pull", "p"}

#: 直接写「下竿」类同义词也只当抛竿，避免玩家凭直觉打字却被告知「不认识」
CAST_WORDS = {
    "钓", "钓鱼", "下竿", "抛竿", "甩竿", "下钩", "抛", "钓鱼吧",
    "cast", "fish", "fishing",
}

#: 子命令关键词（用于「少打空格」容错：`卖1` -> `卖 1`）。
#: 拆的条件（见 `_peel_subcommand`）：残留部分**看起来像参数**（数字/区间/全/所/空开头）
#: 才拆；否则只有 `NAME_ARG_WORDS` 里「参数本来就是名字」的子命令才允许拆。
#: 所以 `查鱼` 不会变成「查 + 鱼」，但 `卖鲤鱼3` 会（卖在 NAME_ARG_WORDS 里）。
SUBCOMMAND_WORDS = {
    "帮助", "菜单", "指令", "背包", "鱼篓", "卖", "一键卖出",
    "图鉴", "收集", "水族馆", "锁定", "解锁", "今日", "排行", "排行榜",
    "商店", "鱼饵", "鱼饵店", "道具", "道具店", "用", "使用", "档案", "体力", "签到",
    "订单", "任务", "钓点", "地点", "地图", "鱼竿", "杂物", "漂流瓶",
    "查", "查询",
    "扩容", "扩建背包", "背包扩容", "鱼篓扩容", "去", "前往",
    # v1.18.31：v1.18.0 那批「一步到位」短写法也要能「少打空格」。
    # 以前只有两级写法认（`水族馆放1`），一级的 `/钓鱼 放1` 会被当成未知子命令 ——
    # 而文档的规则是「残留部分看起来像参数就拆」，`1` 明显像，同类 `卖1` 也认。
    "放", "放入", "养", "取", "取出", "拿", "领", "收租", "收益",
    "喂", "投喂", "洗", "洗髓", "交", "交单", "交货",
    "装备", "换竿", "换鱼竿",
}

#: 各子命令内部的「动作词」，用于再拆一层的少空格容错（`水族馆 放1`）。
#: 长的写法排在前面也不影响——`_peel_action()` 内部按长度倒序匹配。
#: 参数是「名字」的的子命令：残留部分不是数字也允许拆（`卖鲤鱼3`、`去湖泊`）
NAME_ARG_WORDS = {
    "商店", "鱼饵", "鱼饵店", "道具", "道具店", "用", "使用", "水族馆", "馆", "缸",
    "鱼竿", "竿",
    "钓点", "地点", "地图", "去", "前往", "卖", "图鉴", "订单", "任务",
    "卖垃圾", "一键卖出",
    # 短写法里参数是「名字」的那几个（`喂高级饲料1`、`装备竹竿`）
    "喂", "投喂", "装备", "换竿", "换鱼竿",
}
AQUARIUM_ACTIONS = (
    "扩建", "领取", "收益", "投喂", "放入", "取出", "卖出",
    "升级", "放", "取", "卖", "喂", "领",
)
#: 三家店都只认「买」；「扩容」不再挂在商店下面（老写法由 shop.moved 指路）
SHOP_ACTIONS = ("购买", "买")
ROD_ACTIONS = ("购买", "装备", "买", "用", "换")
LOCATION_ACTIONS = ("解锁", "前往", "去", "开")
ORDER_ACTIONS = ("提交", "交")


# =============================================================================
# 模块化收尾：把本模块的全局注入拆出去的 mixin 模块
# =============================================================================

#: 所有拆出去的兄弟模块（新增一个就加进来）
SIBLING_MODULES: tuple[Any, ...] = tuple(
    module
    for module in (
        CALC, DATA_ADMIN, VIEWS, COMMANDS, INTERACTIONS, ENGINE, EDITOR_BRIDGE,
        # 存档模块也接进注入链：它内部的告警/调试日志要靠注入进来的 logger
        # 才能进 AstrBot 日志（拿不到就静默降级，不影响存档功能）
        BACKUP_MODULE,
        # 旧作用域找回：要用注入进来的 logger 报「打不开库」这类情况
        LEGACY,
        # 大鱼乐（v1.18.51）：解析奖表、期望值模型都要用注入进来的鱼池/道具/品质常量
        LOTTERY,
        # 玩家数据编辑器（v1.18.56）：字段清单里的枚举要用鱼池/鱼饵/道具/称号/变异表，
        # 逐条改鱼还要用 _new_instance / _compute_value —— 全都靠注入
        EDITOR_PLAYER,
    )
    if module is not None
)


def _expose_globals(module: Any) -> None:
    """把本模块的全局（常量、工具函数、其它模块引用）注入兄弟模块。

    必须在**模块末尾**调用：此刻所有常量与函数都已定义。
    """
    skip = {
        "__name__",
        "__file__",
        "__builtins__",
        "__loader__",
        "__spec__",
        "__package__",
        "__doc__",
    }
    for key, value in list(globals().items()):
        if key not in skip:
            setattr(module, key, value)


def _expose_globals_all() -> None:
    """刷新所有兄弟模块的全局视图（数值被重新赋值后调用）。"""
    for module in SIBLING_MODULES:
        _expose_globals(module)


_expose_globals_all()
