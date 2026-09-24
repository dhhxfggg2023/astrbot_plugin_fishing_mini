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
  // v1.18.25：鱼饵页的「咬钩率」外挂列（读/写 bait_hook_rates）
  "applyExternalColumns", "fillExternalColumns", "parseNamedFloats", "externalColumnState",
  "numberValuesFromPage", "parseEditorStatus", "autobackupFromStatus", "autobackupPayload",
  "sendCommand", "bridgeRequest", "describeError", "resolvePluginBase", "fetchRawConfig",
  "sleep", "waitForStatus", "renderBanner", "downloadSnapshot",
  "refreshSnapshots",
  // 回复按钮（button_defs）表 + v1.12.1「只拦改过的部分」这套定位/跳转
  "rowKey", "isButtonStyle", "BUTTON_SCENES", "BUTTON_SCENES_FALLBACK", "buttonSceneIds",
  "tabHasError", "problemsInTab", "problemWhere", "gotoProblem", "doSave", "tabHealth",
  // 命令别名 / 自定义命令 两张表 + 玩家页（v1.10.0）
  "renderSubTabs", "canonicalCommands", "normalizePlayerRow", "fetchPlayers",
  "fetchSnapshotPlayers", "playerRowsNow", "renderPlayersTab",
  "renderPlayerRow", "runLegacyAction", "closeLegacyPanel", "applyStatus",
  // 道具效果键白名单（v1.11.0：喂鱼 / 手气 / 装饰 三种角色；v1.15.1 加旧写法映射）
  "ITEM_EFFECT_KEYS", "ITEM_EFFECT_KEY_NAMES", "ITEM_EFFECT_HINT", "ITEM_EFFECT_ALIASES",
  "applyEffectKeys",
  // 彩蛋效果键白名单（v1.18.32：easter_egg_defs 第 4 段，另一套解析器，写死 4 个键）
  "EGG_EFFECT_KEYS", "EGG_EFFECT_KEY_NAMES", "EGG_EFFECT_FLAG_KEYS", "EGG_EFFECT_HINT",
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
  "replyFilterScenes", "replyVisibleGroups", "renderReplyMain", "replyStatsHtml",
  "jumpToSceneCard",
  // 滚动位置保留（点一项再点另一项不该跳回顶部）+ 数值页的实时提示
  "scrollAreas", "snapshotScroll", "restoreScroll",
  "numberRowValue", "levelThresholdAt", "levelCurveHint", "numberLiveHint",
  // v1.18.16：全量配置键（每个键都要有编辑入口）+ 「🔎 全部配置键」视图 + 入口行渲染
  "DEMO_NUMBER_VALUES", "demoNumberRows", "demoConfig", "numberEntryOf", "keyEntryLabel",
  "PAGE_VERSION",
  "keyEntryEditable", "KEY_DOCS", "applyKeyDocs", "numberRowFromItem", "configKeyCoverage",
  // v1.18.47：新配置项自动登记（插件下发 config_schema，页面据此现造一行）
  "numberRowFromSchema", "schemaDef",
  "configKeyCoverageNow", "CONFIG_PAYLOAD_EXTRA_KEYS", "keyOverviewRows", "keysFilteredRows",
  "renderKeysTable", "renderKeysSummary", "renderKeysTab", "refreshKeysMain", "gotoConfigKey",
  "copyKeyName", "numberEntryCellHtml", "shortValueText", "numberRowPlan",
  "PANEL_NEVER_WRITABLE",
  // v1.18.16：场景卡片里「加按钮」的选择器（选项来自已有按钮 / 插件认识的指令）
  "BUTTON_LABEL_PRESETS", "BUTTON_COMMAND_PRESETS", "buttonTemplateHint", "usedButtonValues",
  "buttonOptionTag", "buttonOptionGroup", "buttonLabelOptions", "buttonCommandOptions",
  "buttonStyleNumericOptions", "renderReplyDatalists", "replyNewButtonDraft",
  "resetNewButtonDraft", "renderReplyButtonPicker", "replyPickSummaryHtml",
  "refreshReplyPicker", "refreshReplyPickerSummary", "addPickedButton", "renderReplyButtonRow",
  // 动态按钮（v1.18.35）：翻页 / 再次使用的占位符预演
  "isDynamicButtonScene", "renderButtonSample", "DYNAMIC_BUTTON_SCENES", "DYNAMIC_BUTTON_SAMPLES",
  // v1.18.57：玩家数据是**唯一**的改数据入口（实时 / 存档内都在这一页切换）
  "renderPlayerRow", "playersCommand",
  // v1.18.56：玩家存档大编辑器（全字段 + 逐条改鱼 + 原始 JSON）
  "renderPlayerDataTab", "pdState", "pdPlayerOptions", "pdFindField", "pdDraft", "pdSetDraft",
  "pdFieldControl", "pdFishTable", "pdFishRow", "pdEnumOptions", "pdEnumSelect", "loadPlayerFull",
  "submitPlayerFull", "collectPlayerEdits", "collectFishDrafts", "collectNewFish",
  "pdCalcText", "pdRefreshCalc", "pdBumpField",
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
  check(Object.keys(T.TAB_BY_ID).length === 20, "标签页数量 = 20（14 内容表（含大鱼乐）+ 玩家 + 玩家数据 + 命令组 + 💬 回复 + 🔎 全部配置键）",
    Object.keys(T.TAB_BY_ID).join(","));
  check((T.state.data.fish || []).length === 18, "演示鱼池 18 条", (T.state.data.fish || []).length);
  check((T.state.data.locations || []).length === 16, "演示钓点 16 个（离线演示数据，与线上 19 个无关）", (T.state.data.locations || []).length);
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
     v1.17.0：唯一来源就是 `_effects.BUILTIN_EFFECTS` 注册表本身（`_calc._effect_allowed()`
     直接读它，不再靠「启动时同步一份」，因为 main 的 `_expose_globals_all()` 会把
     同步好的副本覆盖回旧的 —— 扩展键和新内置键在真实运行时全都解析不出来）。
     `_calc.EFFECT_ALLOWED` 只剩「单独导入 _calc 时的兜底」，所以它必须是注册表的**前缀**。
     页面里那份清单只是**离线兜底** —— 在线时页面用插件发来的 effect_keys 覆盖它。 */
  const calcSrc = fs.readFileSync(path.join(__dirname, "_calc.py"), "utf8");
  const allowedBlock = calcSrc.match(/EFFECT_ALLOWED[^=]*=\s*\(([\s\S]*?)\)/);
  const fallbackKeys = allowedBlock
    ? allowedBlock[1].split(",").map(s => s.trim().replace(/["']/g, "")).filter(Boolean)
    : [];
  check(fallbackKeys.length >= 8, "从 _calc.py 读到 EFFECT_ALLOWED 兜底白名单",
    fallbackKeys.join("/") || "没读到");
  const fxSrc = fs.readFileSync(path.join(__dirname, "_effects.py"), "utf8");
  const registryKeys = [];
  const specRe = /EffectSpec\(\s*"([A-Za-z_][A-Za-z0-9_]*)"/g;
  let specHit;
  while ((specHit = specRe.exec(fxSrc)) !== null) registryKeys.push(specHit[1]);
  check(registryKeys.slice(0, fallbackKeys.length).join(",") === fallbackKeys.join(","),
    "注册表前 N 项 == _calc 的兜底白名单（历史 8 键在前）",
    "注册表=" + registryKeys.join("/") + " 兜底=" + fallbackKeys.join("/"));
  check(/def _effect_allowed\(/.test(calcSrc) && /sys\.modules\.get\("astrbot_fishing_effects"\)/.test(calcSrc),
    "解析白名单直接读注册表（不再依赖启动时的同步，绕开被覆盖的坑）", "");
  const pageKeys = T.ITEM_EFFECT_KEY_NAMES.filter(k => k !== "quality_up");
  check(pageKeys.slice().sort().join(",") === registryKeys.slice().sort().join(","),
    "页面兜底清单与注册表一致（在线时会被插件发来的清单覆盖）",
    "页面=" + pageKeys.join("/") + " 注册表=" + registryKeys.join("/"));
  /* 顺序也逐项等于注册表（_effects.py 里写明「顺序 = 页面上的展示顺序」）：
     在线时插件下发的 effect_keys 就是这个顺序，兜底清单跟着排，
     效果列表头的 tooltip 与「插件不认识的效果键」报错提示才不会和插件两副面孔。 */
  check(pageKeys.join(",") === registryKeys.join(","),
    "页面兜底清单的顺序逐项等于 _effects.BUILTIN_EFFECTS 的顺序",
    "页面=" + pageKeys.join("/") + " 注册表=" + registryKeys.join("/"));
  check(pageKeys[pageKeys.length - 1] === "quality_floor" &&
    T.ITEM_EFFECT_KEYS[T.ITEM_EFFECT_KEYS.length - 1][0] === "quality_floor" &&
    registryKeys[registryKeys.length - 1] === "quality_floor",
    "新增的 quality_floor 追加在清单末尾（buff_casts 之后，没插进历史 8 键里）",
    "页面末项=" + pageKeys[pageKeys.length - 1] + " 注册表末项=" + registryKeys[registryKeys.length - 1]);
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
  /* v1.18.19 的 buff_casts：潮汐香 / 玉髓灯现在写「buff_quality=0.15;buff_casts=40」这种串，
     新键必须在白名单里，否则道具页会把本来完全合法的一行标红。 */
  const castsRow = { id: "t_casts", name: "潮汐香", emoji: "🕯️", price: 18000, effects: "buff_quality=0.15;buff_casts=40" };
  check(Object.keys(T.validateRow(T.TAB_BY_ID.items, castsRow)).length === 0,
    "buff_casts 是合法效果键（潮汐香 / 玉髓灯那种写法不报错）",
    JSON.stringify(T.validateRow(T.TAB_BY_ID.items, castsRow)));

  /* 彩蛋效果串（easter_egg_defs 第 4 段）走的是**另一套解析器** `_calc._parse_easter_egg_defs`，
     只认写死的 4 个键（扩展加不了）。和道具一样，写错的键插件静默忽略 ——
     v1.18.32 之前这一列**完全没有校验**（表头 hint 写了键名，但填错不标红）。
     下面的功能用例之外，还用「直接抠插件源码」的方式卡住两边不跑偏。 */
  const eggTab = T.TAB_BY_ID.easter_eggs;
  function eggRow(effects) { return { id: "eg", weight: 10, text: "✨ 惊喜", effects: effects }; }
  check(T.EGG_EFFECT_KEY_NAMES.join(",") === "gold,luck,note,heal_bait",
    "彩蛋效果键白名单 = gold / luck / note / heal_bait", T.EGG_EFFECT_KEY_NAMES.join("/"));
  check(T.EGG_EFFECT_FLAG_KEYS.join(",") === "note,heal_bait",
    "note / heal_bait 归为开关型（值不校验）", T.EGG_EFFECT_FLAG_KEYS.join("/"));
  /* 与插件解析器对账：白名单必须逐项等于 _parse_easter_egg_defs 里那些 key in (...) 的字面量。
     改解析器忘了改页面 → 这里当场红（页面那份清单没法从插件接口取，只能靠这条兜）。 */
  const eggFnStart = calcSrc.indexOf("def _parse_easter_egg_defs");
  let eggFn = eggFnStart < 0 ? "" : calcSrc.slice(eggFnStart + 1);
  const eggFnEnd = eggFn.indexOf("\ndef ");
  if (eggFnEnd >= 0) eggFn = eggFn.slice(0, eggFnEnd);
  const pluginEggKeys = [];
  const eggKeyRe = /key in \(([^)]*)\)/g;
  let eggKeyHit;
  while ((eggKeyHit = eggKeyRe.exec(eggFn)) !== null) {
    eggKeyHit[1].split(",").forEach(function (k) {
      const kk = k.trim().replace(/["']/g, "");
      if (kk) pluginEggKeys.push(kk);
    });
  }
  check(pluginEggKeys.slice().sort().join(",") === T.EGG_EFFECT_KEY_NAMES.slice().sort().join(","),
    "页面彩蛋键白名单 == _calc._parse_easter_egg_defs 认的键",
    "页面=" + T.EGG_EFFECT_KEY_NAMES.join("/") + " 插件=" + pluginEggKeys.join("/"));
  check(T.EGG_EFFECT_HINT.indexOf("gold") >= 0 && T.EGG_EFFECT_HINT.indexOf("heal_bait") >= 0,
    "彩蛋效果列的 tooltip 列出了全部键名");

  check(Object.keys(T.validateRow(eggTab, eggRow(""))).length === 0, "彩蛋效果留空不算错");
  check(Object.keys(T.validateRow(eggTab, eggRow("gold=12"))).length === 0, "gold=12 通过");
  check(Object.keys(T.validateRow(eggTab, eggRow("luck=0.08"))).length === 0, "luck=0.08 通过");
  check(Object.keys(T.validateRow(eggTab, eggRow("note=1"))).length === 0, "note=1 通过");
  check(Object.keys(T.validateRow(eggTab, eggRow("heal_bait=1"))).length === 0, "heal_bait=1 通过");
  check(Object.keys(T.validateRow(eggTab, eggRow("gold=12;luck=0.08"))).length === 0,
    "多个效果分号分隔通过");
  /* 出厂那几行的实际写法（含全角分隔符 / 千分位）一个都不能被标红 ——
     `_defs_num` 会先剥掉千分位逗号，校验必须跟着放行。 */
  const eggDefaults = T.state.data.easter_eggs || [];
  const badEggDefaults = eggDefaults.filter(function (r) {
    return Object.keys(T.validateRow(eggTab, r)).length;
  });
  check(eggDefaults.length > 0 && badEggDefaults.length === 0,
    "出厂的 " + eggDefaults.length + " 条彩蛋全部通过校验",
    badEggDefaults.map(function (r) { return r.id; }).join(" "));
  check(Object.keys(T.validateRow(eggTab, eggRow("gold=1,200"))).length === 0,
    "千分位逗号 gold=1,200 通过（插件 _defs_num 会剥掉逗号）");
  check(Object.keys(T.validateRow(eggTab, eggRow("gold=1，200"))).length === 0,
    "全角千分位 gold=1，200 也通过");
  check(Object.keys(T.validateRow(eggTab, eggRow("gold：12；luck：0.08"))).length === 0,
    "全角冒号 / 全角分号通过（解析器 replace 了 `：` 与 `；`）");
  /* 缺等号：插件那边是 str.partition("=")，key = 整段、值 = 空 ——
     开关型照旧算「打开」（不该标红），数字型会取 0（该校验拦住）。 */
  check(Object.keys(T.validateRow(eggTab, eggRow("note"))).length === 0,
    "光写 note（不带 =1）不标红 —— 插件本来就算它打开");
  check(String(T.validateRow(eggTab, eggRow("gold")).effects).indexOf("gold") >= 0,
    "光写 gold（不带值）会被标红");
  check(!!T.validateRow(eggTab, eggRow("gol=12")).effects,
    "拼错的键 gol 被标红（这正是补校验要挡的那种手滑）");
  check(String(T.validateRow(eggTab, eggRow("gol=12")).effects).indexOf("彩蛋") >= 0,
    "报错文案点名是「彩蛋效果键」，不和道具那张表混淆");
  check(String(T.validateRow(eggTab, eggRow("luck=很多")).effects).indexOf("数字") >= 0,
    "非数字的 luck 被标红");
  check(!!T.validateRow(eggTab, eggRow("meat=2")).effects,
    "道具表的键在彩蛋表里会被标红（两套解析器没有混用）");
  check(!!T.validateRow(eggTab, eggRow("luck=0.5;gol=1")).effects,
    "一行里混一个错键也会被标红");

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
  check(tabsHtml.split("tab-count").length - 1 === 19,
    "20 个标签入口都有条目数徽标（14 内容表（含大鱼乐）+ 玩家 + 玩家数据 + 命令组 + 💬 回复 + 🔎 全部配置键）",
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
        check(out.indexOf("snap:players") >= 0 && out.indexOf("看玩家") >= 0,
          "存档卡片上有「看玩家」入口（跳到玩家页的存档模式）");
      } else if (t.kind === "players") {
        const out = T.renderPlayersTab(t);
        check(out.indexOf("<table") >= 0 && out.indexOf("去改数据") >= 0,
          "玩家页渲染出只读表格（每行带「去改数据」入口）", out.length + " 字符");
        check(out.indexOf("p:gold") < 0 && out.indexOf("savePlayerGold") < 0,
          "玩家页不再就地改金币（改数据只剩「🧰 玩家数据」一处）");
        check(out.indexOf("p:mode") >= 0 && out.indexOf("存档内玩家") >= 0 && out.indexOf("实时玩家") >= 0,
          "玩家页有「实时 / 存档内」两种模式");
      } else if (t.kind === "replies") {
        const out = T.renderRepliesTab(t);
        check(out.indexOf("rp-split") >= 0 && out.indexOf("场景卡片") >= 0 && out.indexOf("按钮总览") >= 0,
          "「💬 回复」渲染出卡片视图 + 二级切换", out.length + " 字符");
        check(out.indexOf("rp-side") >= 0 && out.indexOf("👁️ 预览") >= 0,
          "「💬 回复」右侧有常驻预览面板");
      } else if (t.kind === "playerdata") {
        // v1.18.57：玩家数据大编辑器（唯一的改数据入口；没选玩家时给引导，不是白屏）
        const out = T.renderPlayerDataTab(t);
        check(out.indexOf("玩家数据编辑器") >= 0 && out.indexOf("先在上面选一个玩家") >= 0,
          "「🧰 玩家数据」没选玩家时给引导（不是白屏）", out.length + " 字符");
        check(out.indexOf('data-act="pd2:mode"') >= 0 && out.indexOf("存档内玩家") >= 0,
          "「🧰 玩家数据」自己就能切实时 / 存档内（不用再回玩家页）");
      } else if (t.kind === "keys") {
        const out = T.renderKeysTab(t);
        check(out.indexOf("<table") >= 0 && out.indexOf("全部配置键") >= 0,
          "「🔎 全部配置键」渲染出一张全量键表", out.length + " 字符");
        check(out.indexOf("没有入口 <b>0</b>") >= 0 && out.indexOf("一个都不缺") >= 0,
          "这一页自己写明「插件下发的键一个都不缺」");
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
  check(["fish", "locations", "baits", "rods", "items", "collectibles", "titles", "variants", "weather",
    "numbers", "buttons", "aliases", "custom"]
    .every(function (k) { return Array.isArray(payload[k]); }), "载荷包含全部 14 张表");
  check(!!payload.autoBackup && payload.autoBackup.dailyHour === 4, "载荷带上自动备份设置");
  check(Object.keys(payload).length === 16, "载荷字段数 = 16（14 表 + numbers + autoBackup）", Object.keys(payload).length);
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
  }).then(channelHelpers).then(repliesPure).then(configKeysCoverage).then(baitHookColumn)
    .then(wrappingRules)
    .then(buttonPicker).then(channelRoundTrip).then(repliesOnline)
    .then(saveScope).then(legacyRecovery).then(finish);
}

/* =============================================================================
   [13c] 鱼饵页的「咬钩率」列（v1.18.25：外挂列，真正的值存在 bait_hook_rates）
   ============================================================================= */
async function baitHookColumn() {
  console.log("\n[13c] 🪱 鱼饵页的「咬钩率」列（站长：「ui 里面咬钩率还是没修好」）");
  const tab = T.TABS.filter(function (t) { return t.id === "baits"; })[0];
  const col = (tab.columns || []).filter(function (c) { return c.key === "hook"; })[0];
  check(!!col && col.label === "咬钩率" && col.optional === true,
    "鱼饵页多了一列「咬钩率」（留空 = 不动这一项）",
    (tab.columns || []).map(function (c) { return c.label; }).join("/"));
  const ext = (T.TABLE_DEFS.baits || {}).externalMap || {};
  check(ext.column === "hook" && ext.configKey === "bait_hook_rates",
    "这一列声明了外挂键：值存在 bait_hook_rates，不在鱼饵行里", JSON.stringify(ext));

  // 演示态：值是从 bait_hook_rates 那一行读出来的
  const rows = T.state.data.baits || [];
  const byId = {};
  rows.forEach(function (r) { byId[r.id] = r; });
  check(rows.length > 0 && String((byId.none || {}).hook) === "0.25" &&
    String((byId.worm || {}).hook) === "0.70" && String((byId.dragon_bait || {}).hook) === "1.0",
    "每款饵的咬钩率都填进了表格（空钩 0.25 / 蚯蚓 0.70 / 龙涎 1.0）",
    rows.map(function (r) { return r.id + "=" + r.hook; }).join(" "));

  // 改一行 -> 回写进「数值」页那一行（配置键 bait_hook_rates）
  const numRow = (T.state.data.numbers || []).filter(function (r) { return r.key === "bait_hook_rates"; })[0];
  check(!!numRow, "「数值」页里有 bait_hook_rates 这一行（两个界面共用同一份数据）",
    numRow ? String(numRow.value).slice(0, 40) + "…" : "没找到");
  const backup = { hook: byId.worm.hook, text: numRow.value };
  byId.worm.hook = "0.66";
  const touched = T.applyExternalColumns();
  const afterText = numRow.value;
  const beforeMap = T.parseNamedFloats(backup.text).map;
  const afterMap = T.parseNamedFloats(afterText).map;
  const kept = Object.keys(beforeMap).every(function (k) {
    return k === "worm" ? true : Number(afterMap[k]) === Number(beforeMap[k]);
  });
  check(touched.indexOf("bait_hook_rates") >= 0 && afterText.indexOf("worm:0.66") >= 0 && kept &&
    Object.keys(afterMap).length === Object.keys(beforeMap).length,
    "改了蚯蚓的咬钩率 -> 回写 bait_hook_rates，别的条目一个没动", afterText.slice(0, 60) + "…");
  // 百分数写法与「留空不动」
  byId.worm.hook = "70";
  byId.bread.hook = "";
  T.applyExternalColumns();
  check(numRow.value.indexOf("worm:0.7") >= 0 && numRow.value.indexOf("bread:0.58") >= 0,
    "写 70 = 70%；留空的饵保持原值", numRow.value.slice(0, 50) + "…");
  // 校验：填错要拦、留空不报错
  check(!!T.validateRow(tab, { id: "worm", hook: "abc", text: "" }).hook &&
    !!T.validateRow(tab, { id: "worm", hook: "300", text: "" }).hook &&
    !T.validateRow(tab, { id: "worm", hook: "", text: "" }).hook,
    "填字母 / 填 300 会被拦，留空不报错");
  // 还原现场（后面的用例还要用这份演示数据）
  byId.worm.hook = backup.hook;
  byId.bread.hook = T.parseNamedFloats(backup.text).map.bread;
  numRow.value = backup.text;
  return Promise.resolve();
}

/* =============================================================================
   [12] 数据通道的纯函数：行格式、状态解析、白名单过滤
   ============================================================================= */
async function channelHelpers() {
  console.log("\n[12] 数据通道：配置 <-> 表格 的转换");
  check(Object.keys(T.TABLE_DEFS).join(",") === "fish,rods,baits,items,locations,collectibles,variants,weather,easter_eggs,titles,lottery,buttons,aliases,custom",
    "14 张内容表都有解析/序列化定义", Object.keys(T.TABLE_DEFS).join(","));
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
  /* 道具第 7 段 = 解锁等级（v1.18.16 加的那一列） */
  const itemRow = T.TABLE_DEFS.items.parse("feed_basic|普通饲料|🌾|20|喂鱼|meat=2;spirit=1|20");
  check(itemRow.unlock_level === 20 && itemRow.effects === "meat=2;spirit=1"
    && T.TABLE_DEFS.items.serialize(itemRow).indexOf("|20") > 0,
    "道具行 7 段解析正确（含解锁等级），序列化带回去");
  const oldItem = T.TABLE_DEFS.items.parse("feed_basic|普通饲料|🌾|20|喂鱼|meat=2;spirit=1");
  check(oldItem.unlock_level === 1 && T.TABLE_DEFS.items.serialize(oldItem).slice(-2) === "|1",
    "老格式道具（6 段、没有解锁等级）也认，等级兜 1 并补回第 7 段");
  check(T.TABLE_DEFS.items.parse("feed_basic|普通饲料|🌾|20|喂鱼") === null,
    "道具少于 6 段解析为 null（插件解析器也是这个门槛）");

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

  /* ---- v1.18.17：称号表（title_defs，后期金币回收口）---- */
  const titleDef = T.TABLE_DEFS.titles;
  check(!!titleDef && titleDef.configKey === "title_defs" && titleDef.configType === "list",
    "称号表写回 title_defs（字符串数组，与 _editor_bridge.CONTENT_TABLES 的口径一致）");
  const titleRow = titleDef.parse("deep_lord|深海领主|🔱|1200000|海沟以下都归你管");
  check(titleRow.id === "deep_lord" && titleRow.name === "深海领主" && titleRow.emoji === "🔱"
    && titleRow.price === 1200000 && titleRow.desc === "海沟以下都归你管",
    "称号行 5 段解析正确（id|名称|emoji|价格|说明）", JSON.stringify(titleRow));
  check(titleDef.serialize(titleRow) === "deep_lord|深海领主|🔱|1200000|海沟以下都归你管"
    && titleDef.serialize(titleRow).split("|").length === 5,
    "称号行序列化回 5 段（往返不漂）", titleDef.serialize(titleRow));
  const titleNoDesc = titleDef.parse("deep_lord|深海领主|🔱|1200000");
  check(titleNoDesc !== null && titleNoDesc.desc === "" && titleNoDesc.price === 1200000,
    "说明那一段可以省略（省略 = 没有说明，价格照样解析出来）");
  check(titleDef.parse("deep_lord|深海领主|🔱") === null
    && titleDef.parse("|深海领主|🔱|1200000|说明") === null
    && titleDef.parse("deep_lord||🔱|1200000|说明") === null,
    "缺段 / 缺 id / 缺名称的残缺行解析成 null（保存时不会悄悄改写）");
  const titleTab = T.TAB_BY_ID.titles;
  check(!!titleTab && titleTab.keyField === "id" && titleTab.columns.length === 5 &&
    titleTab.columns.filter(function (c) { return c.valueKey; })[0].key === "price",
    "「🏷 称号」标签页就位，「价格」是排序/批量改价用的 valueKey 列");
  check(titleTab.sortOptions.map(function (p) { return p[0]; }).join(",") === "default,name,valueAsc,valueDesc",
    "称号页的排序只认现成的 sorters（价格两栏走 valueAsc / valueDesc）",
    titleTab.sortOptions.map(function (p) { return p[0]; }).join(","));
  const keepSort = T.state.sort, keepTabId = T.state.tab;
  T.state.tab = "titles";
  T.state.sort = "valueAsc";
  const byPrice = T.visibleRows(titleTab).map(function (it) { return it.row.price; });
  T.state.sort = keepSort;
  T.state.tab = keepTabId;
  check(byPrice.length === 6 && byPrice[0] === 0 && byPrice[5] === 5000000,
    "按价格排序真的读到了 price（valueKey 列配上了，不是读空的 value 字段）", byPrice.join(" < "));
  const titleNew = titleTab.makeRow();
  check(titleNew.id.indexOf("new_title_") === 0 && titleNew.price === 10000,
    "「新增一行」给的是 new_title_xxx + 价格 10000", JSON.stringify(titleNew));
  check((T.state.data.titles || []).length === 6,
    "离线演示也带 6 个称号样例（首屏就能看到长什么样）", (T.state.data.titles || []).length);

  check(T.canonicalCommands().length === 29 && T.canonicalCommands().indexOf("背包") >= 0
    && T.canonicalCommands().indexOf("道具") >= 0 && T.canonicalCommands().indexOf("鱼饵") >= 0,
    "页面知道 29 个规范子命令（v1.18.13 商店拆成三家 + 买；离线用演示清单，在线以插件回写的为准）",
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

  /* ---- v1.10.0 / v1.18.57：玩家页（只读列表 + 跳去玩家数据页）---- */
  check((T.state.playersList || []).length === 4, "离线演示 4 名玩家", (T.state.playersList || []).length);
  check(T.playerRowsNow().length === 4 && T.state.playersMode === "live", "玩家页默认看「实时玩家」");
  const playerHtml = T.renderPlayersTab(T.TAB_BY_ID.players);
  check(playerHtml.indexOf("10001") > 0 && playerHtml.indexOf("12,800") > 0,
    "玩家表渲染出真实金币（千分位格式化）");
  check(playerHtml.indexOf('data-act="p:toData"') > 0, "每行都有「🧰 去改数据」按钮");
  check(playerHtml.indexOf("p:gold") < 0 && playerHtml.indexOf("savePlayerGold") < 0,
    "玩家页没有就地改金币的控件了（改数据只剩「🧰 玩家数据」一处）");

  // 序列化
  const serialized = T.serializeContentTables();
  check(Object.keys(serialized).join(",") === "fish_defs,rod_defs,bait_defs,item_defs,location_defs,collectible_defs,variant_defs,weather_defs,easter_egg_defs,title_defs,lottery_prizes,button_defs,command_aliases,custom_commands",
    "序列化输出 14 张配置表", Object.keys(serialized).join(","));
  check(typeof serialized.fish_defs === "string"
    && serialized.fish_defs.split("\n").length === T.state.data.fish.length,
    "fish_defs 的行数 = 当前表格行数（这里是 " + T.state.data.fish.length + " 行）",
    serialized.fish_defs.split("\n").length);
  check(Array.isArray(serialized.rod_defs)
    && serialized.rod_defs.length === T.state.data.rods.length,
    "rod_defs 的行数 = 当前表格行数（这里是 " + T.state.data.rods.length + " 行）",
    serialized.rod_defs.length);
  check(serialized.rod_defs[1].split("|").length === 8,
    "老鱼竿序列化成 8 段（没有拉线手感就不补那两段，免得噪音）");
  const rodWithFeel = serialized.rod_defs.filter(function (r) {
    return r.split("|").length === 10;
  });
  check(rodWithFeel.length === 2
    && rodWithFeel[0].indexOf("|0.15|1") > 0,
    "带拉线手感的鱼竿序列化成 10 段（窗口加成 / 逃脱率系数）",
    rodWithFeel.join(" ／ "));
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

  // 文本行（逗号/竖线分隔的名单）：不能当数字处理，也不能被判「需要是数字」
  const kwRow = { key: "fish_value_overrides", label: "单条鱼改价", value: "", unit: "鱼:id=价", text: true };
  T.state.data.numbers = [kwRow];
  const kwPayload = T.numberValuesFromPage({ numbers_editable: ["fish_value_overrides"] });
  check(Object.keys(kwPayload).length === 0,
    "文本行留空 = 不提交（插件侧「留空」等于不改动这一项）", JSON.stringify(kwPayload));
  kwRow.value = "锦鲤:3000,鲲:25000";
  check(T.numberValuesFromPage({ numbers_editable: ["fish_value_overrides"] }).fish_value_overrides === "锦鲤:3000,鲲:25000",
    "文本行提交的是字符串，不是 NaN/0");
  check(Object.keys(T.validateRow(T.TAB_BY_ID.numbers, kwRow)).length === 0,
    "文本行不会被判「需要是数字」", JSON.stringify(T.validateRow(T.TAB_BY_ID.numbers, kwRow)));
  check(Object.keys(T.validateRow(T.TAB_BY_ID.numbers, { key: "stamina_max", value: "乱写" })).length === 1,
    "同一张表的数字行照样会拦（文本行没把校验放松）");

  /* ---- 点一个项再点另一个，不许跳回最上面（站长报的） ---- */
  console.log("  ── 📜 滚动位置：整页重绘要保住 ──");
  documentStub.scrollingElement = { scrollTop: 640, scrollLeft: 12 };
  const snapScroll = T.snapshotScroll();
  check(snapScroll.length === 1 && snapScroll[0].top === 640 && snapScroll[0].left === 12,
    "snapshotScroll 记下窗口滚动位置", JSON.stringify(snapScroll));
  documentStub.scrollingElement.scrollTop = 0;      // 模拟重绘把位置冲掉
  documentStub.scrollingElement.scrollLeft = 0;
  T.restoreScroll(snapScroll);
  check(documentStub.scrollingElement.scrollTop === 640
    && documentStub.scrollingElement.scrollLeft === 12,
    "restoreScroll 把位置写回去（重绘后不跳回顶部）",
    String(documentStub.scrollingElement.scrollTop));
  check(/function render\(keepScroll\)[\s\S]{0,200}?snapshotScroll\(\)/.test(html)
    && /restoreScroll\(snap\);\s*\n\s*renderStatusBar\(\);/.test(html),
    "render() 自己会先记位置、渲染完再写回");
  check(/render\(false\);/.test(html) && /换标签页从顶上开始看/.test(html),
    "切标签页是明确要求回顶部的（render(false)）");
  check(/var wrapTop = wrap \? wrap\.scrollTop : 0;/.test(html),
    "搜索只换表格内容时也保住表格内的滚动位置");
  delete documentStub.scrollingElement;

  /* ---- 升级二次曲线：给推荐值 + 按当前数值实时算 ---- */
  console.log("  ── 📈 数值页：算给你看（升级曲线） ──");
  const savedNumbers = T.state.data.numbers;
  T.state.data.numbers = [
    { key: "level_xp_base", label: "升级曲线基准", value: 5, unit: "竿", text: false },
    { key: "level_xp_ratio", label: "等级曲线底数", value: 1.08, unit: "倍", text: false },
    { key: "level_xp_growth", label: "升级曲线二次项", value: 0, unit: "系数", text: false }
  ];
  check(T.numberRowValue("level_xp_ratio") === 1.08, "numberRowValue 读得到当前输入值");
  check(T.levelThresholdAt(30, 5, 1.08, 0) === 520 && T.levelThresholdAt(45, 5, 1.08, 0) === 1785,
    "页面算的等级门槛与插件 _level_threshold 一致（30 级 520 / 45 级 1785）");
  const growthHint = T.numberLiveHint({ key: "level_xp_growth" });
  check(growthHint.indexOf("num-hint") > 0 && growthHint.indexOf("10 级 <b>62</b>") > 0
    && growthHint.indexOf("30 级 <b>520</b>") > 0,
    "二次项那行写出了当前曲线（10 级 62 条 / 30 级 520 条）");
  const flatHint = growthHint.replace(/[,\s]/g, "");
  check(flatHint.indexOf("推荐：<b>0</b>") > 0 && flatHint.indexOf("0.5") > 0
    && flatHint.indexOf("940") > 0 && flatHint.indexOf("2202") > 0,
    "给了推荐值，并且把「填 0.5 / 2 会变成多少条」算出来（940 / 2202）");
  check(growthHint.indexOf("平方") > 0 && growthHint.indexOf("(L−1)²") > 0,
    "讲清了它只加在平方项上（第 L 级多加 growth×(L−1)² 条）");
  T.state.data.numbers[2].value = 0.5;
  const grownHint = T.numberLiveHint({ key: "level_xp_growth" });
  check(/10 级 \+\s*41\s*条/.test(grownHint) && /30 级 \+\s*421\s*条/.test(grownHint)
    && grownHint.indexOf("30 级 <b>940</b>") > 0,
    "把 growth 改成 0.5，提示立刻跟着变（每级多加 41 / 421 条，30 级门槛 940）",
    grownHint.slice(0, 80));
  T.state.data.numbers[2].value = 0;
  const baseHint = T.numberLiveHint({ key: "level_xp_base" });
  check(baseHint.indexOf("num-hint") > 0 && baseHint.indexOf("推荐") < 0,
    "基准/底数两行也显示当前曲线（不用重复讲推荐值）");
  check(T.numberLiveHint({ key: "stamina_max" }) === "",
    "其它数值行不乱加提示（不刷屏）");
  const numTab = T.renderTableTab(T.TAB_BY_ID.numbers);
  check(numTab.indexOf("num-hint") > 0 && numTab.indexOf("算给你看") < 0,
    "提示真的渲染到数值页上了", numTab.length);
  check(/\.num-hint \{[\s\S]{0,160}?color: var\(--muted\)/.test(html),
    "提示样式低调（小字灰色，不抢眼）");
  T.state.data.numbers = savedNumbers;

  /* ---- 长列表不再一行拉到底（站长报的「各钓点咬钩系数把表格拉长了」）---- */
  const longRow = {
    key: "location_hook_factors", label: "各钓点咬钩系数", unit: "钓点:倍", text: true,
    desc: "乘在鱼饵咬钩率上",
    value: "novice:1.0,pond:1.05,lake:1.1,river:1.15,bay:1.2,reef:1.3,deep:1.4,abyss:1.5"
  };
  T.state.data.numbers = [longRow];
  T.state.editing = null;
  const chipHtml = T.renderTableTab(T.TAB_BY_ID.numbers);
  check(chipHtml.indexOf('class="kv-chips"') > 0,
    "长列表显示成小方块容器（可换行）");
  check((chipHtml.match(/<span class="tag">/g) || []).length === 8,
    "8 个条目拆成 8 个小方块（不是一行 8 个逗号）",
    (chipHtml.match(/<span class="tag">/g) || []).length);
  check(chipHtml.indexOf("novice:1.0") > 0 && chipHtml.indexOf("abyss:1.5") > 0,
    "每一条都看得见");
  check(/\.kv-chips \{[\s\S]{0,200}?max-width: 360px/.test(html),
    "小方块有 max-width（表格不会被它撑出去）");
  T.state.data.numbers = [kwRow];
  check(T.renderTableTab(T.TAB_BY_ID.numbers).indexOf('class="kv-chips"') < 0,
    "只有 3 个短条目的文本行还是老样子（不乱加方块）");

  // 点开编辑：多行输入框，一行一条
  T.state.data.numbers = [longRow];
  T.state.tab = "numbers";
  T.startEdit(0, "value");
  const editHtml = T.renderTableTab(T.TAB_BY_ID.numbers);
  check(editHtml.indexOf("is-multiline") > 0 && editHtml.indexOf("<textarea") > 0,
    "编辑长列表用的是多行输入框（textarea）");
  check(editHtml.indexOf("novice:1.0\npond:1.05") > 0,
    "一行一条（换行也是插件认的分隔符）-> " + /novice:1\.0\s*\n/.test(editHtml));
  T.cancelEdit();
  T.state.data.numbers = before;

  /* ---- 神话：洗髓丹那组数值也要「算给你看」 ---- */
  console.log("  ── 🔱 数值页：洗髓丹 / 神话概率 ──");
  const numKeys = T.NUMBER_KEYS.map(function (x) { return x[0]; });
  check(numKeys.indexOf("reroll_daily_limit") >= 0 && numKeys.indexOf("quality_myth_chance") >= 0,
    "「洗髓丹每日上限」「洗髓出神话的概率」都在数值页里");
  check(String(T.NUMBER_KEYS.filter(function (x) { return x[0] === "quality_weights"; })[0][2])
    .indexOf("6") === 0,
    "个体品质权重那行标成「6 个数」");
  const beforeMyth = T.state.data.numbers;
  T.state.data.numbers = [
    { key: "quality_myth_chance", label: "洗髓出神话的概率", value: 0.0025, unit: "每次重掷", text: false },
    { key: "reroll_daily_limit", label: "洗髓丹每日上限", value: 3, unit: "颗/鱼", text: false }
  ];
  const mythHint = T.numberLiveHint({ key: "quality_myth_chance" });
  check(mythHint.indexOf("num-hint") > 0 && mythHint.indexOf("0.25%") > 0
    && mythHint.indexOf("0.75%") > 0,
    "提示算出了「一次重掷 0.25% / 一颗丹 0.75%」", mythHint.slice(0, 70));
  check(/大约 <b>[\d,]+<\/b> 颗丹/.test(mythHint) && mythHint.indexOf("一半概率") > 0,
    "还给出「大概多少颗丹 / 多少天能有一半概率出神话」");
  check(T.numberLiveHint({ key: "reroll_daily_limit" }).indexOf("num-hint") > 0,
    "每日上限那一行也显示同一份计算结果");
  T.state.data.numbers = [
    { key: "quality_myth_chance", label: "洗髓出神话的概率", value: 0, unit: "每次重掷", text: false },
    { key: "reroll_daily_limit", label: "洗髓丹每日上限", value: 3, unit: "颗/鱼", text: false }
  ];
  check(T.numberLiveHint({ key: "quality_myth_chance" }).indexOf("永远洗不出神品") > 0,
    "概率填 0 时提示「永远洗不出神品」");
  T.state.data.numbers = beforeMyth;

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
  T.state.replies.query = "";

  /* ---- 窄窗口不再横着切：表格在窄屏改成「一行一张卡」 ---- */
  console.log("  ── 🖼️ 窄窗口布局：表格拆卡片 ----");
  check(/@media \(max-width: 1180px\)/.test(html), "页面里有窄窗口断点（1180px）");
  check(/@media \(max-width: 1400px\) \{[\s\S]{0,200}?\.rp-split \{ flex-direction: column; \}/.test(html),
    "回复页在 1400px 以下就把预览挪到下面（不硬挤左边那半张表）");
  check(/table\.grid\.stack thead \{ display: none; \}/.test(html),
    "窄屏下隐藏表头（改成每格自己带标签）");
  check(/content: attr\(data-label\)/.test(html), "卡片模式用 data-label 当每格的标签");
  check(/\.table-wrap\.is-stack \{ max-height: none/.test(html),
    "卡片模式取消表格内部限高（整页滚动，不再小框里套小框）");
  check(/max-height: max\(360px/.test(html), "表格限高带下限（窗口矮也不会被压成一条缝）");
  check(/\.statusbar \{[\s\S]{0,400}?flex-wrap: wrap/.test(html),
    "底部状态栏允许换行（窄窗口不再把右边信息挤出屏幕）");
  check(/minmax\(240px, 1fr\)/.test(html) && /minmax\(170px, 1fr\)/.test(html),
    "存档卡片 / 表单栅格的最小宽度调小（窄容器也能放下）");
  check(/\.rp-side \{[\s\S]{0,240}?max-height: calc\(100vh - 118px\)[\s\S]{0,80}?overflow: auto/.test(html),
    "预览面板自己高了就在面板内滚（下半截不会顶在屏幕外够不着）");
  check(/@media \(max-width: 820px\) \{[\s\S]{0,600}?\.sticky-head \{ position: static; \}/.test(html),
    "很窄时顶栏不再吸顶（它换行后高度估不准，容易压住内容）");

  const fishTabHtml = T.renderTableTab(T.TAB_BY_ID.fish);
  check(fishTabHtml.indexOf('class="grid stack') > 0 && fishTabHtml.indexOf("is-stack") > 0,
    "内容表带上了 stack 标记");
  check(fishTabHtml.indexOf('data-label="') > 0, "内容表的每个格子都带 data-label");
  const numTabHtml = T.renderTableTab(T.TAB_BY_ID.numbers);
  check(numTabHtml.indexOf('data-label="当前值"') > 0,
    "数值页「当前值」格子有标签");

  /* ---- 「在场景里加按钮」：总览里的 ＋ 要跳到那张卡片 ---- */
  console.log("  ── ✍️ 场景按钮：总览只做检查，改按钮跳回卡片 ──");
  T.state.replies.view = "overview";
  T.state.replies.sel = {};
  const overviewHtml = T.renderReplyOverview();
  check(overviewHtml.indexOf('data-jump="card"') > 0,
    "总览里的「＋ 加按钮」带上了跳转标记（点了会去场景卡片）");
  check(overviewHtml.indexOf("要改某个场景的按钮文案和指令") > 0,
    "总览顶部写明「改按钮请到卡片里」");
  const cardHtml = T.renderReplyCard(T.replySceneById("cast.hit"));
  check(cardHtml.indexOf("只改这个场景") > 0,
    "场景卡片里写明「在这里加/改 = 只改这个场景」");
  check(cardHtml.indexOf('data-act="rp:btnAdd"') > 0, "场景卡片里有「＋ 加按钮」");

  /* ---- v1.18.35：动态按钮（翻页 / 再次使用）----
     这三个场景的按钮占位符由代码填，所以校验要先按样例值预演，否则 `{指令} {页}`
     会被「指令要以 / 开头」标红；样例表必须和插件侧 _calc.DYNAMIC_BUTTON_SAMPLES 一致。 */
  console.log("  ── 📄 动态按钮：占位符预演 + 场景专属校验 ──");
  check(T.DYNAMIC_BUTTON_SCENES.join(",") === "page.prev,page.next,item.again",
    "动态按钮场景 = page.prev / page.next / item.again", T.DYNAMIC_BUTTON_SCENES.join("/"));
  check(T.renderButtonSample("{指令} {页}") === "/钓鱼 背包 2"
    && T.renderButtonSample("/钓鱼 用 {道具} {参数}") === "/钓鱼 用 锦鲤玉佩 1",
    "占位符按样例值预演（给站长看的就是这条真指令）", T.renderButtonSample("{指令} {页}"));
  check(T.isDynamicButtonScene("page.next") && T.isDynamicButtonScene(" PAGE.PREV ")
    && !T.isDynamicButtonScene("cast.hit"),
    "只有这三个场景算动态按钮场景");
  check(Object.keys(T.validateReplyButton(
    { label: "下一页", data: "{指令} {页}", style: "default" }, "page.next")).length === 0,
    "动态场景里 `{指令} {页}` 不算错");
  check(!!T.validateReplyButton(
    { label: "下一页", data: "{指令} {页}", style: "default" }, "cast.hit").data,
    "同一个模板写在普通场景上照样标红（那边没人填占位符 = 死按钮）");
  check(!!T.validateReplyButton(
    { label: "x", data: "这不是指令", style: "default" }, "page.next").data,
    "动态场景里真写错的指令也拦得住");
  check(T.applyCommandPrefix("{指令} {页}", "/钓鱼") === "{指令} {页}",
    "批量改指令前缀会跳过模板行（不会把 {指令} 拼成 /钓鱼 {指令}）");

  const calcSrcForDyn = fs.readFileSync(path.join(__dirname, "_calc.py"), "utf8");
  const samplesBlock = /DYNAMIC_BUTTON_SAMPLES: dict\[str, str\] = \{(.*?)\}/s.exec(calcSrcForDyn);
  check(!!samplesBlock, "能抠到插件侧的 DYNAMIC_BUTTON_SAMPLES");
  const pluginSamples = {};
  String(samplesBlock ? samplesBlock[1] : "").split("\n").forEach(function (line) {
    const m = /"([^"]+)"\s*:\s*"([^"]*)"/.exec(line);
    if (m) pluginSamples[m[1]] = m[2];
  });
  check(JSON.stringify(pluginSamples) === JSON.stringify(T.DYNAMIC_BUTTON_SAMPLES),
    "占位符样例表与插件逐字一致（改一边忘一边这里就红）", JSON.stringify(pluginSamples));
  const scenesBlock = /DYNAMIC_BUTTON_SCENES: tuple\[str, \.\.\.\] = \((.*?)\)/s.exec(calcSrcForDyn);
  const pluginScenes = String(scenesBlock ? scenesBlock[1] : "").match(/"([^"]+)"/g) || [];
  check(pluginScenes.map(function (s) { return s.slice(1, -1); }).join(",")
    === T.DYNAMIC_BUTTON_SCENES.join(","),
    "动态场景清单也和插件一致",
    pluginScenes.join(",") || "没抠到");

  const btnOnlyScene = T.normalizeReplyScene({
    id: "page.next", label: "「下一页」按钮", desc: "只在不是最后一页时出现",
    button_only: true, source: "config", per_row: 4,
    buttons: [["下一页", "{指令} {页}", 0]],
    text: { template: "{原文}", placeholders: ["原文"], samples: { "原文": "" } }
  });
  check(btnOnlyScene.button_only === true, "scenes 接口的 button_only 传进了页面模型");
  const btnOnlyHtml = T.renderReplyCard(btnOnlyScene);
  check(btnOnlyHtml.indexOf("文案模板") < 0 && btnOnlyHtml.indexOf("没有回复文案") > 0,
    "动态按钮场景的卡片不显示文案编辑器（那场景本来就没有回复文案）");
  check(T.renderReplyCard(T.replySceneById("cast.hit")).indexOf("文案模板") > 0,
    "普通场景的卡片照旧有文案编辑器");

  T.state.replies.query = "zzz";
  T.state.replies.collapsed = { cast: true };
  T.state.replies.previewOpen = false;
  T.jumpToSceneCard("cast.hit");
  check(T.state.replies.view === "cards" && T.state.replies.focus === "cast.hit"
    && T.state.replies.query === "" && T.state.replies.collapsed.cast === false
    && T.state.replies.previewOpen === true,
    "跳到场景卡片时：切回卡片视图、清搜索、展开分组、开预览",
    JSON.stringify({ v: T.state.replies.view, q: T.state.replies.query,
                     c: T.state.replies.collapsed.cast }));
  T.state.replies.view = "cards";

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
   [13b] 🔎 全部配置键：插件配置里的每一个键都要有编辑入口（v1.18.16）
   ============================================================================= */
async function configKeysCoverage() {
  console.log("\n[13b] 🔎 全部配置键：一个都不能漏");
  /* 唯一真相是插件的 _conf_schema.json：页面那份清单只能一一对应，不能少 */
  const schema = JSON.parse(fs.readFileSync(path.join(__dirname, "_conf_schema.json"), "utf8"));
  const schemaKeys = Object.keys(schema);
  check(schemaKeys.length === 143, "配置 schema 里是 143 个键", schemaKeys.length);

  const pageKeys = T.NUMBER_KEYS.map(function (x) { return x[0]; });
  check(pageKeys.indexOf("hostile_keywords") < 0,
    "NUMBER_KEYS 里已经没有 hostile_keywords（v1.18.17 狠角色那套设定整体删除）");
  const newKeys = ["order_level_growth", "order_factor_max", "auto_supply_bait", "auto_equip_bait",
    "auto_supply_buff", "title_defs", "offering_price", "offering_hours",
    "offering_income_bonus", "offering_luck_bonus"];
  const missingNew = newKeys.filter(function (k) { return pageKeys.indexOf(k) < 0; });
  check(missingNew.length === 0, "v1.18.17 新增的 10 个键全都登记在 NUMBER_KEYS 里",
    missingNew.join(",") || "10/10 都在");
  /* v1.18.18 的 4 个「每日额度」键：登记 + 入口是 ""（数值页直接改）+ 分组跟着同族键走 */
  const dailyKeys = ["buff_daily_cast_limit", "hot_soup_daily_limit",
    "reroll_daily_total", "offering_daily_limit"];
  const missingDaily = dailyKeys.filter(function (k) { return pageKeys.indexOf(k) < 0; });
  check(missingDaily.length === 0, "v1.18.18 新增的 4 个「每日额度」键全都登记在 NUMBER_KEYS 里",
    missingDaily.join(",") || "4/4 都在");
  /* v1.18.22 的品质曲线旋钮：登记 + 演示值与 schema 默认一致 */
  const curveKeys = ["luck_weight_step", "luck_cap"];
  const missingCurve = curveKeys.filter(function (k) { return pageKeys.indexOf(k) < 0; });
  check(missingCurve.length === 0, "v1.18.22 新增的 2 个「品质曲线」键全都登记在 NUMBER_KEYS 里",
    missingCurve.join(",") || "2/2 都在");
  check(curveKeys.every(function (k) {
    return JSON.stringify(T.DEMO_NUMBER_VALUES[k]) === JSON.stringify(schema[k].default);
  }), "两个曲线旋钮的演示值与 schema 默认一致",
    curveKeys.map(function (k) { return k + "=" + T.DEMO_NUMBER_VALUES[k]; }).join(" "));
  const dailyItemOf = function (k) {
    return T.NUMBER_KEYS.filter(function (x) { return x[0] === k; })[0];
  };
  const dailyEntryBad = dailyKeys.filter(function (k) {
    return T.numberEntryOf(dailyItemOf(k)) !== "";
  });
  check(dailyEntryBad.length === 0, "这 4 个键的入口都是 \"\"（数值页给输入框，不是跳转 / 只读行）",
    dailyEntryBad.join(",") || "4/4 都是本页可改");
  const dailyGroups = {};
  dailyKeys.forEach(function (k) { dailyGroups[k] = T.numberRowFromItem(dailyItemOf(k), undefined).group; });
  check(dailyGroups.buff_daily_cast_limit === "基础" && dailyGroups.hot_soup_daily_limit === "基础" &&
    dailyGroups.reroll_daily_total === "稀有度" && dailyGroups.offering_daily_limit === "水族馆",
    "这 4 个键跟同族键待在「基础 / 稀有度 / 水族馆」三组里", JSON.stringify(dailyGroups));
  /* v1.18.19 的 escape_difficulty_weight（难度对逃脱率的权重）：登记 + 入口 ""（数值页直接改）
     + 分组跟着同族的拉线键走 + 说明讲清后果。 */
  const edwKey = "escape_difficulty_weight";
  const edwItem = T.NUMBER_KEYS.filter(function (x) { return x[0] === edwKey; })[0];
  check(!!edwItem, "v1.18.19 新增的 escape_difficulty_weight 登记在 NUMBER_KEYS 里",
    edwItem ? edwItem.join(" / ") : "没找到 " + edwKey);
  const edwRow = edwItem ? T.numberRowFromItem(edwItem, schema[edwKey].default) : {};
  check(!!edwItem && T.numberEntryOf(edwItem) === "" && edwRow.entry === "",
    "它的入口是 \"\"（数值页直接改，不是跳转 / 只读行）", String(edwRow.entry));
  check(edwRow.group === "拉线",
    "它跟 window_min / sweet_spot_width / perfect_escape_factor 一起待在「拉线」组", String(edwRow.group));
  check(String(edwRow.desc || "").length >= 30,
    "说明是「讲清后果」的长句（默认 = 历史曲线、填 0 = 难度不再缩放逃脱率）",
    String(edwRow.desc || "").length + " 字");
  const missingPage = schemaKeys.filter(function (k) { return pageKeys.indexOf(k) < 0; });
  const extraPage = pageKeys.filter(function (k) { return schemaKeys.indexOf(k) < 0; });
  check(missingPage.length === 0, "schema 里的键页面上全部登记了（没有遗漏）",
    missingPage.join(",") || "0 个遗漏");
  check(extraPage.length === 0, "页面没有登记 schema 里不存在的键（清理过的旧键）",
    extraPage.join(",") || "0 个多余");
  check(new Set(pageKeys).size === pageKeys.length, "页面清单里没有重复的键",
    pageKeys.length + " 个键 / " + new Set(pageKeys).size + " 个唯一");

  /* 单个数值（int/float/bool）的键**都不能被登记成文本行**。
     文本行的值是按字符串提交的（row.text = true -> 输入框不与 Number() 打交道），
     插件侧 _coerce_number 按数字校验，会把整批 save_numbers 一起拒掉 ——
     站长看到的就是「配置面板保存不了数据」（v1.18.33 的两行：
     luck_weight_step / luck_cap 就标成了文本行，两行都是单个数值，不是列表）。
     列表型（quality_weights 这种）本来就该是文本行，所以只卡 int/float/bool。 */
  const textNumberRows = schemaKeys.filter(function (k) {
    const t = String((schema[k] || {}).type || "");
    if (t !== "int" && t !== "float" && t !== "bool") return false;
    const item = T.NUMBER_KEYS.filter(function (x) { return x[0] === k; })[0];
    return !!item && !!item[4];
  });
  check(textNumberRows.length === 0,
    "int/float/bool 的键没有一个被当成文本行（文本行按字符串提交，插件会整批拒收）",
    textNumberRows.join(",") || "0 个");

  /* 兜底函数：插件下发了页面不认识的键要能当场抓出来 */
  const cov = T.configKeyCoverage(schemaKeys);
  check(cov.missing.length === 0, "configKeyCoverage：没有一个键是「没有入口」的",
    cov.missing.join(",") || "0 个");
  check(cov.badEntry.length === 0, "所有 tab: 入口都指向真实存在的标签页",
    cov.badEntry.join(",") || "0 个");
  check(cov.counts.total === 143 &&
    cov.counts.numbers + cov.counts.tab + cov.counts.panel === 143,
    "143 个键全都有归属（数值页 / 别的页 / 插件面板）", JSON.stringify(cov.counts));

  /* 每个键都要有中文名 + 一句「这个键是干什么的」 */
  const noDoc = T.NUMBER_KEYS.filter(function (item) {
    const row = T.numberRowFromItem(item, undefined);
    return !String(row.label || "").trim() || !String(row.desc || "").trim();
  });
  check(noDoc.length === 0, "每个键都有中文名 + 作用说明",
    noDoc.map(function (x) { return x[0]; }).join(",") || "143/143 都有");
  const longDoc = T.NUMBER_KEYS.filter(function (item) {
    return String(T.numberRowFromItem(item, undefined).desc || "").length >= 30;
  });
  check(longDoc.length >= 80, "绝大多数说明是「讲清后果」的长句（不是复述键名）",
    longDoc.length + "/143 条 ≥30 字");

  /* 入口指向：内容表 / 回复 / 命令 / 存档 都要落在真的能改的那一页 */
  const expectTab = {
    fish_defs: "fish", rod_defs: "rods", bait_defs: "baits", item_defs: "items",
    location_defs: "locations", collectible_defs: "collectibles", variant_defs: "variants",
    weather_defs: "weather", easter_egg_defs: "easter_eggs", title_defs: "titles",
    button_defs: "replies", text_overrides: "replies", button_layout: "replies",
    button_style_mode: "replies", button_default_style: "replies", button_empty_scenes: "replies",
    command_aliases: "aliases", custom_commands: "custom",
    enable_auto_backup: "snapshots", backup_daily_hour: "snapshots",
    backup_interval_hours: "snapshots", backup_keep_daily: "snapshots",
    backup_keep_interval: "snapshots", backup_keep_manual: "snapshots"
  };
  const wrongJump = Object.keys(expectTab).filter(function (k) {
    return cov.entries[k] !== ("tab:" + expectTab[k]);
  });
  check(wrongJump.length === 0, "24 个「在别的页改」的键都指向正确的标签页（点得到）",
    wrongJump.length ? wrongJump.map(function (k) { return k + "=" + cov.entries[k]; }).join(" ") : "24/24 正确");
  check(T.keyEntryLabel("tab:fish").indexOf("鱼池") > 0 &&
    T.keyEntryLabel("tab:snapshots").indexOf("存档") > 0 &&
    T.keyEntryLabel("panel").indexOf("只读") > 0,
    "入口那一列写的是人话（「🐟 鱼池」页 / 插件面板（页面只读））");
  check(T.keyEntryEditable("") === true && T.keyEntryEditable("tab:fish") === true &&
    T.keyEntryEditable("panel") === false,
    "「能不能在页面上改」判定正确（panel 只能在插件配置面板里改）");

  /* 插件面板只读的那几个：必须是**已知的那 7 个**（多一个就说明漏了入口）
     —— v1.18.16 把 backup_dir 放开成可改（存档目录就是个普通路径配置），
     剩下 7 个分别是「没有上传通道的文件字段」和「数据操作 / 页面自己的状态」。 */
  const panelKeys = schemaKeys.filter(function (k) { return cov.entries[k] === "panel"; }).sort();
  check(panelKeys.join(",") ===
    "backup_export_file,backup_import_file,data_action,data_confirm,data_status,data_target,editor_status",
    "插件面板只读的键正好是这 7 个（Python 侧再放开白名单时这里跟着改）", panelKeys.join(","));

  /* 数值页：131 个键铺成 131 行，入口行给跳转按钮、只读行不给输入框 */
  const saved = T.state.data.numbers;
  T.state.data.numbers = T.demoNumberRows();
  const numKeys = T.state.data.numbers.map(function (r) { return r.key; });
  check(numKeys.length === 143 && schemaKeys.every(function (k) { return numKeys.indexOf(k) >= 0; }),
    "数值页按全量清单铺开 143 行（一行都没少）", numKeys.length + " 行");
  const blank = T.state.data.numbers.filter(function (r) {
    return !r.entry && (r.value === "" || r.value === null || r.value === undefined);
  }).map(function (r) { return r.key; }).sort();
  check(blank.join(",") === "backup_dir,config_fingerprint,content_tables_hint,fish_value_overrides,lottery_force_prize,user_edited_keys",
    "演示态里没有空白行（这 6 个键的插件默认值本来就是空串 / 空表：存档目录留空 = 用插件目录下的 backups/；lottery_force_prize 留空 = 不强制奖级）",
    blank.join(","));
  const jumpRow = T.state.data.numbers.filter(function (r) { return r.key === "fish_defs"; })[0];
  check(T.numberEntryCellHtml(jumpRow).indexOf('data-act="num:jump"') > 0 &&
    T.numberEntryCellHtml(jumpRow).indexOf("<input") < 0,
    "内容表那一行只给一个可点的「→ 到 XX 页改」按钮（不给假的输入框）");
  const panelRow = T.state.data.numbers.filter(function (r) { return r.key === "editor_status"; })[0];
  check(T.numberEntryCellHtml(panelRow).indexOf("插件面板项") > 0 &&
    T.numberEntryCellHtml(panelRow).length < 400,
    "只读行写明「插件面板项」并且把几 KB 的 JSON 截短显示（不撑破表格）");
  check(panelRow.value === "" && T.numberEntryCellHtml(panelRow).indexOf("NaN") < 0 &&
    T.numberEntryCellHtml(panelRow).indexOf(">0<") < 0,
    "空值显示成「—」而不是 0（numberRowFromItem 不做无脑 Number()）",
    JSON.stringify(panelRow.value));
  const jsonRow = { key: "editor_status", label: "状态", value: '{"updated_at":1,"snapshots":[' + "x".repeat(300) + "]}",
    text: false, group: "数据面板", entry: "panel", entryLabel: "插件面板（页面只读）" };
  const jsonCell = T.numberEntryCellHtml(jsonRow);
  check(jsonCell.indexOf("共 " + jsonRow.value.length + " 字") > 0 && jsonCell.indexOf("NaN") < 0,
    "长 JSON 只显示开头 + 总字数（不会把表格撑爆）", jsonCell.length + " 字符");
  check(T.numberValuesFromPage(null).editor_status === undefined,
    "只读行永远不会被提交（白名单拿不到时也一样）");
  T.state.data.numbers = saved;

  /* 「🔎 全部配置键」视图：搜得动、说得清、缺了会红脸 */
  const keysHtml = T.renderKeysTab(T.TAB_BY_ID.keys);
  check(keysHtml.indexOf("没有入口 <b>0</b>") > 0 && keysHtml.indexOf("一个都不缺") > 0,
    "视图自己报「插件下发的键一个都不缺」");
  check(keysHtml.indexOf("quality_myth_chance") > 0 && keysHtml.indexOf("洗髓出神品概率") > 0,
    "表里按「配置键 + 中文名 + 说明 + 入口」列出每个键");
  T.state.keysQuery = "洗髓";
  const filtered = T.keysFilteredRows();
  check(filtered.length >= 1 && filtered.every(function (r) {
    return (r.key + r.label + r.desc + r.group).indexOf("洗髓") >= 0;
  }), "按键名 / 中文名 / 说明搜得动", filtered.length + " 条命中");
  T.state.keysQuery = "quality_myth_chance";
  check(T.keysFilteredRows().length === 1 &&
    T.keysFilteredRows()[0].key === "quality_myth_chance",
    "按配置键精确搜索只留那一行");
  T.state.keysQuery = "";
  check(T.keysFilteredRows().length === 143, "清空搜索词 -> 又看到全部 143 个键");
  check(keysHtml.indexOf('data-act="key:query"') > 0 &&
    keysHtml.indexOf('data-act="key:query"') < keysHtml.indexOf('id="keysMain"'),
    "搜索框在 #keysMain 外面（局部重绘表格时不会把输入焦点踢掉）");
  check(T.renderKeysTable([]).indexOf("没有匹配的配置键") > 0 &&
    T.renderKeysTable([]).indexOf("筛出 <b>0</b>") > 0,
    "搜不到时有空状态 + 计数（不是只剩一张空表）");

  /* 白名单是最终权威：不在里面 -> 只读；放行了 -> 连「插件面板项」也跟着能改 */
  const dirItem = T.NUMBER_KEYS.filter(function (x) { return x[0] === "backup_dir"; })[0];
  const dirClosed = T.numberRowPlan(dirItem, "D:/bak", ["stamina_max"]);
  check(dirClosed.entry === "" && dirClosed.writable === false &&
    T.numberEntryCellHtml(dirClosed).indexOf("<input") < 0,
    "插件没放行的键一律只读（backup_dir 不白名单里时也不给输入框）");
  const dirOpen = T.numberRowPlan(dirItem, "D:/bak", ["backup_dir", "stamina_max"]);
  check(dirOpen.entry === "" && dirOpen.writable === true,
    "v1.18.16 插件放行后，存档目录在页面上直接可改");
  const actItem = T.NUMBER_KEYS.filter(function (x) { return x[0] === "data_action"; })[0];
  const actOpen = T.numberRowPlan(actItem, "无", ["data_action"]);
  check(actOpen.entry === "" && actOpen.writable === true && actOpen.openedByPlugin === true,
    "将来插件放行某个「插件面板」键时，页面不用改一行就跟着能改（白名单优先）");
  const statusItem = T.NUMBER_KEYS.filter(function (x) { return x[0] === "editor_status"; })[0];
  check(T.numberRowPlan(statusItem, "{}", ["editor_status"]).writable === false,
    "页面自己的通道（editor_status）就算被放行也永远只读（免得读到过期状态）");
  const stamItem = T.NUMBER_KEYS.filter(function (x) { return x[0] === "stamina_max"; })[0];
  const stamClosed = T.numberRowPlan(stamItem, 20, ["fish_value_mult"]);
  check(stamClosed.writable === false && stamClosed.readonlyWhy.indexOf("白名单") > 0,
    "普通数值键不在白名单里时也不给输入框，并写明原因");

  /* 反例：插件加了新键、页面没跟上时必须红着脸列出来（正常情况这里是 0） */  const badCov = T.configKeyCoverage(schemaKeys.concat(["brand_new_plugin_key"]));
  check(badCov.missing.join(",") === "brand_new_plugin_key",
    "插件多下发一个键就会被抓成「没有入口」", badCov.missing.join(","));
  const savedKeys = T.state.configKeys;
  T.state.configKeys = schemaKeys.concat(["brand_new_plugin_key"]);
  const redHtml = T.renderKeysTab(T.TAB_BY_ID.keys);
  check(redHtml.indexOf("页面还不认识的配置键") > 0 &&
    redHtml.indexOf("brand_new_plugin_key") > 0,
    "视图上真的会出红条点名（不是只在函数里算）");
  check(redHtml.indexOf("没有入口 <b>1</b>") > 0, "汇总条上的「没有入口」跟着变成 1");
  T.state.configKeys = savedKeys;

  /* ---- v1.18.57：玩家数据页 = 唯一的改数据入口（实时 / 存档内都在这切换）----
     站长的原话：「你怎么玩家和玩家数据两个界面都可以改数据，合并成一个啊」。
     所以这里同时验：模式条、存档模式、中文标签（不能再露代码 id）。 */
  T.state.playerData = null;
  T.state.playersList = [
    { user_id: "10001", name: "钓鱼佬", gold: 12800, level: 3, caught: 9, sold: 2, fish: 1, aquarium: 0, saved_text: "" },
  ];
  T.state.snapshotPick = "2026-09-18_1820.json";
  T.state.snapshots = [
    { name: "2026-09-18_1820.json", note: "手动" },
    { name: "auto/2026-09-19_0700.json", note: "自动" },
  ];
  T.state.tab = "playerdata";
  T.state.pluginVersion = "";
  const pdTab = T.TAB_BY_ID.playerdata;
  let pds = T.pdState();
  pds.user_id = "10001";
  pds.loaded = true;
  pds.enums = {
    fish: [{ id: "crucian", name: "🦈 鲫鱼" }],
    quality: [{ id: "凡品", name: "⚪凡品" }, { id: "珍品", name: "🏆珍品" }],
    variants: [{ id: "rainbow", name: "🌈 虹彩" }],
    baits: [{ id: "worm", name: "🪱 蚯蚓" }],
  };
  pds.groups = [
    { title: "基础", items: [
      { key: "gold", label: "金币", kind: "count", value: 12800, desc: "主货币" },
      { key: "stamina", label: "体力", kind: "count", value: -1, desc: "" },
      { key: "baits", label: "鱼饵", kind: "map", value: { worm: 3 }, desc: "", enum: "baits" },
      { key: "equipped_bait", label: "当前鱼饵", kind: "text", value: "worm", desc: "", enum: "baits" },
      { key: "inventory", label: "背包（鱼）", kind: "fish_list", value: [
        { fish_id: "crucian", quality: "珍品", variant: "rainbow", attrs: { meat: 10, spirit: 20, sheen: 30 }, feed_uses: 1, value: 500 },
      ], desc: "逐条可改" },
    ] },
  ];
  const pdHtml = T.renderPlayerDataTab(pdTab);
  check(pdHtml.indexOf('data-act="pd2:mode"') > 0 && pdHtml.indexOf("🟢 实时玩家") > 0 &&
    pdHtml.indexOf("💾 存档内玩家") > 0,
    "玩家数据页自己带「实时 / 存档内」切换（合并后不用再回玩家页切）", pdHtml.length + " 字符");
  check(pdHtml.indexOf("pd2:snapPick") < 0, "「实时玩家」模式下不显示存档下拉（少一步误操作）");
  check(pdHtml.indexOf('data-pd2="基础|gold"') > 0 && pdHtml.indexOf("金币") > 0,
    "插件下发的字段渲染成控件，并带中文名");
  check(pdHtml.indexOf("蚯蚓") > 0 && pdHtml.indexOf("data-pd2item") > 0,
    "计数表/枚举都用中文名显示（不再只写 worm 这种 id）");
  check(pdHtml.indexOf("12,800") > 0, "「现在是多少」显示真实值（千分位）");
  /* v1.18.58/60：站长「只有加上和减去按钮，哪里输入数值呢」——
     数字类字段必须**一眼看出在哪填**：改动方式标签 + 输入框 + 结果预览。 */
  check(pdHtml.indexOf('<span class="pd-tag">数字</span>') > 0 &&
    pdHtml.indexOf('type="number" step="1" data-pd2="基础|gold"') > 0,
    "数字字段带「数字」标签 + 数字输入框（不再让人找不到在哪填）");
  check(pdHtml.indexOf('class="hint pd-calc" data-calc="基础|gold"') > 0,
    "每个数字字段都带结果预览（填完就知道会变成多少）");
  check(T.pdCalcText(12800, "500", "set") === "改后 = 500（现在 12,800，改成）" &&
    T.pdCalcText(12800, "-500", "add") === "改后 = 12,300（现在 12,800，减 500）" &&
    T.pdCalcText(100, "-500", "add") === "改后 = 0（现在 100，减 500）" &&
    T.pdCalcText(12800, "", "set").indexOf("框里填个数") > 0,
    "结果预览按「改成 / 加减」分别算（减到 0 为止、空着会提示）",
    T.pdCalcText(12800, "-500", "add"));
  /* 站长「金币的计算怎么算错了」—— 大数一样要对（千分位 + 结果在最前面） */
  check(T.pdCalcText(991589567, "1", "set") === "改后 = 1（现在 991,589,567，改成）" &&
    T.pdCalcText(991589567, "1000", "add") === "改后 = 991,590,567（现在 991,589,567，加 1,000）",
    "大数（9.9 亿）加减与千分位都对", T.pdCalcText(991589567, "1000", "add"));
  /* v1.18.58：站长反馈「改数据不好改啊，怎么是一堆的挤在一起」——
     控件必须各自成行、表头列宽收敛、分组不要再套内部滚动（那会让它变成一个小窗口）。 */
  check(pdHtml.indexOf('class="pd-line"') > 0 && pdHtml.indexOf('class="grid pd-card"') > 0 &&
    pdHtml.indexOf('class="pd-name"') > 0,
    "字段用「一个字段一张卡」渲染（任何宽度都不再压成一条缝）");
  /* 站长：「你这个怎么只有金币好改」—— 每种字段都得看得见怎么填（v1.18.60 加改动方式标签） */
  check(pdHtml.indexOf('<span class="pd-tag">数字</span>') > 0 &&
    pdHtml.indexOf('<span class="pd-tag">文字表</span>') > 0 &&
    pdHtml.indexOf('<span class="pd-tag">下拉选择</span>') > 0,
    "数字 / 文字表 / 下拉选择 三种字段都带改动方式标签（不只金币能改）");
  check(pdHtml.indexOf('data-act="pd2:bump"') > 0 && pdHtml.indexOf('data-pd2item="基础|baits"') > 0,
    "数字字段有快改按钮、计数表字段有「只改一项」入口");
  /* v1.18.61：玩家数据页也做版本对账（「只有金币好改 / 算错了」多半是缓存了旧页面） */
  T.state.pluginVersion = T.PAGE_VERSION;
  check(T.renderPlayerDataTab(pdTab).indexOf("插件 <b>" + T.PAGE_VERSION + "</b>") > 0,
    "玩家数据页顶部显示插件 / 页面版本（不一致会提示 Ctrl+F5）");
  T.state.pluginVersion = "";
  check(/class="table-wrap is-stack pd-plain/.test(pdHtml) && pdHtml.indexOf('class="mini-head"') > 0,
    "卡片模式 + 分组小标题 + 不再套内部滚动");
  check(pdHtml.indexOf('data-act="pd2:bump" data-key="基础|gold" data-mult="-1"') > 0 &&
    pdHtml.indexOf('data-mult="10"') > 0,
    "数字字段有「＋1 / −1 / ＋10 / −10」快改按钮（不用打字也能改）");
  /* 快改按钮真的改到草稿上：没填倍数 = ±1；框里填了数字 = 按那个数加减；
     减到负数夹成 0（和插件侧口径一致）。 */
  T.pdState().drafts = {};
  T.pdBumpField("基础|gold", 1);
  check(T.pdState().drafts["基础|gold"] === "12801",
    "点「＋1」= 当前值 +1 记进草稿", JSON.stringify(T.pdState().drafts["基础|gold"]));
  T.pdBumpField("基础|gold", 10);
  check(T.pdState().drafts["基础|gold"] === "12811", "再点「＋10」= 再 +10");
  T.pdBumpField("基础|gold", -1);
  T.pdBumpField("基础|gold", -1);
  check(T.pdState().drafts["基础|gold"] === "12809", "点「−1」两次 = 退回 12809");
  const savedQSBump = documentStub.querySelector;
  documentStub.querySelector = function (sel) {
    if (String(sel).indexOf('data-pd2="基础|gold"') >= 0) {
      return { value: "1000", getAttribute: function () { return "基础|gold"; } };
    }
    return savedQSBump.apply(documentStub, arguments);
  };
  T.pdBumpField("基础|gold", 1);
  check(T.pdState().drafts["基础|gold"] === "13809",
    "框里先填 1000，再点「＋1」就是加 1000（框里的数字当倍数用）",
    JSON.stringify(T.pdState().drafts["基础|gold"]));
  documentStub.querySelector = savedQSBump;
  T.pdState().drafts = {};
  // 逐条改鱼：鱼种/品质/异色下拉里只能是中文，而且**不能**是 [object Object]
  const fishRowHtml = T.pdFishRow("inventory", 0, pds.groups[0].items[4].value[0]);
  check(fishRowHtml.indexOf("[object Object]") < 0,
    "鱼表的下拉不会把枚举对象渲染成 [object Object]（v1.18.57 修）");
  check(fishRowHtml.indexOf('value="珍品"') > 0 && fishRowHtml.indexOf("珍品") > 0,
    "品质下拉：显示中文、提交 id");
  check(fishRowHtml.indexOf('value="rainbow"') > 0 && fishRowHtml.indexOf("虹彩") > 0,
    "异色下拉：显示中文、提交 id");
  // 切到「存档内玩家」：要出现存档下拉 + 写明改的是存档文件
  pds.mode2 = "snapshot";
  pds.snapshot = "2026-09-18_1820.json";
  const snapHtml = T.renderPlayerDataTab(pdTab);
  check(snapHtml.indexOf("pd2:snapPick") > 0 && snapHtml.indexOf("2026-09-18_1820.json") > 0,
    "存档模式下列出可选的存档");
  check(snapHtml.indexOf("恢复这份存档") > 0 && snapHtml.indexOf("实时数据一行都不动") > 0,
    "存档模式明确写出「要恢复这份存档才生效」（不会误以为已经生效）");
  // 存档模式但没选档：不能拿实时数据显示（宁可空着）
  pds.snapshot = "";
  const noSnapHtml = T.renderPlayerDataTab(pdTab);
  check(noSnapHtml.indexOf("先在上面选一份存档") > 0 && noSnapHtml.indexOf("data-pd2=") < 0,
    "存档模式没选档时不显示任何编辑控件（避免看着像在改实时数据）");
  pds.mode2 = "live";
  // 保存入口 / 未保存计数
  pds.drafts = {};
  pds.fishEdit = {};
  pds.dirty = 0;
  check(pdHtml.indexOf('data-act="pd2:save"') > 0 && pdHtml.indexOf('data-act="pd2:reload"') > 0,
    "页面有「保存修改」和「重读」两个入口");
  T.pdSetDraft("基础", "gold", "13000");
  check(T.pdState().dirty === 1, "改了金币 -> 未保存计数 = 1", String(T.pdState().dirty));
  check(T.collectPlayerEdits().length === 1 && T.collectPlayerEdits()[0].key === "gold",
    "草稿能收集成插件认识的 edits", JSON.stringify(T.collectPlayerEdits()));
  pds.drafts = {};
  pds.fishEdit = {};
  pds.dirty = 0;
  // 日志：玩家数据页是自己出问题也不能白屏（安全口径要写在页面上）
  const safetyHtml = T.renderPlayerDataTab(pdTab);
  check(safetyHtml.indexOf("整批校验") > 0 && safetyHtml.indexOf("改前自动存一份档") > 0 &&
    safetyHtml.indexOf("重算估值") > 0,
    "页面上写清了安全口径（整批校验 / 改前自动存档 / 重算估值）");
  // 行按钮：每行只有一个「去改数据」入口
  const rowHtml = T.renderPlayerRow(
    { user_id: "10001", name: "钓鱼佬", gold: 500, level: 3, caught: 9, sold: 2, fish: 1, aquarium: 0, saved_text: "" },
    true);
  check(rowHtml.indexOf('data-act="p:toData"') > 0 && rowHtml.indexOf("去改数据") > 0,
    "玩家列表每行有「🧰 去改数据」按钮（带着玩家跳到玩家数据页）");
  check(rowHtml.indexOf('data-act="p:gold"') < 0 && rowHtml.indexOf("改金币") < 0,
    "玩家列表不再有就地改金币的控件");
  /* v1.18.54：玩家页把「插件版本 vs 页面版本」并排显示 ——
     「点了没反应」最常见的原因是浏览器缓存了旧页面，这里一眼能看出来。 */
  T.state.pluginVersion = T.PAGE_VERSION;
  const verHtml = T.renderPlayersTab(T.TAB_BY_ID.players);
  check(verHtml.indexOf("插件 <b>" + T.PAGE_VERSION + "</b>") > 0 &&
    verHtml.indexOf("页面 <b>" + T.PAGE_VERSION + "</b>") > 0,
    "玩家页显示插件版本与页面版本（一致时打勾）");
  T.state.pluginVersion = "v1.18.40";
  const verHtml2 = T.renderPlayersTab(T.TAB_BY_ID.players);
  check(verHtml2.indexOf("Ctrl+F5") > 0 && verHtml2.indexOf("不一致") > 0,
    "两版不一致时明确提示「按 Ctrl+F5 强制刷新」");
  T.state.pluginVersion = "";
  T.state.playerData = null;

  /* v1.18.57：改玩家数据只剩「🧰 玩家数据」一处（玩家页只读），所以读取失败的
     「点了没反应」防护搬到这里测：请求被拒必须把原因写在页面上，不能静默卡住。 */
  {
    const L = loadPageFromSource(makeFakeSdk()).T;
    const loadCase = function (uid) {
      L.state.playerData = null;
      return L.loadPlayerFull(uid, "");
    };
    // ① 后端不认这个动作（旧版插件）：错误信封 -> 页面上写清楚
    captured.playersFail = function () {
      return Promise.reject({
        response: { status: 400, data: { status: "error",
          message: "不认识的玩家动作「player_full_get」（可用：list、set_gold）" } },
        message: "Request failed with status code 400",
      });
    };
    let threw = null;
    try { await loadCase("10001"); } catch (e) { threw = e; }
    check(threw === null, "请求被拒不会把异常抛出去（抛出去 = 点了没反应）",
      threw ? String(threw.message) : "无抛出");
    const lst = L.pdState();
    check(lst.loaded === true && String(lst.error).indexOf("player_full_get") >= 0,
      "后端不认这个动作时，原因写在页面上（不是静默卡住）",
      "error=" + JSON.stringify(lst.error) + " loaded=" + lst.loaded);
    const errHtml = L.renderPlayerDataTab(L.TAB_BY_ID.playerdata);
    check(errHtml.indexOf("读不到就改不了") > 0,
      "页面上还给了「怎么办」（重启插件 / 刷新重试）");
    // ② 恢复真桩：成功路径要把插件下发的字段渲染成控件（端到端）
    captured.playersFail = null;
    await loadCase("10001");
    const okHtml = L.renderPlayerDataTab(L.TAB_BY_ID.playerdata);
    /* ⚠️ 必须现取 pdState()：loadPlayerFull 成功后会 fire-and-forget 地刷新玩家列表
       （fetchPlayers(...).then(render)），那次 render 会重建面板状态对象，
       上面拿着的 lst 引用可能已经过期（测试第一次就是这么误报的）。 */
    const after = L.pdState();
    check(after.error === "" && okHtml.indexOf('data-pd2="') > 0,
      "恢复正常后：拿到插件下发的字段并渲染出控件（端到端）",
      "error=" + JSON.stringify(after.error) + " len=" + okHtml.length);
    // ③ 提交失败要有提示（不能静默）
    captured.playersFail = function () {
      return Promise.reject({ response: { status: 500, data: { message: "数据库正忙" } },
        message: "Request failed with status code 500" });
    };
    let subThrew = null;
    let sub = null;
    try { sub = await L.submitPlayerFull([{ key: "gold", value: 100 }]); }
    catch (e) { subThrew = e; }
    check(subThrew === null && sub && sub.ok === false && String(sub.message).length > 0,
      "提交失败也不抛异常，而是返回失败 + 给站长一句能看懂的原因",
      subThrew ? String(subThrew.message) : JSON.stringify(sub && sub.message));
    captured.playersFail = null;
    L.state.playerData = null;
  }

  /* v1.18.57：连点两次「🗑 删」不能算出错的 index（第二次请求会对着上一次的列表算）
     —— 写请求期间 busy=true，第二个请求根本不发出去。 */
  {
    const R = loadPageFromSource(makeFakeSdk()).T;
    R.state.playerData = null;
    const rst = R.pdState();
    rst.user_id = "10001";
    rst.loaded = true;
    captured.calls = [];
    const p1 = R.submitPlayerFull([{ key: "inventory", op: "remove", index: 1 }]);
    const second = R.submitPlayerFull([{ key: "inventory", op: "remove", index: 1 }]);
    await p1;
    const secondRes = await second;
    const writes = captured.calls.filter(function (c) {
      return c.kind === "post" && JSON.parse(c.body).action === "player_full_set";
    });
    check(writes.length === 1,
      "写请求在进行中时，第二次提交直接被拦下（只发出去一条）", "发出 " + writes.length + " 条");
    check(secondRes && secondRes.ok === false && /稍等|处理/.test(secondRes.message),
      "被拦下的那次给一句人话（不是静默丢掉）", JSON.stringify(secondRes && secondRes.message));
    check(R.pdState().busy === false, "一轮结束后 busy 归位（不会卡住页面）");
    R.state.playerData = null;
  }

  /* ---- v1.18.57：老「改金币 / 小面板」那份代码必须**真的删干净** ----
     站长原话：「你怎么玩家和玩家数据两个界面都可以改数据，合并成一个啊」。
     留着旧函数 = 两套改数据路径，早晚又分叉；这里直接按源码断言。 */
  {
    const src = html;   // 页面源码（本文件开头已经读进来了）
    const deadNames = ["renderPlayerDataPanel", "playerDataEditsFromPage", "openPlayerData",
      "submitPlayerData", "savePlayerGold", "goldEdit", "goldConfirm", "dataEdit",
      "p:goldInput", "p:goldYes", "p:gold"];
    const alive = deadNames.filter(function (n) { return src.indexOf(n) >= 0; });
    check(alive.length === 0, "旧的小面板 / 改金币代码已经删干净（不留第二套改数据路径）",
      "还残留：" + alive.join("、"));
    const bothTabs = (src.match(/id: "playerdata"/g) || []).length;
    check(bothTabs === 1, "「玩家数据」页在标签里只登记一次", "登记了 " + bothTabs + " 次");
  }

  /* ---- v1.18.56：玩家存档大编辑器（全字段 + 逐条改鱼 + 原始 JSON）----
     字段清单由插件下发（player_full_get 的 groups + enums），页面只渲染。 */
  {
    const st = T.pdState();
    st.user_id = "10001";
    st.name = "钓鱼佬";
    st.loaded = true;
    st.error = "";
    st.enums = {
      fish: ["carp", "koi", "kun"], baits: ["worm", "abyss_bait"], items: ["jade_lantern"],
      rods: ["bamboo", "void_rod"], locations: ["novice", "lake"], titles: ["tycoon"],
      variants: ["golden", "prismatic"], achievements: ["catch_10"], weather: ["sunny"],
      collectibles: ["seaweed"], quality: ["凡品", "良品", "精品", "珍品", "绝品", "神品"],
    };
    st.groups = [
      { title: "基础", items: [
        { key: "user_id", label: "玩家 ID", kind: "readonly", desc: "主键", value: "10001" },
        { key: "gold", label: "金币", kind: "count", desc: "主货币", value: 1000 },
        { key: "lottery_loses", label: "连输", kind: "count", desc: "", value: 0 },
        { key: "luck_charges", label: "手气储备", kind: "ratio", desc: "", value: 0 },
        { key: "current_location", label: "当前钓点", kind: "text", desc: "", value: "novice", enum: "locations" },
      ] },
      { title: "资产", items: [
        { key: "baits", label: "鱼饵", kind: "map", desc: "", value: { worm: 5 }, enum: "baits" },
        { key: "rods", label: "鱼竿", kind: "str_list", desc: "", value: ["bamboo"], enum: "rods" },
        { key: "story", label: "连载进度", kind: "json", desc: "", value: { arc: "", ep: 0 } },
        { key: "inventory", label: "背包（鱼）", kind: "fish_list", desc: "",
          value: [{ fish_id: "carp", quality: "精品", variant: "", value: 120,
                    attrs: { meat: 60, spirit: 55, sheen: 50 }, feed_uses: 1, locked: false }] },
      ] },
    ];
    st.drafts = {};
    st.fishEdit = {};
    st.dirty = 0;
    st.raw_json = '{\n "gold": 1000\n}';
    st.rawOpen = false;

    const bigHtml = T.renderPlayerDataTab(T.TAB_BY_ID.playerdata);
    check(bigHtml.indexOf("玩家数据编辑器") > 0 && bigHtml.indexOf("基础（") > 0,
      "大编辑器渲染出分组与字段", bigHtml.length + " 字符");
    check(bigHtml.indexOf('data-pd2="基础|gold"') > 0
      && bigHtml.indexOf('data-pd2add="基础|gold"') > 0,
      "计数类字段给「输入框 + 设为/加减」两个控件");
    check(bigHtml.indexOf('data-pd2="基础|current_location"') > 0
      && bigHtml.indexOf('<option value="lake"') > 0,
      "有枚举的文本字段渲染成下拉（钓点）");
    check(bigHtml.indexOf('data-pd2="资产|baits"') > 0
      && bigHtml.indexOf('data-pd2item="资产|baits"') > 0,
      "计数表给「整表输入 + 加/删一项」");
    check(bigHtml.indexOf('data-pd2="资产|story"') > 0 && bigHtml.indexOf("<textarea") > 0,
      "JSON 字段渲染成可换行的文本域");
    check(bigHtml.indexOf("原始 JSON（高级）") > 0 && bigHtml.indexOf("展开整份存档 JSON") > 0,
      "有「原始 JSON」高级折叠区（默认收起）");
    check(bigHtml.indexOf("🐟") > 0 && bigHtml.indexOf('data-fish="inventory|0"') > 0,
      "鱼单独成表，每条鱼一行、控件都带 data-fish");
    check(bigHtml.indexOf('data-new="inventory"') > 0 && bigHtml.indexOf("＋ 加一条") > 0,
      "鱼表最后一行是「新增一条」（鱼种/品质/异色/三维）");
    check(bigHtml.indexOf("改前自动存一份档") > 0 && bigHtml.indexOf("整批校验") > 0,
      "页面上写明安全口径（整批校验 + 改前自动存档）");
    // readonly 字段不给输入框
    const roItem = T.pdFindField("基础", "user_id");
    check(roItem && T.pdFieldControl("基础", roItem).control.indexOf("<input") < 0,
      "只读字段（玩家 ID）不给输入框");

    // 草稿：改了才计入 dirty，改回原值就清掉
    T.pdSetDraft("基础", "gold", "2000");
    check(T.pdState().dirty === 1, "改一个字段 -> dirty=1", T.pdState().dirty);
    check(T.renderPlayerDataTab(T.TAB_BY_ID.playerdata).indexOf("1 处未保存") > 0,
      "页面上显示「N 处未保存」");
    T.pdSetDraft("基础", "gold", "1000");
    check(T.pdState().dirty === 0, "改回原值 -> dirty 归零（不会白提交）");
    // 折进 fishEdit 的草稿也算 dirty
    T.pdState().fishEdit = { "inventory|0": { quality: "神品", recalc: true } };
    check(T.renderPlayerDataTab(T.TAB_BY_ID.playerdata).indexOf("1 处未保存") > 0,
      "改鱼也算未保存");
    T.pdState().fishEdit = {};

    // 收集：草稿 -> edits（页面与插件之间的契约）
    T.pdSetDraft("基础", "gold", "2000");
    T.pdSetDraft("基础", "current_location", "lake");
    T.pdSetDraft("资产|".replace("|", ""), "story", '{"arc":"a","ep":2}');
    T.pdState().fishEdit = { "inventory|0": { quality: "神品", recalc: true } };
    const savedQSA2 = documentStub.querySelectorAll;
    documentStub.querySelectorAll = function (sel) {
      const s = String(sel);
      if (s.indexOf("[data-pd2item]") >= 0) return [];
      return [];
    };
    const collected = T.collectPlayerEdits();
    documentStub.querySelectorAll = savedQSA2;
    const byKey = {};
    collected.forEach(function (e) { byKey[e.key] = e; });
    check(byKey["gold"] && byKey["gold"].value === 2000 && byKey["gold"].mode === "set",
      "计数草稿收集成 {key,value,mode}", JSON.stringify(byKey["gold"]));
    check(byKey["current_location"] && byKey["current_location"].value === "lake",
      "文本/枚举草稿照原样提交");
    check(byKey["story"] && byKey["story"].value === '{"arc":"a","ep":2}',
      "JSON 草稿按字符串提交（插件侧解析）");
    check(byKey["inventory"] && byKey["inventory"].op === "edit"
      && byKey["inventory"].index === 0 && byKey["inventory"].value.quality === "神品",
      "鱼的草稿收集成 {key,op:edit,index,value}", JSON.stringify(byKey["inventory"]));

    // 保存要二次确认（写的是真实存档）
    T.pdState().dirty = 3;
    check(T.renderPlayerDataTab(T.TAB_BY_ID.playerdata).indexOf('data-act="pd2:save"') > 0,
      "有「保存修改」按钮");
    T.pdState().confirm = true;
    check(T.renderPlayerDataTab(T.TAB_BY_ID.playerdata).indexOf("确认写入（先自动存档）") > 0,
      "点保存先出二次确认条");
    T.pdState().confirm = false;
    T.pdState().drafts = {};
    T.pdState().fishEdit = {};
    T.pdState().dirty = 0;
  }

  /* 配置面板里「只读」的键仍然只有那 7 个：不能因为加了新功能就多出来
     （站长要求「不要在配置页面放只读，全部改成可配置」—— 那 7 个是文件字段 /
     数据操作 / 页面自己的状态，本来就不是「配置值」）。 */
  const stillPanel = schemaKeys.filter(function (k) { return cov.entries[k] === "panel"; }).sort();
  check(stillPanel.length === 7 &&
    stillPanel.join(",") === "backup_export_file,backup_import_file,data_action,data_confirm,data_status,data_target,editor_status",
    "加了新功能之后，只读的键仍然是那 7 个「不是配置值」的（没有多出来）",
    stillPanel.join(","));

  /* v1.18.47：插件把 _conf_schema.json 一起下发了 —— 页面没登记、但 schema 认得的键
     要**自动**长出一行（走数值页），不再把站长挡在红行上。 */
  const savedSchema = T.state.schema;
  T.state.schema = { brand_new_plugin_key: { description: "插件新加的键（说明来自 schema）", type: "int", default: 7 } };
  const autoCov = T.configKeyCoverage(schemaKeys.concat(["brand_new_plugin_key"]));
  check(autoCov.missing.length === 0 && autoCov.auto.indexOf("brand_new_plugin_key") >= 0,
    "schema 认得的新键会被自动登记（不再算「没有入口」）",
    "missing=" + autoCov.missing.join(",") + " auto=" + autoCov.auto.join(","));
  const autoRow = T.numberRowFromSchema("brand_new_plugin_key", 9);
  check(autoRow.entry === "numbers" && autoRow.key === "brand_new_plugin_key" &&
    autoRow.desc.indexOf("schema") > 0,
    "自动生成的行有入口、说明取自 schema", autoRow.desc);
  const autoKeys = T.state.configKeys;
  T.state.configKeys = schemaKeys.concat(["brand_new_plugin_key"]);
  const autoHtml = T.renderKeysTab(T.TAB_BY_ID.keys);
  check(autoHtml.indexOf("页面还不认识的配置键") < 0,
    "视图上不再出红条（这一行已经自动登记了）");
  T.state.configKeys = autoKeys;
  T.state.schema = savedSchema;

  /* 演示数据与真实默认值一致（页面上新增分组/视图时不能出现空白） */
  const demo = T.demoNumberRows();
  check(demo.length === 143 && demo.filter(function (r) { return !r.group; }).length === 0,
    "演示数据 143 行且每行都有分组（数值页的分组标题撑得起来）",
    demo.length + " 行");
  const noDemo = T.NUMBER_KEYS.filter(function (item) {
    return T.DEMO_NUMBER_VALUES[item[0]] === undefined;
  }).map(function (item) { return item[0]; });
  check(noDemo.length === 0, "演示值表覆盖每一个键（不会出现「这行是空的」）",
    noDemo.join(",") || "131/131 都有演示值");
  const staleDemo = ["order_reward_mult", "pond_income_cap_hours", "aquarium_slots", "button_defs",
    "command_aliases"].concat(newKeys).concat(dailyKeys).concat([edwKey]).filter(function (k) {
    return JSON.stringify(T.DEMO_NUMBER_VALUES[k]) !== JSON.stringify(schema[k].default);
  });
  check(staleDemo.length === 0,
    "刷新过的 5 个 + 新增的 10 个 + 4 个每日额度 + 逃脱率权重的演示值 == _conf_schema.json 的默认值",
    staleDemo.join(",") || "20/20 与 schema 一致");
  /* 4 个每日额度键逐字核对：演示值 JSON 全等于 schema default（不是手抄的近似值） */
  const dailyDemoBad = dailyKeys.filter(function (k) {
    return JSON.stringify(T.DEMO_NUMBER_VALUES[k]) !== JSON.stringify(schema[k].default);
  });
  check(dailyDemoBad.length === 0, "4 个每日额度键的演示值 JSON 全等于 _conf_schema.json 的默认值",
    dailyDemoBad.length
      ? dailyDemoBad.map(function (k) {
        return k + "=" + JSON.stringify(T.DEMO_NUMBER_VALUES[k]) + "≠" + JSON.stringify(schema[k].default);
      }).join(" ")
      : dailyKeys.map(function (k) { return k + "=" + JSON.stringify(T.DEMO_NUMBER_VALUES[k]); }).join(" "));
  const dailyDemoRows = demo.filter(function (r) { return dailyKeys.indexOf(r.key) >= 0; });
  check(dailyDemoRows.length === 4 && dailyDemoRows.every(function (r) {
    return r.entry === "" && r.value !== "" && r.value !== undefined && r.value !== null;
  }), "演示态里这 4 行也铺出来了：入口 \"\" + 有值（离线打开就能看见今天的额度）",
    dailyDemoRows.map(function (r) { return r.key + "=" + JSON.stringify(r.value); }).join(" "));
  /* v1.18.19 的 escape_difficulty_weight：演示值 JSON 全等于 schema 默认值（不是手抄的近似值），
     而且这一行在演示态里真铺得出来（入口 "" + 值 = 默认值）。 */
  check(JSON.stringify(T.DEMO_NUMBER_VALUES[edwKey]) === JSON.stringify(schema[edwKey].default) &&
    typeof T.DEMO_NUMBER_VALUES[edwKey] === "number",
    "escape_difficulty_weight 的演示值 JSON 全等于 _conf_schema.json 的默认值",
    JSON.stringify(T.DEMO_NUMBER_VALUES[edwKey]) + " vs " + JSON.stringify(schema[edwKey].default));
  const edwDemoRow = demo.filter(function (r) { return r.key === edwKey; })[0];
  check(!!edwDemoRow && edwDemoRow.entry === "" && edwDemoRow.value === schema[edwKey].default,
    "演示态里这一行也铺出来了：入口 \"\" + 值 = schema 默认值（离线打开就能看见）",
    edwDemoRow ? JSON.stringify(edwDemoRow.value) : "没铺出来");
  check(T.DEMO_NUMBER_VALUES.stamina_max === 20 && T.DEMO_NUMBER_VALUES.level_xp_ratio === 1.08 &&
    T.DEMO_NUMBER_VALUES.quality_myth_chance === 0.0025,
    "演示值就是 _conf_schema.json 里的真实默认值（体力 20 / 曲线 1.08 / 洗髓 0.0025）",
    String(T.DEMO_NUMBER_VALUES.stamina_max));
  const groups = {};
  demo.forEach(function (r) { groups[r.group] = (groups[r.group] || 0) + 1; });
  check(Object.keys(groups).length >= 10, "演示数据铺开了十来个功能分组",
    Object.keys(groups).length + " 组：" + Object.keys(groups).join("/"));
  return Promise.resolve();
}

/* =============================================================================
   [13c] 📄 长文字一律换行：不许 nowrap 截断、不许省略号（v1.18.16）
   ============================================================================= */
async function wrappingRules() {
  console.log("\n[13c] 📄 长文字换行（站长：字数太多就多行显示）");
  check(!/text-overflow:\s*ellipsis/.test(html),
    "整页已经没有 text-overflow: ellipsis 了（不再截断）");
  check(!/\.cut \{[^}]*nowrap/.test(html) && /\.cut \{[^}]*white-space: normal/.test(html),
    ".cut（长说明列）改成换行显示，不再是 nowrap + 省略号");
  check(/\.cut \{[^}]*overflow-wrap: anywhere/.test(html),
    "长说明列还带 overflow-wrap: anywhere（长 URL / 长指令也能折行）");
  check(/\.mono \{[^}]*overflow-wrap: anywhere/.test(html),
    ".mono（等宽字）照样能换行（等宽 ≠ 不许折行）");
  check(/table\.grid tbody td \{[\s\S]{0,220}?white-space: normal;[\s\S]{0,80}?overflow-wrap: anywhere;/.test(html),
    "表格单元格默认就允许换行（不用给每张表加 wrap-cells）");
  check(/td\.num, table\.grid tbody td\.id-cell \{ white-space: nowrap; \}/.test(html),
    "只有数字列 / id 列保留单行（它们本来就没有长文本）");
  check(/\.rp-btn \{[^}]*white-space: normal/.test(html) &&
    !/\.rp-btn \{[^}]*text-overflow/.test(html),
    "预览里的按钮文案也会换行（不再被截成一行）");
  check(/\.rp-row > \.hint, \.rp-card-desc, \.form-note, \.notice div \{[\s\S]{0,120}?white-space: normal/.test(html),
    "场景卡片的说明 / 提示条也会换行");
  check(/\.statusbar > \* \{ min-width: 0; overflow-wrap: anywhere; white-space: normal; \}/.test(html),
    "底部状态栏的长文字换行（窄窗口不顶出屏幕）");
  check(/table\.grid\.stack tbody td \.cut,[\s\S]{0,200}?white-space: normal; word-break: break-word;/.test(html),
    "窄窗口（≤1180px）卡片模式下同样换行（老规矩没被改坏）");
  check(/content: attr\(data-label\)/.test(html) && /@media \(max-width: 1180px\)/.test(html),
    "窄屏拆卡片 + data-label 的老规矩还在");

  /* 渲染出来的东西也真的带着整段文字（不是被截断的半句） */
  const longDesc = "这是一段故意写得很长的说明文字，用来确认表格里的说明列会整段换行显示，而不是被省略号截掉一半导致看不全。";
  const row = { key: "x_long", label: "长说明", value: 1, unit: "", desc: longDesc, text: false, group: "测试" };
  const cells = T.cellHtml(T.TAB_BY_ID.numbers,
    T.TAB_BY_ID.numbers.columns.filter(function (c) { return c.key === "desc"; })[0], row, 0);
  check(cells.indexOf(longDesc) > 0 && cells.indexOf("cut") > 0,
    "说明列渲染出的是整段文字（带 .cut 换行样式，不进省略号）");
  const fishCol = T.TAB_BY_ID.fish.columns.filter(function (c) { return c.key === "flavor"; })[0];
  const fishRow = Object.assign({}, T.state.data.fish[0], {
    flavor: "很长的说明文字：这条鱼的来历能写一大段，页面必须整段显示。" .repeat(4) + "（完）"
  });
  const fishCell = T.cellHtml(T.TAB_BY_ID.fish, fishCol, fishRow, 0);
  check(fishCell.indexOf(fishRow.flavor) > 0 && fishCell.length > fishRow.flavor.length,
    "鱼池的「说明」列也是整段渲染（没有被切成一行）", fishCell.length + " 字符");

  /* 场景卡片里的按钮行：三个控件都在，而且都能换行 */
  const card = T.renderReplyCard(T.replySceneById("cast.hit"));
  check(/\.rp-btn-edit \{[\s\S]{0,120}?flex-wrap: wrap/.test(html),
    "按钮编辑行允许换行（文案 / 指令 / 样式不会互相挤掉）");
  const preview = T.renderReplyPreview(T.previewModelFor(T.replySceneById("cast.hit")));
  check(preview.indexOf("rp-btn") > 0, "预览按钮用的是那条会换行的样式");
  return Promise.resolve();
}

/* =============================================================================
   [13d] 🔘 加按钮 = 从现成选项里挑（文案 / 指令 / 样式），格式与插件一致（v1.18.16）
   ============================================================================= */
async function buttonPicker() {
  console.log("\n[13d] 🔘 场景卡片：加按钮从现成选项里挑");
  const scene = T.replySceneById("cast.hit");

  /* 文案下拉：已有文案（去重）+ 常用推荐 + 手动输入 */
  const labelHtml = T.buttonLabelOptions("");
  check(labelHtml.indexOf("已有的按钮文案") > 0 && labelHtml.indexOf("常用推荐") > 0,
    "文案下拉分成「已有的按钮文案 / 常用推荐」两组");
  check(labelHtml.indexOf(">再来一竿<") > 0 && labelHtml.indexOf(">拉线！<") > 0,
    "下拉里能看到现有 button_defs 里真的用过的文案（再来一竿 / 拉线！）");
  check(labelHtml.indexOf("看背包") > 0 && labelHtml.indexOf("卖光光") > 0,
    "常用推荐也在（看背包 / 卖光光）");
  check(labelHtml.indexOf("✏️ 手动输入…") > 0 && labelHtml.indexOf('value="__manual__"') > 0,
    "保留「✏️ 手动输入…」兜底选项");
  const usedLabels = T.usedButtonValues("label");
  check(usedLabels.length === new Set(usedLabels).size && usedLabels.indexOf("再来一竿") >= 0,
    "「已有文案」是从现有按钮里收集且去过重的", usedLabels.length + " 个：" + usedLabels.slice(0, 5).join("/"));
  check(labelHtml.indexOf("is-bad") < 0, "文案下拉本身不带校验红（选完才校验）");

  /* 指令下拉：已有指令 + 插件下发的规范子命令 + 短写法 / 模板 */
  const cmdHtml = T.buttonCommandOptions("");
  check(cmdHtml.indexOf("已有的指令") > 0 && cmdHtml.indexOf("插件认识的子命令") > 0 &&
    cmdHtml.indexOf("常用短写法 / 模板") > 0,
    "指令下拉分成「已有 / 插件子命令 / 短写法与模板」三组");
  check(cmdHtml.indexOf('value="/钓鱼 事件 {n}"') > 0 && cmdHtml.indexOf("{n} = 第几个选项") > 0,
    "带占位符的模板也给出来了（/钓鱼 事件 {n}）");
  const canon = T.canonicalCommands();
  check(canon.length > 0 && cmdHtml.indexOf('value="/钓鱼 ' + canon[0] + '"') > 0,
    "插件下发的规范子命令都在下拉里（不写死清单）", canon.slice(0, 3).join("/"));
  check(cmdHtml.indexOf('value="/钓鱼 背包"') > 0 && cmdHtml.indexOf('value="/钓鱼"') > 0,
    "已有指令与「只发 /钓鱼」这种短写法都在");
  check(cmdHtml.indexOf('value="__manual__"') > 0, "指令下拉同样保留手动输入");
  check(T.buttonCommandOptions("/钓鱼 查 鲤鱼").indexOf('value="/钓鱼 查 鲤鱼" selected') > 0 &&
    T.buttonLabelOptions("我自己想的文案").indexOf('value="我自己想的文案" selected') > 0,
    "沿用下来的自定义值也会出现在下拉里（不会「隐身」导致选不回去）");
  const cmdValues = (cmdHtml.match(/<option value="([^"]*)"/g) || []);
  check(cmdValues.length === new Set(cmdValues).size, "指令下拉里的选项没有重复",
    cmdValues.length + " 个选项");
  const storyTpl = T.buttonCommandOptions("");
  check(storyTpl.indexOf("{label}") >= 0 || T.buttonTemplateHint("{label}").indexOf("{label}") > 0,
    "{label} / {n} 这类占位符有专门说明（不会被当成要原样发出去的指令）");
  check(T.buttonTemplateHint("/钓鱼 事件 {n}").indexOf("{n}") > 0 &&
    T.buttonTemplateHint("/钓鱼 背包") === "",
    "只有真的带占位符的才加模板说明");

  /* 样式下拉：default / primary / 0~255 */
  const styleHtml = T.buttonStyleNumericOptions("");
  check(styleHtml.indexOf('value="default"') > 0 && styleHtml.indexOf('value="primary"') > 0,
    "样式下拉有 default（灰）与 primary（蓝）");
  check(styleHtml.indexOf('value="0"') > 0 && styleHtml.indexOf('value="255"') > 0 &&
    (styleHtml.match(/<option value="\d+"/g) || []).length === 256,
    "数字样式 0~255 全都在（256 个）",
    (styleHtml.match(/<option value="\d+"/g) || []).length);
  check(T.buttonStyleNumericOptions("7").indexOf('value="7" selected') > 0,
    "页面认不出的数字样式原样选中（保存一次不会把玩家的按钮颜色改掉）");

  /* 卡片里真的渲染出选择器：三个下拉 + 格式说明 */
  T.state.replies.picker = "cast.hit";
  T.resetNewButtonDraft("cast.hit");
  const picker = T.renderReplyButtonPicker("cast.hit");
  check(picker.indexOf('data-act="rp:newLabel"') > 0 && picker.indexOf('data-act="rp:newData"') > 0 &&
    picker.indexOf('data-act="rp:newStyle"') > 0,
    "选择器有三个下拉（文案 / 点击后发送 / 样式）");
  check(picker.indexOf('data-act="rp:btnAddConfirm"') > 0,
    "选择器里有「＋ 加进去」按钮");
  check(picker.indexOf("场景|文案|点击后发送|样式") > 0,
    "选择器里写明存进配置还是老格式（场景|文案|指令|样式）");
  const pickerCard = T.renderReplyCard(scene);
  check(pickerCard.indexOf("rp-picker") > 0 && pickerCard.indexOf("rpPicker-cast.hit") > 0,
    "开着选择器的场景卡片里真的渲染了它");
  T.state.replies.picker = "";
  check(T.renderReplyCard(scene).indexOf("rp-picker") < 0,
    "关掉之后卡片里不再有选择器（不会 154 个场景一起膨胀）");
  T.state.replies.picker = "cast.hit";

  /* 产出格式：选完加进去，落到草稿里就是 {label,data,style}，序列化与插件逐字一致 */
  const nb = T.replyNewButtonDraft();
  nb.label = "看背包";
  nb.data = "/钓鱼 背包";
  nb.style = "primary";
  const before = T.replySceneDraft("cast.hit").buttons.length;
  const added = T.addPickedButton("cast.hit");
  const draft = T.replySceneDraft("cast.hit");
  check(draft.buttons.length === before + 1 && added.label === "看背包" &&
    added.data === "/钓鱼 背包" && added.style === "primary",
    "选中的文案 / 指令 / 样式变成了一个新按钮", JSON.stringify(added));
  const serialized = T.TABLE_DEFS.buttons.serialize({
    scene: "cast.hit", label: added.label, data: added.data, style: added.style
  });
  check(serialized === "cast.hit|看背包|/钓鱼 背包|primary",
    "序列化结果与插件认的格式逐字一致（场景|文案|指令|样式）", serialized);
  const payload = T.replyPayloadFor(null);
  check(String(payload.button_defs).indexOf("cast.hit|看背包|/钓鱼 背包|primary") > 0,
    "保存载荷里就是这一行（没有引入任何新格式）",
    String(payload.button_defs).split("\n").slice(0, 3).join(" / "));

  /* 手动输入兜底：切到手动模式后，写什么就存什么 */
  nb.labelMode = "manual";
  nb.dataMode = "manual";
  nb.label = "我自己写的文案";
  nb.data = "/钓鱼 查 鲤鱼";
  const manual = T.addPickedButton("cast.hit");
  check(manual.label === "我自己写的文案" && manual.data === "/钓鱼 查 鲤鱼",
    "「✏️ 手动输入」写的东西原样保留（选择器只是少打字）", JSON.stringify(manual));
  const manualPicker = T.renderReplyButtonPicker("cast.hit");
  check(manualPicker.indexOf('data-act="rp:newLabelText"') > 0 &&
    manualPicker.indexOf('data-act="rp:newDataText"') > 0 &&
    manualPicker.indexOf("↩ 从列表里选") > 0,
    "手动模式下给的是输入框，并且能切回列表");

  /* {label}/{n} 占位符这条老覆盖点（story 场景）在新选择器里照样给得出来 */
  const tplPickerCmd = T.buttonCommandOptions("/钓鱼 事件 {n}");
  check(tplPickerCmd.indexOf('value="/钓鱼 事件 {n}" selected') > 0,
    "story 那种模板指令在下拉里能被选中（老行为没丢）");
  check(T.renderReplyCard(T.replySceneById("story.prompt")).indexOf("{label}") > 0,
    "故事场景的卡片仍然显示 {label} 这类占位符");

  /* 已有按钮行也能「挑」：三个控件都挂着 datalist */
  const row = T.renderReplyButtonRow("cast.hit", { label: "看背包", data: "/钓鱼 背包", style: "default" }, 0, 1);
  check(row.indexOf('list="rpLabelList"') > 0 && row.indexOf('list="rpCmdList"') > 0 &&
    row.indexOf('list="rpStyleNums"') > 0,
    "已有按钮的文案 / 指令 / 样式输入框也挂上了下拉数据");
  const datalists = T.renderReplyDatalists();
  check(datalists.indexOf('id="rpLabelList"') > 0 && datalists.indexOf('id="rpCmdList"') > 0 &&
    datalists.indexOf('id="rpStyleNums"') > 0,
    "三份 datalist 在回复页里只渲染一次（154 个场景共用）");
  check((datalists.match(/<option value="255">/g) || []).length === 1,
    "数字样式 0~255 也只有一份（不随卡片数量翻倍）");
  const tabHtml = T.renderRepliesTab(T.TAB_BY_ID.replies);
  check(tabHtml.indexOf('id="rpLabelList"') > 0 && tabHtml.indexOf('id="rpStyleNums"') > 0,
    "回复页整体渲染时把 datalist 带上了（不然 list= 引用不到）");

  T.state.replies.picker = "";
  const reset = T.resetNewButtonDraft("cast.miss");
  check(reset.style === T.state.replies.defaultStyle && reset.labelMode === "pick" &&
    reset.dataMode === "pick" && reset.label === "",
    "每次点「＋ 加按钮」都从干净状态开始（样式用全局默认）", JSON.stringify(reset));
  return Promise.resolve();
}

/* =============================================================================
   [14] 数据通道的真实往返：假 SDK（apiGet/apiPost 到插件注册的相对路径）
   ============================================================================= */
const captured = {
  calls: [], context: null, failPost: false, statusReads: 0, phase: "before"
};

/** v1.18.53：插件下发的「可编辑玩家字段」长什么样（真后端由 PLAYER_FIELDS 生成）。 */
const FAKE_PLAYER_SECTIONS = [
  { title: "数值与状态", kind: "fields", items: [
    { key: "gold", label: "金币", desc: "主货币", value: 12800 },
    { key: "total_caught", label: "累计钓获", desc: "等级按它算", value: 214 },
    { key: "stamina", label: "体力", desc: "当前体力", value: -1 },
    { key: "current_location", label: "当前钓点", desc: "钓点 id", value: "novice" },
  ] },
  { title: "计数表", kind: "maps", items: [
    { key: "items", label: "道具数量", desc: "道具表 id", entries: [
      { name: "feed_basic", count: 3 },
    ] },
    { key: "baits", label: "鱼饵数量", desc: "鱼饵表 id", entries: [] },
  ] },
  { title: "鱼", kind: "fish", items: [
    { key: "inventory", label: "背包", count: 7 },
    { key: "aquarium", label: "水族馆", count: 2 },
  ] },
];

/** v1.18.56：插件下发的「玩家全部数据」长什么样（真后端由 _editor_player 生成）。 */
const FAKE_PLAYER_FULL = {
  user_id: "10001", name: "钓鱼佬", from_snapshot: false,
  raw_json: "{\n \"user_id\": \"10001\",\n \"gold\": 12800\n}",
  groups: [
    { key: "基础", title: "基础", items: [
      { key: "gold", label: "金币", kind: "count", value: 12800, has: true, desc: "主货币" },
      { key: "stamina", label: "体力", kind: "count", value: -1, has: true, desc: "" },
    ] },
    { key: "资产", title: "资产", items: [
      { key: "baits", label: "鱼饵", kind: "map", value: { worm: 3 }, has: true, desc: "", enum: "baits" },
      { key: "inventory", label: "背包（鱼）", kind: "fish_list", value: [
        { fish_id: "carp", quality: "凡品", variant: "", attrs: { meat: 10, spirit: 5, sheen: 3 },
          feed_uses: 0, locked: false, value: 120 },
      ], has: true, desc: "逐条可改" },
    ] },
  ],
  enums: {
    fish: [{ id: "carp", name: "🐟 鲤鱼" }],
    quality: [{ id: "凡品", name: "⚪凡品" }, { id: "珍品", name: "🏆珍品" }],
    variants: [{ id: "golden", name: "🌟 黄金" }],
    baits: [{ id: "worm", name: "🪱 蚯蚓" }],
    items: [{ id: "feed_basic", name: "🥫 基础饲料" }],
    rods: [], locations: [], titles: [], achievements: [], weather: [], collectibles: [],
  },
};

function makeFakeSdk() {
  const sdk = {
    ready() {
      return new Promise(function (resolve) {
        sdk._setContext({
          pluginName: "dhhxfggg/astrbot_plugin_fishing_mini",   // author/name 形态
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
        // v1.18.53：测试可以临时注入一个「失败响应」来验错误路径
        if (captured.playersFail) return captured.playersFail();
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
        // v1.18.53：读玩家可编辑数据（字段名单由插件下发）
        if (action === "player_get") {
          return Promise.resolve(Object.assign({}, base, {
            user_id: String(payload.user_id || ""), name: "钓鱼佬", level: 6,
            sections: FAKE_PLAYER_SECTIONS
          }));
        }
        // v1.18.53：改玩家数据（增 / 减 / 改）—— 已被 v1.18.57 的 player_full_set 取代，
        //           这里保留旧动作只是为了验「插件仍认它」（老页面/老客户端不至于报错）
        if (action === "player_set" || action === "snapshot_player_set") {
          const n = ((payload.edits || []).length);
          return Promise.resolve(Object.assign({}, base, {
            message: "已改玩家 " + payload.user_id + "：" + n + " 项"
          }));
        }
        // v1.18.57：玩家全部数据（读 / 写）。写的时候要带 snapshot = 改哪份存档。
        if (action === "player_full_get" || action === "player_full_set") {
          const full = Object.assign({}, FAKE_PLAYER_FULL, {
            user_id: String(payload.user_id || "10001"),
            from_snapshot: !!payload.snapshot,
          });
          if (action === "player_full_get") {
            return Promise.resolve(Object.assign({}, base, full));
          }
          const n = ((payload.edits || []).length);
          return Promise.resolve(Object.assign({}, base, {
            message: (payload.snapshot
              ? "已改存档「" + payload.snapshot + "」里玩家 "
              : "已改玩家 ") + payload.user_id + " 的数据：" + n + " 项",
          }));
        }
        return Promise.resolve(Object.assign({}, base, {
          message: "已把玩家 " + payload.user_id + " 的金币改成 " + payload.gold
        }));
      }
      let message = "已执行 " + payload.action;
      // 「找回旧数据」（v1.18.1）：扫描/导入各自回一份带 legacy 的状态
      if (payload.action === "legacy_scan" || payload.action === "legacy_import") {
        const imported = payload.action === "legacy_import";
        captured.legacyImported = imported;
        const legacy = FAKE_LEGACY(imported);
        const st = Object.assign({}, fresh, {
          legacy: legacy, last_action: payload.action,
          last_result: imported
            ? "导入 1 名玩家、跳过 1 名（当前数据优先）；导入前已存档：manual/2026-09-20_1010.json"
            : "扫描到旧数据 —— dhhxfggg2023/astrbot_plugin_fishing_mini：2 名玩家 / 3 行（点「导入」把缺的补进来）"
        });
        return Promise.resolve({
          status: "ok", ok: true, message: st.last_result,
          editor_status: JSON.stringify(st)
        });
      }
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
  numbers_editable: ["stamina_max", "multi_cast_max", "fish_value_mult",
    "luck_weight_step", "luck_cap"],
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

/** 「找回旧数据」的扫描结果（v1.18.1）：导入前后就差在 present / missing。 */
function FAKE_LEGACY(imported) {
  const scope = "dhhxfggg2023/astrbot_plugin_fishing_mini";
  const players = [
    { key: "player_70001", user_id: "70001", gold: 4321, caught: 88, locations: 1,
      inventory: 0, aquarium: 0, scope_id: scope, present: !!imported },
    { key: "player_70002", user_id: "70002", gold: 999, caught: 7, locations: 1,
      inventory: 0, aquarium: 0, scope_id: scope, present: true },
    // 真库里真有一条这种脏键：导入时会取干净 ID
    { key: "player_<@70003>", user_id: "70003", uid_fixed: true, gold: 5, caught: 1,
      locations: 0, inventory: 0, aquarium: 0, scope_id: scope, present: !!imported }
  ];
  return {
    db_path: "C:/Users/x/.astrbot/data/data_v4.db",
    current_scope: "dhhxfggg/astrbot_plugin_fishing_mini",
    scopes: [{ scope_id: scope, rows: 3, players: players, other_keys: ["leaderboard"],
               missing: imported ? 0 : 1 }],
    players: players,
    players_found: 2,
    players_missing: imported ? 0 : 1
  };
}

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
  // v1.18.33：这两行曾经是文本行（true），保存时按字符串提交 -> 插件整批拒收
  luck_weight_step: 1.0,
  luck_cap: 2.0,
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
  for (let i = 0; i < 40 && (F.state.loading || F.ENV.pluginBase === "astrbot_plugin_fishing_mini"); i++) {
    await new Promise(function (r) { setTimeout(r, 25); });
  }

  check(F.ENV.online === true, "检测到桥接 SDK -> 在线模式");
  check(F.ENV.pluginBase === "dhhxfggg/astrbot_plugin_fishing_mini",
    "pluginName 取的是上下文里给的那个", F.ENV.pluginBase);

  // endpoint 是相对路径，插件名不再参与请求；resolvePluginBase 只做规范化，不发请求
  captured.calls = [];
  const resolved = await F.resolvePluginBase("astrbot_plugin_fishing_mini");
  check(resolved === "astrbot_plugin_fishing_mini",
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
  /* v1.18.16：站长要求「所有配置键都看得见」，所以白名单外的键**照样显示**，
     但必须是没有输入框的只读行 —— 改了也存不下去的假输入框比不显示更糟。 */
  const dataRow = F.state.data.numbers.filter(function (r) { return r.key === "data_status"; })[0];
  check(!!dataRow && dataRow.entry === "panel" && dataRow.writable === false,
    "data_status 看得见，但标成「插件面板项 · 页面只读」", dataRow && dataRow.entry);
  check(!!dataRow && dataRow.value === "不该出现在数值表里",
    "字符串型的只读键原样显示（不会变成 NaN / 0）", dataRow && String(dataRow.value));
  const fishJump = F.state.data.numbers.filter(function (r) { return r.entry === "tab:fish"; })[0];
  check(!!fishJump && fishJump.key === "fish_defs",
    "内容表在数值页只有一个可点的跳转行（去「🐟 鱼池」页改）", fishJump && fishJump.key);
  check(F.numberEntryCellHtml(dataRow).indexOf('data-act=') < 0 &&
    F.numberEntryCellHtml(dataRow).indexOf("<input") < 0,
    "只读行里没有输入框（不给假的编辑入口）");
  check(F.numberEntryCellHtml(fishJump).indexOf('data-act="num:jump"') > 0,
    "跳转行给的是「→ 到 XX 页改」按钮（点得到）");
  const noWhitelist = F.numberValuesFromPage(null);
  check(noWhitelist.data_status === undefined && noWhitelist.editor_status === undefined,
    "拿不到白名单时也不会提交「插件面板项」（否则插件会整批拒收）",
    Object.keys(noWhitelist).join(","));
  check(noWhitelist.stamina_max === 25, "白名单拿不到时普通数值照样能提交");
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
  /* v1.18.33：单个数值的键必须以**数字**提交 —— 以前 luck_weight_step / luck_cap
     被登记成文本行，值按字符串发出去，插件按数字校验、整批拒收，
     站长看到的就是「保存不了数据，请求失败 config（luck_weight_step）需要数字」。 */
  check(numberEnv.payload.luck_weight_step === 1
    && numberEnv.payload.luck_cap === 2,
    "单个数值的键按数字提交（不是字符串 \"1\"）—— 否则插件会整批拒收",
    JSON.stringify({ luck_weight_step: numberEnv.payload.luck_weight_step,
      luck_cap: numberEnv.payload.luck_cap }));
  check(typeof numberEnv.payload.luck_weight_step === "number",
    "luck_weight_step 提交的是 number 类型",
    typeof numberEnv.payload.luck_weight_step);
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

  /* v1.18.57：改玩家数据只有一条通路（player_full_set），实时 / 存档内都在这一页切 */
  captured.calls = [];
  F.state.playerData = null;
  await F.loadPlayerFull("10001", "");
  const getCall = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  const getEnv = JSON.parse(getCall.body);
  check(getCall.endpoint === "players" && getEnv.action === "player_full_get"
    && getEnv.user_id === "10001" && !getEnv.snapshot,
    "读实时玩家全部数据：player_full_get + user_id（不带 snapshot）", JSON.stringify(getEnv));
  check(F.pdState().loaded === true && F.pdState().groups.length === 2,
    "读回来就渲染成分组（插件下发什么就显示什么）", F.pdState().groups.length);

  captured.calls = [];
  const liveRes = await F.submitPlayerFull([{ key: "gold", value: 5000 }]);
  const liveCall = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  const liveEnv = JSON.parse(liveCall.body);
  check(liveCall.endpoint === "players",
    "改实时玩家 POST 到 players（不走 config，也不会被 snapshot_ 前缀误判到存档接口）", liveCall.endpoint);
  check(liveEnv.action === "player_full_set" && liveEnv.user_id === "10001"
    && !liveEnv.snapshot && liveEnv.edits.length === 1,
    "改实时玩家数据的指令正确", JSON.stringify(liveEnv));
  check(liveEnv.confirm === true,
    "请求里带 confirm=true（页面上的二次确认走完才会发这条）");
  check(liveRes.ok === true && /10001/.test(liveRes.message), "改数据结果取插件返回的真实结果", liveRes.message);

  captured.calls = [];
  await F.loadPlayerFull("10001", "2026-09-18_1820.json");
  const snapGetEnv = JSON.parse(captured.calls.filter(function (c) { return c.kind === "post"; })[0].body);
  check(snapGetEnv.action === "player_full_get" && snapGetEnv.snapshot === "2026-09-18_1820.json",
    "读存档内玩家：带上要读哪一份存档", JSON.stringify(snapGetEnv));
  check(F.pdState().mode2 === "snapshot" && F.pdState().snapshot === "2026-09-18_1820.json",
    "页面记住了「现在改的是存档内玩家」");

  captured.calls = [];
  await F.submitPlayerFull([{ key: "gold", value: 6600 }]);
  const snapSetCall = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  const snapSetEnv = JSON.parse(snapSetCall.body);
  check(snapSetCall.endpoint === "players" && snapSetEnv.action === "player_full_set"
    && snapSetEnv.snapshot === "2026-09-18_1820.json"
    && snapSetEnv.user_id === "10001" && snapSetEnv.confirm === true,
    "改存档内玩家的指令正确（带存档名 + 玩家 + confirm）", JSON.stringify(snapSetEnv));
  check(snapSetEnv.edits.length === 1 && snapSetEnv.snapshot === "2026-09-18_1820.json",
    "存档内改数据也走同一条动作（只有一个写入口，不会再分叉）", JSON.stringify(snapSetEnv));
  F.state.playerData = null;

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
        sdk._setContext({ pluginName: "dhhxfggg/astrbot_plugin_fishing_mini", pageName: "editor" });
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

/* =============================================================================
   [16b] 玩家页「找回旧数据」（v1.18.1）：扫描 -> 确认 -> 导入
   ============================================================================= */
async function legacyRecovery() {
  console.log("\n[16b] 找回旧数据：扫描只读 / 导入要确认 / 结果写进面板");
  const F = loadPageFromSource(makeFakeSdk()).T;
  for (let i = 0; i < 40 && (F.state.loading || F.ENV.pluginBase === "astrbot_plugin_fishing_mini"); i++) {
    await new Promise(function (r) { setTimeout(r, 25); });
  }
  check(F.ENV.online === true, "假 SDK 下进入在线模式");
  F.state.tab = "players";
  F.render();

  // 1) 还没扫过：只有「找回旧数据」按钮，没有导入面板
  let html = F.renderPlayersTab(F.TAB_BY_ID.players);
  check(html.indexOf('data-act="p:legacyScan"') > 0, "玩家页有「🔍 找回旧数据」按钮");
  check(html.indexOf('data-act="p:legacyImport"') < 0 && F.state.legacy === null,
    "没扫过时不显示导入面板（不凭空吓人）");

  // 2) 扫描：只发一条 legacy_scan，结果进 state.legacy
  captured.calls = [];
  captured.legacyImported = null;
  const scanRes = await F.runLegacyAction("legacy_scan");
  const scanPost = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  const scanEnv = JSON.parse(scanPost.body);
  check(scanPost.endpoint === "config" && scanEnv.action === "legacy_scan",
    "扫描走 config 通道的 legacy_scan 动作", scanPost.endpoint + " " + scanEnv.action);
  check(scanRes.ok === true && captured.legacyImported === false,
    "扫描不会触发导入", String(captured.legacyImported));
  check(F.state.legacy && F.state.legacy.scopes.length === 1 &&
    F.state.legacy.players_missing === 1,
    "扫描结果进了页面状态（旧作用域数 / 缺几名）",
    JSON.stringify(F.state.legacy && F.state.legacy.players_missing));
  check(F.state.legacyBusy === false, "扫描结束后按钮不再是「处理中」");

  // 3) 面板：作用域、库路径、缺谁、点导入要不要确认
  html = F.renderPlayersTab(F.TAB_BY_ID.players);
  check(html.indexOf("dhhxfggg2023/astrbot_plugin_fishing_mini") > 0 &&
    html.indexOf("data_v4.db") > 0,
    "面板写出旧作用域与库文件路径（站长知道在动哪儿）");
  check(html.indexOf("缺 1 名") > 0 && html.indexOf("已在当前库") > 0,
    "每名玩家标了「缺 / 已在当前库」");
  check(html.indexOf("导入时取干净 ID") > 0,
    "脏键（player_<@xxx>）标了「导入时取干净 ID」");
  check(html.indexOf('data-act="p:legacyImport"') > 0 &&
    html.indexOf('data-act="p:legacyImportYes"') < 0,
    "先给「把缺的玩家并进来」，还没到确认那一步");

  // 4) 二次确认是页面内的（不用浏览器弹窗），取消不会发请求
  F.state.legacyConfirm = true;
  F.render();
  html = F.renderPlayersTab(F.TAB_BY_ID.players);
  check(html.indexOf('data-act="p:legacyImportYes"') > 0 &&
    html.indexOf('data-act="p:legacyImportNo"') > 0,
    "确认态给出「✔ 确定导入 / ✖ 取消」");
  captured.calls = [];
  F.state.legacyConfirm = false;
  check(captured.calls.length === 0, "取消不产生任何请求");

  // 5) 确认导入：一条 legacy_import，结果面板刷新成 0 缺
  F.state.legacyConfirm = true;
  captured.calls = [];
  const impRes = await F.runLegacyAction("legacy_import");
  const impEnv = JSON.parse(captured.calls.filter(function (c) { return c.kind === "post"; })[0].body);
  check(impEnv.action === "legacy_import" && captured.legacyImported === true,
    "确认后发的是 legacy_import", impEnv.action);
  check(impRes.ok === true && /导入 1 名玩家/.test(impRes.message),
    "导入结果显示插件回的真实说明", impRes.message);
  check(F.state.legacyConfirm === false && F.state.legacy.players_missing === 0,
    "导入后页面状态刷新（不再显示缺人）", String(F.state.legacy.players_missing));
  html = F.renderPlayersTab(F.TAB_BY_ID.players);
  check(html.indexOf("（都已在当前库）") > 0, "面板改成「都已在当前库」");
  check(F.state.legacyMsg.indexOf("导入前已存档") > 0,
    "提示里说明导入前存了档（站长能看到旧数据留底）", F.state.legacyMsg.slice(0, 60));

  // 6) 读库失败时照实说（不装「没有旧数据」）
  F.state.legacy = Object.assign({}, F.state.legacy, {
    scopes: [], players: [], players_missing: 0,
    error: "数据库文件不存在：C:/nope/data_v4.db"
  });
  F.state.legacyMsg = "";
  html = F.renderPlayersTab(F.TAB_BY_ID.players);
  check(html.indexOf("读库没成功") > 0 && html.indexOf("data_v4.db") > 0,
    "读库失败时面板写出原因（不静默失败）");
  check(html.indexOf("这一步是<b>只读</b>的") > 0, "并说明这一步是只读的、失败不影响游戏");

  // 7) 关得掉：三种状态（有结果 / 读不到 / 只有提示）都带「✖ 关闭」
  check(html.indexOf('data-act="p:legacyClose"') > 0, "读不到库的面板也有关闭按钮");
  F.state.legacy = FAKE_LEGACY(false);
  F.state.legacyMsg = "扫描到旧数据 —— …";
  html = F.renderPlayersTab(F.TAB_BY_ID.players);
  check(html.indexOf('data-act="p:legacyClose"') >= 0
    && html.split('data-act="p:legacyClose"').length - 1 >= 2,
    "有结果 + 有提示时每条都给了关闭入口（不会「关不掉」）");

  captured.calls = [];
  const closed = await F.closeLegacyPanel();
  const closePost = captured.calls.filter(function (c) { return c.kind === "post"; })[0];
  const closeEnv = JSON.parse(closePost.body);
  check(closePost.endpoint === "config" && closeEnv.action === "legacy_clear",
    "关闭走 config 通道的 legacy_clear（只让插件忘掉缓存，不动数据）",
    closePost.endpoint + " " + closeEnv.action);
  check(closed.ok === true && F.state.legacy === null && F.state.legacyMsg === ""
    && F.state.legacyConfirm === false,
    "关闭后页面状态清干净", String(F.state.legacy) + "/" + F.state.legacyMsg);
  html = F.renderPlayersTab(F.TAB_BY_ID.players);
  check(html.indexOf('data-act="p:legacyClose"') < 0
    && html.indexOf('data-act="p:legacyImport"') < 0
    && html.indexOf("旧作用域里发现的玩家") < 0,
    "面板整块消失（含玩家表）");
  check(html.indexOf('data-act="p:legacyScan"') > 0, "「🔍 找回旧数据」按钮还在（想再看一眼随时点）");

  // 8) 插件回写的状态里 legacy 为 null 时，不许把面板又变出来
  F.applyStatus({ updated_at: 9, ok: true, legacy: null });
  check(F.state.legacy === null, "状态里 legacy=null 不会让面板复活");

  if (F.state._reloadTimer) { clearTimeout(F.state._reloadTimer); }
  return Promise.resolve();
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
