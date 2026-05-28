FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装Python依赖
RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn==0.30.0 \
    clickhouse-driver==0.2.9 \
    redis==5.0.0 \
    pandas==2.2.0 \
    jqdatasdk==1.9.6

# 复制应用代码 & 同步脚本
COPY src/main.py /app/
COPY src/sync_base.py /app/src/
COPY src/sync_daily.py /app/src/
COPY src/sync_etf.py /app/src/
COPY src/sync_extended.py /app/src/
COPY src/sync_fundamentals.py /app/src/
COPY src/sync_index_weights.py /app/src/
COPY src/sync_stk_xr_xd.py /app/src/
COPY src/notify.py /app/src/

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"

# 启动API服务（带proxy-headers，使Nginx反向代理后能看到真实客户端IP）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
