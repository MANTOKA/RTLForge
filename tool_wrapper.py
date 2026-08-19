#!/usr/bin/env python3

import json
import re
import subprocess
import sys

# ===============================
# Error Parser (错误解析器)
# 作用：将 EDA 工具输出的杂乱纯文本报错，提炼为结构化字段，供后续的 Repair Agent 精准定位修改行。
# ===============================

# 匹配 Icarus Verilog 的报错格式，例如： "test.v:10: error: syntax error"
IVERILOG_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):\s*"        # 提取文件名 (file) 和 行号 (line)
    r"(?:(?P<severity>error|warning|Warning):\s*)?"  # 提取严重级别 (severity)
    r"(?P<msg>.*)"  ,                            # 提取具体的报错信息 (msg)
    re.IGNORECASE
)

# 匹配 Verilator 的报错格式，例如： "%Warning-WIDTH: test.v:45:12: Operator ASSIGNW..."
VERILATOR_RE = re.compile(
    r"^%(?P<severity>Error|Warning)"            # 提取严重级别，注意前导的 '%'
    r"(?:-(?P<code>[A-Z0-9_]+))?:\s*"           # 提取特定的错误代码 (code)，如 WIDTH, LATCH (选填)
    r"(?P<file>[^:]+):"                         # 提取文件名 (file)
    r"(?P<line>\d+):"                           # 提取行号 (line)
    r"(?:(?P<col>\d+):)?\s*"                    # 提取列号 (col) (选填，有的报错没有列号)
    r"(?P<msg>.*)"                              # 提取具体信息
)


def parse_iverilog(stderr):
    """解析 Icarus Verilog 的标准错误流，返回字典列表"""
    errors=[]
    for line in stderr.splitlines():    
        m=IVERILOG_RE.match(line)
        if m:
            data=m.groupdict()
            
            message = data["msg"].strip()

            # Icarus 某些版本不会显式输出 "error:"
            # 例如：
            # counter_bad.v:7: syntax error
            severity = data["severity"]

            if severity:
                severity = severity.upper()
            else:
                severity = "ERROR"
            
            errors.append({
                "tool":"iverilog",
                "type":severity,
                "file":data["file"],
                "line":int(data["line"]),
                "message":message
            })
    return errors


def parse_verilator(stderr):
    """解析 Verilator 的标准错误流，返回字典列表"""
    errors=[]
    for line in stderr.splitlines():
        m=VERILATOR_RE.match(line)
        if m:
            data=m.groupdict()
            errors.append({
                "tool":"verilator",
                # 如果有专门的代码(如 LATCH)，则用代码，否则用级别(如 ERROR)
                "type":data["code"] or data["severity"], 
                "file":data["file"],
                "line":int(data["line"]),
                "message":data["msg"]
            })
    return errors


def parse_error(tool, stderr):
    """解析分发器：根据调用的工具自动选择正确的正则匹配逻辑"""
    if tool=="iverilog":
        return parse_iverilog(stderr)
    elif tool=="verilator":
        return parse_verilator(stderr)
    return []


# ===============================
# Tool Execution (底层工具执行器)
# 作用：安全地拉起 Linux 进程，并捕获输出，防止 EDA 工具崩溃导致整个 Python 脚本卡死。
# ===============================

def run_command(cmd):
    """通用的子进程调用函数"""
    try:
        # capture_output=True 确保拿到 stdout 和 stderr 供后续正则分析
        # text=True 直接返回字符串，无需手动 decode bytes
        result=subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        return {
            "returncode":result.returncode,
            "stdout":result.stdout,
            "stderr":result.stderr
        }
    except Exception as e:
        # 捕获系统级异常（例如工具未安装、路径错误）
        return {
            "returncode":-1,
            "stdout":"",
            "stderr":str(e)
        }


# ===============================
# Compile (编译阶段)
# ===============================

def compile_verilog(files):
    """仅进行语法编译，不生成仿真波形，速度快"""
    cmd=[
        "iverilog",
        "-g2012" # 支持 SystemVerilog 的部分语法规范，这对大模型生成的现代代码很关键
    ]
    cmd.extend(files)

    result=run_command(cmd)
    errors=parse_error("iverilog", result["stderr"])

    return {
        "stage":"COMPILE",
        "status":"PASS" if result["returncode"]==0 else "FAIL",
        "errors":errors,
        "stdout":result["stdout"],
        "stderr":result["stderr"] # 保留原始报错供备用
    }


# ===============================
# Lint (静态检查阶段)
# ===============================

def lint_verilog(files):
    """进行代码规范检查，不产生执行文件"""
    cmd=[
        "verilator",
        "--lint-only", # 仅检查，不生成 C++ 仿真模型，极快
        "-Wall"        # 打开所有警告（捕捉 Latch、未连线等极佳选择）
    ]
    cmd.extend(files)

    result=run_command(cmd)
    errors=parse_error("verilator", result["stderr"])

    return {
        "stage":"LINT",
        "status":"PASS" if result["returncode"]==0 else "FAIL",
        "errors":errors,
        "stdout":result["stdout"],
        "stderr":result["stderr"]
    }


# ===============================
# Simulation (仿真阶段)
# ===============================

def simulate(files):
    """执行行为级仿真：先编译为 sim.out，再使用 vvp 运行"""
    # 1. 编译阶段 (生成二进制文件)
    compile_cmd=[
        "iverilog",
        "-g2012",
        "-o",
        "sim.out"
    ]
    compile_cmd.extend(files)
    compile_result=run_command(compile_cmd)

    # 如果编译都过不了，直接阻断，不进入仿真
    if compile_result["returncode"]!=0:
        return {
            "stage":"SIMULATION",
            "status":"FAIL",
            "errors": parse_error("iverilog", compile_result["stderr"])
        }

    # 2. 运行仿真阶段
    sim_result=run_command([
        "vvp",
        "sim.out"
    ])

    return {
        "stage":"SIMULATION",
        "status":"PASS" if sim_result["returncode"]==0 else "FAIL",
        # 注意：这里目前没有针对 Testbench 断言失败 (Assertion Fail) 做错误解析
        "stdout": sim_result["stdout"],
        "stderr": sim_result["stderr"]
    }


# ===============================
# Main Interface (主接口/Agent 入口)
# 作用：接收 Docker 外部传入的命令，并将最终结果 JSON 化打印到标准输出。
# ===============================

def main():
    if len(sys.argv)<3:
        # Agent 传参错误时的容错处理
        print(json.dumps({
            "status":"FAIL",
            "message":"Usage: tool_wrapper.py [compile|lint|simulate] file.v"
        }))
        return

    mode=sys.argv[1]
    files=sys.argv[2:]

    # 路由分发
    if mode=="compile":
        result=compile_verilog(files)
    elif mode=="lint":
        result=lint_verilog(files)
    elif mode=="simulate":
        result=simulate(files)
    else:
        result={
            "status":"FAIL",
            "message":"Unknown mode"
        }

    # 强制以标准 JSON 格式输出，确保 Agent 侧可以无脑 json.loads()
    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        )
    )

if __name__=="__main__":
    main()
