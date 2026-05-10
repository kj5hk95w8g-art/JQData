# A 服务器监控接入方案

> 目标：在 C 服务器 Grafana 上查看 A 服务器（应用服务器）性能指标。

---

## 方案概述

B + D 是同一个方案的两面：
- **B（数据采集）**：在 A 服务器部署 node-exporter，暴露系统指标
- **D（数据展示）**：在 C 服务器 Grafana 上增加 A 服务器的面板

中间通过 D 服务器的 Prometheus 做数据聚合。

```
A 服务器 (106.14.141.212)
    └── node-exporter (:9100)
            │
            │ 内网 VPC 172.24.52.0/24
            ▼
D 服务器 (101.132.161.52)
    └── Prometheus (:9090)  抓取 A:9100
            │
            │ 内网
            ▼
C 服务器 (139.196.186.67)
    └── Grafana (:3000)  展示 A 的指标
```

---

## 具体执行步骤

### 步骤1：A 服务器部署 node-exporter（B）

```bash
ssh deploy@106.14.141.212

# 部署 node-exporter（只读系统指标，轻量容器）
docker run -d \
  --name jqdata-node-exporter-a \
  --restart=unless-stopped \
  --net=host \
  --pid=host \
  -v /:/host:ro \
  prom/node-exporter:latest \
  --path.rootfs=/host \
  --web.listen-address=172.24.52.238:9100
```

**验证：**
```bash
curl http://172.24.52.238:9100/metrics | head -5
```

### 步骤2：D 服务器 Prometheus 添加 A 为采集目标

修改 `/data/jqdata-platform/monitoring/prometheus.yml`，在 `scrape_configs` 下追加：

```yaml
  - job_name: 'node-exporter-a'
    static_configs:
      - targets: ['172.24.52.238:9100']
        labels:
          instance: 'server-a'
          role: 'application'
    metrics_path: /metrics
```

**重载配置（不重启容器）：**
```bash
ssh jqdata-d
curl -X POST http://localhost:9090/-/reload
```

> 如果热重载失败，执行 `docker restart jqdata-prometheus`（监控中断 3-5 秒）

### 步骤3：C 服务器 Grafana 添加 A 的面板（D）

**方式A：在现有 Node Exporter Full 面板中切换实例**
1. 登录 Grafana (`http://139.196.186.67:3000`)
2. 打开 Node Exporter Full 面板
3. 在变量选择器中新增 instance = `server-a`

**方式B：新建专用面板**
1. 导入 Dashboard ID `1860`（Node Exporter Full）
2. 修改查询条件：`job="node-exporter-a"`
3. 保存为 "Server A - 性能监控"

---

## 风险分析

| 风险 | 等级 | 说明 | 缓解措施 |
|------|------|------|---------|
| **AGENTS.md 红线：A 服务器禁止部署新组件** | 🔴 **高** | 明确违反"禁止部署任何新组件" | 需用户在对话中明确说"同意在 A 部署 node-exporter" |
| **端口暴露** | 🟡 中 | 9100 端口只绑定内网 IP，但仍在 VPC 内可访问 | 只绑定 `172.24.52.238:9100`，不映射到 `0.0.0.0`；安全组不开放 9100 |
| **资源占用** | 🟢 低 | node-exporter CPU < 1%, 内存 < 50MB | 对 4核16GB 的 A 服务器影响可忽略 |
| **容器冲突** | 🟢 低 | 9100 端口当前未占用 | 部署前已验证端口空闲 |
| **Prometheus 重启中断** | 🟢 低 | 热重载失败时需重启，监控中断 3-5 秒 | 优先使用 `/-/reload` 热重载 |
| **数据隔离** | 🟢 低 | node-exporter 只读系统指标，不访问业务数据 | 官方镜像，无写权限 |

---

## 与现有方案的对比

| 方案 | 部署位置 | 优点 | 缺点 |
|------|---------|------|------|
| **B+D（自建）** | A 部署 node-exporter | 数据实时、粒度细、与现有 Grafana 统一 | 违反 A 服务器红线 |
| **阿里云云监控** | 零部署 | 不碰 A 服务器、免维护 | 需登录阿里云控制台、粒度粗（1分钟） |
| **C 服务器 Grafana 直连 A** | 零部署（如果 A 已有指标端点） | 最简单 | A 没有现有指标端点，不可行 |

---

## 决策点

如果选 **B+D**，需要你明确说：
> **"同意在 A 服务器部署 node-exporter"**

如果选 **阿里云云监控**，无需任何部署，我直接告诉你控制台路径。
