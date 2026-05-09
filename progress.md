# JQData 工作进度

> 每天早上打开看一下，了解当前状态、今天该做什么、明天计划。
> 数据更新时间：`2026-05-09`

---

## 1. 数据同步状态

| 表 | 数据量 | 状态 | 说明 |
|------|--------|------|------|
| **security_info** | 7,838 条 | ✅ | 股票/ETF/指数基本信息 |
| **index_daily** | 23,025 行 | ✅ | 15 个指数，2020-2026 |
| **stock_daily_pre** | 2,763,000 行 | ⚠️ | 1800 只股票（27 批中完成 9 批） |
| **stock_daily_post** | 0 行 | ❌ | 额度用完，明天开始 |

- 数据范围：2020-01-01 ~ 2026-05-08（近 5 年多）
- JQData 额度：1000 万/天（今天已用完，明天重置）

---

## 2. 今日完成（2026-05-09）

- [x] 正式版 JQData 账号开通
- [x] 指数数据全量同步（15 个指数，2020-2026）
- [x] 前复权股票日线同步 9 批（1800 只）
- [x] C/D 服务器 SSH key 配置
- [x] 创建 deploy 用户（免密 sudo docker）
- [x] 修复 sync_daily.py NaN 处理 bug

---

## 3. 今日待办

- [ ] 继续同步前复权剩余 18 批（明天额度重置后执行）
- [ ] 继续同步后复权前 15 批

---

## 4. 明天的计划（2026-05-10）

### 上午（额度重置后）
1. 执行 `sync_daily.py` 继续同步前复权剩余 18 批
   - 预计 25 分钟，消耗 ~540 万额度
2. 继续同步后复权前 15 批
   - 预计 20 分钟，消耗 ~450 万额度

### 下午
3. 检查 ClickHouse 数据完整性
4. 资产沃土接入方案实施（`jqdata_benchmark_manager.py`）

---

## 5. 后续规划

| 阶段 | 内容 | 预计时间 |
|------|------|---------|
| **数据同步** | 近 5 年 pre+post+index 全量 | 3 天内 |
| **资产沃土接入** | JQData 数据源适配器 | 数据全量后 |
| **A 服务器代理** | Nginx 反向代理 /jqdata/ | 接入时 |
| **正式版历史数据** | 2005-2019 年数据补全 | 长期 |

---

## 6. 快速命令

```bash
# 查看同步进度
cd /data/jqdata-platform/src && tail -f sync_*.log

# 查看 ClickHouse 数据量
ssh deploy@101.132.161.52 "docker exec jqdata-clickhouse clickhouse-client -q 'SELECT name,count() FROM system.tables WHERE database=\"jqdata\"'"

# 查看 JQData 额度
ssh deploy@101.132.161.52 "python3 -c 'import jqdatasdk as jq; jq.auth(\"18918601977\",\"Another123\"); print(jq.get_query_count())'"

# 一键启动同步
ssh deploy@101.132.161.52 "cd /data/jqdata-platform/src && nohup python3 sync_daily.py > sync.log 2>&1 &"
```

---

## 7. 注意事项

- ⚠️ D 服务器带宽 1Mbps，禁止在服务器上执行 `docker build` 或下载大文件
- ⚠️ 额度 1000 万/天，每天早上 8 点左右重置（以 JQData 后台为准）
- ⚠️ 932000.XSHG（中证2000）JQData 不支持，已从指数列表移除

---

*最后更新：2026-05-09 10:45*
