#!/bin/bash
# Enterprise AI Copilot - Copilot 证书自动续期
# 用法：/opt/enterprise-ai-copilot/deploy/renew-copilot-cert.sh
#
# 此脚本部署在上方路径的服务器位置。
# Cron：/etc/cron.d/eac-copilot-certbot（每天 3:15 和 15:15 各执行一次）

set -euo pipefail

LOCK_FILE="/var/lock/eac-copilot-certbot.lock"
LOG_FILE="/opt/enterprise-ai-copilot/certbot/logs/renewal.log"
CERT_NAME="copilot.jintianchi.cn"
CERT_PATH="/opt/eat-what/deploy/nginx/certs/live/${CERT_NAME}/fullchain.pem"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# 检查证书是否存在
if [ ! -f "$CERT_PATH" ]; then
    log "ERROR: Certificate not found at $CERT_PATH"
    exit 1
fi

# 获取当前证书的修改时间
CERT_MTIME_BEFORE=$(stat -c %Y "$CERT_PATH" 2>/dev/null || echo "0")

# 使用 flock 运行 certbot renew
(
    flock -n 200 || { log "ERROR: Could not acquire lock"; exit 1; }

    log "Starting renewal check for ${CERT_NAME}"

    docker run --rm \
        -v /var/lib/docker/volumes/deploy_acme_webroot/_data:/var/www/html \
        -v /opt/eat-what/deploy/nginx/certs:/etc/letsencrypt \
        -v /opt/enterprise-ai-copilot/certbot/work:/var/lib/letsencrypt \
        -v /opt/enterprise-ai-copilot/certbot/logs:/var/log/letsencrypt \
        certbot/certbot:v5.7.0 \
        renew --cert-name "$CERT_NAME" --quiet

    log "Certbot renew completed"

    # 检查证书是否已续期
    CERT_MTIME_AFTER=$(stat -c %Y "$CERT_PATH" 2>/dev/null || echo "0")

    if [ "$CERT_MTIME_BEFORE" != "$CERT_MTIME_AFTER" ]; then
        log "Certificate renewed, testing nginx config"

        # 测试 nginx 配置
        if docker exec eat-what-nginx-prod nginx -t >> "$LOG_FILE" 2>&1; then
            log "Nginx config test passed, reloading"
            docker exec eat-what-nginx-prod nginx -s reload >> "$LOG_FILE" 2>&1
            log "Nginx reloaded successfully"
        else
            log "ERROR: Nginx config test failed, not reloading"
            exit 1
        fi
    else
        log "Certificate not renewed, no action needed"
    fi

) 200>"$LOCK_FILE"

log "Renewal check completed"
