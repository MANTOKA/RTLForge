# Third-party materials

RTLForge's original code is distributed under the root MIT license. Dependencies,
EDA programs, model services and benchmark datasets retain their own terms.

- Icarus Verilog, Verilator and Yosys are installed by the Dockerfile through Ubuntu
  packages. They are separate executables, not relicensed as RTLForge code.
- Vivado and AMD Ross Vivado MCP Server are optional, separately obtained products.
  No AMD installer, server executable, license file or proprietary IP is distributed here.
- OpenAI-compatible providers require the user's own account and API credentials.
- VerilogEval and RTLLM datasets are downloaded separately and excluded from Git.
  Small `human_v2_prob*` regression fixtures are local reproductions/adaptations of
  task interfaces and observed failures; upstream notices are retained in
  [LICENSES/verilog-eval.txt](LICENSES/verilog-eval.txt).
- VerilogCoder, MAGE, RTLFixer and agent-memory papers are design references, not
  bundled implementations. Parent-directory research checkouts and PDFs are excluded.

Upstream sources: [VerilogEval](https://github.com/NVlabs/verilog-eval),
[Icarus](https://github.com/steveicarus/iverilog),
[Verilator](https://github.com/verilator/verilator), [Yosys](https://github.com/YosysHQ/yosys).
