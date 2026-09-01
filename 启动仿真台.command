#!/bin/bash
# 双击即用（macOS / Linux）：进入本文件所在目录 → 找一个 3.8+ 的 Python → 交给 web/launch.py。
# launch.py 会先 git pull 拉最新代码（失败只提示、不阻断），再构建 dist/index.html 并起本地服务。
# 想跳过更新：在终端里执行  ./启动仿真台.command --no-update

# 目录名以 - 开头时 dirname 会给出以 - 开头的相对路径，而 cd - 是「回到上一个目录」的
# 内置语义、不会进入该目录——加上 ./ 前缀避开这个特判。
dir=$(dirname "$0")
case "$dir" in -*) dir="./$dir" ;; esac
cd "$dir" || exit 1

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "没找到 Python 3.8+。"
  echo "  macOS：终端里执行  xcode-select --install  安装命令行工具，"
  echo "         或到 https://www.python.org/downloads/ 下载安装包。"
  echo
  read -r -p "按回车键关闭…" _
  exit 1
fi

"$PY" web/launch.py "$@"
code=$?
if [ $code -ne 0 ]; then
  echo
  read -r -p "启动失败（退出码 $code），按回车键关闭…" _
fi
exit $code
