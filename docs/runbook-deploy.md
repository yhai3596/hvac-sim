# 部署规程（Runbook）

把仿真台部署到一台 Linux 服务器的完整流程。内容来自一次真实部署（腾讯云轻量、Ubuntu 24.04、
域名 `hvac.geopro.top`）踩过的全部坑——每一条故障处置都是实际发生过、并已固化进脚本的。

面向两类读者：照着做的人，和被叫来协助的 AI（后者请连同 [`ai-deploy-prompt.md`](ai-deploy-prompt.md) 一起读）。

---

## 0. 一分钟版本

```bash
# ① 体检（只读，不改任何东西）
bash deploy/preflight.sh 你的域名

# ② 部署（幂等，可反复跑）
sudo DOMAIN=你的域名 EMAIL=你的邮箱 bash deploy/install.sh

# ③ 验收
curl -sSI https://你的域名/ | head -3
curl -sS https://你的域名/ | grep -c '空调控制算法'      # 应输出 ≥1
```

拿不到 `deploy/` 目录？见 §2 的三条取码路径。

---

## 1. 先决条件

| 项 | 要求 | 怎么确认 |
| --- | --- | --- |
| 操作系统 | Debian/Ubuntu 或 RHEL 系 | `preflight.sh` 第 1 节 |
| Python | 3.8+ | 同上 |
| 权限 | root 或**免密** sudo | `sudo -n true` 静默返回即可 |
| 域名 | A 记录已指向服务器公网 IP | `getent hosts 域名` |
| 端口 | 云控制台安全组放行 80、443 | **只能你在控制台点，脚本改不了** |

最后一条是最常见的"部署完打不开"。判断方法：服务器上 `curl -I http://127.0.0.1/` 正常但外网打不开，
那就是安全组。

---

## 2. 决策树

部署本身很简单，难的是"这台机器长什么样"。三个岔路口，`preflight.sh` 会把答案直接打出来。

### 2.1 代码怎么上服务器

国内服务器经常连不上 GitHub。`install.sh` 按顺序自动降级，你也可以直接指定：

| 情况 | 走哪条 | 命令 |
| --- | --- | --- |
| `github.com` 通 | git clone | 默认，什么都不用做 |
| 只有 `codeload.github.com` 通 | HTTPS 源码包 | 脚本自动兜底；或手动 `curl -fL -o /tmp/hvac.tgz "https://codeload.github.com/yhai3596/hvac-sim/tar.gz/refs/heads/main"` |
| 全都不通 | 本地打包传过去 | 见下方 §2.1.1 |

**只有 git 这条路能用自动更新 timer**（其余两条没有 git 元数据，脚本会明确告诉你并跳过）。

#### 2.1.1 离线部署

```bash
# 在能访问 GitHub 的机器上
git clone https://github.com/yhai3596/hvac-sim.git
tar czf /tmp/hvac.tgz --exclude=.git --exclude=dist -C hvac-sim .
scp /tmp/hvac.tgz 用户名@服务器IP:/tmp/

# 服务器上
mkdir -p /tmp/hvac-src && tar xzf /tmp/hvac.tgz -C /tmp/hvac-src
sudo DOMAIN=你的域名 EMAIL=你的邮箱 SRC_DIR=/tmp/hvac-src bash /tmp/hvac-src/deploy/install.sh
```

`SRC_DIR=` 是离线模式开关：用它指定的本地源码，全程不访问网络。

### 2.2 用哪个 web 服务器

**不要跟已有服务抢端口。** `install.sh` 会探测：

| 80/443 的占用者 | 脚本行为 |
| --- | --- |
| 空闲 | 装 nginx + certbot，正常配 |
| Caddy | **并入 Caddy**：写独立的 `/etc/caddy/hvac-sim.caddyfile`，不装 nginx 与 certbot（证书 Caddy 自动签） |
| 其他（Apache / 容器…） | 打印占用情况并停下，交给你决定 |

`WEBSERVER=nginx|caddy` 可强制指定。

### 2.3 证书归谁管

- nginx 路径 → certbot（`SKIP_TLS=1` 可先跳过，DNS 生效后重跑补签）
- Caddy 路径 → Caddy 自动申请与续期，不装 certbot

---

## 3. 标准流程

### 3.1 首次部署

```bash
bash deploy/preflight.sh 你的域名          # 先看清楚环境
sudo DOMAIN=你的域名 EMAIL=你的邮箱 bash deploy/install.sh
```

六个步骤及其预期输出：

| 步骤 | 做什么 | 正常输出关键行 |
| --- | --- | --- |
| 1 | 探测 web 服务器、装依赖 | `包管理器：apt，web 服务器：caddy` |
| 2 | 取代码到 `/opt/hvac-sim` | `已克隆到 xxxxxxx` / `使用本地源码` / `源码包解包完成` |
| 3 | 构建 `dist/index.html` | `已构建 …（160 KB，单文件、无外部依赖）` |
| 4 | 写站点配置并生效 | `caddy validate 通过` + `Caddy 已重载`（或 nginx 的 `nginx -t` + restart） |
| 5 | 可选的大模型 API 反代 | 无 `api.env` 则 `跳过` |
| 6 | 证书 + 自动更新 timer | Caddy 路径显示"证书由 Caddy 自动申请" |

### 3.2 更新

| 部署方式 | 更新做法 |
| --- | --- |
| git 路径 | `systemctl start hvac-sim-update.service`，或等 30 分钟的 timer |
| 源码包 / 离线 | 重新取一次代码再跑一遍 `install.sh`（幂等） |
| GitHub Actions | Actions → deploy → Run workflow，分支选 `main`（见 `deploy.md` §2.8） |

timer 只在 `$APP_DIR` 是 git 工作副本时才装。源码包与 Actions 这两条路下 `$APP_DIR` 通常没有
`.git`，脚本会明确跳过并在日志里说明——**这两种部署没有后台自动更新，更新就是再跑一次部署**。

### 3.3 开启大模型 API 反代（可选）

```bash
sudo mkdir -p /etc/hvac-sim
sudo cp /opt/hvac-sim/deploy/api.env.example /etc/hvac-sim/api.env
sudo chmod 600 /etc/hvac-sim/api.env
sudo nano /etc/hvac-sim/api.env          # 填真实 Key
sudo DOMAIN=你的域名 bash /opt/hvac-sim/deploy/install.sh   # 重跑，这次会挂上 /api/
```

Key 只留在服务器上，浏览器拿不到；页面「API 配置」里 Base URL 填 `/api/<名字>`，Key 栏填占位符。

---

## 4. 验收清单

四条都过才算部署完成：

```bash
# 1) HTTPS 有响应
curl -sSI https://你的域名/ | head -3

# 2) 返回的是我们的页面，不是别人的欢迎页
curl -sS https://你的域名/ | grep -c '空调控制算法'          # ≥1

# 3) 编码正确（中文不乱码）——响应头里应有 charset
curl -sSI https://你的域名/ | grep -i content-type

# 4) HTTP 会跳到 HTTPS
curl -sSI http://你的域名/ | head -3                        # 301/308
```

**第 2 条不能省。** 站点"能打开"但打开的是 Caddy/nginx 默认欢迎页，是这次部署里最耗时的
一个假象——服务在跑、配置校验通过、日志没有报错，就是内容不对。

---

## 5. 故障速查

按这次实际遇到的顺序排列。

### 5.1 `git clone` 报 `GnuTLS recv error (-110)` / `Connection reset`

服务器到 `github.com` 的连接被中断（国内机器常见），不是证书问题。
→ 让脚本自动降级到源码包，或走 §2.1.1 离线部署。不要试图关闭 TLS 校验。

### 5.2 `curl` 拉脚本无响应、光标卡住

`raw.githubusercontent.com` 在国内被墙的概率比 `codeload` 高，而 `curl -fsSL` 的 `-s`
把错误也吞了，所以屏幕上什么都没有。
→ 去掉 `-s`、加 `-m 120` 再看；或改用 `codeload` 拉整包。

### 5.3 nginx 起不来：`bind() to 0.0.0.0:80 failed (98: Address already in use)`

有别的服务占着 80。**不要杀掉它**——那多半是别人的生产站点。
→ `ss -lntp | grep -E ':80|:443'` 看是谁。是 Caddy 就并入 Caddy（脚本会自动这么做）。

### 5.4 站点能打开，但显示的是 Caddy/nginx 的默认欢迎页

两个独立原因，都遇到过：

1. **域名已被现有配置占用**：主 Caddyfile 里有一个站点块列了一串域名（含你的），统统指向
   `/usr/share/caddy`。新写的站点文件不会生效，同名两处还会让 Caddy 报冲突。
   → 必须把域名从那个块的地址列表里摘出来。`install.sh` 会自动做，并保留其余域名。
2. **80 端口被 `:80` 兜底块接管**：站点块只写域名时 Caddy 把它放在 443，HTTP 那边被兜底块截胡。
   → 站点文件里显式写一个 `http://域名 { redir https://{host}{uri} }`，带 Host 的块比 `:80` 更具体。

### 5.5 判据陷阱（这条是给协助部署的人看的）

用"`caddy adapt` 展开后的配置里有没有这个域名"判断站点是否生效，**会被别人的站点块骗过去**。
正确判据是看**只有你的配置才会出现的字符串**，比如站点根目录 `/opt/hvac-sim/dist`：

```bash
sudo caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile | grep -c /opt/hvac-sim/dist
```

同类原则：验证一件事时，要找**只有成功才会出现**的证据，别找"成功和失败都可能出现"的。

### 5.6 证书没签下来

- 安全组没放行 443（或 80，HTTP-01 校验要用）
- DNS 还没生效 → 先 `SKIP_TLS=1` 跑 HTTP，解析好后重跑补签
- Caddy 路径下看 `journalctl -u caddy -n 50` 里的 ACME 记录

### 5.7 非交互执行卡死

`sudo` 要密码时，SSH/CI 里会静默挂起。
→ `sudo -n true` 先验；需要就配 NOPASSWD，或改用 root 登录。

---

### 5.8 GitHub Actions 部署连不上服务器

两个卡点，都只给一句含糊的报错，按下面区分：

| 日志里的话 | 含义 | 处理 |
| --- | --- | --- |
| `Load key ...: error in libcrypto` | `DEPLOY_SSH_KEY` 不是一份能解析的私钥 | 工作流会打印「形状」（非空行数、首尾标识行）。带 CRLF / 挤成一行 / 漏 `BEGIN`-`END` 会自动修；带口令、`.ppk`、粘贴截断需重贴。见 `deploy.md` §2.8 |
| `Permission denied (publickey,password)` | 钥匙能用，但服务器不认 | 见下 |

`Permission denied` 的排查顺序：

```bash
# 1) 工作流日志里看协商摘要：有 Offering public key、没有 Server accepts key → 服务器不认这把钥匙
# 2) 服务器侧看鉴权日志
sudo journalctl -u ssh -n 50 --no-pager | grep -i -E 'sshd|refused|denied'
#    Connection closed by authenticating user root <IP>  → 该用户下没有这把公钥
#    bad ownership or modes for directory ...            → 权限/属主不对
# 3) 比对指纹（空文件会报 is not a public key file）
sudo ssh-keygen -lf /root/.ssh/authorized_keys
sudo sshd -T | grep -Ei 'permitrootlogin|pubkeyauthentication|authorizedkeysfile'
```

最常见的一种：公钥加到了自己平时登录的那个用户（在 `ubuntu@` 提示符下敲 `~/.ssh/authorized_keys`
就是 `/home/ubuntu/...`），而工作流 `DEPLOY_USER` 没设、默认以 root 登录。两条出路——把公钥复制一份
到 `/root/.ssh/authorized_keys`，或加 `DEPLOY_USER` secret 填那个普通用户（需免密 sudo）。

---

## 6. 回滚

| 改动 | 回滚 |
| --- | --- |
| Caddy 主配置 | `install.sh` 每次改动前都备份成 `/etc/caddy/Caddyfile.bak-<时间戳>`，`cp -a` 回去再 `systemctl reload caddy` |
| 本站点配置 | 删掉 `/etc/caddy/hvac-sim.caddyfile`（或 `/etc/nginx/sites-*/hvac-sim.conf`）后 reload |
| 代码版本 | git 路径：`git -C /opt/hvac-sim checkout <提交号> && python3 /opt/hvac-sim/web/serve.py --build` |
| 整站移除 | 上面三条 + `rm -rf /opt/hvac-sim`，并 `systemctl disable --now hvac-sim-update.timer hvac-sim-proxy` |

脚本自身的安全设计：**改配置前先备份，校验或展开检查失败一律还原备份再退出**，
不会留下一个改坏了的 Caddyfile；reload 而非 restart，现有连接不断。

---

## 7. 本次部署的环境事实（供参考对照）

| 项 | 值 |
| --- | --- |
| 服务器 | 腾讯云轻量，Ubuntu，用户 `ubuntu`，免密 sudo 可用 |
| 到 GitHub | `github.com` 通但慢（8.5s）、`codeload` 通（0.5s）、`raw.githubusercontent.com` **不通**（超时） |
| 80/443 | 已被 Caddy 占用，同机还有 7 个其他域名 |
| 主 Caddyfile | 一个站点块列 8 个域名（含本域名）统统指向 `/usr/share/caddy`，**无 import 行** |
| 最终方案 | codeload 取包 → `SRC_DIR` 离线安装 → 并入 Caddy → 证书由 Caddy 自动签 |
| 部署分支 | `main`（`install.sh` 的 `BRANCH` 默认值、文档安装地址、工作流传入值都跟它） |
| SSH | `PermitRootLogin yes`、`PubkeyAuthentication yes`；`/root/.ssh` 权限属主正常 |
| 首次配 Actions 时踩到的 | `DEPLOY_SSH_KEY` 只贴了 key 体（漏 `BEGIN`/`END`）；部署公钥加在了 `ubuntu` 名下而工作流默认以 root 登录，`/root/.ssh/authorized_keys` 当时是 0 字节 |
| 自动更新 | 无。`/opt/hvac-sim` 不是 git 工作副本，timer 未安装；更新走 Actions 手动触发 |
