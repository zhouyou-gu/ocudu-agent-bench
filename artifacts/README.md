# Artifact Layout

Artifacts are recorded through `trace.py` after the live interaction path closes.
The artifact manifest is finalized before destructive runtime cleanup and before
post-run scoring.

Visibility classes:

- `interaction`: observations, actions, and redacted feedback.
- `benchmark_private`: readiness, stimulus, runtime setup, and private paths.
- `oracle`: scorer-only evidence.
