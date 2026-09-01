# 本地使用与服务器部署

整个平台没有任何第三方依赖：前端是一个 160 KB 的单 HTML 文件（无外部脚本、无 CDN、无字体
外链），后端仿真核心是纯标准库 Python。因此"部署"本质上就是把一个静态文件放到能访问的地方。

---

## 一、本地使用

### 1.1 先决条件

只需要 **Python 3.8+**（`python3 -V` 能输出版本即可）。不需要 Node、npm、任何构建工具。
Windows 上如果 `python3` 不识别，把命令里的 `python3` 换成 `python`。

### 1.2 启动

第一次要先把仓库拿下来（只需一次）：

```bash
git clone https://github.com/yhai3596/-.git hvac-sim
cd hvac-sim
```

> **克隆时务必指定目录名。** 本仓库名是单个连字符 `-`，直接 `git clone …/-.git` 会得到一个
> 名为 `-` 的目录，而 `cd -` 在 bash/zsh 里是「回到上一个目录」的内置语义、**不会**进入这个
> 目录（`cd -- -` 同样无效，bash 对 `-` 的特判在 `--` 之后）。若已经克隆成了 `-`，用
> `cd ./-` 或 `cd "$PWD/-"` 进入，或者不进目录直接 `python3 ./-/web/serve.py`——
> `serve.py` 的路径由自身位置推出，在哪个工作目录运行都可以。

之后每次使用，两种方式二选一。

**方式一：双击启动器（推荐，不用记命令）**

| 系统 | 双击这个文件 |
| --- | --- |
| macOS / Linux | `启动仿真台.command` |
| Windows | `启动仿真台.bat` |

启动器做三件事：`git pull` 拉最新代码 → 构建 `dist/index.html` → 起本地服务并打开浏览器。
更新失败（没联网、本地改过文件等）只会打印一行提示，**不会阻断启动**，照常用旧版本。
把它拖到 Dock / 发送到桌面快捷方式，就是一个"双击即用"的入口。启动器和 `serve.py` 一样
从自身位置推出仓库路径，所以哪怕上面那个坑没绕开、目录真的叫 `-`，双击照样能跑。

**方式二：命令行**

```bash
python3 web/launch.py     # 等价于双击启动器（更新 + 构建 + 起服务）
python3 web/serve.py      # 不检查更新，只构建 + 起服务
```

两者都会构建 `dist/index.html` 并在 <http://127.0.0.1:8000> 启动服务、自动打开浏览器。
`launch.py` 把自己不认识的参数原样转交给 `serve.py`，所以下面这些参数两边通用
（双击启动器时用不上参数，需要改端口就走命令行）：

| 参数 | 作用 |
| --- | --- |
| `--port 9000` | 换端口 |
| `--host 0.0.0.0` | 允许局域网同事用你的 IP 访问 |
| `--build` | 只构建 `dist/index.html`，不起服务 |
| `--no-browser` | 不自动打开浏览器 |
| `--proxy` | 额外开启大模型 API 反向代理（见 §2.3） |
| `--no-update` | 仅 `launch.py`：跳过 `git pull` |

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

### 1.5 导出分析报告（PDF / Markdown / HTML）

方案对比表里有记录后，「LLM 方案分析报告」卡片会出现三个导出按钮：

| 按钮 | 产出 | 说明 |
| --- | --- | --- |
| **导出 PDF** | 打开浏览器打印对话框 | 在「目标 / 打印机」里选 **另存为 PDF**（macOS 左下角 PDF ▾ → 存储为 PDF；Windows 选 Microsoft Print to PDF） |
| **下载 Markdown** | `.md` 文件 | 便于进 wiki / 邮件 / 版本库 |
| **下载 HTML** | 单文件 `.html` | 自带打印样式，双击打开再打印同样能出 PDF |

导出的不是网页截图，而是一份完整报告文档：标题与生成信息 → 实验设计（对比臂 × 条件维度、
共同设定、预热/测量时长、自动取值与校验警告）→ 方案对比数据（全部指标 + 相对基准的变化
百分比）→ 对比矩阵 → LLM 分析正文 → 附录指标定义 → 模型边界与结论适用范围。

**为什么用打印而不是一键生成 PDF**：浏览器里直接生成 PDF 的常见做法（jsPDF）内置字体不支持
中文，中文会变成方块；改用 html2canvas 截图则文字变成图片、不可选、体积大且模糊。两者还都要
引入外部库，破坏本项目"零外部依赖单文件"的属性。走打印管线由浏览器用系统字体排版，**中文
正常、文字可选、矢量清晰**——实测产出 5 页 A4、753 KB，PDF 内嵌 CJK 字体子集、无图片对象。

**保存文件的两条路**：Artifact 查看器不允许页面自己发起下载，页面改用平台的 `downloads`
能力（点下载会先弹出保存确认，查看者可以拒绝——拒绝时页面如实提示，不会谎称已下载）；
本地打开或自行部署时走标准的 blob 下载，无需任何权限。两条路都已实测。

> 「导出 PDF」走浏览器打印，在 Artifact 预览里可能被沙箱限制；本地打开一定可用。

### 1.6 数据存在哪

方案对比记录、API 配置、实测步率都存在**浏览器的 localStorage** 里，按"浏览器 + 站点地址"
隔离。三个后果需要知道：

- 换浏览器、换机器、清缓存 → 记录会丢。要留存请用方案对比区的「复制 CSV」导出。
- 用 `file://` 打开和用 `http://localhost` 打开是**两个不同的存储空间**，记录不互通。
- 部署到服务器后，每个人看到的是自己浏览器里的记录，互不干扰（不是共享数据库）。

---

### 1.7 代码更新：哪一步自动、哪一步要手动

| 环节 | 会不会自动 | 说明 |
| --- | --- | --- |
| GitHub 上的代码 | **自动** | 每轮迭代结束都会推到 `claude/ac-control-algorithm-simulation-zivkl0`，而它就是本仓库的默认分支——`git clone` 默认检出它、`git pull` 默认也跟它，所以远端总是最新的 |
| 本地克隆 | **不自动** | git 不会自己拉代码。你的本地副本停在上次 `git pull` 的那一刻，直到你（或启动器）去拉 |
| 浏览器里看到的页面 | 拉完即最新 | `serve.py` 对页面发 `Cache-Control: no-store`，刷新一下就是新版本，不用清缓存 |

所以"自动更新"这件事，落点在**启动的那一刻**：

```bash
# 三选一，效果相同
双击 启动仿真台.command / 启动仿真台.bat   # 每次启动自动 pull，最省事
python3 web/launch.py                      # 同上，命令行版
git pull && python3 web/serve.py           # 手动版
```

启动器会把这次拉到的提交打印出来，一眼看到更新了什么：

```
· 检查更新（origin/claude/ac-control-algorithm-simulation-zivkl0）…
· 已更新到最新版本，本次带来：
    247a417 报告下载接入 Artifact 的 downloads 能力
    7302d68 分析报告支持导出 PDF / Markdown / HTML
```

几点需要知道：

- **更新失败不阻断启动**。没联网、远端不可达、本地有冲突，都只打印 git 的原话然后照常用旧
  版本起服务——不会出现"因为拉不到代码所以用不了"的情况。
- **本地改过仓库里的文件会挡住更新**。启动器用的是 `git pull --ff-only`（只快进、不自动合并，
  免得在你机器上留下冲突残骸）。想保留改动就先 `git stash`，不要了就 `git checkout -- <文件>`。
  自己的实验脚本建议放到仓库目录**之外**，避免每次都被挡。
- **不想每次都联网检查**：`python3 web/launch.py --no-update`，或者直接 `python3 web/serve.py`。
- 想看完整历史：`git log --oneline -20`；想回到某个旧版本：`git checkout <提交号>`（再 `git switch -` 回来）。
- 若以后默认分支改成了 `main`，本地需要切一次：`git fetch origin && git checkout main`，之后
  `git pull` 照旧。

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
    gzip_min_length 1024;          # 160 KB 的页面可压到约 30 KB
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

### 2.7 一键部署脚本（Linux 服务器 + 域名 + HTTPS）

前面几节讲的是"怎么做"，`deploy/install.sh` 把它们串成了一条可重复执行的命令。适用于
Debian/Ubuntu 与 CentOS/RHEL/TencentOS（自动识别 apt / dnf / yum）。

**先决条件**

1. 域名的 A 记录已指向服务器公网 IP（`getent hosts 你的域名` 能返回该 IP）；
2. 云控制台安全组放行 **80 与 443**——脚本改不了安全组，这一步只能你在控制台点；
3. 有 root 或 sudo。

**执行**

```bash
curl -fsSL https://raw.githubusercontent.com/yhai3596/-/claude/ac-control-algorithm-simulation-zivkl0/deploy/install.sh -o install.sh
less install.sh                       # 建议先扫一眼再跑
sudo DOMAIN=你的域名 EMAIL=你的邮箱 bash install.sh
```

脚本按顺序做六件事，每一步都幂等（重复跑只做增量）：

| 步骤 | 内容 |
| --- | --- |
| 1 | 装 git / python3 / nginx / certbot |
| 2 | 克隆或更新代码到 `/opt/hvac-sim`（已存在则 `git reset --hard` 到远端，服务器按只读部署对待） |
| 3 | `python3 web/serve.py --build` 产出 `dist/index.html` |
| 4 | 写 nginx 站点（`charset utf-8`、gzip、`index.html` 不缓存、ACME 目录），`nginx -t` 通过才 reload |
| 5 | 若存在 `/etc/hvac-sim/api.env` 则装大模型 API 反代服务（只监听 127.0.0.1，公网只能经 nginx 的 `/api/` 走） |
| 6 | certbot 申请证书并开启 80→443 跳转；装每 30 分钟拉取更新的 systemd timer |

常用开关：`SKIP_TLS=1` 只配 HTTP（DNS 还没生效时先这样，解析好后重跑补证书）、
`AUTO_UPDATE=0` 不装自动更新、`APP_DIR=` 换目录、`BRANCH=` 换分支、
`SRC_DIR=` 用本地已有源码（离线部署）、`DRY_RUN=1` 只打印将执行的特权命令。

> **服务器连不上 GitHub 怎么办**（国内机器常见，表现为 `GnuTLS recv error (-110)`、
> `Connection reset by peer`）：脚本的取码步骤有三条路径，按顺序自动降级——
> ① `SRC_DIR` 指定的本地源码（完全不联网）→ ② `git clone`（走 HTTP/1.1、加大 postBuffer）
> → ③ `codeload.github.com` 的源码包。三条都不通时会打印出具体的替代做法再退出，
> 而不是留下半个装了一半的系统。走 ② 之外的路径时服务器没有 git 元数据，
> 自动更新 timer 会自动跳过（脚本会明确告诉你），后续更新用同样方式重新推一次代码即可。
>
> 最省事的离线部署：在能访问 GitHub 的机器上打包传过去。
>
> ```bash
> # 本地（能上 GitHub 的机器）
> git clone https://github.com/yhai3596/-.git hvac-sim
> tar czf hvac.tgz -C hvac-sim .
> scp hvac.tgz 用户名@服务器IP:/tmp/
>
> # 服务器上
> mkdir -p /tmp/hvac-src && tar xzf /tmp/hvac.tgz -C /tmp/hvac-src
> sudo DOMAIN=你的域名 EMAIL=你的邮箱 SRC_DIR=/tmp/hvac-src bash /tmp/hvac-src/deploy/install.sh
> ```

**开启大模型 API 反代**（可选，团队共用时推荐——Key 只留服务端，且没有 CORS 问题）

```bash
sudo mkdir -p /etc/hvac-sim
sudo cp /opt/hvac-sim/deploy/api.env.example /etc/hvac-sim/api.env
sudo chmod 600 /etc/hvac-sim/api.env
sudo nano /etc/hvac-sim/api.env        # 填真实 Key
sudo DOMAIN=你的域名 bash /opt/hvac-sim/deploy/install.sh    # 重跑，这次会带上 /api/
```

页面「API 配置」里 Base URL 填 `/api/deepseek`（或你在 env 里起的名字），Key 栏填 `proxy`
之类的占位符即可。

**日常运维**

```bash
systemctl list-timers hvac-sim-update.timer     # 看下次自动更新时间
systemctl start hvac-sim-update.service         # 立刻拉一次更新
journalctl -u hvac-sim-proxy -n 50              # 看反代日志
nginx -t && systemctl reload nginx              # 改完配置自查再生效
```

**已知边界**：脚本本身在容器里以 `DRY_RUN` 跑通了全流程（含真实克隆与构建、配置文件生成），
但开发环境装不了 nginx / certbot / systemd，**这三者的实际行为未在本项目实测**。脚本在
reload 前会先 `nginx -t`，配置有错会当场停住而不是把站点搞挂；证书申请失败也只影响 HTTPS，
HTTP 站点仍然可用。

---

### 2.8 用 GitHub Actions 部署（不想自己 ssh 上服务器时）

`.github/workflows/deploy.yml` 把 §2.7 的脚本挪到 Actions runner 上执行：runner 把**本次 checkout
的源码**打包 scp 到服务器，再 ssh 进去以 `SRC_DIR=` 离线模式执行 `install.sh`，跑完从 runner 实际
访问站点做验收。

这条路对**服务器连不上 GitHub** 的情况尤其合适：代码是 runner 推过去的，服务器全程不需要访问
github.com。部署过程有完整日志，脚本版本也不会漂移。

先在仓库 **Settings → Secrets and variables → Actions** 加三个 secret：

| Secret | 值 |
| --- | --- |
| `DEPLOY_HOST` | 服务器 IP 或域名 |
| `DEPLOY_SSH_KEY` | 能登录该服务器的私钥全文（含 `-----BEGIN/END-----` 两行） |
| `DEPLOY_DOMAIN` | 站点域名 |

可选：`DEPLOY_USER`（默认 root）、`DEPLOY_PORT`（默认 22）、`DEPLOY_EMAIL`（证书通知邮箱）。

然后到 **Actions → deploy → Run workflow**，两个开关：`skip_tls`（DNS 没生效时先只配 HTTP）、
`auto_update`（是否装服务器端的自动更新 timer）。

日志末尾的「验收」步骤会打印站点的 HTTP/HTTPS 响应头，并检查页面里有没有 `<!doctype html>`
和中文正文——这两项能一次性暴露"构建没成功"和"编码不对"两类问题。

**安全上要想清楚的一点**：这等于把一把能登录服务器的私钥交给 GitHub 保管，此后任何有本仓库
写权限的人都能通过点一下 Run workflow 在你的服务器上执行命令。建议：

- 专门生成一把只用于部署的密钥（`ssh-keygen -t ed25519 -f deploy_key`），不要复用你的日常密钥；
- 服务器 `~/.ssh/authorized_keys` 里给这把 key 加 `from="<GitHub runner 出口段>"` 或改用堡垒机；
- 不需要持续部署时，把这个 workflow 文件删掉或在 Actions 里禁用即可，secret 也一并删除。

不接受这个取舍就走 §2.7，自己在服务器上跑一次脚本，效果完全相同。

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
| 部署后打不开，浏览器一直转圈 | 九成是云控制台安全组没放行 80/443。先在服务器上 `curl -I http://127.0.0.1/` 确认 nginx 本机正常 |
| 部署后 `/api/` 返回 502 | 反代服务没起来：`systemctl status hvac-sim-proxy`、`journalctl -u hvac-sim-proxy -n 50` |
| certbot 申请证书失败 | 多半是 80 端口没放行或 DNS 未生效。站点仍可用 HTTP 访问，解析好后重跑脚本即可 |
| 服务器上改了文件，自动更新把改动冲掉了 | 设计如此：`hvac-sim-update.service` 用 `git reset --hard` 与远端对齐。要在服务器改就先 `systemctl disable --now hvac-sim-update.timer` |
| 双击 `启动仿真台.command` 弹「无法验证开发者」 | 只有从网页下载 zip 才会被 Gatekeeper 隔离，`git clone` 下来的不会。右键 →「打开」→「打开」放行一次即可，或 `xattr -d com.apple.quarantine 启动仿真台.command` |
| 双击 `.command` 提示 `permission denied` | 执行位丢了（下载 zip 常见）：`chmod +x 启动仿真台.command` |
| Windows 启动器报 `Python 3.8+ not found` | 装 Python 3 时没勾「Add python.exe to PATH」。重装勾上，或在仓库目录里用完整路径跑 `web\launch.py` |
| 启动器说「更新失败」 | 见 §1.7：没联网或本地改过文件。它只是提示，仿真台照常能用 |
| 除湿量看起来偏高 | 检查计划里的预热时长。室内湿平衡时间常数约 25 h，预热不足 24 h 会显著高估除湿量（详见 `validation-report.md` 3b 节），助手默认预热 24 h 并对不足的情况告警 |
