FROM ubuntu:22.04

WORKDIR /work

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
    && mkdir -p /work /opt/rtlforge \
    && chown -R eda:eda /work /opt/rtlforge

COPY --chown=eda:eda tool_wrapper.py /opt/rtlforge/tool_wrapper.py
COPY --chown=eda:eda config/static_score_v1.json /opt/rtlforge/static_score_v1.json

ENV TMPDIR=/work \
    RTLFORGE_INPUT_ROOT=/inputs \
    RTLFORGE_SCORE_CONFIG=/opt/rtlforge/static_score_v1.json

USER 10001:10001

CMD ["python3", "-u", "/opt/rtlforge/tool_wrapper.py"]
