## Outcome

Describe the completed result and the issue it addresses.

Closes #

## Changes

- 

## Validation

- [ ] Ruff passes: `ruff check .`
- [ ] Unit tests pass: `pytest`
- [ ] Relevant Docker integration tests pass.
- [ ] Failure paths and timeouts were tested where applicable.

## Safety and repository hygiene

- [ ] No secrets, generated RTL, logs, waveforms, or build products are committed.
- [ ] Icarus/Verilator/Yosys retain sandbox limits; optional Vivado uses executor-generated Tcl.
- [ ] Documentation and default configuration were updated when behavior changed.
- [ ] The change is scoped and can be rolled back independently.
