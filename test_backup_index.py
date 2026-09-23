# -*- coding: utf-8 -*-
"""``_backup.py`` 的 index.json 重建自测（独立可跑：``python test_backup_index.py``）。

只测一件事：``BackupStore.rebuild_index()`` 的三条约定 ——

1. **内容不变就不写盘**：插件里 ``_data_admin._refresh_data_status()`` 每 60 秒就会
   调一次它，所以连续调用后 ``index.json`` 的 ``st_mtime_ns`` 必须一动不动；
2. **内容变了要写盘且写对**：新增/删除/改备注之后，清单内容跟着更新；
3. **写盘是原子的、异常是安全的**：临时文件 + ``os.replace``，出错只记日志降级，
   绝不抛给调用方，也绝不破坏已有的索引文件。

⚠️ 红线：所有用例的存档目录都钉在**临时沙箱目录**里（见 ``sandbox()``），测试结束
时删干净；绝不允许碰仓库里真实的 ``backups/``（历史上出过真实存档被测试删掉的事故，
所以最后还有一道「真存档一个字节都没变」的自检）。

纯标准库、不用 astrbot（``_backup.py`` 本身就是纯标准库模块），因此跑得很快。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import itertools
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent


def _load_backup_module():
    """按路径加载同目录的 ``_backup.py``（不 import astrbot，也不碰插件主流程）。"""
    path = PLUGIN_DIR / "_backup.py"
    spec = importlib.util.spec_from_file_location(
        "astrbot_fishing_backup_index_test", path
    )
    if spec is None or spec.loader is None:  # pragma: no cover - 环境异常
        raise RuntimeError(f"加载不了 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bk = _load_backup_module()

#: 建过的沙箱目录（收尾自检：这些必须都被删干净）
SANDBOXES: list[str] = []
#: ``tempfile.mkdtemp()`` 建出来的目录能不能用（``None`` = 还没试过）
_MKDTEMP_USABLE: bool | None = None
#: 环境不让删的目录（只提示，不算失败）
STUCK_DIRS: list[str] = []
_SANDBOX_SEQ = itertools.count(1)


# =============================================================================
# 测试脚手架：临时沙箱 / 计数 / 日志捕获 / 故障注入
# =============================================================================


def _writable(folder: str) -> bool:
    """目录能不能真的往里写文件（有的受限环境只给「建目录」不给「往里写」）。"""
    probe = os.path.join(folder, ".write_probe")
    try:
        with open(probe, "w", encoding="utf-8") as fp:
            fp.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def make_sandbox_dir(tag: str) -> str:
    """建一个临时沙箱目录，返回它的路径。

    ⚠️ 只用系统临时目录，**绝不碰仓库里的 backups/**。

    首选 ``tempfile.mkdtemp()``（标准做法）。个别受限环境（本机的 DSH 文件沙箱会给
    ``mkdtemp`` 建的 0o700 目录设一套 ACL：建得出来、写不进去、也删不掉）会退化：
    这时改用 ``os.makedirs`` 建一个同样随机、同样落在系统临时目录下的目录 ——
    路径写法不同，隔离性与清理效果一样。这个能力只探测一次，免得每个用例都留一个
    删不掉的空目录。
    """
    global _MKDTEMP_USABLE
    if _MKDTEMP_USABLE is not False:  # 只在第一次（或确认可用时）才试 mkdtemp
        folder = tempfile.mkdtemp(prefix=f"fishing_index_{tag}_")
        if _writable(folder):
            _MKDTEMP_USABLE = True
            SANDBOXES.append(folder)
            return folder
        _MKDTEMP_USABLE = False
        try:  # 退路：先把刚建的空目录删掉（删不掉也无所谓，里面一个文件都没有）
            os.rmdir(folder)
        except OSError:
            STUCK_DIRS.append(folder)
    folder = os.path.join(
        tempfile.gettempdir(),
        f"fishing_index_{tag}_{os.getpid()}_{next(_SANDBOX_SEQ)}",
    )
    os.makedirs(folder, exist_ok=True)
    SANDBOXES.append(folder)
    return folder


@contextlib.contextmanager
def sandbox(tag: str):
    """临时沙箱：``with sandbox("xxx") as backups:``，退出时整个删掉。

    产出的是**存档目录**（``<沙箱>/backups``），用例只能往它里面写。
    """
    root = make_sandbox_dir(tag)
    try:
        yield os.path.join(root, "backups")
    finally:
        shutil.rmtree(root, ignore_errors=True)


@contextlib.contextmanager
def counting_writes():
    """把模块里的原子写换成计数版：``with counting_writes() as calls:``。

    ``calls[0]`` 是区间内**真正写成功**的次数（抛错的写不算）。用它证明「写了 / 没写」
    比只看 mtime 稳：有的文件系统时间戳精度粗（同一时钟滴答内的两次写入拿到的是同一个
    ``st_mtime_ns``）。
    """
    calls = [0]
    original = bk._atomic_write_text

    def wrapper(path, text):
        result = original(path, text)
        calls[0] += 1
        return result

    bk._atomic_write_text = wrapper
    try:
        yield calls
    finally:
        bk._atomic_write_text = original


class FakeLogger:
    """接住模块日志（``_backup.py`` 的 logger 由 main 注入，测试里自己塞一个）。"""

    def __init__(self, boom: bool = False) -> None:
        self.lines: list[tuple[str, str]] = []
        self._boom = boom

    def _record(self, level: str, text: str) -> None:
        if self._boom:
            raise RuntimeError("模拟：日志后端自己炸了")
        self.lines.append((level, str(text)))

    def warning(self, text: str) -> None:
        self._record("warning", text)

    def debug(self, text: str) -> None:
        self._record("debug", text)

    def info(self, text: str) -> None:
        self._record("info", text)


@contextlib.contextmanager
def capture_logs(boom: bool = False):
    """临时把 logger 注入 ``_backup`` 模块，用完恢复「没注入」的原样。"""
    log = FakeLogger(boom=boom)
    vars(bk)["logger"] = log
    try:
        yield log
    finally:
        vars(bk).pop("logger", None)


@contextlib.contextmanager
def broken_replace(message: str = "模拟：替换文件时磁盘故障"):
    """让 ``os.replace`` 抛错，模拟「写盘/替换中途出故障」。"""
    original = os.replace

    def boom(src, dst):
        raise OSError(message)

    os.replace = boom  # _backup.py 用的是同一个 os 模块，这里能拦住
    try:
        yield
    finally:
        os.replace = original


def read_index(backups: str) -> dict:
    """读回 ``index.json``（解析不了会直接抛，正好证明没写出坏 JSON）。"""
    raw = Path(backups, "index.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert isinstance(data, dict), "index.json 顶层应该是对象"
    return data


def index_mtime_ns(backups: str) -> int:
    return os.stat(os.path.join(backups, "index.json")).st_mtime_ns


def tmp_leftovers(backups: str) -> list[str]:
    """目录（含子目录）里残留的 ``*.tmp`` 之类临时文件。"""
    return sorted(str(p.relative_to(backups)) for p in Path(backups).rglob("*.tmp"))


def wait_for_next_tick(reference_ns: int, need_ms: int = 120) -> None:
    """等到系统时钟比 ``reference_ns`` 至少前进 ``need_ms`` 毫秒。

    本机 NTFS 的时间戳跟着系统时钟滴答走（同一滴答内多次写入拿到的是同一个
    ``st_mtime_ns``）。所以「mtime 没变」这条断言必须先跨过滴答才有意义 ——
    否则就算真重写了也可能看不出变化，测试会变成假绿。
    """
    deadline = time.monotonic() + 2.0
    while (
        time.time_ns() - reference_ns < need_ms * 1_000_000
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)


def real_backup_digests() -> dict[str, str]:
    """仓库里真 ``backups/`` 下每个文件的「相对路径 -> 内容摘要」。

    ⚠️ 有意跳过 ``index.json``：站长那台正在运行的实例每分钟会重写它（正是本次要
    优化的对象）。比对时也只要求「已有文件没被改、没被删」——允许新增，因为实例
    可能刚好落了份自动存档。
    """
    root = PLUGIN_DIR / "backups"
    state: dict[str, str] = {}
    if not root.is_dir():
        return state
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if name.startswith("index.json"):
                continue
            path = Path(dirpath) / name
            rel = str(path.relative_to(PLUGIN_DIR))
            try:
                state[rel] = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
            except OSError:  # pragma: no cover - 读不到就标记一下
                state[rel] = "unreadable"
    return state


# =============================================================================
# 用例
# =============================================================================


def test_create_when_missing(backups: str) -> None:
    """[1] ``index.json`` 不存在 → 会创建（内容与结构都正确）。"""
    print("\n[1] index.json 不存在时会创建")
    store = bk.BackupStore(backups, 5)
    store.ensure_layout()  # 插件启动时就是这么做的（_init_data_management）
    index_path = os.path.join(backups, "index.json")
    check(not os.path.exists(index_path), "起始状态：目录在，但 index.json 不存在")
    check(
        not str(store.root).startswith(str(PLUGIN_DIR)),
        "存档仓库钉在临时沙箱里（不在仓库目录内）",
        str(store.root),
    )

    with counting_writes() as calls:
        payload = store.rebuild_index()
    check(os.path.isfile(index_path), "调用后 index.json 被创建")
    check(calls[0] == 1, "创建确实走了一次原子写", f"写盘 {calls[0]} 次")
    check(
        payload.get("total") == 0 and payload.get("snapshots") == [],
        "空存档：total=0 / snapshots=[]",
    )
    check(
        payload.get("by_kind") == {"daily": 0, "auto": 0, "manual": 0},
        "空存档：by_kind 三个目录都是 0",
        payload.get("by_kind"),
    )

    disk = read_index(backups)  # 解析不了会直接抛 —— 顺便证明没写坏 JSON
    check(
        disk.get(bk.SNAPSHOT_KEY) == bk.SNAPSHOT_VERSION,
        "文件里带 __fishing_backup__ 版本标记",
    )
    for key in (
        "updated_at",
        "updated_text",
        "data_version",
        "root",
        "total",
        "by_kind",
        "snapshots",
        "players",
    ):
        check(key in disk, f"索引结构里有 {key}")
    check(
        disk.get("data_version") == 5 and disk.get("root") == str(store.root),
        "data_version / root 写对了",
    )
    check(
        isinstance(disk.get("updated_at"), int) and bool(disk.get("updated_text")),
        "updated_at / updated_text 照旧是时间戳",
    )
    check(tmp_leftovers(backups) == [], "没有留下 *.tmp", tmp_leftovers(backups))


def test_unchanged_skips_write(backups: str) -> None:
    """[2] 内容不变 → 不写盘（``st_mtime_ns`` 完全不变，冷缓存也一样）。"""
    print("\n[2] 内容没变就不写盘（mtime 一动不动）")
    store = bk.BackupStore(backups, 5)
    store.ensure_layout()
    store.rebuild_index()  # 第一次：把文件建出来
    before = index_mtime_ns(backups)

    wait_for_next_tick(before)  # 跨过时钟滴答，「没写」才是可证伪的
    with counting_writes() as calls:
        store.rebuild_index()  # 第二次、第三次：同进程、缓存命中
        store.rebuild_index()
    check(calls[0] == 0, "同进程反复调用不再写盘", f"写盘 {calls[0]} 次")
    check(
        index_mtime_ns(backups) == before,
        "第二次之后 st_mtime_ns 完全不变",
        f"{index_mtime_ns(backups)} vs {before}",
    )

    # 冷缓存（新实例）= 模拟插件重启：内容一样也不许重写
    fresh = bk.BackupStore(backups, 5)
    wait_for_next_tick(index_mtime_ns(backups))
    with counting_writes() as calls:
        payload = fresh.rebuild_index()
    check(calls[0] == 0, "换实例（进程内缓存为空）也不重复写", f"写盘 {calls[0]} 次")
    check(index_mtime_ns(backups) == before, "冷缓存下 st_mtime_ns 仍然不变")
    check(payload.get("total") == 0, "冷缓存返回的清单内容照旧正确")

    # 跨秒：要是比较时把 updated_at / updated_text 也算进去，这里必然重写
    deadline = time.monotonic() + 3.0
    second = int(time.time())
    while int(time.time()) == second and time.monotonic() < deadline:
        time.sleep(0.02)
    cold = bk.BackupStore(backups, 5)
    wait_for_next_tick(index_mtime_ns(backups))
    with counting_writes() as calls:
        cold.rebuild_index()
    check(
        calls[0] == 0,
        "时间戳本该变了，依然判定「内容没变」",
        f"写盘 {calls[0]} 次",
    )
    check(index_mtime_ns(backups) == before, "跨秒后 st_mtime_ns 还是不变")

    # 进程内缓存真的生效：命中时连扫盘都不做（不是「扫一遍再决定不写」）
    hitting = bk.BackupStore(backups, 5)
    hitting.rebuild_index()  # 先热一次
    scans = [0]
    original_scan = hitting.list_snapshots

    def counted_scan():
        scans[0] += 1
        return original_scan()

    hitting.list_snapshots = counted_scan  # type: ignore[method-assign]
    with counting_writes() as calls:
        hitting.rebuild_index()
    check(
        scans[0] == 0 and calls[0] == 0,
        "缓存命中：不扫盘、不写盘（几乎零开销）",
        f"扫盘 {scans[0]} 次 / 写盘 {calls[0]} 次",
    )


def test_content_change_rewrites(backups: str) -> None:
    """[3] 内容变了 → 会写盘，且索引内容随之更新。"""
    print("\n[3] 内容变了就写盘，且内容正确")
    store = bk.BackupStore(backups, 7)
    store.ensure_layout()
    store.rebuild_index()
    now = 1_800_000_000.0  # 固定时间：快照名可预期，也免得同分钟重名

    # --- 新增一份手动存档 ---
    wait_for_next_tick(index_mtime_ns(backups))
    with counting_writes() as calls:
        path, _payload = store.write_snapshot(
            "manual", {"1001": {"gold": 10}}, note="第一份", now=now
        )
    disk = read_index(backups)
    check(calls[0] >= 1, "新增快照：确实写盘了", f"写盘 {calls[0]} 次")
    check(disk.get("total") == 1, "清单 total 变成 1", disk.get("total"))
    check(disk.get("by_kind", {}).get("manual") == 1, "by_kind.manual 变成 1")
    entry = (disk.get("snapshots") or [{}])[0]
    check(entry.get("rel") == f"manual/{path.name}", "清单里就是这份快照", entry.get("rel"))
    check(entry.get("note") == "第一份", "备注跟着写进清单", entry.get("note"))
    check(entry.get("count") == 1, "清单里的玩家数正确", entry.get("count"))

    # --- 再存一份 ---
    with counting_writes() as calls:
        path2, _payload = store.write_snapshot(
            "manual", {"1002": {"gold": 20}}, note="第二份", now=now + 60
        )
    disk = read_index(backups)
    check(calls[0] >= 1, "又变了：又写了一次", f"写盘 {calls[0]} 次")
    check(
        disk.get("total") == 2 and disk.get("by_kind", {}).get("manual") == 2,
        "两份都在清单里",
        disk.get("total"),
    )
    check(
        {x["name"] for x in disk.get("snapshots", [])} == {path.name, path2.name},
        "清单里的文件名和磁盘一致",
        [x["name"] for x in disk.get("snapshots", [])],
    )

    # --- 改备注（原地重写快照文件，文件名不变）---
    new_note = "改过的备注，故意写得长一点"
    changed = store.rename_snapshot(path.name, new_note)
    disk = read_index(backups)
    check(changed, "rename_snapshot 找到了这份快照")
    renamed = next(
        (x for x in disk.get("snapshots", []) if x["name"] == path.name), {}
    )
    check(renamed.get("note") == new_note, "快照备注变了，清单跟着更新", renamed.get("note"))
    check(disk.get("total") == 2, "改备注不影响份数", disk.get("total"))

    # --- 删掉一份 ---
    wait_for_next_tick(index_mtime_ns(backups))
    with counting_writes() as calls:
        removed = store.delete_snapshot(path2.name)
    disk = read_index(backups)
    check(removed, "delete_snapshot 成功")
    check(calls[0] >= 1, "少了内容：清单重写", f"写盘 {calls[0]} 次")
    check(disk.get("total") == 1, "删掉一份后 total=1", disk.get("total"))
    check(
        all(x["name"] != path2.name for x in disk.get("snapshots", [])),
        "被删的那份不在清单里",
    )
    check(
        [x["name"] for x in disk.get("snapshots", [])] == [path.name],
        "清单里只剩没删的那份",
        [x["name"] for x in disk.get("snapshots", [])],
    )

    # --- players/ 单玩家导出也会进清单 ---
    store.export_player("1001", {"gold": 10}, now=now)
    store.rebuild_index()
    disk = read_index(backups)
    check(
        [p["user_id"] for p in disk.get("players", [])] == ["1001"],
        "players/ 里有导出文件时，清单的 players 段跟着更新",
        disk.get("players"),
    )

    # --- 非空存档下，冷缓存也不重复写 ---
    before = index_mtime_ns(backups)
    wait_for_next_tick(before)
    with counting_writes() as calls:
        bk.BackupStore(backups, 7).rebuild_index()
    check(
        calls[0] == 0 and index_mtime_ns(backups) == before,
        "有快照时同样做到「内容不变不写盘」",
        f"写盘 {calls[0]} 次",
    )
    check(tmp_leftovers(backups) == [], "这一串操作没留下 *.tmp", tmp_leftovers(backups))


def test_atomic_write(backups: str) -> None:
    """[4] 原子替换：正常路径与异常路径都不留 ``*.tmp``，也不破坏已有索引。"""
    print("\n[4] 原子替换（临时文件 + os.replace）")
    store = bk.BackupStore(backups, 5)
    store.ensure_layout()
    store.rebuild_index()
    check(tmp_leftovers(backups) == [], "正常写盘后没有 *.tmp", tmp_leftovers(backups))

    # 先塞一个「上次崩溃留下的」同名临时文件：下次真写盘时应该被处理掉。
    # ⚠️ 名字必须带上一个**已经死掉**的 pid（见 `_backup._atomic_write_text` 的
    #    唯一化命名）：用一个几乎不可能存在的 pid 冒充崩溃残留。
    stale = Path(backups, "index.json.999999.tmp")
    stale.write_text("{ 半截垃圾", encoding="utf-8")
    store.write_snapshot("manual", {"3001": {"gold": 1}}, note="顺手清垃圾", now=1_800_000_000.0)
    check(not stale.exists(), "残留的 index.json.tmp 被写盘流程处理掉")
    check(tmp_leftovers(backups) == [], "处理完依然没有 *.tmp")

    # ---- 故障注入：os.replace 抛错 ----
    before_disk = read_index(backups)
    before_mtime = index_mtime_ns(backups)
    extra = Path(backups, "auto", "2026-02-02_0300.json")
    extra.write_text(
        json.dumps(
            {
                bk.SNAPSHOT_KEY: bk.SNAPSHOT_VERSION,
                "kind": "auto",
                "kind_name": "按时存档",
                "created_text": "2026-02-02 03:00:00",
                "count": 2,
                "note": "故障注入用",
                "players": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with capture_logs() as log:
        with broken_replace():
            try:
                payload = store.rebuild_index()  # 不许抛异常
                raised = None
            except Exception as exc:  # pragma: no cover - 真抛了就失败
                raised = exc
                payload = {}
    check(raised is None, "写盘失败也不抛给调用方", raised)
    check(
        payload.get("total") == before_disk.get("total", 0) + 1,
        "降级返回的仍是最新扫出来的清单",
        f"total={payload.get('total')}",
    )
    check(tmp_leftovers(backups) == [], "异常路径上临时文件被清理干净", tmp_leftovers(backups))
    check(
        read_index(backups) == before_disk,
        "原有索引文件一个字段都没被动（没被写坏）",
    )
    check(index_mtime_ns(backups) == before_mtime, "原有索引的 mtime 也没动")
    check(
        any(level == "warning" and "清单" in text for level, text in log.lines),
        "失败只记日志（warning 降级）",
        log.lines[:2],
    )

    # 恢复之后：缓存已作废，下一次调用应该把没写成的补上
    wait_for_next_tick(index_mtime_ns(backups))
    with counting_writes() as calls:
        store.rebuild_index()
    disk = read_index(backups)
    check(calls[0] == 1, "故障恢复后重试写入（没有被缓存骗过去）", f"写盘 {calls[0]} 次")
    check(
        disk.get("total") == before_disk.get("total", 0) + 1,
        "重试后索引补上了新快照",
        disk.get("total"),
    )
    check(tmp_leftovers(backups) == [], "重试成功后也没有 *.tmp", tmp_leftovers(backups))

    # 日志本身出问题也不许连累调用方
    safe = True
    with capture_logs(boom=True):
        try:
            bk._log_warning("日志后端炸了也不许抛")
        except Exception:  # pragma: no cover - 抛了就失败
            safe = False
    check(safe, "logger 自己抛异常时静默降级")
    vars(bk).pop("logger", None)
    safe = True
    try:
        bk._log_warning("没注入 logger 时静默降级")
    except Exception:  # pragma: no cover - 抛了就失败
        safe = False
    check(safe, "没注入 logger 时不抛异常（纯标准库模块的默认状态）")


def test_semantic_compare(backups: str) -> None:
    """[5] 「内容不变」的判定口径：忽略易变字段，也不受写法影响。"""
    print("\n[5] 判「内容不变」的口径")
    check(
        list(bk.INDEX_VOLATILE_KEYS) == ["updated_at", "updated_text"],
        "易变字段清单 = updated_at / updated_text",
        bk.INDEX_VOLATILE_KEYS,
    )
    check(
        bk._index_semantic(
            {"updated_at": 1, "updated_text": "x", "total": 0, "snapshots": []}
        )
        == {"total": 0, "snapshots": []},
        "比较时正好剥掉那两个时间戳字段",
    )
    check(bk._index_semantic("不是字典") is None, "非字典内容按「不一致」处理")
    check(
        bk._index_semantic({"total": 1}) == bk._index_semantic({"total": 1}),
        "同一份内容摘要稳定（不会每次算出来都不一样）",
    )

    store = bk.BackupStore(backups, 5)
    store.ensure_layout()
    store.rebuild_index()
    disk = read_index(backups)
    check(
        "updated_at" in disk and "updated_text" in disk,
        "文件里照旧保留时间戳字段（对外结构没改）",
    )

    # 换个写法（键排序 + 另一种缩进 + LF 换行）重写同一份内容 → 语义一致，不该再写
    with open(
        os.path.join(backups, "index.json"), "w", encoding="utf-8", newline="\n"
    ) as fp:
        fp.write(json.dumps(disk, ensure_ascii=False, sort_keys=True, indent=4))
    reformatted = index_mtime_ns(backups)
    wait_for_next_tick(reformatted)
    with counting_writes() as calls:
        bk.BackupStore(backups, 5).rebuild_index()
    check(
        calls[0] == 0,
        "字段顺序 / 缩进 / 换行不同，也算「内容没变」",
        f"写盘 {calls[0]} 次",
    )
    check(
        index_mtime_ns(backups) == reformatted,
        "mtime 保持在重写后的值，没被覆盖",
    )
    check(read_index(backups) == disk, "内容读回来还是同一份", None)


def test_broken_index_recovers(backups: str) -> None:
    """[6] 索引文件坏掉 → 自愈重写，不抛异常。"""
    print("\n[6] index.json 坏掉能自愈")
    store = bk.BackupStore(backups, 5)
    store.ensure_layout()
    store.write_snapshot("daily", {"2001": {"gold": 1}}, note="自愈用", now=1_800_000_000.0)
    Path(backups, "index.json").write_text("{ 这不是 JSON，是半截文件", encoding="utf-8")

    with capture_logs() as log:
        with counting_writes() as calls:
            try:
                payload = store.rebuild_index()
                raised = None
            except Exception as exc:  # pragma: no cover - 抛了就失败
                raised = exc
                payload = {}
    disk = read_index(backups)
    check(raised is None, "文件坏掉也不抛异常", raised)
    check(calls[0] == 1, "坏文件会被重写", f"写盘 {calls[0]} 次")
    check(disk.get("total") == 1, "重写后的内容正确（快照还在）", disk.get("total"))
    check(payload.get("total") == 1, "返回的清单也是对的")
    check(tmp_leftovers(backups) == [], "自愈后不留 *.tmp", tmp_leftovers(backups))
    check(
        log.lines == [] or all(level != "warning" for level, _t in log.lines),
        "自愈属于正常路径，不该报 warning",
        log.lines[:2],
    )

    # 文件在、但内容是「另一个类型」（比如数组）：一样按不一致处理，重写
    Path(backups, "index.json").write_text("[1, 2, 3]", encoding="utf-8")
    with counting_writes() as calls:
        payload = store.rebuild_index()
    check(calls[0] == 1, "内容不是对象时也会重写", f"写盘 {calls[0]} 次")
    check(read_index(backups).get("total") == 1, "重写后依然是正确的对象")


def test_missing_root_degrades(backups: str) -> None:
    """[7] 连存档目录都没有：不抛异常，降级返回结构完整的空清单。"""
    print("\n[7] 存档目录不存在时只降级")
    store = bk.BackupStore(os.path.join(backups, "还没建的目录"), 5)
    with capture_logs() as log:
        try:
            payload = store.rebuild_index()
            raised = None
        except Exception as exc:  # pragma: no cover - 抛了就失败
            raised = exc
            payload = {}
    check(raised is None, "目录不存在也不抛异常", raised)
    check(isinstance(payload, dict), "仍然返回一个 dict")
    check(
        payload.get("total") == 0 and payload.get("snapshots") == [],
        "降级清单的 total / snapshots 是空的",
    )
    check(
        payload.get("by_kind") == {"daily": 0, "auto": 0, "manual": 0},
        "降级清单的 by_kind 结构不变",
        payload.get("by_kind"),
    )
    check(
        sorted(payload) == sorted(store._empty_index()),
        "降级清单的字段和正常清单完全一致（读它的人不用改）",
        sorted(payload),
    )
    check(
        any(level == "warning" for level, _t in log.lines),
        "写不进去时记一条 warning",
        log.lines[:2],
    )


# =============================================================================
# 汇总
# =============================================================================

PASSED = 0
FAILURES: list[str] = []


def check(cond, label, extra=None):
    """断言；extra 只在失败时打出来（和 test_local.py / test_integration.py 一个风格）。"""
    global PASSED
    line = label if extra is None else f"{label}　{extra}"
    if cond:
        PASSED += 1
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {line}")
        FAILURES.append(line)


def main() -> int:
    print("=" * 64)
    print("_backup.py 存档清单（index.json）重建自测")
    print(f"被测模块：{PLUGIN_DIR / '_backup.py'}")
    print(f"临时目录：{tempfile.gettempdir()}")
    print("=" * 64)
    real_before = real_backup_digests()

    with sandbox("create") as backups:
        test_create_when_missing(backups)
    with sandbox("unchanged") as backups:
        test_unchanged_skips_write(backups)
    with sandbox("changed") as backups:
        test_content_change_rewrites(backups)
    with sandbox("atomic") as backups:
        test_atomic_write(backups)
    with sandbox("semantic") as backups:
        test_semantic_compare(backups)
    with sandbox("broken") as backups:
        test_broken_index_recovers(backups)
    with sandbox("noroot") as backups:
        test_missing_root_degrades(backups)

    # ---- 收尾自检 ----
    print("\n[8] 收尾自检（红线）")
    real_after = real_backup_digests()
    touched = [
        rel for rel, digest in real_before.items() if real_after.get(rel) != digest
    ]
    check(
        not touched,
        "仓库里真实的 backups/ 一个字节都没被改（index.json 除外，那是在跑的实例在写）",
        touched,
    )
    real_root = PLUGIN_DIR / "backups"
    check(
        not real_root.is_dir() or bool(real_before),
        "真 backups/ 存在时自检确实扫到了文件（比对有效）",
        len(real_before),
    )
    leftovers = [path for path in SANDBOXES if os.path.exists(path)]
    check(not leftovers, "临时沙箱目录全部清理干净", leftovers)
    if STUCK_DIRS:
        print(
            "ℹ️  本环境的 tempfile.mkdtemp() 目录不可写（文件沙箱 ACL），已自动改用\n"
            "    os.makedirs 建沙箱；下面这些**空**目录是本环境不允许删除的（非本测试\n"
            f"    所能控制）：{STUCK_DIRS}"
        )
    check(len(STUCK_DIRS) <= 1, "能力探测只做一次，最多留一个删不掉的环境残留", STUCK_DIRS)

    print("\n" + "=" * 64)
    if FAILURES:
        print(f"❌ {len(FAILURES)} 项未通过（通过 {PASSED} 项）：")
        for line in FAILURES:
            print(f"   - {line}")
        return 1
    print(f"🎉 全部通过：{PASSED} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
