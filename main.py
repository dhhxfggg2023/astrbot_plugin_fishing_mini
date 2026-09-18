"""AstrBot QQ 群钓鱼小游戏插件（深度养成版）。

所有功能都挂在 ``/钓鱼`` 这一条指令下，避免与其他插件撞名：

- ``/钓鱼``              下竿
- ``/钓鱼 拉``           拉线（只有最高两档鱼种需要，见「互动玩法」）
- ``/钓鱼 背包``         背包
- ``/钓鱼 卖 …``         卖鱼
- ``/钓鱼 图鉴``         收集进度
- ``/钓鱼 水族馆 …``     水族馆 / 投喂 / 领取收益 / 扩建
- ``/钓鱼 商店 …``       鱼饵与道具
- ``/钓鱼 金币 / 签到``  档案与签到
- ``/钓鱼 帮助``         简洁说明

================================ 数值设计 ================================

【两套独立的「品质」概念，别混淆】

1. **鱼种品质（固有属性，不可改变）**
   常见 < 少见 < 稀有 < 传说 < 神话，共 5 档。
   钓到的那一刻就定了，任何道具都改不了，决定基础价值区间与稀有度。

2. **个体品质（可提升）**
   ⚪普通 🟢优良 🔵稀有 🟣极品 🌟传说，共 5 档，
   由「品质加成倍率 quality_mult」决定，影响售价倍率。
   投喂饲料不会改变它（饲料只加个体数值），
   但「洗髓丹」可以提高掷出好个体的概率——所以个体是可以养出来的。

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


class _MissingMixin:
    """兄弟模块加载失败时的占位基类（保证插件仍能启动）。"""



# =============================================================================
# 一、内置默认值
# =============================================================================
#
# 这些是「配置缺省时的兜底」。运行时会优先读 _conf_schema.json 生成的配置，
# 所以想改数值请优先去 WebUI 插件配置页改，而不是改这里的常量。

DEFAULTS: dict[str, Any] = {
    # 默认值指纹（插件回写，只读参考）：代码里的数值一变，这个指纹就变
    "config_fingerprint": "",
    # 数值同步档位：auto = 同步数值与内容 / all = 连开关一起重置 / off = 不同步
    "defaults_sync_mode": "auto",
    "initial_gold": 100,
    "fish_cost": 0,
    "cooldown_seconds": 45,
    "sign_reward": 30,
    "sell_discount": 1.0,
    "enable_group_broadcast": True,
    "backpack_base": 30,
    "backpack_upgrades": ["15|400", "25|1100", "30|2700"],
    "aquarium_capacity": 8,
    "aquarium_bonus": 1.2,
    "interactive_rarities": "传说,神话",
    "window_min": 4,
    "window_max": 8,
    "sweet_spot_width": 0.34,
    "perfect_bonus": 0.30,
    "good_bonus": 0.10,
    "perfect_escape_factor": 0.3,
    "edge_escape_factor": 1.6,
    "rarity_escape_chance": "传说:0.30,神话:0.42",
    "feed_max_uses": 10,
    "quality_weights": [44, 28, 16, 9, 3],
    "rarity_display_names": ["常见", "少见", "稀有", "传说", "神话"],
    "item_drop_chance": 0.14,
    "bottle_note_chance": 0.30,
    "order_count": 3,
    "order_reward_mult": 2.2,
    "order_unlock_level": 3,
    "order_refresh_min_hours": 3,
    "order_refresh_max_hours": 6,
    # 鱼竿：id|名称|emoji|价格|价值加成|幸运加成|解锁等级|描述（解锁等级 = 能买的等级）
    "rod_defs": [
        "bamboo|竹竿|🎋|0|0.00|0.00|1|村口杂货铺送的，能用",
        "carbon|碳素竿|🎣|400|0.05|0.03|4|轻巧顺手，新手进阶首选",
        "stream|溪流竿|🪝|1600|0.09|0.05|9|韧性好，适合溪流与湖泊",
        "dragon|龙纹竿|🐉|5400|0.17|0.11|16|竿身刻龙，专治大鱼",
        "starlight|星辉竿|✨|11000|0.23|0.16|26|夜里会泛微光，深海也用得上",
        "mythic|神话竿|🌈|22000|0.30|0.22|38|传说钓具，据说能引来神话之鱼"
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
    # ---- 数据管理（都在 WebUI 里操作，不用发指令）----
    "level_xp_base": 5.0,           # 升级曲线：每级基础竿数
    "level_xp_ratio": 1.08,         # 升级曲线：等比底数（指数项，越大越陡）
    "level_xp_growth": 0.0,         # 升级曲线：二次增长系数（默认 0，留着做微调）
    # 上钩率：空钩基本靠运气，带饵才容易上鱼（"id:概率" 逗号分隔，站长可调）
    # 钓点难度系数：≥1.0 = 这个钓点必出鱼；<1.0 = 上钩率 × 系数（越深越容易空竿）
    "location_hook_factors": (
        "novice:1.0,bamboo:1.0,canal:1.0,lake:0.94,reed:0.90,sea:0.86,dock:0.82,"
        "night:0.78,mangrove:0.74,swamp:0.70,cave:0.66,ruins:0.62,abyss:0.58,"
        "trench:0.54,glacier:0.50,aurora:0.44"
    ),
    # 前往下一个钓点需要上一个钓点图鉴开到多少比例
    "location_codex_gate": 0.8,
    "bait_hook_rates": "none:0.30,bread:0.72,worm:0.80,bloodworm:0.86,corn:0.90,shrimp:0.94,livebait:0.96,secret:1.0",
    "content_auto_merge": True,     # 旧配置自动合并新版内容（钓点/鱼饵/鱼竿/道具）
    "button_mode": "自动",          # QQ 官方按钮发送形态：自动/markdown/text/关闭
    "data_status": "",              # 插件回写的状态面板（人看）
    "data_action": "无",            # 要执行的数据操作（执行后自动复位）
    "data_target": "",              # 目标玩家ID / 快照文件名
    "data_confirm": False,          # 危险操作二次确认
    "backup_import_file": [],       # 上传存档（导入用，type=file）
    "backup_export_file": [],       # 导出存档（下载用，type=file）
    "enable_auto_backup": True,
    "backup_daily_hour": 4,
    "backup_interval_hours": 6,
    "backup_keep_daily": 30,
    "backup_keep_interval": 20,
    "backup_dir": "",
    "codex_bonus_per_rarity": [0.03, 0.05, 0.08, 0.12, 0.2],
    "pond_income_per_hour": 0.015,
    "pond_income_cap_hours": 12,
    "pond_income_cap_coins": 3000,
    "aquarium_slots": ["精致缸|1600", "生态缸|5400", "深海缸|16000"],
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
    ],
    "item_defs": [
        "feed_basic|普通饲料|🌾|20|打基础的口粮|meat=2;spirit=1",
        "feed_premium|高级饲料|🍖|80|营养均衡，长得快|meat=5;spirit=4;sheen=3",
        "feed_divine|仙露|💧|300|传说中的养鱼圣品|meat=10;spirit=10;sheen=10;value_up=150",
        "pill_quality|洗髓丹|🔮|500|激发血脉，更容易出好个体|quality_up=0.30",
        "coral_deco|珊瑚造景|🪸|260|水族馆装饰，提升馆藏价值|value_up=120",
    ],
    # ---- 可调数值表：想改物价 / 爆率 / 属性范围，改这里（或 WebUI）即可 ----
    # 鱼种品质：出现权重（越大越常见）、价值倍数、拉线难度、三维范围
    "rarity_spawn_weights": "常见:14,少见:4.6,稀有:0.95,传说:0.2,神话:0.04",
    "rarity_value_factors": "常见:1.0,少见:2.6,稀有:6.0,传说:15.0,神话:34.0",
    "rarity_difficulty": "常见:0,少见:0,稀有:0.22,传说:0.5,神话:0.82",
    "rarity_attr_ranges": "常见:40-78,少见:48-85,稀有:55-92,传说:62-97,神话:70-100",
    # 16 个钓点各自的「常见」鱼基准价（按钓点顺序）
    "tier_base_values": "5,6,7,10,12,14,18,23,26,32,41,49,60,73,90,113",
    # 同一条鱼每次上钩的个体差异区间
    "value_variance": "0.92-1.12",
    # 三维属性：售价权重、展示名、及格线
    "attr_weights": "meat:0.45,spirit:0.3,sheen:0.25",
    "attr_labels": "meat:肉质,spirit:灵性,sheen:光泽",
    "attr_par": 60.0,
    # 个体品质档位（名称:下限-上限:emoji，从低到高）
    "quality_tiers": "普通:0.8-1.0:⚪,优良:1.0-1.35:🟢,稀有:1.35-1.8:💎,极品:1.8-2.5:🏆,传说:2.5-4.0:👑",
    # 上钩率解析失败时的兜底值、未列出品质的默认逃脱率
    "hook_rate_fallback": 0.30,
    "default_escape_rate": 0.25,
    # 水族馆隐藏的「不好惹」判定关键词
    "hostile_keywords": "鳄,鲨,蛇,鳗,鳝,乌贼,章鱼,食人,水虎,龙鱼,巨齿,利维坦,归墟,古龙,鲸,鮟鱇,电鳗,鳄雀",
    # 物价总开关：全局倍率 + 单条覆盖（鱼名或 id 均可）
    "fish_value_mult": 1.0,
    "fish_value_overrides": "",
}

# -----------------------------------------------------------------------------
# 默认值自动同步：改了代码里的数值，站长不用再手点「重置配置」
# -----------------------------------------------------------------------------

#: 这些前缀的配置项属于「站长的个人设置 / 管理操作」，永不被自动同步覆盖
DEFAULTS_SYNC_EXCLUDE_PREFIXES: tuple[str, ...] = ("data_", "backup_")
#: 这些键同上（开关类与管理项，跟着指纹一起变但没有意义）
DEFAULTS_SYNC_EXCLUDE_KEYS: frozenset[str] = frozenset(
    {"button_mode", "content_auto_merge", "defaults_sync_mode", "config_fingerprint"}
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
    """把参与同步的默认值做稳定序列化后取哈希前 8 位。

    只要代码里任何一个数值/内容表变了，指纹就会变，插件启动时据此
    把新默认值写回配置——不需要人工维护版本号。

    ⚠️ 必须**调用时现算**：模块初始化后半段会重写 ``DEFAULTS["location_defs"]``
    （由钓点常量生成），提前算出来的指纹会和实际默认值对不上，导致每次启动
    都误判成「数值变了」。
    """
    payload = {key: DEFAULTS[key] for key in _synced_default_keys()}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

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
)

#: 各档「常见」鱼的基准价（每档约 ×1.22，是整条价值曲线的主干）
TIER_BASE_VALUE: tuple[float, ...] = (
    5, 6, 7, 10, 12, 14, 18, 23, 26, 32, 41, 49, 60, 73, 90, 113,
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
    FISH_LOCATION_POOLS[_entry["id"]] = (_entry["home"],)

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
        "hostile_keywords": _parse_word_list(
            str(DEFAULTS.get("hostile_keywords") or ""), (), "内置默认"
        ),
    }


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
    _pool[_fish["id"]] = round(
        ROSTER_RARITY_WEIGHT[_fish["rarity"]] * 0.7, 3
    )

# 隐藏生物：每个钓点都塞一份（出现率极低）
for _loc_id in list(LOCATION_WEIGHTS):
    for _fid in HIDDEN_EVERYWHERE:
        LOCATION_WEIGHTS[_loc_id].setdefault(_fid, HIDDEN_WEIGHT)


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

RODS: list[dict[str, Any]] = [
    {"id": "bamboo", "name": "竹竿", "emoji": "🎋", "price": 0,
     "value_bonus": 0.00, "luck_bonus": 0.00, "desc": "村口杂货铺送的，能用"},
    {"id": "carbon", "name": "碳素竿", "emoji": "🎣", "price": 300,
     "value_bonus": 0.08, "luck_bonus": 0.03, "desc": "轻巧顺手，新手进阶首选"},
    {"id": "stream", "name": "溪流竿", "emoji": "🪝", "price": 1200,
     "value_bonus": 0.15, "luck_bonus": 0.05, "desc": "韧性好，适合溪流与湖泊"},
    {"id": "dragon", "name": "龙纹竿", "emoji": "🐉", "price": 4000,
     "value_bonus": 0.25, "luck_bonus": 0.10, "desc": "竿身刻龙，专治大鱼"},
    {"id": "starlight", "name": "星辉竿", "emoji": "✨", "price": 10000,
     "value_bonus": 0.32, "luck_bonus": 0.14, "desc": "夜里会泛微光，深海也用得上"},
    {"id": "mythic", "name": "神话竿", "emoji": "🌈", "price": 22000,
     "value_bonus": 0.40, "luck_bonus": 0.18, "desc": "传说钓具，据说能引来神话之鱼"},
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
    "MILESTONES": {},
    "ACHIEVEMENTS": {},
}

_CONTENT: dict[str, Any] = {**_CONTENT_FALLBACK, **_load_data_module()}

COLLECTIBLES: list[dict[str, Any]] = _CONTENT["COLLECTIBLES"]
BOTTLE_NOTES: list[str] = _CONTENT["BOTTLE_NOTES"]
VARIANTS: list[dict[str, Any]] = _CONTENT["VARIANTS"]
WEATHERS: list[dict[str, Any]] = _CONTENT["WEATHERS"]
EASTER_EGGS: list[dict[str, Any]] = _CONTENT["EASTER_EGGS"]
RANDOM_EVENTS: list[dict[str, Any]] = _CONTENT["RANDOM_EVENTS"]
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

EVENT_BY_ID: dict[str, dict[str, Any]] = {e["id"]: e for e in RANDOM_EVENTS}


#: 累计钓获达到这些数量时给一句里程碑文案（只提示一次）







# =============================================================================
# 四、个体品质（5 档，可提升）
# =============================================================================
#
# 名称, 加成下限, 加成上限, emoji

QUALITY_TIERS: list[tuple[str, float, float, str]] = [
    ("普通", 0.80, 1.00, "⚪"),
    ("优良", 1.00, 1.35, "🟢"),
    ("稀有", 1.35, 1.80, "🔵"),
    ("极品", 1.80, 2.50, "🟣"),
    ("传说", 2.50, 4.00, "🌟"),
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
                yield event.plain_result(
                    "😵 操作没有成功（已记录到日志）\n"
                    "　可以再试一次；如果一直失败，请把这条消息发给管理员"
                )
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




























def _apply_tunable_config(cfg: dict[str, Any]) -> None:
    """把「可调数值表」配置写回模块级常量，并重建依赖它们的派生表。"""
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

    HOSTILE_KEYWORDS = _parse_word_list(
        _cfg_str(cfg, "hostile_keywords"), BUILTIN["hostile_keywords"],
        "hostile_keywords"
    )

    # 派生表：鱼池权重依赖稀有度权重/价值因子/属性范围，必须重建
    LOCATION_WEIGHTS = _rebuild_location_weights()











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
    weights: list[float], bait_luck: float = 0.0, extra_luck: float = 0.0
) -> float:
    """掷个体品质倍率。幸运值把分布往高品质推。

    weights 支持通过插件配置调整（quality_weights）。
    """
    if not weights or sum(weights) <= 0:
        weights = list(DEFAULTS["quality_weights"])
    total = sum(weights)
    luck = _clamp(float(bait_luck) + float(extra_luck), 0.0, 1.0)
    point = random.random() * total + luck * total * 0.9
    idx = len(weights) - 1
    cumulative = 0.0
    for i, weight in enumerate(weights):
        cumulative += weight
        if point < cumulative:
            idx = i
            break
    if idx >= len(QUALITY_TIERS):
        idx = len(QUALITY_TIERS) - 1
    _, low, high, _ = QUALITY_TIERS[idx]
    return random.uniform(low, high)










# =============================================================================
# 九、展示辅助
# =============================================================================


def _quality_tag(instance: dict[str, Any]) -> str:
    """个体品质标签，例如 ``🟣极品``。"""
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
        self._player_locks: dict[str, asyncio.Lock] = {}
        self._pending_pulls: dict[str, dict[str, Any]] = {}
        #: 记录最近见到的平台名（只在启动日志里提示按钮可用性）
        self._recent_platforms: dict[str, str] = {}
        #: 会话 -> (插曲归属玩家, 时间)：用来挡住「抢别人的插曲」
        self._recent_events: dict[str, tuple[str, float]] = {}
        self._button_warned = False
        self._button_ok_logged = False
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
        cfg["cooldown_seconds"] = max(0, _safe_int(cfg["cooldown_seconds"], 60, 0))
        cfg["sign_reward"] = max(0, _safe_int(cfg["sign_reward"], 20, 0))
        cfg["sell_discount"] = _clamp(_safe_number(cfg["sell_discount"], 1.0), 0.0, 5.0)
        cfg["enable_group_broadcast"] = bool(cfg["enable_group_broadcast"])
        cfg["aquarium_capacity"] = int(
            _clamp(_safe_int(cfg["aquarium_capacity"], 12, 1), 1, 64)
        )
        cfg["aquarium_bonus"] = _clamp(_safe_number(cfg["aquarium_bonus"], 1.2), 1.0, 5.0)
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
            _safe_number(cfg["order_reward_mult"], 2.2), 1.0, 10.0
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
        cfg["easter_egg_chance"] = _clamp(
            _safe_number(cfg["easter_egg_chance"], 0.05), 0.0, 1.0
        )
        cfg["pond_income_per_hour"] = _clamp(
            _safe_number(cfg["pond_income_per_hour"], 0.015), 0.0, 1.0
        )
        cfg["pond_income_cap_hours"] = int(
            _clamp(_safe_int(cfg["pond_income_cap_hours"], 12, 1), 1, 168)
        )
        cfg["pond_income_cap_coins"] = max(
            0, _safe_int(cfg["pond_income_cap_coins"], 3000, 0)
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
        self.baits = _parse_bait_defs(cfg.get("bait_defs"))
        self.items = _parse_item_defs(cfg.get("item_defs"))
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
        self.rods = _parse_rod_defs(cfg.get("rod_defs"))
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

    def _item_list(self) -> list[str]:
        """道具 id 列表（按单价升序，界面上先看到便宜的）。"""
        return sorted(
            self.items,
            key=lambda iid: (_safe_int(self.items[iid].get("price"), 0, 0), iid),
        )

    def _bait_list(self) -> list[str]:
        """除空钩外可购买的鱼饵 id 列表。"""
        return [bid for bid in self.baits if bid != "none"]


    def _aquarium_capacity(self, player: dict[str, Any]) -> int:
        """水族馆总容量 = 基础容量 + 已解锁扩建栏位数。"""
        unlocked = player.get("aquarium_slots") or []
        if not isinstance(unlocked, list):
            unlocked = []
        count = sum(1 for slot in self.aquarium_slots if slot["name"] in unlocked)
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
        return lock

    async def _load_player(self, user_id: str) -> dict[str, Any]:
        """读取并修复玩家数据（含旧版本自动迁移、信封拆包）。"""
        try:
            raw = await self.get_kv_data(self._kv_key(user_id), None)
        except Exception as e:
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
        if migrated:
            logger.info(f"玩家 {user_id} 数据已迁移到 v{DATA_VERSION}")
            await self._save_player(player)
        return player

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
        """把玩家 ID 记进索引（KV 存不了「列出全部 key」，所以自己记一份）。"""
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
        """已知的全部玩家 ID。"""
        try:
            index = await self.get_kv_data("player_index", None)
            if isinstance(index, str):
                index = json.loads(index)
            if isinstance(index, list):
                return [str(x) for x in index if str(x).strip()]
        except Exception as e:
            logger.debug(f"读取玩家索引失败：{e}")
        return []

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
        # ---- 个体品质 ----
        if quality == "极品":
            unlock("perfect_one")
        if quality == "传说":
            unlock("mythic_one")
        top_quality = sum(
            1
            for x in inventory + aquarium
            if QUALITY_RANK.get(x.get("quality", ""), 0) >= QUALITY_RANK["极品"]
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
        if len(rod_ids) >= len(self.rods):
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
    ) -> dict[str, Any]:
        """在指定钓点按权重抽鱼种。

        鱼饵改变各稀有度的命中权重；天气在此基础上再叠一层加成。
        """
        bait = self.baits.get(bait_id) or self.baits.get("none") or {}
        mults: dict[str, float] = bait.get("rarity_mult") or {}
        weather_mults: dict[str, float] = (weather or {}).get("rarity_mult") or {}

        weighted: list[tuple[dict[str, Any], float]] = []
        for fish, weight in _location_pool(location_id):
            mult = _safe_number(mults.get(fish["rarity"]), 1.0)
            mult *= _safe_number(weather_mults.get(fish["rarity"]), 1.0)
            weighted.append((fish, float(weight) * max(0.0, mult)))

        total = sum(w for _, w in weighted)
        if total <= 0:
            pool = _location_pool(location_id)
            return pool[0][0] if pool else FISH_POOL[0]
        point = random.uniform(0, total)
        cumulative = 0.0
        for fish, weight in weighted:
            cumulative += weight
            if point < cumulative:
                return fish
        return weighted[-1][0]

    def _rod(self, player: dict[str, Any]) -> dict[str, Any]:
        """玩家当前装备的鱼竿配置。"""
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
                lines.append(
                    f"　🔮 下一竿手气：{_luck_stars(gain, 0.3)}"
                    f"（累计 {player['luck_charges']:.0%}）"
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

    def _claim_aquarium_bonus(self, instance: dict[str, Any]) -> int:
        """结算「水族馆展出加成」，**每条鱼一生只能领一次**。

        ⚠️ 这里修的是一个真实漏洞：早期版本每次「取出」都按 base_value 加一次
        加成、且直接累加到 live_bonus，于是「取出 -> 再放入 -> 再取出」可以
        无限叠加价格。现在用 ``pond_claimed`` 标记锁定，只能领一次。
        返回本次实际获得的加成金币（已领过则为 0）。
        """
        if instance.get("pond_claimed"):
            return 0
        base = _safe_int(instance.get("base_value"), _instance_value(instance), 1)
        bonus = float(self.cfg["aquarium_bonus"])
        gain = max(0, int(base * (bonus - 1.0)))
        instance["pond_claimed"] = True
        instance["live_bonus"] = _safe_int(instance.get("live_bonus"), 0, 0) + gain
        instance["value"] = base + instance["live_bonus"]
        return gain

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
        yield event.plain_result("\n".join(lines))



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

        **空格容错**：AstrBot 按空格依次填充形参，这里声明到 a6 并把
        a2~a6 统一交给各子命令用 `_tokens()` 解析，因此
        `/钓鱼 卖 1 2 3`、`/钓鱼 卖  1   2`、`/钓鱼 卖 1 2 3 4 5 6`
        都能正确工作。
        """
        user_id = str(event.get_sender_id())
        a1 = (a1 or "").strip()
        # ---- 少打空格的容错：/钓鱼 卖1、/钓鱼 帮助2 ----
        # 把粘连的写法拆成「子命令 + 参数」，并整体后移写回 a2~a6，
        # 这样各子命令拿到的参数与正常写法完全一致。
        # （`/钓鱼 卖1 2` 会被拆成 a1="卖"、a2="1"、a3="2"）
        peeled = self._peel_subcommand(a1)
        if peeled:
            a1, glued = peeled
            a2, a3, a4, a5, a6 = glued, a2 or "", a3 or "", a4 or "", a5 or ""
        rest = [a2 or "", a3 or "", a4 or "", a5 or "", a6 or ""]
        key = a1.lower()
        # after_sub：子命令之后的「全部」参数（a2~a6），给「卖 / 水族馆 / 锁定」这类
        after_sub = " ".join(x for x in rest if x.strip())
        # after_first：再往后一个参数（a3~a6），给「用 / 商店 / 订单」这类
        # 已经把 a2 当作第一个参数单独传下去的子命令，避免重复。
        after_first = " ".join(x for x in rest[1:] if x.strip())

        # ---- 拉线（互动）----
        if key in PULL_WORDS:
            if self._resolve_pull(event):
                yield event.plain_result("✅ 收到，正在收线…")
                return
            yield event.plain_result(
                "🤔 现在没有鱼咬钩。直接发 /钓鱼 下竿，"
                "等提示「咬钩了」再发 /钓鱼 拉"
            )
            return

        # ---- 帮助（分页：/钓鱼 帮助 2）----
        if key in ("帮助", "help", "?", "？", "菜单", "指令"):
            help_text = self._help_text(_to_int(after_sub, 1))
            first = True
            async for reply in self._say(event, help_text, self._cast_rows()):
                yield self._with_at(event, reply) if first else reply
                first = False
            return

        # ---- 无参数：下竿 ----
        if not a1:
            first = True
            async for result in self._do_cast(event, user_id, ""):
                yield self._with_at(event, result) if first else result
                first = False
            return
        # ---- 显式写「下竿」类同义词也算抛竿（空格/用词容错）----
        if key in CAST_WORDS:
            async for result in self._do_cast(event, user_id, ""):
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
            "卖", "卖鱼", "sell", "卖垃圾", "清理", "一键卖出", "junk",
            "卖光光", "卖光", "清空", "全卖", "空背包", "sellall",
        ):
            # 「卖光光」= 清空背包（锁定的留着）；旧词「卖垃圾/清理」不再有单独玩法
            if key in ("卖光光", "卖光", "清空", "全卖", "空背包", "sellall") and not tokens:
                tokens = ["光光"]
            elif key in ("卖垃圾", "清理", "一键卖出", "junk") and not tokens:
                tokens = ["__junk_removed__"]
            handler = self._cmd_sell(event, user_id, *tokens)
        elif key in ("图鉴", "收集", "collection"):
            handler = self._cmd_collection(event, user_id, after_sub)
        elif key in ("水族馆", "馆", "aquarium", "缸"):
            handler = self._cmd_aquarium(event, user_id, a2, after_first)
        # 背包扩容（排在「商店」之前，否则「商店 扩容」会被商店吞掉）
        elif key in ("扩建背包", "扩容", "背包扩容", "鱼篓扩容", "鱼篓"):
            handler = self._cmd_backpack_upgrade(event, user_id)
        elif key in ("锁定", "锁", "lock"):
            handler = self._cmd_lock(event, user_id, *tokens)
        elif key in ("解锁", "解", "unlock"):
            handler = self._cmd_unlock(event, user_id, *tokens)
        elif key in ("今日", "天气", "行情", "today", "weather", "market"):
            handler = self._cmd_today(event, user_id)
        elif key in ("排行", "排行榜", "rank", "top", "榜"):
            handler = self._cmd_leaderboard(event, user_id, after_sub)
        elif key in ("商店", "鱼饵", "道具", "shop", "买"):
            handler = self._cmd_shop(event, user_id, a2, after_first)
        elif key in ("用", "使用", "道具用", "use"):
            handler = self._cmd_use_item(event, user_id, a2, after_first)
        elif key in ("查", "查询", "鱼", "鱼查", "资料", "fish", "info", "lookup"):
            handler = self._cmd_fish_info(event, user_id, after_sub)
        elif key in ("事件", "插曲", "选择", "event"):
            handler = self._cmd_event(event, user_id, a2)
        elif key in ("换饵", "换鱼饵", "装备饵", "上饵", "bait", "equip_bait"):
            handler = self._cmd_equip_bait(event, user_id, a2, after_first)
        elif key in ("金币", "档案", "me", "gold"):
            handler = self._cmd_profile(event, user_id)
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

        if handler is None:
            yield event.plain_result(
                f"🤔 不认识「{a1}」这个用法\n"
                f"　发 /钓鱼 帮助 1 看全部指令（共 7 页）\n"
                f"　最常用：/钓鱼 下竿 ｜ /钓鱼 背包 ｜ /钓鱼 卖 ｜ /钓鱼 今日"
            )
            return
        first = True
        async for result in handler:
            if first:
                result = self._with_at(event, result)
                first = False
            yield result
    async def _sync_defaults(self) -> None:
        """把代码里的新默认数值同步进插件配置。

        站长痛点：每次插件升级改了数值，都得手点一次 WebUI 的「重置配置」，
        否则旧值一直生效。这里用「默认值指纹」解决：

        * 指纹相同 → 什么都不做（几乎零开销）
        * 指纹不同 → 按 ``defaults_sync_mode`` 决定同步范围：
          ``auto``（默认）只同步数值与内容，``all`` 连开关一起重置，
          ``off`` 只更新指纹、保留站长改过的所有值

        ``data_*`` / ``backup_*`` 这类管理设置**永远**不同步。
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
            if mode != "off":
                keys = list(DEFAULTS) if mode == "all" else _synced_default_keys()
                for key in keys:
                    if key in ("config_fingerprint", "defaults_sync_mode"):
                        continue
                    if self.config.get(key) != DEFAULTS[key]:
                        changed[key] = DEFAULTS[key]
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
        for key in self.CONTENT_LIST_KEYS:
            default = DEFAULTS.get(key)
            if not isinstance(default, list) or not default:
                continue
            current = self.config.get(key)
            if not isinstance(current, list) or not current:
                continue  # 空/损坏的交给配置兜底逻辑处理
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
        except Exception as e:  # pragma: no cover
            logger.debug(f"旧配置体检失败：{e}")

    async def terminate(self) -> None:
        """停用/重载：清理内存状态并取消悬挂的互动等待。"""
        try:
            task = getattr(self, "_backup_task", None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._backup_task = None
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
#: 只列「后面跟数字/序号」这类会自然粘连的子命令；参数是用户 ID 或
#: 名字的子命令不列，否则形如 `查鱼` 的输入会被误拆成「查 + 鱼」。
SUBCOMMAND_WORDS = {
    "帮助", "菜单", "指令", "背包", "鱼篓", "卖", "清理", "一键卖出",
    "图鉴", "收集", "水族馆", "锁定", "解锁", "今日", "排行", "排行榜",
    "商店", "鱼饵", "道具", "用", "使用", "金币", "档案", "签到",
    "订单", "任务", "钓点", "地点", "地图", "鱼竿", "杂物", "漂流瓶",
    "查", "查询",
    "扩容", "扩建背包", "背包扩容", "鱼篓扩容", "去", "前往",
}

#: 各子命令内部的「动作词」，用于再拆一层的少空格容错（`水族馆 放1`）。
#: 长的写法排在前面也不影响——`_peel_action()` 内部按长度倒序匹配。
#: 参数是「名字」的的子命令：残留部分不是数字也允许拆（`卖鲤鱼3`、`去湖泊`）
NAME_ARG_WORDS = {
    "商店", "鱼饵", "道具", "用", "使用", "水族馆", "馆", "缸", "鱼竿", "竿",
    "钓点", "地点", "地图", "去", "前往", "卖", "图鉴", "订单", "任务",
    "清理", "卖垃圾", "一键卖出",
}
AQUARIUM_ACTIONS = (
    "扩建", "领取", "收益", "投喂", "放入", "取出", "卖出",
    "升级", "放", "取", "卖", "喂", "领",
)
SHOP_ACTIONS = ("扩建背包", "背包扩容", "鱼篓扩容", "扩建", "扩容", "购买", "买")
ROD_ACTIONS = ("购买", "装备", "买", "用", "换")
LOCATION_ACTIONS = ("解锁", "前往", "去", "开")
ORDER_ACTIONS = ("提交", "交")


# =============================================================================
# 模块化收尾：把本模块的全局注入拆出去的 mixin 模块
# =============================================================================

#: 所有拆出去的兄弟模块（新增一个就加进来）
SIBLING_MODULES: tuple[Any, ...] = tuple(
    module
    for module in (CALC, DATA_ADMIN, VIEWS, COMMANDS, INTERACTIONS, ENGINE)
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
