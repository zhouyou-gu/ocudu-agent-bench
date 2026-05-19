# Benchmark API Reference

This file records the RAN API cases currently represented by the executable
catalog.

| API kind | Agent action/source | Runtime binding |
| --- | --- | --- |
| `ocudu_websocket_prb_policy` | `SET_PRB_POLICY_RATIO_WS` | OCUDU WebSocket `rrm_policy_ratio_set` |
| `ocudu_websocket_ssb_power` | `SET_SSB_BLOCK_POWER_WS` | OCUDU WebSocket `ssb_set` |
| `ocudu_json_metrics` | `json_metrics` observation | OCUDU JSON metrics subscription |
| `e2sm_kpm_v05_observation` | `e2_kpm_v05` observation | OCUDU + FlexRIC E2SM-KPM v05 |
| `e2sm_ccc_prb_policy_control` | `SET_PRB_POLICY_RATIO_CCC` | FlexRIC E2SM-CCC control |
| `e2sm_rc_du_prb_quota_control` | `SET_PRB_POLICY_RATIO_RC_DU` | FlexRIC E2SM-RC DU control |

`NO_ACTION` is a benchmark decision. It is recordable and scorable but is never
sent to OCUDU.
