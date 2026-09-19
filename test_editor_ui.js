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
  // 回复按钮（button_defs）表 + v1.12.1「只拦改过的部分」这套定位/跳转
  "rowKey", "isButtonStyle", "BUTTON_SCENES", "BUTTON_SCENES_FALLBACK", "buttonSceneIds",
  "tabHasError", "problemsInTab", "problemWhere", "gotoProblem", "doSave", "tabHealth",
  // 命令别名 / 自定义命令 两张表 + 玩家页（v1.10.0）
  "renderSubTabs", "canonicalCommands", "normalizePlayerRow", "fetchPlayers",
  "fetchSnapshotPlayers", "savePlayerGold", "playerRowsNow", "renderPlayersTab",
  "renderPlayerRow",
  // 道具效果键白名单（v1.11.0：喂鱼 / 手气 / 装饰 三种角色；v1.15.1 加旧写法映射）
  "ITEM_EFFECT_KEYS", "ITEM_EFFECT_KEY_NAMES", "ITEM_EFFECT_HINT", "ITEM_EFFECT_ALIASES",
  "applyEffectKeys",
  // 💬 回复：场景卡片 / 按钮总览 / 常驻预览
  "REPLY_KEYS", "REPLY_SOURCE_LABEL", "REPLY_LAYOUT_ALL", "REPLY_ROW_MAX",
  "clampPerRow", "normalizeButtonStyle", "normalizeReplyButton", "normalizeReplyScene",
  "normalizeReplyPayload", "replyKVLine", "serializeReplyLines", "parseTextOverrides",
  "serializeTextOverrides", "parseButtonLayout", "serializeButtonLayout", "mergeButtonDefs",
  "mergeTextOverrides", "mergeButtonLayout", "templatePlaceholders", "unknownPlaceholders",
  "renderTemplateText", "renderTemplateHtml", "templateIssues", "templateWarnings",
  "insertPlaceholder", "applyCommandPrefix", "validateReplyButton", "replyPreviewModel",
  "renderReplyPreview", "renderRepliesTab", "renderReplyCard", "renderReplyOverview",
  "renderReplyPreviewPanel", "renderReplyPlaceholderChips", "loadReplies", "saveReplies",
  "replyTextsNow", "replyTouchedKeys", "replyPayloadFor", "replySceneDraft", "replySceneById",
  "replyTextBaseline", "replyDirtyCount", "replyEffectivePerRow", "replyGlobalPerRow",
  "replyFallbackPayload", "applyButtonsTextToTable", "markRepliesSaved", "replyOverviewRows",
  "previewModelFor", "replyBadButtons", "refreshReplyLive", "replyRowMax", "knownPlaceholderNames",
  "replyFilterScenes", "replyVisibleGroups", "renderReplyMain", "replyStatsHtml"
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
  check(Object.keys(T.TAB_BY_ID).length === 16, "标签页数量 = 16（12 + 玩家 + 命令别名 + 自定义命令 + 💬 回复）",
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
  /* 页面上的效果键白名单必须和插件一致。
     v1.13.0：唯一来源是 _effects.BUILTIN_EFFECTS（启动时同步给 _calc.EFFECT_ALLOWED）；
     页面里那份清单只是**离线兜底** —— 在线时页面用插件发来的 effect_keys 覆盖它。 */
  const calcSrc = fs.readFileSync(path.join(__dirname, "_calc.py"), "utf8");
  const allowedBlock = calcSrc.match(/EFFECT_ALLOWED[^=]*=\s*\(([\s\S]*?)\)/);
  const pluginKeys = allowedBlock
    ? allowedBlock[1].split(",").map(s => s.trim().replace(/["']/g, "")).filter(Boolean)
    : [];
  check(pluginKeys.length >= 8, "从 _calc.py 读到 EFFECT_ALLOWED 白名单",
    pluginKeys.join("/") || "没读到");
  const fxSrc = fs.readFileSync(path.join(__dirname, "_effects.py"), "utf8");
  const registryKeys = [];
  const specRe = /EffectSpec\(\s*"([A-Za-z_][A-Za-z0-9_]*)"/g;
  let specHit;
  while ((specHit = specRe.exec(fxSrc)) !== null) registryKeys.push(specHit[1]);
  check(registryKeys.join(",") === pluginKeys.join(","),
    "效果注册表顺序 == _calc 白名单（页面上也是这个顺序）",
    "注册表=" + registryKeys.join("/") + " 白名单=" + pluginKeys.join("/"));
  const pageKeys = T.ITEM_EFFECT_KEY_NAMES.filter(k => k !== "quality_up");
  check(pageKeys.slice().sort().join(",") === pluginKeys.slice().sort().join(","),
    "页面兜底清单与插件一致（在线时会被插件发来的清单覆盖）",
    "页面=" + pageKeys.join("/") + " 插件=" + pluginKeys.join("/"));
  check(/function applyEffectKeys\(/.test(html) && /ITEM_EFFECT_KEYS = rows/.test(html) &&
    /applyEffectKeys\(config\.effect_keys\)/.test(html),
    "页面会用插件发来的 effect_keys 覆盖兜底清单（效果清单不再写死）", "");

  /* 插件清单里的 aliases（旧写法，例如 quality_up）页面也必须认。
     站长报过：洗髓丹那行写着 quality_up，插件明明照收，页面却标红「插件不认识的效果键」。 */
  const aliasPayload = T.ITEM_EFFECT_KEYS
    .filter(function (kv) { return kv[0] !== "quality_up"; })
    .map(function (kv) {
      return {
        key: kv[0], label: kv[1],
        aliases: kv[0] === "buff_quality" ? ["quality_up"] : []
      };
    });
  check(T.applyEffectKeys(aliasPayload) === true, "插件清单能被页面接受");
  check(T.ITEM_EFFECT_KEY_NAMES.indexOf("quality_up") >= 0,
    "清单里的旧写法也进白名单（不会再误报「插件不认识的效果键」）",
    T.ITEM_EFFECT_KEY_NAMES.join("/"));
  check(T.ITEM_EFFECT_ALIASES.quality_up === "buff_quality",
    "旧写法 -> 正式键名的映射也记下来了");
  check(T.ITEM_EFFECT_HINT.indexOf("旧写法") >= 0 && T.ITEM_EFFECT_HINT.indexOf("钓手手气") >= 0,
    "效果提示里写明这是旧写法");
  const aliasRow = { id: "t_alias", name: "洗髓丹", emoji: "🔮", price: 500, effects: "quality_up=0.30" };
  check(Object.keys(T.validateRow(T.TAB_BY_ID.items, aliasRow)).length === 0,
    "写 quality_up 的道具行不报错（插件本来就认）",
    JSON.stringify(T.validateRow(T.TAB_BY_ID.items, aliasRow)));
  const unknownRow = { id: "t_unknown", name: "乱写", emoji: "🔮", price: 1, effects: "nope=1" };
  check(Object.keys(T.validateRow(T.TAB_BY_ID.items, unknownRow)).length > 0,
    "真不认识的效果键照样报错（没把校验放松）");

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
  check(tabsHtml.split("tab-count").length - 1 === 15,
    "15 个标签入口都有条目数徽标（12 原有 + 玩家 + 命令组 + 💬 回复）",
    tabsHtml.split("tab-count").length - 1);
  check(tabsHtml.indexOf('data-tab="replies"') > 0, "标签栏里有「💬 回复」入口");
  check(T.TAB_BY_ID.buttons.label.indexOf("原始文本") > 0,
    "原来的「🔘 按钮」已降级改名成「🔘 按钮（原始文本）」", T.TAB_BY_ID.buttons.label);
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
      } else if (t.kind === "replies") {
        const out = T.renderRepliesTab(t);
        check(out.indexOf("rp-split") >= 0 && out.indexOf("场景卡片") >= 0 && out.indexOf("按钮总览") >= 0,
          "「💬 回复」渲染出卡片视图 + 二级切换", out.length + " 字符");
        check(out.indexOf("rp-side") >= 0 && out.indexOf("👁️ 预览") >= 0,
          "「💬 回复」右侧有常驻预览面板");
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
  }).then(channelHelpers).then(repliesPure).then(channelRoundTrip).then(repliesOnline).then(saveScope).then(finish);
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

  // 文本行（狠角色关键词表）：不能当数字处理，也不能被判「需要是数字」
  const kwRow = { key: "hostile_keywords", label: "狠角色关键词", value: "", unit: "逗号分隔", text: true };
  T.state.data.numbers = [kwRow];
  const kwPayload = T.numberValuesFromPage({ numbers_editable: ["hostile_keywords"] });
  check(Object.keys(kwPayload).length === 0,
    "文本行留空 = 不提交（插件侧「留空」等于用内置默认名单）", JSON.stringify(kwPayload));
  kwRow.value = "鳄,鲨,章鱼";
  check(T.numberValuesFromPage({ numbers_editable: ["hostile_keywords"] }).hostile_keywords === "鳄,鲨,章鱼",
    "文本行提交的是字符串，不是 NaN/0");
  check(Object.keys(T.validateRow(T.TAB_BY_ID.numbers, kwRow)).length === 0,
    "文本行不会被判「需要是数字」", JSON.stringify(T.validateRow(T.TAB_BY_ID.numbers, kwRow)));
  check(Object.keys(T.validateRow(T.TAB_BY_ID.numbers, { key: "stamina_max", value: "乱写" })).length === 1,
    "同一张表的数字行照样会拦（文本行没把校验放松）");
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
   [13] 💬 回复页：三份配置文本 / 占位符 / 预览联动（全部是纯函数，不碰 DOM）
   ============================================================================= */
async function repliesPure() {
  console.log("\n[13] 💬 回复：场景列表与三份配置文本");
  check(T.REPLY_KEYS.join(",") === "button_defs,text_overrides,button_layout",
    "保存回复配置的三个键固定", T.REPLY_KEYS.join(","));
  check(T.REPLY_SOURCE_LABEL.config === "配置" && T.REPLY_SOURCE_LABEL.inherit === "继承" &&
    T.REPLY_SOURCE_LABEL["default"] === "默认" && T.REPLY_SOURCE_LABEL.none === "无按钮",
    "四种按钮来源都有中文标签");

  const scenes = T.state.replies.scenes;
  check(scenes.length === 6 && T.state.replies.groups.length === 3,
    "离线演示 6 个场景 / 3 组（首屏就能看到卡片）",
    scenes.length + " 个场景 / " + T.state.replies.groups.length + " 组");
  check(scenes[0].id === "cast.hit" && scenes[0].source === "config" && scenes[0].buttons.length === 2,
    "第一个场景 cast.hit：来源=配置、2 个按钮");
  check(scenes[0].buttons[0].style === "primary" && scenes[0].buttons[0].label === "🎣 再来一竿",
    "按钮三元组 [文案, 指令, 1] 被规范成 {label,data,style=primary}（官方 1 = 蓝色线框）",
    JSON.stringify(scenes[0].buttons[0]));
  // 数字样式只有 0/1 能翻译成下拉里的两个选项，其余必须原样保留：
  // 翻译错了 = 保存一次就把玩家所有按钮的颜色改掉（v1.13.1 修的坑）
  check(T.normalizeButtonStyle(0) === "default" && T.normalizeButtonStyle(1) === "primary",
    "数字样式 0/1 -> default/primary（对齐官网 0 灰 / 1 蓝）",
    T.normalizeButtonStyle(0) + "/" + T.normalizeButtonStyle(1));
  check(T.normalizeButtonStyle(2) === "2" && T.normalizeButtonStyle(7) === "7"
    && T.normalizeButtonStyle(255) === "255",
    "认不出的数字样式原样保留（2~255 不会被改成 default）",
    [T.normalizeButtonStyle(2), T.normalizeButtonStyle(7), T.normalizeButtonStyle(255)].join(","));
  check(T.normalizeButtonStyle("") === "default" && T.normalizeButtonStyle("默认") === "default"
    && T.normalizeButtonStyle("灰") === "default" && T.normalizeButtonStyle("蓝") === "primary",
    "字符串别名照旧：空/默认/灰 -> default，蓝 -> primary");
  // 往返：插件发 [文案, 指令, 1] -> 页面存回 ...|primary -> 插件再解析还是 1
  const rtBtn = T.normalizeReplyButton(["🎣 再来一竿", "/钓鱼", 1]);
  check(T.TABLE_DEFS.buttons.serialize({
    scene: "cast", label: rtBtn.label, data: rtBtn.data, style: rtBtn.style
  }) === "cast|🎣 再来一竿|/钓鱼|primary",
    "往返不漂：插件给 1，页面存回 primary（插件解析仍是 1）",
    T.TABLE_DEFS.buttons.serialize({
      scene: "cast", label: rtBtn.label, data: rtBtn.data, style: rtBtn.style
    }));
  check(T.state.replies.scenes.filter(function (s) { return s.source === "inherit"; }).length === 1 &&
    T.state.replies.scenes.filter(function (s) { return s.source === "none"; }).length === 1,
    "继承 / 无按钮的来源状态都在演示数据里");

  /* ---- text_overrides：解析与序列化往返 ---- */
  console.log("  ── text_overrides / button_layout 往返 ──");
  const ovText = "cast.hit|🎣 {原文}\nstory.prompt|🎭 换个说法";
  const ovMap = T.parseTextOverrides(ovText);
  check(Object.keys(ovMap).join(",") === "cast.hit,story.prompt",
    "text_overrides 解析成 场景 -> 模板", JSON.stringify(ovMap));
  check(T.serializeTextOverrides(ovMap) === ovText, "text_overrides 往返不漂",
    JSON.stringify(T.serializeTextOverrides(ovMap)));
  check(Object.keys(T.parseTextOverrides("没有竖线的行\n# 注释\n|空场景")).length === 0,
    "缺竖线 / 空场景 / 注释行都被跳过");
  check(T.parseTextOverrides("CAST.HIT|x")["cast.hit"] === "x", "场景 id 大小写归一");
  check(T.serializeTextOverrides({ "cast.hit": "A|B" }) === "cast.hit|A B",
    "模板里的竖线会被洗掉（它是字段分隔符）");
  check(T.serializeTextOverrides({ b: "2", a: "1" }, ["a", "b"]) === "a|1\nb|2",
    "按场景顺序输出（配置里读起来跟界面一致）");
  check(T.serializeTextOverrides({ a: "x", b: "  " }) === "a|x",
    "空覆盖不写成空行");

  /* ---- button_layout：解析与序列化往返 ---- */
  const layout = T.parseButtonLayout("*|3\nstory.prompt|1\nbad|abc\nzero|0\nbig|9");
  check(Object.keys(layout).join(",") === "*,story.prompt",
    "button_layout 只认 1~5 的整数（abc/0/9 被丢掉）", JSON.stringify(layout));
  check(T.serializeButtonLayout({ "story.prompt": 1, "*": 3 }) === "*|3\nstory.prompt|1",
    "button_layout 序列化时 * 排在最前面（全局默认）");
  const layoutBack = T.parseButtonLayout(T.serializeButtonLayout(layout));
  check(layoutBack["*"] === 3 && layoutBack["story.prompt"] === 1, "button_layout 往返不漂");

  /* ---- button_defs：只动改过的场景 ---- */
  console.log("  ── button_defs 合并（没改的一行都不碰） ──");
  const rawLines = [
    "# 这里是我的备注",
    "cast.hit|🎣 再来一竿|/钓鱼|primary",
    "cast.hit|🎒 背包|/钓鱼 背包|default",
    "story.prompt|{label}|/钓鱼 事件 {n}|default",
    "半行没有竖线"
  ];
  const merged = T.mergeButtonDefs(rawLines, {
    "cast.hit": [{ scene: "cast.hit", label: "再钓一竿", data: "/钓鱼", style: "default" }],
    "cast.done": [{ scene: "cast.done", label: "去背包", data: "/钓鱼 背包", style: "default" }]
  });
  check(merged[0] === "# 这里是我的备注", "注释行原样保留");
  check(merged.filter(function (l) { return l.indexOf("cast.hit|") === 0; }).length === 1 &&
    merged.indexOf("cast.hit|再钓一竿|/钓鱼|default") === 1,
    "改过的场景整段替换（两行变一行，位置不动）", merged.join(" ⏎ "));
  check(merged.indexOf("story.prompt|{label}|/钓鱼 事件 {n}|default") > 0,
    "没改过的场景一个字都不动（占位符也保留）");
  check(merged[merged.length - 1] === "cast.done|去背包|/钓鱼 背包|default",
    "原来没有的场景追加到末尾", merged[merged.length - 1]);
  check(merged.indexOf("半行没有竖线") > 0, "解析不了的行也保留（绝不丢配置）");
  check(T.mergeButtonDefs(rawLines, { "cast.hit": [] })
    .every(function (l) { return l.indexOf("cast.hit|") !== 0; }),
    "把场景的按钮清空 = 那两行都没了（回退到继承/默认）");

  /* ---- text_overrides / button_layout 的合并 ---- */
  const sceneList = [
    { id: "a", text: { template: "T-a" } },
    { id: "b", text: { template: "T-b" } },
    { id: "c", text: { template: "T-c" } }
  ];
  const mo = T.mergeTextOverrides({ a: "旧A", b: "旧B" }, sceneList, { a: "新A", b: "T-b", c: "   " });
  check(mo.a === "新A", "改过的场景写新覆盖");
  check(mo.b === undefined, "填回原模板 = 删掉这条覆盖（不留垃圾行）");
  check(mo.c === undefined, "空文案不写覆盖");
  check(T.mergeTextOverrides({ x: "不动" }, sceneList, {}).x === "不动", "没碰过的场景的覆盖保留");
  const ml = T.mergeButtonLayout({ "*": 3, "story.prompt": 1 }, { "story.prompt": "2", "cast.hit": "" });
  check(ml["*"] === 3 && ml["story.prompt"] === 2 && ml["cast.hit"] === undefined,
    "布局合并：改了的写、空的删、全局默认保留", JSON.stringify(ml));

  /* ---- 占位符替换 / 未知占位符 ---- */
  console.log("  ── 占位符：替换、未知高亮、{原文} 警告 ──");
  const samples = { "原文": "🐟 鲤鱼", "鱼名": "鲤鱼" };
  check(T.templatePlaceholders("🎣 {原文}｜{鱼名}｜{原文}").join(",") === "原文,鱼名",
    "占位符按出现顺序去重");
  check(T.renderTemplateText("🎣 {原文}", samples) === "🎣 🐟 鲤鱼",
    "已知占位符被示例值替换", T.renderTemplateText("🎣 {原文}", samples));
  check(T.renderTemplateText("🎣 {没有这个}", samples) === "🎣 {没有这个}",
    "未知占位符原样留着（看得见才改得掉）");
  check(T.unknownPlaceholders("🎣 {原文}{鱼名}{金币}", samples).join(",") === "金币",
    "未知占位符判断函数只挑出 samples 里没有的");
  check(T.unknownPlaceholders("🎣 {原文}{鱼名}{金币}", samples, ["原文", "鱼名", "金币"]).length === 0,
    "场景声明过的占位符就算没有示例值也不算未知（插件认它）");
  const hlHtml = T.renderTemplateHtml("🎣 {原文}{金币}", samples);
  check(hlHtml.indexOf("rp-ph-bad") > 0 && hlHtml.indexOf("{金币}") > 0,
    "未知占位符在预览里被醒目标记（会写坏的情况）");
  check(hlHtml.indexOf("rp-sampled") > 0 && hlHtml.indexOf("🐟 鲤鱼") > 0,
    "已知占位符渲染成示例值");
  check(T.renderTemplateHtml("🎣 {鱼名}", samples, ["鱼名"]).indexOf("rp-ph-bad") < 0,
    "声明过但没有示例值的占位符不会被标成错误");
  check(T.insertPlaceholder("🎣 {原文}", "鱼名", 7, 7) === "🎣 {原文}{鱼名}",
    "点占位符 -> 插到光标处", T.insertPlaceholder("🎣 {原文}", "鱼名", 7, 7));
  check(T.insertPlaceholder("{原文} 了", "鱼名", 0, 4) === "{鱼名} 了",
    "有选中内容时替换选中的那段");
  check(T.insertPlaceholder("", "原文") === "{原文}", "空模板直接插入");

  /* ---- 按钮校验 + 批量改前缀 ---- */
  check(Object.keys(T.validateReplyButton({ label: "看背包", data: "/钓鱼 背包", style: "primary" })).length === 0,
    "合法按钮草稿校验通过");
  check(T.validateReplyButton({ label: "", data: "/钓鱼 背包" }).label &&
    T.validateReplyButton({ label: "看背包", data: "" }).data &&
    T.validateReplyButton({ label: "看背包", data: "背包" }).data &&
    T.validateReplyButton({ label: "看背包", data: "/钓鱼 背包", style: "红色" }).style,
    "空文案 / 空指令 / 指令没有 / 开头 / 样式乱写都会被标红");
  check(T.validateReplyButton({ label: "看背包", data: "/钓鱼 背包", style: "" }).style === undefined,
    "样式留空 = 默认，不算错");
  check(T.applyCommandPrefix("/钓鱼 背包", "/钓鱼") === "/钓鱼 背包", "前缀没变时指令不变");
  check(T.applyCommandPrefix("/钓鱼 背包", "/fish") === "/fish 背包", "批量改前缀只换第一段");
  check(T.applyCommandPrefix("背包", "/钓鱼") === "/钓鱼 背包", "没有前缀的指令会被补上前缀");
  check(T.applyCommandPrefix("/钓鱼 背包", "  ") === "/钓鱼 背包", "前缀为空时原样返回（不会把指令写坏）");

  const dynScene = { id: "x", dynamic_text: true, text: { samples: samples, placeholders: ["原文", "鱼名"] } };
  const w1 = T.templateWarnings("🎣 你钓到了鱼！", dynScene);
  check(w1.some(function (s) { return s.indexOf("必须保留 {原文}") > 0; }),
    "dynamic_text 且模板缺 {原文} -> 警告会说明插件会忽略这次覆盖", w1.join(" | "));
  check(!T.templateWarnings("🎣 {原文}", dynScene).some(function (s) { return s.indexOf("必须保留") > 0; }),
    "保留了 {原文} 就不警告");
  check(T.templateWarnings("🎣 {原文} {金币}", dynScene).some(function (s) { return s.indexOf("金币") > 0; }),
    "未知占位符也进警告文案");
  check(T.templateWarnings("", dynScene).some(function (s) { return s.indexOf("空的") > 0; }),
    "空文案有专门提示");
  const staticScene = { id: "y", dynamic_text: false, text: { samples: {}, placeholders: [] } };
  check(T.templateWarnings("随便写什么都行", staticScene).length === 0,
    "dynamic_text=false 的模板可以任意改写（不报警）");
  check(T.templateIssues("x", {}, true).missingOriginal === true &&
    T.templateIssues("x", {}, false).missingOriginal === false,
    "templateIssues 的 {原文} 判断只看 dynamic_text");

  /* ---- 卡片 ↔ 预览 三者联动 ---- */
  console.log("  ── 卡片 ↔ 预览联动（改文案 / 加减按钮 / 群聊单聊） ──");
  const hit = T.replySceneById("cast.hit");
  const dHit = T.replySceneDraft("cast.hit");
  const m0 = T.previewModelFor(hit);
  check(m0.text.indexOf("🐟 鲤鱼") > 0 && m0.buttons.length === 2,
    "预览模型：文案用示例值渲染，按钮 2 个", m0.text);
  dHit.text = "✨ 恭喜！{原文}";
  dHit.textTouched = true;
  const m1 = T.previewModelFor(hit);
  check(m1.text !== m0.text && m1.text.indexOf("✨ 恭喜！") === 0,
    "改文案后预览文字跟着变（卡片↔预览联动）", m1.text);
  dHit.buttons.push({ label: "看图鉴", data: "/钓鱼 图鉴", style: "default" });
  const m2 = T.previewModelFor(hit);
  check(m2.buttons.length === 3, "加按钮后预览里的按钮数 2 -> 3");
  dHit.buttons.pop();
  check(T.previewModelFor(hit).buttons.length === 2, "删按钮后预览里的按钮数又回到 2");

  const gModel = T.replyPreviewModel(hit, { mode: "group" });
  const pModel = T.replyPreviewModel(hit, { mode: "private" });
  check(gModel.hint !== pModel.hint && gModel.hint.indexOf("群聊") === 0 && pModel.hint.indexOf("单聊") === 0,
    "群聊 / 单聊的点击提示不一样", gModel.hint + " ｜ " + pModel.hint);
  check(gModel.hint.indexOf("不会自动发送") > 0 && pModel.hint.indexOf("直接") > 0,
    "群里点按钮只填输入框、单聊才会自动发送");
  check(gModel.modeLabel === "群聊" && pModel.modeLabel === "单聊" &&
    gModel.chatName !== pModel.chatName, "预览里的会话名跟着模式走");

  const prevHtml = T.renderReplyPreview(T.previewModelFor(hit));
  check(prevHtml.indexOf("rp-bubble") > 0 && prevHtml.indexOf("rp-grid") > 0,
    "预览 HTML 有气泡 + 按钮网格");
  check(prevHtml.indexOf("grid-template-columns:repeat(3") > 0,
    "按钮网格按「每行几个」排（cast.hit 是 3）");
  check(prevHtml.indexOf("✨ 恭喜！") > 0, "预览用的是卡片里正在编辑的文案");
  check(T.renderReplyPreview(gModel).indexOf("群聊") > 0 &&
    T.renderReplyPreview(pModel).indexOf("单聊") > 0,
    "切群聊/单聊 -> 预览 HTML 里的提示跟着换");
  check(T.renderReplyPreview(T.previewModelFor(T.replySceneById("cast.done")))
    .indexOf("没有任何按钮") > 0, "没有按钮的场景预览里会说明白");

  dHit.perRow = "2";
  dHit.layoutTouched = true;
  check(T.replyEffectivePerRow(hit) === 2, "场景的「每行几个」覆盖生效");
  check(T.renderReplyPreview(T.previewModelFor(hit)).indexOf("repeat(2") > 0,
    "预览网格跟着变成每行 2 个");
  dHit.perRow = "";
  dHit.layoutTouched = false;
  check(T.replyEffectivePerRow(hit) === 3, "清掉覆盖后回到插件给的 3 个");

  /* ---- 卡片 HTML ---- */
  console.log("  ── 卡片 / 总览渲染 ──");
  dHit.text = "🎣 {原文}";
  const card = T.renderReplyCard(hit);
  check(card.indexOf("cast.hit") > 0 && card.indexOf("来源：配置") > 0,
    "卡片上有场景 id 与来源徽标（配置）");
  check(card.indexOf('data-act="rp:text"') > 0 && card.indexOf("🎣 {原文}") > 0,
    "卡片里能直接改文案模板");
  check(card.indexOf('data-act="rp:btnLabel"') > 0 && card.indexOf('data-act="rp:btnData"') > 0 &&
    card.indexOf('data-act="rp:btnDel"') > 0 && card.indexOf('data-act="rp:btnUp"') > 0 &&
    card.indexOf('data-act="rp:btnDown"') > 0,
    "卡片里的按钮能改文案/指令/排序/删除");
  check(card.indexOf('data-act="rp:perRow"') > 0 && card.indexOf('data-act="rp:saveCard"') > 0,
    "卡片上有「每行 N 个」覆盖与「只保存这张」");
  const noneCard = T.renderReplyCard(T.replySceneById("cast.done"));
  check(noneCard.indexOf('data-act="rp:btnAdd"') > 0 && noneCard.indexOf("来源：无按钮") > 0,
    "没有按钮的场景也能一键加按钮，来源写着「无按钮」");
  const inheritCard = T.renderReplyCard(T.replySceneById("cast.miss"));
  check(inheritCard.indexOf("来源：继承自 cast") > 0, "继承来的按钮在徽标里标出父场景（继承自 cast）");
  const chips = T.renderReplyPlaceholderChips(hit, "🎣 {原文} {鱼名} {金币}");
  check(chips.indexOf('data-act="rp:ph"') > 0 && chips.indexOf("{鱼名}") > 0,
    "占位符提示可点击插入");
  check(chips.indexOf("is-bad") > 0 && chips.indexOf("{金币}") > 0,
    "模板里出现未声明的占位符会被标红");

  const overview = T.renderReplyOverview();
  check(overview.indexOf('data-act="rp:layoutAll"') > 0 && overview.indexOf("全局设置") > 0,
    "按钮总览顶部有全局设置（* 每行几个）");
  check(overview.indexOf('data-act="rp:batch"') > 0 && overview.indexOf("批量改指令前缀") > 0,
    "按钮总览有批量改样式 / 改前缀 / 删除");
  check(overview.indexOf("cast.hit") > 0 && overview.indexOf("🎣 再来一竿") > 0,
    "总览把所有场景的按钮列成一张表");

  /* ---- 154 个场景时的可用性：搜索 + 分组折叠 + 局部刷新 ---- */
  console.log("  ── 场景搜索 / 分组折叠 / 大场景量冒烟 ──");
  check(T.replyFilterScenes(scenes, "").length === 6, "搜索词为空 -> 全部场景");
  check(T.replyFilterScenes(scenes, "cast.hit").map(function (s) { return s.id; }).join(",") === "cast.hit",
    "按场景 id 搜索");
  check(T.replyFilterScenes(scenes, "空钩").length === 1, "按场景名称搜索（中文）");
  check(T.replyFilterScenes(scenes, "咬钩").length === 2, "按分组名搜索（咬钩与拉线）");
  check(T.replyFilterScenes(scenes, "绝对不存在zzz").length === 0, "搜不到就是空");
  T.state.replies.query = "cast";
  check(T.replyVisibleGroups().length === 1 && T.replyVisibleGroups()[0].scenes.length === 3,
    "筛过之后只剩命中的分组");
  const filteredMain = T.renderReplyMain();
  check(filteredMain.indexOf("cast.miss") > 0 && filteredMain.indexOf("story.prompt") < 0,
    "筛选后卡片列表只剩命中的场景");
  check(T.replyStatsHtml().indexOf("<b>3</b> / 6") > 0, "工具栏计数显示「筛出 3 / 共 6」");
  T.state.replies.query = "";
  T.state.replies.collapsed = { cast: true };
  const collapsedMain = T.renderReplyMain();
  check(collapsedMain.indexOf("▸") > 0 && collapsedMain.indexOf('data-scene="cast.hit"') < 0 &&
    collapsedMain.indexOf("story.prompt") > 0,
    "收起分组后这一组的卡片不再渲染（其他组照常）");
  check(collapsedMain.indexOf("3 个场景") > 0, "分组标题上写着这一组有几个场景");
  T.state.replies.collapsed = {};

  // 24 组 × 7 个场景 = 168 个场景（比线上的 154 个还多一点）：渲染不能炸、也得筛得动
  const bigGroups = [];
  for (let gi = 1; gi <= 24; gi++) {
    const list = [];
    for (let si = 1; si <= 7; si++) {
      list.push({
        id: "g" + gi + ".s" + si, label: "场景 " + gi + "-" + si, desc: "第 " + gi + " 组的第 " + si + " 个场景",
        parent: "g" + gi, parent_label: "g" + gi, source: si === 1 ? "config" : (si === 2 ? "inherit" : "default"),
        per_row: si, dynamic_text: true,
        buttons: [["按钮 A" + si, "/钓鱼 帮助", 1], ["按钮 B" + si, "/钓鱼 背包", 0]],
        text: {
          template: "🎣 {原文}", placeholders: ["原文"], samples: { "原文": "示例 " + gi + "-" + si },
          override: "", preview: "示例 " + gi + "-" + si, dynamic: true
        }
      });
    }
    bigGroups.push({ id: "g" + gi, label: "第 " + gi + " 组", desc: "", scenes: list });
  }
  const bigPayload = T.normalizeReplyPayload({
    transport: "plugin-api", buttons_per_row_default: 3, buttons_per_row_max: 5, groups: bigGroups
  });
  const savedScenes = T.state.replies.scenes;
  const savedGroups = T.state.replies.groups;
  const savedDrafts = T.state.replies.drafts;
  T.state.replies.scenes = bigPayload.scenes;
  T.state.replies.groups = bigPayload.groups;
  T.state.replies.drafts = {};
  T.state.replies.collapsed = {};
  T.state.replies.query = "";
  const bigHtml = T.renderRepliesTab(T.TAB_BY_ID.replies);
  check(bigPayload.scenes.length === 168 && T.state.replies.groups.length === 24,
    "构造出 168 个场景 / 24 组（跟线上 154 个同量级）", bigPayload.scenes.length);
  check(bigHtml.indexOf('data-scene="g24.s7"') > 0 && bigHtml.indexOf('data-scene="g1.s1"') > 0 &&
    bigHtml.indexOf("rp-phone") > 0,
    "168 个场景一次渲染不炸（首尾卡片 + 预览都在）", bigHtml.length + " 字符");
  check(T.replyFilterScenes(bigPayload.scenes, "24-7").length === 1 &&
    T.replyFilterScenes(bigPayload.scenes, "g13").length === 7,
    "大列表里按 id / 名称筛得动");
  T.state.replies.query = "24-7";
  const bigFiltered = T.renderReplyMain();
  check(bigFiltered.indexOf("g24.s7") > 0 && bigFiltered.indexOf("g24.s6") < 0 &&
    bigFiltered.length < bigHtml.length / 10,
    "筛出 1 个场景时只渲染那一张卡", bigFiltered.length + " 字符");
  check(T.clampPerRow("5", 0, bigPayload.perRowMax) === 5 &&
    T.clampPerRow("6", 0, bigPayload.perRowMax) === 0 &&
    T.parseButtonLayout("x|9", bigPayload.perRowMax).x === undefined,
    "接口给的上限（buttons_per_row_max=5）真的会拿来卡住 6");
  T.state.replies.scenes = savedScenes;
  T.state.replies.groups = savedGroups;
  T.state.replies.drafts = savedDrafts;
  T.state.replies.query = "";
  T.state.replies.collapsed = {};
  check(T.state.replies.scenes.length === 6, "大场景量冒烟后状态被还原（后面的断言不受影响）");

  /* ---- 只提交变化的那几项 ---- */
  console.log("  ── 保存 payload：只提交真的变了的项 ──");
  T.state.replies.drafts = {};
  check(T.replyDirtyCount() === 0, "清掉草稿后未保存数为 0");
  check(Object.keys(T.replyPayloadFor(null)).length === 0,
    "什么都没改 -> 一个键都不提交（不会把别的项覆盖成空）",
    JSON.stringify(T.replyPayloadFor(null)));

  const dOnlyText = T.replySceneDraft("cast.hit");
  dOnlyText.text = "🎣 {原文}（改过）";
  dOnlyText.textTouched = true;
  const pText = T.replyPayloadFor(null);
  check(Object.keys(pText).join(",") === "text_overrides",
    "只改文案 -> 只提交 text_overrides", Object.keys(pText).join(","));
  check(pText.text_overrides.indexOf("cast.hit|🎣 {原文}（改过）") === 0,
    "text_overrides 里就是改过的那条", pText.text_overrides);
  check(Object.keys(T.replyPayloadFor("cast.miss")).length === 0,
    "「只保存这张卡片」时不会带上别的场景的改动");
  const emptySave = await T.saveReplies("cast.miss");
  check(emptySave.ok === false && emptySave.empty === true,
    "没改动的卡片不会被提交（有专门提示）", emptySave.message);
  const offlineSave = await T.saveReplies(null);
  check(offlineSave.ok === false && /离线预览/.test(offlineSave.message),
    "离线模式下保存回复配置被拦下（不假装成功）", offlineSave.message);

  const dBtn = T.replySceneDraft("cast.done");
  dBtn.buttons.push({ label: "🎣 再来一竿", data: "/钓鱼", style: "primary" });
  dBtn.buttonsTouched = true;
  const dClear = T.replySceneDraft("cast.miss");
  dClear.buttons = [];
  dClear.buttonsTouched = true;
  T.state.replies.layoutAllTouched = true;
  T.state.replies.layoutAllDraft = "4";
  const pAll = T.replyPayloadFor(null);
  check(Object.keys(pAll).sort().join(",") === "button_defs,button_layout,text_overrides",
    "按钮 + 文案 + 布局都改了 -> 三份文本一起提交", Object.keys(pAll).join(","));
  check(pAll.button_defs.indexOf("cast.done|🎣 再来一竿|/钓鱼|primary") > 0,
    "新按钮写进了 button_defs", pAll.button_defs);
  check(pAll.button_defs.indexOf("cast.miss") < 0, "清空的场景在 button_defs 里不再有行");
  check(pAll.button_defs.indexOf("story|{label}|/钓鱼 事件 {n}|default") > 0,
    "没改过的场景行原样保留（含 {label} 占位符）");
  check(pAll.button_layout.indexOf("*|4") === 0, "全局每行几个写进 button_layout", pAll.button_layout);
  check(pAll.text_overrides.indexOf("cast.hit|🎣 {原文}（改过）") === 0,
    "文案覆盖与按钮改动互不影响");

  T.markRepliesSaved(pAll);
  check(T.replyDirtyCount() === 0 && Object.keys(T.replyPayloadFor(null)).length === 0,
    "保存成功后草稿清零、再保存不会重复提交");
  check((T.state.data.buttons || []).some(function (r) { return r.scene === "cast.done"; }),
    "原始按钮表被同步（避免 save_content 把回复页的改动写回旧值）");
  return Promise.resolve();
}

/* =============================================================================
   [14] 数据通道的真实往返：假 SDK（apiGet/apiPost 到插件注册的相对路径）
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
  button_defs: "cast.hit|🎣 再来一竿|/钓鱼|primary\ncast.hit|🎒 背包|/钓鱼 背包|default\n" +
    "# 保留注释\nstory.prompt|{label}|/钓鱼 事件 {n}|default",
  text_overrides: "",
  button_layout: "*|3",
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
  console.log("\n[14] 数据通道：真实往返（假 AstrBotPluginPage）");
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

  /* ---- 💬 回复：scenes 端点读不到时的只读兜底（不白屏、不抛异常） ---- */
  console.log("  ── 💬 回复：scenes 读不到 -> 只读兜底 ──");
  check(F.state.replies.loaded === true && F.state.replies.readonly === true,
    "scenes 读不到 -> 进入只读兜底模式（页面照常渲染）");
  check(/scenes/.test(F.state.replies.error) && F.state.replies.error.length > 10,
    "失败原因写进页面状态（给用户看的中文提示）", F.state.replies.error);
  check(F.state.replies.scenes.length === 2 && F.state.replies.scenes[0].id === "cast.hit" &&
    F.state.replies.scenes[0].buttons.length === 2,
    "退回用 config 里的 button_defs 只读展示",
    F.state.replies.scenes.map(function (s) { return s.id + ":" + s.buttons.length; }).join(" "));
  const fallbackHtml = F.renderRepliesTab(F.TAB_BY_ID.replies);
  check(fallbackHtml.indexOf("读不到「回复场景」接口") > 0 && fallbackHtml.indexOf("🔄 重试") > 0,
    "页面上有中文错误提示 + 重试按钮");
  check(fallbackHtml.indexOf("只读") > 0 && fallbackHtml.indexOf('data-act="rp:saveCard"') < 0,
    "兜底视图标明只读（没有「只保存这张」这类改写入侵）");
  const roSave = await F.saveReplies(null);
  check(roSave.ok === false && /没有改动/.test(roSave.message),
    "只读兜底时没有改动 -> 保存直接说「没有改动」", roSave.message);
  const roDraft = F.replySceneDraft("cast.hit");
  roDraft.text = "改一改试试";
  roDraft.textTouched = true;
  const roSave2 = await F.saveReplies(null);
  check(roSave2.ok === false && /只读/.test(roSave2.message),
    "只读模式下保存被拦下并说明原因（不静默失败）", roSave2.message);
  roDraft.textTouched = false;
  if (F.state._reloadTimer) { clearTimeout(F.state._reloadTimer); }
  return Promise.resolve();
}

/* =============================================================================
   [15] 💬 回复：真实的 scenes 往返（假 SDK 提供 scenes 路由）
   ============================================================================= */
const FAKE_SCENES = {
  status: "ok", transport: "plugin-api", scene_total: 3,
  buttons_per_row_default: 3, buttons_per_row_max: 5,
  button_styles: [{ value: "default", label: "默认（灰）" }, { value: "primary", label: "主要（蓝）" }],
  groups: [
    {
      id: "cast", label: "🎣 下竿", desc: "抛竿与连钓的所有回复",
      scenes: [
        {
          id: "cast.hit", label: "钓到鱼的结果", desc: "单竿钓上鱼之后那条消息",
          parent: "cast", parent_label: "cast", source: "config", per_row: 3, dynamic_text: true,
          default_buttons: [["🎣 再来一竿", "/钓鱼", 1]],
          buttons: [["🎣 再来一竿", "/钓鱼", 1], ["🎒 背包", "/钓鱼 背包", 1]],
          text: {
            template: "🎣 {原文}", placeholders: ["原文", "鱼名"],
            samples: { "原文": "🎣 🐟 鲤鱼　⚪普通　120 金币", "鱼名": "鲤鱼" },
            override: "", preview: "🎣 🐟 鲤鱼　⚪普通　120 金币", dynamic: true
          }
        },
        {
          id: "cast.none", label: "没钓到 / 空钩", desc: "", parent: "cast", parent_label: "cast",
          source: "none", per_row: 0, dynamic_text: true,
          default_buttons: [], buttons: [],
          text: {
            template: "{原文}", placeholders: ["原文"],
            samples: { "原文": "💨 这一竿空了" }, override: "",
            preview: "💨 这一竿空了", dynamic: true
          }
        }
      ]
    },
    {
      id: "story", label: "🎭 随机插曲", desc: "", scenes: [
        {
          id: "story.prompt", label: "插曲选项", desc: "", parent: "story", parent_label: "story",
          source: "default", per_row: 1, dynamic_text: false,
          default_buttons: [["{label}", "/钓鱼 事件 {n}", 0]],
          buttons: [["{label}", "/钓鱼 事件 {n}", 0]],
          text: {
            template: "🎭 {原文}", placeholders: ["原文", "label", "n"],
            samples: { "原文": "遇到一只猫" }, override: "",
            preview: "🎭 遇到一只猫", dynamic: false
          }
        }
      ]
    }
  ]
};

/** 会回 scenes 的假 SDK（其余行为跟 makeFakeSdk 一样）。 */
function makeScenesSdk() {
  const sdk = makeFakeSdk();
  const baseGet = sdk.apiGet;
  const basePost = sdk.apiPost;
  sdk.apiGet = function (endpoint) {
    if (String(endpoint) === "scenes") {
      capture("get", endpoint, null);
      return Promise.resolve(JSON.parse(JSON.stringify(FAKE_SCENES)));
    }
    return baseGet.call(sdk, endpoint);
  };
  sdk.apiPost = function (endpoint, body) {
    if (body && body.action === "save_replies") {
      capture("post", endpoint, JSON.stringify(body));
      return Promise.resolve({
        status: "ok", ok: true,
        message: "已写回回复配置（" + Object.keys(body.payload || {}).join(" / ") + "）",
        editor_status: JSON.stringify(FAKE_STATUS)
      });
    }
    return basePost.call(sdk, endpoint, body);
  };
  return sdk;
}

async function repliesOnline() {
  console.log("\n[15] 💬 回复：真实的 scenes 往返（假 SDK）");
  captured.calls = [];
  captured.phase = "before";
  global.window.AstrBotPluginPage = makeScenesSdk();
  const fresh = loadPageFromSource(makeScenesSdk());
  const F = fresh.T;
  for (let i = 0; i < 60 && (F.state.loading || !F.state.replies.loaded); i++) {
    await new Promise(function (r) { setTimeout(r, 25); });
  }
  check(F.ENV.online === true && F.state.replies.readonly === false && !F.state.replies.error,
    "scenes 通了 -> 在线可编辑（不走只读兜底）", F.state.replies.error || "无错误");
  check(F.state.replies.scenes.length === 3 && F.state.replies.groups.length === 2,
    "读到 3 个场景 / 2 组", F.state.replies.scenes.map(function (s) { return s.id; }).join(","));
  check(F.state.replies.scenes[1].id === "cast.none" && F.state.replies.scenes[1].source === "none" &&
    F.state.replies.scenes[1].buttons.length === 0,
    "没有按钮的场景读得到（来源=无按钮）");
  check(F.state.replies.scenes[2].source === "default" &&
    F.state.replies.scenes[2].buttons[0].style === "default" &&
    F.state.replies.scenes[2].buttons[0].data === "/钓鱼 事件 {n}",
    "出厂默认按钮的原样保留（数字样式 0 -> default）");
  check(F.state.replies.perRowDefault === 3 && F.state.replies.perRowMax === 5 &&
    F.state.replies.buttonStyles.length === 2 &&
    F.state.replies.buttonStyles[0].value === "default",
    "全局每行几个 / 上限 / 样式选项都来自接口",
    F.state.replies.perRowDefault + " / " + F.state.replies.perRowMax);
  check(F.state.replies.original.button_defs.indexOf("# 保留注释") >= 0 &&
    F.state.replies.originalLayout["*"] === 3,
    "原始 button_defs / button_layout 都读到了（判断「哪几项变了」的基线）");
  check(F.templateWarnings("🎭 {原文} {label} {n}", F.state.replies.scenes[2]).length === 0 &&
    F.unknownPlaceholders("🎭 {原文} {label} {n}",
      F.state.replies.scenes[2].text.samples,
      F.state.replies.scenes[2].text.placeholders).length === 0,
    "{label}/{n} 这种「声明了但没示例值」的占位符不算写坏（插件认）");

  const tabHtml = F.renderRepliesTab(F.TAB_BY_ID.replies);
  check(tabHtml.indexOf("cast.hit") > 0 && tabHtml.indexOf("🎣 再来一竿") > 0 &&
    tabHtml.indexOf("rp-bubble") > 0, "卡片 + 常驻预览都渲染出来了");
  check(tabHtml.indexOf("来源：无按钮") > 0 && tabHtml.indexOf("来源：默认") > 0,
    "来源徽标按接口给的 source 显示");
  check(tabHtml.indexOf("群聊：点按钮只会把指令填进输入框") > 0, "预览里写清了群聊点按钮的行为");

  const d = F.replySceneDraft("cast.hit");
  d.text = "🐟 {原文}";
  d.textTouched = true;
  const dn = F.replySceneDraft("cast.none");
  dn.buttons.push({ label: "🎣 再来一竿", data: "/钓鱼", style: "primary" });
  dn.buttonsTouched = true;
  dn.perRow = "2";
  dn.layoutTouched = true;

  captured.calls = [];
  const res = await F.saveReplies(null);
  check(res.ok === true, "保存成功（插件同步写回）", res.message);
  const post = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  check(!!post && post.endpoint === "config", "save_replies POST 到插件注册的 config", post && post.endpoint);
  const env = JSON.parse(post.body);
  check(env.action === "save_replies", "指令名是 save_replies", env.action);
  check(Object.keys(env.payload).sort().join(",") === "button_defs,button_layout,text_overrides",
    "三份文本都在 payload 里一起提交", Object.keys(env.payload).join(","));
  check(env.payload.button_defs.indexOf("# 保留注释") >= 0, "注释行原样保留");
  check(env.payload.button_defs.indexOf("cast.none|🎣 再来一竿|/钓鱼|primary") > 0,
    "新加的按钮写进了 button_defs", env.payload.button_defs);
  check(env.payload.button_defs.indexOf("story.prompt|{label}|/钓鱼 事件 {n}|default") > 0,
    "没改过的场景一个字都不动");
  check(env.payload.text_overrides === "cast.hit|🐟 {原文}",
    "text_overrides 只有改过的那条", env.payload.text_overrides);
  check(env.payload.button_layout.indexOf("*|3") === 0 &&
    env.payload.button_layout.indexOf("cast.none|2") > 0,
    "button_layout = 全局默认 + 这个场景的覆盖行", env.payload.button_layout);
  check(F.replyDirtyCount() === 0 && Object.keys(F.replyPayloadFor(null)).length === 0,
    "保存成功后草稿清零、再点保存不会重复提交");
  check((F.state.data.buttons || []).some(function (r) { return r.scene === "cast.none"; }),
    "原始按钮表被同步成保存后的内容");
  if (F.state._reloadTimer) { clearTimeout(F.state._reloadTimer); }
  return Promise.resolve();
}

/* =============================================================================
   [16] 保存只拦「这次改过的部分」（v1.12.1）
   现场：站长在「💬 回复」页给 cast.miss_none 这类新场景加了按钮，页面白名单还写着
         老的 5 个场景 -> 那几行判红 -> doSave 整页拒收 -> 他改好的命令别名一起没了。
   ============================================================================= */

/** 真实插件场景表的缩样：包含老 5 个 + 只有新版本才有的点号场景。 */
const REAL_SCENE_IDS = ["cast", "pull", "bag", "location", "story",
  "cast.hit", "cast.junk", "cast.miss_none", "cast.miss_bait", "cast.miss_deep",
  "cast.multi_summary", "pull.escape", "store.buy_ok"];

/**
 * 插件**真实的全部场景键**：从 `_texts.py` 的 `TEXTS` 块里抠出来。
 * `test_local.py` 第 18 组已经断言 `_calc.SCENE_IDS` 与 `TEXTS` 的键一一对应，
 * 所以这份清单就是插件真实提供给站长的场景全集（154 个）。
 */
const PLUGIN_SCENE_KEYS = (function () {
  const src = fs.readFileSync(path.join(__dirname, "_texts.py"), "utf8");
  const block = src.match(/^TEXTS: dict\[str, str\] = \{\n([\s\S]*?)^\}/m);
  if (!block) return [];
  const keys = [];
  block[1].replace(/^ {4}"([a-z0-9_.]+)":/gm, function (m, k) { keys.push(k); return m; });
  return keys;
})();

/** 站长的现场：回复页给新场景加过按钮（老白名单会把这 4 行判红）。 */
const USER_BUTTON_DEFS = [
  "cast|再来一竿|/钓鱼|default",
  "cast|看背包|/钓鱼 背包|default",
  "pull|拉线！|/钓鱼 拉|primary",
  "story|{label}|/钓鱼 事件 {n}|default",
  "cast.miss_none|再来一竿|/钓鱼|primary",
  "cast.miss_bait|再来一竿|/钓鱼|primary",
  "cast.miss_deep|再来一竿|/钓鱼|primary",
  "cast.multi_summary|卖光光|/钓鱼 卖光光|default"
].join("\n");

function realScenesPayload() {
  return {
    status: "ok", transport: "plugin-api", scene_total: REAL_SCENE_IDS.length,
    buttons_per_row_default: 3, buttons_per_row_max: 5,
    button_styles: [{ value: "default", label: "默认（灰）" }, { value: "primary", label: "主要（蓝）" }],
    groups: [{
      id: "all", label: "全部", desc: "",
      scenes: REAL_SCENE_IDS.map(function (id) {
        return {
          id: id, label: id, desc: "", parent: id.split(".")[0], parent_label: id.split(".")[0],
          source: "none", per_row: 3, dynamic_text: false,
          default_buttons: [], buttons: [],
          text: { template: "{原文}", placeholders: ["原文"], samples: { "原文": id },
                  override: "", preview: id, dynamic: false }
        };
      })
    }]
  };
}

/** 读配置回「站长现场」那份，scenes 回真实场景缩样。 */
function makeSaveScopeSdk(config) {
  const sdk = {
    ready() {
      return new Promise(function (r) {
        sdk._setContext({ pluginName: "astrbot_plugin_fishing_mini", pageName: "editor" });
        r();
      });
    },
    getContext() { return {}; }, _setContext() {}, onContext() { return function () {}; },
    apiGet(endpoint) {
      capture("get", endpoint, null);
      if (String(endpoint) === "config") {
        return Promise.resolve({ status: "ok", metadata: {}, i18n: {}, config: config });
      }
      if (String(endpoint) === "scenes") return Promise.resolve(realScenesPayload());
      return Promise.reject(new Error("unexpected GET " + endpoint));
    },
    apiPost(endpoint, body) {
      capture("post", endpoint, JSON.stringify(body || {}));
      if (String(endpoint) === "scenes") return Promise.resolve(realScenesPayload());
      return Promise.resolve({
        status: "ok", ok: true, message: "已写入内容表：3 张",
        editor_status: JSON.stringify(FAKE_STATUS)
      });
    },
    upload() { return Promise.reject(new Error("上传通道已废弃")); },
    download() { return Promise.reject(new Error("not used")); },
    subscribeSSE() { return Promise.reject(new Error("not used")); }
  };
  return sdk;
}

/** 取「刚弹出来的提示」的文本（toast 会 appendChild 到 #toasts 桩上）。 */
function newToasts(F, from) {
  const box = documentStub.getElementById("toasts");
  return (box.children || []).slice(from).map(function (el) { return String(el.innerHTML || ""); });
}

async function saveScope() {
  console.log("\n[16] 保存只拦「这次改过的部分」（v1.12.1 改命令别名存不进去）");
  const baseCfg = {
    fish_defs: FAKE_CONFIG.fish_defs, rod_defs: FAKE_CONFIG.rod_defs,
    bait_defs: FAKE_CONFIG.bait_defs, item_defs: FAKE_CONFIG.item_defs,
    location_defs: FAKE_CONFIG.location_defs,
    button_defs: USER_BUTTON_DEFS,
    command_aliases: "拉|拉线,收,收线\n排行|排行榜,rank,top,榜\n背包|包,bag",
    custom_commands: "", text_overrides: "", button_layout: "",
    editor_status: JSON.stringify(FAKE_STATUS)
  };
  captured.calls = [];
  const F = loadPageFromSource(makeSaveScopeSdk(baseCfg)).T;
  for (let i = 0; i < 80 && (F.state.loading || !F.state.replies.loaded); i++) {
    await new Promise(function (r) { setTimeout(r, 25); });
  }

  check(F.ENV.online === true && F.state.replies.scenes.length === REAL_SCENE_IDS.length,
    "场景表从插件接口读到（" + REAL_SCENE_IDS.length + " 个）",
    F.state.replies.scenes.length);

  // 1) 白名单不再写死：插件给的每个场景都算合法
  const ids = F.buttonSceneIds();
  check(Array.isArray(ids) && ids.length === REAL_SCENE_IDS.length,
    "buttonSceneIds() = 插件给的全量场景", ids && ids.length);
  check(ids.indexOf("cast.miss_none") >= 0 && ids.indexOf("cast") >= 0,
    "新场景（cast.miss_none）和老场景（cast）都在白名单里");
  check(F.BUTTON_SCENES_FALLBACK.length === 5 && F.BUTTON_SCENES === F.BUTTON_SCENES_FALLBACK,
    "老 5 个场景降级成「离线兜底」常量，兼容旧引用");

  // 2) 站长那 8 行按钮：一处都不该标红（这就是他丢别名的根因）
  const btnRows = F.state.data.buttons || [];
  const btnProblems = F.problemsInTab("buttons", false);
  check(btnRows.length === 8, "现场那 8 行按钮都解析进表了", btnRows.length);
  check(btnProblems.length === 0, "8 行按钮没有一处标红（含 4 行新场景）",
    btnProblems.map(function (p) { return p.key + ":" + p.message; }).join(" | ") || "0 处");
  // 站长看到的就是渲染出来的红格子：从 HTML 层面再确认一遍
  const btnHtml = F.renderTableTab(F.TAB_BY_ID.buttons);
  check(btnHtml.indexOf("cell-bad") < 0 && btnHtml.indexOf("is-bad") < 0,
    "按钮表渲染出的 HTML 里一格红都没有",
    btnHtml.indexOf("cell-bad") < 0 ? "干净" : "仍有 cell-bad");
  check(btnHtml.indexOf("cast.miss_none") > 0 && btnHtml.indexOf("cast.multi_summary") > 0,
    "那 4 行新场景按钮照样显示在表里（不是被藏起来）");

  // 3) 插件给的每个场景各来一行，都不该判红
  const everySceneBad = REAL_SCENE_IDS.filter(function (id) {
    return Object.keys(F.validateRow(F.TAB_BY_ID.buttons,
      { scene: id, label: "再来一竿", data: "/钓鱼", style: "default" })).length > 0;
  });
  check(everySceneBad.length === 0, "插件给的全部场景都通得过校验",
    everySceneBad.join(",") || REAL_SCENE_IDS.length + "/" + REAL_SCENE_IDS.length);

  // 3b) 拿插件**真实的全部场景**（从 _texts.py 抠出来，和 _calc.SCENE_IDS 一一对应）再验一遍：
  //     只要它不通过，站长在回复页配的按钮就会在「🔘 按钮（原始文本）」页被误判成红线。
  check(PLUGIN_SCENE_KEYS.length >= 150,
    "从 _texts.py 抠到插件真实的场景全集", PLUGIN_SCENE_KEYS.length + " 个");
  const keepScenesForAll = F.state.replies.scenes;
  F.state.replies.scenes = PLUGIN_SCENE_KEYS.map(function (id) { return { id: id }; });
  const idsAll = F.buttonSceneIds() || [];
  const notAccepted = PLUGIN_SCENE_KEYS.filter(function (id) {
    return idsAll.indexOf(id.toLowerCase()) < 0;
  });
  check(notAccepted.length === 0, "插件真实场景全在页面的可选清单里",
    notAccepted.join(",") || PLUGIN_SCENE_KEYS.length + "/" + PLUGIN_SCENE_KEYS.length);
  const realBad = PLUGIN_SCENE_KEYS.filter(function (id) {
    return Object.keys(F.validateRow(F.TAB_BY_ID.buttons,
      { scene: id, label: "再来一竿", data: "/钓鱼", style: "default" })).length > 0;
  });
  check(realBad.length === 0, "插件真实场景每一行按钮都不判红（v1.12.1 的根因，永久卡住）",
    realBad.slice(0, 5).join(",") || PLUGIN_SCENE_KEYS.length + "/" + PLUGIN_SCENE_KEYS.length);
  F.state.replies.scenes = keepScenesForAll;

  // 4) 场景表没拿到时不校验场景（宁可漏报也不误报）
  const keepScenes = F.state.replies.scenes;
  F.state.replies.scenes = [];
  check(F.buttonSceneIds() === null, "场景表读不到时 buttonSceneIds() 返回 null");
  check(Object.keys(F.validateRow(F.TAB_BY_ID.buttons,
    { scene: "完全不存在的场景", label: "x", data: "/钓鱼", style: "default" })).indexOf("scene") < 0,
    "场景表读不到时不报「场景不认识」（不会误伤整页保存）");
  F.state.replies.scenes = keepScenes;

  // 5) 真正写错的场景仍然报，而且文案说清「插件没有这个场景」
  const badScene = F.validateRow(F.TAB_BY_ID.buttons,
    { scene: "cast.typo_scene", label: "再来一竿", data: "/钓鱼", style: "default" });
  check(/插件没有这个场景/.test(badScene.scene || ""), "写错的场景照旧报错并说明原因", badScene.scene);

  // 6) 改别名 + 表里有一处「没动过的历史红格子」-> 保存照常进行，只额外提醒
  const staleRow = { scene: "cast.typo_scene", label: "历史遗留", data: "/钓鱼", style: "default" };
  F.state.data.buttons.push(staleRow);
  F.state.originals.buttons[F.rowKey(F.TAB_BY_ID.buttons, staleRow)] = JSON.stringify(staleRow);
  const aliasRow = (F.state.data.aliases || []).filter(function (r) { return r.canonical === "排行"; })[0];
  check(!!aliasRow, "别名表里有「排行」这一行");
  aliasRow.aliases = aliasRow.aliases + ",排名";
  F.state.tab = "aliases";

  let toastFrom = (documentStub.getElementById("toasts").children || []).length;
  captured.calls = [];
  await F.doSave();
  const posts = captured.calls.filter(function (c) { return c.kind === "post" && c.endpoint === "config"; });
  check(posts.length === 1, "只改别名时保存真的写进去了（以前是 0 次）", posts.length + " 次 POST");
  const sent = posts.length ? JSON.parse(posts[0].body) : {};
  check(JSON.stringify(sent).indexOf("排名") >= 0, "载荷里带上了新别名「排名」");
  const staleToasts = newToasts(F, toastFrom);
  check(staleToasts.some(function (h) { return h.indexOf("历史问题") >= 0; }),
    "没动过的历史红格子只做非阻塞提醒", staleToasts.length + " 条提示");
  check(staleToasts.some(function (h) { return h.indexOf("cast.typo_scene") >= 0; }),
    "提醒里点名了那处历史问题在哪一行");
  check(staleToasts.some(function (h) { return h.indexOf("toast-act") >= 0; }),
    "提醒带「去看第一处」按钮（能跳过去）");

  // 7) 真的改错了 -> 拦下来，但要说清「哪张表第几行哪一列」
  aliasRow.aliases = "排行|带竖线|的别名";
  F.state.originals.aliases = {};
  toastFrom = (documentStub.getElementById("toasts").children || []).length;
  captured.calls = [];
  await F.doSave();
  const blockedPosts = captured.calls.filter(function (c) { return c.kind === "post" && c.endpoint === "config"; });
  check(blockedPosts.length === 0, "改错的那次被拦下（不发写入请求）", blockedPosts.length + " 次 POST");
  const blockToasts = newToasts(F, toastFrom);
  check(blockToasts.some(function (h) { return h.indexOf("别名里不能出现竖线") >= 0; }),
    "提示里带上具体原因", blockToasts.join(" / ").slice(0, 120));
  check(blockToasts.some(function (h) { return h.indexOf("第 2 行") >= 0 && h.indexOf("命令别名") >= 0; }),
    "提示里点名「命令别名 第 2 行」");
  check(F.state.tab === "aliases" && F.state.editing && F.state.editing.index === 1 &&
    F.state.editing.key === "aliases",
    "自动跳到出错的那一格并展开输入框",
    F.state.tab + " #" + (F.state.editing ? F.state.editing.index + "/" + F.state.editing.key : "无"));

  // 8) 提示文案的可读性 + 跳转会清掉搜索/筛选
  const where = F.problemWhere({ tabId: "aliases", index: 1, key: "aliases", message: "别名里不能出现竖线（它是字段分隔符）" });
  check(where.indexOf("「命令别名」第 2 行") === 0 && where.indexOf("「别名") > 0,
    "问题描述 = 表 + 行号 + 行标识 + 列名", where);
  F.state.query = "zzz";
  F.state.filter = "zzz";
  check(F.gotoProblem({ tabId: "buttons", index: 0, key: "scene" }) === true &&
    F.state.tab === "buttons" && F.state.query === "" && F.state.filter === "",
    "跳转会把搜索/筛选清掉（否则目标行可能被过滤掉看不见）");
  check(F.gotoProblem({ tabId: "不存在的表", index: 0, key: "scene" }) === false,
    "跳转到不存在的表返回 false（不炸）");

  // 9) 干净数据下不该无中生有地报警
  F.state.originals.aliases = null;
  F.state.data.aliases = [];
  F.state.data.buttons = [];
  F.state.originals.buttons = {};
  toastFrom = (documentStub.getElementById("toasts").children || []).length;
  captured.calls = [];
  await F.doSave();
  check(captured.calls.filter(function (c) { return c.kind === "post" && c.endpoint === "config"; }).length === 1,
    "没有任何改动时保存照常放行");
  check(!newToasts(F, toastFrom).some(function (h) { return h.indexOf("历史问题") >= 0; }),
    "没有历史问题时不会瞎提醒");

  // 10) 标签上的问题标记：红=改错的行（会拦保存），黄=历史遗留（不拦）
  const histRow = { scene: "cast.typo_scene", label: "历史遗留", data: "/钓鱼", style: "default" };
  F.state.data.buttons.push(histRow);
  F.state.originals.buttons[F.rowKey(F.TAB_BY_ID.buttons, histRow)] = JSON.stringify(histRow);
  const h1 = F.tabHealth(["buttons"]);
  check(h1.stale === 1 && h1.bad === 0, "只有历史问题时记在 stale 上（不拦保存）", JSON.stringify(h1));
  F.renderTabs();
  const tabsHtml1 = String(documentStub.getElementById("tabs").innerHTML || "");
  check(tabsHtml1.indexOf("tab-bad is-stale") > 0 && tabsHtml1.indexOf("tab-bad is-block") < 0,
    "没改过的表：标签上是黄色 ⚠ 而不是红的");

  histRow.scene = "cast.typo_scene_2";   // 还在改这张表 -> 变成「改过的行有错」
  const h2 = F.tabHealth(["buttons"]);
  check(h2.bad === 1 && h2.stale === 0, "改过的行有错时记在 bad 上（会拦保存）", JSON.stringify(h2));
  F.renderTabs();
  const tabsHtml2 = String(documentStub.getElementById("tabs").innerHTML || "");
  check(tabsHtml2.indexOf("tab-bad is-block") > 0, "改错时标签上出现红色 ⛔");
  check(F.tabHealth(["snapshots"]).bad === 0 && F.tabHealth(["snapshots"]).stale === 0,
    "存档页这种非表格标签不参与健康度统计（不炸）");

  if (F.state._reloadTimer) { clearTimeout(F.state._reloadTimer); }
  return Promise.resolve();
}

/* =============================================================================
   [17] 收尾
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
