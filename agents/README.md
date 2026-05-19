# Agent Boundary

Agents interact with `agent_api_wrapper.py`. The wrapper sends the agent-visible
task view, the current observation, the allowed action schema, and previous
redacted feedback. It never exposes runtime setup, stimulus schedule, oracle
labels, private traces, output paths, or raw runtime handles.
