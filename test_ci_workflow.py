# -*- coding: utf-8 -*-
"""校验 GitHub Actions workflow 的 YAML 结构与关键字段。"""

import sys
from pathlib import Path

import yaml

WF = Path(__file__).parent / ".github" / "workflows" / "docker-image.yml"
FAILS: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("  ✅ " if ok else "  ❌ ") + label)
    if not ok:
        FAILS.append(label)


raw = WF.read_text(encoding="utf-8")
doc = yaml.safe_load(raw)
# PyYAML 按 YAML 1.1 解析，`on:` 会变成布尔键 True —— GitHub 自己按 1.2 解析，这是已知差异
on_block = doc.get("on", doc.get(True))
check(isinstance(doc, dict), "workflow 是合法 YAML")
check(
    isinstance(on_block, dict) and set(on_block) >= {"push", "pull_request", "workflow_dispatch"},
    f"触发器完整：{sorted(on_block) if isinstance(on_block, dict) else on_block}",
)

jobs = doc.get("jobs") or {}
job = jobs.get("build") or {}
check(bool(job), f"存在 build job：{list(jobs)}")
check(
    job.get("permissions", {}).get("packages") == "write",
    f"job 有 packages: write 权限（推 GHCR 必需）：{job.get('permissions')}",
)

steps = job.get("steps") or []
uses = [s.get("uses", "") for s in steps]
check(any("actions/checkout@" in u for u in uses), "使用 actions/checkout")
check(any("docker/setup-buildx-action@" in u for u in uses), "使用 setup-buildx")
check(any("docker/login-action@" in u for u in uses), "使用 docker/login-action")
check(sum(1 for u in uses if "docker/build-push-action@" in u) == 2, "构建与推送分成两步")
check(any("docker/metadata-action@" in u for u in uses), "使用 metadata-action 生成标签")

login = next(s for s in steps if "login-action" in s.get("uses", ""))
check(
    login.get("if") == "github.event_name != 'pull_request'",
    f"PR 时不登录（fork 无写权限）：if={login.get('if')}",
)
check("secrets.GITHUB_TOKEN" in str(login.get("with")), "用 GITHUB_TOKEN 登录 GHCR")

build = next(s for s in steps if "build-push-action" in s.get("uses", "") and s.get("with", {}).get("load"))
push = next(s for s in steps if "build-push-action" in s.get("uses", "") and s.get("with", {}).get("push"))
check(build["with"].get("load") is True, "第一步构建 load 到本地（供冒烟测试）")
check(push["with"].get("push") is True, "第二步推送到 GHCR")
check(
    push.get("if") == "github.event_name != 'pull_request'",
    "PR 时不推送镜像",
)
check(
    "cache-from" in build["with"] and "cache-to" in build["with"],
    "启用 GHA 构建缓存",
)

smoke = next((s for s in steps if "冒烟测试" in str(s.get("name", ""))), None)
check(smoke is not None, "存在冒烟测试步骤")
body = str(smoke.get("run", "")) if smoke else ""
for needle, label in (
    ("/opt/fishing-plugin/metadata.yaml", "冒烟：检查预置插件文件"),
    ("py_compile", "冒烟：容器内做语法校验"),
    ("data/plugins/astrbot_plugin_qq_fishing", "冒烟：验证入口脚本同步插件"),
    ("test ! -e \"$DST/docker\"", "冒烟：确认开发文件没进运行目录"),
):
    check(needle in body, label)

summary = next((s for s in steps if "GITHUB_STEP_SUMMARY" in str(s.get("run", ""))), None)
check(summary is not None, "把 pull 命令写进 Job Summary")
check("ghcr.io/${{ github.repository }}" in raw, "镜像名用 ghcr.io/<owner>/<repo>")

# .dockerignore 必须把 CI 文件排除在构建上下文外，避免无谓的缓存失效
dockerignore = (Path(__file__).parent / ".dockerignore").read_text(encoding="utf-8")
check(".github/" in dockerignore, ".dockerignore 排除了 .github/（不进镜像）")
gitignore = (Path(__file__).parent / ".gitignore").read_text(encoding="utf-8")
check(".github/" not in gitignore, ".gitignore 不排除 .github/（workflow 要提交）")

print("\n" + "=" * 60)
if FAILS:
    print(f"❌ {len(FAILS)} 项未通过：")
    for item in FAILS:
        print(" -", item)
    sys.exit(1)
print("🎉 workflow 校验通过")
