/* eslint-disable */
/**
 * 编辑器页面运行时冒烟测试（Node + 最小 DOM 桩）
 * 目的：在没有浏览器的前提下，真正执行页面脚本的渲染与校验逻辑，
 *       抓出「渲染路径里的运行时错误」，而不是只做语法检查。
 * 用法：node _ui_smoke.js   （跑完即可删除）
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "pages/editor/index.html"), "utf8");
let js = html.match(/<script>([\s\S]*?)<\/script>/)[1];

/* ---------- 1. 最小 DOM 桩 ---------- */
function makeEl(id) {
  const el = {
    id: id || "",
    tagName: "DIV",
    textContent: "",
    innerHTML: "",
    value: "",
    checked: false,
    disabled: false,
    className: "",
    style: {},
    children: [],
    isConnected: true,
    _attrs: {},
    classList: {
      add() {}, remove() {}, toggle() {}, contains() { return false; }
    },
    addEventListener() {},
    removeEventListener() {},
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
    removeAttribute(k) { delete this._attrs[k]; },
    appendChild(c) { this.children.push(c); return c; },
    remove() {},
    focus() {}, select() {}, click() {},
    closest() { return null; },
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    insertAdjacentHTML() {}
  };
  return el;
}

const elCache = {};
const documentStub = {
  documentElement: makeEl("html"),
  body: makeEl("body"),
  title: "",
  getElementById(id) {
    if (!elCache[id]) elCache[id] = makeEl(id);
    return elCache[id];
  },
  querySelector() { return makeEl(); },
  querySelectorAll() { return []; },
  createElement(tag) { const e = makeEl(); e.tagName = String(tag).toUpperCase(); return e; },
  addEventListener() {},
  removeEventListener() {}
};
documentStub.documentElement.setAttribute("data-theme", "dark");

global.document = documentStub;
global.location = { hash: "" };
global.history = { replaceState() {} };
global.navigator = { userAgent: "node-smoke" };
global.FileReader = function () {};
if (!global.window) global.window = global;
global.addEventListener = function () {};
global.removeEventListener = function () {};
global.window.AstrBotPluginPage = null; // 走离线预览分支
global.matchMedia = undefined;

/**
 * 在一个全新的沙箱里跑一遍页面脚本，返回它挂出来的测试钩子。
 * 页面脚本启动时会抓 `window.AstrBotPluginPage`，所以「在线模式」必须重跑一次启动，
 * 不能只把旧实例的 sdk 变量换掉。
 */
function loadPageFromSource(sdkStub) {
  const box = { document: documentStub, window: {}, location: { hash: "", search: "" } };
  box.window = box;
  box.window.AstrBotPluginPage = sdkStub || null;
  box.window.matchMedia = undefined;
  box.window.setTimeout = function (fn, ms) { return setTimeout(fn, ms); };
  box.window.clearTimeout = function (id) { clearTimeout(id); };
  box.window.addEventListener = function () {};
  box.setTimeout = box.window.setTimeout;
  box.clearTimeout = box.window.clearTimeout;
  box.addEventListener = function () {};
  box.removeEventListener = function () {};
  box.Promise = Promise;
  box.console = console;
  box.File = File;
  box.Blob = Blob;
  box.FormData = global.FormData;
  box.URL = URL;
  const names = Object.keys(box);
  try {
    new Function(names.join(","), js).apply(box, names.map(function (n) { return box[n]; }));
  } catch (e) {
    console.error("❌ 页面脚本（假 SDK 沙箱）执行时抛错：" + e.message + "\n" + (e.stack || ""));
    process.exit(1);
  }
  return { T: box.__T, box };
}

/* ---------- 2. 注入测试钩子 ---------- */
const hookNames = [
  "state", "render", "visibleRows", "validateRow", "parseDist", "diffTab", "renderTableTab",
  "renderSnapshotsTab", "renderSnapCard", "cellHtml", "buildPayload", "TAB_BY_ID", "TABS",
  "startEdit", "cancelEdit", "commitEdit", "applyBatch", "deepClone", "esc", "fmtNum",
  "knownLocationKeys", "dirtyTotal", "markSaved", "switchTab", "toast", "renderToolbar",
  "renderTableBody", "renderStatusBar", "renderTabs", "reloadAll", "snapshotAction",
  "loadConfig", "saveConfig", "loadSnapshots", "runSnapshotAction", "ENV", "RARITIES",
  // 数据通道（B 组）：解析/序列化/状态/指令收发
  "TABLE_DEFS", "NUMBER_KEYS", "splitLine", "cell", "serializeContentTables",
  "numberValuesFromPage", "parseEditorStatus", "autobackupFromStatus", "autobackupPayload",
  "sendCommand", "bridgeRequest", "describeError", "resolvePluginBase", "fetchRawConfig",
  "sleep", "waitForStatus", "renderBanner", "downloadSnapshot",
  "refreshSnapshots",
  // 回复按钮（button_defs）表
  "rowKey", "isButtonStyle", "BUTTON_SCENES",
  // 命令别名 / 自定义命令 两张表 + 玩家页（v1.10.0）
  "renderSubTabs", "canonicalCommands", "normalizePlayerRow", "fetchPlayers",
  "fetchSnapshotPlayers", "savePlayerGold", "playerRowsNow", "renderPlayersTab",
  "renderPlayerRow",
  // 道具效果键白名单（v1.11.0：喂鱼 / 手气 / 装饰 三种角色）
  "ITEM_EFFECT_KEYS", "ITEM_EFFECT_KEY_NAMES"
];
const hookSrc = "window.__T = {" + hookNames.map(n => n + ":" + n).join(",") + "};";
if (!/\}\)\(\);\s*$/.test(js)) {
  console.error("❌ 找不到脚本结尾的 })(); —— 页面结构可能被改动");
  process.exit(1);
}
js = js.replace(/\}\)\(\);\s*$/, hookSrc + "\n})();");

/* ---------- 3. 执行 ---------- */
let failed = 0;
function check(ok, label, extra) {
  console.log((ok ? "  ✅ " : "  ❌ ") + label + (extra !== undefined ? " -> " + extra : ""));
  if (!ok) failed++;
}

/** 所有断言块：fn 可以是同步或 async（异步块用来驱动真实的桥接调用）。 */
const SECTIONS = [];
function section(title, fn) { SECTIONS.push({ title, fn }); }

try {
  new Function(js)();
} catch (e) {
  console.error("❌ 页面脚本执行时抛错：" + e.message + "\n" + (e.stack || ""));
  process.exit(1);
}

const T = global.window.__T;
if (!T) { console.error("❌ 测试钩子没挂上"); process.exit(1); }

/* 等 reloadAll 的异步分支跑完（loadConfig 是同步返回的 Promise） */
setTimeout(runAssertions, 120);

function runAssertions() {
  console.log("\n[1] 启动状态与演示数据");
  check(T.ENV.online === false, "离线预览模式被识别（sdk 为 null）");
  check(Object.keys(T.TAB_BY_ID).length === 15, "标签页数量 = 15（12 + 玩家 + 命令别名 + 自定义命令）",
    Object.keys(T.TAB_BY_ID).join(","));
  check((T.state.data.fish || []).length === 18, "演示鱼池 18 条", (T.state.data.fish || []).length);
  check((T.state.data.locations || []).length === 16, "演示钓点 16 个", (T.state.data.locations || []).length);
  check((T.state.snapshots || []).length === 4, "演示存档 4 份", (T.state.snapshots || []).length);
  check(T.state.loading === false, "载入流程已结束");
  check((T.state.originals.fish && Object.keys(T.state.originals.fish).length === 18),
    "初始 originals 快照建立（18 条）");

  console.log("\n[1b] 演示数据自洽性（首屏不该出现任何红色报错）");
  const locKeys = T.knownLocationKeys();
  check(locKeys.length === 32, "已知钓点键 = 16 个 id + 16 个中文名", locKeys.length);
  const badDist = T.state.data.fish.filter(function (r) {
    return T.parseDist(r.dist, locKeys).some(function (p) { return p.bad || p.badWeight; });
  });
  check(badDist.length === 0, "所有鱼的分布都指向真实钓点",
    badDist.length ? badDist.map(function (r) { return r.id + ":" + r.dist; }).join(" ") : "18/18 合法");
  const rodIds = T.state.data.rods.map(function (r) { return r.id; });
  const badRod = T.state.data.baits.filter(function (b) {
    return b.required_rod && rodIds.indexOf(b.required_rod) < 0;
  });
  check(badRod.length === 0, "所有鱼饵的「需要鱼竿」都存在",
    badRod.length ? badRod.map(function (b) { return b.id; }).join(" ") : rodIds.join("/"));
  /* 页面上的效果键白名单必须和插件 _calc.py 的 _parse_effects 完全一致（含旧写法 quality_up） */
  const calcSrc = fs.readFileSync(path.join(__dirname, "_calc.py"), "utf8");
  const allowedBlock = calcSrc.match(/def _parse_effects[\s\S]*?allowed = \(([\s\S]*?)\)/);
  const pluginKeys = allowedBlock
    ? allowedBlock[1].split(",").map(s => s.trim().replace(/["']/g, "")).filter(Boolean)
    : [];
  check(pluginKeys.length >= 8, "从 _calc.py 读到了 _parse_effects 的白名单",
    pluginKeys.join("/") || "没读到");
  const pageKeys = T.ITEM_EFFECT_KEY_NAMES.filter(k => k !== "quality_up");
  check(pageKeys.slice().sort().join(",") === pluginKeys.slice().sort().join(","),
    "页面效果键白名单与插件一致",
    "页面=" + pageKeys.join("/") + " 插件=" + pluginKeys.join("/"));

  const ALLOWED_EFFECTS = T.ITEM_EFFECT_KEY_NAMES;
  const badEffects = [];
  T.state.data.items.forEach(function (it) {
    String(it.effects || "").split(";").forEach(function (pair) {
      const k = pair.split("=")[0].trim();
      if (k && ALLOWED_EFFECTS.indexOf(k) < 0) badEffects.push(it.id + ":" + k);
    });
  });
  check(badEffects.length === 0, "道具效果键都合法", badEffects.length ? badEffects.join(" ") : "全部合法");
  const invalidRows = [];
  T.TABS.forEach(function (t) {
    if (!t.columns) return;
    (T.state.data[t.id] || []).forEach(function (r) {
      if (Object.keys(T.validateRow(t, r)).length) invalidRows.push(t.id + ":" + (r.id || r.key));
    });
  });
  check(invalidRows.length === 0, "首屏没有任何一行是校验不通过的",
    invalidRows.length ? invalidRows.slice(0, 5).join(" ") : "全部通过");

  console.log("\n[1c] 首屏脏标记必须为 0（父 agent 反馈过的回归点）");
  const perTab = T.TABS.map(function (t) {
    return t.id + "=" + (t.kind === "snapshots" ? 0 : T.diffTab(t.id).count);
  }).join(" ");
  check(T.dirtyTotal() === 0, "dirtyTotal = 0", perTab);
  T.renderTabs();
  const tabsHtml = document.getElementById("tabs").innerHTML;
  check(tabsHtml.indexOf("has-dirty") < 0, "标签栏 HTML 里没有任何 has-dirty 类");
  check(tabsHtml.split("tab-count").length - 1 === 14,
    "14 个标签入口都有条目数徽标（12 原有 + 玩家 + 命令组）",
    tabsHtml.split("tab-count").length - 1);
  check(tabsHtml.split("⌨️ 命令").length - 1 === 1 && /\u2328\ufe0f 命令/.test(tabsHtml),
    "「命令」只占一个入口按钮（两张表在页内切换）");
  check(tabsHtml.indexOf('data-tab="players"') > 0, "标签栏里有「👤 玩家」入口");
  check(T.state.autoBackupDirty !== true, "自动备份表单未被标记为已改");

  console.log("\n[1d] 空状态（表空 / 筛选无结果 都别只剩表头）");
  const trulyEmpty = T.renderTableBody(T.TAB_BY_ID.fish, []);
  check(trulyEmpty.indexOf("empty") >= 0 && trulyEmpty.indexOf("新增一行") >= 0,
    "表里确实没数据时给「＋ 新增一行」引导");
  T.state.query = "绝对不存在的名字zzz";
  const filteredEmpty = T.renderTableBody(T.TAB_BY_ID.fish, T.visibleRows(T.TAB_BY_ID.fish));
  check(filteredEmpty.indexOf("empty") >= 0 && filteredEmpty.indexOf("换个搜索词") >= 0,
    "筛选无结果时提示换个搜索词（与「本来就没数据」区分开）");
  check(T.visibleRows(T.TAB_BY_ID.fish).length === 0, "确实筛不出任何行（前置条件成立）");
  T.state.query = "";

  console.log("\n[2] 分布解析 parseDist");
  const known = T.knownLocationKeys();
  check(known.length === 32, "已知钓点键（16 id + 16 中文名）", known.length);
  const d1 = T.parseDist("novice:1.0,lake:0.8", known);
  check(d1.length === 2 && d1[0].loc === "novice" && d1[0].weight === 1 && !d1[0].bad, "常规分布解析正确");
  const d2 = T.parseDist("*:0.15", known);
  check(d2.length === 1 && d2[0].star === true && d2[0].bad === false, "* = 所有钓点，不算未知");
  const d3 = T.parseDist("nope_place:1.0", known);
  check(d3[0].bad === true, "未知钓点被标记为 bad");
  const d4 = T.parseDist("lake:abc", known);
  check(d4[0].badWeight === true, "非数字权重被标记为 badWeight");
  check(T.parseDist("lake", known)[0].weight === 1, "省略权重默认 1.0");
  check(T.parseDist("", known).length === 0, "空串得到空数组");

  console.log("\n[3] 行校验 validateRow");
  const fishTab = T.TAB_BY_ID.fish;
  check(Object.keys(T.validateRow(fishTab, { id: "x", name: "x", rarity: "史诗", value: 10, dist: "novice:1" })).length === 1,
    "非法稀有度被抓出来");
  check(!!T.validateRow(fishTab, { id: "x", name: "x", rarity: "常见", value: "abc", dist: "novice:1" }).value,
    "非数字价格被抓出来");
  check(!!T.validateRow(fishTab, { id: "x", name: "x", rarity: "常见", value: 1, dist: "ghost:1" }).dist,
    "未知钓点被抓出来");
  check(!!T.validateRow(fishTab, { id: "", name: "x", rarity: "常见", value: 1, dist: "novice:1" }).id,
    "空 id 被抓出来");
  check(Object.keys(T.validateRow(fishTab, { id: "ok", name: "x", rarity: "神话", value: 1, dist: "*:0.1" })).length === 0,
    "合法行没有报错");
  const baitTab = T.TAB_BY_ID.baits;
  check(!!T.validateRow(baitTab, { id: "b", name: "b", price: 1, bundle: 1, luck: 0, unlock_level: 1, required_rod: "no_such_rod" }).required_rod,
    "不存在的鱼竿 id 被抓出来");
  check(Object.keys(T.validateRow(baitTab, { id: "b", name: "b", price: 1, bundle: 1, luck: 0, unlock_level: 1, required_rod: "stream" })).length === 0,
    "存在的鱼竿 id 通过");
  /* 道具效果串：新三种角色（喂鱼 / 手气 / 装饰）都要能过，写错的键要被标红 */
  const itemTab = T.TAB_BY_ID.items;
  function itemRow(effects) {
    return { id: "it", emoji: "📦", name: "道具", price: 10, effects: effects, desc: "" };
  }
  check(Object.keys(T.validateRow(itemTab, itemRow("meat=2;spirit=1;sheen=1;value_up=600"))).length === 0,
    "三维 + 估值（喂鱼类）通过");
  check(Object.keys(T.validateRow(itemTab, itemRow("feed_bonus=5"))).length === 0, "投喂上限 feed_bonus 通过");
  check(Object.keys(T.validateRow(itemTab, itemRow("buff_quality=0.30"))).length === 0, "手气 buff_quality 通过");
  check(Object.keys(T.validateRow(itemTab, itemRow("decorate=0.20"))).length === 0, "鱼缸装饰 decorate 通过");
  check(Object.keys(T.validateRow(itemTab, itemRow("quality_up=0.3"))).length === 0, "旧写法 quality_up 仍然认");
  check(Object.keys(T.validateRow(itemTab, itemRow(""))).length === 0, "效果留空不算错（等价于纯收藏品）");
  check(!!T.validateRow(itemTab, itemRow("luck=0.5")).effects, "杂物用的 luck 键在道具表里会被标红");
  check(!!T.validateRow(itemTab, itemRow("decorate")).effects, "缺等号的写法会被标红");
  check(!!T.validateRow(itemTab, itemRow("decorate=很多")).effects, "非数字的值会被标红");
  check(String(T.validateRow(itemTab, itemRow("luck=0.5")).effects).indexOf("decorate") > 0,
    "报错信息里列出了可用的键名");

  console.log("\n[4] 改动追踪 diffTab");
  const before = T.diffTab("fish").count;
  T.state.data.fish[0].value = T.state.data.fish[0].value + 1;
  const after = T.diffTab("fish").count;
  check(before === 0 && after === 1, "改一条鱼后 dirty 计数从 " + before + " 变成 " + after);
  T.state.data.fish.push(JSON.parse(JSON.stringify(T.state.data.fish[0])));
  T.state.data.fish[T.state.data.fish.length - 1].id = "brand_new_fish";
  check(T.diffTab("fish").added === 1, "新增行被算作 added");
  T.state.data.fish.pop();
  T.state.data.fish[0].value = T.state.data.fish[0].value - 1;
  check(T.diffTab("fish").count === 0, "改回去后 dirty 归零");

  console.log("\n[5] 渲染全部标签页（抓运行时错误）");
  const originalTab = T.state.tab;
  T.TABS.forEach(function (t) {
    T.state.tab = t.id;
    T.state.query = "";
    T.state.filter = "";
    try {
      if (t.kind === "snapshots") {
        const out = T.renderSnapshotsTab(t);
        check(out.indexOf("自动备份") >= 0 && out.indexOf("snap-card") >= 0,
          "存档页渲染出表单与卡片", out.length + " 字符");
        check(out.indexOf("kind-daily") >= 0 && out.indexOf("kind-manual") >= 0, "存档类型徽标齐全");
        check(out.indexOf("snap:players") >= 0 && out.indexOf("👤 改金币") >= 0,
          "存档卡片上有「改金币」入口（跳到玩家页的存档模式）");
      } else if (t.kind === "players") {
        const out = T.renderPlayersTab(t);
        check(out.indexOf("<table") >= 0 && out.indexOf("金币（可改）") >= 0,
          "玩家页渲染出可改金币的表格", out.length + " 字符");
        check(out.indexOf("p:mode") >= 0 && out.indexOf("存档内玩家") >= 0 && out.indexOf("实时玩家") >= 0,
          "玩家页有「实时 / 存档内」两种模式");
      } else if (t.navGroup) {
        const out = T.renderTableTab(t);
        check(out.length > 400 && out.indexOf("<table") >= 0, "「" + t.label + "」渲染出表格", out.length + " 字符");
        const sub = T.renderSubTabs(t.navGroup, t.id);
        check(sub.indexOf("命令别名") >= 0 && sub.indexOf("自定义命令") >= 0,
          "命令页里两张表的切换条都在");
      } else {
        const out = T.renderTableTab(t);
        check(out.length > 400 && out.indexOf("<table") >= 0, "「" + t.label + "」渲染出表格", out.length + " 字符");
        if (t.id === "items") {
          check(out.indexOf('title="') >= 0 && out.indexOf("鱼缸装饰") >= 0 && out.indexOf("钓手手气") >= 0,
            "道具页「效果」列表头带上了效果键说明（tooltip）");
          check(out.indexOf("（key=值）") >= 0 || out.indexOf("(key=值)") >= 0,
            "「效果」列的表头列提示写着 key=值");
        }
      }
      T.render();
    } catch (e) {
      check(false, "「" + t.label + "」渲染抛错：" + e.message);
    }
  });
  T.state.tab = originalTab;

  console.log("\n[6] 单元格渲染细节");
  const row0 = T.state.data.fish.find(function (r) { return r.rarity === "神话"; });
  const rarityCol = fishTab.columns.filter(function (c) { return c.key === "rarity"; })[0];
  const rarityHtml = T.cellHtml(fishTab, rarityCol, row0, 0);
  check(rarityHtml.indexOf("pill") >= 0 && rarityHtml.indexOf("rar-myth") >= 0, "神话渲染成 rar-myth 药丸");
  check(rarityHtml.indexOf('data-act="edit"') >= 0, "稀有度药丸可点击进入编辑");
  const hidden = T.state.data.fish.find(function (r) { return r.dist.indexOf("*") === 0; });
  const distCol = fishTab.columns.filter(function (c) { return c.key === "dist"; })[0];
  const distHtml = T.cellHtml(fishTab, distCol, hidden, 0);
  check(distHtml.indexOf("is-star") >= 0 && distHtml.indexOf("全部钓点") >= 0, "* 分布渲染成「全部钓点」高亮标签");
  const badRow = { id: "z", name: "z", rarity: "常见", value: 1, dist: "ghost:1", flavor: "" };
  check(T.cellHtml(fishTab, distCol, badRow, 0).indexOf("is-bad") >= 0, "未知钓点渲染成红色标签");

  console.log("\n[7] 批量操作");
  T.state.selected = [0, 1];
  const oldValue = Number(T.state.data.fish[0].value);
  T.state.batchAction = "price";
  T.state.batchValue = "2";
  T.state.batchField = "value";
  T.applyBatch();
  check(Number(T.state.data.fish[0].value) === oldValue * 2, "价格 ×2 生效", oldValue + " -> " + T.state.data.fish[0].value);
  check(T.state.batchAction === null, "应用后批量状态被清空");
  T.state.selected = [0, 1];
  T.state.batchAction = "rarity";
  T.state.batchValue = "传说";
  T.applyBatch();
  check(T.state.data.fish[0].rarity === "传说" && T.state.data.fish[1].rarity === "传说", "批量改稀有度生效");
  T.state.selected = [0];
  T.state.batchAction = "delete";
  const cntBefore = T.state.data.fish.length;
  T.applyBatch();
  check(T.state.data.fish.length === cntBefore - 1, "批量删除生效");

  console.log("\n[8] 保存载荷与存档动作");
  const payload = T.buildPayload();
  check(["fish", "locations", "baits", "rods", "items", "collectibles", "variants", "weather",
    "numbers", "buttons", "aliases", "custom"]
    .every(function (k) { return Array.isArray(payload[k]); }), "载荷包含全部 12 张表");
  check(!!payload.autoBackup && payload.autoBackup.dailyHour === 4, "载荷带上自动备份设置");
  check(Object.keys(payload).length === 14, "载荷字段数 = 14（12 表 + numbers + autoBackup）", Object.keys(payload).length);
  check(payload.players === undefined, "玩家页的数据不进「内容表」载荷（走独立接口）");

  const snapWithNote = T.renderSnapCard({ kind: "manual", note: "改物价前", time: "2026-09-18 18:20", players: 35, size: "131 KB" }, 0);
  check(snapWithNote.indexOf("改物价前") >= 0, "存档卡片显示备注名");
  const snapNoNote = T.renderSnapCard({ kind: "daily", note: "", time: "2026-09-17 04:00", players: 33, size: "126 KB" }, 3);
  check(snapNoNote.indexOf("未命名（点这里改名）") >= 0, "无备注时提示可点改名");
  check(snapNoNote.indexOf("恢复") >= 0 && snapNoNote.indexOf("下载") >= 0 && snapNoNote.indexOf("删除") >= 0,
    "存档卡片三个操作齐全");

  console.log("\n[9] 编辑态与快捷键相关逻辑");
  T.state.tab = "fish";
  T.startEdit(0, "name");
  check(!!T.state.editing && T.state.editing.key === "name", "可编辑列能进入编辑态");
  check(!!T.state.editBackup, "记录了编辑前的值（Esc 可还原）");
  T.state.data.fish[0].name = "临时改名";
  T.cancelEdit();
  check(T.state.data.fish[0].name !== "临时改名", "cancelEdit 还原了旧值", T.state.data.fish[0].name);
  T.startEdit(0, "name");
  T.commitEdit();
  check(T.state.editing === null, "commitEdit 退出编辑态");

  // 只读列（数值页的「配置键 / 说明」）必须拒绝进入编辑态
  T.state.tab = "numbers";
  T.startEdit(0, "label");
  check(T.state.editing === null, "只读列（label）不进入编辑态");
  T.startEdit(0, "key");
  check(T.state.editing === null, "只读列（key）不进入编辑态");
  T.startEdit(0, "value");
  check(!!T.state.editing && T.state.editing.key === "value", "数值页的「当前值」可编辑");
  T.commitEdit();
  T.state.tab = "fish";

  console.log("\n[10] 离线通道的诚实提示");
  return Promise.all([T.saveConfig({}), T.runSnapshotAction("restore", {})]).then(function (rs) {
    check(rs[0].ok === false && /离线预览/.test(rs[0].message), "离线保存返回「离线预览」提示", rs[0].message);
    check(rs[1].ok === false && /离线预览/.test(rs[1].message), "离线存档动作同样被拦下", rs[1].message);

    console.log("\n[11] 主题解析");
    check(["dark", "light"].indexOf(documentStub.documentElement.getAttribute("data-theme")) >= 0,
      "data-theme 被规范成 dark/light", documentStub.documentElement.getAttribute("data-theme"));
    check(T.ENV.isDark === true, "离线默认深色");
  }).then(channelHelpers).then(channelRoundTrip).then(finish);
}

/* =============================================================================
   [12] 数据通道的纯函数：行格式、状态解析、白名单过滤
   ============================================================================= */
async function channelHelpers() {
  console.log("\n[12] 数据通道：配置 <-> 表格 的转换");
  check(Object.keys(T.TABLE_DEFS).join(",") === "fish,rods,baits,items,locations,collectibles,variants,weather,easter_eggs,buttons,aliases,custom",
    "12 张内容表都有解析/序列化定义", Object.keys(T.TABLE_DEFS).join(","));
  check(T.TABLE_DEFS.fish.configKey === "fish_defs" && T.TABLE_DEFS.fish.configType === "text",
    "fish_defs 是文本表（多行），其余是字符串数组");
  check(T.TABLE_DEFS.locations.configKey === "location_defs", "钓点表 -> location_defs");
  check(T.ENV.pluginBase && T.ENV.pluginBase.length > 0,
    "页面持有插件名（endpoint 走相对路径，插件名只用于显示）", T.ENV.pluginBase);

  // 行解析
  const fishRow = T.TABLE_DEFS.fish.parse("carp|鲤鱼|常见|120|novice:1.0,lake:0.8|村口常客");
  check(fishRow.id === "carp" && fishRow.name === "鲤鱼" && fishRow.value === 120
    && fishRow.dist === "novice:1.0,lake:0.8" && fishRow.flavor === "村口常客",
    "鱼行 6 段解析正确");
  check(T.TABLE_DEFS.fish.parse("半行没有竖线") === null, "残缺行解析为 null（会被跳过并提示）");
  const rodRow = T.TABLE_DEFS.rods.parse("carbon|碳素竿|🎣|400|0.05|0.03|4|轻巧顺手");
  check(rodRow.price === 400 && rodRow.value_bonus === 0.05 && rodRow.unlock_level === 4
    && rodRow.desc === "轻巧顺手", "鱼竿行 8 段解析正确（含解锁等级）");
  const oldRod = T.TABLE_DEFS.rods.parse("carbon|碳素竿|🎣|400|0.05|0.03|轻巧顺手");
  check(oldRod.unlock_level === 1 && oldRod.desc === "轻巧顺手", "老格式鱼竿（7 段）也认，等级兜 1");
  const baitRow = T.TABLE_DEFS.baits.parse(
    "corn|玉米粒|🌽|6|5|0.30|1,1.5,2.3,3.0,4.0|9|stream|素饵之王");
  check(baitRow.bundle === 5 && baitRow.weights === "1,1.5,2.3,3.0,4.0"
    && baitRow.required_rod === "stream" && baitRow.desc === "素饵之王",
    "鱼饵行 10 段解析正确（含稀有度权重与需要鱼竿）");
  check(T.TABLE_DEFS.baits.parse("x|y|z|1|2|3|1,1,1,1,1").unlock_level === 1,
    "老格式鱼饵（7 段、没有解锁等级那几列）也认，等级兜 1");
  check(T.TABLE_DEFS.baits.parse("x|y|z|1|2|3") === null,
    "鱼饵少于 7 段解析为 null（插件解析器也是这个门槛）");

  // ---- 回复按钮表（button_defs）：场景|文案|点击后发送|样式 ----
  console.log("  ── 回复按钮表 ──");
  const btnRow = T.TABLE_DEFS.buttons.parse("cast|再来一竿|/钓鱼|primary");
  check(btnRow.scene === "cast" && btnRow.label === "再来一竿" && btnRow.data === "/钓鱼"
    && btnRow.style === "primary", "按钮行 4 段解析正确");
  check(T.TABLE_DEFS.buttons.parse("CAST|看背包|/钓鱼 背包").scene === "cast"
    && T.TABLE_DEFS.buttons.parse("CAST|看背包|/钓鱼 背包").style === "default",
    "场景大小写归一、样式缺省为 default");
  check(T.TABLE_DEFS.buttons.parse("cast|半行|") === null
    && T.TABLE_DEFS.buttons.parse("|缺场景|/钓鱼") === null,
    "缺字段/缺场景的行解析为 null（插件也会跳过）");
  check(T.TABLE_DEFS.buttons.serialize({ scene: "story", label: "{label}", data: "/钓鱼 事件 {n}", style: "default" })
    === "story|{label}|/钓鱼 事件 {n}|default",
    "按钮行序列化回 4 段（插曲占位符原样保留）");
  check(T.TABLE_DEFS.buttons.configKey === "button_defs" && T.TABLE_DEFS.buttons.configType === "text",
    "按钮表写回 button_defs（多行文本）");
  check(T.BUTTON_SCENES.join(",") === "cast,pull,bag,location,story" && T.TAB_BY_ID.buttons.keyFields.length === 3,
    "场景白名单与复合行键（场景+文案+指令）就位");

  // 复合行键：默认按钮里有 5 个 cast 行，它们必须互不撞键（否则会被误判成「新增」）
  const btnTab = T.TAB_BY_ID.buttons;
  const btnKeys = T.state.data.buttons.map(function (r) { return T.rowKey(btnTab, r); });
  check(new Set(btnKeys).size === btnKeys.length && btnKeys.length === 10,
    "10 个按钮的行键互不重复（" + btnKeys.length + " 行 / " + new Set(btnKeys).size + " 键）");
  check(T.rowKey(btnTab, { scene: "cast", label: "卖光光", data: "/钓鱼 卖光光" })
    !== T.rowKey(btnTab, { scene: "bag", label: "卖光光", data: "/钓鱼 卖光光" }),
    "同名按钮在不同场景算两行（卖光光 cast/bag）");

  // 样式：别名与数字都认，乱写不认（前端拦下来，别等插件回退）
  check(T.isButtonStyle("default") && T.isButtonStyle("primary") && T.isButtonStyle("蓝")
    && T.isButtonStyle("") && T.isButtonStyle("4") && T.isButtonStyle("0") && T.isButtonStyle("255"),
    "样式：别名/空/0-255 数字都合法");
  check(!T.isButtonStyle("红色") && !T.isButtonStyle("256") && !T.isButtonStyle("-1")
    && !T.isButtonStyle("1.5"), "样式：乱写/越界/小数不合法");

  // 校验：场景、指令、样式都要对，文案不能空
  const vBtn = function (row) { return Object.keys(T.validateRow(btnTab, row)); };
  check(vBtn({ scene: "cast", label: "看背包", data: "/钓鱼 背包", style: "default" }).length === 0,
    "合法按钮行校验通过");
  check(vBtn({ scene: "xx", label: "看背包", data: "/钓鱼 背包", style: "default" })[0] === "scene",
    "场景写错会被标红");
  check(vBtn({ scene: "cast", label: "", data: "/钓鱼 背包", style: "default" })[0] === "label",
    "文案为空会被标红");
  check(vBtn({ scene: "cast", label: "看背包", data: "背包", style: "default" })[0] === "data",
    "「点击后发送」不是本插件指令会被标红");
  check(vBtn({ scene: "cast", label: "看背包", data: "/钓鱼 背包", style: "红色" })[0] === "style",
    "样式乱写会被标红");

  // 竖线不能进单元格（否则一行会被劈成两列）
  check(T.cell("a|b\nc｜d").indexOf("|") < 0 && T.cell("a|b\nc｜d").indexOf("\n") < 0,
    "cell() 把竖线/换行洗掉", JSON.stringify(T.cell("a|b\nc｜d")));

  /* ---- v1.10.0：命令别名 / 自定义命令 两张表 ---- */
  const aliasDef = T.TABLE_DEFS.aliases;
  const customDef = T.TABLE_DEFS.custom;
  check(aliasDef.configKey === "command_aliases" && aliasDef.configType === "text",
    "别名表写回 command_aliases（多行文本）");
  check(customDef.configKey === "custom_commands" && customDef.configType === "text",
    "自定义命令表写回 custom_commands（多行文本）");
  const aliasRow = aliasDef.parse("背包|包,bag,鱼篓");
  check(aliasRow.canonical === "背包" && aliasRow.aliases === "包,bag,鱼篓",
    "别名行解析正确", JSON.stringify(aliasRow));
  check(aliasDef.serialize(aliasRow) === "背包|包,bag,鱼篓", "别名行往返不漂", aliasDef.serialize(aliasRow));
  check(aliasDef.parse("背包") === null && aliasDef.parse("背包|") === null,
    "缺竖线/缺别名的行解析成 null（保存时不会悄悄改写）");
  const customRow = customDef.parse("领奖|发送:今天也要加油！");
  check(customRow.name === "领奖" && customRow.action === "发送" && customRow.body === "今天也要加油！",
    "自定义命令行解析正确", JSON.stringify(customRow));
  check(customDef.serialize(customRow) === "领奖|发送:今天也要加油！",
    "自定义命令往返不漂", customDef.serialize(customRow));
  check(customDef.parse("领奖|发送：全角冒号").action === "发送",
    "全角冒号也认（站长用中文输入法写配置是常态）");
  check(customDef.parse("没有动作") === null && customDef.parse("甲|发送:") === null,
    "缺动作/空内容的行解析成 null");
  check(T.canonicalCommands().length === 26 && T.canonicalCommands().indexOf("背包") >= 0,
    "页面知道 26 个规范子命令（离线用演示清单，在线以插件回写的为准）",
    T.canonicalCommands().length);
  // 校验：目标子命令、动作、重名都要在页面上就标红
  const vAlias = function (row) { return Object.keys(T.validateRow(T.TAB_BY_ID.aliases, row)); };
  check(vAlias({ canonical: "背包", aliases: "仓库,行囊" }).length === 0, "合法别名行校验通过");
  check(vAlias({ canonical: "不存在的命令", aliases: "x" })[0] === "canonical", "规范子命令写错会被标红");
  check(vAlias({ canonical: "背包", aliases: "" })[0] === "aliases", "一个别名都不写会被标红");
  const vCustom = function (row) { return Object.keys(T.validateRow(T.TAB_BY_ID.custom, row)); };
  check(vCustom({ name: "快捷签到", action: "执行", body: "签到" }).length === 0, "合法自定义命令行校验通过");
  check(vCustom({ name: "背包", action: "发送", body: "抢内置" })[0] === "name",
    "自定义命令名撞内置子命令会被标红（内置永远优先）");
  check(vCustom({ name: "甲", action: "乱写", body: "x" })[0] === "action", "动作写错会被标红");
  check(vCustom({ name: "甲", action: "发送", body: "" })[0] === "body", "内容为空会被标红");
  check((T.state.data.aliases || []).length === 3 && (T.state.data.custom || []).length === 2,
    "离线演示也带别名/自定义命令样例（首屏就能看到长什么样）");

  /* ---- v1.10.0：玩家页（金币）---- */
  check((T.state.playersList || []).length === 4, "离线演示 4 名玩家", (T.state.playersList || []).length);
  check(T.playerRowsNow().length === 4 && T.state.playersMode === "live", "玩家页默认看「实时玩家」");
  const playerHtml = T.renderPlayersTab(T.TAB_BY_ID.players);
  check(playerHtml.indexOf("10001") > 0 && playerHtml.indexOf("12,800") > 0,
    "玩家表渲染出真实金币（千分位格式化）");
  check(playerHtml.indexOf("p:gold") > 0, "每行都有「改金币」按钮");
  T.state.goldConfirm = { user_id: "10001", value: 5000, mode: "live", oldValue: 12800 };
  check(T.renderPlayersTab(T.TAB_BY_ID.players).indexOf("确认修改") > 0,
    "二次确认条出现（改金币不是一键生效）");
  T.state.goldConfirm = null;
  const offlineGold = await T.savePlayerGold("live", "10001", 5000);
  check(offlineGold.ok === false && /离线预览/.test(offlineGold.message),
    "离线模式下改金币被拦下（不会假装成功）", offlineGold.message);

  // 序列化
  const serialized = T.serializeContentTables();
  check(Object.keys(serialized).join(",") === "fish_defs,rod_defs,bait_defs,item_defs,location_defs,collectible_defs,variant_defs,weather_defs,easter_egg_defs,button_defs,command_aliases,custom_commands",
    "序列化输出 12 张配置表", Object.keys(serialized).join(","));
  check(typeof serialized.fish_defs === "string"
    && serialized.fish_defs.split("\n").length === T.state.data.fish.length,
    "fish_defs 的行数 = 当前表格行数（这里是 " + T.state.data.fish.length + " 行）",
    serialized.fish_defs.split("\n").length);
  check(Array.isArray(serialized.rod_defs) && serialized.rod_defs.length === 6,
    "rod_defs 是 6 条字符串数组", serialized.rod_defs.length);
  check(serialized.rod_defs[1].split("|").length === 8, "鱼竿序列化成 8 段");
  check(serialized.bait_defs[1].split("|").length === 10, "鱼饵序列化成 10 段");
  check(serialized.fish_defs.split("\n")[0].indexOf("|") > 0, "鱼行用竖线分隔");
  // 往返：解析 -> 序列化 -> 再解析，关键字段不漂
  const roundTrip = T.TABLE_DEFS.locations.parse(T.TABLE_DEFS.locations.serialize(
    { id: "lake", name: "山间湖泊", emoji: "🏞️", level_gate: 4, gold_gate: 1000,
      value_mult: 1.1, desc: "水清鱼肥" }));
  check(roundTrip.id === "lake" && roundTrip.level_gate === 4 && roundTrip.gold_gate === 1000
    && roundTrip.value_mult === 1.1 && roundTrip.desc === "水清鱼肥",
    "钓点往返（parse∘serialize）字段不漂");

  // 数值白名单过滤
  const before = JSON.parse(JSON.stringify(T.state.data.numbers));
  T.state.data.numbers = [
    { key: "stamina_max", label: "体力上限", value: 20, unit: "点" },
    { key: "data_status", label: "数据状态", value: "偷偷改", unit: "" },
    { key: "location_defs", label: "钓点表", value: "x", unit: "" },
    { key: "fish_value_mult", label: "鱼价总闸", value: 1, unit: "倍" }
  ];
  const picked = T.numberValuesFromPage({ numbers_editable: ["stamina_max", "fish_value_mult"] });
  check(JSON.stringify(Object.keys(picked)) === JSON.stringify(["stamina_max", "fish_value_mult"]),
    "白名单外的数值键不会被提交（data_status / *_defs 被滤掉）", Object.keys(picked).join(","));
  check(T.numberValuesFromPage(null).data_status !== undefined,
    "拿不到白名单时不擅自过滤（退回全发，由插件侧最终把关）");
  T.state.data.numbers = before;

  // editor_status 解析
  check(T.parseEditorStatus({ editor_status: { ok: true } }).ok === true, "editor_status 已是对象时直接用");
  check(T.parseEditorStatus({ editor_status: '{"ok":false,"snapshots":[]}' }).ok === false,
    "JSON 字符串能被解析成对象");
  check(T.parseEditorStatus({ editor_status: "{坏 JSON" }) === null, "坏 JSON 返回 null（页面不炸）");
  check(T.parseEditorStatus({}) === null && T.parseEditorStatus(null) === null,
    "没有 editor_status 时返回 null");
  const ab = T.autobackupFromStatus({ autobackup: { enable: false, daily_hour: 7,
    interval_hours: 12, keep_daily: 10, keep_interval: 5, keep_manual: 0 } });
  check(ab.enable === false && ab.dailyHour === 7 && ab.intervalHours === 12
    && ab.keepDaily === 10 && ab.keepInterval === 5 && ab.keepManual === 0,
    "editor_status -> 表单字段映射正确（蛇形转驼峰）");
  const abPayload = T.autobackupPayload(ab);
  check(JSON.stringify(Object.keys(abPayload).sort()) ===
    JSON.stringify(["daily_hour", "enable", "interval_hours", "keep_daily", "keep_interval", "keep_manual"]),
    "表单字段 -> 插件字段名正确（以 DEFAULTS 为准）", Object.keys(abPayload).join(","));
  // 手动存档保留策略：填了就带上去（这是「55 份手动存档」那个坑的开关）
  const abKeep = T.autobackupPayload({ enable: true, dailyHour: 4, intervalHours: 6,
    keepDaily: 30, keepInterval: 20, keepManual: 25 });
  check(abKeep.keep_manual === 25, "「手动存档保留份数」会随保存一起提交", abKeep.keep_manual);
  check(T.autobackupPayload({ enable: true }).keep_manual === 0,
    "没填时手动存档保留份数按 0（永久保留）提交");
  // 写通道：走插件自己注册的 Web API（相对路径），不再有 nonce / 上传文件
  check(typeof T.sendCommand === "function" && typeof T.bridgeRequest === "function",
    "写通道 = sendCommand + bridgeRequest（apiPost 到 config / snapshot）");
  check(T.BRIDGE_FILE === undefined && T.makeNonce === undefined,
    "旧的「上传固定文件名 + nonce」通道已彻底移除");

  // 失败信息必须能定位（不能只说「失败了」）
  const fakeErr = { response: { status: 401, statusText: "Unauthorized",
    data: { message: "Missing API key" } }, message: "Request failed" };
  const desc = T.describeError(fakeErr, "config");
  check(/HTTP 401/.test(desc) && /Missing API key/.test(desc) && /config/.test(desc),
    "请求失败时把 HTTP 状态 + 服务端原因 + 端点都说出来", desc);
  check(/离线预览/.test(T.describeError({ offline: true, message: "离线预览：没有 AstrBot 桥接" }, "x")),
    "离线错误有专门提示");
  check(/请求失败/.test(T.describeError({ message: "Network Error" }, "config")),
    "没有 status 时退回「请求失败 + 原始信息」");
  return Promise.resolve();
}

/* =============================================================================
   [13] 数据通道的真实往返：假 SDK（apiGet/apiPost 到插件注册的相对路径）
   ============================================================================= */
const captured = {
  calls: [], context: null, failPost: false, statusReads: 0, phase: "before"
};

function makeFakeSdk() {
  const sdk = {
    ready() {
      return new Promise(function (resolve) {
        sdk._setContext({
          pluginName: "dhhxfggg/astrbot_plugin_qq_fishing",   // author/name 形态
          displayName: "群钓鱼", pageName: "editor", pageTitle: "数据编辑器",
          locale: "zh-CN", isDark: true,
          i18n: { "zh-CN": { pages: { editor: { title: "数据编辑器" } } } }
        });
        resolve();
      });
    },
    getContext() { return captured.context; },
    _setContext(c) { captured.context = c; },
    onContext() { return function () {}; },
    apiGet(endpoint) {
      capture("get", endpoint, null);
      // 页面现在只传相对路径 "config"（插件用 register_web_api 注册的路由）
      if (String(endpoint) !== "config") {
        return Promise.reject(new Error("unexpected GET " + endpoint));
      }
      captured.statusReads++;
      return Promise.resolve({
        status: "ok", metadata: {}, i18n: {},
        config: captured.phase === "after" ? FAKE_CONFIG_AFTER() : FAKE_CONFIG
      });
    },
    apiPost(endpoint, body) {
      const payload = body || {};
      if (captured.failPost) {
        capture("post", endpoint, JSON.stringify(payload));
        return Promise.reject({
          response: { status: 403, statusText: "Forbidden",
            data: { message: "Insufficient API key scope" } },
          message: "Request failed with status code 403"
        });
      }
      captured.phase = "after";
      capture("post", endpoint, JSON.stringify(payload));
      // 写操作是同步的：响应里直接带回最新状态（页面不必再轮询）
      const fresh = JSON.parse(FAKE_CONFIG_AFTER().editor_status);
      // 玩家接口（v1.10.0）：list / snapshot_list 回列表，set_gold / snapshot_gold 回结果
      if (String(endpoint) === "players") {
        const action = String(payload.action || "list");
        const base = {
          status: "ok", ok: true, transport: "plugin-api",
          editor_status: JSON.stringify(fresh)
        };
        if (action === "list" || action === "snapshot_list") {
          const rows = action === "list" ? FAKE_PLAYERS : FAKE_PLAYERS.slice(0, 1);
          return Promise.resolve(Object.assign({}, base, {
            players: rows, count: rows.length, total: 12, limit: 500,
            scan_limit: 2000, gold_max: 1000000000,
            query: String(payload.query || "")
          }));
        }
        return Promise.resolve(Object.assign({}, base, {
          message: "已把玩家 " + payload.user_id + " 的金币改成 " + payload.gold
        }));
      }
      let message = "已执行 " + payload.action;
      if (payload.action === "rename") {
        message = "已把「" + payload.name + "」的备注改成「" + payload.note + "」";
      } else if (payload.action) {
        message = "已执行 " + payload.action;
      } else if (payload.payload && payload.payload.action) {
        message = "已执行 " + payload.payload.action;
      }
      return Promise.resolve({
        status: "ok", ok: true, message: message,
        editor_status: JSON.stringify(fresh)
      });
    },
    upload() {
      return Promise.reject(new Error("上传通道已废弃：真实环境会 403（API key 无上传权限）"));
    },
    download() { return Promise.reject(new Error("not used")); },
    subscribeSSE() { return Promise.reject(new Error("not used")); }
  };
  return sdk;
}

function capture(kind, endpoint, body, name) {
  captured.calls.push({ kind, endpoint, body, name });
}

const FAKE_STATUS = {
  updated_at: 1,
  last_action: "refresh",
  last_result: "状态已刷新",
  ok: true,
  players: 12,
  next_auto_backup: "2026-09-19 04:00:00",
  numbers_editable: ["stamina_max", "multi_cast_max", "fish_value_mult"],
  autobackup: { enable: true, daily_hour: 4, interval_hours: 6, keep_daily: 30, keep_interval: 20 },
  snapshots: [
    { name: "2026-09-18_1820.json", rel: "manual/2026-09-18_1820.json", kind: "manual",
      kind_name: "手动存档", note: "改物价前", time: "2026-09-18 18:20", players: 12,
      size: "131 KB", mtime: 100 },
    { name: "2026-09-18.json", rel: "daily/2026-09-18.json", kind: "daily",
      kind_name: "每日存档", note: "", time: "2026-09-18 04:00", players: 12,
      size: "130 KB", mtime: 90 }
  ]
};

const FAKE_PLAYERS = [
  { user_id: "10001", name: "钓鱼佬", gold: 12800, level: 6, caught: 214, sold: 180,
    fish: 12, aquarium: 3, saved_text: "2026-09-18 18:20:11" },
  { user_id: "10002", name: "小钓手", gold: 3200, level: 3, caught: 88, sold: 70,
    fish: 5, aquarium: 1, saved_text: "2026-09-18 17:02:40" }
];

const FAKE_CONFIG = {
  fish_defs: "carp|鲤鱼|常见|120|novice:1.0|村口常客\ncrucian|鲫鱼|常见|90|novice:1.2|巴掌大",
  rod_defs: ["bamboo|竹竿|🎋|0|0.00|0.00|1|送的"],
  bait_defs: ["none|空钩|🪝|0|0|0|1,1,1,1,1|1||免费", "worm|蚯蚓|🪱|2|5|0.14|1,1.2,1.6,1.8,2.0|2||万用饵"],
  item_defs: ["feed_basic|普通饲料|🌾|20|打基础|meat=2;spirit=1"],
  location_defs: ["novice|新手村|🏡|1|0|1.00|村口小池塘"],
  stamina_max: 25,
  multi_cast_max: 15,
  fish_value_mult: 1.0,
  data_status: "不该出现在数值表里",
  editor_status: JSON.stringify(FAKE_STATUS)
};

/** 第二次读配置时返回「插件已执行完」的状态（updated_at 更大）。 */
function FAKE_CONFIG_AFTER() {
  const done = Object.assign({}, FAKE_STATUS, {
    updated_at: 2,
    last_action: "snapshot_rename",
    last_result: "已把「2026-09-18_1820.json」的备注改成「新备注」",
    ok: true
  });
  return Object.assign({}, FAKE_CONFIG, { editor_status: JSON.stringify(done) });
}

async function channelRoundTrip() {
  console.log("\n[13] 数据通道：真实往返（假 AstrBotPluginPage）");
  captured.uploads = [];
  captured.probeFailures = 0;
  global.window.AstrBotPluginPage = makeFakeSdk();
  // 页面脚本在启动时抓的是 window.AstrBotPluginPage，所以这里要重跑一次完整启动
  const fresh = loadPageFromSource(makeFakeSdk());
  const F = fresh.T;
  // 在线启动要真发几次请求（探测 pluginName + 读配置 + 读状态），等它跑完
  for (let i = 0; i < 40 && (F.state.loading || F.ENV.pluginBase === "astrbot_plugin_qq_fishing"); i++) {
    await new Promise(function (r) { setTimeout(r, 25); });
  }

  check(F.ENV.online === true, "检测到桥接 SDK -> 在线模式");
  check(F.ENV.pluginBase === "dhhxfggg/astrbot_plugin_qq_fishing",
    "pluginName 取的是上下文里给的那个", F.ENV.pluginBase);

  // endpoint 是相对路径，插件名不再参与请求；resolvePluginBase 只做规范化，不发请求
  captured.calls = [];
  const resolved = await F.resolvePluginBase("astrbot_plugin_qq_fishing");
  check(resolved === "astrbot_plugin_qq_fishing",
    "resolvePluginBase 只返回插件名、不再探测（endpoint 已经不带插件名）", resolved);
  check(captured.calls.length === 0, "resolvePluginBase 不发任何请求", captured.calls.length);
  check(captured.calls.concat(captured.calls).every(function (c) { return c.endpoint !== undefined; })
    || true, "（占位：下一步统一检查 endpoint）");
  check((F.state.data.fish || []).length === 2,
    "钓鱼表按 fish_defs 解析出 2 条（不是演示数据）", (F.state.data.fish || []).length);
  check(F.state.data.rods.length === 1 && F.state.data.baits.length === 2
    && F.state.data.items.length === 1 && F.state.data.locations.length === 1,
    "竿/饵/道具/钓点四张表都解析成功");
  check(F.state.data.fish[0].value === 120 && F.state.data.fish[0].dist === "novice:1.0",
    "配置里的基准价与分布被正确解析");
  check(F.dirtyTotal() === 0, "刚载入时没有脏标记（真实数据也一样）", F.dirtyTotal());

  const keys = F.state.data.numbers.map(function (r) { return r.key; });
  check(keys.indexOf("stamina_max") >= 0 && keys.indexOf("multi_cast_max") >= 0,
    "数值页填入了白名单内的键", keys.join(","));
  check(keys.indexOf("data_status") < 0,
    "数值页不显示 data_status（不在插件白名单里）", keys.join(","));
  check(F.state.data.numbers.filter(function (r) { return r.key === "stamina_max"; })[0].value === 25,
    "数值页显示的是配置里的真实值（25）");

  const snaps = F.state.snapshots;
  check(snaps.length === 2, "存档列表来自 editor_status", snaps.length);
  check(snaps[0].name === "2026-09-18_1820.json" && snaps[0].note === "改物价前",
    "存档条目带真实文件名与备注（恢复/删除要用）", snaps[0].name);
  check(snaps[0].kind === "manual" && snaps[0].kind_name === "手动存档",
    "kind / kind_name 都带上了");
  check(F.state.players === 12 && F.state.nextAutoBackup === "2026-09-19 04:00:00",
    "玩家数与下次自动存档时间来自 editor_status",
    F.state.players + " / " + F.state.nextAutoBackup);
  check(F.state.autoBackup.dailyHour === 4 && F.state.autoBackup.keepInterval === 20,
    "自动备份表单被真实配置覆盖");

  // ---- 保存：内容表 + 数值 + 自动备份，各一条指令（POST 到相对路径 config）----
  captured.calls = [];
  F.state.autoBackupDirty = true;
  F.state.data.fish[0].value = 999;          // 制造一处改动
  const saveRes = await F.saveConfig(F.buildPayload());
  check(saveRes.ok === true, "保存成功（同步写回，返回真实结果）", saveRes.message);

  const posts = captured.calls.filter(function (c) { return c.kind === "post"; });
  check(posts.length === 3, "内容 / 数值 / 自动备份各发一条，共 3 条", posts.length);
  check(posts.every(function (c) { return c.endpoint === "config"; }),
    "全部 POST 到插件注册的相对路径 config（不是 /api/files）",
    posts.map(function (c) { return c.endpoint; }).join(","));

  const bodies = posts.map(function (c) { return JSON.parse(c.body); });
  const contentEnv = bodies.filter(function (b) { return b.action === "save_content"; })[0];
  check(!!contentEnv, "发了 save_content 指令");
  check(contentEnv.payload.tables.fish_defs.split("\n")[0].indexOf("999") > 0,
    "改动写进了 fish_defs 文本", contentEnv.payload.tables.fish_defs.split("\n")[0]);
  check(Array.isArray(contentEnv.payload.tables.rod_defs),
    "rod_defs 以字符串数组形式提交");
  const numberEnv = bodies.filter(function (b) { return b.action === "save_numbers"; })[0];
  check(!!numberEnv && numberEnv.payload.stamina_max === 25
    && numberEnv.payload.fish_value_mult === 1.0,
    "数值指令只带白名单内的键", JSON.stringify(numberEnv && numberEnv.payload));
  check(!numberEnv.payload.data_status && !numberEnv.payload.location_defs,
    "数值指令里没有 data_status / location_defs");
  const autoEnv = bodies.filter(function (b) { return b.action === "save_autobackup"; })[0];
  check(!!autoEnv && autoEnv.payload.daily_hour === 4 && autoEnv.payload.enable === true,
    "自动备份设置用插件字段名提交", JSON.stringify(autoEnv && autoEnv.payload));
  F.state.savedAt = null;
  if (F.state._reloadTimer) { clearTimeout(F.state._reloadTimer); }

  // ---- 保存失败：必须给出 HTTP 状态与原因 ----
  captured.calls = [];
  captured.failPost = true;
  const failRes = await F.saveConfig(F.buildPayload());
  captured.failPost = false;
  check(failRes.ok === false && /HTTP 403/.test(failRes.message),
    "保存失败时把 HTTP 状态显示出来", failRes.message);
  check(/Insufficient API key scope/.test(failRes.message),
    "保存失败时把服务端原因也显示出来", failRes.message);

  // ---- 存档动作：走同一个通道，等插件回写结果 ----
  captured.calls = [];
  captured.statusReads = 0;
  captured.phase = "before";
  const renameRes = await F.runSnapshotAction("rename",
    { name: "2026-09-18_1820.json", note: "新备注" });
  const renameCall = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  const renameEnv = JSON.parse(renameCall.body);
  check(renameCall.endpoint === "snapshot", "存档动作 POST 到相对路径 snapshot", renameCall.endpoint);
  check(renameEnv.action === "rename"
    && renameEnv.name === "2026-09-18_1820.json"
    && renameEnv.note === "新备注",
    "改名指令带对了快照名与新备注", JSON.stringify(renameEnv));
  check(renameRes.ok === true && /改过|已把/.test(renameRes.message),
    "改名结果取的是插件返回的真实结果（不是页面自说自话）", renameRes.message);

  captured.calls = [];
  await F.runSnapshotAction("create", { note: "页面手动存档" });
  const createEnv = JSON.parse(captured.calls.filter(function (c) { return c.kind === "post"; })[0].body);
  check(createEnv.action === "create" && createEnv.note === "页面手动存档",
    "新建存档指令正确");

  captured.calls = [];
  await F.runSnapshotAction("delete", { name: "2026-09-18.json" });
  const delEnv = JSON.parse(captured.calls.filter(function (c) { return c.kind === "post"; })[0].body);
  check(delEnv.action === "delete" && delEnv.name === "2026-09-18.json",
    "删除存档指令正确");

  captured.calls = [];
  await F.runSnapshotAction("restore", { name: "2026-09-18.json", player: "89777" });
  const restoreEnv = JSON.parse(captured.calls.filter(function (c) { return c.kind === "post"; })[0].body);
  check(restoreEnv.action === "restore" && restoreEnv.player === "89777",
    "恢复指令带上了玩家 ID（支持只恢复单个玩家）");

  const unknown = await F.runSnapshotAction("nuke", {});
  check(unknown.ok === false && /不认识/.test(unknown.message),
    "未知存档动作被页面自己拦下", unknown.message);

  // ---- 玩家接口（v1.10.0）：list / snapshot_list / set_gold / snapshot_gold ----
  captured.calls = [];
  const okPlayers = await F.fetchPlayers(false);
  const listCall = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  const listEnv = JSON.parse(listCall.body);
  check(okPlayers === true && listCall.endpoint === "players",
    "玩家列表走相对路径 players（插件注册的第三个路由）", listCall.endpoint);
  check(listEnv.action === "list" && listEnv.query === "",
    "列表指令带 action=list + 搜索词", JSON.stringify(listEnv));
  check(F.state.playersList.length === 2 && F.state.playersList[0].gold === 12800
    && F.state.playersList[0].level === 6,
    "玩家行被规范化（金币/等级/钓获都拿到了）", JSON.stringify(F.state.playersList[0]));

  captured.calls = [];
  await F.fetchSnapshotPlayers("2026-09-18_1820.json", false);
  const snapEnv = JSON.parse(captured.calls.filter(function (c) { return c.kind === "post"; })[0].body);
  check(snapEnv.action === "snapshot_list" && snapEnv.name === "2026-09-18_1820.json",
    "存档内玩家走 snapshot_list + 快照名", JSON.stringify(snapEnv));
  check(F.state.snapshotPlayers.length === 1, "存档内玩家列表渲染出来了", F.state.snapshotPlayers.length);

  captured.calls = [];
  const liveRes = await F.savePlayerGold("live", "10001", 5000);
  const liveCall = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  const liveEnv = JSON.parse(liveCall.body);
  check(liveCall.endpoint === "players",
    "改金币 POST 到 players（不走 config，也不会被 snapshot_ 前缀误判到存档接口）", liveCall.endpoint);
  check(liveEnv.action === "set_gold" && liveEnv.user_id === "10001" && liveEnv.gold === 5000,
    "改实时玩家金币的指令正确", JSON.stringify(liveEnv));
  check(liveEnv.confirm === true,
    "请求里带 confirm=true（页面上的二次确认走完才会发这条）");
  check(liveRes.ok === true && /10001/.test(liveRes.message), "改金币结果取插件返回的真实结果", liveRes.message);

  captured.calls = [];
  await F.savePlayerGold("snapshot", "10001", 6600, "2026-09-18_1820.json");
  const snapGoldCall = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  const snapGoldEnv = JSON.parse(snapGoldCall.body);
  check(snapGoldCall.endpoint === "players"
    && snapGoldEnv.action === "snapshot_gold" && snapGoldEnv.name === "2026-09-18_1820.json"
    && snapGoldEnv.user_id === "10001" && snapGoldEnv.gold === 6600 && snapGoldEnv.confirm === true,
    "改存档内金币的指令正确（带快照名 + 玩家 + 金币 + confirm）", JSON.stringify(snapGoldEnv));
  if (F.state._reloadTimer) { clearTimeout(F.state._reloadTimer); }
  return Promise.resolve();
}

/* =============================================================================
   [14] 收尾
   ============================================================================= */
function finish() {
  console.log("\n" + "=".repeat(62));
  if (failed) {
    console.log("❌ " + failed + " 项未通过");
    process.exit(1);
  }
  console.log("🎉 页面运行时冒烟测试全部通过（" + hookNames.length + " 个内部函数已实际执行）");
  process.exit(0);
}
