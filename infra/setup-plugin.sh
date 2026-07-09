#!/bin/bash
# 下载 DuckDB Grafana 插件 v0.4.5
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_VERSION="0.4.5"
PLUGIN_ZIP="motherduck-duckdb-datasource-${PLUGIN_VERSION}.zip"
PLUGIN_URL="https://github.com/motherduckdb/grafana-duckdb-datasource/releases/download/v${PLUGIN_VERSION}/${PLUGIN_ZIP}"
PLUGIN_DIR="${SCRIPT_DIR}/grafana/plugins/motherduck-duckdb-datasource"

if [ -d "$PLUGIN_DIR" ]; then
    echo "✅ 插件已存在: ${PLUGIN_DIR}"
    exit 0
fi

echo "📦 下载 DuckDB 插件 v${PLUGIN_VERSION}..."
mkdir -p "${SCRIPT_DIR}/grafana/plugins"
curl -sL -o "/tmp/${PLUGIN_ZIP}" "$PLUGIN_URL"
unzip -q "/tmp/${PLUGIN_ZIP}" -d "${SCRIPT_DIR}/grafana/plugins/"
rm -f "/tmp/${PLUGIN_ZIP}"
echo "✅ 插件已安装到 ${PLUGIN_DIR}"
