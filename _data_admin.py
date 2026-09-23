# -*- coding: utf-8 -*-
"""数据管理：快照 / 导出 / 导入 / 恢复 / 清除 / 自动存档。

原本这 400 多行挤在 main.py 里，单文件太大、改一处怕碰坏全篇，所以拆出来。
这些方法用到 main.py 里的常量与工具函数（logger、BACKUP_MODULE、_safe_int…），
共享方式是「main 在模块末尾把自己的全局注入本模块」——因此下面的代码可以
像还在主文件里一样直接引用它们，一行都不用改。

⚠️ 维护约定：本模块的方法**不要**对共享的模块级变量做重新赋值
（`X = ...` 只会改到本模块的副本），要改就原地改（`X.clear()/update()`、
`X[:] = ...`），或者调用 main 里提供的 helper。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

#: 状态面板（`data_status`，只给人看的那段文字）的最大长度。
#: ⚠️ 一定要有上限：这段文字会写进插件配置，历史上因为它自我嵌套膨胀过几十 KB。
STATUS_MAX_CHARS = 1200


class DataAdminMixin:
    """数据管理相关方法（由 FishingPlugin 继承，见 main.py 的类定义）。"""

    def _init_data_management(self) -> None:
        """启动数据管理：建存档目录、执行配置里排队的操作、起自动存档循环。"""
        self._data_status = ""
        store = getattr(self, "backup_store", None)
        if store is None:
            logger.warning("存档模块不可用，数据管理功能已跳过（_backup.py 是否缺失？）")
            return
        store.ensure_layout()
        try:
            logger.info(
                f"平台：{self._platform_hint()}；"
                f"QQ 官方机器人（qq_official）上会自动带按钮，其它平台自动用纯文本"
            )
        except Exception:  # pragma: no cover
            pass
        logger.info(
            f"存档目录：{store.root}（每日 {self.cfg.get('backup_daily_hour')} 点、"
            f"每 {self.cfg.get('backup_interval_hours')} 小时自动存档，"
            f"手动/导入/清除都在插件配置的「数据管理」里）"
        )
        # 配置保存会热重载插件：这里立刻执行配置里选好的操作
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_data_action())
            self._backup_task = loop.create_task(self._auto_backup_loop())
        except RuntimeError:  # pragma: no cover - 没有事件循环（单测直接调用时）
            self._backup_task = None

    # =========================================================================
    # 数据管理（全部通过插件配置触发，玩家侧没有任何指令）
    # =========================================================================

    #: 配置里可选的数据操作 -> 说明（供状态面板与网页展示）
    DATA_ACTIONS: dict[str, str] = {
        "无": "不执行任何操作",
        "立即存档": "立刻把所有玩家存档成一份快照（manual/）",
        "导出全部玩家": "每个玩家各导出一个 JSON 到 players/，方便单独发回或迁移",
        "导出单个玩家": "导出 data_target 指定的那个玩家",
        "从快照恢复": "用 data_target 指定的快照覆盖全部玩家（留空=最新的快照）",
        "恢复单个玩家": "只恢复 data_target 指定的玩家（快照名写在 data_note）",
        "导入上传的存档": "导入「上传存档文件」里最新上传的那份 JSON",
        "清除单个玩家": "删除 data_target 指定玩家的数据（需勾选确认）",
        "清除全部玩家数据": "删除所有玩家数据（需勾选确认，会先自动存一份档）",
    }

    def _set_status(self, text: str, *, log: bool = True) -> None:
        """把结果写回配置项 data_status，WebUI 刷新即可看到。"""
        stamp = _text_now()
        line = f"[{stamp}] {text}"
        try:
            self.config["data_status"] = line
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception as e:  # pragma: no cover
            logger.debug(f"写回数据状态失败：{e}")
        self._data_status = line
        if log:
            logger.info(f"数据管理：{text}")

    def _platform_hint(self) -> str:
        """给日志用：提示按钮只在 QQ 官方机器人上出现。"""
        platforms: set[str] = set()
        try:
            for player in (self._recent_platforms or {}).values():
                if player:
                    platforms.add(str(player))
        except Exception:
            pass
        return "、".join(sorted(platforms)) if platforms else "等待玩家消息"

    def _data_files_root(self) -> str:
        """上传/导出的落地根目录。

        默认是插件目录（AstrBot 的 file 类型配置会把文件存到 ``files/<配置键>/``）；
        测试里可以把 ``self.data_files_root`` 指到临时目录，免得写脏真插件目录。
        """
        root = getattr(self, "data_files_root", None)
        return str(root) if root else os.path.dirname(os.path.abspath(__file__))

    def _import_dir(self) -> str:
        """WebUI「上传存档文件」那一项的落盘目录（AstrBot 约定：files/<配置键>/）。"""
        return os.path.join(self._data_files_root(), "files", "backup_import_file")

    def _export_dir(self) -> str:
        """WebUI「导出存档文件」那一项的落盘目录（放这里才能在网页里点下载）。"""
        return os.path.join(self._data_files_root(), "files", "backup_export_file")

    async def _snapshot(self, kind: str, note: str = "") -> str:
        """做一份快照，返回给管理员看的说明。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用（_backup.py 缺失？）"
        players = await self._dump_all_players()
        path, payload = store.write_snapshot(kind, players, note=note)
        # 清理会真的删文件，所以每次都把「删了哪几份」写进日志：
        # 出问题时能一眼看出是哪次清理、清掉了什么（历史上排查过一次存档失踪）。
        def _log_pruned(removed: list[str]) -> None:
            if removed:
                logger.info(
                    f"{kind} 存档超出保留份数，已清理 {len(removed)} 份："
                    + "、".join(removed[:5])
                    + ("…" if len(removed) > 5 else "")
                )

        if kind == "auto":
            _log_pruned(store.prune("auto", int(self.cfg.get("backup_keep_interval", 20))))
        if kind == "daily":
            _log_pruned(store.prune_daily_days(int(self.cfg.get("backup_keep_daily", 30))))
        if kind == "manual":
            # 手动存档默认永久保留；站长设了 backup_keep_manual 就只留最近 N 份
            keep_manual = int(self.cfg.get("backup_keep_manual", 0) or 0)
            if keep_manual > 0:
                _log_pruned(store.prune("manual", keep_manual))
        store.rebuild_index()
        return f"{path.name}（{payload['count']} 名玩家）"

    async def _export_players(self, target: str = "") -> str:
        """导出玩家数据到 players/ 与 files/backup_export_file/。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用"
        ids = [target] if target else await self._player_ids()
        if not ids:
            return "没有可导出的玩家（索引为空）"
        done = 0
        export_dir = self._export_dir()
        try:
            os.makedirs(export_dir, exist_ok=True)
        except OSError:
            export_dir = str(store.path_of("exported"))
        for uid in ids:
            try:
                raw = await self.get_kv_data(self._kv_key(uid), None)
            except Exception:
                continue
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            if not isinstance(raw, dict):
                continue
            path = store.export_player(uid, raw)
            if path is None:
                continue
            done += 1
            try:
                with open(path, encoding="utf-8") as src_fp, open(
                    os.path.join(export_dir, os.path.basename(str(path))),
                    "w",
                    encoding="utf-8",
                ) as dst_fp:
                    dst_fp.write(src_fp.read())
            except OSError:
                pass
        # 把文件列表写回「导出存档文件」配置项：WebUI 里会变成可下载的条目
        try:
            names = sorted(
                (
                    name
                    for name in os.listdir(export_dir)
                    if name.lower().endswith(".json")
                ),
                reverse=True,
            )[:100]
            files = [f"files/backup_export_file/{name}" for name in names]
            # 先更新运行时配置（测试里 config 可能就是普通 dict），再尝试落盘
            self.cfg["backup_export_file"] = list(files)
            try:
                self.config["backup_export_file"] = files
                save = getattr(self.config, "save_config", None)
                if callable(save):
                    save()
            except Exception as e:  # pragma: no cover
                logger.debug(f"落盘导出文件列表失败：{e}")
        except Exception as e:  # pragma: no cover
            logger.debug(f"写回导出文件列表失败：{e}")
        store.rebuild_index()
        return (
            f"已导出 {done} 名玩家：存档在 {store.path_of('players')}，"
            f"也能在「导出存档文件」那一项里点文件名下载"
        )

    async def _restore_snapshot(self, snapshot: str, target: str = "") -> str:
        """从快照恢复（target 为空=全部玩家）。恢复前先自动存一份档。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用"
        snap = store.load_snapshot(snapshot)
        if not snap:
            return f"找不到快照「{snapshot or '最新'}」（用「立即存档」先存一份）"
        players = snap.get("players") or {}
        if not players:
            return f"快照「{snapshot or '最新'}」里没有玩家数据"
        await self._snapshot("manual", note="恢复前自动存档")
        restored = 0
        if target:
            raw = players.get(str(target))
            data, _ = BACKUP_MODULE.unwrap_player(raw) if BACKUP_MODULE else (raw, False)
            if data is None:
                return f"快照里没有玩家 {target}"
            await self.put_kv_data(self._kv_key(target), _json_dumps(raw))
            await self._remember_player(str(target))
            restored = 1
        else:
            for uid, raw in players.items():
                data, _ = BACKUP_MODULE.unwrap_player(raw) if BACKUP_MODULE else (raw, False)
                if data is None:
                    continue
                await self.put_kv_data(self._kv_key(str(uid)), _json_dumps(raw))
                await self._remember_player(str(uid))
                restored += 1
        store.rebuild_index()
        return f"已从「{snap.get('path', snapshot)}」恢复 {restored} 名玩家"

    async def _import_uploaded(self) -> str:
        """导入「上传存档文件」里最新的一份 JSON（单玩家信封或整份快照都支持）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用"
        plugin_dir = self._data_files_root()
        candidates: list[str] = []
        # AstrBot 的 file 类型配置存的是相对路径列表（files/<配置键>/xxx.json）
        for rel in self.cfg.get("backup_import_file") or []:
            if isinstance(rel, str) and rel.strip():
                candidates.append(os.path.join(plugin_dir, rel.replace("/", os.sep)))
        folder = self._import_dir()
        if os.path.isdir(folder):
            candidates.extend(
                os.path.join(folder, name)
                for name in os.listdir(folder)
                if name.lower().endswith(".json")
            )
        files = sorted(
            {p for p in candidates if os.path.isfile(p)},
            key=lambda p: os.path.getmtime(p),
            reverse=True,
        )
        if not files:
            return "还没有上传存档（把 JSON 拖到「上传存档文件」那一项，保存后再选这个操作）"
        newest = files[0]
        newest_name = os.path.basename(newest)
        try:
            with open(newest, encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, ValueError) as e:
            return f"上传的 {newest_name} 不是合法 JSON：{e}"
        if not isinstance(payload, dict):
            return f"上传的 {newest_name} 内容格式不对"

        store.copy_into(newest, "manual", f"imported_{newest_name}")
        # ⚠️ 覆盖实时玩家数据之前先存一份（和「清除」「从快照恢复」一致）：
        #    导入是**整体覆盖**，上传错文件 / 上传成别人的存档就再也回不来了。
        #    前置快照让站长还能用「从快照恢复」倒回去。
        await self._snapshot("manual", note="导入前自动存档")
        # 形态一：整份快照
        if isinstance(payload.get("players"), dict):
            count = 0
            for uid, raw in payload["players"].items():
                data, _ = BACKUP_MODULE.unwrap_player(raw) if BACKUP_MODULE else (raw, False)
                if data is None:
                    continue
                await self.put_kv_data(self._kv_key(str(uid)), _json_dumps(raw))
                await self._remember_player(str(uid))
                count += 1
            return f"已从 {newest_name} 导入 {count} 名玩家（快照格式）"
        # 形态二：单个玩家信封
        data, _ = BACKUP_MODULE.unwrap_player(payload) if BACKUP_MODULE else (payload, False)
        uid = str(payload.get("user_id") or self.cfg.get("data_target") or "").strip()
        if data is None or not uid:
            return "单个玩家存档需要带 user_id 字段（或在 data_target 里填玩家ID）"
        await self.put_kv_data(self._kv_key(uid), _json_dumps(payload))
        await self._remember_player(uid)
        return f"已导入玩家 {uid}（来自 {newest_name}）"

    async def _clear_players(self, target: str = "") -> str:
        """清除玩家数据（清除前先自动存档）。"""
        store = getattr(self, "backup_store", None)
        if store is None:
            return "存档模块不可用"
        ids = [target] if target else await self._player_ids()
        if not ids:
            return "没有要清除的玩家"
        await self._snapshot("manual", note="清除前自动存档")
        removed = 0
        for uid in ids:
            try:
                await self.delete_kv_data(self._kv_key(uid))
                removed += 1
            except Exception as e:
                logger.warning(f"清除玩家 {uid} 失败：{e}")
        if not target:
            try:
                await self.put_kv_data("player_index", _json_dumps([]))
            except Exception:
                pass
        else:
            try:
                keep = [x for x in await self._player_ids() if x != str(target)]
                await self.put_kv_data("player_index", _json_dumps(keep))
            except Exception:
                pass
        store.rebuild_index()
        return f"已清除 {removed} 名玩家的数据（清除前的快照已保存）"

    async def _run_data_action(self) -> None:
        """执行配置里选择的数据操作，然后把它复位成「无」。"""
        action = str(self.cfg.get("data_action") or "无").strip()
        if action in ("", "无", "none", "None"):
            return
        target = str(self.cfg.get("data_target") or "").strip()
        confirm = bool(self.cfg.get("data_confirm"))
        # ⚠️「导入上传的存档」也在危险名单里：它会 put_kv_data **直接覆盖**实时玩家
        #    （整份快照形态 = 覆盖一大批人），以前既不要确认、也不做前置快照。
        dangerous = action in (
            "清除单个玩家",
            "清除全部玩家数据",
            "从快照恢复",
            "导入上传的存档",
        )
        try:
            if dangerous and not confirm:
                result = f"「{action}」需要先勾选「我已确认」再保存配置"
            elif action == "立即存档":
                result = "已存档：" + await self._snapshot("manual", note="配置里手动触发")
            elif action == "导出全部玩家":
                result = await self._export_players()
            elif action == "导出单个玩家":
                result = (
                    await self._export_players(target)
                    if target
                    else "请在 data_target 里填要导出的玩家 ID"
                )
            elif action == "从快照恢复":
                result = await self._restore_snapshot(target)
            elif action == "恢复单个玩家":
                # data_target 支持「快照名/玩家ID」；只写玩家 ID 时从最新快照恢复
                snap_name, _, only_player = str(target).partition("/")
                if not only_player:
                    snap_name, only_player = "", snap_name
                result = (
                    await self._restore_snapshot(snap_name.strip(), only_player.strip())
                    if only_player.strip()
                    else "请在 data_target 里填玩家 ID（想指定快照就写「快照名/玩家ID」）"
                )
            elif action == "导入上传的存档":
                result = await self._import_uploaded()
            elif action == "清除单个玩家":
                result = (
                    await self._clear_players(target)
                    if target
                    else "请在 data_target 里填要清除的玩家 ID"
                )
            elif action == "清除全部玩家数据":
                result = await self._clear_players()
            else:
                result = f"不认识的操作「{action}」"
        except Exception as e:
            logger.error(f"数据操作「{action}」执行失败：{e}", exc_info=True)
            result = f"「{action}」执行失败：{e}"

        # 复位动作 + 回写状态
        try:
            self.config["data_action"] = "无"
            self.config["data_confirm"] = False
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception as e:  # pragma: no cover
            logger.debug(f"复位数据动作失败：{e}")
        self.cfg["data_action"] = "无"
        self.cfg["data_confirm"] = False
        self._set_status(result)
        await self._refresh_data_status(extra=result)

    async def _refresh_data_status(self, extra: str = "") -> None:
        """刷新状态面板：玩家数、存档数量、最近快照、目录。

        ⚠️ v1.18.16 修的坑：这里以前把**上一版面板**（`self._data_status` 里存的整段）
        又嵌进新面板的「上次操作」行里，然后 `self._data_status = 面板`，
        于是每 60 秒刷一次就把自己复制一遍 —— 站长的配置里那段文字已经膨胀成几十 KB，
        WebUI 的「数据状态」彻底没法看（而且每 60 秒写一次配置）。
        现在：`self._data_status` **只存「上一次操作」那一句短的**（由 `_set_status` 写），
        面板每次现拼、限长，**内容没变就不写盘**。
        """
        store = getattr(self, "backup_store", None)
        if store is None:
            return
        try:
            ids = await self._player_ids()
            index = store.rebuild_index()
            items = index.get("snapshots") or []
            latest = items[0] if items else None
            lines = [
                f"玩家数：{len(ids)}",
                f"存档数：{index.get('total', 0)}"
                f"（每日 {index['by_kind'].get('daily', 0)} / 按时 "
                f"{index['by_kind'].get('auto', 0)} / 手动 "
                f"{index['by_kind'].get('manual', 0)}）",
                f"最近快照：{(latest or {}).get('rel', '无')}"
                + (f"（{latest.get('mtime_text', '')}，{latest.get('count', 0)} 名玩家）" if latest else ""),
                f"目录：{store.root}",
                f"自动存档：{'开' if self.cfg.get('enable_auto_backup') else '关'}"
                f"（每 {self.cfg.get('backup_interval_hours')} 小时 / 每天 "
                f"{self.cfg.get('backup_daily_hour')} 点）",
                f"上次操作：{self._data_status or '无'}",
            ]
            if extra:
                lines.append(f"结果：{extra}")
            text = "\n".join(lines)
            # 限长：面板只给人看，多余的直接截断（绝不把它再喂回自己）
            if len(text) > STATUS_MAX_CHARS:
                text = text[:STATUS_MAX_CHARS] + "…"
            if str(self.config.get("data_status") or "") == text:
                return                      # 没变就不写盘，省掉每分钟一次的配置写入
            self.config["data_status"] = text
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception as e:  # pragma: no cover
            logger.debug(f"刷新数据状态失败：{e}")

    async def _auto_backup_loop(self) -> None:
        """后台循环：按时存档 + 每日存档 + 顺便执行配置里排队的操作。"""
        try:
            await asyncio.sleep(20)  # 启动后先等一会儿，别和加载抢 IO
            while True:
                try:
                    if not self.cfg.get("enable_auto_backup"):
                        pass
                    else:
                        store = getattr(self, "backup_store", None)
                        today = self._today_text()
                        items = store.list_snapshots() if store else []
                        # 每日存档：今天还没有就存一份（绝不覆盖已有的）
                        has_daily = any(
                            item["kind"] == "daily"
                            and item["name"].startswith(today)
                            for item in items
                        )
                        now_hour = int(time.strftime("%H"))
                        if not has_daily and now_hour >= int(
                            self.cfg.get("backup_daily_hour", 4)
                        ):
                            await self._snapshot("daily", note="每日自动存档")
                        # 按时存档
                        interval = int(self.cfg.get("backup_interval_hours", 6))
                        if interval > 0:
                            auto_items = [x for x in items if x["kind"] == "auto"]
                            last_ts = auto_items[0]["mtime"] if auto_items else 0
                            if time.time() - last_ts >= interval * 3600:
                                await self._snapshot("auto", note=f"每 {interval} 小时自动存档")
                    await self._refresh_data_status()
                    await self._run_data_action()
                except Exception as e:
                    logger.warning(f"自动存档循环出错（会继续跑）：{e}")
                await asyncio.sleep(60)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception as e:  # pragma: no cover
            logger.error(f"自动存档循环退出：{e}")

    #: 会被「自动合并」的内容型配置项（按每行第一个 | 前的 id 去重）
    CONTENT_LIST_KEYS: tuple[str, ...] = (
        "location_defs", "rod_defs", "bait_defs", "item_defs", "title_defs",
    )

