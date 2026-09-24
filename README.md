# 群钓鱼 · astrbot_plugin_fishing_mini

**QQ 群**里的钓鱼养成小游戏，基于 **AstrBot v4.x** 插件规范（实测 AstrBot v4.28.1 + Python 3.12）。

**271 种水族 · 19 个钓点 · 10 档鱼饵 · 8 档鱼竿 · 13 种养成道具 · 52 个成就 · 天气 / 鱼市行情 /
订单 / 水族馆 / 群排行榜 / 连载剧情 · QQ 官方按钮**

只注册 `/钓鱼` **一个指令**（避免和别的插件撞名），**无第三方依赖**。

> 📖 **本文只讲怎么把它跑起来。** 玩法机制、数值平衡、全部配置项、指令一览、
> 更新日志与常见问题在 **[README.full.md](README.full.md)**。

---

## 🚀 部署

### 方式一：Docker Compose（推荐，AstrBot + 插件一体镜像）

```bash
git clone https://github.com/dhhxfggg2023/astrbot_plugin_fishing_mini.git
cd astrbot_plugin_fishing_mini

cp .env.example .env          # 填机器人凭据，见下表
docker compose up -d --build  # 首次构建
docker compose logs -f astrbot
```

日志里出现「管理面板已启动」后，访问 **`http://<服务器IP>:6185`** 进 AstrBot 面板，
在 **插件** 页确认 `群钓鱼` 已加载。

`.env` 关键变量（完整说明见 `.env.example`）：

| 变量 | 说明 |
| --- | --- |
| `TZ` | 时区，中国填 `Asia/Shanghai`（影响体力重置、每日额度、鱼市行情） |
| `DASHBOARD_PORT` | 管理面板端口，默认 `6185` |
| `ASTRBOT_DASHBOARD_INITIAL_PASSWORD` | 首次初始化的面板密码，留空 = 自动生成并打印在日志里 |
| `QQ_APPID` / `QQ_SECRET` | **QQ 官方机器人**凭据（QQ 开放平台 → 开发设置） |
| `QQ_USE_MARKDOWN` | 建议 `true` —— 按钮功能依赖官方键盘接口 |
| `ONEBOT_WS_PORT` / `ONEBOT_WS_TOKEN` | **OneBot v11** 反向 WS（NapCat / Lagrange / LLOneBot 连 `ws://<IP>:6199/ws`） |
| `FISHING_CONFIG_OVERWRITE` | `1` = 用 `.env` 强行覆盖 AstrBot 里已有的平台配置 |
| `FISHING_FORCE_SYNC` | `1` = 每次启动都用镜像里的插件覆盖数据卷里的插件目录 |

数据都在 `./data`（`data_v4.db`、`config/`、`plugins/`、`backups/`），**删容器不丢档**。
迁移旧存档：两边都停掉 → 把原来的 `data/` 整个拷到项目里的 `./data/` → `docker compose up -d`。

> ⚠️ `.env` 里全是密钥，**别提交到 Git**（`.gitignore` / `.dockerignore` 已排除）。
> 本插件自身不含任何密钥，机器人凭据属于 AstrBot 主程序，由容器的入口脚本写进
> `data/cmd_config.json`（写入前自动备份，日志里不打印密钥）。

### 方式二：用 GitHub Actions 构建好的镜像（免本地构建）

```bash
docker pull ghcr.io/dhhxfggg2023/astrbot_plugin_fishing_mini:latest

docker run -d --name astrbot \
  -p 6185:6185 -p 6199:6199 \
  -v "$PWD/data:/AstrBot/data" \
  --env-file .env -e TZ=Asia/Shanghai \
  ghcr.io/dhhxfggg2023/astrbot_plugin_fishing_mini:latest
```

首次使用前要到 [package 设置](https://github.com/users/dhhxfggg2023/packages/container/astrbot_plugin_fishing_mini/settings)
把可见性改成 **Public**，否则匿名 `docker pull` 会 404。

### 方式三：装进已有的 AstrBot（不用 Docker）

把整个仓库目录放进 AstrBot 的插件目录，然后到 **WebUI → 插件** 点「重载插件」：

```
<AstrBot 数据目录>/data/plugins/astrbot_plugin_fishing_mini/
```

- Windows：`C:\Users\<用户名>\.astrbot\data\plugins\`
- Linux / macOS：`~/astrbot/data/plugins/`

> ⚠️ AstrBot **只在启动时扫描一次**插件目录，新增目录后必须手动重载或重启，不会自动发现。

---

## ⌨️ 跑起来先试这三条

```
/钓鱼              # 下竿（咬钩了会提示）
/钓鱼 拉           # 咬钩后限时收线（QQ 官方上带按钮，点一下就行）
/钓鱼 帮助         # 13 页帮助，钓点/道具/图鉴都在里面
```

其余操作都有一步到位的短写法：`/钓鱼 卖光光`、`/钓鱼 换饵`、`/钓鱼 图鉴`、
`/钓鱼 今日`、`/钓鱼 排行`、`/钓鱼 档案`、`/钓鱼 洗 1`（用洗髓丹重掷品质）…
批量命令支持任意多个序号（`/钓鱼 卖 1 3 5`）。完整清单见
[README.full.md → 指令一览](README.full.md#-指令一览)。

---

## ⚙️ 配置怎么改

插件自带数据编辑器页面，**玩法内容与全部数值都在那里改**（鱼池 / 钓点 / 鱼竿 / 鱼饵 /
道具 / 称号 / 杂物 / 变异 / 天气 / 彩蛋 / 回复按钮 / 别名 / 大鱼乐奖表，存档管理，
以及 **🧰 玩家数据**：一个玩家的 65 个字段 + 逐条改鱼 + 原始 JSON，实时或存档内都在这改）：

> **AstrBot 面板 → 插件 → 群钓鱼 → 「数据编辑器」页面**

WebUI 的插件配置面板里只保留 3 条救生索，其余配置项都标了 `invisible`
（面板不是改这个插件的地方）。每一项的含义、默认值与调参建议见
**[README.full.md](README.full.md)** 的「插件配置」章节。

---

## 📄 许可

MIT License（见 [LICENSE](LICENSE)）
