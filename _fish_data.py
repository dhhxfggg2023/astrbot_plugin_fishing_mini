# -*- coding: utf-8 -*-
"""鱼种名单（按钓点分组），供 main.py 合并进 FISH_POOL。

============================ 想增删鱼？改这个文件 ============================
每条鱼一行，字段含义：
  id      唯一标识。**写进玩家存档与图鉴**，已经存在的 id 不要改（改了等于换了一条鱼）
  name    展示名（也要唯一，`/钓鱼 查 <名字>` 靠它定位；重名会取第一条）
  rarity  鱼种品质：常见 / 少见 / 稀有 / 传说 / 神话
          —— 它的**出现权重**由配置 `rarity_spawn_weights` 决定，价值倍率由
             `rarity_value_factors` 决定，不用在这里写权重
  flavor  图鉴里的一句风味描述（简短即可）

想调某条鱼的价格：不要改这里，用配置 `fish_value_overrides`（如 `锦鲤:500`）
或全局倍率 `fish_value_mult` —— 那样升级时不会和默认值打架。

键是钓点 id（novice/bamboo/…），与配置 `location_defs` 里的 id 对应；
main.py 会把这些鱼登记进对应钓点的鱼池，并按上面的稀有度权重参与抽取。
=============================================================================
"""

LOCATION_ROSTERS: dict[str, list[dict]] = {
    "novice": [
        {"id": "novice_roach", "name": "白条", "rarity": "常见", "flavor": "细长的小鱼抢饵最积极"},
        {"id": "novice_crucian", "name": "鲫鱼", "rarity": "常见", "flavor": "池塘里最老实的小家伙"},
        {"id": "novice_loach", "name": "泥鳅", "rarity": "常见", "flavor": "滑得抓不住的黄褐色小条"},
        {"id": "novice_stone_moroko", "name": "麦穗鱼", "rarity": "常见", "flavor": "身侧一道黑线爱啄钩"},
        {"id": "novice_shrimp", "name": "小河虾", "rarity": "常见", "flavor": "透明得能看见肚里的沙"},
        {"id": "novice_snail", "name": "田螺", "rarity": "常见", "flavor": "壳口一圈青苔还带着泥"},
        {"id": "novice_bitterling", "name": "鳑鲏", "rarity": "常见", "flavor": "阳光下闪出彩虹般的鳞"},
        {"id": "novice_yellow_catfish", "name": "黄颡鱼", "rarity": "少见", "flavor": "背鳍竖起来扎手的黄胡子"},
        {"id": "novice_carp", "name": "鲤鱼", "rarity": "少见", "flavor": "尾巴一甩溅起半池水花"},
        {"id": "novice_mussel", "name": "河蚌", "rarity": "少见", "flavor": "壳缝里吐出一串小气泡"},
        {"id": "novice_turtle", "name": "巴西龟", "rarity": "少见", "flavor": "被人放生后长满了绿藻"},
        {"id": "novice_koi", "name": "锦鲤", "rarity": "稀有", "flavor": "红白花纹在浅水里游弋"},
    ],
    "bamboo": [
        {"id": "bamboo_chub", "name": "溪哥", "rarity": "常见", "flavor": "鳞片闪着溪水的亮光"},
        {"id": "bamboo_horsemouth", "name": "马口鱼", "rarity": "常见", "flavor": "嘴大贪吃见饵就往前冲"},
        {"id": "bamboo_river_snail", "name": "山坑螺", "rarity": "常见", "flavor": "吸在石头上扣都扣不动"},
        {"id": "bamboo_goby", "name": "虾虎鱼", "rarity": "常见", "flavor": "趴在卵石上一动不动"},
        {"id": "bamboo_barb", "name": "小鲃", "rarity": "常见", "flavor": "竹影下成群游来游去"},
        {"id": "bamboo_stream_crab", "name": "溪蟹", "rarity": "常见", "flavor": "举着小钳子横着走路"},
        {"id": "bamboo_labiobarbus", "name": "光唇鱼", "rarity": "少见", "flavor": "嘴唇厚实最爱啃青苔"},
        {"id": "bamboo_spiny_eel", "name": "刺鳅", "rarity": "少见", "flavor": "浑身细刺钻进石缝里"},
        {"id": "bamboo_corbicula", "name": "河蚬", "rarity": "少见", "flavor": "沙底上一排小小的纹路"},
        {"id": "bamboo_spiny_frog", "name": "棘胸蛙", "rarity": "少见", "flavor": "蹲在湿石上鼓着腮帮"},
        {"id": "bamboo_giant_salamander", "name": "娃娃鱼", "rarity": "稀有", "flavor": "溪水深处传来婴儿哭声"},
        {"id": "bamboo_peach_jellyfish", "name": "桃花水母", "rarity": "稀有", "flavor": "水中漂浮的淡粉色小伞"},
    ],
    "canal": [
        {"id": "canal_tilapia", "name": "罗非鱼", "rarity": "常见", "flavor": "污水里也活得膘肥体壮"},
        {"id": "canal_crayfish", "name": "小龙虾", "rarity": "常见", "flavor": "举着红钳从石缝里爬出"},
        {"id": "canal_old_boot", "name": "破靴子", "rarity": "常见", "flavor": "靴筒里还藏着两只小虾"},
        {"id": "canal_soda_can", "name": "易拉罐", "rarity": "常见", "flavor": "罐口卡着一只小螃蟹"},
        {"id": "canal_mosquitofish", "name": "食蚊鱼", "rarity": "常见", "flavor": "水面上一群指头长的小鱼"},
        {"id": "canal_mud_carp", "name": "鲮鱼", "rarity": "常见", "flavor": "贴着河底慢慢啃食青泥"},
        {"id": "canal_walking_catfish", "name": "塘鲺", "rarity": "少见", "flavor": "能在湿泥里扭着爬行"},
        {"id": "canal_mitten_crab", "name": "中华绒螯蟹", "rarity": "少见", "flavor": "毛茸茸的钳子夹住鱼线"},
        {"id": "canal_ricefield_eel", "name": "黄鳝", "rarity": "少见", "flavor": "从石阶缝里滑出半条身子"},
        {"id": "canal_silver_carp", "name": "鲢鱼", "rarity": "少见", "flavor": "受惊时跃出水面半米高"},
        {"id": "canal_alligator_snapper", "name": "鳄龟", "rarity": "稀有", "flavor": "咬合力惊人的放生怪物"},
        {"id": "canal_chinese_sucker", "name": "胭脂鱼", "rarity": "稀有", "flavor": "背鳍如帆通体泛着红光"},
    ],
    "lake": [
        {"id": "lake_bream", "name": "鳊鱼", "rarity": "常见", "flavor": "侧扁的身子像一片银叶"},
        {"id": "lake_icefish", "name": "银鱼", "rarity": "常见", "flavor": "几乎透明的细软小鱼"},
        {"id": "lake_shrimp", "name": "湖虾", "rarity": "常见", "flavor": "长须在水草间轻轻摆动"},
        {"id": "lake_grass_carp", "name": "草鱼", "rarity": "常见", "flavor": "拖着水草在浅湾里转悠"},
        {"id": "lake_whitefish", "name": "翘嘴白鱼", "rarity": "常见", "flavor": "追着小鱼把水面炸开花"},
        {"id": "lake_carp", "name": "鲤鱼", "rarity": "少见", "flavor": "金鳞一闪就钻进深水"},
        {"id": "lake_bighead_carp", "name": "鳙鱼", "rarity": "少见", "flavor": "大头慢慢吞下一口浮游"},
        {"id": "lake_catfish", "name": "鲶鱼", "rarity": "少见", "flavor": "两根长须在浑水里探路"},
        {"id": "lake_mussel", "name": "河蚌", "rarity": "少见", "flavor": "在泥底拖出一道浅沟"},
        {"id": "lake_black_carp", "name": "青鱼", "rarity": "稀有", "flavor": "力大得能把竿拉成弓"},
        {"id": "lake_mandarin_fish", "name": "鳜鱼", "rarity": "稀有", "flavor": "斑纹藏在石缝里等猎物"},
        {"id": "lake_giant_softshell", "name": "百年老鳖", "rarity": "稀有", "flavor": "背上刻满岁月的旧纹"},
    ],
    "reed": [
        {"id": "reed_frog", "name": "青蛙", "rarity": "常见", "flavor": "扑通一声跳进芦苇丛"},
        {"id": "reed_water_snake", "name": "水蛇", "rarity": "常见", "flavor": "贴着水面划出长长波纹"},
        {"id": "reed_reed_shrimp", "name": "芦苇虾", "rarity": "常见", "flavor": "攀在苇秆上随波摇晃"},
        {"id": "reed_river_snail", "name": "螺蛳", "rarity": "常见", "flavor": "壳上粘着几缕新鲜水草"},
        {"id": "reed_stone_moroko", "name": "麦穗鱼", "rarity": "常见", "flavor": "在苇影里成群打转"},
        {"id": "reed_yellow_catfish", "name": "黄颡鱼", "rarity": "少见", "flavor": "扎手的黄胡子在泥里拱"},
        {"id": "reed_river_crab", "name": "河蟹", "rarity": "少见", "flavor": "八条腿横着穿过浅滩"},
        {"id": "reed_leech", "name": "蚂蟥", "rarity": "少见", "flavor": "吸在腿上怎么拽都不松"},
        {"id": "reed_freshwater_prawn", "name": "青虾", "rarity": "少见", "flavor": "弹尾一缩窜出半米远"},
        {"id": "reed_snakehead", "name": "黑鱼", "rarity": "稀有", "flavor": "黑背破水像一枚鱼雷"},
        {"id": "reed_softshell_turtle", "name": "甲鱼", "rarity": "稀有", "flavor": "伸长脖子一口咬住饵"},
        {"id": "reed_pearl_mussel", "name": "珍珠蚌", "rarity": "稀有", "flavor": "壳里含着颗温润的珠子"},
    ],
    "sea": [
        {"id": "sea_hairtail", "name": "带鱼", "rarity": "常见", "flavor": "银亮长身像一把快刀"},
        {"id": "sea_yellow_croaker", "name": "黄鱼", "rarity": "常见", "flavor": "出水时咕咕叫个不停"},
        {"id": "sea_swimming_crab", "name": "梭子蟹", "rarity": "常见", "flavor": "两把尖钳护着满壳蟹黄"},
        {"id": "sea_white_shrimp", "name": "白虾", "rarity": "常见", "flavor": "一网拉上来活蹦乱跳"},
        {"id": "sea_pomfret", "name": "鲳鱼", "rarity": "少见", "flavor": "扁得像面镜子闪着光"},
        {"id": "sea_squid", "name": "鱿鱼", "rarity": "少见", "flavor": "喷出一团墨汁就逃走"},
        {"id": "sea_spanish_mackerel", "name": "鲅鱼", "rarity": "少见", "flavor": "银蓝背上满是花纹"},
        {"id": "sea_jellyfish", "name": "海蜇", "rarity": "少见", "flavor": "半透明地一伸又一缩"},
        {"id": "sea_red_seabream", "name": "真鲷", "rarity": "稀有", "flavor": "通体粉红像海里的樱花"},
        {"id": "sea_lobster", "name": "大龙虾", "rarity": "稀有", "flavor": "长须一弹溅起一片水花"},
        {"id": "sea_tuna", "name": "金枪鱼", "rarity": "稀有", "flavor": "肌肉绷紧拖着鱼线狂奔"},
        {"id": "sea_oarfish", "name": "皇带鱼", "rarity": "传说", "flavor": "银带般的身子长得望不到头"},
    ],
    "dock": [
        {"id": "dock_seabass", "name": "鲈鱼", "rarity": "常见", "flavor": "在桥墩阴影里伏击小鱼"},
        {"id": "dock_old_tire", "name": "旧轮胎", "rarity": "常见", "flavor": "胎壁里住着一窝小蟹"},
        {"id": "dock_shore_crab", "name": "小螃蟹", "rarity": "常见", "flavor": "举着钳子卡住鱼钩不放"},
        {"id": "dock_sardine", "name": "沙丁鱼", "rarity": "常见", "flavor": "成群绕着木桩打转"},
        {"id": "dock_eel", "name": "鳗鱼", "rarity": "少见", "flavor": "缠住鱼线怎么都解不开"},
        {"id": "dock_octopus", "name": "章鱼", "rarity": "少见", "flavor": "八条腕抱着鱼饵不撒手"},
        {"id": "dock_starfish", "name": "海星", "rarity": "少见", "flavor": "五只角慢慢翻过木桩"},
        {"id": "dock_barnacle", "name": "藤壶", "rarity": "少见", "flavor": "密密麻麻糊满整块船板"},
        {"id": "dock_black_seabream", "name": "黑鲷", "rarity": "稀有", "flavor": "黑色鳍边一收就钻底"},
        {"id": "dock_stonefish", "name": "石头鱼", "rarity": "稀有", "flavor": "装作石块等人踩上去"},
        {"id": "dock_seahorse", "name": "海马", "rarity": "稀有", "flavor": "蜷着尾巴挂在旧缆绳上"},
        {"id": "dock_megalodon", "name": "巨齿鲨", "rarity": "传说", "flavor": "旧码头外掀起三米巨浪"},
    ],
    "night": [
        {"id": "night_catfish", "name": "鲶鱼", "rarity": "常见", "flavor": "月光下贴着堤根巡游"},
        {"id": "night_loach", "name": "泥鳅", "rarity": "常见", "flavor": "夜里钻出泥底四处乱窜"},
        {"id": "night_river_shrimp", "name": "河虾", "rarity": "常见", "flavor": "月影里闪着透明的小点"},
        {"id": "night_topmouth", "name": "翘嘴白鱼", "rarity": "常见", "flavor": "炸水声传遍整个堤坝"},
        {"id": "night_lanternfish", "name": "灯笼鱼", "rarity": "少见", "flavor": "一排小灯在暗水里游过"},
        {"id": "night_opah", "name": "月鱼", "rarity": "少见", "flavor": "鳞片映着月色泛出红光"},
        {"id": "night_glow_jellyfish", "name": "夜光水母", "rarity": "少见", "flavor": "蓝光一闪一闪浮上来"},
        {"id": "night_giant_catfish", "name": "巨型鲶鱼", "rarity": "稀有", "flavor": "拖得鱼竿弯进水里"},
        {"id": "night_moonlit_eel", "name": "银月鳗", "rarity": "稀有", "flavor": "银色身子像一道月光"},
        {"id": "night_ghost_lanternfish", "name": "幽灵灯笼鱼", "rarity": "稀有", "flavor": "幽绿的光在深处熄灭"},
        {"id": "night_glow_shark", "name": "夜光鲨", "rarity": "稀有", "flavor": "背鳍划开墨色的水面"},
        {"id": "night_white_dragon_fish", "name": "白龙鱼", "rarity": "传说", "flavor": "月光里跃出一条白影"},
    ],
    "mangrove": [
        {"id": "mangrove_mudskipper", "name": "弹涂鱼", "rarity": "常见", "flavor": "用胸鳍在泥滩上蹦跳"},
        {"id": "mangrove_fiddler_crab", "name": "招潮蟹", "rarity": "常见", "flavor": "举着一只大钳来回招手"},
        {"id": "mangrove_oyster", "name": "牡蛎", "rarity": "常见", "flavor": "牢牢粘在红树根上"},
        {"id": "mangrove_baby_shark", "name": "幼鲨", "rarity": "少见", "flavor": "在浅湾里练习捕猎"},
        {"id": "mangrove_mud_snail", "name": "泥螺", "rarity": "少见", "flavor": "拖着黏液在泥面爬行"},
        {"id": "mangrove_prawn", "name": "基围虾", "rarity": "少见", "flavor": "在树根之间弹跳逃窜"},
        {"id": "mangrove_mud_crab", "name": "青蟹", "rarity": "少见", "flavor": "青壳大钳夹得人发麻"},
        {"id": "mangrove_horseshoe_crab", "name": "中华鲎", "rarity": "稀有", "flavor": "蓝血甲壳像一顶小钢盔"},
        {"id": "mangrove_saltwater_crocodile", "name": "湾鳄", "rarity": "稀有", "flavor": "浮木般的背脊缓缓移动"},
        {"id": "mangrove_giant_python", "name": "巨蟒", "rarity": "稀有", "flavor": "从树根间垂下半个身子"},
        {"id": "mangrove_dugong", "name": "儒艮", "rarity": "稀有", "flavor": "笨拙地抱着水草啃食"},
        {"id": "mangrove_flood_dragon", "name": "蛟龙", "rarity": "传说", "flavor": "潮水翻涌时探出长角"},
    ],
    "swamp": [
        {"id": "swamp_marsh_shrimp", "name": "沼虾", "rarity": "常见", "flavor": "雾气里挥着细长的钳"},
        {"id": "swamp_water_snail", "name": "水蜗牛", "rarity": "常见", "flavor": "背着壳在浮叶上爬"},
        {"id": "swamp_newt", "name": "蝾螈", "rarity": "常见", "flavor": "橙腹的小东西扭着尾巴"},
        {"id": "swamp_ricefield_eel", "name": "黄鳝", "rarity": "少见", "flavor": "从腐叶底下滑出一截"},
        {"id": "swamp_marsh_turtle", "name": "沼泽龟", "rarity": "少见", "flavor": "壳上长满绿毛像块石头"},
        {"id": "swamp_water_snake", "name": "水蛇", "rarity": "少见", "flavor": "雾气中划开一道水痕"},
        {"id": "swamp_toad", "name": "蟾蜍", "rarity": "少见", "flavor": "鼓着喉咙发出低哑叫声"},
        {"id": "swamp_alligator_gar", "name": "鳄雀鳝", "rarity": "稀有", "flavor": "满口尖牙咬得鱼线发白"},
        {"id": "swamp_loach_king", "name": "泥鳅王", "rarity": "稀有", "flavor": "手臂粗的泥鳅搅翻泥浆"},
        {"id": "swamp_giant_leech", "name": "巨型水蛭", "rarity": "稀有", "flavor": "黑亮的身子缠上小腿"},
        {"id": "swamp_nine_eyed_fish", "name": "九目怪鱼", "rarity": "传说", "flavor": "九只眼睛同时转向你"},
        {"id": "swamp_ghost_catfish", "name": "幽冥巨鲶", "rarity": "传说", "flavor": "雾里传来低沉的吞咽声"},
    ],
    "cave": [
        {"id": "cave_blindfish", "name": "盲鱼", "rarity": "常见", "flavor": "没有眼睛也能找到饵"},
        {"id": "cave_cave_shrimp", "name": "洞穴虾", "rarity": "常见", "flavor": "通体雪白在暗水里漂"},
        {"id": "cave_albino_tadpole", "name": "白化蝌蚪", "rarity": "常见", "flavor": "苍白的小尾巴摆动不停"},
        {"id": "cave_cave_snail", "name": "洞穴螺", "rarity": "少见", "flavor": "壳薄得能看见里面的肉"},
        {"id": "cave_albino_catfish", "name": "白化鲶", "rarity": "少见", "flavor": "白色长须在黑暗里摸索"},
        {"id": "cave_blind_eel", "name": "盲眼鳗", "rarity": "少见", "flavor": "顺着暗流无声地滑过"},
        {"id": "cave_olm", "name": "洞螈", "rarity": "稀有", "flavor": "粉白的小龙趴在石上"},
        {"id": "cave_albino_giant_salamander", "name": "白化巨鲵", "rarity": "稀有", "flavor": "巨大白影贴着河底移动"},
        {"id": "cave_stone_spirit_fish", "name": "石灵鱼", "rarity": "稀有", "flavor": "鳞片像会呼吸的钟乳石"},
        {"id": "cave_glow_jellyfish", "name": "幽光水母", "rarity": "稀有", "flavor": "幽蓝冷光点亮整条暗河"},
        {"id": "cave_blind_white_dragon", "name": "盲眼白龙", "rarity": "传说", "flavor": "无眼的巨首擦过岩壁"},
        {"id": "cave_earthcore_sturgeon", "name": "地心鲟", "rarity": "传说", "flavor": "骨板厚重如远古铠甲"},
    ],
    "ruins": [
        {"id": "ruins_grouper", "name": "石斑鱼", "rarity": "常见", "flavor": "斑驳的身影守在船舷边"},
        {"id": "ruins_clownfish", "name": "小丑鱼", "rarity": "常见", "flavor": "在海葵触手间钻来钻去"},
        {"id": "ruins_sea_turtle", "name": "海龟", "rarity": "少见", "flavor": "慢悠悠绕过断裂的桅杆"},
        {"id": "ruins_lionfish", "name": "狮子鱼", "rarity": "少见", "flavor": "张开满身毒鳍缓缓逼近"},
        {"id": "ruins_sea_urchin", "name": "海胆", "rarity": "少见", "flavor": "黑刺扎进手套里发麻"},
        {"id": "ruins_wreck_crab", "name": "沉船蟹", "rarity": "少见", "flavor": "从锈蚀的舱门里爬出"},
        {"id": "ruins_treasure_chest_fish", "name": "宝箱鱼", "rarity": "稀有", "flavor": "鳞片像铜锁闪着暗光"},
        {"id": "ruins_giant_grouper", "name": "巨型石斑", "rarity": "稀有", "flavor": "一张嘴能吞下整条鱼"},
        {"id": "ruins_rusty_swordfish", "name": "锈剑鱼", "rarity": "稀有", "flavor": "长吻上缠着旧船的铁锈"},
        {"id": "ruins_pearl_king_clam", "name": "珍珠王贝", "rarity": "稀有", "flavor": "壳里滚出拳头大的珍珠"},
        {"id": "ruins_ghost_shark", "name": "幽灵鲨", "rarity": "传说", "flavor": "半透明的影子穿过船骸"},
        {"id": "ruins_sunken_leviathan", "name": "沉船巨鲲", "rarity": "传说", "flavor": "船体震动它正从下方经过"},
    ],
    "abyss": [
        {"id": "abyss_deep_sea_shrimp", "name": "深海虾", "rarity": "常见", "flavor": "苍白细腿在泥底摸索"},
        {"id": "abyss_deep_sea_crab", "name": "深海蟹", "rarity": "常见", "flavor": "盲眼长腿抱着一块腐肉"},
        {"id": "abyss_deep_sea_eel", "name": "深海鳗", "rarity": "少见", "flavor": "张开大口等着一切落下"},
        {"id": "abyss_sail_jellyfish", "name": "帆水母", "rarity": "少见", "flavor": "像一片蓝色小帆漂过去"},
        {"id": "abyss_deep_octopus", "name": "深海章鱼", "rarity": "少见", "flavor": "吸盘在舷边留下圆痕"},
        {"id": "abyss_anglerfish", "name": "鮟鱇鱼", "rarity": "稀有", "flavor": "头顶小灯照出满口尖牙"},
        {"id": "abyss_giant_squid", "name": "巨型乌贼", "rarity": "稀有", "flavor": "触腕上密布着倒钩"},
        {"id": "abyss_vampire_squid", "name": "吸血乌贼", "rarity": "稀有", "flavor": "披着黑红斗篷缓缓飘过"},
        {"id": "abyss_dragonfish", "name": "深海龙鱼", "rarity": "稀有", "flavor": "獠牙长到合不上嘴"},
        {"id": "abyss_colossal_squid", "name": "大王酸浆鱿", "rarity": "传说", "flavor": "眼球比人头还大的家伙"},
        {"id": "abyss_megamouth_shark", "name": "巨口鲨", "rarity": "传说", "flavor": "巨口张开像一张大网"},
        {"id": "abyss_leviathan", "name": "利维坦", "rarity": "神话", "flavor": "海沟深处传来远古咆哮"},
    ],
    "trench": [
        {"id": "trench_amphipod", "name": "端足虫", "rarity": "常见", "flavor": "指甲盖大的白虫翻着泥"},
        {"id": "trench_ghost_shrimp", "name": "幽灵虾", "rarity": "常见", "flavor": "半透明的影子掠过探照灯"},
        {"id": "trench_snailfish", "name": "狮子鱼", "rarity": "少见", "flavor": "胶质身体在万米下漂浮"},
        {"id": "trench_sea_pig", "name": "海猪", "rarity": "少见", "flavor": "粉胖的身子用细腿走路"},
        {"id": "trench_glass_sponge", "name": "玻璃海绵", "rarity": "少见", "flavor": "骨架像一件透明工艺品"},
        {"id": "trench_sixgill_shark", "name": "六鳃鲨", "rarity": "稀有", "flavor": "远古的轮廓从黑暗中浮出"},
        {"id": "trench_ghost_octopus", "name": "幽灵蛸", "rarity": "稀有", "flavor": "深红斗篷在无光处张开"},
        {"id": "trench_gulper_eel", "name": "巨口鳗", "rarity": "稀有", "flavor": "能把嘴张得比身体还大"},
        {"id": "trench_spider_crab", "name": "巨蛛蟹", "rarity": "传说", "flavor": "长腿跨过整个探测窗口"},
        {"id": "trench_dark_sea_serpent", "name": "无光海蛇", "rarity": "传说", "flavor": "黑暗中只有鳞片在反光"},
        {"id": "trench_thousand_eye_fish", "name": "万眼鱼", "rarity": "传说", "flavor": "每只眼睛都盯着不同方向"},
        {"id": "trench_guixu", "name": "归墟", "rarity": "神话", "flavor": "万水归处连光都无法逃出"},
    ],
    "glacier": [
        {"id": "glacier_cod", "name": "鳕鱼", "rarity": "常见", "flavor": "冰凉水里肉质格外紧实"},
        {"id": "glacier_ice_shrimp", "name": "冰虾", "rarity": "常见", "flavor": "在冰洞下闪着细碎的光"},
        {"id": "glacier_arctic_char", "name": "冰鲑", "rarity": "少见", "flavor": "红腹在冰下格外醒目"},
        {"id": "glacier_ice_snail", "name": "冰螺", "rarity": "少见", "flavor": "壳上结着一层薄薄的冰"},
        {"id": "glacier_snow_crab", "name": "雪蟹", "rarity": "少见", "flavor": "白壳长腿在冰底爬行"},
        {"id": "glacier_arctic_sturgeon", "name": "极地鳇", "rarity": "稀有", "flavor": "破冰而出掀起大片水花"},
        {"id": "glacier_ice_jellyfish", "name": "冰晶水母", "rarity": "稀有", "flavor": "冻成晶体的伞盖仍在动"},
        {"id": "glacier_ice_turtle", "name": "千年冰龟", "rarity": "稀有", "flavor": "龟壳冻成了半透明的冰"},
        {"id": "glacier_arctic_seal", "name": "北极海豹", "rarity": "传说", "flavor": "圆滚滚的家伙抢走鱼饵"},
        {"id": "glacier_frozen_whale", "name": "冰封巨鲸", "rarity": "传说", "flavor": "冰层下传来悠长的鲸歌"},
        {"id": "glacier_cod_king", "name": "极光鳕王", "rarity": "传说", "flavor": "鳞片映出整片绿色天光"},
        {"id": "glacier_glacier_heart", "name": "冰川之心", "rarity": "神话", "flavor": "冰下传来缓慢的搏动声"},
    ],
    "aurora": [
        {"id": "aurora_aurora_shrimp", "name": "极光虾", "rarity": "常见", "flavor": "壳上流转着淡淡的绿光"},
        {"id": "aurora_aurora_cod", "name": "极光鳕", "rarity": "少见", "flavor": "在光幕下游成一条长线"},
        {"id": "aurora_stardust_jellyfish", "name": "星尘水母", "rarity": "少见", "flavor": "触须上落满细小的光点"},
        {"id": "aurora_crystal_crab", "name": "冰晶蟹", "rarity": "少见", "flavor": "钳子上凝着蓝白的霜花"},
        {"id": "aurora_aurora_salmon", "name": "极光鲑", "rarity": "稀有", "flavor": "逆着光瀑向上游动"},
        {"id": "aurora_starlight_eel", "name": "星辉鳗", "rarity": "稀有", "flavor": "身上流转着银河般的纹"},
        {"id": "aurora_light_pillar_fish", "name": "光柱鱼", "rarity": "稀有", "flavor": "把极光折成一根根光柱"},
        {"id": "aurora_sky_whale", "name": "天穹巨鲸", "rarity": "传说", "flavor": "影子投在整片极光之上"},
        {"id": "aurora_aurora_dragonfish", "name": "极光龙鱼", "rarity": "传说", "flavor": "鳞片把整片天幕照得发亮"},
        {"id": "aurora_star_orbit_jellyfish", "name": "星轨水母", "rarity": "传说", "flavor": "伞盖旋转如一片星轨"},
        {"id": "aurora_eternal_night_sturgeon", "name": "永夜冰鲟", "rarity": "传说", "flavor": "骨板下压着整片永夜"},
        {"id": "aurora_aurora_primordial_dragon", "name": "极光古龙", "rarity": "神话", "flavor": "光幕裂开它缓缓垂下头"},
    ],
}

# ===== 配置默认值：当前生效鱼池的文本形态（由导出脚本生成，勿手改）=====
#: 一行一条鱼：`id|名称|稀有度|基准价|分布|说明`；
#: 分布 = `钓点:权重` 逗号分隔，`*` 表示所有钓点；权重是「基础权重」，
#: 运行时会再乘 `rarity_spawn_weights` 里对应稀有度的权重。
FISH_DEFS_DEFAULT: str = '''\
white_bait|白条|常见|6|novice:1.1104|细长的小鱼，抢饵最积极
small_crucian|小鲫鱼|常见|4|novice:0.2143|最常见的鱼，脾气很好
river_shrimp|河虾|常见|21|night:0.8785|半透明的小家伙，弹跳力惊人
mud_snail|田螺|常见|5|novice:1.1482|慢吞吞地贴在钩上，懒得挣扎
loach|泥鳅|常见|5|novice:0.9217,night:1.0711|滑不溜手，抓它得有点耐心
crucian|河鲫|常见|5|novice:0.2143|银光闪闪，成群结队
carp|鲤鱼|少见|14|novice:1.0111,lake:0.9595|力气不小，尾巴拍水很响
topmouth_culter|翘嘴鲌|常见|8|novice:0.2143|喜欢在水面追着小鱼跑
grass_carp|草鱼|常见|10|lake:1.0102|吃草长大的，肉厚实
catfish|鲶鱼|少见|24|lake:0.9697,night:2.9723|躲在淤泥里，胡须很灵敏
snakehead|黑鱼|稀有|63|reed:1.0183|水中恶霸，牙齿很锋利
river_crab|河蟹|少见|30|reed:1.0804|横着走，钳子夹人很疼
bass|鳜鱼|稀有|69|lake:0.9709|肉食性，专挑小鱼下手
bighead_carp|鳙鱼|少见|28|lake:1.0216|脑袋特别大，游得慢
softshell|甲鱼|稀有|83|reed:0.8659|咬住就不松口，小心取钩
eel|河鳗|少见|51|lake:0.4348|细长有力，缠线一把好手
mandarin_fish|鳜花鱼|稀有|85|lake:0.7368|花纹华美，肉嫩刺少
giant_salamander|娃娃鱼|稀有|40|bamboo:0.8704|叫声像婴儿，脾气却很大
pearl_shell|珍珠贝|稀有|108|sea:0.7368|壳里偶尔藏着好东西
seahorse|海马|稀有|119|dock:0.9175|游泳姿势相当优雅
cuttlefish|墨鱼|稀有|136|sea:0.7368|被抓住就喷你一脸墨
sturgeon|中华鲟|稀有|170|sea:0.7368|活化石级别的大家伙
koi|锦鲤|稀有|27|novice:1.1311|据说见到它会有好运气
golden_turtle|金龟|传说|363|swamp:0.5|背甲泛着金光，慢吞吞地划水
discus|七彩神仙鱼|传说|431|swamp:0.5|水族箱里的活宝石
moon_jellyfish|月华水母|传说|500|swamp:0.5|半透明伞盖泛着淡蓝月光
arowana|金龙鱼|传说|624|sea:0.5|鳞片如金甲，游动雍容华贵
deep_anglerfish|深海鮟鱇|神话|907|abyss:1|额前挂着一盏幽蓝小灯
dragon_koi|龙鲤|神话|1247|swamp:1|传说跃过龙门就会化龙
abyss_whale|深渊鲸|神话|1701|abyss:1|深海巨影，一次摆尾掀起暗流
kun|鲲|神话|2552|abyss:1|北冥有鱼，其名为鲲
boot_carp|靴子鲤|少见|60|novice:0.4348|有人把破靴子扔进池塘，它就在里面安了家
bamboo_shrimp|竹节虾|稀有|320|bamboo:0.7368|一节一节的花纹，藏在竹影下面
lantern_fish|灯笼鱼|少见|61|night:1.0669|成群挂在堤坝边，像一串小灯笼
ghost_jelly|幽灵水母|传说|900|swamp:0.5|半透明得像一缕烟，雾天才会出现
loach_king|泥鳅王|稀有|204|swamp:1.1323|传说泥鳅活过百年就会长出龙须
pearl_lume|夜明珠贝|传说|1550|sea:0.5|壳缝里透出微光，月夜里最亮
golden_hook|金钩鱼|神话|3400|glacier:1|嘴里叼着一枚金色鱼钩——上一任主人的吧
novice_crucian|鲫鱼|常见|5|novice:0.9604|池塘里最老实的小家伙
novice_stone_moroko|麦穗鱼|常见|5|novice:1.0003,reed:0.8926|身侧一道黑线爱啄钩
novice_shrimp|小河虾|常见|5|novice:0.9274|透明得能看见肚里的沙
novice_bitterling|鳑鲏|常见|5|novice:0.8971|阳光下闪出彩虹般的鳞
novice_yellow_catfish|黄颡鱼|少见|15|novice:1.1068,reed:0.9508|背鳍竖起来扎手的黄胡子
novice_mussel|河蚌|少见|12|novice:0.871,lake:1.096|壳缝里吐出一串小气泡
novice_turtle|巴西龟|少见|15|novice:1.1164|被人放生后长满了绿藻
bamboo_chub|溪哥|常见|7|bamboo:1.1251|鳞片闪着溪水的亮光
bamboo_horsemouth|马口鱼|常见|6|bamboo:0.9244|嘴大贪吃见饵就往前冲
bamboo_river_snail|山坑螺|常见|6|bamboo:0.9037|吸在石头上扣都扣不动
bamboo_goby|虾虎鱼|常见|5|bamboo:0.8515|趴在卵石上一动不动
bamboo_barb|小鲃|常见|5|bamboo:0.8572|竹影下成群游来游去
bamboo_stream_crab|溪蟹|常见|6|bamboo:0.9472|举着小钳子横着走路
bamboo_labiobarbus|光唇鱼|少见|17|bamboo:1.0999|嘴唇厚实最爱啃青苔
bamboo_spiny_eel|刺鳅|少见|14|bamboo:0.8941|浑身细刺钻进石缝里
bamboo_corbicula|河蚬|少见|15|bamboo:0.9115|沙底上一排小小的纹路
bamboo_spiny_frog|棘胸蛙|少见|17|bamboo:1.0582|蹲在湿石上鼓着腮帮
bamboo_peach_jellyfish|桃花水母|稀有|33|bamboo:0.8824|水中漂浮的淡粉色小伞
canal_tilapia|罗非鱼|常见|6|canal:0.8545|污水里也活得膘肥体壮
canal_crayfish|小龙虾|常见|7|canal:0.9475|举着红钳从石缝里爬出
canal_old_boot|破靴子|常见|7|canal:0.9919|靴筒里还藏着两只小虾
canal_soda_can|易拉罐|常见|7|canal:0.9709|罐口卡着一只小螃蟹
canal_mosquitofish|食蚊鱼|常见|7|canal:0.9094|水面上一群指头长的小鱼
canal_mud_carp|鲮鱼|常见|6|canal:0.8989|贴着河底慢慢啃食青泥
canal_walking_catfish|塘鲺|少见|19|canal:1.0456|能在湿泥里扭着爬行
canal_mitten_crab|中华绒螯蟹|少见|18|canal:0.9667|毛茸茸的钳子夹住鱼线
canal_ricefield_eel|黄鳝|少见|19|canal:1.0288,swamp:0.9067|从石阶缝里滑出半条身子
canal_silver_carp|鲢鱼|少见|18|canal:0.9817|受惊时跃出水面半米高
canal_alligator_snapper|鳄龟|稀有|44|canal:1.0333|咬合力惊人的放生怪物
canal_chinese_sucker|胭脂鱼|稀有|39|canal:0.9121|背鳍如帆通体泛着红光
lake_bream|鳊鱼|常见|12|lake:1.1404|侧扁的身子像一片银叶
lake_icefish|银鱼|常见|10|lake:1.0276|几乎透明的细软小鱼
lake_shrimp|湖虾|常见|9|lake:0.9028|长须在水草间轻轻摆动
lake_whitefish|翘嘴白鱼|常见|11|lake:1.1068,night:0.973|追着小鱼把水面炸开花
lake_black_carp|青鱼|稀有|61|lake:0.9967|力大得能把竿拉成弓
lake_giant_softshell|百年老鳖|稀有|54|lake:0.8677|背上刻满岁月的旧纹
reed_frog|青蛙|常见|14|reed:1.132|扑通一声跳进芦苇丛
reed_water_snake|水蛇|常见|11|reed:0.8755,swamp:0.3239|贴着水面划出长长波纹
reed_reed_shrimp|芦苇虾|常见|12|reed:0.9343|攀在苇秆上随波摇晃
reed_river_snail|螺蛳|常见|13|reed:1.0357|壳上粘着几缕新鲜水草
reed_leech|蚂蟥|少见|28|reed:0.8797|吸在腿上怎么拽都不松
reed_freshwater_prawn|青虾|少见|29|reed:0.9154|弹尾一缩窜出半米远
reed_pearl_mussel|珍珠蚌|稀有|66|reed:0.8926|壳里含着颗温润的珠子
sea_hairtail|带鱼|常见|16|sea:1.1005|银亮长身像一把快刀
sea_yellow_croaker|黄鱼|常见|16|sea:1.1314|出水时咕咕叫个不停
sea_swimming_crab|梭子蟹|常见|13|sea:0.9187|两把尖钳护着满壳蟹黄
sea_white_shrimp|白虾|常见|15|sea:1.048|一网拉上来活蹦乱跳
sea_pomfret|鲳鱼|少见|38|sea:1.0159|扁得像面镜子闪着光
sea_squid|鱿鱼|少见|32|sea:0.8524|喷出一团墨汁就逃走
sea_spanish_mackerel|鲅鱼|少见|40|sea:1.0945|银蓝背上满是花纹
sea_jellyfish|海蜇|少见|32|sea:0.8575|半透明地一伸又一缩
sea_red_seabream|真鲷|稀有|77|sea:0.8947|通体粉红像海里的樱花
sea_lobster|大龙虾|稀有|97|sea:1.1476|长须一弹溅起一片水花
sea_tuna|金枪鱼|稀有|79|sea:0.9202|肌肉绷紧拖着鱼线狂奔
sea_oarfish|皇带鱼|传说|186|sea:0.8584|银带般的身子长得望不到头
dock_seabass|鲈鱼|常见|19|dock:1.0492|在桥墩阴影里伏击小鱼
dock_old_tire|旧轮胎|常见|17|dock:0.9451|胎壁里住着一窝小蟹
dock_shore_crab|小螃蟹|常见|17|dock:0.9304|举着钳子卡住鱼钩不放
dock_sardine|沙丁鱼|常见|17|dock:0.9475|成群绕着木桩打转
dock_eel|鳗鱼|少见|51|dock:1.081|缠住鱼线怎么都解不开
dock_octopus|章鱼|少见|49|dock:1.0231|八条腕抱着鱼饵不撒手
dock_starfish|海星|少见|54|dock:1.1398|五只角慢慢翻过木桩
dock_barnacle|藤壶|少见|47|dock:0.9796|密密麻麻糊满整块船板
dock_black_seabream|黑鲷|稀有|98|dock:0.8827|黑色鳍边一收就钻底
dock_stonefish|石头鱼|稀有|100|dock:0.9031|装作石块等人踩上去
dock_megalodon|巨齿鲨|传说|284|dock:1.0339|旧码头外掀起三米巨浪
night_opah|月鱼|少见|64|night:1.0534|鳞片映着月色泛出红光
night_glow_jellyfish|夜光水母|少见|64|night:1.0561|蓝光一闪一闪浮上来
night_giant_catfish|巨型鲶鱼|稀有|143|night:1.0207|拖得鱼竿弯进水里
night_moonlit_eel|银月鳗|稀有|129|night:0.9079|银色身子像一道月光
night_ghost_lanternfish|幽灵灯笼鱼|稀有|160|night:1.1482|幽绿的光在深处熄灭
night_glow_shark|夜光鲨|稀有|129|night:0.9049|背鳍划开墨色的水面
night_white_dragon_fish|白龙鱼|传说|339|night:0.9595|月光里跃出一条白影
mangrove_mudskipper|弹涂鱼|常见|24|mangrove:0.9076|用胸鳍在泥滩上蹦跳
mangrove_fiddler_crab|招潮蟹|常见|29|mangrove:1.1173|举着一只大钳来回招手
mangrove_oyster|牡蛎|常见|25|mangrove:0.9346|牢牢粘在红树根上
mangrove_baby_shark|幼鲨|少见|78|mangrove:1.1428|在浅湾里练习捕猎
mangrove_mud_snail|泥螺|少见|75|mangrove:1.0948|拖着黏液在泥面爬行
mangrove_prawn|基围虾|少见|75|mangrove:1.1011|在树根之间弹跳逃窜
mangrove_mud_crab|青蟹|少见|64|mangrove:0.928|青壳大钳夹得人发麻
mangrove_horseshoe_crab|中华鲎|稀有|177|mangrove:1.1203|蓝血甲壳像一顶小钢盔
mangrove_saltwater_crocodile|湾鳄|稀有|138|mangrove:0.8542|浮木般的背脊缓缓移动
mangrove_giant_python|巨蟒|稀有|157|mangrove:0.9838|从树根间垂下半个身子
mangrove_dugong|儒艮|稀有|177|mangrove:1.1239|笨拙地抱着水草啃食
mangrove_flood_dragon|蛟龙|传说|359|mangrove:0.8944|潮水翻涌时探出长角
swamp_marsh_shrimp|沼虾|常见|32|swamp:0.9847|雾气里挥着细长的钳
swamp_water_snail|水蜗牛|常见|30|swamp:0.9037|背着壳在浮叶上爬
swamp_newt|蝾螈|常见|29|swamp:0.8635|橙腹的小东西扭着尾巴
swamp_marsh_turtle|沼泽龟|少见|74|swamp:0.8581|壳上长满绿毛像块石头
swamp_toad|蟾蜍|少见|91|swamp:1.0813|鼓着喉咙发出低哑叫声
swamp_alligator_gar|鳄雀鳝|稀有|221|swamp:1.1428|满口尖牙咬得鱼线发白
swamp_giant_leech|巨型水蛭|稀有|184|swamp:0.9325|黑亮的身子缠上小腿
swamp_nine_eyed_fish|九目怪鱼|传说|464|swamp:0.9424|九只眼睛同时转向你
swamp_ghost_catfish|幽冥巨鲶|传说|517|swamp:1.0615|雾里传来低沉的吞咽声
cave_blindfish|盲鱼|常见|40|cave:0.9475|没有眼睛也能找到饵
cave_cave_shrimp|洞穴虾|常见|38|cave:0.9016|通体雪白在暗水里漂
cave_albino_tadpole|白化蝌蚪|常见|45|cave:1.0747|苍白的小尾巴摆动不停
cave_cave_snail|洞穴螺|少见|99|cave:0.9001|壳薄得能看见里面的肉
cave_albino_catfish|白化鲶|少见|119|cave:1.1059|白色长须在黑暗里摸索
cave_blind_eel|盲眼鳗|少见|103|cave:0.9472|顺着暗流无声地滑过
cave_olm|洞螈|稀有|230|cave:0.9094|粉白的小龙趴在石上
cave_albino_giant_salamander|白化巨鲵|稀有|221|cave:0.8695|巨大白影贴着河底移动
cave_stone_spirit_fish|石灵鱼|稀有|262|cave:1.0498|鳞片像会呼吸的钟乳石
cave_glow_jellyfish|幽光水母|稀有|238|cave:0.9418|幽蓝冷光点亮整条暗河
cave_blind_white_dragon|盲眼白龙|传说|608|cave:0.967|无眼的巨首擦过岩壁
cave_earthcore_sturgeon|地心鲟|传说|591|cave:0.9364|骨板厚重如远古铠甲
ruins_grouper|石斑鱼|常见|49|ruins:0.9832|斑驳的身影守在船舷边
ruins_clownfish|小丑鱼|常见|55|ruins:1.099|在海葵触手间钻来钻去
ruins_sea_turtle|海龟|少见|121|ruins:0.9235|慢悠悠绕过断裂的桅杆
ruins_lionfish|狮子鱼|少见|131|ruins:1.0123,trench:0.9235|张开满身毒鳍缓缓逼近
ruins_sea_urchin|海胆|少见|138|ruins:1.0717|黑刺扎进手套里发麻
ruins_wreck_crab|沉船蟹|少见|125|ruins:0.955|从锈蚀的舱门里爬出
ruins_treasure_chest_fish|宝箱鱼|稀有|336|ruins:1.1326|鳞片像铜锁闪着暗光
ruins_giant_grouper|巨型石斑|稀有|336|ruins:1.1308|一张嘴能吞下整条鱼
ruins_rusty_swordfish|锈剑鱼|稀有|333|ruins:1.1209|长吻上缠着旧船的铁锈
ruins_pearl_king_clam|珍珠王贝|稀有|328|ruins:1.1032|壳里滚出拳头大的珍珠
ruins_ghost_shark|幽灵鲨|传说|847|ruins:1.1425|半透明的影子穿过船骸
ruins_sunken_leviathan|沉船巨鲲|传说|754|ruins:1.006|船体震动它正从下方经过
abyss_deep_sea_shrimp|深海虾|常见|55|abyss:0.892|苍白细腿在泥底摸索
abyss_deep_sea_crab|深海蟹|常见|67|abyss:1.1029|盲眼长腿抱着一块腐肉
abyss_deep_sea_eel|深海鳗|少见|141|abyss:0.8785|张开大口等着一切落下
abyss_sail_jellyfish|帆水母|少见|162|abyss:1.0225|像一片蓝色小帆漂过去
abyss_deep_octopus|深海章鱼|少见|163|abyss:1.0288|吸盘在舷边留下圆痕
abyss_anglerfish|鮟鱇鱼|稀有|405|abyss:1.1122|头顶小灯照出满口尖牙
abyss_giant_squid|巨型乌贼|稀有|328|abyss:0.8839|触腕上密布着倒钩
abyss_vampire_squid|吸血乌贼|稀有|343|abyss:0.9274|披着黑红斗篷缓缓飘过
abyss_dragonfish|深海龙鱼|稀有|386|abyss:1.0549|獠牙长到合不上嘴
abyss_colossal_squid|大王酸浆鱿|传说|930|abyss:1.0144|眼球比人头还大的家伙
abyss_megamouth_shark|巨口鲨|传说|811|abyss:0.8731|巨口张开像一张大网
abyss_leviathan|利维坦|神话|1921|abyss:0.916|海沟深处传来远古咆哮
trench_amphipod|端足虫|常见|67|trench:0.8944|指甲盖大的白虫翻着泥
trench_ghost_shrimp|幽灵虾|常见|69|trench:0.9199|半透明的影子掠过探照灯
trench_sea_pig|海猪|少见|203|trench:1.0558|粉胖的身子用细腿走路
trench_glass_sponge|玻璃海绵|少见|203|trench:1.0528|骨架像一件透明工艺品
trench_sixgill_shark|六鳃鲨|稀有|460|trench:1.033|远古的轮廓从黑暗中浮出
trench_ghost_octopus|幽灵蛸|稀有|417|trench:0.9265|深红斗篷在无光处张开
trench_gulper_eel|巨口鳗|稀有|394|trench:0.8698|能把嘴张得比身体还大
trench_spider_crab|巨蛛蟹|传说|1212|trench:1.0933|长腿跨过整个探测窗口
trench_dark_sea_serpent|无光海蛇|传说|1137|trench:1.0198|黑暗中只有鳞片在反光
trench_thousand_eye_fish|万眼鱼|传说|1267|trench:1.1464|每只眼睛都盯着不同方向
trench_guixu|归墟|神话|2477|trench:0.9763|万水归处连光都无法逃出
glacier_cod|鳕鱼|常见|91|glacier:0.9946|冰凉水里肉质格外紧实
glacier_ice_shrimp|冰虾|常见|82|glacier:0.886|在冰洞下闪着细碎的光
glacier_arctic_char|冰鲑|少见|231|glacier:0.9643|红腹在冰下格外醒目
glacier_ice_snail|冰螺|少见|263|glacier:1.1107|壳上结着一层薄薄的冰
glacier_snow_crab|雪蟹|少见|261|glacier:1.102|白壳长腿在冰底爬行
glacier_arctic_sturgeon|极地鳇|稀有|571|glacier:1.0405|破冰而出掀起大片水花
glacier_ice_jellyfish|冰晶水母|稀有|571|glacier:1.0405|冻成晶体的伞盖仍在动
glacier_ice_turtle|千年冰龟|稀有|603|glacier:1.1026|龟壳冻成了半透明的冰
glacier_arctic_seal|北极海豹|传说|1530|glacier:1.1218|圆滚滚的家伙抢走鱼饵
glacier_frozen_whale|冰封巨鲸|传说|1263|glacier:0.9097|冰层下传来悠长的鲸歌
glacier_cod_king|极光鳕王|传说|1476|glacier:1.0789|鳞片映出整片绿色天光
glacier_glacier_heart|冰川之心|神话|2762|glacier:0.8743|冰下传来缓慢的搏动声
aurora_aurora_shrimp|极光虾|常见|125|aurora:1.0891|壳上流转着淡淡的绿光
aurora_aurora_cod|极光鳕|少见|320|aurora:1.0735|在光幕下游成一条长线
aurora_stardust_jellyfish|星尘水母|少见|281|aurora:0.9328|触须上落满细小的光点
aurora_crystal_crab|冰晶蟹|少见|295|aurora:0.9826|钳子上凝着蓝白的霜花
aurora_aurora_salmon|极光鲑|稀有|692|aurora:1.0003|逆着光瀑向上游动
aurora_starlight_eel|星辉鳗|稀有|635|aurora:0.9103|身上流转着银河般的纹
aurora_light_pillar_fish|光柱鱼|稀有|669|aurora:0.9643|把极光折成一根根光柱
aurora_sky_whale|天穹巨鲸|传说|1859|aurora:1.0822|影子投在整片极光之上
aurora_aurora_dragonfish|极光龙鱼|传说|1917|aurora:1.1188|鳞片把整片天幕照得发亮
aurora_star_orbit_jellyfish|星轨水母|传说|1941|aurora:1.1341|伞盖旋转如一片星轨
aurora_eternal_night_sturgeon|永夜冰鲟|传说|1829|aurora:1.063|骨板下压着整片永夜
aurora_aurora_primordial_dragon|极光古龙|神话|4257|aurora:1.0942|光幕裂开它缓缓垂下头
redfin|赤眼鳟|常见|5|novice:0.7|眼睛红红的，喜欢顶水游
pond_snail|塘螺|常见|4|novice:0.7|壳上裹着一层滑溜溜的苔
stone_carp|石斑吻鰕|常见|5|bamboo:0.7|贴在石头上啃青苔
bamboo_shrimp2|青虾|少见|18|bamboo:0.7|通体透明，夜里会发一点青
canal_guppy|孔雀鱼|常见|7|canal:0.7|尾鳍颜色比运河水还鲜艳
canal_eel|运河黄鳝|少见|21|canal:0.7|从水泥缝里钻出来的老住户
silver_carp|鲢鱼|常见|10|lake:0.7|成群游动，尾巴一摆一片白
lake_perch|湖鲈|少见|24|lake:0.7|追着小鱼跑，咬钩很凶
reed_loach|芦苇鳅|常见|12|reed:0.7|藏在苇根下，身上带花纹
sea_sardine|沙丁鱼|常见|15|sea:0.7|密密麻麻挤成一片银云
sea_bream|海鲷|少见|40|sea:0.7|牙口好，能把钩咬断
dock_goby|虾虎鱼|常见|16|dock:0.7|趴在旧船底一动不动
night_moth_fish|夜蛾鱼|常见|24|night:0.7|被灯光引来，围着堤坝转
night_ray|月鳐|传说|317|night:0.7|在月光下像一片会飞的黑影
mangrove_crab|招潮蟹|少见|72|mangrove:0.7|举着一只特别大的钳子
swamp_leech|水蛭|常见|36|swamp:0.7|黏糊糊地挂在钩上
swamp_eel|沼泽电鳗|传说|520|swamp:0.7|摸一下能麻半天
cave_blind_shrimp|盲虾|常见|38|cave:0.7|眼睛退化了，全靠触须
cave_whitefish|白化鳅|少见|109|cave:0.7|通体雪白，怕光
ruins_porgy|遗迹鲷|常见|55|ruins:0.7|在锈铁皮之间穿来穿去
ruins_lobster|船蛆虾|少见|136|ruins:0.7|住在烂木头里，钳子很硬
abyss_snailfish|深渊狮子鱼|稀有|342|abyss:0.7|半透明的身体里有微光
abyss_vampire|吸血乌贼|传说|978|abyss:0.7|张开像一把黑伞
trench_grenadier|深渊鼠尾鳕|稀有|418|trench:0.7|尾巴细得像鞭子
glacier_icefish|冰鱼|常见|95|glacier:0.7|血是透明的，贴着冰面游
glacier_char|北极红点鲑|传说|1355|glacier:0.7|身上洒满红色小点
aurora_crystal_fish|极光晶鱼|稀有|748|aurora:0.7|鳞片折出的光一直在变色
aurora_ghost_whale|极光幽灵鲸|神话|3607|aurora:0.7|游过时整片冰面都亮了一下
big_fat_fish|大肥鱼|稀有|360|novice:0.1579,bamboo:0.1579,canal:0.1579,lake:0.1579,reed:0.1579,sea:0.1579,dock:0.1579,night:0.1579,mangrove:0.1579,swamp:0.1579,cave:0.1579,ruins:0.1579,abyss:0.1579,trench:0.1579,glacier:0.1579,aurora:0.1579|原型据说是现实里的 DeepSeek 模型：问它什么都肯答，答得又稳又长，就是偶尔会想很久'''
