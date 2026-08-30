# 本地使用与服务器部署

整个平台没有任何第三方依赖：前端是一个 136 KB 的单 HTML 文件（无外部脚本、无 CDN、无字体
外链），后端仿真核心是纯标准库 Python。因此"部署"本质上就是把一个静态文件放到能访问的地方。

---

## 一、本地使用

### 1.1 先决条件

只需要 **Python 3.8+**（`python3 -V` 能输出版本即可）。不需要 Node、npm、任何构建工具。
Windows 上如果 `python3` 不识别，把命令里的 `python3` 换成 `python`。

### 1.2 启动（推荐方式）

```bash
git clone https://github.com/yhai3596/-.git hvac-sim
cd hvac-sim
python3 web/serve.py
```

> **克隆时务必指定目录名。** 本仓库名是单个连字符 `-`，直接 `git clone …/-.git` 会得到一个
> 名为 `-` 的目录，而 `cd -` 在 bash/zsh 里是「回到上一个目录」的内置语义、**不会**进入这个
> 目录（`cd -- -` 同样无效，bash 对 `-` 的特判在 `--` 之后）。若已经克隆成了 `-`，用
> `cd ./-` 或 `cd "$PWD/-"` 进入，或者不进目录直接 `python3 ./-/web/serve.py`——
> `serve.py` 的路径由自身位置推出，在哪个工作目录运行都可以。

脚本会构建 `dist/index.html` 并在 <http://127.0.0.1:8000> 启动服务、自动打开浏览器。
常用参数：

| 参数 | 作用 |
| --- | --- |
| `--port 9000` | 换端口 |
| `--host 0.0.0.0` | 允许局域网同事用你的 IP 访问 |
| `--build` | 只构建 `dist/index.html`，不起服务 |
| `--no-browser` | 不自动打开浏览器 |
| `--proxy` | 额外开启大模型 API 反向代理（见 §2.3） |

### 1.3 为什么不直接双击 `web/index.html`

`web/index.html` 是按 Claude Artifact 的约定写的**正文片段**——发布时平台会自动套上
`<!doctype html>` 和 `<head>`（含 `meta charset`）。直接双击这个文件，实测会有两个问题：

- **浏览器进入 quirks 模式**（`document.compatMode === "BackCompat"`），盒模型与表格渲染
  和设计时不一致；
- **页面编码全靠浏览器猜**。本项目在 Linux + Chromium 上猜对了 UTF-8，但中文 Windows 的
  Chrome 常把无 charset 声明的本地 HTML 当成 GBK，整页中文变乱码。

`web/serve.py` 补的就是这一层。构建产物 `dist/index.html` 是**自带 doctype 与 UTF-8 声明的
完整单文件**，实测双击打开即为标准模式、中文正常、localStorage 可用——要发给同事，直接发
这个文件就行，不必发整个仓库。

### 1.4 Python 仿真核心

网页端和 Python 端是同一物理模型的双实现，已做数值交叉验证。批量扫参、写脚本、进 CI 用
Python 端更方便：

```bash
python3 -m sim.test_sim          # 17 项单元测试
python3 -m sim.run_validation    # V0~V10 验证场景，重新生成 docs/validation-tables.md
```

```python
from sim.scenarios import run_scenario

r = run_scenario("C", "miami", scenario="summer_design", days=3.0,
                 tons=3.0, satisfaction=1.0, fan_ctrl="auto", prelearn_q=9000)
print(r.summary())
```

### 1.5 数据存在哪

方案对比记录、API 配置、实测步率都存在**浏览器的 localStorage** 里，按"浏览器 + 站点地址"
隔离。三个后果需要知道：

- 换浏览器、换机器、清缓存 → 记录会丢。要留存请用方案对比区的「复制 CSV」导出。
- 用 `file://` 打开和用 `http://localhost` 打开是**两个不同的存储空间**，记录不互通。
- 部署到服务器后，每个人看到的是自己浏览器里的记录，互不干扰（不是共享数据库）。

---

## 二、部署到服务器

### 2.1 纯静态托管（绝大多数情况选这个）

```bash
python3 web/serve.py --build     # 产出 dist/index.html
```

把 `dist/` 整个目录拷到任意静态服务器即可：nginx、Apache、IIS、GitHub Pages、对象存储
（S3 / OSS / COS）+ CDN、Vercel、Netlify 都行。没有后端进程，没有数据库，不用装运行时。

nginx 最小配置（**`charset utf-8;` 这行不能省**，否则中文可能乱码）：

```nginx
server {
    listen 80;
    server_name sim.example.com;
    root /var/www/ac-sim;          # 里面放 dist/index.html
    index index.html;
    charset utf-8;

    gzip on;
    gzip_types text/html;
    gzip_min_length 1024;          # 136 KB 的页面可压到约 30 KB
}
```

Apache 用 `AddDefaultCharset UTF-8`；IIS 在 MIME 设置里给 `.html` 指定 UTF-8。

### 2.2 大模型 API：两种模式，先决定用哪种

页面的「LLM 方案分析报告」和智能助手的自然语言解析要调用大模型。调用是**浏览器直接发给
厂商接口**的，因此有两件事要考虑：CORS 和 Key 的存放。

**模式 A —— 每人填自己的 Key（零后端，适合小范围自用）**

每个使用者在「API 配置」里填自己的 Key，Key 只存在他自己浏览器的 localStorage，不上传到
你的服务器。缺点是受厂商 CORS 策略限制：接口必须返回允许浏览器跨域的响应头，否则调用会被
浏览器拦掉（页面会提示"网络请求被阻止"）。

- OpenAI 与 Anthropic 支持浏览器直连（Anthropic 需要 `anthropic-dangerous-direct-browser-access`
  头，页面已经带上了）。
- DeepSeek / 智谱 GLM / Kimi / MiniMax 的 CORS 策略**请以你实测为准**——本项目的开发环境
  没有外网出口，无法替你验证。判断方法：配好一条，点「测试」，若报"网络请求被阻止"就是被
  CORS 拦了，改用模式 B。

**模式 B —— 服务器反向代理（推荐给团队共用）**

由服务器代为转发，Key 只留在服务端，浏览器永远拿不到；同时因为变成了同源请求，**CORS 问题
彻底消失**，不受厂商策略影响。

### 2.3 用内置反代（零依赖，已实测）

`web/serve.py --proxy` 自带一个标准库实现的反向代理。后端用环境变量声明：

```bash
export AC_API_DEEPSEEK_URL=https://api.deepseek.com
export AC_API_DEEPSEEK_KEY=sk-你的key
export AC_API_CLAUDE_URL=https://api.anthropic.com
export AC_API_CLAUDE_KEY=sk-ant-你的key
export AC_API_CLAUDE_PROTOCOL=anthropic     # 不填默认按 OpenAI 协议注入 Bearer
python3 web/serve.py --host 0.0.0.0 --port 8000 --proxy
```

然后在页面「API 配置」里：

| 字段 | 填什么 |
| --- | --- |
| 协议 | 与后端一致（DeepSeek 选 OpenAI 协议，Claude 选 Anthropic 协议） |
| Base URL | `/api/deepseek`、`/api/claude`（就是 `AC_API_<名字>_URL` 里的那个名字，小写） |
| 模型 | 照填厂商的模型名 |
| API Key | 随便填个占位符如 `proxy`（页面要求非空，实际不会被使用） |

实测结论：OpenAI 协议与 Anthropic 协议两条路径都通，智能助手"解析需求 → 跑实验 → 生成报告"
整条链路经反代可用，浏览器 localStorage 里存的确实只有占位符。

安全提醒：反代持有真实 Key，**不要用 `--host 0.0.0.0` 直接暴露到公网**。要么只在内网监听，
要么前面套一层带鉴权的 nginx（见下）。Key 用环境变量传入，不要写进代码或提交进仓库。

### 2.4 用 nginx 反代（如果你已经在用 nginx）

下面这段是按 nginx 的 `proxy_pass` 语义写的模板——本项目开发环境没有 nginx，**未经实测**，
上线前请用 `curl -i https://sim.example.com/api/deepseek/chat/completions` 确认转发路径正确：

```nginx
server {
    listen 443 ssl;
    server_name sim.example.com;
    charset utf-8;

    root /var/www/ac-sim;
    index index.html;

    # OpenAI 兼容协议：页面会请求 /api/deepseek/chat/completions
    location /api/deepseek/ {
        proxy_pass https://api.deepseek.com/;
        proxy_set_header Host api.deepseek.com;
        proxy_set_header Authorization "Bearer sk-你的key";
        proxy_ssl_server_name on;
        proxy_read_timeout 300s;      # 生成报告可能要一两分钟，默认 60s 会超时
    }

    # Anthropic 协议：页面会请求 /api/claude/v1/messages
    location /api/claude/ {
        proxy_pass https://api.anthropic.com/;
        proxy_set_header Host api.anthropic.com;
        proxy_set_header x-api-key "sk-ant-你的key";
        proxy_set_header anthropic-version "2023-06-01";
        proxy_ssl_server_name on;
        proxy_read_timeout 300s;
    }
}
```

路径拼接规则（用于自查）：页面对 OpenAI 协议会把 Base 后面接 `/chat/completions`，对
Anthropic 协议接 `/v1/messages`；nginx 的 `location /api/x/` + `proxy_pass https://host/`
会把 `/api/x/` 前缀替换掉，于是正好还原成厂商的原始路径。

### 2.5 HTTPS

页面本身是纯静态，HTTP 也能跑；但厂商接口都是 HTTPS，若页面用 HTTPS 提供而 API 走 HTTP 会
触发混合内容拦截（本项目不会出现，因为所有接口都是 HTTPS）。要证书最省事的是 Caddy：

```
sim.example.com {
    root * /var/www/ac-sim
    file_server
}
```

Caddy 会自动申请并续期 Let's Encrypt 证书。用 nginx 则配 certbot。

### 2.6 更新流程

```bash
git pull
python3 web/serve.py --build
rsync -a dist/ user@server:/var/www/ac-sim/     # 或你惯用的发布方式
```

`dist/` 是构建产物，已加入 `.gitignore`，不进版本库。

---

## 三、常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 中文乱码 | 用了 `web/index.html` 而不是 `dist/index.html`，或服务器没设 `charset utf-8` |
| 点「测试」提示"网络请求被阻止" | 厂商 CORS 拦截，或在 Artifact 预览环境里（该环境禁止外部请求）。改用 §2.3 的反代，或用「复制分析提示词」把数据贴到任意大模型对话里 |
| Artifact 链接里助手不能生成报告 | 同上：Artifact 预览环境的 CSP 拦截所有外部请求。内置规则解析、批量仿真、对比矩阵在 Artifact 里都能正常用；要用 LLM 就本地跑或自行部署 |
| 批量实验时页面像卡住 | 正常：跑实验期间主循环会暂停，进度条与剩余时间在助手卡片里；可点「停止实验」 |
| 换了台机器，方案记录没了 | localStorage 按浏览器+站点隔离，不会跟着账号走。用「复制 CSV」导出保存 |
| 生成报告超时 | 反代默认 300s；若用 nginx 记得把 `proxy_read_timeout` 调大，默认 60s 不够 |
| 除湿量看起来偏高 | 检查计划里的预热时长。室内湿平衡时间常数约 25 h，预热不足 24 h 会显著高估除湿量（详见 `validation-report.md` 3b 节），助手默认预热 24 h 并对不足的情况告警 |
