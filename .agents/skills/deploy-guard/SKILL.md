---
name: deploy-guard
description: |
  Deployment readiness checks for JQData platform.
  Use when asked to "准备部署" / "检查部署条件" / "deploy check" / 
  before deploying to C/D production servers.
  Covers server topology, container health, config separation, and deployment sequence.
---

# Deploy Guard — JQData

## Pre-Deployment Checklist

### 1. Environment Confirmation
- [ ] User explicitly approved deployment target (C/D server)
- [ ] Deploy as `deploy` user, never root
- [ ] SSH access verified: `ssh deploy@<host>`

### 2. Server Topology Verification

| Target | Host | Path | Services |
|--------|------|------|----------|
| C (可视化/调度) | 139.196.186.67 | /data/jqdata-platform | Grafana, Airflow |
| D (核心数据层) | 101.132.161.52 | /data/jqdata-platform | ClickHouse, Redis, FastAPI, Nginx |

- **Server A/B**: ❌ Do NOT touch. A runs core business (AssetWotu/yuntuCenter), B is test environment.

### 3. Git Status Check
```bash
git status  # must be clean
git log --oneline -3  # verify correct commit/tag
```

### 4. Configuration Separation
- [ ] `docker-compose.*.yml` in Git is read-only reference
- [ ] `.env.production` is server-local — never modify in repo
- [ ] Verify `CONFIG_VERSION` matches code version

### 5. Container Health (Server D)
```bash
docker ps  # check running containers
docker logs clickhouse --tail 50  # check errors
docker logs redis --tail 20  # check errors
docker logs api --tail 50  # check FastAPI errors
```

### 6. Database Backup (Before DDL)
- [ ] If migration scripts included, backup first:
```bash
docker exec clickhouse clickhouse-client -q "SHOW TABLES"
```

### 7. Deploy Sequence
```bash
# On target server (C or D)
cd /data/jqdata-platform
./scripts/deploy.sh v0.1.x
```

**Post-deploy:**
- [ ] Verify Nginx responds on 18080
- [ ] Verify FastAPI health endpoint
- [ ] Verify ClickHouse query responds

## Red Lines

| Violation | Action |
|-----------|--------|
| Deploy to A/B servers | ❌ STOP |
| Root login | ❌ STOP |
| Modify `.env.production` in repo | ❌ STOP |
| Deploy without user approval | ❌ STOP |
| Skip git status check | ⚠️ WARN |
| No backup before DDL | ⚠️ WARN |
