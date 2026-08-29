ARG PYTHON_IMAGE=python:3.12-slim
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
ARG GAP_VERSION=
FROM ${PYTHON_IMAGE}
ARG PIP_INDEX
ARG GAP_VERSION
ENV GAP_VERSION=${GAP_VERSION}

WORKDIR /app
COPY apps/api/requirements.txt ./requirements.txt
# 使用预编译 wheel，避免 apt 安装 gcc（国内可设 PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple）
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt
COPY apps/api/ .
COPY deploy/myclaw-runnable.Dockerfile /app/deploy/myclaw-runnable.Dockerfile
COPY deploy/gap.version /app/deploy/gap.version
RUN mkdir -p /app/data /app/deploy
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "logging_config.json"]
