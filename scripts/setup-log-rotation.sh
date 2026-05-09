#!/bin/bash
# =============================================================================
# Docker 日志轮转配置
# 
# 作用：限制单个容器日志大小，防止撑满磁盘。
# 
# 方案A（推荐，热生效）：在 docker-compose.yml 里给每个服务加 logging 配置
#   不需要重启docker daemon，只对当前项目生效
# 
# 方案B（全局生效，需重启docker）：修改 /etc/docker/daemon.json
#   执行本脚本后需重启 docker 服务：systemctl restart docker
#   ⚠️ 生产环境重启docker会短暂中断所有容器，请在维护窗口执行
# =============================================================================
set -e

LOG_MAX_SIZE="100m"
LOG_MAX_FILE="3"

echo "📝 Docker 日志轮转配置"
echo "   单容器日志上限: ${LOG_MAX_SIZE} × ${LOG_MAX_FILE} = 300MB"
echo ""

# 方案B: 配置daemon.json（全局默认）
DAEMON_JSON="/etc/docker/daemon.json"

if [[ -f "$DAEMON_JSON" ]]; then
    echo "⚠️  ${DAEMON_JSON} 已存在，备份到 ${DAEMON_JSON}.bak.$(date +%s)"
    cp "$DAEMON_JSON" "${DAEMON_JSON}.bak.$(date +%s)"
fi

cat > "$DAEMON_JSON" <<EOF
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "${LOG_MAX_SIZE}",
    "max-file": "${LOG_MAX_FILE}"
  },
  "registry-mirrors": ["https://docker.mirrors.sjtug.sjtu.edu.cn"]
}
EOF

echo "✅ 已写入 ${DAEMON_JSON}"
echo ""
echo "⚠️  注意：此配置对新创建的容器生效。"
echo "   如需全局生效，请执行: systemctl restart docker"
echo "   如需仅对当前项目生效（不重启docker），请在 docker-compose.yml 中添加:"
cat <<'EOF'

    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "3"

EOF
