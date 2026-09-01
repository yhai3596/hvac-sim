#!/usr/bin/env bash
# 在服务器上一键部署空调控制算法仿真平台（静态站点 + 可选的大模型 API 反代）。
#
#   curl -fsSL https://raw.githubusercontent.com/yhai3596/-/claude/ac-control-algorithm-simulation-zivkl0/deploy/install.sh -o install.sh
#   sudo DOMAIN=hvac.geopro.top EMAIL=you@example.com bash install.sh
#
# 幂等：重复执行只做增量（已存在的仓库走 git pull，已签发的证书不重复申请）。
# 可调环境变量：
#   DOMAIN     站点域名（必填）
#   EMAIL      Let's Encrypt 通知邮箱；留空则用 --register-unsafely-without-email
#   SKIP_TLS=1 只配 HTTP，不申请证书（DNS 还没生效时先这样，之后再跑一遍即可补证书）
#   APP_DIR    代码目录，默认 /opt/hvac-sim
#   BRANCH     部署分支，默认 claude/ac-control-algorithm-simulation-zivkl0
#   AUTO_UPDATE=0  不安装每 30 分钟自动拉取更新的 systemd timer
#   DRY_RUN=1  只打印将要执行的特权命令并把配置写到 NGINX_DIR/SYSTEMD_DIR（用于自测）
set -euo pipefail

DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
APP_DIR="${APP_DIR:-/opt/hvac-sim}"
REPO="${REPO:-https://github.com/yhai3596/-.git}"
BRANCH="${BRANCH:-claude/ac-control-algorithm-simulation-zivkl0}"
ENV_FILE="${ENV_FILE:-/etc/hvac-sim/api.env}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SKIP_TLS="${SKIP_TLS:-0}"
AUTO_UPDATE="${AUTO_UPDATE:-1}"
DRY_RUN="${DRY_RUN:-0}"
PROXY_PORT="${PROXY_PORT:-8010}"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die()  { printf '\n\033[31m错误：%s\033[0m\n' "$*" >&2; exit 1; }

# 特权动作统一走这里，DRY_RUN 下只打印，便于在没有 root 的机器上验证流程
run() {
  if [ "$DRY_RUN" = "1" ]; then printf '   [dry-run] %s\n' "$*"; else "$@"; fi
}

[ -n "$DOMAIN" ] || die "必须指定域名，例如：sudo DOMAIN=hvac.geopro.top bash install.sh"
if [ "$DRY_RUN" != "1" ] && [ "$(id -u)" != "0" ]; then die "请用 root 或 sudo 执行"; fi

# ---------- 1. 装依赖 ----------
say "1/6 安装依赖（git / python3 / nginx / certbot）"
if command -v apt-get >/dev/null 2>&1; then
  PKG=apt
  run env DEBIAN_FRONTEND=noninteractive apt-get update -qq
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 nginx
  [ "$SKIP_TLS" = "1" ] || run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot python3-certbot-nginx
elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
  PKG=$(command -v dnf >/dev/null 2>&1 && echo dnf || echo yum)
  run "$PKG" install -y -q git python3 nginx
  [ "$SKIP_TLS" = "1" ] || run "$PKG" install -y -q certbot python3-certbot-nginx
else
  die "没找到 apt/dnf/yum，请手动安装 git、python3、nginx（可选 certbot）后重跑"
fi
info "包管理器：$PKG"

# ---------- 2. 取代码 ----------
say "2/6 获取代码到 $APP_DIR（分支 $BRANCH）"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" reset --hard --quiet "origin/$BRANCH"      # 服务器只读部署，以远端为准
  info "已更新到 $(git -C "$APP_DIR" rev-parse --short HEAD)"
else
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --quiet --branch "$BRANCH" "$REPO" "$APP_DIR"
  info "已克隆到 $(git -C "$APP_DIR" rev-parse --short HEAD)"
fi
chmod 755 "$APP_DIR"

# ---------- 3. 构建 ----------
say "3/6 构建 dist/index.html"
python3 "$APP_DIR/web/serve.py" --build
chmod 755 "$APP_DIR/dist"; chmod 644 "$APP_DIR/dist/index.html"

# ---------- 4. nginx ----------
say "4/6 写 nginx 站点配置"
if [ -d /etc/nginx/sites-enabled ]; then
  NGINX_DIR="${NGINX_DIR:-/etc/nginx/sites-available}"; NGINX_LINK=1
else
  NGINX_DIR="${NGINX_DIR:-/etc/nginx/conf.d}"; NGINX_LINK=0
fi
mkdir -p "$NGINX_DIR"
SITE="$NGINX_DIR/hvac-sim.conf"

PROXY_BLOCK=""
if [ -f "$ENV_FILE" ]; then
  PROXY_BLOCK=$(cat <<PROXY

    # 大模型 API 反代：真实 Key 只在 $ENV_FILE 里，浏览器拿不到
    location /api/ {
        proxy_pass http://127.0.0.1:$PROXY_PORT;
        proxy_set_header Host \$host;
        proxy_read_timeout 300s;      # 生成报告可能要一两分钟，默认 60s 不够
        proxy_buffering off;
    }
PROXY
)
fi

cat > "$SITE" <<NGINX
# 由 deploy/install.sh 生成，重跑脚本会覆盖本文件
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    root $APP_DIR/dist;
    index index.html;
    charset utf-8;                     # 不能省，否则中文可能乱码

    gzip on;
    gzip_types text/html;
    gzip_min_length 1024;              # 160 KB 的页面可压到约 30 KB

    location = /index.html {
        add_header Cache-Control "no-store";   # 更新后刷新即生效
    }

    location /.well-known/acme-challenge/ { root /var/www/html; }   # certbot 续期用
$PROXY_BLOCK
    location / { try_files \$uri \$uri/ /index.html; }
}
NGINX
info "已写入 $SITE"
# 注意：这里必须用 if 而不是 `[ ... ] && cmd`——set -e 下条件为假会让整个脚本退出
if [ "$NGINX_LINK" = "1" ]; then run ln -sf "$SITE" /etc/nginx/sites-enabled/hvac-sim.conf; fi
if [ -f /etc/nginx/sites-enabled/default ]; then run rm -f /etc/nginx/sites-enabled/default; fi

# SELinux（CentOS/TencentOS 常见）：不打标签 nginx 读不了 /opt 下的文件
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null || echo Disabled)" = "Enforcing" ]; then
  info "检测到 SELinux Enforcing，给 dist 打 httpd_sys_content_t 标签"
  run chcon -Rt httpd_sys_content_t "$APP_DIR/dist"
  run setsebool -P httpd_can_network_connect 1     # 反代需要
fi

run nginx -t
run systemctl enable --now nginx
run systemctl reload nginx

# ---------- 5. 可选：大模型 API 反代 ----------
say "5/6 大模型 API 反代"
if [ -f "$ENV_FILE" ]; then
  chmod 600 "$ENV_FILE"
  cat > "$SYSTEMD_DIR/hvac-sim-proxy.service" <<UNIT
[Unit]
Description=HVAC 仿真台 大模型 API 反向代理
After=network-online.target

[Service]
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $APP_DIR/web/serve.py --host 127.0.0.1 --port $PROXY_PORT --proxy --no-browser
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$APP_DIR/dist

[Install]
WantedBy=multi-user.target
UNIT
  run systemctl daemon-reload
  run systemctl enable --now hvac-sim-proxy.service
  info "已启动反代（只监听 127.0.0.1:$PROXY_PORT，公网只能经 nginx 的 /api/ 访问）"
else
  info "未发现 $ENV_FILE，跳过。要开启见 deploy/api.env.example"
fi

# ---------- 6. 证书 + 自动更新 ----------
say "6/6 HTTPS 与自动更新"
if [ "$SKIP_TLS" = "1" ]; then
  info "SKIP_TLS=1，跳过证书申请"
else
  RESOLVED=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)
  info "域名解析：${RESOLVED:-未解析到}"
  if [ -z "$RESOLVED" ]; then
    info "DNS 还没生效，先只跑 HTTP；解析好之后重跑本脚本即可补证书"
  elif [ -n "$EMAIL" ]; then
    run certbot --nginx -d "$DOMAIN" --agree-tos -m "$EMAIL" --redirect --non-interactive
  else
    run certbot --nginx -d "$DOMAIN" --agree-tos --register-unsafely-without-email --redirect --non-interactive
  fi
fi

if [ "$AUTO_UPDATE" = "1" ]; then
  cat > "$SYSTEMD_DIR/hvac-sim-update.service" <<UNIT
[Unit]
Description=拉取 HVAC 仿真台最新代码并重新构建
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/git -C $APP_DIR fetch --quiet origin $BRANCH
ExecStart=/usr/bin/git -C $APP_DIR reset --hard --quiet origin/$BRANCH
ExecStart=/usr/bin/python3 $APP_DIR/web/serve.py --build
# 前缀 - 表示失败也不算错：没装反代时这个单元不存在，属正常
ExecStart=-/bin/systemctl try-restart hvac-sim-proxy.service
UNIT
  cat > "$SYSTEMD_DIR/hvac-sim-update.timer" <<UNIT
[Unit]
Description=每 30 分钟检查一次 HVAC 仿真台更新

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  run systemctl daemon-reload
  run systemctl enable --now hvac-sim-update.timer
  info "已装自动更新 timer（每 30 分钟；systemctl list-timers hvac-sim-update.timer 查看）"
else
  info "AUTO_UPDATE=0，未安装自动更新"
fi

say "完成"
info "站点：http://$DOMAIN/  （若已签发证书则为 https://）"
info "代码：$APP_DIR    构建产物：$APP_DIR/dist/index.html"
info "手动更新：systemctl start hvac-sim-update.service"
info "别忘了在云控制台安全组放行 80 与 443 端口"
