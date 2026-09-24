# -*- coding: utf-8 -*-
"""临时工具：把由代码生成的那些内容表默认值同步回 _conf_schema.json（同步完就删）。

为什么需要它：``_game_data.py`` 里的 ``*_DEFS_DEFAULT`` 是「官方内容」的唯一出处，
``DEFAULTS`` 运行时从那里取值；而 ``_conf_schema.json`` 的 default 是**手抄副本**，
两边必须逐字节一致（``test_local.py`` 有断言卡这件事）。
本脚本只同步那几张「由代码生成」的表，其它键一个字都不动。
"""
import io
import json
import sys

sys.path.insert(0, ".")
import main as m  # noqa: E402

SYNC_KEYS = ("button_defs", "collectible_defs", "variant_defs", "weather_defs",
             "easter_egg_defs", "fish_defs",
             # 别名表的默认值是**由子命令总表现算**的（加了子命令就会变），
             # 所以也属于「代码生成」，必须跟着同步
             "command_aliases")

path = "_conf_schema.json"
schema = json.loads(io.open(path, encoding="utf-8-sig").read())
changed = []
for key in SYNC_KEYS:
    if key not in schema or key not in m.DEFAULTS:
        continue
    if schema[key].get("default") != m.DEFAULTS[key]:
        schema[key]["default"] = m.DEFAULTS[key]
        changed.append(key)

with io.open(path, "w", encoding="utf-8") as fh:
    json.dump(schema, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
print("已同步：", changed or "（无需改动）")
