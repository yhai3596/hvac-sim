#!/usr/bin/env bash
# 部署前环境探测：只读，不装任何东西、不改任何配置。
# 把结果贴给协助部署的人（或 AI），能省掉大部分来回试探。
#
#   bash preflight.sh [域名]
#
# 退出码恒为 0——这是体检不是关卡，有问题由人判断。

DOMAIN="${1:-${DOMAIN:-}}"
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
sec()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }

sec "1 系统与权限"
info "$( (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || uname -sr )"
info "内核 $(uname -r)   架构 $(uname -m)"
for p in apt-get dnf yum; do command -v $p >/dev/null 2>&1 && { ok "包管理器：$p"; break; }; done
if command -v python3 >/dev/null 2>&1; then ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2)"; else bad "没有 python3（构建页面需要）"; fi
if [ "$(id -u)" = 0 ]; then ok "当前是 root"
elif sudo -n true 2>/dev/null; then ok "免密 sudo 可用（非交互部署的前提）"
else bad "sudo 需要密码：CI/非交互部署会卡死，本地手动执行则无妨"; fi
df -h / 2>/dev/null | awk 'NR==2{printf "    根分区可用 %s（已用 %s）\n",$4,$5}'

sec "2 谁在占 80/443"
if command -v ss >/dev/null 2>&1; then
  L=$(ss -lntpH 2>/dev/null | awk '$4 ~ /:(80|443)$/')
  if [ -z "$L" ]; then ok "80/443 空闲，可以直接装 nginx"
  else
    printf '%s\n' "$L" | sed 's/^/    /'
    if printf '%s' "$L" | grep -q '"caddy"';    then warn "Caddy 在用这两个端口 → 站点应并入 Caddy，不要让 nginx 去抢"; fi
    if printf '%s' "$L" | grep -q '"nginx"';    then warn "nginx 已在运行 → 站点应作为一个 server 块加进去"; fi
    if printf '%s' "$L" | grep -qE '"(apache2|httpd)"'; then warn "Apache 在用 → 需另行决定共存方式"; fi
    if printf '%s' "$L" | grep -q '"docker-proxy"'; then warn "端口被容器占用 → 需确认容器内是什么服务"; fi
  fi
else warn "没有 ss，跳过端口检查"; fi

sec "3 已有的 web 服务器与配置布局"
for svc in caddy nginx apache2 httpd; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^$svc\.service"; then
    printf '    %-8s %s\n' "$svc" "$(systemctl is-active $svc 2>/dev/null)/$(systemctl is-enabled $svc 2>/dev/null)"
  fi
done
[ -f /etc/caddy/Caddyfile ] && { ok "/etc/caddy/Caddyfile 存在"; 
  grep -qE '^[[:space:]]*import' /etc/caddy/Caddyfile && info "已有 import 行" || warn "没有 import 行：新增独立站点文件时需要补一行"; }
[ -d /etc/nginx/sites-enabled ] && info "nginx 用 sites-available/sites-enabled 布局"
[ -d /etc/nginx/conf.d ] && [ ! -d /etc/nginx/sites-enabled ] && info "nginx 用 conf.d 布局"

sec "4 域名是否已被现有配置占用"
if [ -z "$DOMAIN" ]; then warn "没传域名，跳过（用法：bash preflight.sh 你的域名）"
else
  hit=0
  for f in /etc/caddy/Caddyfile /etc/caddy/*.caddyfile /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*; do
    [ -f "$f" ] || continue
    if grep -qF "$DOMAIN" "$f" 2>/dev/null; then hit=1; warn "$f 里已经出现 $DOMAIN"; grep -nF "$DOMAIN" "$f" | head -3 | sed 's/^/      /'; fi
  done
  [ "$hit" = 0 ] && ok "$DOMAIN 尚未被任何现有站点配置占用"
  [ "$hit" = 1 ] && info "→ 必须先把域名从原有站点块里摘出来，否则新配置不会生效（还可能报站点冲突）"
fi

sec "5 DNS 与本机公网 IP"
if [ -n "$DOMAIN" ]; then
  R=$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -1)
  [ -n "$R" ] && ok "$DOMAIN → $R" || bad "$DOMAIN 解析不到：证书申请会失败，可先用 SKIP_TLS=1 只跑 HTTP"
  MY=$(curl -sS -m 8 https://api.ipify.org 2>/dev/null || curl -sS -m 8 https://ifconfig.me 2>/dev/null || echo "")
  if [ -n "$MY" ]; then
    info "本机公网出口 IP：$MY"
    [ -n "$R" ] && [ "$R" != "$MY" ] && warn "解析地址与本机出口 IP 不同（有 CDN/代理则正常，否则检查 A 记录）"
  fi
fi

sec "6 到 GitHub 各域的连通性（决定用哪条取码路径）"
for h in github.com codeload.github.com raw.githubusercontent.com; do
  R=$(curl -sS -m 10 -o /dev/null -w '%{http_code} %{time_total}s' "https://$h/" 2>/dev/null || echo "000 超时")
  case "$R" in
    000*) bad "$(printf '%-28s %s' "$h" "$R")" ;;
    *)    ok  "$(printf '%-28s %s' "$h" "$R")" ;;
  esac
done
info "全通 → 直接 git clone；只有 codeload 通 → 用源码包；都不通 → 本地打包 scp 过来（SRC_DIR=）"

sec "7 已有的部署痕迹"
if [ -d /opt/hvac-sim ]; then
  ok "/opt/hvac-sim 已存在"
  [ -d /opt/hvac-sim/.git ] && info "是 git 仓库（可用 systemd timer 自动更新）" || info "非 git 目录（自动更新不可用，更新需重新推代码）"
  [ -f /opt/hvac-sim/dist/index.html ] && info "已构建：$(du -h /opt/hvac-sim/dist/index.html | cut -f1)"
else info "尚未部署过"; fi
[ -f /etc/hvac-sim/api.env ] && ok "/etc/hvac-sim/api.env 存在（会启用大模型 API 反代）"
if command -v getenforce >/dev/null 2>&1; then
  [ "$(getenforce 2>/dev/null)" = "Enforcing" ] && warn "SELinux Enforcing：静态文件需要打 httpd_sys_content_t 标签" || info "SELinux：$(getenforce 2>/dev/null)"
fi

printf '\n\033[1m== 探测完毕（本脚本没有修改任何东西）\033[0m\n'
