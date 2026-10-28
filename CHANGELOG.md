# Changelog

## 0.3.0 — 2026-10-28

Step-by-step dates, before/after behavior and evidence windows are preserved in
[the development history](docs/CHANGE_HISTORY_ZH.md).

Research preview of the complete RTL generation-to-synthesis engineering loop.

- Rename product/package to RTLForge; add the Windows console launcher source.
- Add clarified clock/reset/handshake contracts and bounded Planner validation retry.
- Prioritize remaining repairs after repair-introduced compile errors; preserve evidence.
- Continue an existing candidate with additional repair budget, without re-planning.
- Prompt on provider output truncation and retry with a user-selected larger limit.
- Store five typed Memory categories with scoped Debugger retrieval.
- Integrate official AMD Vivado MCP, source-bound synthesis constraints/results and repair feedback.
- Add generic module reuse, frozen contracts/sources, transitive dependencies and top-only generation.
- Update the handbook, MIT license, public examples, clean-checkout tests and release boundaries.

The local full BM decoder passed 160 independent frames and real Vivado OOC synthesis:
7,825 LUT, 4,586 FF, WNS +2.475 ns at 10 ns. Private task artifacts/RTL are not bundled.
This is one engineering case, not a benchmark-wide effectiveness or performance claim.

Not delivered: Compose deployment, asynchronous consolidation, external-document RAG,
dedicated waveform-summary tool, or controlled scoring/Memory effectiveness ablations.
