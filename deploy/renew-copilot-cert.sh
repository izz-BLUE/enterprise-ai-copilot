#!/bin/bash
# Enterprise AI Copilot - Copilot Certificate Auto-Renewal
# Usage: /opt/enterprise-ai-copilot/deploy/renew-copilot-cert.sh
#
# This script is deployed to the server at the path above.
# Cron: /etc/cron.d/eac-copilot-certbot (twice daily at 3:15 AM and 3:15 PM)

set -euo pipefail

LOCK_FILE="/var/lock/eac-copilot-certbot.lock"
LOG_FILE="/opt/enterprise-ai-copilot/certbot/logs/renewal.log"
CERT_NAME="copilot.jintianchi.cn"
CERT_PATH="/opt/eat-what/deploy/nginx/certs/live/${CERT_NAME}/fullchain.pem"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Check if certificate exists
if [ ! -f "$CERT_PATH" ]; then
    log "ERROR: Certificate not found at $CERT_PATH"
    exit 1
fi

# Get current certificate mtime
CERT_MTIME_BEFORE=$(stat -c %Y "$CERT_PATH" 2>/dev/null || echo "0")

# Run certbot renew with flock
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

    # Check if certificate was renewed
    CERT_MTIME_AFTER=$(stat -c %Y "$CERT_PATH" 2>/dev/null || echo "0")

    if [ "$CERT_MTIME_BEFORE" != "$CERT_MTIME_AFTER" ]; then
        log "Certificate renewed, testing nginx config"

        # Test nginx config
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
