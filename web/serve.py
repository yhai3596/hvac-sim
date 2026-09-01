#!/usr/bin/env python3
"""本地运行 / 部署打包：把 web/index.html 包装成可独立打开的 HTML 并起一个本地服务。

为什么需要这一步：web/index.html 是按 Claude Artifact 的约定写的正文片段——发布时平台
会自动套上 <!doctype html> 与 <head>（含 meta charset）。直接双击这个文件虽然多数情况下
能跑，但浏览器会进入 quirks 模式（盒模型/表格渲染与设计时不一致），且页面编码要靠浏览器
猜（中文 Windows 上 Chrome 常猜成 GBK → 整页乱码）。本脚本补上这两层。

用法：
    python3 web/serve.py                # 构建 dist/index.html 并在 http://127.0.0.1:8000 打开
    python3 web/serve.py --port 9000    # 换端口
    python3 web/serve.py --build        # 只构建，不起服务（把 dist/ 拷到任意静态服务器即可）
    python3 web/serve.py --host 0.0.0.0 # 允许局域网同事访问
    python3 web/serve.py --proxy        # 额外开启大模型 API 反向代理（Key 只留在服务端）

反向代理：用环境变量声明后端，页面里把 Base URL 填成 /api/<名字> 即可（同源，无 CORS 问题，
浏览器拿不到 Key；页面的 Key 栏必须非空，随便填 proxy 之类的占位符）：

    export AC_API_DEEPSEEK_URL=https://api.deepseek.com
    export AC_API_DEEPSEEK_KEY=sk-xxx
    export AC_API_CLAUDE_URL=https://api.anthropic.com
    export AC_API_CLAUDE_KEY=sk-ant-xxx
    export AC_API_CLAUDE_PROTOCOL=anthropic      # 不填默认按 OpenAI 协议注入 Bearer
    python3 web/serve.py --proxy

只用标准库，无第三方依赖。
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "index.html"
DIST = ROOT / "dist"

# 与 Artifact 发布环境保持一致的最小骨架：doctype（标准模式）+ UTF-8 + 视口 + 极简 reset。
# 页面自身的 <title>/<style> 在正文里，浏览器会把它们提到 head，不影响渲染。
HEAD = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ctext y='26' font-size='26'%3E%E2%9D%84%EF%B8%8F%3C/text%3E%3C/svg%3E">
<style>
:root{color-scheme:light}
body{margin:0;font:14px system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
img{max-width:100%}
[hidden]{display:none!important}
</style>
</head>
<body>
"""
FOOT = "\n</body>\n</html>\n"


def build() -> Path:
    if not SRC.exists():
        raise SystemExit(f"找不到源文件：{SRC}")
    body = SRC.read_text(encoding="utf-8")
    if "<!doctype" in body[:200].lower():
        raise SystemExit("web/index.html 已自带 doctype，无需本脚本包装（请检查是否被改动）")
    DIST.mkdir(exist_ok=True)
    out = DIST / "index.html"
    out.write_text(HEAD + body + FOOT, encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"已构建 {out}（{kb:.0f} KB，单文件、无外部依赖）")
    build_site_config()
    return out


def build_site_config():
    """按 AC_API_* 生成 dist/config.json：站点级 API 配置，任何浏览器打开都自动带上。

    只写入 /api/<名字> 这样的同源路径与占位 Key——**真实 Key 永远不进这个文件**，
    它留在服务端反代里。没有声明后端时删除旧文件，避免下发过期配置。
    """
    out = DIST / "config.json"
    backends = load_backends()
    if not backends:
        if out.exists():
            out.unlink()
        return None
    apis = []
    for name in sorted(backends):
        up = name.upper()
        apis.append({
            "name": os.environ.get(f"AC_API_{up}_LABEL", name),
            "protocol": backends[name]["protocol"],
            "base": f"/api/{name}",
            "model": os.environ.get(f"AC_API_{up}_MODEL", ""),
            "key": "proxy",          # 占位符：页面要求非空，实际由反代注入真实 Key
        })
    out.write_text(json.dumps({"apis": apis}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {out}（{len(apis)} 个站点级 API 配置，不含任何真实 Key）")
    return out


def load_backends() -> dict:
    """从环境变量 AC_API_<名字>_URL / _KEY / _PROTOCOL 读取反代后端。"""
    out = {}
    for k, v in os.environ.items():
        if not (k.startswith("AC_API_") and k.endswith("_URL")):
            continue
        name = k[len("AC_API_"):-len("_URL")].lower()
        out[name] = {
            "url": v.rstrip("/"),
            "key": os.environ.get(f"AC_API_{name.upper()}_KEY", ""),
            "protocol": os.environ.get(f"AC_API_{name.upper()}_PROTOCOL", "openai").lower(),
        }
    return out


class Handler(http.server.SimpleHTTPRequestHandler):
    """静态资源强制 text/html; charset=utf-8 并禁用缓存；可选把 /api/<名字>/ 反代到大模型接口。"""

    backends: dict = {}
    # extensions_map 是文档化的类属性，优先级高于 mimetypes 猜测，跨 Python 版本稳定
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".html": "text/html; charset=utf-8",
                      ".htm": "text/html; charset=utf-8"}

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(DIST), **kw)

    def guess_type(self, path):
        t = super().guess_type(path)
        if t == "text/html":
            return "text/html; charset=utf-8"
        return t

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):        # 静默常规访问日志（也避免把 URL 里的东西记下来）
        pass

    # ---- 反向代理：/api/<名字>/<上游路径> ----
    def _match_backend(self):
        if not self.path.startswith("/api/"):
            return None, None
        rest = self.path[len("/api/"):]
        name, _, tail = rest.partition("/")
        be = self.backends.get(name.lower())
        if not be:
            return None, None
        return be, "/" + tail

    def do_POST(self):
        be, tail = self._match_backend()
        if not be:
            self.send_error(404, "Not Found")
            return
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else b""
        headers = {"content-type": self.headers.get("content-type", "application/json")}
        if be["protocol"] == "anthropic":
            headers["x-api-key"] = be["key"]
            headers["anthropic-version"] = self.headers.get("anthropic-version", "2023-06-01")
        else:
            headers["authorization"] = "Bearer " + be["key"]
        req = urllib.request.Request(be["url"] + tail, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data, status, ctype = r.read(), r.status, r.headers.get("content-type", "application/json")
        except urllib.error.HTTPError as e:          # 把上游错误原样透传，页面的报错提示才准确
            data, status, ctype = e.read(), e.code, e.headers.get("content-type", "application/json")
        except Exception as e:                        # 网络层失败
            data = f'{{"error":{{"message":"代理请求失败：{type(e).__name__}"}}}}'.encode()
            status, ctype = 502, "application/json"
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    ap = argparse.ArgumentParser(description="构建并本地运行空调控制仿真台")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="默认只监听本机；填 0.0.0.0 可让局域网同事访问")
    ap.add_argument("--build", action="store_true", help="只构建 dist/index.html，不起服务")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--proxy", action="store_true",
                    help="开启大模型 API 反向代理（后端由 AC_API_* 环境变量声明）")
    args = ap.parse_args()

    build()
    if args.build:
        print("把 dist/ 目录整体拷到任意静态服务器即可部署（详见 docs/deploy.md）")
        return

    if args.proxy:
        Handler.backends = load_backends()
        if Handler.backends:
            for n, b in Handler.backends.items():
                print(f"反代 /api/{n}/  →  {b['url']}  （{b['protocol']} 协议，"
                      f"Key {'已配置' if b['key'] else '缺失！'}）")
            print("页面「API 配置」里把 Base URL 填成 /api/<名字>，Key 栏随便填个占位符即可")
        else:
            print("⚠ 未发现 AC_API_*_URL 环境变量，反代未挂载任何后端")

    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.TCPServer((args.host, args.port), Handler)
    except OSError as e:
        raise SystemExit(
            f"无法在 {args.host}:{args.port} 启动（{e.strerror or e}）。\n"
            f"端口可能已被占用，换一个即可：python3 web/serve.py --port {args.port + 1}\n"
            f"查看占用者：lsof -i :{args.port}")
    with httpd:
        url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}/"
        print(f"仿真台已启动：{url}")
        if args.host == "0.0.0.0":
            print("（已监听 0.0.0.0，局域网同事可用你的 IP 访问）")
        print("按 Ctrl+C 停止")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
