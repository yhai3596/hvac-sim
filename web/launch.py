#!/usr/bin/env python3
"""双击启动入口：更新代码 → 构建 dist/index.html → 起本地服务并打开浏览器。

两个启动器（macOS 的 `启动仿真台.command`、Windows 的 `启动仿真台.bat`）都只做一件事：
找到一个 3.8+ 的 Python，然后把控制权交给本文件。真正的逻辑写在这里，好处有两个：

- Windows 批处理里不必出现中文（cmd.exe 的代码页问题），而 Python 3.6+ 在 Windows 控制台
  是用宽字符 API 输出的，中文不会乱码；
- 更新逻辑只有一份，两个平台行为一致。

用法：
    python3 web/launch.py                # 更新 + 构建 + 起服务（默认 http://127.0.0.1:8000）
    python3 web/launch.py --no-update    # 跳过 git pull，直接起服务
    python3 web/launch.py --port 9000    # 其余参数原样转交给 web/serve.py

只用标准库，无第三方依赖。
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(args, timeout=60):
    """在仓库根目录跑一条 git 命令。禁掉交互式凭证提示，避免双击后卡在看不见的输入等待上。"""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    # 显式按 UTF-8 解码：git 的提交信息是 UTF-8，而中文 Windows 上 Python 默认会拿
    # 系统代码页（cp936）去解，日志里的中文会变成乱码；errors=replace 保证绝不因此抛异常。
    return subprocess.run(["git"] + list(args), cwd=str(ROOT), env=env, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, encoding="utf-8", errors="replace")


def update():
    """尽力而为地拉取最新代码：任何一步不成立都只提示，绝不阻断启动。"""
    if not (ROOT / ".git").exists():
        print("· 不是 git 仓库（可能是直接下载的 zip），跳过更新检查")
        return
    try:
        head0 = git(["rev-parse", "HEAD"])
    except FileNotFoundError:
        print("· 没找到 git 命令，跳过更新检查")
        return
    except subprocess.TimeoutExpired:
        print("· git 无响应，跳过更新检查")
        return
    if head0.returncode != 0:
        print("· 读不到当前版本，跳过更新检查")
        return
    old = head0.stdout.strip()

    up = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if up.returncode != 0:
        print("· 当前分支没有对应的远端分支，跳过更新检查")
        return
    print("· 检查更新（%s）…" % up.stdout.strip())

    try:
        r = git(["pull", "--ff-only"], timeout=120)
    except subprocess.TimeoutExpired:
        print("· 更新超时（120s），用本地现有版本启动")
        return
    if r.returncode != 0:
        tail = [ln for ln in (r.stderr or r.stdout).strip().splitlines() if ln.strip()]
        print("· 更新失败，用本地现有版本启动。git 的原话：")
        for ln in tail[:6]:
            print("    " + ln)
        print("  常见原因：没联网 / 本地改过文件（先 git stash）/ 本地有分叉提交（git log 看看）")
        return

    new = git(["rev-parse", "HEAD"]).stdout.strip()
    if new == old:
        print("· 已是最新版本")
        return
    print("· 已更新到最新版本，本次带来：")
    log = git(["log", "--oneline", "-10", "%s..HEAD" % old])
    for line in log.stdout.strip().splitlines():
        print("    " + line)


def main():
    if sys.version_info < (3, 8):
        raise SystemExit("需要 Python 3.8 或更高版本，当前是 %s" % sys.version.split()[0])

    argv = list(sys.argv[1:])
    if "--no-update" in argv:
        argv = [a for a in argv if a != "--no-update"]
    else:
        update()
    print()

    serve = ROOT / "web" / "serve.py"
    if not serve.exists():
        raise SystemExit("找不到 %s（仓库是不是没克隆完整？）" % serve)
    # 在更新之后才加载 serve.py，这样 git pull 拉下来的新版本本次就能生效
    spec = importlib.util.spec_from_file_location("ac_serve", str(serve))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.argv = [str(serve)] + argv
    mod.main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止")
