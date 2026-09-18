# -*- coding: utf-8 -*-
"""游戏内容表（纯数据，改这里就能加内容，不用碰逻辑）。

包含：
- COLLECTIBLES   杂物/打捞品
- BOTTLE_NOTES   漂流瓶纸条
- VARIANTS       变异个体
- WEATHERS       天气
- EASTER_EGGS    上鱼后的小惊喜（对外不叫这个名字）
- RANDOM_EVENTS  抛竿时的小插曲（两个选项）
- MILESTONES     累计钓获里程碑
- ACHIEVEMENTS   成就

====================== 想加内容 / 改内容？改这个文件 ======================
每个表都是一行一条，加一行就多一个内容，删一行就没了：

- COLLECTIBLES   杂物：`id / name / emoji / weight（出现权重，越大越多）/ value（卖价）/ desc`
                 —— 想调「一竿钓上杂物的概率」用配置 `item_drop_chance`
- BOTTLE_NOTES   漂流瓶纸条：纯字符串列表（概率看配置 `bottle_note_chance`）
- VARIANTS       变异：`id / name / emoji / weight / mult（价值倍率）/ desc`
- WEATHERS       天气：`id / name / emoji / weight / rarity_mult（{品质: 权重倍数}，晴天写 {}）/
                 window_mult（拉线窗口倍数）/ luck（本日手气加成）/
                 escape_mult（逃脱率倍数）/ desc`
- EASTER_EGGS    上鱼后的小惊喜：`id / weight / text / 效果`，
                 效果键按需挑一个：`gold`（给金币）/ `note`（得一张纸条，写 true）/
                 `luck`（本竿手气 +N）/ `heal_bait`（返还鱼饵，写 true）
- RANDOM_EVENTS  抛竿小插曲：`id / weight / text / choices[]`（**两个**选项），
                 每个选项：`label`（按钮文字）/ `text`（选择后的描述）/
                 `good`（好结果文案）/ `idle`（空手文案），
                 奖励键可选：`gold` / `note` / `bait`（给几个鱼饵）
- MILESTONES     里程碑：`{累计钓获: 文案}`
- ACHIEVEMENTS   成就：`{id: 文案}`（id 会写进玩家存档，别改已有的）

⚠️ 已经上线的 **id 不要改**（存档与图鉴按 id 记录）；只改名字/描述/数值是安全的。
改完重载插件即生效；本文件缺失时插件会用内置兜底内容继续跑。
=========================================================================

字段含义见 main.py 顶部的模块说明。删掉本文件插件仍能启动，
但会退化成「只有内置兜底内容」的精简版。
"""

from __future__ import annotations

COLLECTIBLES: list[dict[str, Any]] = [
    {"id": "seaweed", "name": "海草", "emoji": "🌿", "weight": 30,
     "value": 2, "desc": "缠在钩上的水草，晒干能当柴烧"},
    {"id": "old_boot", "name": "破靴子", "emoji": "🥾", "weight": 22,
     "value": 1, "desc": "谁把靴子扔水里了……"},
    {"id": "tin_can", "name": "铁皮罐", "emoji": "🥫", "weight": 18,
     "value": 3, "desc": "锈迹斑斑，卖废铁也能换点钱"},
    {"id": "drift_bottle", "name": "漂流瓶", "emoji": "🍾", "weight": 16,
     "value": 5, "desc": "瓶里好像塞着什么……"},
    {"id": "starry_shell", "name": "星纹贝", "emoji": "🐚", "weight": 8,
     "value": 25, "desc": "壳上有星形纹路，挺好看"},
    {"id": "sunken_coin", "name": "沉水古币", "emoji": "🪙", "weight": 4,
     "value": 60, "desc": "不知道是哪个朝代的"},
    {"id": "treasure_chest", "name": "沉船宝箱", "emoji": "🎁", "weight": 2,
     "value": 200, "desc": "锈死了，但摇一摇里面叮当作响"},
]


BOTTLE_NOTES: list[str] = [
    "「第 37 天，还是没钓到鲲。」——某位前辈",
    "「据说月华水母只在月圆之夜浮上来。」",
    "「迷雾沼泽东边有个老渔夫，他的饵很特别。」",
    "「我把最好的鱼竿藏在了深海海沟底部。」",
    "「别把田螺卖光，有人专门收。」",
    "「拉线要稳，不要急。」",
    "「这是一张藏宝图的一半……另一半呢？」",
    "「给捡到这张纸条的人：好运。」",
    "「我养的锦鲤跑了。若你钓到它，请善待。」",
    "「秘制饵的配方：蚯蚓 + 虾饵 + 一点点运气。」",
    "「深海鮟鱇的灯，其实是它的诱饵。」",
    "「我已经连续签到 100 天了，你呢？」",
    "「有人说鲲的肚子里装着另一个湖。」",
    "「迷雾沼泽的水，别喝。」",
]


VARIANTS: list[dict[str, Any]] = [
    {"id": "golden", "name": "黄金", "emoji": "🌟", "weight": 34, "mult": 1.30,
     "desc": "通体流金，鳞片像鎏过一样"},
    {"id": "albino", "name": "白化", "emoji": "❄️", "weight": 24, "mult": 1.45,
     "desc": "雪白通透，眼睛泛着淡红"},
    {"id": "crimson", "name": "赤焰", "emoji": "🔥", "weight": 18, "mult": 1.60,
     "desc": "鳍边像烧着一样红"},
    {"id": "jade", "name": "翡翠", "emoji": "💚", "weight": 13, "mult": 1.85,
     "desc": "青碧欲滴，像是玉雕出来的"},
    {"id": "abyss", "name": "幽冥", "emoji": "🌑", "weight": 8, "mult": 2.30,
     "desc": "颜色深得发黑，只有眼睛有一点光"},
    {"id": "prismatic", "name": "虹彩", "emoji": "🌈", "weight": 3, "mult": 3.20,
     "desc": "随光线变化七种颜色，极其罕见"},
]


WEATHERS: list[dict[str, Any]] = [
    {
        "id": "sunny", "name": "晴朗", "emoji": "☀️", "weight": 30,
        "rarity_mult": {}, "window_mult": 1.00, "luck": 0.0, "escape_mult": 1.0,
        "desc": "风和日丽，什么都有可能上钩",
    },
    {
        "id": "cloudy", "name": "多云", "emoji": "⛅", "weight": 24,
        "rarity_mult": {}, "window_mult": 1.05, "luck": 0.02, "escape_mult": 0.95,
        "desc": "天光柔和，鱼口不错",
    },
    {
        "id": "rain", "name": "下雨", "emoji": "🌧️", "weight": 16,
        "rarity_mult": {"少见": 1.4, "稀有": 1.5, "传说": 1.3}, "window_mult": 0.90,
        "luck": 0.04, "escape_mult": 1.0,
        "desc": "雨天鱼更活跃，少见以上的鱼变多",
    },
    {
        "id": "fog", "name": "起雾", "emoji": "🌫️", "weight": 12,
        "rarity_mult": {"稀有": 1.6, "传说": 1.8, "神话": 1.5}, "window_mult": 0.85,
        "luck": 0.03, "escape_mult": 1.1,
        "desc": "雾里看不清浮漂，但大鱼敢靠岸",
    },
    {
        "id": "moon", "name": "月夜", "emoji": "🌙", "weight": 10,
        "rarity_mult": {"传说": 1.7, "神话": 2.0}, "window_mult": 0.92,
        "luck": 0.06, "escape_mult": 1.05,
        "desc": "月华之下，传说与神话之鱼会浮上来",
    },
    {
        "id": "wind", "name": "大风", "emoji": "💨", "weight": 8,
        "rarity_mult": {"少见": 1.3, "稀有": 1.4}, "window_mult": 0.75,
        "luck": 0.02, "escape_mult": 1.25,
        "desc": "风大浪急，拉线窗口更短、更容易跑鱼",
    },
]


EASTER_EGGS: list[dict[str, Any]] = [
    {"id": "coin_in_fish", "weight": 22,
     "text": "🪙 鱼肚子里居然有一枚硬币！", "gold": 12},
    {"id": "old_map", "weight": 16,
     "text": "🗺️ 鱼鳞上沾着一张湿透的旧地图碎片……", "note": True},
    {"id": "lucky_scale", "weight": 18,
     "text": "✨ 摘下一片闪着光的鱼鳞，手感很吉利。", "luck": 0.08},
    {"id": "friend", "weight": 14,
     "text": "🐟 它在你手里安静待了一会儿才松开，像是认识你。", "luck": 0.05},
    {"id": "bait_back", "weight": 12,
     "text": "🪱 收线时鱼饵还好好地挂在钩上。", "heal_bait": True},
    {"id": "pearl_in_mouth", "weight": 10,
     "text": "🦪 鱼嘴里含着一颗小珍珠！", "gold": 35},
    {"id": "lost_ring", "weight": 5,
     "text": "💍 鱼线上缠着一枚旧戒指，不知道是谁的。", "gold": 80},
]


RANDOM_EVENTS: list[dict[str, Any]] = [
    {
        "id": "ripple", "weight": 22,
        "text": "水面忽然划开一道细纹，像是有什么贴着水皮过去了",
        "choices": [
            {"label": "追着看", "text": "你顺着那道纹把钩甩了过去",
             "good": "钩子刚沉下去就被撞了一下，收上来一看，钩上挂着几枚被水泡软的铜钱。",
             "gold": 25, "idle": "什么也没追到，只有一圈圈散开的水纹。"},
            {"label": "按兵不动", "text": "你按住竿子，等它自己散掉",
             "good": "过了一会儿，水面上浮起一小片亮晶晶的鳞。",
             "note": True, "idle": "水面重新平了下来，像什么都没发生过。"},
        ],
    },
    {
        "id": "old_net", "weight": 18,
        "text": "钓线挂住了一团发黑的旧渔网",
        "choices": [
            {"label": "拽上来", "text": "你使劲把网拖到岸边",
             "good": "网眼里卡着几枚还能用的鱼饵，你顺手收下了。",
             "bait": 3, "idle": "网烂得一扯就碎，只留下一手黑泥。"},
            {"label": "割断它", "text": "你换了个位置重新下钩",
             "good": "省了力气，也躲开了缠线的麻烦。",
             "idle": "换了地方，水面安静得有点过分。"},
        ],
    },
    {
        "id": "stranger", "weight": 16,
        "text": "岸上有人喊你，问今天行情怎么样",
        "choices": [
            {"label": "聊两句", "text": "你回头跟那人聊了几句",
             "good": "临走他塞给你一点钱，说「买包烟」。",
             "gold": 40, "idle": "聊了半天，他只说了句「今天风大」。"},
            {"label": "专心钓鱼", "text": "你没回头，盯着浮漂",
             "good": "浮漂在这时候轻轻点了一下。",
             "luck": 0.06, "idle": "浮漂纹丝不动。"},
        ],
    },
    {
        "id": "crate", "weight": 14,
        "text": "一只木箱慢慢从上游漂过来，撞在脚边的石头上",
        "choices": [
            {"label": "撬开看看", "text": "你用竿梢把箱盖撬开",
             "good": "里面是些泡烂的旧东西，底下压着几枚硬币。",
             "gold": 60, "idle": "箱子里只有水和泥。"},
            {"label": "推回水里", "text": "你把它推回水流里",
             "good": "箱子打着转漂远了，水面下似乎有影子跟着它。",
             "note": True, "idle": "箱子卡在石头缝里，纹丝不动。"},
        ],
    },
    {
        "id": "whisper", "weight": 12,
        "text": "水底传来一阵很低的震动，像是远处有什么在翻身",
        "choices": [
            {"label": "贴耳细听", "text": "你把耳朵凑近水面",
             "good": "那声音听久了，手上忽然稳得出奇。",
             "luck": 0.12, "idle": "只听见自己的心跳。"},
            {"label": "赶紧收线", "text": "你利索地把线收了回来",
             "good": "收线时钩上还带上来一小丛水草。",
             "note": True, "idle": "线收得干干净净。"},
        ],
    },
    {
        "id": "lost_hook", "weight": 12,
        "text": "钩上挂着一枚别人断线留下的旧钩",
        "choices": [
            {"label": "收起来", "text": "你把旧钩摘下来收好",
             "good": "钩子还能用，你顺手换上了自己的线。",
             "bait": 2, "idle": "钩子锈得掰一下就断。"},
            {"label": "扔回去", "text": "你把它弹回水里",
             "good": "水面响了一声，像是有什么接住了它。",
             "note": True, "idle": "只荡开一圈涟漪。"},
        ],
    },
    {
        "id": "cold_snap", "weight": 10,
        "text": "水面忽然起了一层白雾，冷得指尖发麻",
        "choices": [
            {"label": "坚持守着", "text": "你把衣领竖起来继续盯漂",
             "good": "雾散的时候，钩上已经挂了东西。",
             "gold": 45, "idle": "雾散了，钩上空空如也。"},
            {"label": "先暖暖手", "text": "你把手揣进兜里歇了一会儿",
             "good": "再握竿时手感清楚了不少。",
             "luck": 0.08, "idle": "手是暖了，鱼也走了。"},
        ],
    },
    {
        "id": "kid_asking", "weight": 10,
        "text": "岸边有个小孩凑过来，问你在钓什么",
        "choices": [
            {"label": "教他两句", "text": "你给他讲了讲怎么看漂",
             "good": "小孩听得认真，塞给你一颗糖和几枚硬币。",
             "gold": 30, "idle": "他听完就跑开了。"},
            {"label": "让他安静", "text": "你比了个嘘的手势",
             "good": "他乖乖蹲在旁边看，水面一直很静。",
             "luck": 0.05, "idle": "他撇撇嘴走了。"},
        ],
    },
    {
        "id": "old_song", "weight": 8,
        "text": "对岸有人在放很老的歌，一句一句飘过来",
        "choices": [
            {"label": "跟着哼", "text": "你跟着调子哼了两句",
             "good": "心情松下来，手上也稳了。",
             "luck": 0.10, "idle": "调子跑得太远，你自己都笑了。"},
            {"label": "专心钓鱼", "text": "你把它当背景音",
             "good": "歌声里，浮漂轻轻点了一下。",
             "gold": 20, "idle": "歌放完了，漂没动。"},
        ],
    },
    {
        "id": "sunken_bell", "weight": 6,
        "text": "竿梢碰到水底一个硬东西，发出闷闷的一声",
        "choices": [
            {"label": "捞上来看看", "text": "你小心地把它带出水面",
             "good": "是一枚小铜铃，擦干净还能响。",
             "gold": 80, "idle": "只是块石头，白费力气。"},
            {"label": "绕开它", "text": "你换了半米的位置重新下钩",
             "good": "新位置底下干净，钩落得很顺。",
             "note": True, "idle": "换了个地方，照样挂底。"},
        ],
    },
]


MILESTONES: dict[int, str] = {
    1: "🎉 第一条鱼！这门手艺算是入门了",
    10: "🎉 十条了，你开始摸清这片水的脾气",
    25: "🎉 二十五条——村里人都认识你了",
    50: "🎉 五十条！背包的味道已经洗不掉了",
    100: "🎉 一百条。你成了这片水域的常客",
    200: "🎉 两百条。有人开始叫你「老师傅」",
    300: "🎉 三百条。你钓上来的鱼能装满一条小船",
    500: "🎉 五百条。传说中的人物，就是你",
}


ACHIEVEMENTS: dict[str, str] = {
    "first_fish": "🎣 初次垂钓：钓到第一条鱼",
    "catch_10": "📦 小有收获：累计钓到 10 条",
    "catch_50": "🏆 垂钓达人：累计钓到 50 条",
    "catch_200": "👑 一代钓神：累计钓到 200 条",
    "catch_500": "🌊 渔获如山：累计钓到 500 条",
    "rare_hunter": "🐡 稀有猎手：钓到稀有鱼种",
    "legend_hunter": "✨ 传说猎手：钓到传说鱼种",
    "myth_hunter": "🌈 神话猎手：钓到神话鱼种",
    "perfect_one": "🟣 极品收获：钓到极品个体",
    "mythic_one": "🌟 天选之鱼：钓到传说个体",
    "perfect_three": "🔥 极品三连：背包里有 3 条极品及以上",
    "perfect_pull": "⚡ 神之一手：完美拉线成功",
    "perfect_10": "🎯 稳如老狗：完美拉线 10 次",
    "clutch_win": "😱 惊险一刻：偏差拉线仍把鱼拉了上来",
    "collector_10": "📖 小收藏家：图鉴收集 10 种",
    "collector_20": "📚 收藏家：图鉴收集 20 种",
    "collector_all": "🏅 大全套：图鉴全部集齐",
    "junk_5": "🗑️ 打捞队员：收集 5 种杂物",
    "junk_all": "🧭 海底清道夫：收集全部杂物",
    "note_5": "📜 读信人：收集 5 张纸条",
    "note_all": "🍾 瓶中信：收集全部纸条",
    "aquarist": "🐠 水族爱好者：水族馆住满",
    "feeder": "🌾 饲养员：投喂累计 20 次",
    "feeder_100": "🍖 金牌饲养员：投喂累计 100 次",
    "attr_max": "💯 满值之鱼：把一条鱼的某项三维喂到 100",
    "rich_1000": "💰 小富即安：金币达到 1000",
    "rich_10000": "💎 腰缠万贯：金币达到 10000",
    "rich_100000": "🏦 一方富豪：金币达到 100000",
    "first_order": "📋 接单达人：完成第一笔订单",
    "order_30": "🚚 金牌供货商：累计完成 30 笔订单",
    "rod_2": "🎣 鸟枪换炮：拥有第二根鱼竿",
    "rod_all": "🌈 钓具收藏家：集齐全部鱼竿",
    "loc_2": "🗺️ 走出新手村：解锁第二个钓点",
    "loc_all": "🧭 踏遍天涯：解锁全部钓点",
    "full_bag": "🎒 满载而归：背包装到上限",
    "first_variant": "🧬 万里挑一：钓到第一条变异个体",
    "variant_3": "🧬 变异收藏家：收集 3 种变异",
    "variant_all": "🌈 七彩祥瑞：集齐全部变异",
    "prismatic_one": "🌈 虹彩之约：钓到最罕见的虹彩变异",
    "bad_weather": "💨 风雨无阻：在大风天成功钓上传说鱼",
    "moon_hunter": "🌙 月下独钓：在月夜钓到神话鱼",
    "market_master": "📈 行情猎手：单次卖鱼触发 2000 金币以上的行情加成",
    "codex_common": "📗 常见全收集：集齐所有常见鱼（价值+3%）",
    "codex_rare": "📘 稀有全收集：集齐所有稀有鱼（价值+8%）",
    "codex_all": "📕 图鉴大成：集齐全部鱼种（价值+20%）",
    "pond_first": "🏞️ 鱼塘初收：第一次领取鱼塘收益",
    "pond_big": "💦 鱼塘丰收：一次领取 1000 金币以上的鱼塘收益",
    "egg_first": "🥚 意外之喜：钓上来时碰上过一次说不清的事",
    "egg_collector": "🥚 见多识广：碰上过 5 种说不清的事",
    "record_5": "📈 五项全能：五个品质都留下了最佳纪录",
    "record_10k": "💎 万元户：单条鱼价值突破 10000 金币",
}


# =============================================================================
# 内容表默认值（供配置项 collectible_defs / variant_defs / weather_defs /
# easter_egg_defs 使用；由脚本从上面的常量导出，改数据请直接改上面的常量后重跑导出）
# 行格式：
#   杂物  id|名称|emoji|权重|价值|说明
#   变异  id|名称|emoji|相对权重|价值倍率|说明
#   天气  id|名称|emoji|权重|稀有度倍率|窗口倍率|运气|逃脱倍率|说明
#   彩蛋  id|权重|文案|效果(gold=…;note=1;luck=…;heal_bait=1)
#   按钮  场景|文案|点击后发送|样式（见文件末尾 BUTTON_DEFS_DEFAULT 的说明）
# =============================================================================

COLLECTIBLE_DEFS_DEFAULT: str = """\
seaweed|海草|🌿|30|2|缠在钩上的水草，晒干能当柴烧
old_boot|破靴子|🥾|22|1|谁把靴子扔水里了……
tin_can|铁皮罐|🥫|18|3|锈迹斑斑，卖废铁也能换点钱
drift_bottle|漂流瓶|🍾|16|5|瓶里好像塞着什么……
starry_shell|星纹贝|🐚|8|25|壳上有星形纹路，挺好看
sunken_coin|沉水古币|🪙|4|60|不知道是哪个朝代的
treasure_chest|沉船宝箱|🎁|2|200|锈死了，但摇一摇里面叮当作响
"""

VARIANT_DEFS_DEFAULT: str = """\
golden|黄金|🌟|34|1.3|通体流金，鳞片像鎏过一样
albino|白化|❄️|24|1.45|雪白通透，眼睛泛着淡红
crimson|赤焰|🔥|18|1.6|鳍边像烧着一样红
jade|翡翠|💚|13|1.85|青碧欲滴，像是玉雕出来的
abyss|幽冥|🌑|8|2.3|颜色深得发黑，只有眼睛有一点光
prismatic|虹彩|🌈|3|3.2|随光线变化七种颜色，极其罕见
"""

WEATHER_DEFS_DEFAULT: str = """\
sunny|晴朗|☀️|30||1.0|0.0|1.0|风和日丽，什么都有可能上钩
cloudy|多云|⛅|24||1.05|0.02|0.95|天光柔和，鱼口不错
rain|下雨|🌧️|16|少见:1.4,稀有:1.5,传说:1.3|0.9|0.04|1.0|雨天鱼更活跃，少见以上的鱼变多
fog|起雾|🌫️|12|稀有:1.6,传说:1.8,神话:1.5|0.85|0.03|1.1|雾里看不清浮漂，但大鱼敢靠岸
moon|月夜|🌙|10|传说:1.7,神话:2.0|0.92|0.06|1.05|月华之下，传说与神话之鱼会浮上来
wind|大风|💨|8|少见:1.3,稀有:1.4|0.75|0.02|1.25|风大浪急，拉线窗口更短、更容易跑鱼
"""

EASTER_EGG_DEFS_DEFAULT: str = """\
coin_in_fish|22|🪙 鱼肚子里居然有一枚硬币！|gold=12
old_map|16|🗺️ 鱼鳞上沾着一张湿透的旧地图碎片……|note=1
lucky_scale|18|✨ 摘下一片闪着光的鱼鳞，手感很吉利。|luck=0.08
friend|14|🐟 它在你手里安静待了一会儿才松开，像是认识你。|luck=0.05
bait_back|12|🪱 收线时鱼饵还好好地挂在钩上。|heal_bait=1
pearl_in_mouth|10|🦪 鱼嘴里含着一颗小珍珠！|gold=35
lost_ring|5|💍 鱼线上缠着一枚旧戒指，不知道是谁的。|gold=80
"""


# =============================================================================
# button_defs 默认值（在配置面板 / 编辑器页面里改的就是这段文本）
# 行格式：
#   场景|按钮文案|点击后发送|样式
#   场景  cast=抛竿结果 / pull=咬钩提示（拉线） / bag=背包视图
#         location=钓点视图 / story=随机插曲（模板：{label}=选项文案，{n}=第几个选项）
#   样式  default（灰）/ primary（蓝），也可以直接写数字（QQ 的 render_data.style）
#   同一场景的多行 = 多个按钮，顺序即显示顺序；每行最多摆几个由代码决定
#   （cast/bag/location/pull 每行 3 个，story 每个选项一行）
#   点击后发送必须是本插件认识的指令（/钓鱼 …），否则这一行会被跳过
# =============================================================================

BUTTON_DEFS_DEFAULT: str = """\
cast|再来一竿|/钓鱼|default
cast|看背包|/钓鱼 背包|default
cast|今日|/钓鱼 今日|default
cast|卖光光|/钓鱼 卖光光|default
cast|帮助|/钓鱼 帮助|default
pull|拉线！|/钓鱼 拉|primary
bag|卖光光|/钓鱼 卖光光|default
bag|水族馆|/钓鱼 水族馆|default
bag|再来一竿|/钓鱼|default
location|查图鉴|/钓鱼 图鉴|default
location|背包|/钓鱼 背包|default
location|今日|/钓鱼 今日|default
story|{label}|/钓鱼 事件 {n}|default"""
