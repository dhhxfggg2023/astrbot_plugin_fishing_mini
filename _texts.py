# -*- coding: utf-8 -*-
"""回复文案表（配置项 ``text_overrides``）：**每一条回复**的文案都可以改。

## 这套东西怎么运作

* 插件里每条回复都有一个**场景键**（scene id），键的清单在 ``_calc.py`` 的
  ``REPLY_SCENES`` 里（例如 ``cast.hit`` = 钓到鱼之后那条结果、``bag.list`` =
  背包列表、``shop.bought`` = 买到东西）。**按钮场景与文案键是同一套键名**：
  给 ``cast.hit`` 配按钮就是给这条回复配按钮，给 ``cast.hit`` 配文案就是给
  这条回复配文案 —— 编辑器页面「💬 回复」页一张卡片同时管这两件事。
* 默认文案写在 ``TEXTS`` 里。**由代码按玩家数据拼装的长文本**（背包列表、
  连钓战报…）默认模板是 ``{原文}``：``{原文}`` 就是代码原本拼好的那段文字，
  站长可以在它前后加词、换 emoji、改语气，但**不能把它删掉**（删掉就等于丢掉
  全部数据）。带 ``{原文}`` 的场景都在 ``DYNAMIC`` 里，运行时也会再拦一道。
* 除 ``{原文}`` 外，少数场景还额外提供具体占位符（见 ``EXTRA_PLACEHOLDERS``，
  例如 ``cast.hit`` 的 ``{鱼名}``/``{估值}``）。这些占位符的值由调用点传进来，
  所以只有这里登记过的才允许出现在模板里 —— 写错的占位符在保存时就会被跳过
  并告警，绝不会让玩家看到 ``{鱼名}`` 这种原文。
* 渲染在 ``render_scene()``：**覆盖模板渲染失败就当没写**，一律回退代码原文。

## 配置写法（配置项 text_overrides / 编辑器页面「💬 回复」页）

```
cast.miss_none|🪝 水面静悄悄的，连个泡都没有
pull.hook|{原文}\n　⚡ 快！{秒数} 秒内发 /钓鱼 拉
bag.list|{原文}\n　💡 想清空就发 /钓鱼 卖光光
```

一行一条：``场景|模板``；``#`` 开头的行是注释；没写到的场景用内置默认。

## 与按钮场景的对应关系（护栏）

``_calc.SCENE_IDS`` 与这里的 ``TEXTS`` **必须一一对应**，少一个就说明有人新增
回复忘了登记 —— ``test_local.py`` 里有一条断言专门卡这件事，会红。
"""

from __future__ import annotations

#: 场景 id -> 默认文案模板。**由代码拼装的长文本用 ``{原文}`` 占位**
TEXTS: dict[str, str] = {
    "sell.result": "{原文}",  # 动态文本
    "pull.none": "🤔 这会儿没有鱼咬钩\n　先 /钓鱼 下竿，看到「咬钩了」再 /钓鱼 拉",
    "help.page": "{原文}",  # 动态文本
    "help.unknown": "{原文}",  # 动态文本
    "custom.send": "{原文}",  # 动态文本
    "pull.confirm": "✅ 收到，正在收线…",
    "cast.multi_bad_times": "🤔 次数要写正整数\n　例：/钓鱼 10 = 连下十竿",
    "system.error": "😵 这一下没成，日志里记了一笔\n　再试一次；老出错就把这条发给管理员",
    "cast.busy": "🎣 竿子还在手里\n　先 /钓鱼 拉，或者干脆等它跑掉",
    "cast.hit": "{原文}",  # 动态文本
    "cast.multi_limit": "{原文}",  # 动态文本
    "cast.multi_busy": "🎣 上一竿还等着收\n　先 /钓鱼 拉，再谈连钓",
    "cast.multi_summary": "{原文}",  # 动态文本
    "cast.no_gold": "{原文}",  # 动态文本
    "cast.bag_full": "{原文}",  # 动态文本
    "cast.multi_no_gold": "{原文}",  # 动态文本
    "cast.multi_bag_full": "{原文}",  # 动态文本
    "cast.multi_achievement": "{原文}",  # 动态文本
    "cast.multi_milestone": "{原文}",  # 动态文本
    "cast.multi_save_failed": "⚠️ 这批渔获没存上\n　把这条发给管理员，日志里有详情",
    "cast.bad_bait": "{原文}",  # 动态文本
    "cast.no_stamina": "{原文}",  # 动态文本
    "cast.bait_note": "{原文}",  # 动态文本
    "cast.multi_no_stamina": "{原文}",  # 动态文本
    "cast.junk": "{原文}",  # 动态文本
    "cast.multi_no_bait": "{原文}",  # 动态文本
    "backpack.upgraded": "{原文}",  # 动态文本
    "rank.view": "{原文}",  # 动态文本
    "lock.done": "{原文}",  # 动态文本
    "unlock.done": "{原文}",  # 动态文本
    "bag.list": "{原文}",  # 动态文本
    "fishinfo.detail": "{原文}",  # 动态文本
    "collection.view": "{原文}",  # 动态文本
    "stamina.view": "{原文}",  # 动态文本
    "profile.renamed": "{原文}",  # 动态文本
    "profile.view": "{原文}",  # 动态文本
    "sign.result": "{原文}",  # 动态文本
    "story.result": "{原文}",  # 动态文本
    "orders.list": "{原文}",  # 动态文本
    "location.list": "{原文}",  # 动态文本
    "rod.list": "{原文}",  # 动态文本
    "backpack.max": "{原文}",  # 动态文本
    "backpack.no_gold": "{原文}",  # 动态文本
    "today.view": "{原文}",  # 动态文本
    "rank.empty": "📊 榜上还空着\n　去 /钓鱼 抛两竿，第一名先到先得",
    "rank.no_data": "{原文}",  # 动态文本
    "bag.empty": "{原文}",  # 动态文本
    "fishinfo.location_detail": "{原文}",  # 动态文本
    "fishinfo.not_found": "{原文}",  # 动态文本
    "fishinfo.index": "{原文}",  # 动态文本（/钓鱼 查 能查哪些东西；取代了原来的 fishinfo.usage）
    "fishinfo.multi_match": "{原文}",  # 动态文本（一个词命中好几样）
    "collection.detail": "{原文}",  # 动态文本
    "collection.location": "{原文}",  # 动态文本
    "aquarium.usage": "📖 水族馆\n　/钓鱼 水族馆　　　　看缸\n　放 1 / 取 1 / 卖 1　进出与变现\n　用 <道具> 1　　　　　投喂\n　领　　　　　　　　　收当天的产出\n　扩建　　　　　　　　花金币加位",
    "shop.bait_usage": "📖 /钓鱼 鱼饵　　　　看饵的货架\n　/钓鱼 鱼饵 买 <名字> [数量]\n　例：/钓鱼 鱼饵 买 蚯蚓 20",
    "bait.empty": "{原文}",  # 动态文本
    "item.feed_done": "{原文}",  # 动态文本
    "story.none": "🤔 眼下没什么要你拿主意的事",
    "story.expired": "💨 你犹豫的工夫，那点动静过去了",
    "story.bad_choice": "{原文}",  # 动态文本
    "orders.locked": "{原文}",  # 动态文本
    "orders.submit_result": "{原文}",  # 动态文本
    "location.moved": "{原文}",  # 动态文本
    "location.unlock_go": "{原文}",  # 动态文本
    "rod.bought": "{原文}",  # 动态文本
    "rod.equipped": "{原文}",  # 动态文本
    "lock.usage": "📖 锁住的鱼不会被卖掉\n　/钓鱼 锁定 <序号…>　/钓鱼 解锁 <序号…>\n　序号看 /钓鱼 背包",
    "lock.bad_index": "{原文}",  # 动态文本
    "lock.all_done": "{原文}",  # 动态文本
    "sell.empty": "🎒 背包是空的，没东西可卖",
    "sell.removed": "🧹 「卖 垃圾」这个玩法已经去掉了\n　清空背包：/钓鱼 卖光光（锁着的会留下）\n　只卖一种：/钓鱼 卖 鲤鱼\n　按序号卖：/钓鱼 卖 1 2 3",
    "sell.nothing": "🤔 没找到能卖的鱼\n　可能序号超了，或这种鱼你还没钓到\n　发 /钓鱼 背包 看一眼",
    "sell.locked_note": "{原文}",  # 动态文本
    "fishinfo.empty": "{原文}",  # 动态文本
    "aquarium.view": "{原文}",  # 动态文本
    "aquarium.upgraded": "{原文}",  # 动态文本
    "aquarium.income": "{原文}",  # 动态文本
    "aquarium.feed_usage": "📖 投喂用「用」指令\n　/钓鱼 用 <道具名> <栏位号>\n　例：/钓鱼 用 高级饲料 1",
    "aquarium.put_done": "{原文}",  # 动态文本
    "aquarium.take_done": "{原文}",  # 动态文本
    "aquarium.sell_done": "{原文}",  # 动态文本
    "shop.list": "{原文}",  # 动态文本
    "shop.bought": "{原文}",  # 动态文本
    "bait.equipped": "{原文}",  # 动态文本
    "bait.not_found": "{原文}",  # 动态文本
    "bait.not_owned": "{原文}",  # 动态文本
    "bait.same": "{原文}",  # 动态文本
    "item.usage": "{原文}",  # 动态文本
    "item.missing": "{原文}",  # 动态文本
    "item.used": "{原文}",  # 动态文本
    "item.deco_used": "{原文}",  # 动态文本
    "item.breed_done": "{原文}",  # 动态文本
    "item.feed_no_fish": "🐠 缸里空着，先放条鱼进去",
    "item.feed_usage": "{原文}",  # 动态文本
    # 洗髓丹（v1.18.0 新增效果）：固定文案的两条可以直接整段改，动态的两条保留 {原文}
    "item.reroll_no_fish": "🐠 缸里空着，先放条要洗的鱼",
    "item.reroll_usage": "{原文}",  # 动态文本
    "item.reroll_bad_slot": "🤔 没这个栏位，/钓鱼 水族馆 看看序号",
    "item.reroll_failed": "{原文}",  # 动态文本
    "item.reroll_done": "{原文}",  # 动态文本
    "sign.done": "{原文}",  # 动态文本
    "story.wrong_owner": "🙅 这是别人的动静，你插不上手\n　自己下竿才会撞见属于你的那一段",
    "orders.usage": "{原文}",  # 动态文本
    "location.not_found": "🤔 没有这个钓点，/钓鱼 钓点 看看",
    "location.locked": "{原文}",  # 动态文本
    "location.already_here": "{原文}",  # 动态文本
    "location.unlocked": "{原文}",  # 动态文本
    "location.unlock_need": "{原文}",  # 动态文本
    "rod.not_found": "🤔 没这款鱼竿，名字再对一遍",
    "rod.owned": "{原文}",  # 动态文本
    "rod.level_low": "{原文}",  # 动态文本
    "rod.no_gold": "{原文}",  # 动态文本
    "rod.not_owned": "{原文}",  # 动态文本
    "sell.all_locked": "{原文}",  # 动态文本
    "aquarium.max": "🏠 已经扩到最大了，没地方再加",
    "aquarium.no_gold": "{原文}",  # 动态文本
    "aquarium.income_start": "{原文}",  # 动态文本
    "aquarium.income_empty": "🐠 缸里没鱼，鱼塘也就没有产出",
    "aquarium.income_wait": "{原文}",  # 动态文本
    "aquarium.put_usage": "{原文}",  # 动态文本
    "aquarium.full": "{原文}",  # 动态文本
    "aquarium.take_usage": "{原文}",  # 动态文本
    "aquarium.sell_usage": "📖 /钓鱼 水族馆 卖 <栏位号…>\n　例：/钓鱼 水族馆 卖 1　或　卖 1 3 5",
    "aquarium.bad_slot": "{原文}",  # 动态文本
    "shop.usage": "📖 鱼竿 /道具 /鱼饵，三家各买各的\n　/钓鱼 鱼竿 买 <名字>\n　/钓鱼 道具 买 <名字> [数量]\n　/钓鱼 鱼饵 买 <名字> [数量]\n　写错店名会说清该去哪家",
    "shop.item_usage": "📖 /钓鱼 道具　　　　看道具货架\n　/钓鱼 道具 买 <名字> [数量]\n　例：/钓鱼 道具 买 高级饲料 5",
    "shop.free_hook": "🪝 空钩不要钱，不用买\n　发 /钓鱼 或者 /钓鱼 空钩 就能用",
    "shop.not_found": "{原文}",  # 动态文本
    "shop.wrong_shop_rod": "{原文}",  # 动态文本
    "shop.wrong_shop_item": "{原文}",  # 动态文本
    "shop.wrong_shop_bait": "{原文}",  # 动态文本
    "shop.bait_list": "{原文}",  # 动态文本
    "shop.item_list": "{原文}",  # 动态文本
    "shop.moved": "{原文}",  # 动态文本（拆店提示里带着玩家原本想买的东西）
    "story.recap": "{原文}",  # 动态文本（连载的「第 N 话　上次：…」）
    "bait.locked": "{原文}",  # 动态文本
    "item.empty": "🎒 没有道具，去 /钓鱼 道具 买",
    "item.deco_disabled": "🪸 本服没开装饰位（decoration_slots = 0）",
    "item.deco_full": "{原文}",  # 动态文本
    "item.deco_none": "🪸 没有要摆的装饰道具了（先去 /钓鱼 道具 买）",
    "item.breed_no_fish": "🐠 缸里空着，先放条鱼再谈培育",
    "item.breed_usage": "{原文}",  # 动态文本
    "item.breed_bad_slot": "🤔 没这个栏位，/钓鱼 水族馆 看看序号",
    "item.breed_failed": "{原文}",  # 动态文本
    "item.feed_full": "{原文}",  # 动态文本
    "item.feed_missing": "{原文}",  # 动态文本
    "sell.bad_index": "{原文}",  # 动态文本
    "sell.no_fish": "{原文}",  # 动态文本
    "sell.missing": "{原文}",  # 动态文本
    "shop.locked": "{原文}",  # 动态文本
    "shop.no_gold_bait": "{原文}",  # 动态文本
    "shop.no_gold_item": "{原文}",  # 动态文本
    "pull.hook": "{原文}",  # 动态文本
    "story.prompt": "{原文}",  # 动态文本
    "pull.timeout": "{原文}",  # 动态文本
    "pull.escape": "{原文}",  # 动态文本
    # ---- 共用按钮组：它们自己的回复不存在，写在这里是为了「给整组回复统一改文案」----
    # （某个子场景没单独配文案时，会继承这里的模板，规则与按钮完全一样）
    "cast": "{原文}",  # 共用组（下竿）
    "pull": "{原文}",  # 共用组（拉线）
    "bag": "{原文}",  # 共用组（背包）
    "location": "{原文}",  # 共用组（钓点）
    "story": "{原文}",  # 共用组（小插曲）
    # ---- 特殊分支：文案随分支/推送走，由代码在出口处选场景 ----
    "cast.miss_none": "🪝 空钩在水里漂了半天，鱼碰了碰就游走了",
    "cast.miss_bait": "{原文}",  # 动态文本（带回鱼饵名）
    "cast.miss_deep": "{原文}",  # 动态文本（带回钓点名）
    "cast.achievement": "{原文}",  # 动态文本（推送：新成就）
    "cast.milestone": "{原文}",  # 动态文本（推送：里程碑）
    "cast.egg": "{原文}",  # 动态文本（推送：彩蛋）
    "cast.save_failed": (
        "⚠️ 这一竿没存上，记录可能留不住\n"
        "　把这条发给管理员核对（日志里有详情）"
    ),
    "cast.load_failed": (
        "😵 读取你的存档失败了（可能是数据库正忙）\n"
        "　这次操作没有扣任何东西，稍后再试一次就好"
    ),
    "collectibles.view": "{原文}",  # 动态文本
    "broadcast.catch": "{原文}",  # 动态文本
    # ---- 自动补给 / 称号 / 香火供奉（v1.18.17）：文案都由代码拼装 ----
    "auto.view": "{原文}",
    "auto.on": "{原文}",
    "auto.off": "✅ 已关掉手气道具的自动补给（鱼饵照旧自动补）",
    "auto.bad": "{原文}",  # 动态文本（带回可选项清单）
    "title.list": "{原文}",  # 动态文本（称号清单）
    "title.bought": "{原文}",  # 动态文本（带回称号名与余额）
    "title.owned": "{原文}",
    "title.not_found": "🤔 没有这个称号，发 /钓鱼 称号 看清单",
    "title.no_gold": "{原文}",  # 动态文本（带回差价）
    "title.not_owned": "🎒 你还没买这个称号（/钓鱼 称号 买 <名字>）",
    "title.equipped": "{原文}",  # 动态文本（带回称号名）
    "title.disabled": "🏷 本服没有配置称号（title_defs 留空）",
    "offering.done": "{原文}",  # 动态文本（带回加成与余额）
    "offering.no_gold": "{原文}",  # 动态文本（带回差价与效果）
}

#: 少数场景在「原文」之外还提供的占位符：(占位符, 示例值)。
#: ⚠️ 只有调用点真的把值传进来了才能登记在这里，否则模板永远渲染不出来。
EXTRA_PLACEHOLDERS: dict[str, tuple[tuple[str, str], ...]] = {
    "cast.hit": (
        ("鱼名", "鲤鱼"),
        ("品质", "⚪普通"),
        ("估值", "1,240"),
        ("余额", "3,800"),
        ("评价", "完美"),
    ),
    "cast.miss_none": (
        ("鱼饵", "🪝空钩"),
        ("钓点", "🏡新手村"),
    ),
    "cast.miss_bait": (
        ("鱼饵", "🪱蚯蚓"),
        ("钓点", "🏞️山间湖泊"),
    ),
    "cast.miss_deep": (
        ("鱼饵", "🪱蚯蚓"),
        ("钓点", "🌊近海渔场"),
    ),
    "pull.hook": (
        ("鱼名", "鲤鱼"),
        ("秒数", "6"),
        ("手感", "竿尖猛地弯了下去"),
    ),
    "cast.achievement": (("列表", "初次下水"),),
    "broadcast.catch": (
        ("昵称", "小明"),
        ("渔获", "🐟鲤鱼"),
    ),
}

#: 「原文」的示例值（编辑器预览用）。没登记的用下面那句通用说明
SAMPLE_ORIGINAL: dict[str, str] = {
    "cast.hit": "🎣 🐟鲤鱼　💰 1,240　⚪普通　余额 3,800",
    "cast.junk": "🪝 钩上来一只旧鞋（杂物 +1）",
    "cast.miss_none": "🪝 空钩在水里漂了半天，鱼碰了碰就游走了",
    "cast.miss_bait": "🎣 咬了一口又吐掉了——🪱蚯蚓 白搭了",
    "cast.miss_deep": "🌊 🌊近海渔场 水太深了，鱼不太愿意开口",
    "cast.multi_summary": "🎣 连钓 10 次\n1. 🐟鲤鱼 常见 120金\n…\n✅ 上鱼 8 条｜空竿 2 次｜杂物 0 个",
    "pull.hook": "🐟 鲤鱼 咬钩了！竿尖猛地弯了下去\n⚡ 6 秒内发 /钓鱼 拉（或点下面的按钮）",
    "pull.timeout": "💨 超时了——鲤鱼 吐钩跑了（这一竿的鱼饵已经用掉了）",
    "pull.escape": "👍良好　但线一松——鲤鱼 挣脱跑了",
    "bag.list": "🎒 背包 3/30 条 · 总估值 1,860 金币\n 1.🐟鲤鱼　⚪普通　120 金币",
    "bag.empty": "🎒 背包空空的（容量 30）\n💡 发 /钓鱼 下竿试试手气",
    "sell.result": "💰 卖出 3 条，收入 360 金币\n　余额 4,160",
    "shop.list": "🛒 商店　💰 3,800\n🎣 当前鱼饵：🪝空钩\n— 鱼饵 —",
    "shop.bought": "🛒 购买 🪱蚯蚓 ×20",
    "rod.bought": "🎣 买到 🎣碳素竿！",
    "location.list": "📍 钓点列表\n　🏡新手村　×1.00　需1级\n💡 点按钮看图鉴",
    "help.page": "🎣 帮助 1/7 · 基础\n\n　/钓鱼　　　　　下竿\n\n💡 /钓鱼 帮助 2",
    "rank.view": "📊 群内排行\n🥇 小明　1,240 金币\n　你的排名：第 3 名",
    "story.prompt": "❔ 水面上漂来一个木箱\n　1. 打开看看　/钓鱼 事件 1\n　2. 不理它　/钓鱼 事件 2",
    "story.result": "📦 箱子里是几枚旧硬币。",
    "aquarium.view": "🐠 水族馆 1/8\n 1.🐟鲤鱼　⚪普通\n🧮 估值 120",
    "collection.view": "📖 鱼种图鉴 12/232",
    "profile.view": "📇 档案\n等级 5　金币 3,800",
    "today.view": "🌤️ 今日　晴　💰 行情：鲤鱼 +10%",
    "sign.result": "✅ 签到 +30　💰 3,830",
    "broadcast.catch": "📢 小明 钓到了 🐟鲤鱼！",
    "sell.locked_note": "🔒 另有 2 条锁定的鱼留在背包里（/钓鱼 解锁 1 可以解锁）",
    "cast.achievement": "🎉 初次下水",
    # ---- 自动补给 / 称号 / 供奉（v1.18.17）----
    "auto.view": "🤖 自动补给\n　当前：🎐锦鲤玉佩（鱼饵用光会自动买）\n　写法：/钓鱼 自动 锦鲤玉佩",
    "auto.on": "✅ 自动补给已设为 🎐锦鲤玉佩\n　buff 用光时自动买 1 个并立刻用上",
    "title.list": "🏷 称号　当前：🎣钓鱼新手\n　🎣钓鱼新手　已拥有　刚拿到竿子的第一天",
    "title.bought": "🏷 买下称号「🔱深海领主」并戴上了！\n　💰 余额 3,800",
    "title.owned": "✅ 你已经有「深海领主」了（/钓鱼 称号 戴 深海领主）",
    "title.no_gold": "💸 「归墟之主」要 5,000,000 金币，你还差 4,996,200",
    "title.equipped": "✅ 已戴上「🔱深海领主」",
    "offering.done": "🕯️ 供奉成功！接下来 24 小时：\n　挂机产出 +50%　手气 +0.05",
    "offering.no_gold": "💸 供奉一次要 200,000 金币，你还差 196,200",
}

#: 通用兜底示例（没登记的动态场景用它）
SAMPLE_FALLBACK = "（这条回复由插件按玩家数据拼装，这里只是示意）"

#: 模板必须保留 ``{原文}`` 的场景（= 文本由代码拼装，默认模板就是 ``{原文}``）
DYNAMIC: frozenset[str] = frozenset(
    scene for scene, template in TEXTS.items() if template == "{原文}"
)

#: 场景 -> 允许出现的占位符（含永远允许的 ``原文``）
PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    scene: ("原文",) + tuple(name for name, _sample in EXTRA_PLACEHOLDERS.get(scene, ()))
    for scene in TEXTS
}

#: 场景 -> 占位符示例值（编辑器预览 + 校验提示用）
SAMPLES: dict[str, dict[str, str]] = {
    scene: dict(
        {"原文": SAMPLE_ORIGINAL.get(scene, SAMPLE_FALLBACK)},
        **{name: sample for name, sample in EXTRA_PLACEHOLDERS.get(scene, ())},
    )
    for scene in TEXTS
}

#: ``text_overrides`` 的默认值（空 = 全用内置文案，行为与没有这个功能时逐字一致）
TEXT_OVERRIDES_DEFAULT: str = ""

#: 全部可改的场景键（给编辑器页面与护栏断言用；顺序即 TEXTS 的定义顺序）
TEXT_KEYS: tuple[str, ...] = tuple(TEXTS.keys())

#: 能被 ``TEXTS`` 渲染的合法占位符写法（字母/下划线开头，或中文键）
_PLACEHOLDER_RE = None


def _placeholder_names(template: str) -> tuple[str, ...]:
    """取出模板里的 ``{占位符}`` 名字（支持中文键）。"""
    global _PLACEHOLDER_RE
    if _PLACEHOLDER_RE is None:
        import re

        _PLACEHOLDER_RE = re.compile(r"\{([^{}\s]+)\}")
    return tuple(dict.fromkeys(_PLACEHOLDER_RE.findall(str(template or ""))))


def needs_original(scene: str) -> bool:
    """这个场景的模板是否**默认必须**保留 ``{原文}``。

    只有「文本由代码拼装、又没有别的具体占位符可用」的场景才必须保留：
    ``bag.list|只有这句`` 会把整个背包列表吃掉，所以默认不生效
    （想故意吃掉就写 ``!!`` 开头的整段替换，见 ``is_full_replace``）；
    而 ``cast.hit|🎣 恭喜 {鱼名}`` 有具体占位符可用，是站长的正当改写。
    """
    return scene in DYNAMIC and len(PLACEHOLDERS.get(scene, ("原文",))) <= 1


#: 整段替换的开关前缀：模板以它开头 = 「我知道会丢掉动态内容，我就要自己写整段」
FULL_REPLACE_PREFIX = "!!"


def is_full_replace(template: str) -> bool:
    """模板是不是「整段替换」（``!!`` 开头）。"""
    return str(template or "").lstrip().startswith(FULL_REPLACE_PREFIX)


def strip_full_replace(template: str) -> str:
    """去掉「整段替换」前缀，返回真正的模板正文。"""
    text = str(template or "").lstrip()
    if text.startswith(FULL_REPLACE_PREFIX):
        return text[len(FULL_REPLACE_PREFIX):].lstrip()
    return text


def bad_placeholders(scene: str, template: str) -> tuple[str, ...]:
    """模板里出现了该场景不允许的占位符时返回它们（保存时告警并跳过该行）。"""
    allowed = PLACEHOLDERS.get(scene)
    if allowed is None:
        return ()
    return tuple(name for name in _placeholder_names(template) if name not in allowed)


def parse_overrides(raw: object, *, warn=None) -> dict[str, str]:
    """解析 ``text_overrides``：``场景|模板``（一行一条）。

    * ``#`` 开头 / 空行 → 跳过（不算错）
    * 未知场景 → 跳过并告警（只报一次，附首条坏行）
    * 缺竖线 / 空场景 / 空模板 → 跳过并告警
    * 占位符不在该场景允许集合里 → 跳过并告警
      （避免出现「站长写了 {鱼名} 但这条回复根本没这个值」这种静默失效）
    """
    out: dict[str, str] = {}
    bad = 0
    first_bad = ""
    first_reason = ""
    for line in str(raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        scene, sep, template = text.partition("|")
        scene = scene.strip()
        template = template.strip()
        if not sep or not scene or not template:
            bad += 1
            first_bad = first_bad or text
            first_reason = first_reason or "缺竖线或内容为空"
            continue
        if scene not in TEXTS:
            bad += 1
            first_bad = first_bad or text
            first_reason = first_reason or f"没有这个场景键（{scene}）"
            continue
        wrong = bad_placeholders(scene, template)
        if wrong:
            bad += 1
            first_bad = first_bad or text
            first_reason = first_reason or (
                f"{scene} 里不该出现 {{{wrong[0]}}}（可用："
                f"{'、'.join(PLACEHOLDERS.get(scene, ())) or '无'}）"
            )
            continue
        if needs_original(scene) and "{原文}" not in template \
                and not is_full_replace(template):
            bad += 1
            first_bad = first_bad or text
            first_reason = first_reason or (
                f"{scene} 的正文由插件按数据拼装，模板里必须保留 {{原文}}"
                f"（或者用 !! 开头表示「整段替换、不要动态内容」）"
            )
            continue
        out[scene] = template
    if bad and warn:
        warn(
            f"text_overrides 有 {bad} 行没生效（{first_reason}；首条：{first_bad[:40]}）"
        )
    return out


def render_scene(
    scene: str,
    original: str,
    values: dict | None = None,
    overrides: dict | None = None,
) -> str:
    """算出一条回复的最终文案：站长的覆盖模板优先，渲染不了就用代码原文。

    永远不会把 ``{占位符}`` 原文丢给玩家：
      1. 没有覆盖 / 覆盖为空 → 代码原文
      2. 动态文本的模板丢了 ``{原文}`` → 代码原文（当没写）
         —— 除非模板以 ``!!`` 开头：那是站长明确要求的**整段替换**，
         直接用他自己写的那段（动态内容被他主动放弃）
      3. 模板渲染异常（占位符没值、格式写坏）→ 代码原文
    """
    text = "" if original is None else str(original)
    if not overrides or not scene:
        return text
    template = str(overrides.get(scene) or "")
    if not template:
        return text
    if is_full_replace(template):
        template = strip_full_replace(template)
        if not template:
            return text
    elif needs_original(scene) and "{原文}" not in template:
        return text
    data = {"原文": text}
    for key, value in (values or {}).items():
        data[str(key)] = "" if value is None else str(value)
    try:
        return template.format(**data)
    except (KeyError, IndexError, ValueError):
        return text


__all__ = [
    "TEXTS",
    "TEXT_KEYS",
    "TEXT_OVERRIDES_DEFAULT",
    "PLACEHOLDERS",
    "SAMPLES",
    "DYNAMIC",
    "EXTRA_PLACEHOLDERS",
    "bad_placeholders",
    "needs_original",
    "parse_overrides",
    "render_scene",
]
