# A 服务器监控接入

> 状态：✅ 已完成  
> 完成时间：2026-05-10  
> 版本：v0.1.3 / v0.1.4

---

## 已完成内容

| 步骤 | 内容 | 状态 |
|------|------|------|
| 1. A 服务器部署 node-exporter | `docker run` 部署，绑定 `172.24.52.238:9100` | ✅ |
| 2. 阿里云安全组配置 | 允许 `172.24.52.0/24` 访问 A 的 9100 端口 | ✅ 用户手动配置 |
| 3. Prometheus 配置更新 | 新增 `node-exporter-a` job，instance 标签为 `server A` | ✅ |
| 4. Grafana 面板 | Node Exporter Full 面板支持切换 `server D` / `server A` | ✅ |
| 5. deploy.sh 兜底 | 热重载后校验配置同步，未同步自动重启容器 | ✅ v0.1.4 |

---

## 架构

```
A 服务器 (106.14.141.212)
    └── node-exporter (172.24.52.238:9100)
            │
            │ VPC 内网 172.24.52.0/24
            ▼
D 服务器 (101.132.161.52)
    └── Prometheus (:9090)  采集 A:9100
            │
            │ 内网
            ▼
C 服务器 (139.196.186.67)
    └── Grafana (:3000)  展示 A/D 性能指标
```

---

## Grafana 使用

**URL：** `http://139.196.186.67:3000/d/afliqnsxl2hvka`  
**账号：** `admin` / `admin123`

打开面板后，在顶部变量选择器切换：

| 选择 | 说明 |
|------|------|
| **server D** | D 服务器（核心数据层）性能指标 |
| **server A** | A 服务器（应用服务器）性能指标 |

> 旧名称 `server-d` / `server-a` 为历史数据，30 天 retention 后自动清除。

---

## 运维备忘

### 重启 A 服务器 node-exporter

```bash
ssh deploy@106.14.141.212
docker restart jqdata-node-exporter-a
```

### 更新 node-exporter 镜像

```bash
ssh deploy@106.14.141.212
docker pull prom/node-exporter:latest
docker restart jqdata-node-exporter-a
```

---

*最后更新：2026-05-10*
