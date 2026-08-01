#!/bin/sh
# BridGes API 容器入口。
#
# 容器部署不要求宿主机创建 `.env`：普通配置全部由 Compose 环境变量注入；
# 若调用方未显式提供 BRIDGES_SECRET_KEY / BRIDGES_SECRET_KEY_FILE，则在本
# 容器数据卷（/var/lib/bridges）内首次启动时生成并持久化主密钥，保证重启
# 后本地状态仍可解密。密钥文件权限收紧为仅属主可读写。

set -eu

DATA_DIR=/var/lib/bridges

if [ -z "${BRIDGES_SECRET_KEY:-}" ] && [ -z "${BRIDGES_SECRET_KEY_FILE:-}" ]; then
    KEY_FILE="$DATA_DIR/secret.key"
    if [ ! -s "$KEY_FILE" ]; then
        mkdir -p "$DATA_DIR"
        chmod 700 "$DATA_DIR"
        python -c "import secrets; print(secrets.token_hex(32))" > "$KEY_FILE"
        chmod 600 "$KEY_FILE"
    fi
    export BRIDGES_SECRET_KEY_FILE="$KEY_FILE"
fi

exec "$@"
