# -*- coding: utf-8 -*-
"""验证 docker/ 下的部署脚本：.env 注入逻辑 + compose yaml 语法。

用假凭据在工作区临时目录里跑，不碰真实 AstrBot 配置。
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
INJECTOR = HERE / "docker" / "inject_cmd_config.py"
SANDBOX_TMP = HERE / ".tmp_docker_verify"   # 本机 Temp 目录在沙箱里不可写
FAILS: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("  ✅ " if ok else "  ❌ ") + label)
    if not ok:
        FAILS.append(label)


def run_injector(cfg_path: Path, **envs: str) -> str:
    """在干净环境变量下跑一次注入脚本，返回它打印的日志。"""
    old = dict(os.environ)
    try:
        for key in list(os.environ):
            if key.startswith(("QQ_", "ONEBOT_", "FISHING_")):
                os.environ.pop(key, None)
        os.environ["ASTRBOT_CONFIG_FILE"] = str(cfg_path)
        os.environ.update({k: str(v) for k, v in envs.items()})
        spec = importlib.util.spec_from_file_location("inject_probe", INJECTOR)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main()
        return buf.getvalue()
    finally:
        os.environ.clear()
        os.environ.update(old)


def write_cfg(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_cfg(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


shutil.rmtree(SANDBOX_TMP, ignore_errors=True)
try:
    print("[1] compose / Dockerfile 基本检查")
    try:
        import yaml

        compose = yaml.safe_load((HERE / "docker-compose.yml").read_text(encoding="utf-8"))
        svc = compose["services"]["astrbot"]
        check("build" in svc and "image" in svc, f"compose 服务可用：{list(compose['services'])}")
        check(
            any("/AstrBot/data" in v for v in svc["volumes"]),
            f"数据卷挂载到 /AstrBot/data：{svc['volumes']}",
        )
        check(
            any(str(p).endswith(":6185") for p in svc["ports"]),
            f"管理面板端口映射：{svc['ports']}",
        )
        check(svc.get("env_file") == [".env"], f"env_file 指向 .env：{svc.get('env_file')}")
    except ImportError:  # pragma: no cover
        print("  ⚠ 本机没有 pyyaml，跳过 compose 语法检查")

    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    check("FROM soulter/astrbot:latest" in dockerfile, "Dockerfile 基于官方镜像")
    check(
        "entrypoint.sh" in dockerfile and "FISHING_PLUGIN_NAME=astrbot_plugin_fishing_mini" in dockerfile,
        "Dockerfile 挂好 entrypoint 与插件目录名",
    )

    print("\n[1b] entrypoint.sh 的清理列表：运行时功能目录必须留下")
    entrypoint = (HERE / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    # 只从 `rm -rf "${DST_DIR}/..." \` 那条命令里抠出被删的路径（别把别的 rm 也算进来）
    cleanup_paths: list[str] = []
    collecting = False
    for line in entrypoint.splitlines():
        stripped = line.strip()
        if not collecting:
            if stripped.startswith("rm -rf") and "DST_DIR" in stripped:
                collecting = True
            else:
                continue
        for token in stripped.replace("\\", " ").split():
            token = token.strip().strip('"').strip("'")
            if token.startswith("${DST_DIR}/"):
                cleanup_paths.append(token[len("${DST_DIR}/") :])
        if not stripped.endswith("\\"):
            break
    check(bool(cleanup_paths), f"解析出清理列表：{cleanup_paths}")
    for keep, why in (
        ("pages", "插件页面（数据编辑器）就在 pages/ 里，删了页面直接 404"),
        (".astrbot-plugin", "页面 i18n（中文标题）在 .astrbot-plugin/i18n/ 里"),
        ("main.py", "插件本体"),
        ("_editor_bridge.py", "编辑器通道模块"),
    ):
        hit = [p for p in cleanup_paths if p.rstrip("/").endswith(keep)]
        check(not hit, f"清理列表没有误删 {keep}（{why}）")
    # 反面用例：开发文件确实应该被删掉（否则「没误删」这句毫无意义）
    for gone in ("docker", "Dockerfile", ".git"):
        hit = [p for p in cleanup_paths if p.rstrip("/").endswith(gone)]
        check(bool(hit), f"清理列表确实删掉了开发文件 {gone}")

    print("\n[1c] Dockerfile / .dockerignore 不会把页面排除在镜像外")
    dockerignore_paths = [
        line.strip()
        for line in (HERE / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    check("COPY . /opt/fishing-plugin/" in dockerfile, "Dockerfile 整仓 COPY（pages/ 一并进镜像）")
    for blocked in ("pages", "pages/", ".astrbot-plugin", "i18n"):
        check(
            blocked not in dockerignore_paths,
            f".dockerignore 没有排除 {blocked}（否则镜像里没有页面）",
        )

    print("\n[2] 注入脚本：填空 + 不覆盖已有值")
    tmp = SANDBOX_TMP / "case2"
    cfg = tmp / "cmd_config.json"
    write_cfg(
        cfg,
        {
            "config_version": 1,
            "platform": [
                {
                    "id": "x1",
                    "type": "qq_official",
                    "enable": False,
                    "appid": "",
                    "secret": "",
                    "enable_group_c2c": False,
                    "enable_guild_direct_message": False,
                    "use_markdown": False,
                },
                {
                    "id": "x2",
                    "type": "aiocqhttp",
                    "enable": True,
                    "ws_reverse_host": "0.0.0.0",
                    "ws_reverse_port": 6199,
                    "ws_reverse_token": "EXISTING_TOKEN",
                },
            ],
        },
    )
    log = run_injector(
        cfg,
        QQ_ENABLE="true",
        QQ_APPID="1234567890",
        QQ_SECRET="FAKE_SECRET_VALUE",
        QQ_ENABLE_GROUP_C2C="true",
        QQ_USE_MARKDOWN="true",
        ONEBOT_ENABLE="true",
        ONEBOT_WS_TOKEN="NEW_TOKEN_VALUE",
    )
    data = read_cfg(cfg)
    qq, ob = data["platform"][0], data["platform"][1]
    check(qq["appid"] == "1234567890", f"空 appid 被填入 -> {qq['appid']}")
    check(qq["secret"] == "FAKE_SECRET_VALUE", "空 secret 被填入")
    check(qq["enable"] is True, "平台被启用")
    check(qq["enable_group_c2c"] is True, "enable_group_c2c 按 .env 写入")
    check(qq["use_markdown"] is True, "use_markdown 按 .env 写入")
    check(ob["ws_reverse_token"] == "EXISTING_TOKEN", "已存在的 OneBot token 不被覆盖")
    check("FAKE_SECRET_VALUE" not in log, "日志里不打印 secret 明文")
    check(
        any(p.name.startswith("cmd_config.json.bak-") for p in tmp.iterdir()),
        "写之前先备份了原配置",
    )
    check(len(data["platform"]) == 2, f"平台条目数不变：{len(data['platform'])}")

    print("\n[2b] .env 没写的开关不动 AstrBot 里的现有配置")
    write_cfg(
        cfg,
        {
            "platform": [
                {
                    "id": "x1",
                    "type": "qq_official",
                    "enable": True,
                    "appid": "1234567890",
                    "secret": "FAKE_SECRET_VALUE",
                    "enable_group_c2c": False,
                    "use_markdown": False,
                }
            ]
        },
    )
    run_injector(cfg, QQ_ENABLE="true", QQ_APPID="1234567890", QQ_SECRET="FAKE_SECRET_VALUE")
    kept = read_cfg(cfg)["platform"][0]
    check(
        kept["enable_group_c2c"] is False and kept["use_markdown"] is False,
        "开关没写进 .env 时保持原样（不擅自打开）",
    )

    print("\n[3] 强制覆盖开关")
    run_injector(
        cfg,
        QQ_ENABLE="true",
        QQ_APPID="1234567890",
        QQ_SECRET="FAKE_SECRET_VALUE",
        ONEBOT_ENABLE="true",
        ONEBOT_WS_TOKEN="NEW_TOKEN_VALUE",
        FISHING_CONFIG_OVERWRITE="1",
    )
    check(
        read_cfg(cfg)["platform"][1]["ws_reverse_token"] == "NEW_TOKEN_VALUE",
        "FISHING_CONFIG_OVERWRITE=1 时覆盖已有值",
    )

    print("\n[4] 平台不存在时新建；配置未生成 / 什么都没填的情况")
    tmp4 = SANDBOX_TMP / "case4"
    cfg4 = tmp4 / "cmd_config.json"
    write_cfg(cfg4, {"config_version": 1, "platform": []})
    run_injector(cfg4, QQ_ENABLE="true", QQ_APPID="aaa", QQ_SECRET="bbb")
    item = read_cfg(cfg4)["platform"][0]
    check(
        item["type"] == "qq_official" and item["appid"] == "aaa" and bool(item["id"]),
        f"自动新建平台条目（type/id 齐全）：{ {k: item[k] for k in ('type', 'id')} }",
    )

    missing = tmp4 / "nope" / "cmd_config.json"
    log = run_injector(missing, QQ_ENABLE="true", QQ_APPID="aaa", QQ_SECRET="bbb")
    check("没找到" in log and not missing.exists(), "配置还没生成时不报错、不瞎造文件")

    log = run_injector(cfg4)
    check("没有需要注入" in log, "什么都不填时明确说明跳过")

    print("\n[5] .env 相关忽略规则")
    gitignore = (HERE / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (HERE / ".dockerignore").read_text(encoding="utf-8")
    check(
        "\n.env\n" in gitignore and "!.env.example" in gitignore,
        ".gitignore 排除 .env、保留 .env.example",
    )
    check("\n.env\n" in dockerignore and "!.env.example" in dockerignore, ".dockerignore 排除 .env")
    check("backups/" in gitignore, ".gitignore 排除 backups/（真实玩家存档）")
finally:
    shutil.rmtree(SANDBOX_TMP, ignore_errors=True)

print("\n" + "=" * 60)
if FAILS:
    print(f"❌ {len(FAILS)} 项未通过：")
    for item in FAILS:
        print(" -", item)
    sys.exit(1)
print("🎉 Docker 部署脚本验证通过")
