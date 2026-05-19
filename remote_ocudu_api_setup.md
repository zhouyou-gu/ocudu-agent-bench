# Remote OCUDU API Setup

Runtime setup is infrastructure. It prepares OCUDU, Open5GS, srsUE, ZMQ, and
FlexRIC assets so `runtime_setup.py` can instantiate a task runtime. It does not
own task scoring, stimulus scheduling, or agent-visible API projection.

Required readiness classes:

- OCUDU WebSocket command path.
- OCUDU JSON metrics path.
- Docker/ZMQ launch assets.
- FlexRIC and E2SM-KPM assets for E2 observation tasks.
- FlexRIC control xApp assets for E2 control tasks.
