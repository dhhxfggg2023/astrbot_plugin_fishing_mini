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
  "makeNonce", "sleep", "waitForStatus", "BRIDGE_FILE", "renderBanner", "downloadSnapshot",
  "refreshSnapshots"
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
  check(Object.keys(T.TAB_BY_ID).length === 11, "标签页数量 = 11", Object.keys(T.TAB_BY_ID).join(","));
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
  const ALLOWED_EFFECTS = ["meat", "spirit", "sheen", "quality_up", "value_up", "heal"];
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
  check(tabsHtml.split("tab-count").length - 1 === 11, "11 个标签都有条目数徽标");
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
      } else {
        const out = T.renderTableTab(t);
        check(out.length > 400 && out.indexOf("<table") >= 0, "「" + t.label + "」渲染出表格", out.length + " 字符");
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
  check(["fish", "locations", "baits", "rods", "items", "collectibles", "variants", "weather", "numbers"]
    .every(function (k) { return Array.isArray(payload[k]); }), "载荷包含全部 9 张表");
  check(!!payload.autoBackup && payload.autoBackup.dailyHour === 4, "载荷带上自动备份设置");
  check(Object.keys(payload).length === 11, "载荷字段数 = 11（9 表 + numbers + autoBackup）", Object.keys(payload).length);

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
function channelHelpers() {
  console.log("\n[12] 数据通道：配置 <-> 表格 的转换");
  check(Object.keys(T.TABLE_DEFS).join(",") === "fish,rods,baits,items,locations,collectibles,variants,weather,easter_eggs",
    "9 张内容表都有解析/序列化定义", Object.keys(T.TABLE_DEFS).join(","));
  check(T.TABLE_DEFS.fish.configKey === "fish_defs" && T.TABLE_DEFS.fish.configType === "text",
    "fish_defs 是文本表（多行），其余是字符串数组");
  check(T.TABLE_DEFS.locations.configKey === "location_defs", "钓点表 -> location_defs");
  check(T.BRIDGE_FILE === "fishing_editor_bridge.json",
    "指令文件名与插件约定一致（_editor_bridge.BRIDGE_FILE_NAME）", T.BRIDGE_FILE);

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

  // 竖线不能进单元格（否则一行会被劈成两列）
  check(T.cell("a|b\nc｜d").indexOf("|") < 0 && T.cell("a|b\nc｜d").indexOf("\n") < 0,
    "cell() 把竖线/换行洗掉", JSON.stringify(T.cell("a|b\nc｜d")));

  // 序列化
  const serialized = T.serializeContentTables();
  check(Object.keys(serialized).join(",") === "fish_defs,rod_defs,bait_defs,item_defs,location_defs,collectible_defs,variant_defs,weather_defs,easter_egg_defs",
    "序列化输出 9 张配置表", Object.keys(serialized).join(","));
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
    interval_hours: 12, keep_daily: 10, keep_interval: 5 } });
  check(ab.enable === false && ab.dailyHour === 7 && ab.intervalHours === 12
    && ab.keepDaily === 10 && ab.keepInterval === 5,
    "editor_status -> 表单字段映射正确（蛇形转驼峰）");
  const abPayload = T.autobackupPayload(ab);
  check(JSON.stringify(Object.keys(abPayload).sort()) ===
    JSON.stringify(["daily_hour", "enable", "interval_hours", "keep_daily", "keep_interval"]),
    "表单字段 -> 插件字段名正确（以 DEFAULTS 为准）", Object.keys(abPayload).join(","));
  const n1 = T.makeNonce(), n2 = T.makeNonce();
  check(n1 !== n2 && /^n[a-z0-9]+-[a-z0-9]{8,}$/.test(n1) && n1.indexOf("_") < 0,
    "nonce 唯一、只含 URL/JSON 安全字符", n1 + " / " + n2);
  // 同毫秒内连续生成也必须不同（靠两段随机数，不靠时间）
  const same = [T.makeNonce(), T.makeNonce(), T.makeNonce(), T.makeNonce()];
  check(new Set(same).size === 4, "同一毫秒内连续生成 4 个 nonce 互不相同", same.join(","));

  // 失败信息必须能定位（不能只说「失败了」）
  const fakeErr = { response: { status: 401, statusText: "Unauthorized",
    data: { message: "Missing API key" } }, message: "Request failed" };
  const desc = T.describeError(fakeErr, "/api/files");
  check(/HTTP 401/.test(desc) && /Missing API key/.test(desc) && /\/api\/files/.test(desc),
    "上传失败时把 HTTP 状态 + 服务端原因 + 端点都说出来", desc);
  check(/离线预览/.test(T.describeError({ offline: true, message: "离线预览：没有 AstrBot 桥接" }, "x")),
    "离线错误有专门提示");
  check(/请求失败/.test(T.describeError({ message: "Network Error" }, "/api/files")),
    "没有 status 时退回「请求失败 + 原始信息」");
  return Promise.resolve();
}

/* =============================================================================
   [13] 数据通道的真实往返：假 SDK（含 pluginName 两种形态 + 上传捕获）
   ============================================================================= */
const captured = {
  uploads: [], context: null, probeFailures: 0, failUpload: false,
  statusReads: 0, phase: "before"
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
      // 模拟「pluginName 是 name 时接口 404」：逼页面去试 author/name
      if (/plugins\/astrbot_plugin_qq_fishing\/config$/.test(String(endpoint))) {
        captured.probeFailures++;
        return Promise.reject({ response: { status: 404, data: { message: "插件不存在" } } });
      }
      if (/plugins\/dhhxfggg\/astrbot_plugin_qq_fishing\/config$/.test(String(endpoint))) {
        captured.statusReads++;
        // phase：模拟「插件处理指令」这一瞬间 —— before = 还在旧状态，after = 已做完
        return Promise.resolve({
          metadata: {}, i18n: {},
          config: captured.phase === "after" ? FAKE_CONFIG_AFTER() : FAKE_CONFIG
        });
      }
      if (endpoint.indexOf("/config") >= 0) {
        return Promise.resolve({ metadata: {}, config: FAKE_CONFIG, i18n: {} });
      }
      return Promise.reject(new Error("unexpected GET " + endpoint));
    },
    apiPost() { return Promise.reject(new Error("页面不该用 apiPost 保存（没有 PUT）")); },
    upload(endpoint, file) {
      // 一上传成功，插件那边就算「处理完了」——后续读配置会看到新状态
      captured.phase = "after";
      if (captured.failUpload) {
        capture("upload", endpoint, null, file && file.name);
        return Promise.reject({
          response: { status: 403, statusText: "Forbidden",
            data: { message: "Insufficient API key scope" } },
          message: "Request failed with status code 403"
        });
      }
      return Promise.resolve(file && typeof file.arrayBuffer === "function"
        ? file.arrayBuffer().then(function (buf) {
            capture("upload", endpoint, Buffer.from(buf).toString("utf8"), file.name);
            return { attachment_id: "att_1", filename: file.name, type: "file" };
          })
        : null);
    },
    download() { return Promise.reject(new Error("not used")); },
    subscribeSSE() { return Promise.reject(new Error("not used")); }
  };
  return sdk;
}

function capture(kind, endpoint, body, name) {
  captured.uploads.push({ kind, endpoint, body, name });
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

  // pluginName 可能是 name（老形态）也可能是 author/name：直接验一次「两种都试」
  captured.probeFailures = 0;
  F.ENV.pluginBase = "astrbot_plugin_qq_fishing";
  const resolved = await F.resolvePluginBase("astrbot_plugin_qq_fishing");
  check(captured.probeFailures >= 1,
    "先用 name 试（接口回 404）——确实发起了探测请求", "探测失败次数=" + captured.probeFailures);
  check(resolved === "dhhxfggg/astrbot_plugin_qq_fishing" && F.ENV.pluginBase === resolved,
    "失败后自动改用 author/name，并记住可用的那个", resolved);
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

  // ---- 保存：内容表 + 数值 + 自动备份，各一条指令 ----
  captured.uploads = [];
  F.state.autoBackupDirty = true;
  F.state.data.fish[0].value = 999;          // 制造一处改动
  const saveRes = await F.saveConfig(F.buildPayload());
  check(saveRes.ok === true && /1~2 秒/.test(saveRes.message),
    "保存成功返回「已提交，1~2 秒后生效」", saveRes.message);
  const kinds = captured.uploads.map(function (u) { return u.endpoint; });
  check(kinds.every(function (e) { return e === "/api/files"; }),
    "所有指令都上传到 /api/files（页面没有 PUT 可用）", kinds.join(","));
  check(captured.uploads.length === 3, "内容 / 数值 / 自动备份各发一条，共 3 条", captured.uploads.length);

  const envs = captured.uploads.map(function (u) { return JSON.parse(u.body); });
  check(envs.every(function (e) { return e.nonce && e.action && e.payload; }),
    "每条指令都带 nonce / action / payload");
  check(new Set(envs.map(function (e) { return e.nonce; })).size === envs.length,
    "三条指令的 nonce 各不相同");
  const contentEnv = envs.filter(function (e) { return e.action === "save_content"; })[0];
  check(!!contentEnv, "发了 save_content 指令");
  check(contentEnv.payload.tables.fish_defs.split("\n")[0].indexOf("999") > 0,
    "改动写进了 fish_defs 文本", contentEnv.payload.tables.fish_defs.split("\n")[0]);
  check(Array.isArray(contentEnv.payload.tables.rod_defs),
    "rod_defs 以字符串数组形式提交");
  const numberEnv = envs.filter(function (e) { return e.action === "save_numbers"; })[0];
  check(!!numberEnv && numberEnv.payload.stamina_max === 25
    && numberEnv.payload.fish_value_mult === 1.0,
    "数值指令只带白名单内的键", JSON.stringify(numberEnv && numberEnv.payload));
  check(!numberEnv.payload.data_status && !numberEnv.payload.location_defs,
    "数值指令里没有 data_status / location_defs");
  const autoEnv = envs.filter(function (e) { return e.action === "save_autobackup"; })[0];
  check(!!autoEnv && autoEnv.payload.daily_hour === 4 && autoEnv.payload.enable === true,
    "自动备份设置用插件字段名提交", JSON.stringify(autoEnv && autoEnv.payload));
  check(captured.uploads[0].name === F.BRIDGE_FILE,
    "上传的文件名就是约定的固定文件名", captured.uploads[0].name);
  F.state.savedAt = null;
  if (F.state._reloadTimer) { clearTimeout(F.state._reloadTimer); }

  // ---- 上传失败：必须给出 HTTP 状态与原因 ----
  captured.failUpload = true;
  const failRes = await F.saveConfig(F.buildPayload());
  captured.failUpload = false;
  check(failRes.ok === false && /HTTP 403/.test(failRes.message),
    "上传失败时把 HTTP 状态显示出来", failRes.message);
  check(/Insufficient API key scope/.test(failRes.message),
    "上传失败时把服务端原因也显示出来", failRes.message);

  // ---- 存档动作：走同一个通道，等插件回写结果 ----
  captured.uploads = [];
  captured.statusReads = 0;
  captured.phase = "before";   // 上传之前：插件那边还是旧状态
  const renameRes = await F.runSnapshotAction("rename",
    { name: "2026-09-18_1820.json", note: "新备注" });
  const renameEnv = JSON.parse(captured.uploads[0].body);
  check(renameEnv.action === "snapshot_rename"
    && renameEnv.payload.name === "2026-09-18_1820.json"
    && renameEnv.payload.note === "新备注",
    "改名指令带对了快照名与新备注", JSON.stringify(renameEnv.payload));
  check(captured.statusReads >= 2 && captured.statusReads <= 4,
    "上传之后会轮询配置，直到 editor_status.updated_at 变新（不空转满 6 秒）",
    "读了 " + captured.statusReads + " 次");
  check(renameRes.ok === true && /改过|已把/.test(renameRes.message),
    "改名结果取的是插件回写的真实结果（不是页面自说自话）", renameRes.message);

  captured.uploads = [];
  await F.runSnapshotAction("create", { note: "页面手动存档" });
  check(JSON.parse(captured.uploads[0].body).action === "snapshot_create",
    "新建存档指令正确");

  captured.uploads = [];
  await F.runSnapshotAction("delete", { name: "2026-09-18.json" });
  check(JSON.parse(captured.uploads[0].body).action === "snapshot_delete",
    "删除存档指令正确");

  captured.uploads = [];
  await F.runSnapshotAction("restore", { name: "2026-09-18.json", player: "89777" });
  const restoreEnv = JSON.parse(captured.uploads[0].body);
  check(restoreEnv.action === "snapshot_restore" && restoreEnv.payload.player === "89777",
    "恢复指令带上了玩家 ID（支持只恢复单个玩家）");

  const unknown = await F.runSnapshotAction("nuke", {});
  check(unknown.ok === false && /不认识/.test(unknown.message),
    "未知存档动作被页面自己拦下", unknown.message);
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
