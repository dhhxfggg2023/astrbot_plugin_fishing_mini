# 待办 / 下一步开发

> 这个文件记录「已经和用户确认、但还没做」的需求，避免跨会话丢失。
> 完成一项就删掉一项；**已完成但容易踩坑的知识**放在文末「经验留档」，不要删。

## 当前没有待办

v1.10.0 的四项需求（玩家金币编辑 / 命令别名 / 自定义命令 / 配置面板瘦身）已全部交付，
详见 README 的 `### v1.10.0` 更新日志。下一轮想做什么，先和用户确认再往这里写。

## 建议的下一步（**未与用户确认**，仅备选）

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
