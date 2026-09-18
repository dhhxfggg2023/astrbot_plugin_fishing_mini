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

/* ---------- 2. 注入测试钩子 ---------- */
const hookNames = [
  "state", "render", "visibleRows", "validateRow", "parseDist", "diffTab", "renderTableTab",
  "renderSnapshotsTab", "renderSnapCard", "cellHtml", "buildPayload", "TAB_BY_ID", "TABS",
  "startEdit", "cancelEdit", "commitEdit", "applyBatch", "deepClone", "esc", "fmtNum",
  "knownLocationKeys", "dirtyTotal", "markSaved", "switchTab", "toast", "renderToolbar",
  "renderTableBody", "renderStatusBar", "renderTabs", "reloadAll", "snapshotAction",
  "loadConfig", "saveConfig", "loadSnapshots", "runSnapshotAction", "ENV", "RARITIES"
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
  check(Object.keys(T.TAB_BY_ID).length === 10, "标签页数量 = 10", Object.keys(T.TAB_BY_ID).join(","));
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
  check(tabsHtml.split("tab-count").length - 1 === 10, "10 个标签都有条目数徽标");
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
  check(Object.keys(payload).length === 10, "载荷字段数 = 10（9 表 + autoBackup）", Object.keys(payload).length);

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

    console.log("\n" + "=".repeat(62));
    if (failed) {
      console.log("❌ " + failed + " 项未通过");
      process.exit(1);
    }
    console.log("🎉 页面运行时冒烟测试全部通过（" + hookNames.length + " 个内部函数已实际执行）");
    process.exit(0);
  });
}
