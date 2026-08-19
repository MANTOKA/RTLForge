FROM ubuntu:22.04

# 设置环境变量，避免交互式弹窗打断构建
ENV DEBIAN_FRONTEND=noninteractive

# Install the deterministic EDA toolchain used by the sandbox.
RUN apt-get update -o Acquire::Retries=5 && apt-get install -y --no-install-recommends \
    iverilog \
    verilator \
    yosys \
    python3 \
    perl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# A fixed unprivileged identity prevents generated RTL from running as root.
RUN groupadd --gid 10001 eda \
    && useradd --uid 10001 --gid eda --no-create-home --shell /usr/sbin/nologin eda \
    && mkdir -p /work /opt/verilog-agent \
    && chown -R eda:eda /work /opt/verilog-agent

COPY --chown=eda:eda tool_wrapper.py /opt/verilog-agent/tool_wrapper.py

ENV TMPDIR=/work \
    VERILOG_AGENT_INPUT_ROOT=/inputs

WORKDIR /work
USER 10001:10001

CMD ["python3", "-u", "/opt/verilog-agent/tool_wrapper.py"]
