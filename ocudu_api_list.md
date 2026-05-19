# OCUDU API List

| Runtime API | Benchmark action or source | Status |
| --- | --- | --- |
| `rrm_policy_ratio_set` | `SET_PRB_POLICY_RATIO_WS` | implemented catalog binding |
| `ssb_set` | `SET_SSB_BLOCK_POWER_WS` | implemented catalog binding |
| `metrics_subscribe` | `json_metrics` observation | implemented catalog binding |
| E2SM-KPM v05 subscription | `e2_kpm_v05` observation | implemented catalog binding |
| E2SM-CCC O-RRMPolicyRatio | `SET_PRB_POLICY_RATIO_CCC` | implemented gated binding |
| E2SM-RC DU style 2 action 6 | `SET_PRB_POLICY_RATIO_RC_DU` | implemented gated binding |

Future OCUDU APIs should be added first to the static API catalog, then exposed
through task-selected projections only.
