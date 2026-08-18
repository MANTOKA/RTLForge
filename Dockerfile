FROM ubuntu:22.04

# 设置环境变量，避免交互式弹窗打断构建
ENV DEBIAN_FRONTEND=noninteractive

# 使用国内Ubuntu镜像源
RUN sed -i \
    -e 's/archive.ubuntu.com/mirrors.aliyun.com/g' \
    -e 's/security.ubuntu.com/mirrors.aliyun.com/g' \
    /etc/apt/sources.list

# 安装基础依赖与硬件 EDA 工具
RUN apt-get update -o Acquire::Retries=5 && apt-get install -y \
    iverilog \
    verilator \
    python3 \
    python3-pip \
    build-essential \
    flex \
    bison \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /workspace


CMD ["python3", "-u", "/workspace/tool_wrapper.py"]