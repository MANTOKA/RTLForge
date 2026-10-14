"""RS KES milestone using the existing Planner/Generator/Debugger orchestration."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from verilog_agent.agents.debugger import RealDebugger
from verilog_agent.agents.generator import ArtifactRepository
from verilog_agent.agents.showcase import PlannerGeneratorShowcase
from verilog_agent.agents.testbench import RealTestbenchGenerator
from verilog_agent.benchmarks.rs_reference import GF, bm, decode, ribm, vectors
from verilog_agent.providers import OpenAIResponsesProvider, ProviderSettings
from verilog_agent.providers.usage import RecordingProvider
from verilog_agent.services import (
    DockerEDARunner,
    GoldenTestbenchVerificationService,
    StaticCandidateEvaluator,
)


def packed(values: list[int]) -> int:
    return sum(x << (10 * i) for i, x in enumerate(values))


def requirement(algorithm: str) -> str:
    common = f"""实现 RS(272,257) 的 {algorithm} KES 核心，顶层 rs_kes_{algorithm}。
本任务仅为完整译码器的中间里程碑，不包含 syndrome/Chien/Forney。
Verilog-2005。GF(2^10)，p(x)=x^10+x^3+1 (0x409)，alpha=2，t=7。
接口严格为：input clk,rst,start; input [139:0] syndromes;
output busy,done; output [79:0] locator; output [69:0] evaluator。
系数低次在低位；syndromes[10*i +:10]=S_(i+1)，i=0..13。
上升沿工作，高有效同步复位。空闲时 start=1 捕获全部输入；运算中忽略 start。
busy 从接收 start 后为1直到结果完成。完成时 done 脉冲一周期、busy=0，
locator/evaluator 同时有效，保持到下一次完成或复位。复位清零输出及状态。
输入可以在捕获后改变。最多512个周期完成，无零 syndrome 的特殊协议。
有限域加法是 XOR，乘法是模 p(x) 的无进位乘法。不得用整数乘除代替。
必须使用有界状态机；循环索引与位宽明确；避免锁存器、未驱动信号、多驱动。
"""
    if algorithm == "bm":
        return (
            common
            + """
算法按 Zhang 第4章 Algorithm 2。初始化 Lambda=1，B=x，L=0。
对 r=1..14：d=S_r XOR sum(i=1..L) Lambda_i*S_(r-i)。
若 d 非零：newLambda=oldLambda XOR d*B；若 2L<r：
newL=r-L，newB=oldLambda/d；否则 B 保持。每轮末 B 左移一个系数补零。
若 d=0，Lambda/L 保持，B仍左移。所有更新使用本轮旧状态。
内部多项式可用15项以免中间截断错误；最终 locator 为 Lambda_0..Lambda_7，
Lambda_0=1。evaluator 为 (Lambda*S) mod x^14 的最低7项，
其中 S(x)=S1+S2*x+...+S14*x^13。只验收0..7符号错误产生的 syndrome。
逆元可用 a^1022 的有界迭代 GF 运算。不得生成查表文件依赖。
"""
        )
    return (
        common
        + """
采用 Zhang 图4.4 RiBM统一阵列，22个元素的 D/T，索引0..21。
初始化 D=T={S1..S14,0,0,0,0,0,0,0,1}（此列表索引从0递增），g=1，k=0。
每轮 d=oldD[0]；令 shift[i]=oldD[i+1] (i<21)，shift[21]=0。
对所有 i 同步更新 newD[i]=g*shift[i] XOR d*oldT[i]。
若 d!=0 且 k>=0：newT=shift，newg=d，newk=-k-1；
否则 T/g 保持，newk=k+1。k是有符号整数，要能表示-15..15。
执行14轮，locator 系数0..7取最终D[7..14]，evaluator系数0..6取D[0..6]。
evaluator 是书中 Omega_hat，不是普通BM的Omega。不要归一化输出；
按上述确定迭代逐系数验收，包括全零 syndrome。KES不允许求逆。
"""
    )


def golden_tb(module: str, cases: list[dict]) -> str:
    calls = "\n".join(
        f"check_case(140'h{c['syndrome_hex']},80'h{c['locator_hex']},70'h{c['evaluator_hex']});"
        for c in cases
    )
    return f"""module tb;
reg clk=0; always #5 clk=~clk;
reg rst=1,start=0; reg [139:0] syndromes=0;
wire busy,done; wire [79:0] locator; wire [69:0] evaluator;
{module} dut(.clk(clk),.rst(rst),.start(start),.syndromes(syndromes),
 .busy(busy),.done(done),.locator(locator),.evaluator(evaluator));
integer count=0;
task check_case;
 input [139:0] s; input [79:0] l; input [69:0] e;
 integer cycles;
 begin
 @(negedge clk); start=1; syndromes=s;
 @(posedge clk); #1;
 cycles=0;
 while(done!==1'b1 && cycles<512) begin
   @(negedge clk); start=0; syndromes=~s;
   @(posedge clk); #1; cycles=cycles+1;
   if(done!==1'b1 && busy!==1'b1) $fatal(1,"busy protocol");
 end
 if(done!==1'b1 || busy!==1'b0) $fatal(1,"completion timeout/protocol");
 if(locator!==l || evaluator!==e) $fatal(1,"KES mismatch case %0d",count);
 @(negedge clk); start=0;
 @(posedge clk); #1;
 if(done!==1'b0 || locator!==l || evaluator!==e) $fatal(1,"result hold");
 count=count+1;
 end
endtask
initial begin
 repeat(3) @(negedge clk);
 if(busy!==0 || done!==0 || locator!==0 || evaluator!==0) $fatal(1,"reset");
 rst=0;
 {calls}
 $display("RS_GOLDEN_PASS cases=%0d",count); $finish;
end
initial begin #10000000; $fatal(1,"global timeout"); end
endmodule
"""


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class RSRunner(DockerEDARunner):
    """Require positive completion evidence, not just simulator exit code zero."""

    def run_files(self, action: str, input_paths: list[Path]) -> dict:
        result = super().run_files(action, input_paths)
        if action == "simulate" and result.get("status") == "PASS":
            marker = (
                "RS_GOLDEN_PASS cases=160"
                if input_paths[0].name == "golden_tb.sv"
                else "RS_AGENT_TB_PASS"
            )
            if marker not in result.get("stdout", ""):
                result = dict(
                    result,
                    status="FAIL",
                    category="ASSERTION_FAILURE",
                    stderr="Required RS completion marker missing",
                )
        return result


def prepare(root: Path, algorithm: str) -> list[dict]:
    root.mkdir(parents=True, exist_ok=True)
    cases = vectors()
    kes = []
    for case in cases:
        for algo in ("bm", "ribm"):
            if decode(case["received"], algo) != case["message"]:
                raise ValueError(f"software oracle failure: {case['id']} {algo}")
        locator, evaluator = (bm if algorithm == "bm" else ribm)(case["syndromes"][:14])
        kes.append(
            {
                "id": case["id"],
                "syndrome_hex": f"{packed(case['syndromes'][:14]):035x}",
                "locator_hex": f"{packed(locator):020x}",
                "evaluator_hex": f"{packed(evaluator):018x}",
            }
        )
    write_json(root / "vectors.json", cases)
    write_json(root / "kes_vectors.json", kes)
    (root / "requirement.txt").write_text(requirement(algorithm), encoding="utf-8")
    (root / "golden_tb.sv").write_text(golden_tb(f"rs_kes_{algorithm}", kes), encoding="utf-8")
    write_json(
        root / "manifest.json",
        {
            "stage": "KES_ONLY",
            "algorithm": algorithm,
            "seed": 20261014,
            "cases": len(cases),
            "software_roundtrips": 2 * len(cases),
            "field": {"bits": GF.bits, "polynomial": GF.polynomial},
            "rs": [272, 257],
            "first_root": 1,
            "rtl_verified": False,
            "hashes": {
                p.name: sha256(p.read_bytes()).hexdigest()
                for p in root.iterdir()
                if p.is_file() and p.name != "manifest.json"
            },
        },
    )
    return kes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", choices=("bm", "ribm"), required=True)
    parser.add_argument("--live", action="store_true", help="call configured model API")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--request-timeout", type=int, default=None)
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--reasoning-effort", choices=("none", "low", "high", "max"))
    parser.add_argument("--env-file", type=Path, help="explicit provider environment file")
    parser.add_argument("--reuse-testbench", type=Path, help="reuse a prior generated TB")
    parser.add_argument("--stage", choices=("rtl", "all"), default="all")
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    root = args.root or Path("runs") / (
        "rs_" + args.algorithm + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    root = root.resolve()
    if root.exists():
        raise SystemExit("Use a new output directory to preserve prior evidence")
    cases = prepare(root, args.algorithm)
    print(f"Prepared {len(cases)} cases at {root}", flush=True)
    if not args.live:
        return
    if args.env_file is None:
        load_dotenv(override=False)
    if args.stream:
        import os

        os.environ["VERILOG_AGENT_RESPONSES_STREAM"] = "1"
    if args.env_file is not None:
        import os

        if not args.env_file.is_file():
            raise SystemExit(f"Provider env file not found: {args.env_file}")
        environment = dict(os.environ)
        environment.update({k: v for k, v in dotenv_values(args.env_file).items() if v is not None})
        settings = ProviderSettings.from_env(environment)
    else:
        settings = ProviderSettings.from_env()
    print(
        f"Provider: model={settings.model} base_url={settings.base_url} "
        f"api_style={settings.resolved_api_style}",
        flush=True,
    )
    if args.request_timeout is not None:
        settings = replace(settings, timeout_seconds=args.request_timeout, max_retries=0)
    if args.max_output_tokens is not None:
        settings = replace(settings, max_output_tokens=args.max_output_tokens)
    if args.reasoning_effort is not None:
        settings = replace(settings, reasoning_effort=args.reasoning_effort)
    provider = OpenAIResponsesProvider(settings)
    runner = RSRunner(runs_root=root, timeout_seconds=120)
    try:
        showcase = PlannerGeneratorShowcase(
            provider=provider,
            provider_label="rs_live",
            database=root / "tasks.sqlite3",
            runs_root=root,
            evaluator=StaticCandidateEvaluator(runner, root),
            verifier=GoldenTestbenchVerificationService(
                runner,
                root,
                lambda _: root / "golden_tb.sv",
                testbench_source="rs_software_oracle",
                confidence="MEDIUM",
            ),
            debugger=RealDebugger(provider, artifacts=ArtifactRepository(root)),
            emit=lambda text: print(text, flush=True) if text.startswith("阶段") else None,
        )
        result = showcase.run(requirement(args.algorithm), candidate_count=1, max_repairs=1)
        write_json(root / "run_result.json", result)
        selected = next(
            (
                c
                for c in result["candidates"]
                if c["candidate_id"] == result["selected_candidate_id"]
            ),
            None,
        )
        if args.stage == "all":
            # The TB role runs after RTL verification and never receives the RTL source.
            if args.reuse_testbench:
                tb = args.reuse_testbench.read_text(encoding="utf-8")
                write_json(root / "tb_reuse.json", {
                    "source": str(args.reuse_testbench.resolve()),
                    "sha256": sha256(tb.encode()).hexdigest(),
                })
            else:
                tb_provider = RecordingProvider(provider, role="testbench_generator")
                try:
                    tb = RealTestbenchGenerator(tb_provider).generate(
                        requirement(args.algorithm), cases[::20]
                    )
                finally:
                    write_json(root / "tb_model_calls.json", tb_provider.calls)
            (root / "agent_tb.sv").write_text(tb, encoding="utf-8")
            if selected:
                outcome = runner.run_files(
                    "simulate", [root / "agent_tb.sv", Path(selected["artifact_path"])]
                )
                write_json(root / "agent_tb_result.json", outcome)
        print(f"Run saved at {root}", flush=True)
    except Exception as exc:
        # Provider errors may contain endpoint/request details; store only safe classification.
        cause = exc.__cause__
        write_json(
            root / "failure.json",
            {
                "type": type(exc).__name__,
                "completed": False,
                "cause_type": type(cause).__name__ if cause else None,
                "http_status": getattr(cause, "status_code", None),
            },
        )
        raise SystemExit(
            f"RS run failed: {type(exc).__name__}; evidence retained at {root}"
        ) from None


if __name__ == "__main__":
    main()
