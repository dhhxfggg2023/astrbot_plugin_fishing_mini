"""示例扩展：给钓鱼插件加两个自己的道具效果（v1.13.0）。

**怎么用**：这个文件就是模板。复制一份改名（例如 ``my_effect.py``）再改内容即可，
插件每次启动 / 重载会自动扫描 ``extensions/*.py``。想临时停用某个扩展，
把文件名前面加个下划线（``_my_effect.py``）就会被跳过。

扩展只需要暴露两个模块级变量（都是可选的）：

``EFFECTS``
    新效果的键 -> 说明。说明可以只写一句中文，也可以写字典
    （``label`` / ``scope`` / ``unit``）。注册成功后这个键就能写进
    ``item_defs`` 的第 6 段（``/钓鱼`` 内容表 → 道具 → 效果），
    编辑器页面的效果清单也会自动出现它 —— 不用改插件本体。

``HANDLERS``
    效果键 -> 处理函数。玩家用这件道具时被调用，签名固定为::

        def handler(*, plugin, player, item_id, key, value) -> str | None

    * ``plugin``  插件实例（想读配置就 ``plugin.cfg["..."]``）
    * ``player``  玩家数据字典，**直接改它**就行（插件会接着保存）
    * ``item_id`` 玩家用的那件道具
    * ``key`` / ``value``  效果键和数值
    * 返回值：给玩家看的一行文字（``None`` / ``""`` 表示不显示）

    不写 ``HANDLERS`` 的键也不会报错：数值会被记在
    ``player["ext_effects"][键]`` 里，扩展之后自己读取解释。

红线（插件会拦住，不是靠自觉）：
* 扩展**不能覆盖内置键**（``meat`` / ``decorate`` 这些），只能新增
* 处理函数抛异常只会让这一条效果失效并打日志，不会影响这次使用的其它效果
"""

from __future__ import annotations

from typing import Any

#: 手气上限（和插件内置的锦鲤玉佩用同一个上限，避免扩展把数值调爆）
LUCK_CAP = 2.0


def _on_lucky_token(
    *, plugin: Any, player: dict[str, Any], item_id: str, key: str, value: float
) -> str:
    """幸运符：复用插件已有的手气 buff 字段（luck_charges / buff_casts_left）。

    这两个字段就是锦鲤玉佩用的那两个（见 ``_commands._cmd_use_item``），
    所以扩展做出来的效果和内置道具**完全同一条链路**：抛竿时会被读到。
    """
    casts = int(plugin.cfg.get("buff_cast_count") or 10)
    current = float(player.get("luck_charges") or 0.0)
    player["luck_charges"] = round(min(LUCK_CAP, current + float(value)), 6)
    player["buff_casts_left"] = max(int(player.get("buff_casts_left") or 0), casts)
    return f"　幸运符生效：接下来 {casts} 竿手气更好"


#: 新效果键：幸运符（真的有效果，见上面的处理函数）
#: 香气：故意只声明不写处理函数，用来演示「数值先记下来，逻辑以后再说」
EFFECTS: dict[str, Any] = {
    "lucky_token": {
        "label": "幸运符：手气 +N（倍率），持续 buff_cast_count 竿",
        "scope": "cast",
        "unit": "倍率（0.3 = +30% 手气）",
    },
    "aroma": {
        "label": "香气：示例键，插件只把数值记进玩家的 ext_effects",
        "scope": "ext",
        "unit": "整数",
    },
}

HANDLERS: dict[str, Any] = {"lucky_token": _on_lucky_token}
