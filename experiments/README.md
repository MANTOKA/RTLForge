# Frozen experiment records

This directory contains small, Git-tracked experiment manifests. A manifest fixes the identity,
configuration, evidence hashes, result summary, and known limitations of one completed run.

Raw `runs/` artifacts remain ignored: they can contain generated RTL, benchmark references,
golden-testbench artifacts, and large SQLite databases. The manifest links to their expected local
paths and hashes the report files, so the checked-in record is reviewable without publishing raw
benchmark material.

Do not overwrite a frozen record. A new model, prompt, scoring setting, Memory mode, candidate
budget, repair budget, toolchain, or dataset snapshot requires a new experiment directory.
