# D服务器：API+同步 统一镜像
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn==0.30.0 \
    clickhouse-driver==0.2.9 \
    redis==5.0.0 \
    pandas==2.2.0 \
    jqdatasdk==1.9.6

# 代码通过volume挂载，镜像只打包依赖
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
