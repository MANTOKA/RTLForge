from __future__ import annotations

OFFLINE_COUNTER_SPEC = (
    "设计一个4位同步加法计数器模块 counter，输入 clk 和高有效同步复位 rst，"
    "输出 q[3:0]。rst=1 时在下一个时钟上升沿将 q 清零，否则 q 每个上升沿加1，"
    "溢出后回绕到0。"
)


def counter_plan_payload() -> dict:
    return {
        "module_name": "counter",
        "ports": [
            {"name": "clk", "direction": "input", "width": 1},
            {"name": "rst", "direction": "input", "width": 1},
            {"name": "q", "direction": "output", "width": 4},
        ],
        "clock": "clk",
        "reset": "rst",
        "assumptions": ["rst 为同步高有效复位"],
        "acceptance_criteria": [
            "rst=1 时，q 在时钟上升沿变为0",
            "rst=0 时，q 在每个时钟上升沿按模16加1",
        ],
        "task_tree_root": {
            "node_id": "root",
            "title": "实现并准备验证 counter",
            "description": "构建可综合的4位同步计数器",
            "acceptance_criteria": ["接口与行为符合 SpecIR"],
            "children": [
                {
                    "node_id": "rtl",
                    "title": "生成 counter RTL",
                    "description": "实现复位、递增和溢出回绕行为",
                    "acceptance_criteria": ["模块是可综合的 Verilog-2005"],
                },
                {
                    "node_id": "verify",
                    "title": "验证计数器行为",
                    "description": "检查复位、递增和溢出回绕场景",
                    "acceptance_criteria": ["全部指定的时序行为通过"],
                },
            ],
        },
    }


def counter_candidate_payload() -> dict:
    return {
        "candidates": [
            {
                "rtl": (
                    "module counter(\n"
                    "    input  wire       clk,\n"
                    "    input  wire       rst,\n"
                    "    output reg  [3:0] q\n"
                    ");\n"
                    "always @(posedge clk) begin\n"
                    "    if (rst)\n"
                    "        q <= 4'b0000;\n"
                    "    else\n"
                    "        q <= q + 4'b0001;\n"
                    "end\n"
                    "endmodule"
                ),
                "design_pattern": "单时序进程",
                "assumptions": ["4位加法自然按模16回绕"],
            },
            {
                "rtl": (
                    "module counter(\n"
                    "    input  wire       clk,\n"
                    "    input  wire       rst,\n"
                    "    output reg  [3:0] q\n"
                    ");\n"
                    "wire [3:0] next_q;\n"
                    "assign next_q = q + 4'b0001;\n"
                    "always @(posedge clk) begin\n"
                    "    if (rst)\n"
                    "        q <= 4'b0000;\n"
                    "    else\n"
                    "        q <= next_q;\n"
                    "end\n"
                    "endmodule"
                ),
                "design_pattern": "独立的下一值数据通路",
                "assumptions": ["4位加法自然按模16回绕"],
            },
        ]
    }
