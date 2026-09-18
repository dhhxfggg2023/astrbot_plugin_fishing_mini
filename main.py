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
    "rod_defs": [
        "bamboo|竹竿|🎋|0|0.00|0.00|村口杂货铺送的，能用",
        "carbon|碳素竿|🎣|400|0.05|0.03|轻巧顺手，新手进阶首选",
        "stream|溪流竿|🪝|1600|0.09|0.05|韧性好，适合溪流与湖泊",
        "dragon|龙纹竿|🐉|5400|0.17|0.11|竿身刻龙，专治大鱼",
        "starlight|星辉竿|✨|11000|0.23|0.16|夜里会泛微光，深海也用得上",
        "mythic|神话竿|🌈|22000|0.30|0.22|传说钓具，据说能引来神话之鱼"
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
    "level_xp_growth": 0.6,        # 升级曲线：二次增长系数（越后面越贵）
    # 上钩率：空钩基本靠运气，带饵才容易上鱼（"id:概率" 逗号分隔，站长可调）
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
    "bait_defs": [
        "none|空钩|🪝|0|0|0|1,1,1,1,1|什么也不挂，全凭本事（免费）",
        "bread|面包屑|🍞|1|10|0.06|1,1.1,1.3,1.4,1.5|厨房剩的，便宜大碗",
        "worm|蚯蚓|🪱|2|5|0.14|1,1.2,1.6,1.8,2.0|万用饵，两块钱一钩",
        "bloodworm|红虫|🪰|4|5|0.22|1,1.4,2.0,2.4,3.0|小鱼最爱，上钩快",
        "corn|玉米粒|🌽|6|5|0.30|1,1.5,2.3,3.0,4.0|素饵之王，草鱼克星",
        "shrimp|虾饵|🦐|12|3|0.40|1,1.8,2.6,3.6,4.6|肉食鱼最爱，稀有度明显上升",
        "livebait|活饵小鱼|🐟|22|2|0.52|1,2.0,3.2,4.6,6.0|活蹦乱跳，专勾大鱼",
        "secret|秘制饵|🍯|45|1|0.68|1,2.0,4.0,6.0,8.0|祖传配方，闻着就不一样",
    ],
    "item_defs": [
        "feed_basic|普通饲料|🌾|20|打基础的口粮|meat=2;spirit=1",
        "feed_premium|高级饲料|🍖|80|营养均衡，长得快|meat=5;spirit=4;sheen=3",
        "feed_divine|仙露|💧|300|传说中的养鱼圣品|meat=10;spirit=10;sheen=10;value_up=150",
        "pill_quality|洗髓丹|🔮|500|激发血脉，更容易出好个体|quality_up=0.30",
        "coral_deco|珊瑚造景|🪸|260|水族馆装饰，提升馆藏价值|value_up=120",
    ],
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


def _rebuild_location_weights() -> dict[str, dict[str, float]]:
    """用名单权重重建各钓点主池，并回收名单外的老鱼。"""
    result: dict[str, dict[str, float]] = {
        loc: {fid: round(float(w), 3) for fid, w in pool.items() if fid in FISH_BY_ID}
        for loc, pool in ROSTER_WEIGHTS.items()
    }
    for fish in FISH_POOL:
        fid = fish["id"]
        if any(fid in pool for pool in result.values()):
            continue
        homes = FISH_LOCATION_POOLS.get(fid) or ()
        home = next((h for h in homes if h in result), LOCATION_TIER_ORDER[0])
        result.setdefault(home, {})[fid] = LEGACY_EXTRA_WEIGHT.get(fish["rarity"], 1.0)
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
        LOCATION_WEIGHTS[_loc_id].setdefault(_fid, 0.15)


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

#: 每级需要的累计钓获
FISH_PER_LEVEL = 15
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


def _variant_mult(variant_id: str | None) -> float:
    """变异个体的价值倍率（非变异返回 1.0）。"""
    if not variant_id:
        return 1.0
    variant = VARIANT_BY_ID.get(variant_id)
    return float(variant["mult"]) if variant else 1.0


def _codex_key(fish_id: str, variant: str | None = None) -> str:
    """图鉴条目的键：普通个体用 fish_id，变异体加后缀区分。"""
    return f"{fish_id}{VARIANT_CODEX_SUFFIX}{variant}" if variant else fish_id


def _split_codex_key(key: str) -> tuple[str, str | None]:
    """把图鉴键拆回 (fish_id, variant_id)。"""
    if VARIANT_CODEX_SUFFIX in key:
        fish_id, _, variant = key.partition(VARIANT_CODEX_SUFFIX)
        return fish_id, (variant or None)
    return key, None

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


def _is_number(value: Any) -> bool:
    """是否为可用作数值的 int/float（bool 不算）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_number(value: Any, default: float) -> float:
    """把任意值安全地转成 float。

    注意：插件配置里从「竖线分隔字符串」解析出来的字段都是 **str**，
    所以这里必须处理字符串，不能只认 int/float。
    """
    if isinstance(value, bool):
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return float(default)
    return float(default)


def _safe_int(raw: Any, default: int = 0, minimum: int | None = None) -> int:
    """把任意值安全地转成 int（同样要能处理配置里的字符串）。"""
    if isinstance(raw, bool):
        value = default
    elif isinstance(raw, (int, float)):
        value = int(raw)
    elif isinstance(raw, str):
        try:
            value = int(float(raw.strip()))
        except (TypeError, ValueError):
            value = default
    else:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _to_int(text: Any, default: int = 0) -> int:
    """安全地把用户输入转成 int（用户输入一律是 str）。"""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default


def _fmt_gold(amount: Any) -> str:
    try:
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return "0"


def _time_text(timestamp: Any) -> str:
    try:
        ts = float(timestamp)
        if ts <= 0:
            return "——"
        return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
    except (OSError, OverflowError, ValueError, TypeError):
        return "未知"


def _text_now(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """当前时间文本（存档/状态面板统一用它）。"""
    return datetime.now().strftime(fmt)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


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


def _instance_value(instance: dict[str, Any]) -> int:
    """鱼实例的当前价值。"""
    return max(0, _safe_int(instance.get("value"), 0, 0))


def _inventory_value(fish_list: list[dict[str, Any]]) -> int:
    return sum(_instance_value(x) for x in fish_list)


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


def _parse_bait_defs(raw: Any) -> dict[str, dict[str, Any]]:
    """解析鱼饵定义。返回 {bait_id: {...}}，一定包含 none（空钩）。"""
    baits: dict[str, dict[str, Any]] = {}
    items = raw if isinstance(raw, list) else DEFAULTS["bait_defs"]
    for entry in items:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 7:
            continue
        bait_id, name, emoji = parts[0], parts[1], parts[2]
        if not bait_id or not name:
            continue
        price = max(0, _to_int(parts[3], 0))
        stock = max(0, _to_int(parts[4], 0))
        luck = _clamp(_safe_number(parts[5], 0.0), 0.0, 5.0)
        mults: list[float] = []
        for token in (parts[6] or "").split(","):
            mults.append(_clamp(_safe_number(token, 1.0), 0.01, 50.0))
        while len(mults) < len(RARITY_ORDER):
            mults.append(1.0)
        desc = parts[7] if len(parts) > 7 else ""
        baits[bait_id] = {
            "id": bait_id,
            "name": name,
            "emoji": emoji,
            "price": price,
            "stock": stock,
            "luck": luck,
            "rarity_mult": {
                rarity: mults[idx] for idx, rarity in enumerate(RARITY_ORDER)
            },
            "desc": desc,
        }

    if "none" not in baits:
        baits["none"] = {
            "id": "none",
            "name": "空钩",
            "emoji": "🪝",
            "price": 0,
            "stock": 0,
            "luck": 0.0,
            "rarity_mult": {r: 1.0 for r in RARITY_ORDER},
            "desc": "什么也不挂，全凭本事",
        }
    # 空钩永远免费
    baits["none"]["price"] = 0
    return baits


def _parse_effects(text: str) -> dict[str, float]:
    """解析道具效果串：``meat=2;spirit=1;quality_up=0.35``。"""
    effects: dict[str, float] = {}
    for token in (text or "").split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip()
        if key not in ("meat", "spirit", "sheen", "quality_up", "value_up", "heal"):
            continue
        effects[key] = _safe_number(value.strip(), 0.0)
    return effects


def _parse_item_defs(raw: Any) -> dict[str, dict[str, Any]]:
    """解析道具定义。返回 {item_id: {...}}。"""
    items: dict[str, dict[str, Any]] = {}
    entries = raw if isinstance(raw, list) else DEFAULTS["item_defs"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 6:
            continue
        item_id, name, emoji = parts[0], parts[1], parts[2]
        if not item_id or not name:
            continue
        items[item_id] = {
            "id": item_id,
            "name": name,
            "emoji": emoji,
            "price": max(0, _to_int(parts[3], 0)),
            "desc": parts[4],
            "effects": _parse_effects(parts[5]),
        }
    return items


def _parse_aquarium_slots(raw: Any) -> list[dict[str, Any]]:
    """解析水族馆扩建栏位。"""
    slots: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS["aquarium_slots"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        slots.append({"name": parts[0], "price": max(0, _to_int(parts[1], 0))})
    return slots


def _parse_escape_map(raw: Any) -> dict[str, float]:
    """解析 ``传说:0.22,神话:0.32`` 形式的逃脱率表。"""
    result: dict[str, float] = {}
    text = raw if isinstance(raw, str) else ""
    for token in text.split(","):
        token = token.strip()
        if not token or ":" not in token:
            continue
        name, _, value = token.partition(":")
        name = name.strip()
        if name:
            result[name] = _clamp(_safe_number(value.strip(), 0.2), 0.0, 1.0)
    if not result:
        result = {"传说": 0.22, "神话": 0.32}
    return result


#: 上钩率解析失败 / 键不认识时的兜底值（与空钩同档，绝不回退成 100%）
HOOK_RATE_FALLBACK: float = 0.30


def _parse_hook_rates(
    raw: Any, baits: dict[str, dict[str, Any]]
) -> tuple[dict[str, float], list[str]]:
    """解析 ``id:概率`` 形式的上钩率表，返回 (表, 不认识的键)。

    做了这些容错（站长手写配置很容易踩）：
    * 全角逗号 ``，`` / 中文冒号 ``：`` / 多余空格
    * 百分数写法（数值 > 1 时自动 /100，``worm:80`` → 0.80）
    * 中文饵名（用鱼饵表里的 ``name`` 反查 id，``面包屑:0.8`` → ``bread``）
    * 值解析不出来 → 回退 :data:`HOOK_RATE_FALLBACK`（而不是 1.0）
    """
    result: dict[str, float] = {}
    unknown: list[str] = []
    text = raw if isinstance(raw, str) else ""
    by_name: dict[str, str] = {}
    for bid, bait in baits.items():
        name = str(bait.get("name") or "").strip()
        if name:
            by_name[name] = bid
    normalized = text.replace("，", ",").replace("：", ":")
    for token in normalized.split(","):
        token = token.strip()
        if not token or ":" not in token:
            continue
        key, _, value = token.partition(":")
        key = key.strip()
        if not key:
            continue
        # 键：先当 id，再当展示名（中文名/大小写都能认）
        bid = key if key in baits else by_name.get(key)
        if bid is None:
            lowered = key.lower()
            bid = lowered if lowered in baits else by_name.get(key)
        if bid is None:
            unknown.append(key)
            continue
        raw_value = value.strip().rstrip("%").strip()
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            number = HOOK_RATE_FALLBACK
        if number > 100.0:
            number = HOOK_RATE_FALLBACK     # 明显写错了（比如多打一个 0）
        elif number > 1.0:
            number = number / 100.0         # 百分数写法
        result[bid] = _clamp(number, 0.0, 1.0)
    return result, unknown


def _parse_rod_defs(raw: Any) -> list[dict[str, Any]]:
    """解析鱼竿定义：``id|名称|emoji|价格|价值加成|幸运加成|描述``。"""
    rods: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS["rod_defs"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 6 or not parts[0] or not parts[1]:
            continue
        rods.append(
            {
                "id": parts[0],
                "name": parts[1],
                "emoji": parts[2],
                "price": max(0, _to_int(parts[3], 0)),
                "value_bonus": _clamp(_safe_number(parts[4], 0.0), 0.0, 5.0),
                "luck_bonus": _clamp(_safe_number(parts[5], 0.0), 0.0, 2.0),
                "desc": parts[6] if len(parts) > 6 else "",
            }
        )
    if not rods:
        rods = [
            {"id": "bamboo", "name": "竹竿", "emoji": "🎋", "price": 0,
             "value_bonus": 0.0, "luck_bonus": 0.0, "desc": "备用的旧竿"}
        ]
    # 确保有免费的入门竿
    if not any(r["price"] <= 0 for r in rods):
        rods.insert(
            0,
            {"id": "bamboo", "name": "竹竿", "emoji": "🎋", "price": 0,
             "value_bonus": 0.0, "luck_bonus": 0.0, "desc": "备用的旧竿"},
        )
    return rods


def _parse_location_defs(raw: Any) -> list[dict[str, Any]]:
    """解析钓点定义：``id|名称|emoji|解锁等级|解锁金币|价值倍率|说明``。"""
    locations: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS["location_defs"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 6 or not parts[0] or not parts[1]:
            continue
        locations.append(
            {
                "id": parts[0],
                "name": parts[1],
                "emoji": parts[2],
                "level_gate": max(1, _to_int(parts[3], 1)),
                "gold_gate": max(0, _to_int(parts[4], 0)),
                "value_mult": _clamp(_safe_number(parts[5], 1.0), 0.1, 10.0),
                "desc": parts[6] if len(parts) > 6 else "",
            }
        )
    if not locations:
        locations = [
            {"id": "novice", "name": "新手村", "emoji": "🏡", "level_gate": 1,
             "gold_gate": 0, "value_mult": 1.0, "desc": "村口小池塘"}
        ]
    return locations


def _parse_backpack_upgrades(raw: Any) -> list[dict[str, Any]]:
    """解析背包扩容阶梯：``增量|价格``。"""
    ups: list[dict[str, Any]] = []
    entries = raw if isinstance(raw, list) else DEFAULTS["backpack_upgrades"]
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 2:
            continue
        add = _to_int(parts[0], 0)
        if add > 0:
            ups.append({"add": add, "price": max(0, _to_int(parts[1], 0))})
    return ups


# =============================================================================
# 八、玩家数据模型
# =============================================================================

KV_KEY_PREFIX = "player_"
#: 数据版本：1/2 = 早期版本，3 = 鱼实例列表，4 = 三维属性 + 道具 + 水族馆扩建
DATA_VERSION = 4
#: 单次投喂上限（相对鱼种品质）
BASE_AQUARIUM_CAPACITY = 12


def _default_player(user_id: str) -> dict[str, Any]:
    """新玩家初始数据。"""
    return {
        "user_id": str(user_id),
        "data_version": DATA_VERSION,
        "gold": 100,
        "inventory": [],       # 背包（鱼实例列表，容量受限）
        "aquarium": [],        # 水族馆（鱼实例列表）
        "collection": {},      # fish_id -> {count,best_value,first_ts}
        "baits": {},           # bait_id -> 数量
        "equipped_bait": "none",
        "items": {},           # item_id -> 数量（含商店道具与钓上来的杂物）
        "aquarium_slots": [],  # 已解锁的水族馆扩建栏位名
        "backpack_slots": [],  # 已购买的背包扩容档位下标
        # 鱼竿：拥有 + 当前装备
        "rods": ["bamboo"],
        "equipped_rod": "bamboo",
        # 钓点：已解锁 + 当前所在地
        "locations": ["novice"],
        "current_location": "novice",
        # 订单：接单日期 + 订单列表 + 下一批刷新时间（不定时刷新）
        "order_date": "",
        "orders": [],
        "order_next_ts": 0,
        # 正在等玩家决定的随机小插曲：{"id": ..., "ts": ...}
        "event": None,
        # 每日天气 / 鱼市行情（按日期缓存，全天不变）
        "weather_date": "",
        "weather": "",
        "market_date": "",
        "market": [],
        # 鱼塘（水族馆）挂机收益
        "pond_last_ts": 0,
        "pond_claimed_ts": 0,
        "pond_best_income": 0,
        "market_best_bonus": 0,
        # 昵称与平台缓存（排行榜展示 / 排查平台适配问题）
        "last_name": "",
        "last_platform": "",
        # 收集：钓上来的杂物种类记录
        "collectibles": {},
        "bottle_notes": [],    # 收集到的纸条（仅保留最近 30 条）
        "variants": {},        # variant_id -> 累计钓到的次数
        "total_caught": 0,
        "total_sold": 0,
        "total_fed": 0,
        "total_orders": 0,
        "perfect_pulls": 0,
        "clutch_wins": 0,
        "last_fish_time": 0,
        "last_sign_date": "",
        "last_income_date": "",
        "rod_level": 1,
        "achievements": [],
        # 已提示过的里程碑（累计钓获数量），避免重复刷屏
        "milestones": [],
        # 各鱼种品质的最佳渔获记录（长期目标）
        "best_records": {},
        "luck_charges": 0.0,
    }


#: 升级曲线参数：升到 L 级需要的累计钓获 = base*(L-1) + growth*(L-1)^2
#: 越往后每一级要得越多（不是等差数列），站长可在配置里调。
LEVEL_CURVE: dict[str, float] = {"base": 5.0, "growth": 0.6}


def _level_threshold(level: int) -> int:
    """升到 ``level`` 级需要的累计钓获。"""
    n = max(0, int(level) - 1)
    base = _safe_number(LEVEL_CURVE.get("base"), 5.0)
    growth = _safe_number(LEVEL_CURVE.get("growth"), 0.6)
    return int(round(base * n + growth * n * n))


def _player_level(player: dict[str, Any]) -> int:
    """当前等级：按累计钓获查曲线，上限 MAX_LEVEL。"""
    total = _safe_int(player.get("total_caught"), 0, 0)
    level = 1
    while level < MAX_LEVEL and total >= _level_threshold(level + 1):
        level += 1
    return level


def _level_progress(player: dict[str, Any]) -> tuple[int, int, int]:
    """返回 (当前等级, 本级已钓条数, 升级所需条数)。"""
    caught = _safe_int(player.get("total_caught"), 0, 0)
    level = _player_level(player)
    if level >= MAX_LEVEL:
        return level, 0, 0
    into = caught % max(1, FISH_PER_LEVEL)
    return level, into, FISH_PER_LEVEL


def _backpack_capacity(player: dict[str, Any], cfg: dict[str, Any]) -> int:
    """背包容量 = 基础容量 + 已购买的扩容档位。"""
    owned = player.get("backpack_slots") or []
    if not isinstance(owned, list):
        owned = []
    upgrades = cfg.get("_backpack_upgrades") or []
    total = int(cfg.get("backpack_base", 30))
    for idx in owned:
        if isinstance(idx, int) and 0 <= idx < len(upgrades):
            total += int(upgrades[idx].get("add", 0))
    return total


def _new_instance(
    fish_id: str,
    quality_mult: float,
    value_override: float | None = None,
    attrs: dict[str, int] | None = None,
    source: str = "fishing",
    value_bonus: float = 0.0,
    location_mult: float = 1.0,
    variant: str | None = None,
    codex_mult: float = 1.0,
) -> dict[str, Any] | None:
    """生成一条鱼实例。fish_id 不在图鉴里时返回 None。"""
    fish = FISH_BY_ID.get(fish_id)
    if fish is None:
        return None

    quality_mult = _clamp(float(quality_mult), 0.5, 5.0)
    quality, _ = _quality_label(quality_mult)

    # 三维属性
    if attrs:
        final_attrs = {
            key: int(_clamp(_safe_int(attrs.get(key), 60), 1, 100))
            for key in ATTR_WEIGHTS
        }
    else:
        low, high = RARITY_ATTR_RANGE.get(fish["rarity"], (40, 80))
        final_attrs = {
            key: random.randint(int(low), int(high)) for key in ATTR_WEIGHTS
        }

    # 个体差异：上钩时固定下来，之后重算价值都复用它（否则投喂可能掉价）
    variance = random.uniform(*VALUE_VARIANCE)
    # 装备/钓点/变异/图鉴集齐加成，同样在上钩时固化进 base_value
    gear = (
        (1.0 + max(0.0, float(value_bonus)))
        * max(0.1, float(location_mult))
        * _variant_mult(variant)
        * max(0.1, float(codex_mult))
    )
    if value_override is not None and _is_number(value_override):
        base = max(1, int(value_override))
    else:
        base = _compute_value(fish["value"], final_attrs, quality_mult, variance, gear)

    return {
        "id": uuid.uuid4().hex[:10],
        "fish_id": fish_id,
        "variant": variant,
        # base_value 是「算出来的基础价」，value = base_value + live_bonus（养成加成）
        "base_value": base,
        "value": base,
        "quality": quality,
        "quality_mult": round(quality_mult, 4),
        "value_variance": round(variance, 4),
        "gear_mult": round(gear, 4),
        "attrs": final_attrs,
        "feed_uses": 0,
        "live_bonus": 0,
        "locked": False,
        # 水族馆展出加成是否已领取（每条鱼终生只能领一次，防止无限叠加）
        "pond_claimed": False,
        "source": source,
        "ts": int(time.time()),
    }


def _compute_value(
    base_value: float,
    attrs: dict[str, int],
    quality_mult: float,
    variance: float | None = None,
    gear_mult: float = 1.0,
) -> int:
    """售价 = 基准价 x 属性系数 x 个体品质倍率 x 个体差异 x 装备/钓点倍率。

    关键：``variance``（个体差异）与 ``gear_mult``（鱼竿+钓点）在鱼上钩时
    掷一次就固化下来，之后重算价值必须传入同一个值。否则每次投喂都重掷随机数，
    会出现「喂了反而掉价」。
    """
    weighted = sum(
        _clamp(_safe_number(attrs.get(key), ATTR_PAR), 0, 100) * weight
        for key, weight in ATTR_WEIGHTS.items()
    )
    attr_factor = 1.0 + (weighted - ATTR_PAR) / 100.0
    if variance is None:
        variance = random.uniform(*VALUE_VARIANCE)
    return max(
        1,
        int(
            round(
                base_value
                * attr_factor
                * quality_mult
                * variance
                * max(0.1, float(gear_mult))
            )
        ),
    )


#: 名字里带这些字的多半是狠角色（敌对生物）。玩家看不到任何标记或提示，
#: 只有把两条放在同一个缸里才会「出事」——保持神秘感。
HOSTILE_KEYWORDS: tuple[str, ...] = (
    "鳄", "鲨", "蛇", "鳗", "鳝", "乌贼", "章鱼", "食人", "水虎", "龙鱼",
    "巨齿", "利维坦", "归墟", "古龙", "鲸", "鮟鱇", "电鳗", "鳄雀",
)


def _is_hostile(fish_id: str) -> bool:
    """这条鱼是不是「不好惹」的（内部判定，玩家侧无任何显示）。"""
    fish = FISH_BY_ID.get(fish_id)
    if fish is None:
        return False
    name = str(fish.get("name") or "")
    return any(word in name for word in HOSTILE_KEYWORDS)


def _fish_power(instance: dict[str, Any]) -> int:
    """一条鱼的综合实力：三维 + 个体品质 + 鱼种品质 + 变异 + 养成。

    敌对冲突就看这个值：高的一方活下来，低的一方没了。
    """
    attrs = instance.get("attrs") or {}
    power = sum(_safe_int(attrs.get(k), 0, 0) for k in ATTR_WEIGHTS)
    power += int(_safe_number(instance.get("quality_mult"), 1.0) * 60)
    power += RARITY_RANK.get(_fish_rarity(instance.get("fish_id", "")), 0) * 45
    power += int(_variant_mult(instance.get("variant")) * 40)
    feed = _safe_int(instance.get("feed_uses"), 0, 0)
    power += feed * 6
    return power


def _apply_feed(instance: dict[str, Any], effects: dict[str, float]) -> tuple[dict[str, int], int]:
    """对一条鱼应用饲料效果。

    设计要点：基础价值 ``base_value`` 与养成加成 ``live_bonus`` **分开存**。
    投喂只重算 base_value 并把 value_up 累加到 live_bonus，
    最终价值 = base_value + live_bonus —— 这样投喂永远不会让鱼掉价
    （早期版本直接用「当前价值」当基准，随机差异会导致喂了反而变便宜）。

    返回 (属性变化量, 价值变化量)。
    """
    attrs = instance.setdefault("attrs", {})
    before = {key: _safe_int(attrs.get(key), int(ATTR_PAR), 1) for key in ATTR_WEIGHTS}
    for key in ATTR_WEIGHTS:
        delta = int(round(_safe_number(effects.get(key), 0)))
        attrs[key] = int(_clamp(before[key] + delta, 1, 100))

    old_value = _instance_value(instance)
    value_up = int(round(_safe_number(effects.get("value_up"), 0)))
    if value_up:
        instance["live_bonus"] = _safe_int(instance.get("live_bonus"), 0, 0) + value_up

    fish = FISH_BY_ID.get(instance.get("fish_id", ""))
    if fish is not None:
        quality_mult = _safe_number(instance.get("quality_mult"), 1.0)
        # 复用上钩时固定的个体差异，保证「喂养只涨不跌」
        variance = _clamp(
            _safe_number(instance.get("value_variance"), 1.0),
            VALUE_VARIANCE[0],
            VALUE_VARIANCE[1],
        )
        old_base = _safe_int(instance.get("base_value"), 0, 0)
        # ⚠️ 必须把上钩时固化的装备倍率一起传进去：漏传会让鱼竿/钓点/变异/图鉴
        # 加成全部被打回 1.0，喂一次就大幅掉价（这是修过的真实 bug）。
        raw_gear = instance.get("gear_mult")
        if _is_number(raw_gear):
            gear_mult = _clamp(float(raw_gear), 0.1, 50.0)
        elif old_base > 0:
            # 旧存档没存 gear_mult：用「喂之前的价格 ÷ 不带装备的算法价」反推，
            # 这样这一次投喂仍按原来的鱼竿+钓点档次涨价
            pure_old = _compute_value(
                fish["value"], before, quality_mult, variance
            )
            gear_mult = _clamp(old_base / max(1, pure_old), 0.1, 50.0)
            instance["gear_mult"] = round(gear_mult, 4)
        else:
            gear_mult = 1.0
        new_base = _compute_value(
            fish["value"], attrs, quality_mult, variance, gear_mult
        )
        # 只涨不跌的保险：任何情况下都不允许投喂把基础价压低
        instance["base_value"] = max(old_base, new_base)
    instance["value"] = _safe_int(instance.get("base_value"), old_value, 1) + _safe_int(
        instance.get("live_bonus"), 0, 0
    )
    instance["feed_uses"] = _safe_int(instance.get("feed_uses"), 0, 0) + 1

    gained = {key: attrs[key] - before[key] for key in ATTR_WEIGHTS}
    return gained, _instance_value(instance) - old_value


def _quality_label(multiplier: float) -> tuple[str, str]:
    """根据品质倍率反推个体品质名与 emoji。"""
    for name, low, high, emoji in QUALITY_TIERS:
        if low <= multiplier < high:
            return name, emoji
    top = QUALITY_TIERS[-1]
    if multiplier >= top[1]:
        return top[0], top[3]
    bottom = QUALITY_TIERS[0]
    return bottom[0], bottom[3]


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


def _repair_instance(raw: Any) -> dict[str, Any] | None:
    """修复一条鱼实例；无法修复返回 None。"""
    if not isinstance(raw, dict):
        return None
    fish_id = raw.get("fish_id")
    if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
        return None

    instance_id = raw.get("id")
    if not isinstance(instance_id, str) or not instance_id:
        instance_id = uuid.uuid4().hex[:10]

    quality_mult = _clamp(_safe_number(raw.get("quality_mult"), 1.0), 0.5, 5.0)
    quality, _ = _quality_label(quality_mult)

    # 三维：老数据没有，就按品质区间中值补一个（保证价格合理）
    raw_attrs = raw.get("attrs")
    low, high = RARITY_ATTR_RANGE.get(FISH_BY_ID[fish_id]["rarity"], (40, 80))
    mid = int((low + high) / 2)
    attrs: dict[str, int] = {}
    for key in ATTR_WEIGHTS:
        source = raw_attrs.get(key) if isinstance(raw_attrs, dict) else None
        attrs[key] = int(_clamp(_safe_int(source, mid), 1, 100))

    # 装备倍率：老数据没有就按 1.0（等价于没吃任何鱼竿/钓点加成）
    gear_mult = _clamp(_safe_number(raw.get("gear_mult"), 1.0), 0.1, 50.0)

    value = _safe_int(raw.get("value"), -1)
    if value <= 0:
        # 老数据连 value 都没有：按同一条鱼的算法补一个价，顺便带上装备倍率
        value = _compute_value(
            FISH_BY_ID[fish_id]["value"], attrs, quality_mult, None, gear_mult
        )

    live_bonus = _safe_int(raw.get("live_bonus"), 0, 0)
    # base_value 是「不含养成加成」的基础价；老数据没有就用 value - live_bonus 反推
    base_value = _safe_int(raw.get("base_value"), -1)
    if base_value <= 0:
        base_value = max(1, value - max(0, live_bonus))

    # 个体差异：老数据没有就补一个中值，保证后续重算稳定
    variance = _clamp(
        _safe_number(raw.get("value_variance"), 1.0), VALUE_VARIANCE[0], VALUE_VARIANCE[1]
    )
    variant = raw.get("variant")
    if not isinstance(variant, str) or variant not in VARIANT_BY_ID:
        variant = None

    return {
        "id": instance_id,
        "fish_id": fish_id,
        "variant": variant if variant in VARIANT_BY_ID else None,
        "base_value": base_value,
        "value": max(1, value),
        "quality": quality,
        "quality_mult": round(quality_mult, 4),
        "value_variance": round(variance, 4),
        "gear_mult": round(gear_mult, 4),
        "attrs": attrs,
        "feed_uses": _safe_int(raw.get("feed_uses"), 0, 0),
        "live_bonus": live_bonus,
        "locked": bool(raw.get("locked")),
        "pond_claimed": bool(raw.get("pond_claimed")),
        "source": raw.get("source")
        if isinstance(raw.get("source"), str)
        else "fishing",
        "ts": _safe_int(raw.get("ts"), 0, 0),
    }


def _sync_collection(player: dict[str, Any]) -> None:
    """用背包 + 水族馆校正图鉴（count 为累计值，只增不减）。"""
    collection = player.setdefault("collection", {})
    all_fish = list(player.get("inventory", [])) + list(player.get("aquarium", []))
    held: dict[str, int] = {}
    for instance in all_fish:
        fish_id = instance.get("fish_id")
        if isinstance(fish_id, str) and fish_id in FISH_BY_ID:
            held[fish_id] = held.get(fish_id, 0) + 1

    for fish_id, count in held.items():
        entry = collection.get(fish_id)
        if not isinstance(entry, dict):
            entry = {"count": 0, "best_value": 0, "first_ts": 0}
            collection[fish_id] = entry
        entry["count"] = max(_safe_int(entry.get("count"), 0, 0), count)

    for instance in all_fish:
        fish_id = instance.get("fish_id")
        if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
            continue
        entry = collection.get(fish_id)
        if not isinstance(entry, dict):
            continue
        entry["best_value"] = max(
            _safe_int(entry.get("best_value"), 0, 0), _instance_value(instance)
        )
        if not _safe_int(entry.get("first_ts"), 0, 0):
            entry["first_ts"] = _safe_int(instance.get("ts"), 0, 0)


def _held_counts(player: dict[str, Any]) -> dict[str, int]:
    held: dict[str, int] = {}
    for instance in list(player.get("inventory", [])) + list(
        player.get("aquarium", [])
    ):
        fish_id = instance.get("fish_id")
        if isinstance(fish_id, str) and fish_id in FISH_BY_ID:
            held[fish_id] = held.get(fish_id, 0) + 1
    return held


def _repair_player(raw: Any, user_id: str) -> tuple[dict[str, Any], bool]:
    """修复/迁移玩家数据，返回 (player, 是否迁移过)。"""
    player = _default_player(user_id)
    if not isinstance(raw, dict):
        return player, False

    migrated = False
    try:
        player["gold"] = _safe_int(raw.get("gold"), player["gold"], 0)

        # --- 背包 ---
        raw_inv = raw.get("inventory")
        if isinstance(raw_inv, list):
            cleaned: list[dict[str, Any]] = []
            for item in raw_inv:
                instance = _repair_instance(item)
                if instance is not None:
                    cleaned.append(instance)
            player["inventory"] = cleaned
        elif isinstance(raw_inv, dict):
            # v1/v2：{"carp": 3}
            migrated = True
            converted: list[dict[str, Any]] = []
            for fish_id, count in raw_inv.items():
                if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
                    continue
                for _ in range(min(_safe_int(count, 0, 0), 500)):
                    instance = _new_instance(
                        fish_id, _roll_quality_mult(DEFAULTS["quality_weights"]), source="legacy"
                    )
                    if instance is not None:
                        converted.append(instance)
            player["inventory"] = converted

        # --- 水族馆 ---
        raw_aq = raw.get("aquarium")
        if isinstance(raw_aq, list):
            aquarium: list[dict[str, Any]] = []
            for item in raw_aq:
                instance = _repair_instance(item)
                if instance is not None:
                    aquarium.append(instance)
            player["aquarium"] = aquarium

        # --- 图鉴（键可能是 fish_id，也可能是 fish_id#variant 的变异条目）---
        raw_col = raw.get("collection")
        collection: dict[str, dict[str, Any]] = {}
        if isinstance(raw_col, dict):
            for key, entry in raw_col.items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                fish_id, variant = _split_codex_key(key)
                if fish_id not in FISH_BY_ID:
                    continue
                if variant is not None and variant not in VARIANT_BY_ID:
                    continue
                collection[_codex_key(fish_id, variant)] = {
                    "count": _safe_int(entry.get("count"), 0, 0),
                    "best_value": _safe_int(entry.get("best_value"), 0, 0),
                    "first_ts": _safe_int(entry.get("first_ts"), 0, 0),
                }
        player["collection"] = collection

        # --- 订单（不定时刷新）---
        order_date = raw.get("order_date", "")
        player["order_date"] = order_date if isinstance(order_date, str) else ""
        # 刷新时间戳必须保留：丢了会导致每次读档都换一批订单（交单永远失败）
        player["order_next_ts"] = _safe_int(raw.get("order_next_ts"), 0, 0)
        raw_orders = raw.get("orders")
        orders: list[dict[str, Any]] = []
        if isinstance(raw_orders, list):
            for item in raw_orders:
                if not isinstance(item, dict):
                    continue
                fish_id = item.get("fish_id")
                if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
                    continue
                orders.append(
                    {
                        "fish_id": fish_id,
                        "need": max(1, _safe_int(item.get("need"), 1, 1)),
                        "have": max(0, _safe_int(item.get("have"), 0, 0)),
                        "reward": max(1, _safe_int(item.get("reward"), 1, 1)),
                        "done": bool(item.get("done")),
                    }
                )
        player["orders"] = orders

        # --- 正在等玩家决定的随机小插曲 ---
        raw_event = raw.get("event")
        player["event"] = None
        if isinstance(raw_event, dict) and raw_event.get("id") in EVENT_BY_ID:
            player["event"] = {
                "id": str(raw_event["id"]),
                "ts": _safe_int(raw_event.get("ts"), 0, 0),
            }

        # --- 天气与鱼市（按日期缓存）---
        for key in ("weather_date", "weather", "market_date"):
            value = raw.get(key, "")
            player[key] = value if isinstance(value, str) else ""
        if player["weather"] and player["weather"] not in WEATHER_BY_ID:
            player["weather"] = ""
        raw_market = raw.get("market")
        market: list[dict[str, Any]] = []
        if isinstance(raw_market, list):
            for item in raw_market:
                if not isinstance(item, dict):
                    continue
                fish_id = item.get("fish_id")
                if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
                    continue
                market.append(
                    {
                        "fish_id": fish_id,
                        "mult": _clamp(_safe_number(item.get("mult"), 1.5), 1.0, 10.0),
                    }
                )
        player["market"] = market

        # --- 鱼塘挂机收益时间戳 ---
        player["pond_last_ts"] = _safe_int(raw.get("pond_last_ts"), 0, 0)
        player["pond_claimed_ts"] = _safe_int(raw.get("pond_claimed_ts"), 0, 0)
        player["pond_best_income"] = _safe_int(raw.get("pond_best_income"), 0, 0)
        player["market_best_bonus"] = _safe_int(raw.get("market_best_bonus"), 0, 0)
        last_name = raw.get("last_name", "")
        player["last_name"] = last_name if isinstance(last_name, str) else ""

        # --- 变异收集 ---
        raw_variants = raw.get("variants")
        variants: dict[str, int] = {}
        if isinstance(raw_variants, dict):
            for vid, cnt in raw_variants.items():
                if isinstance(vid, str) and vid in VARIANT_BY_ID:
                    variants[vid] = _safe_int(cnt, 0, 0)
        player["variants"] = variants

        # --- 鱼饵 ---
        raw_baits = raw.get("baits")
        baits: dict[str, int] = {}
        if isinstance(raw_baits, dict):
            for bait_id, count in raw_baits.items():
                if isinstance(bait_id, str) and bait_id != "none":
                    baits[bait_id] = _safe_int(count, 0, 0)
        legacy_bait = _safe_int(raw.get("bait_count"), 0, 0)
        if legacy_bait > 0:
            baits["worm"] = baits.get("worm", 0) + legacy_bait
            migrated = True
        player["baits"] = baits

        equipped = raw.get("equipped_bait")
        player["equipped_bait"] = equipped if isinstance(equipped, str) else "none"

        # --- 道具 ---
        raw_items = raw.get("items")
        items: dict[str, int] = {}
        if isinstance(raw_items, dict):
            for item_id, count in raw_items.items():
                if isinstance(item_id, str):
                    items[item_id] = _safe_int(count, 0, 0)
        player["items"] = items

        # --- 水族馆扩建 ---
        raw_slots = raw.get("aquarium_slots")
        if isinstance(raw_slots, list):
            player["aquarium_slots"] = [s for s in raw_slots if isinstance(s, str)]

        # --- 背包扩容档位 ---
        raw_bp = raw.get("backpack_slots")
        if isinstance(raw_bp, list):
            player["backpack_slots"] = [
                v for v in raw_bp if isinstance(v, int) and not isinstance(v, bool)
            ]

        # --- 鱼竿 ---
        raw_rods = raw.get("rods")
        rods: list[str] = []
        if isinstance(raw_rods, list):
            rods = [r for r in raw_rods if isinstance(r, str) and r in ROD_BY_ID]
        if not rods:
            rods = [DEFAULT_ROD]
        player["rods"] = rods
        equipped_rod = raw.get("equipped_rod")
        player["equipped_rod"] = (
            equipped_rod if equipped_rod in rods else rods[0]
        )

        # --- 钓点 ---
        raw_locs = raw.get("locations")
        locs: list[str] = []
        if isinstance(raw_locs, list):
            locs = [l for l in raw_locs if isinstance(l, str) and l in LOCATION_BY_ID]
        if DEFAULT_LOCATION not in locs:
            locs.insert(0, DEFAULT_LOCATION)
        player["locations"] = locs
        current = raw.get("current_location")
        player["current_location"] = current if current in locs else DEFAULT_LOCATION


        # --- 杂物图鉴与纸条（彩蛋计数 egg_* 也要保留）---
        raw_coll = raw.get("collectibles")
        col: dict[str, int] = {}
        if isinstance(raw_coll, dict):
            for cid, cnt in raw_coll.items():
                if not isinstance(cid, str):
                    continue
                if cid in COLLECTIBLE_BY_ID or (
                    cid.startswith("egg_") and cid[4:] in EASTER_EGG_BY_ID
                ):
                    col[cid] = _safe_int(cnt, 0, 0)
        player["collectibles"] = col

        raw_notes = raw.get("bottle_notes")
        if isinstance(raw_notes, list):
            player["bottle_notes"] = [n for n in raw_notes if isinstance(n, str)][-30:]

        # --- 计数 ---
        player["total_orders"] = _safe_int(raw.get("total_orders"), 0, 0)
        # 拉线技巧计数（用于成就）
        player["perfect_pulls"] = _safe_int(raw.get("perfect_pulls"), 0, 0)
        player["clutch_wins"] = _safe_int(raw.get("clutch_wins"), 0, 0)

        # --- 里程碑提示记录 ---
        raw_ms = raw.get("milestones")
        player["milestones"] = (
            [_safe_int(m, 0, 0) for m in raw_ms if _safe_int(m, 0, 0) > 0]
            if isinstance(raw_ms, list)
            else []
        )

        # --- 最佳渔获纪录（图鉴页展示 + 长期目标成就）---
        raw_records = raw.get("best_records")
        records: dict[str, dict[str, Any]] = {}
        if isinstance(raw_records, dict):
            for rarity, rec in raw_records.items():
                if rarity not in RARITY_RANK or not isinstance(rec, dict):
                    continue
                fish_id = rec.get("fish_id")
                if not isinstance(fish_id, str) or fish_id not in FISH_BY_ID:
                    continue
                variant = rec.get("variant")
                records[rarity] = {
                    "fish_id": fish_id,
                    "variant": variant if variant in VARIANT_BY_ID else None,
                    "quality": rec.get("quality")
                    if rec.get("quality") in QUALITY_RANK
                    else QUALITY_TIERS[0][0],
                    "value": _safe_int(rec.get("value"), 0, 0),
                    "ts": _safe_int(rec.get("ts"), 0, 0),
                }
        player["best_records"] = records

        # --- 洗髓丹累积的品质幸运储备 ---
        player["luck_charges"] = _clamp(
            _safe_number(raw.get("luck_charges"), 0.0), 0.0, 5.0
        )

        # --- 计数器 ---
        player["total_caught"] = _safe_int(raw.get("total_caught"), 0, 0)
        player["total_sold"] = _safe_int(raw.get("total_sold"), 0, 0)
        player["total_fed"] = _safe_int(raw.get("total_fed"), 0, 0)

        # --- 时间 ---
        player["last_fish_time"] = _safe_int(raw.get("last_fish_time"), 0, 0)
        for key in ("last_sign_date", "last_income_date"):
            value = raw.get(key, "")
            player[key] = value if isinstance(value, str) else ""

        player["rod_level"] = _safe_int(raw.get("rod_level"), 1, 1)

        # --- 成就 ---
        raw_ach = raw.get("achievements")
        achievements: list[str] = []
        if isinstance(raw_ach, list):
            for item in raw_ach:
                if isinstance(item, str) and item in ACHIEVEMENTS:
                    achievements.append(item)
        player["achievements"] = achievements

        _sync_collection(player)

    except Exception as e:  # pragma: no cover
        logger.warning(f"玩家 {user_id} 数据修复失败，已重置：{e}")
        return _default_player(user_id), False

    player["user_id"] = str(user_id)
    player["data_version"] = DATA_VERSION
    return player, migrated


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


def _instance_line(instance: dict[str, Any], with_value: bool = True) -> str:
    """一行完整信息：``🌟✨ 黄金锦鲤 ✨传说 🟣极品 · 1280``。"""
    fish_id = instance.get("fish_id", "")
    fish = FISH_BY_ID.get(fish_id)
    if fish is None:
        return "❔ 未知"
    variant = instance.get("variant")
    variant_tag = ""
    name = fish["name"]
    if variant:
        vcfg = VARIANT_BY_ID.get(variant)
        if vcfg:
            variant_tag = f"{vcfg['emoji']}{vcfg['name']}·"
            name = f"{vcfg['name']}{name}"
    locked = "🔒" if instance.get("locked") else ""
    text = (
        f"{variant_tag}{_fish_emoji(fish)} {name} "
        f"{_rarity_tag(fish_id)} {_quality_tag(instance)}{locked}"
    )
    if with_value:
        text += f" · {_fmt_gold(_instance_value(instance))}"
    return text


def _attrs_line(instance: dict[str, Any], compact: bool = True) -> str:
    """三维属性一行展示。

    两个模式都**保留完整属性名**（肉质/灵性/光泽）——
    缩写成「肉/灵/光」虽然短，但玩家看不懂，尤其对不上投喂时涨的是哪一项。
    ``compact=True`` 只省掉数值之间的空格。
    """
    attrs = instance.get("attrs") or {}
    if compact:
        return " ".join(
            f"{label}{_safe_int(attrs.get(key), 0, 0)}"
            for key, label in ATTR_LABELS.items()
        )
    return "　".join(
        f"{label} {_safe_int(attrs.get(key), 0, 0)}" for key, label in ATTR_LABELS.items()
    )


def _attrs_bar(instance: dict[str, Any]) -> str:
    """带进度条的三维展示，用于单条鱼详情（简短版）。"""
    attrs = instance.get("attrs") or {}
    return "　".join(
        f"{label}{_safe_int(attrs.get(key), 0, 0)} {_bar(_safe_int(attrs.get(key), 0, 0), 6)}"
        for key, label in ATTR_LABELS.items()
    )


def _sort_key(instance: dict[str, Any]) -> tuple[int, int, int]:
    """排序：变异 > 个体品质 > 鱼种品质 > 价值，均从高到低。"""
    return (
        0 if instance.get("variant") else 1,
        -QUALITY_RANK.get(instance.get("quality", ""), 0),
        -RARITY_RANK.get(_fish_rarity(instance.get("fish_id", "")), 0),
        -_instance_value(instance),
    )


# =============================================================================
# 十、插件主体
# =============================================================================


class FishingPlugin(Star):
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
        cfg["level_xp_growth"] = _clamp(
            _safe_number(cfg.get("level_xp_growth"), 0.6), 0.0, 20.0
        )
        LEVEL_CURVE["base"] = cfg["level_xp_base"]
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

    def _rarity_name(self, rarity: str) -> str:
        """按配置把品质名映射成展示名（下标即稀有度）。"""
        idx = RARITY_RANK.get(rarity, 0)
        names = self.cfg.get("rarity_display_names") or []
        if 0 <= idx < len(names):
            return str(names[idx])
        return rarity

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

    def _reply(self, event: AstrMessageEvent, text: str):
        """统一的回复入口。

        不同平台对文本的处理不一样，这里做一层适配：

        - **QQ 官方机器人（qq_official）**：不支持原生 markdown 时会由 AstrBot
          自动降级为纯文本，所以我们统一走 ``plain_result`` 即可，安全。
        - 其余平台：同样是纯文本，不做额外处理。

        单独包一个函数是为了将来接按钮/模板消息时只需改这一个地方。
        当前 AstrBot（v4.28.1）的消息组件里没有 Button/Keyboard，
        QQ 官方机器人的按钮需要直接用 botpy 的 keyboard payload，
        不属于插件公开 API，因此这里不做，改用清晰的分行文本指令引导。
        """
        return event.plain_result(text)

    # -------------------------------------------------------------------------
    # 玩家数据读写（插件级 KV 存储）
    # -------------------------------------------------------------------------

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

    def _rod_label(self, player: dict[str, Any]) -> str:
        rod = self._rod(player)
        return f"{rod.get('emoji', '')}{rod.get('name', '鱼竿')}"

    def _location(self, player: dict[str, Any]) -> dict[str, Any]:
        """玩家当前所在钓点配置。"""
        loc_id = player.get("current_location")
        loc = self.location_by_id.get(loc_id) if isinstance(loc_id, str) else None
        return loc or self.location_by_id.get(DEFAULT_LOCATION) or {
            "id": DEFAULT_LOCATION, "name": "新手村", "emoji": "🏡",
            "level_gate": 1, "gold_gate": 0, "value_mult": 1.0, "desc": "",
        }

    def _location_label(self, player: dict[str, Any]) -> str:
        loc = self._location(player)
        return f"{loc.get('emoji', '')}{loc.get('name', '钓点')}"

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

    def _weather_label(self, player: dict[str, Any]) -> str:
        weather = self._weather(player)
        return f"{weather['emoji']}{weather['name']}" if weather else ""

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

    def _market_label(self, player: dict[str, Any]) -> str:
        parts = []
        for entry in player.get("market") or []:
            if not isinstance(entry, dict):
                continue
            fish = FISH_BY_ID.get(entry.get("fish_id", ""))
            if fish is None:
                continue
            mult = _safe_number(entry.get("mult"), 1.0)
            parts.append(
                f"{_fish_emoji(fish)}{fish['name']}+{(mult - 1) * 100:.0f}%"
            )
        return "　".join(parts)

    # -------------------------------------------------------------------------
    # 变异个体
    # -------------------------------------------------------------------------

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

    # -------------------------------------------------------------------------
    # QQ 官方机器人：内联键盘（按钮）
    # -------------------------------------------------------------------------
    #
    # AstrBot 的消息组件里没有按钮，所以这里在**平台允许时**直接调用
    # botpy 的原生接口发一条带 keyboard 的消息（官方文档：
    # /v2/groups/{group_openid}/messages 的 keyboard 字段）。
    # 按钮一律用 action.type = 2（指令按钮）：点击后等价于玩家发出 data 里的指令，
    # 这样按钮走的是 AstrBot 正常的指令链路，不需要额外的回调接口。
    # 其它平台 / 发送失败时自动退回纯文本，并在正文里把指令写清楚。

    @staticmethod
    def _btn(label: str, data: str, style: int = 1) -> dict[str, Any]:
        """构造一个官方「指令按钮」。"""
        return {
            "id": f"b{abs(zlib.crc32(data.encode('utf-8'))) % 100000000}",
            "render_data": {
                "label": label[:10],
                "visited_label": label[:10],
                "style": style,
            },
            "action": {
                "type": 2,  # 2 = 指令按钮
                "permission": {"type": 2},  # 2 = 所有人可用
                "data": data,
                "enter": True,   # 单聊里点一下直接发送
                "reply": False,
            },
        }

    @staticmethod
    def _keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any] | None:
        rows = [r for r in rows if r]
        if not rows:
            return None
        return {"content": {"rows": [{"buttons": r} for r in rows]}}

    async def _send_with_buttons(
        self, event: AstrMessageEvent, text: str, rows: list[list[dict[str, Any]]]
    ) -> bool:
        """发一条带按钮的消息；不支持/失败返回 False（调用方退回纯文本）。

        官方文档里「带键盘的消息」示例是 **markdown + keyboard**（msg_type=2），
        但纯文本 + keyboard（msg_type=0）在部分场景也能用，而且 markdown 需要
        额外权限。所以这里按配置 `button_mode` 依次尝试，并把**哪种形态成功**
        （或具体报错）写进日志，方便定位「为什么没有按钮」。
        """
        keyboard = self._keyboard(rows)
        if keyboard is None:
            return False
        mode = str(self.cfg.get("button_mode") or "自动").strip().lower()
        if mode in ("关闭", "off", "none", "false", "0"):
            return False
        try:
            platform = str(event.get_platform_name() or "").lower()
        except Exception:
            return False
        if platform not in ("qq_official", "qq_official_webhook"):
            return False
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        msg_obj = getattr(event, "message_obj", None)
        if api is None or msg_obj is None:
            return False

        raw = getattr(msg_obj, "raw_message", None)
        msg_id = str(getattr(msg_obj, "message_id", "") or "")
        body = (text or "").strip()

        shapes: list[dict[str, Any]] = []
        if mode in ("自动", "auto", "markdown", "md"):
            shapes.append({"msg_type": 2, "markdown": {"content": body}})
        if mode in ("自动", "auto", "text", "纯文本"):
            shapes.append({"msg_type": 0, "content": body})

        group_openid = str(
            getattr(raw, "group_openid", "") or getattr(msg_obj, "group_id", "") or ""
        )
        author = getattr(raw, "author", None)
        openid = str(getattr(author, "user_openid", "") or "")

        last_error: Exception | None = None
        for shape in shapes:
            payload = dict(shape)
            payload["keyboard"] = keyboard
            payload["msg_seq"] = random.randint(1, 99999)
            if msg_id:
                payload["msg_id"] = msg_id
            try:
                if group_openid and hasattr(api, "post_group_message"):
                    await api.post_group_message(group_openid=group_openid, **payload)
                elif openid and hasattr(api, "post_c2c_message"):
                    await api.post_c2c_message(openid=openid, **payload)
                else:
                    return False
                # 成功了：记一次是怎么发出去的，以后排查有据可依
                if not getattr(self, "_button_ok_logged", False):
                    self._button_ok_logged = True
                    logger.info(
                        f"QQ 官方按钮发送成功（msg_type={shape['msg_type']}，"
                        f"{len(rows)} 行按钮）"
                    )
                return True
            except Exception as e:  # 换下一种形态
                last_error = e

        if last_error is not None and not getattr(self, "_button_warned", False):
            self._button_warned = True
            logger.warning(
                f"QQ 官方按钮发送失败，已退回纯文本：{last_error}\n"
                f"　已尝试的形态：{[s['msg_type'] for s in shapes]}；"
                f"可在插件配置里把 button_mode 设为 markdown 或 text 单独试，"
                f"或设为「关闭」不再尝试。常见原因：适配器不支持 keyboard、"
                f"机器人没有内联键盘/markdown 权限。"
            )
        elif last_error is not None:
            logger.debug(f"带按钮的消息发送失败，改用纯文本：{last_error}")
        return False

    def _with_at(self, event: AstrMessageEvent, result: Any) -> Any:
        """给一条回复加上 @发送者（只用在每条指令的第一条消息上）。

        平台支持 At 组件时才加；不支持/出错就原样返回，不影响功能。
        """
        try:
            platform = str(event.get_platform_name() or "").lower()
            if platform not in ("aiocqhttp", "qq_official", "qq_official_webhook"):
                return result
            text = getattr(result, "text", None)
            if not isinstance(text, str) or not text:
                return result
            from astrbot.api.message_components import At, Plain

            uid = str(event.get_sender_id())
            return event.chain_result([At(qq=uid), Plain("\n" + text)])
        except Exception:
            return result

    async def _say(
        self,
        event: AstrMessageEvent,
        text: str,
        rows: list[list[dict[str, Any]]] | None = None,
    ):
        """**统一输出出口**：能发按钮就发按钮，否则退回纯文本。

        用法：``async for r in self._say(event, text, rows): yield r``
        """
        if rows and await self._send_with_buttons(event, text, rows):
            return
        yield event.plain_result(text)

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

    def _bag_rows(self) -> list[list[dict[str, Any]]]:
        """背包视图的按钮：卖光光 / 水族馆 / 再来一竿。"""
        return [[
            self._btn("卖光光", "/钓鱼 卖光光"),
            self._btn("水族馆", "/钓鱼 水族馆"),
            self._btn("再来一竿", "/钓鱼"),
        ]]

    def _shop_rows(self) -> list[list[dict[str, Any]]]:
        """商店视图的按钮：一键买常用饵。"""
        cheap = sorted(
            (b for b in self._bait_list() if b != "none"),
            key=lambda b: self.baits[b].get("price", 0),
        )[:3]
        return [[
            self._btn(f"买{self.baits[b]['name']}", f"/钓鱼 商店 买 {self.baits[b]['name']} 10")
            for b in cheap
        ]] or []

    def _location_rows(self) -> list[list[dict[str, Any]]]:
        """钓点视图的按钮：图鉴 / 查看背包。"""
        return [[
            self._btn("查图鉴", "/钓鱼 图鉴"),
            self._btn("背包", "/钓鱼 背包"),
            self._btn("今日", "/钓鱼 今日"),
        ]]

    def _cast_rows(self) -> list[list[dict[str, Any]]]:
        """抛竿结果下面的常用按钮。"""
        return [
            [
                self._btn("再来一竿", "/钓鱼"),
                self._btn("看背包", "/钓鱼 背包"),
                self._btn("今日", "/钓鱼 今日"),
            ],
            [self._btn("卖光光", "/钓鱼 卖光光"), self._btn("帮助", "/钓鱼 帮助")],
        ]

    def _pull_rows(self) -> list[list[dict[str, Any]]]:
        """咬钩提示下面的按钮。"""
        return [[self._btn("拉线！", "/钓鱼 拉", style=4)]]

    def _event_rows(self, event_def: dict[str, Any]) -> list[list[dict[str, Any]]]:
        rows: list[list[dict[str, Any]]] = []
        for index, choice in enumerate(event_def.get("choices") or [], 1):
            rows.append([self._btn(f"{choice['label']}", f"/钓鱼 事件 {index}")])
        return rows

    # -------------------------------------------------------------------------
    # 随机小插曲
    # -------------------------------------------------------------------------

    def _maybe_start_event(self, player: dict[str, Any]) -> dict[str, Any] | None:
        """偶尔触发一次小插曲（触发条件不对外说明）。"""
        if player.get("event"):
            return None
        chance = _clamp(
            _safe_number(self.cfg.get("story_chance"), 0.06), 0.0, 1.0
        )
        if chance <= 0 or random.random() >= chance:
            return None
        total = sum(e["weight"] for e in RANDOM_EVENTS)
        point = random.uniform(0, total)
        acc = 0.0
        for event_def in RANDOM_EVENTS:
            acc += event_def["weight"]
            if point < acc:
                return event_def
        return RANDOM_EVENTS[-1]

    def _event_prompt(
        self, event_def: dict[str, Any], rows: bool = True
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """插曲的文案 + 按钮。"""
        lines = [f"❔ {event_def['text']}"]
        for index, choice in enumerate(event_def.get("choices") or [], 1):
            lines.append(f"　{index}. {choice['label']}　/钓鱼 事件 {index}")
        text = "\n".join(lines)
        return text, (self._event_rows(event_def) if rows else [])

    async def _cmd_event(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """处理小插曲的选择：/钓鱼 事件 1"""
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            current = player.get("event") or {}
            event_def = EVENT_BY_ID.get(current.get("id", ""))
            if not isinstance(event_def, dict):
                owner, ts = self._recent_events.get(self._session_key(event), ("", 0.0))
                if owner and owner != user_id and time.time() - ts < 600:
                    yield event.plain_result(
                        "🙅 这是别人的动静，你插不上手\n"
                        "　自己下竿的时候才会遇到属于你的小插曲"
                    )
                    return
                yield event.plain_result("🤔 眼下没什么需要你决定的事")
                return
            # 插曲放着不管会自己散掉（10 分钟）
            if int(time.time()) - _safe_int(current.get("ts"), 0, 0) > 600:
                player.pop("event", None)
                await self._save_player(player)
                yield event.plain_result("💨 你犹豫了一会儿，那点动静已经过去了")
                return

            choices = event_def.get("choices") or []
            idx = _to_int(a2, 0)
            if not (1 <= idx <= len(choices)):
                text, _ = self._event_prompt(event_def, rows=False)
                yield event.plain_result(text)
                return

            choice = choices[idx - 1]
            player.pop("event", None)
            lines = [f"　{choice['text']}"]

            # 结算：奖励都很轻，不影响经济
            good = random.random() < 0.65
            if good:
                lines.append(f"　{choice.get('good') or '……'}")
                gold = _safe_int(choice.get("gold"), 0, 0)
                if gold:
                    player["gold"] = _safe_int(player.get("gold"), 0, 0) + gold
                    lines.append(f"　💰 +{_fmt_gold(gold)}")
                bait_count = _safe_int(choice.get("bait"), 0, 0)
                if bait_count:
                    bait_id = "worm"
                    baits = player.setdefault("baits", {})
                    baits[bait_id] = _safe_int(baits.get(bait_id), 0, 0) + bait_count
                    lines.append(f"　{self._bait_label(bait_id)} ×{bait_count}")
                if choice.get("luck"):
                    gain = _safe_number(choice["luck"], 0.0)
                    player["luck_charges"] = _clamp(
                        _safe_number(player.get("luck_charges"), 0.0) + gain, 0.0, 2.0
                    )
                    lines.append(f"　🔮 下一竿手气：{_luck_stars(gain, 0.3)}")
                if choice.get("note"):
                    note = random.choice(BOTTLE_NOTES)
                    notes = player.setdefault("bottle_notes", [])
                    if note not in notes:
                        notes.append(note)
                        player["bottle_notes"] = notes[-30:]
                    lines.append(f"　📜 你记下了一句：{note}")
            else:
                lines.append(f"　{choice.get('idle') or '什么也没发生。'}")

            new_ach = self._check_achievements(player)
            saved = await self._save_player(player)
            yield event.plain_result("\n".join(lines))

    def _milestone_text(self, player: dict[str, Any]) -> str:
        """刚达到里程碑时返回文案并标记；否则返回空串。"""
        caught = _safe_int(player.get("total_caught"), 0, 0)
        shown = player.setdefault("milestones", [])
        if not isinstance(shown, list):
            shown = []
            player["milestones"] = shown
        if caught in MILESTONES and caught not in shown:
            shown.append(caught)
            return MILESTONES[caught]
        return ""

    def _update_best_records(
        self, player: dict[str, Any], catch: dict[str, Any]
    ) -> None:
        """更新「最佳渔获」记录：按鱼种品质各留一条最高的。

        这是玩家的长期目标之一——图鉴看「有没有」，最佳记录看「有多好」。
        """
        try:
            fish_id = catch.get("fish_id", "")
            rarity = _fish_rarity(fish_id)
            value = _instance_value(catch)
            records = player.setdefault("best_records", {})
            if not isinstance(records, dict):
                records = {}
                player["best_records"] = records
            old = records.get(rarity)
            if not isinstance(old, dict) or value > _safe_int(old.get("value"), 0, 0):
                records[rarity] = {
                    "fish_id": fish_id,
                    "variant": catch.get("variant"),
                    "quality": catch.get("quality", ""),
                    "value": value,
                    "ts": _safe_int(catch.get("ts"), 0, 0),
                }
        except Exception as e:
            logger.debug(f"更新最佳渔获记录失败：{e}")

    def _best_records_text(self, player: dict[str, Any]) -> list[str]:
        """渲染「最佳渔获」几行，供图鉴页展示。"""
        records = player.get("best_records") or {}
        if not isinstance(records, dict) or not records:
            return []
        lines = ["【最佳渔获】"]
        for rarity in reversed(RARITY_ORDER):  # 从高到低
            rec = records.get(rarity)
            if not isinstance(rec, dict):
                continue
            fish = FISH_BY_ID.get(rec.get("fish_id", ""))
            if fish is None:
                continue
            variant = rec.get("variant")
            vtag = ""
            if variant and variant in VARIANT_BY_ID:
                vcfg = VARIANT_BY_ID[variant]
                vtag = f"{vcfg['emoji']}{vcfg['name']}"
            quality = rec.get("quality", "")
            lines.append(
                f"　{self._rarity_name(rarity)}：{vtag}{fish['name']}"
                f"　{QUALITY_EMOJI.get(quality, '')}{quality}"
                f"　{_fmt_gold(rec.get('value', 0))} 金币"
            )
        return lines if len(lines) > 1 else []

    # -------------------------------------------------------------------------
    # 图鉴集齐的永久加成
    # -------------------------------------------------------------------------

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

    def _codex_mult_text(self, player: dict[str, Any]) -> str:
        mult = self._codex_mult(player)
        return f"+{(mult - 1) * 100:.0f}%" if mult > 1.0 else "无"

    # -------------------------------------------------------------------------
    # 群内排行榜（存一份轻量索引，避免遍历所有玩家）
    # -------------------------------------------------------------------------

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

    def _roll_cast_outcome(
        self, bait_id: str, can_loot: bool
    ) -> tuple[str, dict[str, Any] | None]:
        """这一竿的结果：``("fish", None)`` / ``("item", 杂物)`` / ``("nothing", None)``。

        判定顺序就是玩家直觉里的顺序（用户明确要求）：
        1. 先看有没有中鱼 —— 中了就是鱼，**不会被杂物抢走**，
           所以「上鱼率 == bait_hook_rates 里设置的值」；
        2. 没中鱼才看钩子上有没有带物件（``item_drop_chance``）；
        3. 都没有就是空手而归。

        ``can_loot=False``（完全免费的空钩）时不出物件，避免零成本白刷杂物。
        抽成独立方法是为了让三段概率可被单测稳定采样。
        """
        hook = self._hook_rate(bait_id)
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

    def _bait_label(self, bait_id: str) -> str:
        bait = self.baits.get(bait_id)
        if not bait:
            return "🪝空钩"
        return f"{bait.get('emoji', '')}{bait.get('name', bait_id)}"

    def _item_label(self, item_id: str) -> str:
        item = self.items.get(item_id)
        if not item:
            return item_id
        return f"{item.get('emoji', '')}{item.get('name', item_id)}"

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

    def _interaction_window(
        self, fish: dict[str, Any], weather: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """算出一条鱼的拉线窗口参数；不需要互动则返回 None。

        窗口时长、最佳点位、逃脱率都由鱼种的 ``diff``（难度）与 ``drift``（偏移）决定，
        所以**每种鱼的窗口期时间和位置都不一样**；天气会再整体缩放窗口与逃脱率。
        """
        rarity = fish["rarity"]
        if rarity not in self.interactive_rarities:
            return None

        diff = _clamp(_safe_number(fish.get("diff"), 0.5), 0.0, 1.0)
        drift = _clamp(_safe_number(fish.get("drift"), 0.0), -0.5, 0.5)
        window_mult = _clamp(
            _safe_number((weather or {}).get("window_mult"), 1.0), 0.3, 3.0
        )
        escape_mult = _clamp(
            _safe_number((weather or {}).get("escape_mult"), 1.0), 0.2, 3.0
        )

        w_min = int(self.cfg["window_min"])
        w_max = int(self.cfg["window_max"])
        # 难度越高 -> 越短的窗口
        scaled_min = max(2, int(round(w_min * (1.0 - 0.35 * diff) * window_mult)))
        scaled_max = max(
            scaled_min + 1, int(round(w_max * (1.0 - 0.40 * diff) * window_mult))
        )
        window = random.uniform(float(scaled_min), float(scaled_max))

        # 最佳点位：默认居中，按 drift 偏移，并留出安全边距
        half = self.cfg["sweet_spot_width"] / 2.0
        center = _clamp(0.5 + drift, half + 0.05, 1.0 - half - 0.05)

        base_escape = self.escape_map.get(rarity, 0.25)
        escape = _clamp(base_escape * (0.75 + 0.5 * diff) * escape_mult, 0.0, 0.95)

        return {
            "window": window,
            "center": center,
            "half": half,
            "escape": escape,
        }

    def _judge_pull(
        self, pos: float, spec: dict[str, Any]
    ) -> tuple[str, float, float, str]:
        """根据落点位置判定评价。纯函数，方便单测。

        ``pos`` 是 0~1 的落点（0 = 刚咬钩，1 = 窗口结束）。
        返回 ``(评价, 品质幸运加成, 逃脱率倍数, 标记 emoji)``。
        """
        center = _clamp(_safe_number(spec.get("center"), 0.5), 0.0, 1.0)
        half = _clamp(_safe_number(spec.get("half"), 0.17), 0.01, 0.5)
        drift = abs(_clamp(pos, 0.0, 1.0) - center)

        if drift <= half * 0.5:
            return (
                "完美",
                float(self.cfg["perfect_bonus"]),
                float(self.cfg["perfect_escape_factor"]),
                "🎯",
            )
        if drift <= half * 1.5:
            return "良好", float(self.cfg["good_bonus"]), 1.0, "👍"
        return "偏差", 0.0, float(self.cfg["edge_escape_factor"]), "😅"

    async def _play_minigame(
        self,
        event: AstrMessageEvent,
        user_id: str,
        fish: dict[str, Any],
        bait_id: str,
        spec: dict[str, Any],
    ):
        """拉线互动：提示 -> 等「拉」 -> 按落点评价。

        异步生成器：先 yield 若干提示消息，最后 yield 一个 dict 结果
        （异步生成器不能用带值的 return，所以用最后 yield dict 回传）。
        结果格式：``{"catch": 鱼实例 | None, "rating": "完美/良好/偏差/失败", ...}``
        """
        window = spec["window"]
        center = spec["center"]

        loop = asyncio.get_running_loop()
        future: asyncio.Future[float] = loop.create_future()
        # 先注册等待、再提示玩家，避免「提示已发出但还没开始监听」的竞态
        self._pending_pulls[user_id] = {
            "future": future,
            "session": self._session_key(event),
            "deadline": time.monotonic() + window,
        }

        tips = ["竿尖猛地弯了下去", "浮漂一下子沉进水里", "线被拽得吱吱响",
                "水面炸开一朵水花", "手里的竿传来一股大力"]
        hook_text = (
            f"{_fish_emoji(fish)} {fish['name']} 咬钩了！{random.choice(tips)}\n"
            f"⚡ {window:.0f} 秒内发 /钓鱼 拉（或点下面的按钮）"
        )
        async for reply in self._say(event, hook_text, self._pull_rows()):
            yield reply

        started = time.monotonic()
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=window)
            hit = True
            elapsed = time.monotonic() - started
        except asyncio.TimeoutError:
            hit = False
            elapsed = window
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        finally:
            self._pending_pulls.pop(user_id, None)

        if not hit:
            yield event.plain_result(
                f"💨 超时了——{fish['name']} 吐钩跑了（这一竿的鱼饵已经用掉了）\n"
                f"　下次在提示的时间内发 /钓鱼 拉 就能拉住它"
            )
            yield {"catch": None, "rating": "失败", "bonus": 0.0}
            return

        # 落点：0~1 的位置，越靠近 center 越准
        pos = _clamp(elapsed / window, 0.0, 1.0) if window > 0 else 0.0
        rating, bonus, factor, mark = self._judge_pull(pos, spec)

        escape = _clamp(spec["escape"] * factor, 0.0, 0.95)
        if random.random() < escape:
            yield event.plain_result(
                f"{mark} {rating}　但线一松——{fish['name']} 挣脱跑了"
            )
            yield {"catch": None, "rating": rating, "bonus": 0.0}
            return

        bait = self.baits.get(bait_id) or {}
        quality_mult = _roll_quality_mult(
            self.cfg["quality_weights"],
            bait_luck=_safe_number(bait.get("luck"), 0.0) + spec.get("weather_luck", 0.0),
            extra_luck=bonus,
        )
        catch = _new_instance(
            fish["id"],
            quality_mult,
            variant=spec.get("variant"),
            value_bonus=spec.get("rod_value_bonus", 0.0),
            location_mult=spec.get("location_mult", 1.0),
            codex_mult=spec.get("codex_mult", 1.0),
        )
        yield {"catch": catch, "rating": rating, "bonus": bonus, "mark": mark}

    async def _run_minigame(self, event, user_id, fish, bait_id, spec):
        """驱动 minigame，返回 (消息列表, 结果 dict)。"""
        messages: list[Any] = []
        result: dict[str, Any] | None = None
        async for item in self._play_minigame(event, user_id, fish, bait_id, spec):
            if isinstance(item, dict):
                result = item
            else:
                messages.append(item)
        if result is None:
            result = {"catch": None, "rating": "失败", "bonus": 0.0}
        return messages, result

    @staticmethod
    def _session_key(event: AstrMessageEvent) -> str:
        """当前会话标识，用于避免跨群误触发互动。"""
        try:
            return str(event.unified_msg_origin)
        except Exception:
            try:
                return f"{event.get_platform_name()}:{event.get_group_id()}"
            except Exception:
                return "unknown"

    def _resolve_pull(self, event: AstrMessageEvent) -> bool:
        """若该玩家正在等「拉」，唤醒等待。返回是否触发。"""
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            return False
        pending = self._pending_pulls.get(user_id)
        if not pending:
            return False
        session = pending.get("session")
        if session and session != self._session_key(event):
            return False
        future = pending.get("future")
        if future is None or future.done():
            return False
        future.set_result(time.monotonic())
        return True

    async def _do_cast(self, event: AstrMessageEvent, user_id: str, bait_name: str):
        """执行一次抛竿（含互动玩法）。"""
        broadcast_catch: dict[str, Any] | None = None
        cfg = self.cfg

        lock = self._lock_for(user_id)
        if lock.locked():
            yield event.plain_result("🎣 手上还捏着竿呢，先 /钓鱼 拉 或等它跑掉")
            return

        async with lock:
            player = await self._load_player(user_id)
            now = time.time()

            # 记录昵称，供排行榜展示
            try:
                name = event.get_sender_name()
                if isinstance(name, str) and name:
                    player["last_name"] = name[:24]
            except Exception:
                pass

            # 记录来源平台，便于排查「QQ 官方机器人」等适配问题
            try:
                player["last_platform"] = str(event.get_platform_name() or "")
                self._recent_platforms[user_id] = player["last_platform"]
            except Exception:
                pass

            # 首次操作时确定今日天气与鱼市行情（全天不变）
            if self._ensure_weather(player) or self._ensure_market(player):
                await self._save_player(player)

            # --- 选鱼饵 ---
            bait_id = "none"
            bait_note = ""
            if bait_name:
                matched = self._find_bait(bait_name)
                if matched is None:
                    names = "、".join(self.baits[b]["name"] for b in self._bait_list())
                    yield event.plain_result(
                        f"🤔 没有「{bait_name}」这种饵。可买：{names}"
                    )
                    return
                bait_id = matched
                if bait_id != "none":
                    owned = _safe_int((player.get("baits") or {}).get(bait_id), 0, 0)
                    if owned <= 0:
                        # 没货也不打断这一竿：直接用空钩，并说清楚怎么固定成空钩
                        bait_id = "none"
                        bait_note = (
                            f"🎒 {self._bait_label(matched)} 用完了，这一竿改用空钩"
                            f"（/钓鱼 商店 买 {self.baits[matched]['name']} 补货，"
                            f"或 /钓鱼 换饵 空钩 固定用空钩）"
                        )
            else:
                equipped = player.get("equipped_bait", "none")
                if (
                    isinstance(equipped, str)
                    and equipped in self.baits
                    and equipped != "none"
                ):
                    if _safe_int((player.get("baits") or {}).get(equipped), 0, 0) > 0:
                        bait_id = equipped
                    else:
                        # 用完就真的换掉：写进存档，别每竿都偷偷回退一次
                        bait_id = "none"
                        player["equipped_bait"] = "none"
                        bait_note = (
                            f"🎒 {self._bait_label(equipped)} 用完了，"
                            f"已自动换回空钩（/钓鱼 商店 买 {self.baits[equipped]['name']} "
                            f"可补货）"
                        )

            # --- 冷却 ---
            cooldown = int(cfg["cooldown_seconds"])
            if cooldown > 0:
                elapsed = now - _safe_int(player.get("last_fish_time"), 0, 0)
                remain = cooldown - elapsed
                if remain > 0:
                    yield event.plain_result(
                        f"⏳ 钓鱼冷却中：还要等 {int(remain) + 1} 秒"
                        f"（每次下竿间隔 {cooldown} 秒）"
                    )
                    return

            # --- 费用 ---
            # 鱼饵在下竿时只扣库存（买的时候已经付过钱），不再重复收饵钱；
            # 空钩下竿完全免费（fish_cost 默认 0，站长仍可在配置里开启钓费）。
            total_cost = max(0, _safe_int(cfg["fish_cost"], 0, 0))
            if total_cost and _safe_int(player.get("gold"), 0, 0) < total_cost:
                yield event.plain_result(
                    f"💸 下竿需要 {total_cost} 金币，你只有 "
                    f"{_fmt_gold(player.get('gold', 0))}。可 /钓鱼 签到 或 /钓鱼 卖"
                )
                return

            # --- 背包容量检查（限制无限囤货）---
            backpack_cap = _backpack_capacity(player, cfg)
            if len(player.get("inventory") or []) >= backpack_cap:
                yield event.plain_result(
                    f"🎒 背包满了（{backpack_cap}）！先 /钓鱼 卖 或 /钓鱼 水族馆 放，"
                    f"也可以 /钓鱼 商店 买 扩建背包"
                )
                return

            # --- 扣费 / 扣饵（各一次）---
            if total_cost:
                player["gold"] = _safe_int(player.get("gold"), 0, 0) - total_cost
            if bait_id != "none":
                baits = player.setdefault("baits", {})
                baits[bait_id] = max(0, _safe_int(baits.get(bait_id), 0, 0) - 1)

            # ---- 这一竿的结果：中鱼 / 钩上物件 / 空手而归（一竿只出一样）----
            # 顺序见 _roll_cast_outcome：先判中鱼（上鱼率==配置的咬钩率），
            # 没中鱼才可能钩上杂物；完全免费的空钩不出杂物。
            outcome, drop = self._roll_cast_outcome(
                bait_id, bait_id != "none" or total_cost > 0
            )
            if outcome != "fish":
                player["last_fish_time"] = int(now)
                await self._save_player(player)
                if outcome == "item" and drop is not None:
                    player = await self._load_player(user_id)
                    drop_text = await self._apply_collectible(player, drop, user_id)
                    lines = [t for t in (drop_text, bait_note) if t]
                    if lines:
                        async for reply in self._say(
                            event, "\n".join(lines), self._cast_rows()
                        ):
                            yield reply
                    # 这一竿没钓到鱼：不计渔获、不进图鉴、不刷新最佳纪录、不播报
                    async for reply in self._maybe_trigger_story(event, user_id):
                        yield reply
                    return
                tip = (
                    "🪝 空钩在水里漂了半天，鱼碰了碰就游走了"
                    if bait_id == "none"
                    else f"🎣 咬了一口又吐掉了——{self._bait_label(bait_id)} 白搭了"
                )
                cheap = min(
                    (b for b in self._bait_list() if b != "none"),
                    key=lambda b: self.baits[b].get("price", 0),
                    default=None,
                )
                if bait_id == "none" and cheap:
                    tail = (
                        "　挂个鱼饵上钩率会高很多：/钓鱼 商店 买 "
                        f"{self.baits[cheap]['name']}"
                        f"（{self.baits[cheap]['price']} 金/个）"
                    )
                else:
                    tail = "　换个更对口的饵，或者挑鱼多的钓点再试"
                yield event.plain_result(f"{tip}\n{tail}")
                return

            # ---- 抽鱼种（按当前钓点的鱼池 + 今日天气）----
            loc = self._location(player)
            rod = self._rod(player)
            weather = self._weather(player)
            fish = self._roll_species(bait_id, loc["id"], weather)
            # 幸运值 = 洗髓丹储备 + 鱼竿幸运，本次抛竿读一次，收尾时消耗储备
            luck = _safe_number(player.get("luck_charges"), 0.0)
            gear_luck = _safe_number(rod.get("luck_bonus"), 0.0)
            player["last_fish_time"] = int(now)
            await self._save_player(player)

            spec = self._interaction_window(fish, weather)
            # 变异只在上钩瞬间掷一次；命中后普通鱼也会变成「惊喜」
            variant = self._roll_variant()
            weather_luck = _safe_number((weather or {}).get("luck"), 0.0)
            rod_value = _safe_number(rod.get("value_bonus"), 0.0)
            loc_value = _safe_number(loc.get("value_mult"), 1.0)
            codex_mult = self._codex_mult(player)

            # 低档鱼直接上钩；高档鱼走拉线互动
            if spec is None:
                bait = self.baits.get(bait_id) or {}
                quality_mult = _roll_quality_mult(
                    self.cfg["quality_weights"],
                    bait_luck=_safe_number(bait.get("luck"), 0.0)
                    + gear_luck
                    + weather_luck,
                    extra_luck=luck,
                )
                catch = _new_instance(
                    fish["id"],
                    quality_mult,
                    value_bonus=rod_value,
                    location_mult=loc_value,
                    variant=variant,
                    codex_mult=codex_mult,
                )
                rating = None
            else:
                # 把天气/装备/图鉴/变异信息带进互动，供拉线成功后造鱼使用
                spec = dict(spec)
                spec["variant"] = variant
                spec["weather_luck"] = weather_luck
                spec["rod_value_bonus"] = rod_value
                spec["location_mult"] = loc_value
                spec["codex_mult"] = codex_mult
                messages, result = await self._run_minigame(
                    event, user_id, fish, bait_id, spec
                )
                for message in messages:
                    yield message
                player = await self._load_player(user_id)
                catch = result.get("catch")
                rating = result.get("rating")

            # 消耗幸运储备（无论成功与否，抛竿即用掉）
            player["luck_charges"] = 0.0
            await self._save_player(player)

            if catch is None:
                # 鱼跑了：扣费已在前面完成（饵已经消耗掉），
                # 但「饵用完了」这类提示必须照说，否则玩家只看到掉钱。
                if bait_note:
                    yield event.plain_result(bait_note)
                return

            # 拉线技巧计数（用于成就）
            if rating == "完美":
                player["perfect_pulls"] = (
                    _safe_int(player.get("perfect_pulls"), 0, 0) + 1
                )
            elif rating == "偏差":
                player["clutch_wins"] = _safe_int(player.get("clutch_wins"), 0, 0) + 1

            broadcast_catch = catch
            result_text = self._format_result(
                player, catch, bait_id, total_cost, rating
            )
            if bait_note:
                result_text += f"\n{bait_note}"
            async for reply in self._say(event, result_text, self._cast_rows()):
                yield reply
            await self._finalize_catch(
                event,
                player,
                catch,
                user_id,
                perfect=rating == "完美",
                bait_id=bait_id,
            )

            # --- 偶尔来一段小插曲（触发条件不对外说明）---
            async for reply in self._maybe_trigger_story(event, user_id):
                yield reply

        # --- 群播报（锁外）：变异体 / 传说 / 神话都值得播报 ---
        if cfg["enable_group_broadcast"] and broadcast_catch is not None:
            if broadcast_catch.get("variant") or _fish_rarity(
                broadcast_catch.get("fish_id", "")
            ) in ("传说", "神话"):
                await self._broadcast(event, broadcast_catch)

    async def _maybe_trigger_story(self, event: AstrMessageEvent, user_id: str):
        """抛竿收尾：偶尔来一段小插曲（触发条件不对外说明）。

        钓鱼与「钓上杂物」两条路径都要走这里，所以单独抽出来，避免复制粘贴。
        """
        player = await self._load_player(user_id)
        story = self._maybe_start_event(player)
        if story is None:
            return
        player["event"] = {"id": story["id"], "ts": int(time.time())}
        self._recent_events[self._session_key(event)] = (user_id, time.time())
        await self._save_player(player)
        text, rows = self._event_prompt(story)
        async for reply in self._say(event, text, rows):
            yield reply

    def _format_result(
        self,
        player: dict[str, Any],
        catch: dict[str, Any],
        bait_id: str,
        total_cost: int,
        rating: str | None,
    ) -> str:
        """上鱼结果，尽量简短。"""
        head = f"🎣 {_instance_line(catch)}"
        if rating:
            mark = {"完美": "🎯", "良好": "👍", "偏差": "😅"}.get(rating, "")
            head = f"{mark}{rating}！{head}"
        fish = FISH_BY_ID.get(catch.get("fish_id", ""))
        if fish and fish["rarity"] in ("传说", "神话"):
            head += f"　{fish['flavor']}"
        lines = [head, f"　{_attrs_line(catch)}　余额 {_fmt_gold(player.get('gold', 0))}"]
        return "\n".join(lines)

    async def _finalize_catch(
        self,
        event: AstrMessageEvent,
        player: dict[str, Any],
        catch: dict[str, Any],
        user_id: str,
        perfect: bool = False,
        bait_id: str = "none",
    ) -> None:
        """把渔获写进背包 / 图鉴 / 成就并保存。"""
        try:
            player.setdefault("inventory", []).append(catch)
            player["total_caught"] = _safe_int(player.get("total_caught"), 0, 0) + 1

            # 图鉴：变异体是独立条目（fish_id#variant）
            variant = catch.get("variant")
            codex_key = _codex_key(catch["fish_id"], variant)
            collection = player.setdefault("collection", {})
            entry = collection.get(codex_key)
            if not isinstance(entry, dict):
                entry = {"count": 0, "best_value": 0, "first_ts": catch["ts"]}
                collection[codex_key] = entry
            entry["count"] = _safe_int(entry.get("count"), 0, 0) + 1
            entry["best_value"] = max(
                _safe_int(entry.get("best_value"), 0, 0), _instance_value(catch)
            )
            if not _safe_int(entry.get("first_ts"), 0, 0):
                entry["first_ts"] = catch["ts"]

            # 变异计数
            if variant:
                variants = player.setdefault("variants", {})
                variants[variant] = _safe_int(variants.get(variant), 0, 0) + 1

            # 最佳渔获记录（长期目标：不断刷新自己的纪录）
            self._update_best_records(player, catch)

            # 奇遇事件（低概率小惊喜，不影响平衡）
            # 先结算彩蛋再查成就，这样「意外之喜」能在当竿立刻解锁
            egg = self._roll_easter_egg()
            egg_text = self._apply_easter_egg(player, egg, bait_id) if egg else ""
            if egg_text:
                coll = player.setdefault("collectibles", {})
                key = f"egg_{egg['id']}"
                coll[key] = _safe_int(coll.get(key), 0, 0) + 1

            new_achievements = self._check_achievements(player, catch)
            if perfect and "perfect_pull" not in player["achievements"]:
                player["achievements"].append("perfect_pull")
                new_achievements.append(ACHIEVEMENTS["perfect_pull"])
            if variant and "first_variant" not in player["achievements"]:
                player["achievements"].append("first_variant")
                new_achievements.append(ACHIEVEMENTS["first_variant"])
            if (
                variant == "prismatic"
                and "prismatic_one" not in player["achievements"]
            ):
                player["achievements"].append("prismatic_one")
                new_achievements.append(ACHIEVEMENTS["prismatic_one"])

            # 里程碑：整十/整百竿的额外小奖励文案
            milestone = self._milestone_text(player)

            saved = await self._save_player(player)
            # 更新群内排行榜索引
            await self._touch_leaderboard(player)

            if new_achievements:
                await event.send(
                    event.plain_result("🎉 " + "；".join(new_achievements))
                )
            if egg_text:
                await event.send(event.plain_result(egg_text))
            if milestone:
                await event.send(event.plain_result(milestone))
            if not saved:
                await event.send(
                    event.plain_result(
                        "⚠️ 数据保存失败，这条记录可能不会保留\n"
                        "　请把这条消息发给管理员核对（日志里有详情）"
                    )
                )
        except Exception as e:
            logger.error(f"写入渔获失败（玩家 {user_id}）：{e}", exc_info=True)

    async def _apply_collectible(
        self, player: dict[str, Any], drop: dict[str, Any], user_id: str
    ) -> str:
        """处理钓上来的杂物：记账、漂流瓶开纸条、值钱的直接折算金币。

        现在杂物与鱼互斥（一竿只出一样），所以文案要明确「这一竿上来的
        不是鱼」，别让玩家以为同时还钓到了鱼。
        """
        try:
            items = player.setdefault("items", {})
            items[drop["id"]] = _safe_int(items.get(drop["id"]), 0, 0) + 1
            coll = player.setdefault("collectibles", {})
            coll[drop["id"]] = _safe_int(coll.get(drop["id"]), 0, 0) + 1

            lines = [
                f"{drop['emoji']} 钩子空了，倒是带上来一个 {drop['name']}（这一竿没有鱼）",
                f"　{drop['desc']}　已收进杂物收藏",
            ]

            if drop["id"] == "drift_bottle":
                if random.random() < float(self.cfg["bottle_note_chance"]):
                    note = random.choice(BOTTLE_NOTES)
                    notes = player.setdefault("bottle_notes", [])
                    if note not in notes:
                        notes.append(note)
                        player["bottle_notes"] = notes[-30:]
                        lines.append(f"📜 瓶里有张纸条：{note}")
                    else:
                        lines.append(f"📜 又是这张纸条：{note}")
                else:
                    lines.append("📜 摇了摇……瓶子是空的。")
            elif _safe_int(drop.get("value"), 0, 0) >= 25:
                # 值钱的杂物直接折算成金币，省得再手动卖
                gold = int(drop["value"])
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + gold
                lines.append(f"💰 这东西值钱，直接换了 {_fmt_gold(gold)} 金币")

            new_ach = self._check_achievements(player)
            await self._save_player(player)
            if new_ach:
                lines.append("🎉 " + "；".join(new_ach))
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"处理杂物失败（玩家 {user_id}）：{e}", exc_info=True)
            return ""

    async def _broadcast(self, event: AstrMessageEvent, catch: dict[str, Any]) -> None:
        try:
            sender = event.get_sender_name() or str(event.get_sender_id())
            await event.send(
                event.plain_result(f"📢 {sender} 钓到了 {_instance_line(catch)}！")
            )
        except Exception as e:
            logger.warning(f"群播报失败：{e}")

    # -------------------------------------------------------------------------
    # 每日订单
    # -------------------------------------------------------------------------

    def _order_rarities(self, level: int) -> tuple[str, ...]:
        """按等级取当前可出现的订单品质。"""
        result: tuple[str, ...] = ("常见",)
        for lv, rarities in ORDER_RARITY_BY_LEVEL:
            if level >= lv:
                result = rarities
        return result

    def _roll_orders(self, level: int) -> list[dict[str, Any]]:
        """生成一批订单。越贵的鱼要得越少。"""
        rarities = set(self._order_rarities(level))
        candidates = [f for f in FISH_POOL if f["rarity"] in rarities]
        if not candidates:
            candidates = list(FISH_POOL)
        # 同一批订单不出现重复鱼种
        unique: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for fish in candidates:
            if fish["id"] in seen_ids:
                continue
            seen_ids.add(fish["id"])
            unique.append(fish)
        candidates = unique or candidates
        count = min(int(self.cfg["order_count"]), len(candidates))
        picked = random.sample(candidates, count)

        mult = float(self.cfg["order_reward_mult"])
        orders: list[dict[str, Any]] = []
        for fish in picked:
            if fish["value"] <= 10:
                need = random.randint(3, 6)
            elif fish["value"] <= 60:
                need = random.randint(2, 4)
            elif fish["value"] <= 300:
                need = random.randint(1, 2)
            else:
                need = 1
            unit = max(1, int(fish["value"] * 1.3))  # 参考单价（含品质均值）
            orders.append(
                {
                    "fish_id": fish["id"],
                    "need": need,
                    "have": 0,
                    "reward": max(1, int(unit * need * mult)),
                    "done": False,
                }
            )
        return orders

    def _next_order_ts(self, now: int | None = None) -> int:
        """下一批订单出现的时间戳（几小时一批，间隔在配置里可调）。"""
        now = int(now if now is not None else time.time())
        low = max(1, _safe_int(self.cfg.get("order_refresh_min_hours"), 3, 1))
        high = max(low, _safe_int(self.cfg.get("order_refresh_max_hours"), 6, 1))
        return now + random.randint(low * 3600, high * 3600)

    def _order_wait_text(self, player: dict[str, Any]) -> str:
        """还有多久刷新（给玩家一个明确的等待预期）。"""
        now = int(time.time())
        next_ts = _safe_int(player.get("order_next_ts"), 0, 0)
        if next_ts <= now:
            return "随时会来新单"
        minutes = max(1, (next_ts - now) // 60)
        if minutes < 60:
            return f"约 {minutes} 分钟后刷新"
        hours, rest = minutes // 60, minutes % 60
        if rest < 5:
            return f"约 {hours} 小时后刷新"
        return f"约 {hours} 小时 {rest} 分钟后刷新"

    def _ensure_orders(self, player: dict[str, Any]) -> bool:
        """确保当前这批订单已生成（不定时刷新）。

        刷新规则：到了 `order_next_ts` 就换一批新的，没做完的旧单直接过期。
        返回 True 表示数据有变化需要保存。
        """
        now = int(time.time())
        next_ts = _safe_int(player.get("order_next_ts"), 0, 0)
        has_orders = bool(player.get("orders"))
        if has_orders and next_ts > now:
            return False
        player["orders"] = self._roll_orders(_player_level(player))
        player["order_next_ts"] = self._next_order_ts(now)
        player["order_date"] = self._today_text()  # 只用于展示「这是哪天接的单」
        return True

    async def _cmd_orders(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str
    ):
        """订单：/钓鱼 订单 ｜ /钓鱼 订单 交 1（不定时刷新，交过就不能再交）"""
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            level = _player_level(player)
            changed = self._ensure_orders(player)

            if level < int(self.cfg["order_unlock_level"]):
                if changed:
                    await self._save_player(player)
                yield event.plain_result(
                    f"📋 订单需要 {int(self.cfg['order_unlock_level'])} 级"
                    f"（你现在 {level} 级，多钓鱼吧）"
                )
                return

            orders: list[dict[str, Any]] = player.get("orders") or []
            sub = (a2 or "").strip().lower()
            # 少打空格的容错：`交1`
            peeled_order = self._peel_action(sub, ORDER_ACTIONS)
            if peeled_order:
                sub, glued = peeled_order
                a3 = f"{glued} {a3}".strip()

            # ---- 提交订单（支持批量：交 1 2 / 交 1-3 / 交 全部）----
            if sub in ("交", "提交", "submit"):
                idxs = self._parse_indices(a3, orders)
                if not idxs:
                    yield event.plain_result(
                        f"📖 /钓鱼 订单 交 <序号>　1~{len(orders)}，"
                        f"支持 1 2 3 / 1-3 / 全部"
                    )
                    return

                done_lines: list[str] = []
                total_reward = 0
                short: list[str] = []
                already: list[int] = []
                inventory: list[dict[str, Any]] = player.get("inventory") or []

                for idx in idxs:
                    order = orders[idx - 1]
                    fish = FISH_BY_ID.get(order.get("fish_id", ""))
                    if fish is None:
                        continue
                    if order.get("done"):
                        already.append(idx)
                        continue
                    need = _safe_int(order.get("need"), 1, 1)
                    # 优先拿最便宜的个体交单，把好鱼留给自己
                    group = sorted(
                        [x for x in inventory if x.get("fish_id") == fish["id"]],
                        key=_instance_value,
                    )
                    if len(group) < need:
                        short.append(
                            f"{fish['name']} 还差 {need - len(group)} 条"
                        )
                        continue
                    used_ids = {id(x) for x in group[:need]}
                    inventory = [
                        x for x in inventory if id(x) not in used_ids
                    ]
                    reward = _safe_int(order.get("reward"), 1, 1)
                    total_reward += reward
                    order["done"] = True
                    player["total_orders"] = (
                        _safe_int(player.get("total_orders"), 0, 0) + 1
                    )
                    done_lines.append(
                        f"　{idx}. {fish['name']} ×{need} → {_fmt_gold(reward)}"
                    )

                if not done_lines:
                    msg = "🤔 这些订单都没交成"
                    if already:
                        msg += (
                            "　已经交过："
                            + "、".join(str(i) for i in already)
                        )
                    if short:
                        msg += "\n　鱼不够：" + "；".join(short)
                    yield event.plain_result(msg)
                    return

                player["inventory"] = inventory
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + total_reward

                lines = [f"📦 交单 {len(done_lines)} 单 → {_fmt_gold(total_reward)} 金币"]
                lines.extend(done_lines)
                lines.append(f"💰 余额 {_fmt_gold(player['gold'])}")
                if already:
                    lines.append(
                        f"　（跳过已经交过的 {'、'.join(str(i) for i in already)}）"
                    )
                if short:
                    lines.append("　（鱼不够：" + "；".join(short) + "）")
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            # ---- 查看订单 ----
            if changed:
                await self._save_player(player)
            inventory = player.get("inventory") or []
            held: dict[str, int] = {}
            for inst in inventory:
                fid = inst.get("fish_id")
                if isinstance(fid, str):
                    held[fid] = held.get(fid, 0) + 1

            lines = [
                f"📋 当前订单（{self._order_wait_text(player)}，"
                f"过期会换一批）"
            ]
            for i, order in enumerate(orders, start=1):
                fish = FISH_BY_ID.get(order.get("fish_id", ""))
                if fish is None:
                    continue
                need = _safe_int(order.get("need"), 1, 1)
                have = min(held.get(fish["id"], 0), need)
                mark = "✅" if order.get("done") else ("📦" if have >= need else "⏳")
                lines.append(
                    f"{i}.{mark}{_fish_emoji(fish)}{fish['name']} "
                    f"{have}/{need}　→ {_fmt_gold(order.get('reward', 0))}"
                )
            lines.append("💡 /钓鱼 订单 交 1 提交（支持 交 1 2 3 / 交 全部）")
            yield event.plain_result("\n".join(lines))

    # -------------------------------------------------------------------------
    # 钓点 / 鱼竿 / 背包
    # -------------------------------------------------------------------------

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

    async def _cmd_locations(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str
    ):
        """钓点：/钓鱼 钓点 ｜ /钓鱼 钓点 解锁 <名称> ｜ /钓鱼 去 <名称>

        解锁规则：等级达标 + 上一个钓点图鉴开到 80%（隐藏生物不计） + 一次性金币，三样齐了才能前往。
        """
        sub = (a2 or "").strip().lower()
        # 少打空格的容错：`解锁湖泊` / `去湖泊`
        peeled_loc = self._peel_action(sub, LOCATION_ACTIONS)
        if peeled_loc:
            sub, glued = peeled_loc
            a3 = f"{glued} {a3}".strip()
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            level = _player_level(player)
            unlocked: list[str] = player.get("locations") or []
            current = player.get("current_location", DEFAULT_LOCATION)

            # ---- 前往 ----
            if sub in ("去", "前往", "go"):
                target = self._find_location(a3 or a2)
                if target is None:
                    yield event.plain_result("🤔 没有这个钓点，/钓鱼 钓点 看看")
                    return
                if target["id"] not in unlocked:
                    yield event.plain_result(
                        f"🔒 {target['name']} 还没解锁，先 /钓鱼 钓点 解锁 {target['name']}"
                    )
                    return
                if target["id"] == current:
                    yield event.plain_result(f"📍 你已经在 {target['name']} 了")
                    return
                player["current_location"] = target["id"]
                saved = await self._save_player(player)
                lines = [
                    f"🚶 前往 {target['emoji']}{target['name']}　"
                    f"价值×{target['value_mult']:.2f}"
                ]
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            # ---- 解锁 ----
            if sub in ("解锁", "unlock", "开"):
                target = self._find_location(a3 or a2)
                if target is None:
                    yield event.plain_result("🤔 没有这个钓点")
                    return
                if target["id"] in unlocked:
                    yield event.plain_result(f"✅ {target['name']} 已解锁")
                    return
                # 解锁条件：等级 + 上一个钓点图鉴 80% + 一次性金币
                ratio = _clamp(
                    _safe_number(self.cfg.get("location_codex_gate"), 0.8), 0.0, 1.0
                )
                prev_id = self._prev_location_id(target["id"])
                prev_cfg = self.location_by_id.get(prev_id) or {} if prev_id else {}
                got = need = 0
                if prev_id:
                    got, need = self._location_codex_progress(player, prev_id)
                codex_need = int(need * ratio + 0.999)
                price = _safe_int(target.get("gold_gate"), 0, 0)
                gold_now = _safe_int(player.get("gold"), 0, 0)
                lacks: list[str] = []
                if prev_id and need and got < codex_need:
                    lacks.append(
                        f"图鉴「{prev_cfg.get('name', prev_id)}」{got}/{need}"
                        f"（需 {codex_need} 种，差 {codex_need - got} 种）"
                        f"　/钓鱼 图鉴 {prev_cfg.get('name', prev_id)}"
                    )
                if price > gold_now:
                    lacks.append(
                        f"金币 {_fmt_gold(price)}（你有 {_fmt_gold(gold_now)}，"
                        f"差 {_fmt_gold(price - gold_now)}）"
                    )
                if level < target["level_gate"]:
                    lacks.append(f"等级 {target['level_gate']} 级（你现在 {level} 级）")
                if lacks:
                    yield event.plain_result(
                        f"🔒 还不能去 {target['emoji']}{target['name']}，还差：\n"
                        + "\n".join(f"　· {x}" for x in lacks)
                        + "\n　（图鉴里的隐藏生物不算数）"
                    )
                    return
                player["gold"] = gold_now - price
                unlocked.append(target["id"])
                player["locations"] = unlocked
                player["current_location"] = target["id"]
                # ⚠️ 先更新 locations 再检查成就，否则「解锁第二个钓点」这类
                # 依赖 locations 数量的成就要等到下一次操作才解锁。
                lines = [
                    f"🗺️ 解锁并前往 {target['emoji']}{target['name']}！",
                    f"　价值×{target['value_mult']:.2f}　"
                    f"💰 余额 {_fmt_gold(player['gold'])}",
                ]
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            # ---- 查看 ----
            lines = [f"🗺️ 钓点　当前 {self._location_label(player)}　等级 {level}"]
            for loc in sorted(
                self.locations,
                key=lambda l: (_safe_int(l.get("level_gate"), 1, 1),
                               _safe_int(l.get("gold_gate"), 0, 0)),
            ):
                is_unlocked = loc["id"] in unlocked
                here = "📍" if loc["id"] == current else "　"
                if is_unlocked:
                    lines.append(
                        f"{here}{loc['emoji']}{loc['name']}　×{loc['value_mult']:.2f}"
                        f"　{loc['desc']}"
                    )
                else:
                    prev_id = self._prev_location_id(loc["id"])
                    if prev_id:
                        prev_cfg = self.location_by_id.get(prev_id) or {}
                        got, need = self._location_codex_progress(player, prev_id)
                        ratio = _clamp(
                            _safe_number(self.cfg.get("location_codex_gate"), 0.8),
                            0.0,
                            1.0,
                        )
                        gate_need = int(need * ratio + 0.999)
                        mark = "✅" if got >= gate_need else "🔒"
                        lines.append(
                            f"{mark}{loc['emoji']}{loc['name']}　图鉴 "
                            f"{got}/{need}（需 {gate_need}）"
                            f"　{_fmt_gold(loc['gold_gate'])}金"
                            f"　{loc['level_gate']}级"
                        )
                    else:
                        lines.append(f"🔒{loc['emoji']}{loc['name']}")
            lines.append("💡 点按钮看图鉴，或写：/钓鱼 去 <钓点名>")
            async for reply in self._say(event, "\n".join(lines), self._location_rows()):
                yield reply

    async def _cmd_rods(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str
    ):
        """鱼竿：/钓鱼 鱼竿 ｜ 买 <名称> ｜ 用 <名称>"""
        sub = (a2 or "").strip().lower()
        # 少打空格的容错：`买碳素竿` / `用碳素竿`
        peeled_rod = self._peel_action(sub, ROD_ACTIONS)
        if peeled_rod:
            sub, glued = peeled_rod
            a3 = f"{glued} {a3}".strip()
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            owned: list[str] = player.get("rods") or [DEFAULT_ROD]
            equipped = player.get("equipped_rod", DEFAULT_ROD)

            if sub in ("买", "购买", "buy"):
                rod = self._find_rod(a3 or a2)
                if rod is None:
                    yield event.plain_result("🤔 没有这款鱼竿")
                    return
                if rod["id"] in owned:
                    yield event.plain_result(f"✅ 你已经有 {rod['name']} 了")
                    return
                price = int(rod["price"])
                if _safe_int(player.get("gold"), 0, 0) < price:
                    yield event.plain_result(
                        f"💸 {rod['name']} 需要 {_fmt_gold(price)} 金币，"
                        f"你只有 {_fmt_gold(player.get('gold', 0))}"
                    )
                    return
                player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                owned.append(rod["id"])
                player["rods"] = owned
                player["equipped_rod"] = rod["id"]
                lines = [
                    f"🎣 买到 {rod['emoji']}{rod['name']}！已自动装备",
                    f"　价值+{rod['value_bonus']:.0%}　手气{_luck_stars(rod['luck_bonus'], 0.2)}"
                    f"　💰 {_fmt_gold(player['gold'])}",
                ]
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            if sub in ("用", "装备", "换", "use", "equip"):
                rod = self._find_rod(a3 or a2)
                if rod is None:
                    yield event.plain_result("🤔 没有这款鱼竿")
                    return
                if rod["id"] not in owned:
                    yield event.plain_result(f"🎒 你还没买 {rod['name']}")
                    return
                player["equipped_rod"] = rod["id"]
                saved = await self._save_player(player)
                lines = [f"✅ 已装备 {self._rod_label(player)}"]
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            lines = [f"🎣 鱼竿　当前 {self._rod_label(player)}"]
            for rod in self.rods:
                here = "📍" if rod["id"] == equipped else "　"
                tag = "已拥有" if rod["id"] in owned else f"{_fmt_gold(rod['price'])}金"
                lines.append(
                    f"{here}{rod['emoji']}{rod['name']}　{tag}　"
                    f"价值+{rod['value_bonus']:.0%}　手气{_luck_stars(rod['luck_bonus'], 0.2)}"
                )
            lines.append("💡 /钓鱼 鱼竿 买 <名称> ｜ /钓鱼 鱼竿 用 <名称>")
            yield event.plain_result("\n".join(lines))

    async def _cmd_backpack_upgrade(self, event: AstrMessageEvent, user_id: str):
        """背包扩容（公开入口，自己加锁）。"""
        async with self._lock_for(user_id):
            async for result in self._do_backpack_upgrade(event, user_id):
                yield result

    async def _do_backpack_upgrade(self, event: AstrMessageEvent, user_id: str):
        """背包扩容的实际逻辑。

        ⚠️ 调用方必须**已经持有该玩家的锁**。这是为了避免
        「/钓鱼 商店 扩容」转发时重复获取同一把 asyncio.Lock 造成自死锁
        （asyncio.Lock 不可重入，我们真的踩过这个坑）。
        """
        player = await self._load_player(user_id)
        owned = player.get("backpack_slots") or []
        if not isinstance(owned, list):
            owned = []
        capacity = _backpack_capacity(player, self.cfg)

        nxt = next(
            (i for i in range(len(self.backpack_upgrades)) if i not in owned), None
        )
        if nxt is None:
            yield event.plain_result(f"🎒 背包已扩到最大（{capacity}）")
            return

        up = self.backpack_upgrades[nxt]
        price = int(up.get("price", 0))
        if _safe_int(player.get("gold"), 0, 0) < price:
            yield event.plain_result(
                f"💸 扩容 +{up['add']} 需要 {_fmt_gold(price)} 金币，"
                f"你只有 {_fmt_gold(player.get('gold', 0))}"
            )
            return
        player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
        owned.append(nxt)
        player["backpack_slots"] = owned
        saved = await self._save_player(player)
        lines = [
            f"🎒 背包扩容 +{up['add']}！容量 {capacity} → "
            f"{_backpack_capacity(player, self.cfg)}",
            f"💰 余额 {_fmt_gold(player['gold'])}",
        ]
        if not saved:
            lines.append("⚠️ 保存失败")
        yield event.plain_result("\n".join(lines))

    async def _cmd_today(self, event: AstrMessageEvent, user_id: str):
        """今日天气 + 鱼市行情 + 图鉴加成，一眼看完今天的看点。"""
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            changed = self._ensure_weather(player) | self._ensure_market(player)
            if changed:
                await self._save_player(player)

            weather = self._weather(player)
            lines = [f"📅 今日 · {player.get('weather_date') or self._today_text()}"]
            if weather:
                # 概率/倍率一律不写给玩家看，只留味道对得上的定性描述
                lines.append(
                    f"{weather['emoji']} {weather['name']}　{weather['desc']}"
                )
                lines.append(
                    f"　今日水情：{_weather_hint(weather)}"
                )
            else:
                lines.append("（天气系统未启用）")

            market = self._market_label(player)
            if market:
                lines.append(f"📈 今日高价：{market}")
                lines.append("　卖这些鱼能多赚不少，行情每天刷新")
            else:
                lines.append("📈 今日无特别行情")

            lines.append(f"📕 图鉴集齐加成：{self._codex_mult_text(player)}")
            completed = self._codex_completed_rarities(player)
            if completed:
                lines.append(
                    "　已集齐：" + "、".join(self._rarity_name(r) for r in completed)
                )
            yield event.plain_result("\n".join(lines))

    async def _cmd_leaderboard(self, event: AstrMessageEvent, user_id: str, a2: str):
        """群内排行榜：/钓鱼 排行 [金币|图鉴|收获|最贵]"""
        index = await self.get_kv_data(self._leaderboard_key(), {})
        if not isinstance(index, dict) or not index:
            yield event.plain_result("📊 还没有排行数据，先去 /钓鱼 抛几竿吧")
            return

        key = (a2 or "").strip().lower()
        if key in ("金币", "gold", "钱"):
            field, title, unit = "gold", "金币", "金币"
        elif key in ("图鉴", "种类", "kinds", "collect"):
            field, title, unit = "kinds", "图鉴种类", "种"
        elif key in ("最贵", "单条", "best"):
            field, title, unit = "best", "单条最贵", "金币"
        else:
            field, title, unit = "caught", "累计钓获", "条"

        rows = []
        for uid, entry in index.items():
            if not isinstance(entry, dict):
                continue
            value = _safe_int(entry.get(field), 0, 0)
            if value <= 0:
                continue
            name = entry.get("name") or uid
            rows.append((value, str(name), str(uid), entry))
        if not rows:
            yield event.plain_result(f"📊 还没有「{title}」的数据")
            return

        rows.sort(key=lambda r: -r[0])
        me = str(user_id)
        my_rank = next((i for i, r in enumerate(rows, 1) if r[2] == me), None)

        lines = [f"🏆 本群排行榜 · {title}"]
        for i, (value, name, uid, entry) in enumerate(rows[:10], 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i:>2}.")
            mark = " ←你" if uid == me else ""
            extra = ""
            if field == "best":
                fish = FISH_BY_ID.get(entry.get("best_fish", ""))
                if fish:
                    extra = f"（{fish['name']}）"
            lines.append(f"{medal} {name[:12]}　{_fmt_gold(value)} {unit}{extra}{mark}")
        if my_rank is None:
            lines.append("　你还没有上榜，加油！")
        elif my_rank > 10:
            lines.append(f"　你的排名：第 {my_rank} 名")
        lines.append("💡 /钓鱼 排行 金币｜图鉴｜最贵")
        yield event.plain_result("\n".join(lines))

    async def _cmd_lock(self, event: AstrMessageEvent, user_id: str, *rest):
        """锁定/解锁：/钓鱼 锁定 1 2 ｜ /钓鱼 解锁 1

        锁定的鱼不会被「/钓鱼 卖」或「/钓鱼 卖光光」卖掉，避免手滑。
        序号之后可能还有更多参数（形参只声明到 a3），所以额外从原始消息尾部补齐。
        """
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            inventory: list[dict[str, Any]] = player.get("inventory") or []
            ordered = sorted(inventory, key=_sort_key)

            # 序号统一交给 _parse_indices（支持 "1 2 3" / "1-5" / "全部"）
            indices = self._parse_indices(" ".join(self._tokens(*rest)), ordered)

            if not indices:
                yield event.plain_result(
                    "📖 /钓鱼 锁定 <序号…>　锁定的鱼不会被卖出\n"
                    "　/钓鱼 解锁 <序号…>\n"
                    "　先用 /钓鱼 背包 看序号"
                )
                return
            bad = [i for i in indices if i > len(ordered)]
            if bad:
                yield event.plain_result(
                    f"🤔 序号 {'/'.join(map(str, bad))} 超出 1~{len(ordered)}"
                )
                return
            touched = 0
            for idx in indices:
                instance = ordered[idx - 1]
                if not bool(instance.get("locked")):
                    instance["locked"] = True
                    touched += 1
            player["inventory"] = inventory
            saved = await self._save_player(player)

        lines = [f"🔒 锁定了 {touched} 条鱼（已锁的跳过）"]
        lines.append("　这些鱼不会被 /钓鱼 卖 或 /钓鱼 卖光光 卖掉")
        if not saved:
            lines.append("⚠️ 保存失败")
        yield event.plain_result("\n".join(lines))

    async def _cmd_unlock(self, event: AstrMessageEvent, user_id: str, *rest):
        """解锁：/钓鱼 解锁 1 2"""
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            inventory: list[dict[str, Any]] = player.get("inventory") or []
            ordered = sorted(inventory, key=_sort_key)

            parts: list[str] = []
            indices = self._parse_indices(" ".join(self._tokens(*rest)), ordered)

            if not indices:
                # 没给序号就全部解锁
                touched = 0
                for instance in inventory:
                    if bool(instance.get("locked")):
                        instance["locked"] = False
                        touched += 1
                player["inventory"] = inventory
                saved = await self._save_player(player)
                lines = [f"🔓 已解锁全部 {touched} 条鱼"]
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            bad = [i for i in indices if i > len(ordered)]
            if bad:
                yield event.plain_result(
                    f"🤔 序号 {'/'.join(map(str, bad))} 超出 1~{len(ordered)}"
                )
                return
            touched = 0
            for idx in indices:
                instance = ordered[idx - 1]
                if bool(instance.get("locked")):
                    instance["locked"] = False
                    touched += 1
            player["inventory"] = inventory
            saved = await self._save_player(player)
        lines = [f"🔓 解锁了 {touched} 条鱼"]
        if not saved:
            lines.append("⚠️ 保存失败")
        yield event.plain_result("\n".join(lines))


    async def _cmd_collectibles(self, event: AstrMessageEvent, user_id: str):
        player = await self._load_player(user_id)
        coll: dict[str, int] = player.get("collectibles") or {}
        notes: list[str] = player.get("bottle_notes") or []
        got = len([c for c in coll if _safe_int(coll.get(c), 0, 0) > 0])

        lines = [f"🎁 杂物收集 {got}/{len(COLLECTIBLES)}"]
        # 已收集的按价值从高到低排，未收集的（❔）统一放最后
        ordered = sorted(
            COLLECTIBLES,
            key=lambda item: (
                _safe_int(coll.get(item["id"]), 0, 0) <= 0,
                -_safe_int(item.get("value"), 0, 0),
            ),
        )
        for item in ordered:
            count = _safe_int(coll.get(item["id"]), 0, 0)
            if count:
                have = _safe_int((player.get("items") or {}).get(item["id"]), 0, 0)
                lines.append(
                    f"　{item['emoji']}{item['name']} 累计×{count}"
                    + (f" 现有{have}" if have else "")
                )
            else:
                lines.append("　❔ ???")
        lines.append(f"📜 纸条 {len(notes)}/{len(BOTTLE_NOTES)} 张")
        if notes:
            lines.append(f"　最近：{notes[-1]}")
        yield event.plain_result("\n".join(lines))

    # =========================================================================
    # 指令入口
    # =========================================================================

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

    # =========================================================================
    # 背包 / 卖鱼 / 图鉴
    # =========================================================================

    async def _cmd_bag(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """背包。默认列出前 20 条；`/钓鱼 背包 2` 翻页。"""
        player = await self._load_player(user_id)
        inventory: list[dict[str, Any]] = player.get("inventory") or []
        cap = _backpack_capacity(player, self.cfg)
        if not inventory:
            yield event.plain_result(
                f"🎒 背包空空的（容量 {cap}）\n💡 发 /钓鱼 下竿试试手气"
            )
            return

        per_page = 20
        page = max(1, _to_int(a2, 1))
        total_pages = max(1, (len(inventory) + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page

        ordered = sorted(inventory, key=_sort_key)
        value = _inventory_value(inventory)
        lines = [
            f"🎒 背包 {len(inventory)}/{cap} 条 · 总估值 {_fmt_gold(value)} 金币"
            + (f" · 第 {page}/{total_pages} 页" if total_pages > 1 else "")
        ]
        for idx, instance in enumerate(
            ordered[start : start + per_page], start=start + 1
        ):
            mark = "🔒" if instance.get("locked") else "　"
            lines.append(
                f"{idx:>2}.{mark}{_instance_line(instance)}　{_attrs_line(instance)}"
            )
        lines.append("【用法】")
        lines.append("　/钓鱼 卖 1 2 3　按序号卖（可给多个）")
        lines.append("　/钓鱼 卖 鲤鱼　　按鱼名卖光这种鱼")
        lines.append("　/钓鱼 卖光光　　一次清空背包")
        lines.append("　/钓鱼 锁定 1　　 锁定后不会被卖出")
        lines.append("　/钓鱼 水族馆 放 1 2　放进水族馆")
        if total_pages > 1:
            lines.append(f"💡 /钓鱼 背包 {page % total_pages + 1} 看下一页")
        async for reply in self._say(event, "\n".join(lines), self._bag_rows()):
            yield reply

    async def _cmd_sell(self, event: AstrMessageEvent, user_id: str, *rest):
        """卖鱼。支持全部形式：

        - ``/钓鱼 卖``              全部卖出（低价优先）
        - ``/钓鱼 卖 1 2 3``        按序号卖（任意条数）
        - ``/钓鱼 卖 1-5``          按区间卖
        - ``/钓鱼 卖 全部``          全部卖出
        - ``/钓鱼 卖光光``          清空背包（锁定的留着）
        - ``/钓鱼 卖 鲤鱼``          卖光某一种鱼
        - ``/钓鱼 卖 鲤鱼 3``        卖这种鱼的 3 条
        """
        tokens = self._tokens(*rest)
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            self._ensure_market(player)  # 确保今日行情已生成
            inventory: list[dict[str, Any]] = player.get("inventory") or []
            if not inventory:
                yield event.plain_result("🎒 背包空空的，没东西可卖")
                return

            ordered = sorted(inventory, key=_sort_key)
            discount = float(self.cfg["sell_discount"])

            def price_of(instance: dict[str, Any]) -> tuple[int, int]:
                """返回 (最终售价, 行情加成部分)。"""
                base = max(1, int(_instance_value(instance) * discount))
                mult = self._market_mult(player, instance.get("fish_id", ""))
                final = max(1, int(base * mult))
                return final, max(0, final - base)

            # ---- 「卖 垃圾」已经取消：说清楚现在该用什么 ----
            if tokens and tokens[0] in (
                "__junk_removed__", "垃圾", "小鱼", "杂鱼", "junk", "trash",
            ):
                yield event.plain_result(
                    "🧹 「卖 垃圾」这个玩法已经去掉了\n"
                    "　想一次清空背包：/钓鱼 卖光光（锁定的鱼会留下）\n"
                    "　想只卖某种鱼：/钓鱼 卖 鲤鱼（可加数量）\n"
                    "　想按序号卖：/钓鱼 卖 1 2 3"
                )
                return

            # ---- 解析目标 ----
            targets: list[dict[str, Any]] = []
            sell_all = (
                not tokens
                or tokens[0] in ("全部", "所有", "all", "全", "光光", "卖光", "清空")
            )
            skipped_locked = 0
            if sell_all:
                # 「卖光光」= 把背包清空：锁定的鱼留着（那是玩家特意保护的）
                unlocked = [x for x in inventory if not bool(x.get("locked"))]
                skipped_locked = len(inventory) - len(unlocked)
                targets = sorted(unlocked, key=_instance_value)
            elif all(self._is_index_token(t) or "-" in t or "~" in t for t in tokens):
                indices = self._parse_indices(" ".join(tokens), ordered)
                if not indices:
                    yield event.plain_result(
                        f"🤔 序号要在 1~{len(ordered)} 之间（发 /钓鱼 背包 看序号）"
                    )
                    return
                targets = [ordered[i - 1] for i in indices]
            else:
                # 按鱼名：第一个 token 是名字，后面可选数量
                first, glued_count = tokens[0], ""
                # 少打空格的容错：`卖 鲤鱼3` 等价于 `卖 鲤鱼 3`
                if len(tokens) == 1:
                    split_nc = self._split_name_count(first)
                    if split_nc and self._find_fish_by_name(split_nc[0]):
                        first, glued_count = split_nc
                fish = self._find_fish_by_name(first)
                if fish is None:
                    sample = "、".join(f["name"] for f in FISH_POOL[:6])
                    yield event.plain_result(
                        f"🤔 没有叫「{first}」的鱼\n"
                        f"　可卖示例：{sample} …\n"
                        f"　也可以按序号卖：/钓鱼 卖 1 2 3"
                    )
                    return
                count = (
                    _to_int(tokens[1], 0)
                    if len(tokens) > 1
                    else _to_int(glued_count, 0)
                )
                group = sorted(
                    [x for x in inventory if x.get("fish_id") == fish["id"]],
                    key=_instance_value,
                )
                if not group:
                    yield event.plain_result(f"🤔 你还没有 {fish['name']}")
                    return
                targets = group if count <= 0 else group[: min(count, len(group))]

            if not targets:
                if sell_all and skipped_locked:
                    yield event.plain_result(
                        f"🔒 背包里 {skipped_locked} 条鱼都锁着，卖光光不会动它们\n"
                        f"　想一起卖：/钓鱼 解锁 1 2 3 之后再 /钓鱼 卖光光"
                    )
                    return
                yield event.plain_result(
                    "🤔 没有可卖的鱼\n"
                    "　可能原因：背包是空的、序号超范围、或这种鱼你还没有\n"
                    "　发 /钓鱼 背包 看背包，或用 /钓鱼 卖光光 一次卖光"
                )
                return

            sold = [(x, price_of(x)[0]) for x in targets]
            bonus = sum(price_of(x)[1] for x in targets)
            player["inventory"] = [
                x for x in inventory if id(x) not in {id(t) for t in targets}
            ]
            async for out in self._finalize_sale(event, player, sold, "💵 卖出", bonus):
                yield out
            if skipped_locked:
                yield event.plain_result(
                    f"🔒 另有 {skipped_locked} 条锁定的鱼留在背包里"
                    f"（/钓鱼 解锁 1 可以解锁）"
                )

    async def _cmd_fish_info(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """查鱼：``/钓鱼 查 鲤鱼`` 或 ``/钓鱼 查 山间湖泊``。

        钓点太多、鱼有 203 种，玩家记不住谁在哪儿——所以这个入口给两件事：
        1) 输入鱼名 → 它在哪些钓点、什么品质、基准价、要不要拉线、自己有没有
        2) 输入钓点名 → 这个钓点有哪些鱼（带基准价，按品质分组）
        """
        name = (a2 or "").strip()
        if not name:
            sample = "、".join(f["name"] for f in FISH_POOL[:5])
            yield event.plain_result(
                "📖 /钓鱼 查 <鱼名 或 钓点名>\n"
                f"　例：/钓鱼 查 鲤鱼　/钓鱼 查 山间湖泊\n"
                f"　常见鱼：{sample} …\n"
                "　不知道名字就发 /钓鱼 图鉴 看进度、/钓鱼 图鉴 详 看完整清单"
            )
            return

        player = await self._load_player(user_id)
        collection = player.get("collection") or {}
        held = _held_counts(player)

        def mine(fish_id: str) -> tuple[int, int, int]:
            entry = collection.get(fish_id)
            total = _safe_int(entry.get("count"), 0, 0) if isinstance(entry, dict) else 0
            best = _safe_int(entry.get("best_value"), 0, 0) if isinstance(entry, dict) else 0
            return total, best, held.get(fish_id, 0)

        # ---- 按钓点查 ----
        loc = self._find_location(name)
        if loc is not None:
            ids = [
                fid
                for fid, w in (LOCATION_WEIGHTS.get(loc["id"]) or {}).items()
                if w > 0 and fid in FISH_BY_ID
            ]
            if not ids:
                yield event.plain_result(f"🐟 {loc['name']} 没有配置鱼种")
                return
            lines = [
                f"{loc['emoji']} {loc['name']}　共 {len(ids)} 种"
                f"　价值×{loc['value_mult']:.2f}"
                f"　需{loc['level_gate']}级"
                + (f"/{_fmt_gold(loc['gold_gate'])}金" if loc["gold_gate"] else "")
            ]
            for rarity in RARITY_ORDER:
                group = sorted(
                    (FISH_BY_ID[fid] for fid in ids if _fish_rarity(fid) == rarity),
                    key=lambda f: f["value"],
                )
                if not group:
                    continue
                lines.append(f"【{self._rarity_name(rarity)}】")
                for fish in group:
                    total, _best, now = mine(fish["id"])
                    mark = "✅" if total > 0 else "❔"
                    lines.append(
                        f"　{mark}{fish['name']}　{_fmt_gold(fish['value'])}金"
                        + (f"　存{now}" if now else "")
                    )
            lines.append("💡 /钓鱼 查 <鱼名> 看它在哪些钓点出现")
            yield event.plain_result("\n".join(lines))
            return

        # ---- 按鱼名查 ----
        fish = self._find_fish_by_name(name)
        if fish is None:
            yield event.plain_result(
                f"🤔 没有叫「{name}」的鱼，也没这个钓点\n"
                "　试试 /钓鱼 图鉴 详 [页码] 看完整鱼名单，"
                "或 /钓鱼 钓点 看钓点列表"
            )
            return
        homes = [
            loc_cfg
            for loc_cfg in self.locations
            if (LOCATION_WEIGHTS.get(loc_cfg["id"]) or {}).get(fish["id"], 0) > 0
        ]
        rarity = fish["rarity"]
        interactive = rarity in self.interactive_rarities
        total, best, now = mine(fish["id"])
        lines = [
            f"{_fish_emoji(fish)} {fish['name']}　{self._rarity_name(rarity)}"
            f"　基准价 {_fmt_gold(fish['value'])} 金币",
            f"　上钩难易：{'要拉线（会跑，手要快）' if interactive else '直接上钩，不用拉线'}",
        ]
        if fish.get("flavor"):
            lines.append(f"　{fish['flavor']}")
        if homes:
            lines.append("　出没钓点：" + "、".join(
                f"{h['emoji']}{h['name']}(×{h['value_mult']:.2f})" for h in homes
            ))
        else:
            lines.append("　出没钓点：暂时没人见到过（隐藏鱼？）")
        if total > 0:
            lines.append(
                f"　我的记录：共 {total} 条　最高卖过 {_fmt_gold(best)}"
                + (f"　背包里还有 {now} 条" if now else "")
            )
        else:
            lines.append("　我的记录：还没钓到过 ❔")
        lines.append("💡 /钓鱼 查 <钓点名> 看那个钓点的全部鱼种")
        yield event.plain_result("\n".join(lines))

    async def _cmd_collection(self, event: AstrMessageEvent, user_id: str, a2: str = ""):
        """鱼种图鉴（203 种鱼，所以按钓点分区 + 可翻页）。

        - ``/钓鱼 图鉴``            各钓点的收集进度（一行一个钓点）
        - ``/钓鱼 图鉴 湖泊``        看某个钓点里还差哪些鱼
        - ``/钓鱼 图鉴 详 [页码]``   按品质排序的完整清单，每页 15 种
        """
        player = await self._load_player(user_id)
        collection: dict[str, dict[str, Any]] = player.get("collection") or {}
        held = _held_counts(player)
        owned = {
            fid
            for fid, e in collection.items()
            if isinstance(e, dict) and _safe_int(e.get("count"), 0, 0) > 0
        }
        arg = (a2 or "").strip()
        toks = self._tokens(a2)
        lower = toks[0].lower() if toks else ""

        def entry_of(fish_id: str) -> tuple[int, int]:
            entry = collection.get(fish_id)
            if not isinstance(entry, dict):
                return 0, 0
            return _safe_int(entry.get("count"), 0, 0), _safe_int(
                entry.get("best_value"), 0, 0
            )

        # ---- 详 [页码]：完整清单，按品质从低到高、每页 15 种 ----
        if lower in ("详", "详细", "all", "detail") or (len(toks) == 1 and arg.isdigit()):
            page = (
                _to_int(toks[1], 1)
                if lower in ("详", "详细", "all", "detail") and len(toks) > 1
                else (_to_int(arg, 1) if arg.isdigit() else 1)
            )
            ordered = sorted(
                FISH_POOL,
                key=lambda f: (RARITY_RANK.get(f["rarity"], 0), f["value"]),
            )
            per_page = 15
            total_pages = max(1, (len(ordered) + per_page - 1) // per_page)
            page = max(1, min(page, total_pages))
            start = (page - 1) * per_page
            lines = [
                f"📖 鱼种图鉴（详）{len(owned)}/{len(FISH_POOL)}"
                f"　第 {page}/{total_pages} 页"
            ]
            for fish in ordered[start : start + per_page]:
                total, best = entry_of(fish["id"])
                if total > 0:
                    now = held.get(fish["id"], 0)
                    lines.append(
                        f"　{_fish_emoji(fish)}{fish['name']}"
                        f"　{self._rarity_name(fish['rarity'])}"
                        f"　共{total} 最高{_fmt_gold(best)}"
                        + (f" 存{now}" if now else "")
                    )
                else:
                    lines.append(
                        f"　❔ ???　{self._rarity_name(fish['rarity'])}"
                    )
            tail_page = page % total_pages + 1
            lines.append(f"💡 /钓鱼 图鉴 详 {tail_page} 看下一页")
            yield event.plain_result("\n".join(lines))
            return

        # ---- 图鉴 <钓点名>：这个钓点里还差哪些 ----
        loc = self._find_location(arg) if arg and lower not in ("详", "详细") else None
        if loc is not None:
            ids = [
                fid
                for fid, w in (LOCATION_WEIGHTS.get(loc["id"]) or {}).items()
                if w > 0 and fid in FISH_BY_ID
            ]
            ids.sort(
                key=lambda fid: (
                    RARITY_RANK.get(_fish_rarity(fid), 0),
                    _safe_int(FISH_BY_ID[fid].get("value"), 0, 0),
                )
            )
            got = [fid for fid in ids if fid in owned]
            lines = [
                f"{loc['emoji']} {loc['name']} 图鉴 {len(got)}/{len(ids)}"
                f"　×{loc['value_mult']:.2f}"
            ]
            for fid in ids:
                fish = FISH_BY_ID[fid]
                total, best = entry_of(fid)
                if total > 0:
                    now = held.get(fid, 0)
                    lines.append(
                        f"　{_fish_emoji(fish)}{fish['name']}"
                        f"　{self._rarity_name(fish['rarity'])}"
                        f"　共{total} 最高{_fmt_gold(best)}"
                        + (f" 存{now}" if now else "")
                    )
                else:
                    lines.append(
                        f"　❔ ???　{self._rarity_name(fish['rarity'])}"
                    )
            lines.append("💡 /钓鱼 图鉴 看各钓点总进度")
            yield event.plain_result("\n".join(lines))
            return

        # ---- 主视图：一行一个钓点（✅ = 已够八成，可以前往下一个钓点）----
        lines = [f"📖 鱼种图鉴 {len(owned)}/{len(FISH_POOL)}"]
        ratio = _clamp(
            _safe_number(self.cfg.get("location_codex_gate"), 0.8), 0.0, 1.0
        )
        gate_ok = 0
        fully_done = 0
        for loc_cfg in self.locations:
            ids = self._location_species(loc_cfg["id"])
            if not ids:
                continue
            got = [fid for fid in ids if fid in owned]
            need_gate = int(len(ids) * ratio + 0.999)
            if len(got) >= need_gate:
                gate_ok += 1
            if len(got) >= len(ids):
                fully_done += 1
            mark = "✅" if len(got) >= need_gate else "　"
            lines.append(
                f"{mark}{loc_cfg['emoji']}{loc_cfg['name']} {len(got)}/{len(ids)}"
            )
        missing = len(FISH_POOL) - len(owned)
        lines.append(
            f"🏅 {len(self.locations)} 个钓点全部集齐！" if fully_done == len(self.locations)
            else f"📌 已开八成 {gate_ok}/{len(self.locations)} 个钓点"
                 f"　全图鉴还差 {missing} 种"
        )
        done = self._codex_completed_rarities(player)
        if done:
            lines.append(
                "📕 已集齐品质：" + "、".join(self._rarity_name(r) for r in done)
            )
        lines.extend(self._best_records_text(player))
        lines.append("💡 /钓鱼 图鉴 <钓点名> 看还差哪些　/钓鱼 图鉴 详 看完整清单")
        yield event.plain_result("\n".join(lines))

    # =========================================================================
    # 水族馆
    # =========================================================================

    async def _cmd_aquarium(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str = ""
    ):
        """水族馆：放/取/卖/领/扩建/投喂。

        ``a3`` 是「子命令之后全部剩余参数」拼成的字符串，
        因此 ``放 1``、``放 1 3 5``、``取 1-3``、``取 全部`` 都能正常解析。
        """
        sub = (a2 or "").strip().lower()
        # 少打空格的容错：`放1` / `取1-3` / `卖全部` 都能拆开
        peeled_aq = self._peel_action(sub, AQUARIUM_ACTIONS)
        if peeled_aq:
            sub, glued = peeled_aq
            a3 = f"{glued} {a3}".strip()
        spec = self._tokens(a3)
        # 兜底：如果上层把子命令一并塞进了 a3，剥掉开头的重复子命令
        if spec and spec[0].lower() == sub and len(spec) > 1:
            spec = spec[1:]
        spec_text = " ".join(spec)

        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            aquarium: list[dict[str, Any]] = player.setdefault("aquarium", [])
            inventory: list[dict[str, Any]] = player.get("inventory") or []
            capacity = self._aquarium_capacity(player)

            # ---- 欣赏 ----
            if not sub:
                yield event.plain_result(self._aquarium_view(player))
                return

            # ---- 扩建 ----
            if sub in ("扩建", "升级", "expand"):
                unlocked = player.setdefault("aquarium_slots", [])
                nxt = next(
                    (s for s in self.aquarium_slots if s["name"] not in unlocked), None
                )
                if nxt is None:
                    yield event.plain_result("🏠 已经扩到最大了")
                    return
                price = int(nxt["price"])
                if _safe_int(player.get("gold"), 0, 0) < price:
                    yield event.plain_result(
                        f"💸 扩建「{nxt['name']}」需要 {_fmt_gold(price)}，金币不足"
                    )
                    return
                player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                unlocked.append(nxt["name"])
                lines = [
                    f"🏠 扩建成功：{nxt['name']}　容量 → {self._aquarium_capacity(player)}",
                    f"💰 余额 {_fmt_gold(player['gold'])}",
                ]
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            # ---- 领取鱼塘挂机收益（按小时累积，与水族馆合并）----
            if sub in ("领", "领取", "收益", "income", "collect"):
                now_ts = int(time.time())
                last = _safe_int(player.get("pond_last_ts"), 0, 0)
                if last <= 0:
                    # 第一次：开始计时
                    player["pond_last_ts"] = now_ts
                    await self._save_player(player)
                    yield event.plain_result(
                        "🏞️ 鱼塘开始计产了！\n"
                        "　每小时产出馆藏估值的 "
                        f"{float(self.cfg['pond_income_per_hour']) * 100:.1f}%，"
                        f"最多累积 {int(self.cfg['pond_income_cap_hours'])} 小时\n"
                        "　过一阵子再来 /钓鱼 水族馆 领"
                    )
                    return

                total_value = _inventory_value(aquarium)
                if total_value <= 0:
                    yield event.plain_result("🐠 水族馆是空的，鱼塘没有产出")
                    return

                hours = min(
                    (now_ts - last) / 3600.0,
                    float(self.cfg["pond_income_cap_hours"]),
                )
                rate = float(self.cfg["pond_income_per_hour"])
                cap_coins = int(self.cfg["pond_income_cap_coins"])
                income = min(int(total_value * rate * hours), cap_coins)
                if income <= 0:
                    wait_min = max(1, int(60 - (now_ts - last) / 60.0))
                    yield event.plain_result(
                        f"⏳ 产出还不够，再等约 {wait_min} 分钟（每小时结算一次）"
                    )
                    return

                player["pond_last_ts"] = now_ts
                player["pond_claimed_ts"] = now_ts
                player["pond_best_income"] = max(
                    _safe_int(player.get("pond_best_income"), 0, 0), income
                )
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + income
                cap_note = "（已达单次上限）" if income >= cap_coins else ""
                lines = [
                    f"🏞️ 鱼塘产出 +{_fmt_gold(income)} 金币{cap_note}",
                    f"　挂机 {hours:.1f} 小时 · 馆藏估值 {_fmt_gold(total_value)}",
                    f"💰 余额 {_fmt_gold(player['gold'])}",
                ]
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            # ---- 投喂（统一走 /钓鱼 用 <道具> <栏位号>）----
            if sub in ("喂", "投喂", "feed"):
                yield event.plain_result(
                    "📖 投喂请用：/钓鱼 用 <道具名> <水族馆栏位号>\n"
                    "　例：/钓鱼 用 高级饲料 1"
                )
                return

            # ---- 放入（支持批量序号：放 1 2 3 / 放 1-3 / 放 全部）----
            if sub in ("放", "放入", "养", "add"):
                ordered = sorted(inventory, key=_sort_key)
                indices = self._parse_indices(spec_text, ordered)
                if not indices:
                    yield event.plain_result(
                        "📖 /钓鱼 水族馆 放 <背包序号…>\n"
                        "　例：/钓鱼 水族馆 放 1　或　放 1 3 5　或　放 1-5\n"
                        f"　背包有 {len(inventory)} 条，水族馆 {len(aquarium)}/{capacity}\n"
                        "　先用 /钓鱼 背包 看序号"
                    )
                    return

                room = capacity - len(aquarium)
                if room <= 0:
                    yield event.plain_result(
                        f"🐠 水族馆已满（{len(aquarium)}/{capacity}）\n"
                        "　可 /钓鱼 水族馆 扩建 扩容，或先 /钓鱼 水族馆 取/卖"
                    )
                    return

                # 先取出要放的鱼（按序号降序 pop，避免索引错位）
                picked: list[dict[str, Any]] = []
                for idx in sorted(indices, reverse=True):
                    picked.append(ordered[idx - 1])
                picked.reverse()

                accepted = picked[:room]
                skipped = len(picked) - len(accepted)
                for instance in accepted:
                    inventory.remove(instance)
                    instance["source"] = "aquarium"
                    aquarium.append(instance)
                player["inventory"] = inventory
                self._sort_aquarium(aquarium)
                player["aquarium"] = aquarium
                saved = await self._save_player(player)

                lines = [f"🐠 放入 {len(accepted)} 条鱼"]
                for instance in accepted[:5]:
                    lines.append(f"　{_instance_line(instance)}")
                if len(accepted) > 5:
                    lines.append(f"　… 其余 {len(accepted) - 5} 条已放入")
                # 缸里的相处结果（机制对玩家不可见，只给现象）
                duel_lines, changed = self._resolve_tank_conflicts(aquarium)
                if changed:
                    player["aquarium"] = aquarium
                    await self._save_player(player)
                lines.extend(duel_lines)
                lines.append(f"　水族馆 {len(aquarium)}/{capacity}")
                if skipped:
                    lines.append(
                        f"　⚠️ 容量不足，{skipped} 条没放进去（先扩建或取出一些）"
                    )
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            # ---- 取出（支持批量序号）----
            if sub in ("取", "取出", "拿", "take"):
                indices = self._parse_indices(spec_text, aquarium)
                if not indices:
                    yield event.plain_result(
                        f"📖 /钓鱼 水族馆 取 <栏位号…>\n"
                        f"　例：/钓鱼 水族馆 取 1　或　取 1 3 5\n"
                        f"　当前水族馆有 {len(aquarium)} 条（发 /钓鱼 水族馆 看栏位）"
                    )
                    return
                got: list[tuple[dict[str, Any], int]] = []
                for idx in sorted(indices, reverse=True):
                    if not (1 <= idx <= len(aquarium)):
                        continue
                    instance = aquarium.pop(idx - 1)
                    gain = self._claim_aquarium_bonus(instance)
                    instance["source"] = "fishing"
                    inventory.append(instance)
                    got.append((instance, gain))
                player["inventory"] = inventory
                saved = await self._save_player(player)
                total_gain = sum(g for _, g in got)
                lines = [f"🎣 取出 {len(got)} 条鱼（估值 +{_fmt_gold(total_gain)}）"]
                for instance, gain in got[:5]:
                    lines.append(
                        f"　{_instance_line(instance)}"
                        + (f"　养大+{_fmt_gold(gain)}" if gain else "　（加成已领过）")
                    )
                if len(got) > 5:
                    lines.append(f"　… 其余 {len(got) - 5} 条已放入背包")
                lines.append(f"🧺 背包 {len(inventory)}/{_backpack_capacity(player, self.cfg)}")
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            # ---- 卖掉馆藏（支持批量序号）----
            if sub in ("卖", "卖出", "sell"):
                indices = self._parse_indices(spec_text, aquarium)
                if not indices:
                    yield event.plain_result(
                        "📖 /钓鱼 水族馆 卖 <栏位号…>\n"
                        "　例：/钓鱼 水族馆 卖 1　或　卖 1 3 5"
                    )
                    return
                discount = float(self.cfg["sell_discount"])
                income = 0
                sold: list[tuple[dict[str, Any], int, int]] = []
                for idx in sorted(indices, reverse=True):
                    if not (1 <= idx <= len(aquarium)):
                        continue
                    instance = aquarium.pop(idx - 1)
                    gain = self._claim_aquarium_bonus(instance)
                    price = max(1, int(_instance_value(instance) * discount))
                    income += price
                    sold.append((instance, price, gain))
                if not sold:
                    yield event.plain_result(f"🤔 没有有效的栏位号（1~{len(aquarium)}）")
                    return
                player["gold"] = _safe_int(player.get("gold"), 0, 0) + income
                player["total_sold"] = _safe_int(player.get("total_sold"), 0, 0) + len(
                    sold
                )
                await self._touch_leaderboard(player)
                lines = [f"💵 卖出馆藏 {len(sold)} 条 → {_fmt_gold(income)} 金币"]
                for instance, price, gain in sold[:5]:
                    lines.append(
                        f"　{_instance_line(instance, with_value=False)} → {_fmt_gold(price)}"
                        + (f"（含展出+{_fmt_gold(gain)}）" if gain else "")
                    )
                if len(sold) > 5:
                    lines.append(f"　… 其余 {len(sold) - 5} 条同上")
                lines.append(f"💰 余额 {_fmt_gold(player['gold'])}")
                saved = await self._save_with_notices(player, lines)
                yield event.plain_result("\n".join(lines))
                return

            yield event.plain_result(
                "📖 水族馆用法\n"
                "　/钓鱼 水族馆　　　　　　欣赏\n"
                "　/钓鱼 水族馆 放 1　　　 从背包放入\n"
                "　/钓鱼 水族馆 取 1　　　 取回（估值+加成）\n"
                "　/钓鱼 水族馆 卖 1　　　 直接卖（估值+加成）\n"
                "　/钓鱼 用 <道具> 1　　　投喂提升三维\n"
                "　/钓鱼 水族馆 领　　　　 领取每日收益\n"
                "　/钓鱼 水族馆 扩建　　　 花金币扩容"
            )

    def _resolve_tank_conflicts(
        self, aquarium: list[dict[str, Any]]
    ) -> tuple[list[str], bool]:
        """结算水族馆里的「相处结果」：狠角色同缸必有一方出事。

        **刻意不做任何提示**：不告诉玩家哪种鱼凶、也不告诉判定规则，
        只给出结果（水浑了 / 少了一条），让玩家自己摸规律。
        返回 (要追加的文案, 是否改动了缸内内容)。
        """
        lines: list[str] = []
        changed = False
        try:
            # 逐对结算：只要有一方是狠角色，弱者就会被淘汰
            for _ in range(len(aquarium)):
                hostile = [
                    (i, x) for i, x in enumerate(aquarium) if _is_hostile(x.get("fish_id", ""))
                ]
                if not hostile:
                    break
                # 狠角色之间、以及狠角色与邻居之间都可能出事
                i_h, h = hostile[0]
                rival_idx = None
                for j, other in enumerate(aquarium):
                    if j == i_h:
                        continue
                    if _is_hostile(other.get("fish_id", "")) or j in (
                        i_h - 1,
                        i_h + 1,
                    ):
                        rival_idx = j
                        break
                if rival_idx is None:
                    break
                rival = aquarium[rival_idx]
                if _fish_power(h) >= _fish_power(rival):
                    loser, winner = rival, h
                else:
                    loser, winner = h, rival
                aquarium.remove(loser)
                changed = True
                loser_name = _fish_name(loser.get("fish_id", ""))
                lines.append(
                    f"　…缸里有点动静，{_fish_emoji(FISH_BY_ID.get(winner.get('fish_id',''), {}))}"
                    f"{_fish_name(winner.get('fish_id', ''))} 把 "
                    f"{loser_name} 逼到了角落，{loser_name} 没了"
                )
        except Exception as e:  # pragma: no cover
            logger.debug(f"水族馆相处结算失败：{e}")
        return lines, changed

    @staticmethod
    def _sort_aquarium(aquarium: list[dict[str, Any]]) -> None:
        """把缸里的鱼排成固定顺序（变异 > 个体品质 > 鱼种品质 > 价值）。

        展示顺序和「取出/卖出 N」的序号必须一致，否则玩家会对不上号。
        """
        try:
            aquarium.sort(key=_sort_key)
        except Exception:
            pass

    def _aquarium_view(self, player: dict[str, Any]) -> str:
        aquarium: list[dict[str, Any]] = player.get("aquarium") or []
        self._sort_aquarium(aquarium)
        capacity = self._aquarium_capacity(player)
        lines = [f"🐠 水族馆 {len(aquarium)}/{capacity}"]
        if not aquarium:
            lines.append("　（空缸）　/钓鱼 水族馆 放 1 放鱼进来")
            return "\n".join(lines)

        total = 0
        best = None
        for idx, instance in enumerate(aquarium, start=1):
            value = _instance_value(instance)
            total += value
            if best is None or value > _instance_value(best):
                best = instance
            feed = _safe_int(instance.get("feed_uses"), 0, 0)
            lines.append(
                f"{idx:>2}.{_instance_line(instance)}　{_attrs_line(instance)}"
                + (f" 喂{feed}" if feed else "")
            )
        lines.append(f"🧮 估值 {_fmt_gold(total)}（取出/卖出 ×{self.cfg['aquarium_bonus']:g}）")
        if best is not None:
            lines.append(f"👑 镇馆之宝：{_instance_line(best)}")
        # 今日收益提示（与「/钓鱼 水族馆 领」的结算口径完全一致）
        today = self._today_text()
        if player.get("last_income_date") != today:
            now_ts = int(time.time())
            last = _safe_int(player.get("pond_last_ts"), 0, 0) or now_ts
            hours = min(
                (now_ts - last) / 3600.0,
                float(self.cfg["pond_income_cap_hours"]),
            )
            est = min(
                int(total * float(self.cfg["pond_income_per_hour"]) * hours),
                int(self.cfg["pond_income_cap_coins"]),
            )
            if est > 0:
                lines.append(f"💰 今日可领 {_fmt_gold(est)}　/钓鱼 水族馆 领")
        return "\n".join(lines)

    # =========================================================================
    # 商店 / 道具
    # =========================================================================

    async def _cmd_shop(self, event: AstrMessageEvent, user_id: str, a2: str, a3: str = ""):
        """商店。

        - ``/钓鱼 商店``                    看货架
        - ``/钓鱼 商店 买 <名字>``           买一组（鱼饵按组、道具 1 个）
        - ``/钓鱼 商店 买 <名字> <数量>``     批量买（组数/个数）
        - ``/钓鱼 商店 扩容``                背包扩容
        """
        sub = (a2 or "").strip().lower()
        # 少打空格的容错：`买蚯蚓` / `扩容` 这类粘连写法也能拆开
        peeled_shop = self._peel_action(sub, SHOP_ACTIONS)
        if peeled_shop:
            sub, glued = peeled_shop
            a3 = f"{glued} {a3}".strip()
        spec = self._tokens(a3)
        if spec and spec[0].lower() == sub and len(spec) > 1:
            spec = spec[1:]
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)

            if not sub:
                yield event.plain_result(self._shop_view(player))
                return

            # 「商店 扩容」：转发到背包扩容（调用无锁版本，避免自死锁）
            if sub in ("扩容", "扩建背包", "背包扩容", "鱼篓扩容", "鱼篓"):
                async for result in self._do_backpack_upgrade(event, user_id):
                    yield result
                return

            if sub in ("买", "购买", "buy"):
                if not spec:
                    yield event.plain_result(
                        "📖 /钓鱼 商店 买 <名字> [数量]\n"
                        "　例：/钓鱼 商店 买 蚯蚓（1 个）　买 蚯蚓 20（20 个）\n"
                        "　鱼饵和道具都按「个」买，数量不写就是 1"
                    )
                    return
                name = spec[0]
                times = _to_int(spec[1], 1) if len(spec) > 1 else 1
                # 少打空格的容错：`买 蚯蚓2` 等价于 `买 蚯蚓 2`
                if len(spec) == 1:
                    split_nc = self._split_name_count(name)
                    if split_nc and (
                        self._find_bait(split_nc[0]) or self._find_item(split_nc[0])
                    ):
                        name, times_text = split_nc
                        times = _to_int(times_text, 1)
                times = max(1, min(times, 999))
                bait_id = self._find_bait(name)
                item_id = self._find_item(name)

                if bait_id == "none":
                    yield event.plain_result(
                        "🪝 空钩是免费的，不需要购买\n"
                        "　直接发 /钓鱼 或 /钓鱼 空钩 就能用它下竿"
                    )
                    return
                if bait_id is None and item_id is None:
                    names = "、".join(
                        [self.baits[b]["name"] for b in self._bait_list()]
                        + [i["name"] for i in self.items.values()]
                    )
                    yield event.plain_result(
                        f"🤔 商店里没有「{name}」\n　在售：{names}"
                    )
                    return

                if bait_id is not None:
                    bait = self.baits[bait_id]
                    unit = max(0, int(bait.get("price", 0)))   # 单价：按个卖
                    want = max(1, min(times, 9999))
                    price = unit * want
                    if _safe_int(player.get("gold"), 0, 0) < price:
                        yield event.plain_result(
                            f"💸 金币不足：买 {want} 个需要 {_fmt_gold(price)}，"
                            f"你只有 {_fmt_gold(player.get('gold', 0))}"
                            f"（{_fmt_gold(unit)}/个）"
                        )
                        return
                    amount = want
                    player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                    baits = player.setdefault("baits", {})
                    baits[bait_id] = _safe_int(baits.get(bait_id), 0, 0) + amount
                    player["equipped_bait"] = bait_id
                    saved = await self._save_player(player)
                    lines = [
                        f"🛒 购买 {self._bait_label(bait_id)} ×{amount}"
                        f"（{_fmt_gold(unit)}/个）→ {_fmt_gold(price)} 金币"
                    ]
                else:
                    item = self.items[item_id]
                    price = int(item.get("price", 0)) * times
                    if _safe_int(player.get("gold"), 0, 0) < price:
                        yield event.plain_result(
                            f"💸 金币不足：买 {times} 个需要 {_fmt_gold(price)}，"
                            f"你只有 {_fmt_gold(player.get('gold', 0))}"
                        )
                        return
                    player["gold"] = _safe_int(player.get("gold"), 0, 0) - price
                    items = player.setdefault("items", {})
                    items[item_id] = _safe_int(items.get(item_id), 0, 0) + times
                    saved = await self._save_player(player)
                    lines = [
                        f"🛒 购买 {self._item_label(item_id)} ×{times}"
                        f" → {_fmt_gold(price)} 金币",
                        f"　{item['desc']}",
                    ]

                # 统一补上「持有量 + 余额」这两条必要信息
                if bait_id is not None:
                    lines.append("　已装备为当前鱼饵")
                    lines.append(
                        f"　持有 {player['baits'].get(bait_id, 0)} 个"
                        f"　💰 余额 {_fmt_gold(player['gold'])}"
                    )
                else:
                    lines.append(
                        f"　持有 {player['items'].get(item_id, 0)} 个"
                        f"　💰 余额 {_fmt_gold(player['gold'])}"
                    )
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            yield event.plain_result(
                "📖 /钓鱼 商店　　　　　　看货架\n"
                "　/钓鱼 商店 买 <名字> [数量]\n"
                "　/钓鱼 商店 扩容　　　 背包扩容"
            )

    def _shop_view(self, player: dict[str, Any]) -> str:
        baits = player.get("baits") or {}
        items = player.get("items") or {}
        lines = [
            f"🛒 商店　💰 {_fmt_gold(player.get('gold', 0))}",
            f"🎣 当前鱼饵：{self._bait_label(player.get('equipped_bait', 'none'))}",
            "— 鱼饵 —",
        ]
        for bait_id in self._bait_list():
            bait = self.baits[bait_id]
            owned = _safe_int(baits.get(bait_id), 0, 0)
            lines.append(
                f"　{self._bait_label(bait_id)} {bait['price']}金/个"
                f"　持有{owned}　手气{_luck_stars(bait.get('luck'), 0.7)}"
                f"　{bait.get('desc', '')}"
            )
        lines.append("— 道具 —")
        for item_id in self._item_list():
            item = self.items[item_id]
            owned = _safe_int(items.get(item_id), 0, 0)
            lines.append(
                f"　{self._item_label(item_id)} {item['price']}金　持有{owned}"
                f"　{item['desc']}"
            )
        return "\n".join(lines)

    async def _cmd_equip_bait(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str = ""
    ):
        """换饵：/钓鱼 换饵 [饵名|空钩]

        不带参数时列出背包里的鱼饵并标出当前用的是哪个；
        带参数时把「当前鱼饵」写进存档，之后裸发 `/钓鱼` 就一直用它。
        """
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            baits = player.get("baits") or {}
            current = player.get("equipped_bait", "none")
            name = (a2 or "").strip()

            def owned_of(bid: str) -> int:
                return _safe_int(baits.get(bid), 0, 0)

            if not name:
                lines = [f"🎣 当前鱼饵：{self._bait_label(current)}"]
                lines.append("　— 背包里的饵 —")
                any_bait = False
                for bid in self._bait_list():
                    count = owned_of(bid)
                    if count <= 0:
                        continue
                    any_bait = True
                    mark = "✅" if bid == current else "　"
                    lines.append(
                        f"{mark}{self._bait_label(bid)} ×{count}"
                        f"　{self.baits[bid].get('desc', '')}"
                    )
                if not any_bait:
                    lines.append("　（没有鱼饵，/钓鱼 商店 买 蚯蚓）")
                lines.append("💡 /钓鱼 换饵 蚯蚓　或　/钓鱼 换饵 空钩（不消耗鱼饵）")
                yield event.plain_result("\n".join(lines))
                return

            target = self._find_bait(name)
            if target is None:
                names = "、".join(
                    [self.baits[b]["name"] for b in self._bait_list()]
                    + ["空钩"]
                )
                yield event.plain_result(
                    f"🤔 没有「{name}」这种饵。可以换：{names}"
                )
                return

            if target != "none" and owned_of(target) <= 0:
                yield event.plain_result(
                    f"🎒 你还没有 {self._bait_label(target)}，"
                    f"先去 /钓鱼 商店 买 {self.baits[target]['name']}"
                )
                return

            if target == current:
                yield event.plain_result(
                    f"🎣 当前用的就是 {self._bait_label(target)}"
                    + (f"（还剩 {owned_of(target)} 个）" if target != "none" else "")
                )
                return

            player["equipped_bait"] = target
            new_ach = self._check_achievements(player)
            saved = await self._save_player(player)
            lines = [f"🎣 已换饵：{self._bait_label(current)} → {self._bait_label(target)}"]
            if target == "none":
                lines.append("　空钩：不花鱼饵，上钩的多是小鱼小虾")
            else:
                lines.append(
                    f"　还剩 {owned_of(target)} 个"
                    f"　{self.baits[target].get('desc', '')}"
                )
            yield event.plain_result("\n".join(lines))

    async def _cmd_use_item(
        self, event: AstrMessageEvent, user_id: str, a2: str, a3: str
    ):
        """使用道具：/钓鱼 用 <道具名> [水族馆栏位序列]

        - 饲料类：需要指定水族馆栏位号，提升那条鱼的三维。
          栏位支持批量：`用 高级饲料 1 2 3` / `用 高级饲料 1-5` / `用 高级饲料 全部`，
          每个栏位消耗 1 个道具，道具用完就停（并在回复里说明）。
        - 洗髓丹（quality_up）：作用在自己身上，购买后自动累积到幸运值。
        """
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            item_id = self._find_item(a2)
            # 少打空格的容错：/钓鱼 用高级饲料 1、/钓鱼 用 高级饲料 1-3
            if item_id is None:
                peeled_item = self._peel_action(
                    a2, tuple(i["name"] for i in self.items.values())
                )
                if peeled_item:
                    guess, glued = peeled_item
                    item_id = self._find_item(guess)
                    if item_id is not None:
                        a3 = f"{glued} {a3}".strip()
            if item_id is None:
                owned = player.get("items") or {}
                if not owned:
                    yield event.plain_result("🎒 没有道具，去 /钓鱼 商店 买")
                    return
                names = "、".join(
                    self._item_label(i) for i, c in owned.items() if _safe_int(c, 0, 0) > 0
                )
                yield event.plain_result(f"📖 /钓鱼 用 <道具> <序列>　你有：{names or '无'}")
                return

            items = player.get("items") or {}
            if _safe_int(items.get(item_id), 0, 0) <= 0:
                yield event.plain_result(
                    f"🎒 没有 {self._item_label(item_id)}，去 /钓鱼 商店 买"
                )
                return

            item = self.items.get(item_id) or {}
            effects = item.get("effects") or {}

            # --- 洗髓丹：提升「下一批鱼」的个体品质幸运 ---
            if _safe_number(effects.get("quality_up"), 0.0) > 0:
                items[item_id] = _safe_int(items.get(item_id), 0, 0) - 1
                gain = _safe_number(effects.get("quality_up"), 0.0)
                player["luck_charges"] = _clamp(
                    _safe_number(player.get("luck_charges"), 0.0) + gain, 0.0, 2.0
                )
                saved = await self._save_player(player)
                lines = [
                    f"🔮 使用 {self._item_label(item_id)}",
                    f"　下一竿手气：{_luck_stars(gain, 0.3)}（储备 {_luck_stars(player['luck_charges'], 0.5)}）",
                ]
                if not saved:
                    lines.append("⚠️ 保存失败")
                yield event.plain_result("\n".join(lines))
                return

            # --- 饲料类：需要水族馆目标（支持批量栏位）---
            aquarium: list[dict[str, Any]] = player.get("aquarium") or []
            if not aquarium:
                yield event.plain_result("🐠 水族馆是空的，先把鱼放进去养")
                return
            # 不带栏位 = 喂全缸（「喂养对所有鱼生效」）
            if not (a3 or "").strip():
                slots = list(range(1, len(aquarium) + 1))
            else:
                slots = self._parse_indices(a3, aquarium)
            if not slots:
                yield event.plain_result(
                    f"📖 /钓鱼 用 {item.get('name', item_id)} [水族馆栏位]\n"
                    f"　不写栏位就是喂全缸；也可以写 1 2 3 / 1-3"
                )
                return

            stock = _safe_int(items.get(item_id), 0, 0)
            max_uses = int(self.cfg["feed_max_uses"])
            used = 0
            grown: list[str] = []
            skipped_full: list[int] = []
            value_gain = 0
            for idx in slots:
                if stock <= 0:
                    break
                instance = aquarium[idx - 1]
                if _safe_int(instance.get("feed_uses"), 0, 0) >= max_uses:
                    skipped_full.append(idx)
                    continue
                stock -= 1
                used += 1
                _, delta = _apply_feed(instance, effects)
                value_gain += delta
                grown.append(f"　{idx}. {_instance_line(instance, with_value=False)}"
                             f"　{_safe_int(instance.get('feed_uses'), 0, 0)}/{max_uses}")

            if used <= 0:
                if skipped_full:
                    yield event.plain_result(
                        f"🍖 栏位 {'、'.join(str(i) for i in skipped_full)} "
                        f"都已经喂满 {max_uses} 次了"
                    )
                else:
                    yield event.plain_result(f"🎒 没有 {self._item_label(item_id)} 了")
                return

            items[item_id] = stock
            player["total_fed"] = _safe_int(player.get("total_fed"), 0, 0) + used
            new_ach = self._check_achievements(player)
            saved = await self._save_player(player)

            sign = "+" if value_gain >= 0 else ""
            lines = [
                f"🍽 {self._item_label(item_id)} ×{used}"
                f"　馆藏总价 {sign}{_fmt_gold(value_gain)}"
                f"　剩余道具 {stock}"
            ]
            # 单条时展开细节，批量时只列前 6 条，避免刷屏
            lines.extend(grown[:6])
            if len(grown) > 6:
                lines.append(f"　… 其余 {len(grown) - 6} 条也喂到了")
            if skipped_full:
                lines.append(
                    f"　（跳过已喂满的栏位 {len(skipped_full)} 条）"
                )
            yield event.plain_result("\n".join(lines))

    # =========================================================================
    # 档案 / 签到 / 管理员
    # =========================================================================

    async def _cmd_profile(self, event: AstrMessageEvent, user_id: str):
        player = await self._load_player(user_id)
        inventory = player.get("inventory") or []
        aquarium = player.get("aquarium") or []
        collection = player.get("collection") or {}
        baits = player.get("baits") or {}
        items = player.get("items") or {}
        capacity = self._aquarium_capacity(player)

        bait_text = "、".join(
            f"{self.baits[b]['name']}×{_safe_int(baits.get(b), 0, 0)}"
            for b in self._bait_list()
            if _safe_int(baits.get(b), 0, 0) > 0
        ) or "无"
        item_text = "、".join(
            f"{self.items[i]['name']}×{_safe_int(items.get(i), 0, 0)}"
            for i in self._item_list()
            if _safe_int(items.get(i), 0, 0) > 0
        ) or "无"
        kinds = sum(
            1
            for e in collection.values()
            if isinstance(e, dict) and _safe_int(e.get("count"), 0, 0) > 0
        )
        total_value = _inventory_value(inventory) + _inventory_value(aquarium)
        cap = _backpack_capacity(player, self.cfg)
        aq_cap = self._aquarium_capacity(player)

        lines = [
            "📊 档案",
            f"💰 {_fmt_gold(player.get('gold', 0))}　🎣 {self._rod_label(player)}"
            f"　📍 {self._location_label(player)}",
            f"🧰 鱼饵 {self._bait_label(player.get('equipped_bait', 'none'))}"
            f"　背包 {len(inventory)}/{cap}　水族馆 {len(aquarium)}/{aq_cap}",
            f"🧮 渔获估值 {_fmt_gold(total_value)}"
            f"　🏅 累计钓 {_safe_int(player.get('total_caught'), 0, 0)}"
            f"　卖 {_safe_int(player.get('total_sold'), 0, 0)}"
            f"　喂 {_safe_int(player.get('total_fed'), 0, 0)}",
            f"📖 图鉴 {kinds}/{len(FISH_POOL)}　🎁 杂物 "
            f"{sum(1 for c, n in (player.get('collectibles') or {}).items() if _safe_int(n, 0, 0) > 0)}"
            f"/{len(COLLECTIBLES)}　📜 纸条 {len(player.get('bottle_notes') or [])}",
            f"🎖 成就 {len(player.get('achievements') or [])}/{len(ACHIEVEMENTS)}"
            f"　📋 订单 {_safe_int(player.get('total_orders'), 0, 0)}",
            f"🎒 饵：{bait_text}",
            f"🧰 道具：{item_text}",
        ]
        luck = _safe_number(player.get("luck_charges"), 0.0)
        if luck > 0:
            lines.append(f"🔮 手气储备 {_luck_stars(luck, 0.5)}")
        yield event.plain_result("\n".join(lines))

    async def _cmd_sign(self, event: AstrMessageEvent, user_id: str):
        today = self._today_text()
        async with self._lock_for(user_id):
            player = await self._load_player(user_id)
            if player.get("last_sign_date") == today:
                yield event.plain_result(
                    f"📅 今天已签到　💰 {_fmt_gold(player.get('gold', 0))}"
                )
                return
            reward = int(self.cfg["sign_reward"])
            player["last_sign_date"] = today
            player["gold"] = _safe_int(player.get("gold"), 0, 0) + reward
            new_ach = self._check_achievements(player)
            saved = await self._save_player(player)
        lines = [f"✅ 签到 +{reward}　💰 {_fmt_gold(player['gold'])}"]
        if new_ach:
            lines.append("🎉 " + "；".join(new_ach))
        if not saved:
            lines.append("⚠️ 保存失败")
        yield event.plain_result("\n".join(lines))

    # =========================================================================
    # 帮助
    # =========================================================================

    def _help_pages(self) -> list[tuple[str, list[str]]]:
        """帮助分页内容：(标题, 行列表)。每页都尽量短，避免刷屏。"""
        cfg = self.cfg
        cd = (
            "无冷却"
            if int(cfg["cooldown_seconds"]) <= 0
            else f'{int(cfg["cooldown_seconds"])}s'
        )
        interactive = "/".join(
            sorted(self.interactive_rarities, key=lambda r: RARITY_RANK.get(r, 0))
        )
        rarity_line = " < ".join(self._rarity_name(r) for r in RARITY_ORDER)
        base_cap = _backpack_capacity({"backpack_slots": []}, cfg)

        loc_lines = [
            f"　{loc['emoji']}{loc['name']}　×{loc['value_mult']:.2f}"
            f"　需{loc['level_gate']}级"
            + (f"/{_fmt_gold(loc['gold_gate'])}金" if loc["gold_gate"] else "")
            for loc in sorted(
                self.locations,
                key=lambda l: (_safe_int(l.get("level_gate"), 1, 1),
                               _safe_int(l.get("gold_gate"), 0, 0)),
            )
        ]
        rod_lines = [
            f"　{rod['emoji']}{rod['name']}　{_fmt_gold(rod['price'])}金"
            f"　价值+{rod['value_bonus']:.0%}　手气{_luck_stars(rod['luck_bonus'], 0.2)}"
            for rod in sorted(self.rods, key=lambda r: _safe_int(r.get("price"), 0, 0))
        ]
        bait_lines = [
            f"　{self.baits[b]['emoji']}{self.baits[b]['name']}　"
            f"{self.baits[b]['price']}金/个　"
            f"手气{_luck_stars(self.baits[b]['luck'], 0.7)}"
            for b in self._bait_list()
        ]
        item_lines = [
            f"　{self._item_label(iid)}　{self.items[iid]['price']}金　"
            f"{self.items[iid]['desc']}"
            for iid in self._item_list()
        ]

        fee_line = (
            f"　下竿免费（空钩不花钱）　冷却 {cd}　签到 {cfg['sign_reward']}"
            if int(cfg["fish_cost"]) <= 0
            else f"　钓费 {cfg['fish_cost']}/竿　冷却 {cd}　签到 {cfg['sign_reward']}"
        )
        pages: list[tuple[str, list[str]]] = [
            (
                "基础",
                [
                    "　/钓鱼　　　　　下竿（写 钓/抛竿 也行）",
                    "　/钓鱼 拉　　　 拉线（也可写 收线/提竿）",
                    "　/钓鱼 背包　　 看背包",
                    "　/钓鱼 卖光光　 清空背包换金币",
                    "　/钓鱼 换饵 蚯蚓　换鱼饵（换饵 空钩 不花钱）",
                    "　/钓鱼 金币　　 档案",
                    "　/钓鱼 签到　　 每日金币",
                    "　/钓鱼 今日　　 今日天气与行情",
                    "　/钓鱼 排行　　 群内排行榜",
                    "　/钓鱼 锁定 1　 锁定不想卖的鱼",
                    "　/钓鱼 事件 1　 水面上偶尔会有事发生",
                    fee_line,
                ],
            ),
        ]

        # 钓点有 16 个，一页放不下：每 6 个一页
        per_page = 6
        chunks = [
            loc_lines[i : i + per_page] for i in range(0, len(loc_lines), per_page)
        ] or [[]]
        for index, chunk in enumerate(chunks, 1):
            title = "钓点" if len(chunks) == 1 else f"钓点 {index}/{len(chunks)}"
            extra = (
                [
                    "　/钓鱼 去 <名称>　　　 前往",
                    "　/钓鱼 钓点 解锁 <名>　解锁",
                ]
                if index == len(chunks)
                else []
            )
            pages.append((title, chunk + extra))

        pages.extend(
            [
                ("鱼竿", rod_lines + ["　/钓鱼 鱼竿 买 <名> ｜ 用 <名>"]),
                (
                    "鱼饵与道具",
                    bait_lines
                    + ["　— 道具 —"]
                    + item_lines
                    + ["　/钓鱼 商店 买 <名> [个数]", "　/钓鱼 用 <道具> [栏位]"],
                ),
                (
                    "养成与赚钱",
                    [
                        "　/钓鱼 水族馆　　　　 放/取/卖/领/扩建",
                        "　/钓鱼 订单　　　　　 订单（不定时刷新，收益更高）",
                        "　/钓鱼 订单 交 1 2　　批量交单（交过的不再收）",
                        "　/钓鱼 商店 扩容　　　背包扩容",
                        f"　背包上限 {base_cap} 起，不能无限囤货",
                        "　养鱼提升肉质/灵性/光泽 → 直接涨价",
                    ],
                ),
                (
                    "收集与社交",
                    [
                        "　/钓鱼 查 <鱼名>　　 这条鱼在哪些钓点",
                    "　/钓鱼 查 <钓点名>　 这个钓点有哪些鱼",
                    "　/钓鱼 图鉴　　　　　 鱼的收集进度",
                        "　/钓鱼 杂物　　　　　 杂物与纸条收集",
                    ],
                ),
                (
                    "品质与拉线",
                    [
                        f"　鱼种：{rarity_line}（固有，不可变）",
                        "　个体：⚪普通 🟢优良 🔵稀有 🟣极品 🌟传说",
                        f"　只有 {interactive} 需要拉线",
                        "　🎯完美 > 👍良好 > 😅偏差，超时鱼会跑",
                        f"　图鉴 {len(FISH_POOL)} 种　成就 {len(ACHIEVEMENTS)} 个",
                        "　累计钓获 1/10/25/50/100… 有里程碑",
                    ],
                ),
            ]
        )
        return pages

    def _help_text(self, page: int = 1) -> str:
        """分页帮助，避免一次性输出过多文字。"""
        pages = self._help_pages()
        total = len(pages)
        page = int(_clamp(page, 1, total))
        title, lines = pages[page - 1]
        head = f"🎣 帮助 {page}/{total} · {title}"
        tail = (
            f"💡 /钓鱼 帮助 {page + 1}"
            if page < total
            else "💡 /钓鱼 帮助 1 回到第一页"
        )
        return "\n".join([head, ""] + lines + ["", tail])

    # =========================================================================
    # 生命周期
    # =========================================================================

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

    def _init_data_management(self) -> None:
        """启动数据管理：建存档目录、执行配置里排队的操作、起自动存档循环。"""
        self._data_status = ""
        store = getattr(self, "backup_store", None)
        if store is None:
            logger.warning("存档模块不可用，数据管理功能已跳过（_backup.py 是否缺失？）")
            return
        store.ensure_layout()
        try:
            logger.info(
                f"平台：{self._platform_hint()}；"
                f"QQ 官方机器人（qq_official）上会自动带按钮，其它平台自动用纯文本"
            )
        except Exception:  # pragma: no cover
            pass
        logger.info(
            f"存档目录：{store.root}（每日 {self.cfg.get('backup_daily_hour')} 点、"
            f"每 {self.cfg.get('backup_interval_hours')} 小时自动存档，"
            f"手动/导入/清除都在插件配置的「数据管理」里）"
        )
        # 配置保存会热重载插件：这里立刻执行配置里选好的操作
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_data_action())
            self._backup_task = loop.create_task(self._auto_backup_loop())
        except RuntimeError:  # pragma: no cover - 没有事件循环（单测直接调用时）
            self._backup_task = None

    # =========================================================================
    # 数据管理（全部通过插件配置触发，玩家侧没有任何指令）
    # =========================================================================

    #: 配置里可选的数据操作 -> 说明（供状态面板与网页展示）
    DATA_ACTIONS: dict[str, str] = {
        "无": "不执行任何操作",
        "立即存档": "立刻把所有玩家存档成一份快照（manual/）",
        "导出全部玩家": "每个玩家各导出一个 JSON 到 players/，方便单独发回或迁移",
        "导出单个玩家": "导出 data_target 指定的那个玩家",
        "从快照恢复": "用 data_target 指定的快照覆盖全部玩家（留空=最新的快照）",
        "恢复单个玩家": "只恢复 data_target 指定的玩家（快照名写在 data_note）",
        "导入上传的存档": "导入「上传存档文件」里最新上传的那份 JSON",
        "清除单个玩家": "删除 data_target 指定玩家的数据（需勾选确认）",
        "清除全部玩家数据": "删除所有玩家数据（需勾选确认，会先自动存一份档）",
    }

    def _set_status(self, text: str, *, log: bool = True) -> None:
        """把结果写回配置项 data_status，WebUI 刷新即可看到。"""
        stamp = _text_now()
        line = f"[{stamp}] {text}"
        try:
            self.config["data_status"] = line
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception as e:  # pragma: no cover
            logger.debug(f"写回数据状态失败：{e}")
        self._data_status = line
        if log:
            logger.info(f"数据管理：{text}")

    def _platform_hint(self) -> str:
        """给日志用：提示按钮只在 QQ 官方机器人上出现。"""
        platforms: set[str] = set()
        try:
            for player in (self._recent_platforms or {}).values():
                if player:
                    platforms.add(str(player))
        except Exception:
            pass
        return "、".join(sorted(platforms)) if platforms else "等待玩家消息"

    def _data_files_root(self) -> str:
        """上传/导出的落地根目录。

        默认是插件目录（AstrBot 的 file 类型配置会把文件存到 ``files/<配置键>/``）；
        测试里可以把 ``self.data_files_root`` 指到临时目录，免得写脏真插件目录。
        """
        root = getattr(self, "data_files_root", None)
        return str(root) if root else os.path.dirname(os.path.abspath(__file__))

    def _import_dir(self) -> str:
        """WebUI「上传存档文件」那一项的落盘目录（AstrBot 约定：files/<配置键>/）。"""
        return os.path.join(self._data_files_root(), "files", "backup_import_file")

    def _export_dir(self) -> str:
        """WebUI「导出存档文件」那一项的落盘目录（放这里才能在网页里点下载）。"""
        return os.path.join(self._data_files_root(), "files", "backup_export_file")

    async def _snapshot(self, kind: str, note: str = "") -> str:
        """做一份快照，返回给管理员看的说明。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用（_backup.py 缺失？）"
        players = await self._dump_all_players()
        path, payload = store.write_snapshot(kind, players, note=note)
        if kind == "auto":
            store.prune("auto", int(self.cfg.get("backup_keep_interval", 20)))
        if kind == "daily":
            store.prune_daily_days(int(self.cfg.get("backup_keep_daily", 30)))
        store.rebuild_index()
        return f"{path.name}（{payload['count']} 名玩家）"

    async def _export_players(self, target: str = "") -> str:
        """导出玩家数据到 players/ 与 files/backup_export_file/。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用"
        ids = [target] if target else await self._player_ids()
        if not ids:
            return "没有可导出的玩家（索引为空）"
        done = 0
        export_dir = self._export_dir()
        try:
            os.makedirs(export_dir, exist_ok=True)
        except OSError:
            export_dir = str(store.path_of("exported"))
        for uid in ids:
            try:
                raw = await self.get_kv_data(self._kv_key(uid), None)
            except Exception:
                continue
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            if not isinstance(raw, dict):
                continue
            path = store.export_player(uid, raw)
            if path is None:
                continue
            done += 1
            try:
                with open(path, encoding="utf-8") as src_fp, open(
                    os.path.join(export_dir, os.path.basename(str(path))),
                    "w",
                    encoding="utf-8",
                ) as dst_fp:
                    dst_fp.write(src_fp.read())
            except OSError:
                pass
        # 把文件列表写回「导出存档文件」配置项：WebUI 里会变成可下载的条目
        try:
            names = sorted(
                (
                    name
                    for name in os.listdir(export_dir)
                    if name.lower().endswith(".json")
                ),
                reverse=True,
            )[:100]
            files = [f"files/backup_export_file/{name}" for name in names]
            # 先更新运行时配置（测试里 config 可能就是普通 dict），再尝试落盘
            self.cfg["backup_export_file"] = list(files)
            try:
                self.config["backup_export_file"] = files
                save = getattr(self.config, "save_config", None)
                if callable(save):
                    save()
            except Exception as e:  # pragma: no cover
                logger.debug(f"落盘导出文件列表失败：{e}")
        except Exception as e:  # pragma: no cover
            logger.debug(f"写回导出文件列表失败：{e}")
        store.rebuild_index()
        return (
            f"已导出 {done} 名玩家：存档在 {store.path_of('players')}，"
            f"也能在「导出存档文件」那一项里点文件名下载"
        )

    async def _restore_snapshot(self, snapshot: str, target: str = "") -> str:
        """从快照恢复（target 为空=全部玩家）。恢复前先自动存一份档。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用"
        snap = store.load_snapshot(snapshot)
        if not snap:
            return f"找不到快照「{snapshot or '最新'}」（用「立即存档」先存一份）"
        players = snap.get("players") or {}
        if not players:
            return f"快照「{snapshot or '最新'}」里没有玩家数据"
        await self._snapshot("manual", note="恢复前自动存档")
        restored = 0
        if target:
            raw = players.get(str(target))
            data, _ = BACKUP_MODULE.unwrap_player(raw) if BACKUP_MODULE else (raw, False)
            if data is None:
                return f"快照里没有玩家 {target}"
            await self.put_kv_data(self._kv_key(target), _json_dumps(raw))
            await self._remember_player(str(target))
            restored = 1
        else:
            for uid, raw in players.items():
                data, _ = BACKUP_MODULE.unwrap_player(raw) if BACKUP_MODULE else (raw, False)
                if data is None:
                    continue
                await self.put_kv_data(self._kv_key(str(uid)), _json_dumps(raw))
                await self._remember_player(str(uid))
                restored += 1
        store.rebuild_index()
        return f"已从「{snap.get('path', snapshot)}」恢复 {restored} 名玩家"

    async def _import_uploaded(self) -> str:
        """导入「上传存档文件」里最新的一份 JSON（单玩家信封或整份快照都支持）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用"
        plugin_dir = self._data_files_root()
        candidates: list[str] = []
        # AstrBot 的 file 类型配置存的是相对路径列表（files/<配置键>/xxx.json）
        for rel in self.cfg.get("backup_import_file") or []:
            if isinstance(rel, str) and rel.strip():
                candidates.append(os.path.join(plugin_dir, rel.replace("/", os.sep)))
        folder = self._import_dir()
        if os.path.isdir(folder):
            candidates.extend(
                os.path.join(folder, name)
                for name in os.listdir(folder)
                if name.lower().endswith(".json")
            )
        files = sorted(
            {p for p in candidates if os.path.isfile(p)},
            key=lambda p: os.path.getmtime(p),
            reverse=True,
        )
        if not files:
            return "还没有上传存档（把 JSON 拖到「上传存档文件」那一项，保存后再选这个操作）"
        newest = files[0]
        newest_name = os.path.basename(newest)
        try:
            with open(newest, encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, ValueError) as e:
            return f"上传的 {newest_name} 不是合法 JSON：{e}"
        if not isinstance(payload, dict):
            return f"上传的 {newest_name} 内容格式不对"

        store.copy_into(newest, "manual", f"imported_{newest_name}")
        # 形态一：整份快照
        if isinstance(payload.get("players"), dict):
            count = 0
            for uid, raw in payload["players"].items():
                data, _ = BACKUP_MODULE.unwrap_player(raw) if BACKUP_MODULE else (raw, False)
                if data is None:
                    continue
                await self.put_kv_data(self._kv_key(str(uid)), _json_dumps(raw))
                await self._remember_player(str(uid))
                count += 1
            return f"已从 {newest_name} 导入 {count} 名玩家（快照格式）"
        # 形态二：单个玩家信封
        data, _ = BACKUP_MODULE.unwrap_player(payload) if BACKUP_MODULE else (payload, False)
        uid = str(payload.get("user_id") or self.cfg.get("data_target") or "").strip()
        if data is None or not uid:
            return "单个玩家存档需要带 user_id 字段（或在 data_target 里填玩家ID）"
        await self.put_kv_data(self._kv_key(uid), _json_dumps(payload))
        await self._remember_player(uid)
        return f"已导入玩家 {uid}（来自 {newest_name}）"

    async def _clear_players(self, target: str = "") -> str:
        """清除玩家数据（清除前先自动存档）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用"
        ids = [target] if target else await self._player_ids()
        if not ids:
            return "没有要清除的玩家"
        await self._snapshot("manual", note="清除前自动存档")
        removed = 0
        for uid in ids:
            try:
                await self.delete_kv_data(self._kv_key(uid))
                removed += 1
            except Exception as e:
                logger.warning(f"清除玩家 {uid} 失败：{e}")
        if not target:
            try:
                await self.put_kv_data("player_index", _json_dumps([]))
            except Exception:
                pass
        else:
            try:
                keep = [x for x in await self._player_ids() if x != str(target)]
                await self.put_kv_data("player_index", _json_dumps(keep))
            except Exception:
                pass
        store.rebuild_index()
        return f"已清除 {removed} 名玩家的数据（清除前的快照已保存）"

    async def _run_data_action(self) -> None:
        """执行配置里选择的数据操作，然后把它复位成「无」。"""
        action = str(self.cfg.get("data_action") or "无").strip()
        if action in ("", "无", "none", "None"):
            return
        target = str(self.cfg.get("data_target") or "").strip()
        confirm = bool(self.cfg.get("data_confirm"))
        dangerous = action in ("清除单个玩家", "清除全部玩家数据", "从快照恢复")
        try:
            if dangerous and not confirm:
                result = f"「{action}」需要先勾选「我已确认」再保存配置"
            elif action == "立即存档":
                result = "已存档：" + await self._snapshot("manual", note="配置里手动触发")
            elif action == "导出全部玩家":
                result = await self._export_players()
            elif action == "导出单个玩家":
                result = (
                    await self._export_players(target)
                    if target
                    else "请在 data_target 里填要导出的玩家 ID"
                )
            elif action == "从快照恢复":
                result = await self._restore_snapshot(target)
            elif action == "恢复单个玩家":
                # data_target 支持「快照名/玩家ID」；只写玩家 ID 时从最新快照恢复
                snap_name, _, only_player = str(target).partition("/")
                if not only_player:
                    snap_name, only_player = "", snap_name
                result = (
                    await self._restore_snapshot(snap_name.strip(), only_player.strip())
                    if only_player.strip()
                    else "请在 data_target 里填玩家 ID（想指定快照就写「快照名/玩家ID」）"
                )
            elif action == "导入上传的存档":
                result = await self._import_uploaded()
            elif action == "清除单个玩家":
                result = (
                    await self._clear_players(target)
                    if target
                    else "请在 data_target 里填要清除的玩家 ID"
                )
            elif action == "清除全部玩家数据":
                result = await self._clear_players()
            else:
                result = f"不认识的操作「{action}」"
        except Exception as e:
            logger.error(f"数据操作「{action}」执行失败：{e}", exc_info=True)
            result = f"「{action}」执行失败：{e}"

        # 复位动作 + 回写状态
        try:
            self.config["data_action"] = "无"
            self.config["data_confirm"] = False
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception as e:  # pragma: no cover
            logger.debug(f"复位数据动作失败：{e}")
        self.cfg["data_action"] = "无"
        self.cfg["data_confirm"] = False
        self._set_status(result)
        await self._refresh_data_status(extra=result)

    async def _refresh_data_status(self, extra: str = "") -> None:
        """刷新状态面板：玩家数、存档数量、最近快照、目录。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return
        try:
            ids = await self._player_ids()
            index = store.rebuild_index()
            items = index.get("snapshots") or []
            latest = items[0] if items else None
            lines = [
                f"玩家数：{len(ids)}",
                f"存档数：{index.get('total', 0)}"
                f"（每日 {index['by_kind'].get('daily', 0)} / 按时 "
                f"{index['by_kind'].get('auto', 0)} / 手动 "
                f"{index['by_kind'].get('manual', 0)}）",
                f"最近快照：{(latest or {}).get('rel', '无')}"
                + (f"（{latest.get('mtime_text', '')}，{latest.get('count', 0)} 名玩家）" if latest else ""),
                f"目录：{store.root}",
                f"自动存档：{'开' if self.cfg.get('enable_auto_backup') else '关'}"
                f"（每 {self.cfg.get('backup_interval_hours')} 小时 / 每天 "
                f"{self.cfg.get('backup_daily_hour')} 点）",
                f"上次操作：{self._data_status or '无'}",
            ]
            if extra:
                lines.append(f"结果：{extra}")
            text = "\n".join(lines)
            self.config["data_status"] = text
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
            self._data_status = text
        except Exception as e:  # pragma: no cover
            logger.debug(f"刷新数据状态失败：{e}")

    async def _auto_backup_loop(self) -> None:
        """后台循环：按时存档 + 每日存档 + 顺便执行配置里排队的操作。"""
        try:
            await asyncio.sleep(20)  # 启动后先等一会儿，别和加载抢 IO
            while True:
                try:
                    if not self.cfg.get("enable_auto_backup"):
                        pass
                    else:
                        store = getattr(self, "backup_store", None)
                        today = self._today_text()
                        items = store.list_snapshots() if store else []
                        # 每日存档：今天还没有就存一份（绝不覆盖已有的）
                        has_daily = any(
                            item["kind"] == "daily"
                            and item["name"].startswith(today)
                            for item in items
                        )
                        now_hour = int(time.strftime("%H"))
                        if not has_daily and now_hour >= int(
                            self.cfg.get("backup_daily_hour", 4)
                        ):
                            await self._snapshot("daily", note="每日自动存档")
                        # 按时存档
                        interval = int(self.cfg.get("backup_interval_hours", 6))
                        if interval > 0:
                            auto_items = [x for x in items if x["kind"] == "auto"]
                            last_ts = auto_items[0]["mtime"] if auto_items else 0
                            if time.time() - last_ts >= interval * 3600:
                                await self._snapshot("auto", note=f"每 {interval} 小时自动存档")
                    await self._refresh_data_status()
                    await self._run_data_action()
                except Exception as e:
                    logger.warning(f"自动存档循环出错（会继续跑）：{e}")
                await asyncio.sleep(60)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception as e:  # pragma: no cover
            logger.error(f"自动存档循环退出：{e}")

    #: 会被「自动合并」的内容型配置项（按每行第一个 | 前的 id 去重）
    CONTENT_LIST_KEYS: tuple[str, ...] = (
        "location_defs", "rod_defs", "bait_defs", "item_defs",
    )

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
