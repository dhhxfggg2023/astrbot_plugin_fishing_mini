# 待办 / 下一步开发

> 这个文件记录「已经和用户确认、但还没做」的需求，避免跨会话丢失。
> 完成一项就删掉一项；**已完成但容易踩坑的知识**放在文末「经验留档」，不要删。

## 当前没有待办

**v1.13.0 已完成**：① 按钮样式统一设置（策略 + 统一样式，默认行为不变）
② 效果键注册表（`_effects.EFFECTS` 一处生效）③ `extensions/*.py` 扩展点（含失败隔离、
示例扩展、README 章节、`test_effects_ext.py` 76 项回归）。

v1.10.0 的四项需求（玩家金币编辑 / 命令别名 / 自定义命令 / 配置面板瘦身）、
v1.11.0 的**养成道具重做**（三种角色：喂鱼 / 钓手手气 / 鱼缸装饰）、
v1.12.0 的**回复全配置化**（154 个回复场景：每条回复都能单独配按钮 + 改文案、
编辑器「💬 回复」卡片页 + 常驻预览、珊瑚造景可一次摆多个、`index.json` 不再空写）与
v1.12.1 的**紧急修复**（页面白名单写死 5 个场景 → 改命令别名被整页拒收；
改成「场景从插件接口实时取 + 保存只校验这次改过的行 + 精确到格子的报错与跳转」）
都已交付，详见 README 的 `### v1.12.1` / `### v1.12.0` / `### v1.11.0` / `### v1.10.0` 更新日志。
下一轮想做什么，先和用户确认再往这里写。

### 站长侧待办（不是代码问题，需要他自己点一下）

- 他配置里的 `command_aliases` **仍然缺「排名」**（那次编辑被 v1.12.0 的保存闸门吞了）：
  升级到 v1.12.1 后**刷新编辑器页面**，在「⌨️ 命令」页给 `排行` 那行补上 `,排名` 再保存即可。
- 他配置里的 `button_defs` 有 4 行用了 `cast.miss_none` / `cast.miss_bait` /
  `cast.miss_deep` / `cast.multi_summary` —— 这 4 行本来就是合法的，升级后不再判红。
- 遗留目录 `x5_eyhfj4p4/` 需要他在插件目录下手动 `Remove-Item -Recurse -Force`。

### 回复场景体系（v1.12.0）的维护约定 —— 改代码前必读

1. **场景清单是唯一真相**：`_calc.py` 的 `SCENE_GROUPS` + `REPLY_SCENES`（场景 id、
   分组、说明、继承的父场景）。新增一条给玩家看的回复 = 在这里加一行 + 在
   `_texts.py` 的 `TEXTS` 里加同名键（两处必须一一对应，`test_local.py` 第 18 组断言会红）。
2. **回复出口必须带场景**：所有 `event.plain_result(...)` 都要包在
   `async for _r in self._say_msg(event, "场景", event.plain_result(...)): yield _r`
   里（或 `self._say(event, 文本, "场景", values=...)` / `await self._push(event, "场景", 文本)`）。
   裸写 `yield event.plain_result(...)` 会被第 18 组的 AST 扫描抓到。
3. **默认行为不许变**：新场景默认**没有按钮**（`REPLY_SCENES` 的继承字段留空）；
   老的 5 个场景（cast/pull/bag/location/story）仍然是内置默认按钮的拥有者，
   从它们拆出来的 7 条回复靠「继承」沿用那一组。文案的默认值 = 代码原本的字符串，
   `text_overrides` 留空时一个字都不变（默认文案由「由代码拼装」决定是
   `{原文}` 还是可整段改写，见 `_texts.TEXTS`）。
4. **改文案/按钮的运行时链路**：`_say` / `_say_msg` / `_push` → `_views._scene_text`
   （文案覆盖，含父场景继承）→ `_texts.render_scene`；按钮走 `_views._scene_items`
   （自己配的 > 继承父场景 > 内置）→ `_layout_rows`（按 `button_layout` 排版）。
5. **占位符要真的传值**：`_texts.EXTRA_PLACEHOLDERS` 里登记了某个场景的额外占位符，
   就要求对应调用点通过 `values={...}` 把值传进来；只登记不传值 = 站长写了也渲染不出来
   （渲染失败会安全回退原文，但这是静默失效，属于坑）。
6. **编辑器页面**：`scenes` 端点（`_editor_bridge.py`）给页面喂场景卡片数据，
   保存走 `config` 端点的 `save_replies` 动作（只提交改动的那几项）。
   页面断言在 `test_editor_ui.js` 的 `[13]~[15]` 组，改页面后必须 `node test_editor_ui.js`。

## 建议的下一步（**未与用户确认**，仅备选）

- 鱼缸装饰目前只有「加成挂机产出」一种作用；若用户想要「装饰也能提升鱼的成长速度」
  或「装饰有互斥/组合效果」，在 `_decoration_bonus()` 里扩展，注意
  `_prune_decorations()` 要在**每次读取前**先剪过期项。
- 钓手手气道具只有 `buff_quality` 一种（提升个体品质概率）。若要做「提升稀有鱼出现率」
  的手气道具，需要给 `buff_casts_left` 配一个「作用类型」字段，否则两种 buff 会互相顶掉。
- 玩家页目前只能改金币；如果用户想要「重置某个玩家 / 补发鱼饵」这类操作，
  照 `_editor_set_player_gold` 的写法加动作即可（**只加白名单字段**，仍然二次确认 + 改前自动存档）。
- 别名支持「少打空格」已经可用；若用户要求别名也能改**内置写法的含义**
  （例如让 `/钓鱼 包` 变成别的子命令），需要改分派链而不是别名表 —— 目前是刻意不做的。
- 自定义命令若用户想要更多动作（例如 `发送:` 附带图片、`执行:` 带权限判断），
  在 `_calc.CUSTOM_COMMAND_ACTIONS` 里加类型 + 在 `fishing()` 的自定义分支里加执行分支。

---

## 经验留档（踩过的坑，勿再走弯路）

### 1. 数据隔离（最重要，曾造成不可恢复的损失）

`_refresh_config()` 会按配置项 `backup_dir` **重建** `self.backup_store`，
所以测试里只赋值 `backup_store` 是拦不住的 —— 必须在 `make_plugin()` 里
把 `backup_dir` 钉进系统临时沙箱（现在就是这么做的 + 有断言），
并且在任何删除/清理类用例前断言「存档根目录确实在沙箱里」。
历史上因为这条，测试把真插件目录里 55 份手动快照删掉了（不可恢复）。
`test_local.py` 的 `[17]` 还加了一条「真插件 `backups/` 前后零变化」的自检。

### 2. 编辑器页面通道（已打通，勿再走弯路）

- 插件页面 SDK 的 endpoint 会被拼成 `/api/v1/plugins/extensions/<插件名>/<endpoint>`；
  该路径由 AstrBot 的 `_call_plugin_extension` 在 `star_context.registered_web_apis` 里匹配，
  匹配不到就是「未找到该路由」。
- 因此**插件必须自己注册 Web API**：
  `context.register_web_api("/<插件名>/config", handler, ["GET","POST"], desc)`
  —— route 必须带插件名前缀，页面只传相对路径 `config`。
- 现有 4 个路由：`config`（GET 读 / POST 写）、`snapshot`（存档动作）、
  `players`（玩家列表 / 存档内玩家 / 改金币）。**加新路由就照这个模式加一条 route spec。**
- ❌ 不要再用「上传文件 + 轮询」通道：真实环境返回 `403 Insufficient API key scope`。
- ❌ 不要再猜 `/api/plugins/<id>/config` 这类路径：那是 AstrBot 自己的接口，不在插件页面权限内。
- ⚠️ 页面里的 `sendCommand()` 会把 `snapshot_` 开头的动作**自动改投到 `snapshot` 路由**并去掉前缀 ——
  名字里带 `snapshot_` 但实际不属于存档的动作（例如 `snapshot_gold`）必须直接 POST 到自己的路由，
  否则会被改写成 `{action: "gold"}` 打到存档接口上（v1.10.0 真的踩到过，靠 `test_editor_ui.js` 抓到）。

### 3. 改 `pages/editor/index.html` 的硬规矩

- **只做最小改动**：先 `read` 再 `edit`，**永远不要用正则整段替换函数**（已经两次把页面写坏）。
- **每改一处立刻跑 `node test_editor_ui.js`**（离线 + 假 SDK 两种通道都会跑）。
- **页面里任何「插件侧才有的清单」都不许写死**（v1.12.1 的教训，代价是站长存不了配置）：
  场景键有 154 个、还会随版本增加，写死的白名单会把合法数据判成红线。
  需要清单就从插件接口拿（`scenes` 端点给了 `scene_total` 和每个场景），
  拿不到时**返回 null 表示不校验**，并且**永远不要拿校验结果去拦整页保存**。
- **保存闸门只拦「这次改过的行」**：`doSave()` → `diffTab(id).dirty` → `problemsInTab(id, true)`；
  没动过的行里的问题走 `stale` 分支只提醒（`tabHealth()` 给标签上的 ⛔/⚠ 供数）。
  报错必须给出「表 · 行号 · 行标识 · 字段 · 原因」，并用 `gotoProblem()` 跳过去 ——
  只说「有内容没填对」等于让站长自己一格格找，历史上就是这么丢掉他的别名编辑的。
- 加标签页时要同步：`TABS`、`TABLE_DEFS`、`state.data` 初始化、
  `test_editor_ui.js` 里的标签数 / 载荷字段数 / 序列化键清单断言。
- `invisible` 是**显示开关**：`_config_schema_to_default_config()` 不看它，
  所有项照样进默认配置；AstrBot 前端的 `AstrBotConfigV4` 用 `l.invisible` 决定要不要渲染那一行。
  所以「隐藏」不会让配置值丢失，也不会让编辑器页面改不了（页面白名单来自 `DEFAULTS`）。

### 4. 配置默认值同步

- `_defaults_fingerprint()` 只对**参与同步**的键做哈希；新增配置项若属于
  「站长自己写的内容」（内容表、命令别名、自定义命令）或纯说明项，
  必须加进 `DEFAULTS_SYNC_EXCLUDE_KEYS`，否则升级时会把站长的编辑覆盖掉，
  也会让指纹变化触发一次全量同步。

### 5. 道具效果键（v1.11.0 的老规矩；v1.13.0 起简化成一处，见 §7）

效果键的白名单在**三个地方**，改的时候一起改，否则会出现「配置里写了但没生效」：

1. `_calc.py` 的 `_parse_effects()` —— `allowed` 元组（真正的解析口，写错的键**静默忽略**）；
2. `pages/editor/index.html` 的 `ITEM_EFFECT_KEYS` —— 页面提示 + 行校验（写错会标红）；
3. `test_editor_ui.js` 会**直接读 `_calc.py` 的源码**断言两边键集合一致，所以漏改会立刻报错。

三种角色的语义（别混）：

- 喂鱼 `meat/spirit/sheen/value_up` → **一次性永久**，落在某条鱼的实例上；
  `feed_bonus` 只抬这条鱼的**投喂次数上限**（`feed_max_uses` + 累计 `feed_bonus`，夹在 0~20）。
- 钓手手气 `buff_quality` → 落在**玩家**身上，`luck_charges` + `buff_casts_left`；
  `_engine.py` 每竿扣一次 `buff_casts_left`，**只有扣到 0 才清 `luck_charges`**
  （彩蛋捡到的手气没有竿数，仍然一竿即清）。**别在喂鱼分支里改玩家状态。**
- 鱼缸装饰 `decorate` → 落在 `player["decorations"]` 列表，每条有自己的 `expire_ts`；
  加成在领取挂机收益时用 `_decoration_bonus()` 现算（时间按真实时间，下线也走）。

### 7. 效果键现在只有一处（v1.13.0）

加一个内置效果键，只需要在 `_effects.py` 的 `BUILTIN_EFFECTS` 里加一行 `EffectSpec`：

* 解析白名单：`load_extensions()` / `_apply_extensions()` 会调 `sync_to_calc()`，
  把键名同步进 `_calc.EFFECT_ALLOWED`（解析实现仍在 `_calc._parse_effects`，
  数值写法与历史版本逐字一致）
* 页面清单：`_editor_bridge` 把 `effect_table()` 塞进 `config` 与 `scenes` 两个接口，
  页面 `applyEffectKeys()` 覆盖内置兜底清单 —— **别再往页面里写死清单**
* 编辑器校验：页面用插件发来的键名判断「插件不认识的效果键」，拿不到就**不校验**
  （宁可漏报，也不能把本来对的行标红 —— v1.12.1 的教训）

配套的坑：

* `test_editor_ui.js` 会去读插件源码比对白名单。白名单从 `_parse_effects` 函数体搬到
  模块级 `EFFECT_ALLOWED` + `_effects.BUILTIN_EFFECTS` 之后，**测试的正则要跟着改**
  （现在同时校验「注册表顺序 == 白名单顺序」）
* `TABLE_DEFS` 是页面加载时建的静态对象，`title` 已经把效果说明快照下来了；
  `applyEffectKeys()` 里要顺手把它同步回来，否则表头 tooltip 还是旧的
* `_effects` / `_calc` 是 `_load_sibling` 按**别名**注册的（`astrbot_fishing_effects`）。
  兄弟模块里要拿对方，必须先查 `sys.modules`，裸 `import _calc` 会拿到另一个实例
  （白名单里没有扩展键，扩展键就解析不出来了 —— 单测里踩过）
* 扩展处理函数的签名固定为 `(*, plugin, player, item_id, key, value)`；
  只有扩展键的道具由插件代劳「消耗一件 + 保存 + 提示」，和内置效果混在一件道具上时
  提示仍由内置分支负责（扩展的文字不单独显示）

### 8. 按钮样式：0 = 灰、1 = 蓝（v1.13.1 更正，别再猜）

**官网取值就是 0 和 1，别自己发明数字。**
《消息按钮》文档写得很死：`render_data.style | int | 是 | 按钮样式：0 灰色线框，1 蓝色线框`
（bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html）。
`botpy` 的 `RenderData.style` 只是个裸 `int`（没有枚举），所以文档就是唯一定义。

* v1.13.1 之前 `_calc.BUTTON_STYLE_ALIASES` 写的是 `default=1 / primary=4` —— 纯猜的。
  后果：标着「默认（灰）」的按钮在 QQ 里是**蓝色线框**，而 `primary=4` 根本不在文档里。
  现在 `default=0`、`primary=1`，`BUTTON_STYLE_DEFAULT = 0`，`_interactions._btn` 兜底也是 0。
* **页面与插件必须用同一套数字**：`pages/editor/index.html` 的 `normalizeButtonStyle()`
  把 `0→default`、`1→primary`，其余数字（2~255）**原样透传**。
  页面认不出的数字绝不能改写成 default —— 那是「保存一次就把玩家的按钮颜色全改了」。
* 改这张表 = 改站长线上按钮的颜色。要改先想清楚：老配置里 `|default` 的按钮会换色。
* 数值样式（`|7` 这种）任何环节都不要翻译、不要四舍五入，官网只保证 0/1 的含义，
  其余数字是「原样透传」，插件不替 QQ 做解释。

### 9. 订单为什么按钓点抽（v1.14.0）

`_roll_orders(level, location_id=None)`：

* 给了 `location_id` -> 候选池 = `_location_pool(location_id)`（**当前钓点**）；
  首选 = 等级品质档内的鱼，补充 = **同一个图**其它品质的鱼。本图这一档不够时用
  同图的鱼补满（`order_count` 不缩水），**绝不跨图** —— 「站在哪儿只能被点哪儿的货」
  是硬约束，不然又会出现「要一条你根本钓不到的鱼」（v1.14.0 之前就是这样：
  池子是全部 232 种，会点到还没解锁的钓点的鱼）。
* `location_id=None` -> 老行为：全鱼池里按品质档抽，补充池为空。
* ⚠️ 抽签顺序不能反：`_order_pools()` 返回 `(首选, 补充)`，**先抽首选、不够再补**。
  直接 `random.sample(首选 + 补充)` 会把等级品质档稀释掉
  （Lv20 站在新手村就会一直点常见鱼）。

换钓点换单（`_ensure_orders`）：

* `expired = (没订单) or 到点了` -> 换一批，并把 `order_move_rerolls` **清零**；
  没到点但 `order_location != current_location` -> 也算要换（`moved`），
  但要受 `order_move_rerolls` 限制（默认 1）。
* ⚠️ 顺序陷阱：一开始写成「只要 `moved` 就 +1」，结果**到点刷新和换图撞在一起**时
  计数被 +1 而不是清零，玩家在新周期里白白少一次换单机会。现在 `expired` 优先判断。
* 换单**不重置** `order_next_ts`（否则来回换图 = 无限刷单）；
  每个周期最多换 `order_move_rerolls` 批，这就是防刷单的那道闸。
* 移动回复只在「本来就有订单 + 真的换了」时才多一行，别在没看过订单的玩家那儿刷屏。

`/钓鱼 去` 与 `解锁并前往` **两处**都要调 `_ensure_orders`（`_cmd_locations` 里
两个 `player["current_location"] = ...` 各一处），漏一个就会出现「换图了订单没跟」。

测试口径：`test_local.py` 的「订单按当前钓点刷新」段 —— 抽 200 批断言零越界、
换图换单 + 计数、上限拦住第二次换图、到点刷新清零、开关关掉完全回老行为、
隐藏生物开关、`order_move_rerolls=0`。**别只测「抽出来的鱼在图里」**，
「不换单」那条路径也要钉住，否则防刷单被改坏了没人知道。

### 10. 水族馆相处机制：方向比强弱更重要（v1.15.0）

站长报的「藤壶怎么把章鱼吃了」不是概率问题，是**规则方向**写错了：
旧版是「狠角色 + 对手（另一个狠角色或紧挨着的邻居），谁 `_fish_power` 低谁没」。
于是养肥的藤壶（`_fish_power` 高）能把章鱼冤死 —— 文案还写成「藤壶把章鱼逼到了角落」，
等于说藤壶是凶手。**温和鱼根本不该有下嘴的能力。**

现在的规则（`_resolve_tank_conflicts`）：

* 只有 `_is_hostile` 的鱼会主动攻击；
* 对手是另一个狠角色 -> 壮的吃弱的（duel，文案「打了一架，X 没了」）；
* 对手是温和鱼 -> 狠角色**必须 `_fish_power >=` 对方**才吃得下，
  否则 `continue` 换下一个狠角色试（小章鱼挨着养肥的大鱼：两条都活着）；
* 温和鱼之间永远无事发生。

坑与约定：

* 一轮最多淘汰一条，淘汰后重新扫描（外层 `for _ in range(len(aquarium))` +
  内层找到能下嘴的就 `break`，靠 `acted` 判断「这轮没人动手」就收工）——
  不要写成「每轮固定换掉一条」，否则同一个狠角色会被反复配对。
* **不要用 `diff`（拉线难度）当捕食者信号**：实测章鱼 diff=0.0、月华水母 diff=0.62，
  两者完全分不开。分类只有 `HOSTILE_KEYWORDS` 一张表。
* `_parse_word_list("")` 返回**内置默认**（不是空元组）：所以「清空关键词 = 恢复默认」，
  页面文案也照这个说。想让缸里彻底没有狠角色，只能写一个匹配不到任何鱼名的词。
* 页面侧要改这张表，靠 `NUMBER_KEYS` 第 5 位 `true` 标成文本行：
  `cellHtml` / `onCellInput` / `validateRow` / 对齐 class 都判了 `row.text`，
  **漏一处就会出现「需要是数字」或者把逗号串 `Number()` 成 NaN**。
  加断言见 `test_editor_ui.js`「文本行…」四条。
* ⚠️ 探针里 `import _editor_bridge as eb` 拿到的是**另一份模块实例**
  （`DEFAULTS` 没注入 → `number_whitelist()` 返回 0 条，看着像「白名单没这个键」）。
  要验证页面通道，必须用插件按别名加载的那份：`sys.modules["astrbot_fishing_editor_bridge"]`。
  同样的坑 `_calc` / `_effects` 也有（见 §7）。

### 11. 页面校验用的是插件下发的清单 —— 下发什么就得包含什么（v1.15.1）

站长报「UI 里洗髓丹那行报错：不认识的效果键」。那行写的是 `quality_up=0.30`，
而 `_calc._parse_effects` 一直照收（别名 -> `buff_quality`）：**插件认，页面不认**。

根因：v1.13.0 把页面的写死清单换成 `_effects.effect_table()` 下发，
而 `as_dict()` 只给了**规范键名**，没给 `EFFECT_ALIASES` 里的旧写法。
页面于是拿着一份「少了别名」的清单去校验，把一行完全能用的配置标红。

规矩：

* 注册表里凡是**解析器会接受**的键，都必须出现在 `effect_table()` 的 `as_dict()` 里
  （现在每行带 `aliases`），否则页面就会误报。加别名的同时要加断言：
  `test_effects_ext.py`「每行都带 aliases」+ `test_editor_ui.js`「旧写法也进白名单」。
* 页面**只接受、不改写**：`ITEM_EFFECT_ALIASES` 只用于认键和写 tooltip，
  绝不把站长写的 `quality_up` 偷偷换成 `buff_quality`。
* 给页面下发清单这类「单一来源」改造，**改完要拿站长的真实配置跑一遍**：
  这次就是他的 `item_defs` 里有旧写法才暴露出来的（实测那行和「锦鲤玉佩」
  解析结果一模一样 —— 洗髓丹是 v1.11.0 之前的遗留行）。

见闻（`hostiles_seen`）的约定：

* 只在**当着玩家的面**发生捕食时记录，记的是**凶手**（`winner`）的鱼种 id；
  `_resolve_tank_conflicts` 因此返回三元组 `(文案, 有没有变, 被看破的鱼种)`，
  调用点（`水族馆 放`）负责并进玩家数据 —— 别在结算函数里直接改玩家字典。
* 展示只在 `/钓鱼 查 <鱼名>`（他要求的入口）。**不要**顺手加进图鉴/背包，
  那些是共享 helper，一行一行改容易漏。
* 老存档没有这个字段 -> `_calc` 读档时补空表，老玩家开局同样是「不知道」。
