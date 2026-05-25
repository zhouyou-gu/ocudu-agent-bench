"""Static RAN API catalog and task projection logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from benchmark_api.types import (
    ApiCompatibilityStatus,
    RanActionType,
    RanApiBackend,
    RanApiKind,
    RanApiRole,
    RanObservationSource,
    SafeErrorClass,
)


@dataclass(frozen=True)
class ApiDescriptor:
    api_id: str
    kind: RanApiKind
    roles: tuple[RanApiRole, ...]
    backend: RanApiBackend
    status: ApiCompatibilityStatus
    description: str
    action_type: RanActionType | None = None
    observation_source: RanObservationSource | None = None
    wire_command: str | None = None
    timeout_s: float = 5.0
    safe_error_classes: tuple[SafeErrorClass, ...] = (
        SafeErrorClass.SCHEMA_ERROR,
        SafeErrorClass.PERMISSION_ERROR,
        SafeErrorClass.TIMEOUT,
        SafeErrorClass.RUNTIME_REJECTED,
        SafeErrorClass.RUNTIME_UNAVAILABLE,
        SafeErrorClass.INTERNAL_REDACTED,
    )
    runtime_requirements: tuple[str, ...] = ()
    request_fields: tuple[str, ...] = ()
    response_fields: tuple[str, ...] = ()
    agent_visible: bool = True
    oracle_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def private_dict(self) -> dict[str, Any]:
        return {
            "api_id": self.api_id,
            "kind": self.kind.value,
            "roles": [role.value for role in self.roles],
            "backend": self.backend.value,
            "status": self.status.value,
            "description": self.description,
            "action_type": self.action_type.value if self.action_type else None,
            "observation_source": self.observation_source.value if self.observation_source else None,
            "wire_command": self.wire_command,
            "timeout_s": self.timeout_s,
            "safe_error_classes": [err.value for err in self.safe_error_classes],
            "runtime_requirements": list(self.runtime_requirements),
            "request_fields": list(self.request_fields),
            "response_fields": list(self.response_fields),
            "agent_visible": self.agent_visible,
            "oracle_only": self.oracle_only,
            "metadata": dict(self.metadata),
        }

    def agent_dict(self) -> dict[str, Any]:
        if not self.agent_visible or self.oracle_only:
            raise ValueError(f"API {self.api_id} is not agent-visible")
        data: dict[str, Any] = {
            "api_id": self.api_id,
            "kind": self.kind.value,
            "roles": [role.value for role in self.roles if role != RanApiRole.ORACLE_EVIDENCE],
            "backend": self.backend.value,
            "description": self.description,
            "timeout_s": self.timeout_s,
            "safe_error_classes": [err.value for err in self.safe_error_classes],
            "request_fields": list(self.request_fields),
            "response_fields": list(self.response_fields),
        }
        if self.action_type is not None:
            data["action_type"] = self.action_type.value
        if self.observation_source is not None:
            data["observation_source"] = self.observation_source.value
        return data


DEFAULT_API_DESCRIPTORS: tuple[ApiDescriptor, ...] = (
    ApiDescriptor(
        api_id="ws_prb_policy",
        kind=RanApiKind.OCUDU_WEBSOCKET_PRB_POLICY,
        roles=(RanApiRole.ACTION, RanApiRole.FEEDBACK),
        backend=RanApiBackend.WEBSOCKET,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Set OCUDU PRB policy ratios through the benchmark-mediated WebSocket path.",
        action_type=RanActionType.SET_PRB_POLICY_RATIO_WS,
        wire_command="rrm_policy_ratio_set",
        runtime_requirements=("ocudu_websocket",),
        request_fields=("min_prb_policy_ratio", "max_prb_policy_ratio", "plmn", "sst", "sd", "dedicated_ratio"),
        response_fields=("accepted", "safe_message"),
    ),
    ApiDescriptor(
        api_id="ws_ssb_power",
        kind=RanApiKind.OCUDU_WEBSOCKET_SSB_POWER,
        roles=(RanApiRole.ACTION, RanApiRole.FEEDBACK),
        backend=RanApiBackend.WEBSOCKET,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Set OCUDU SSB block power through the benchmark-mediated WebSocket path.",
        action_type=RanActionType.SET_SSB_BLOCK_POWER_WS,
        wire_command="ssb_set",
        runtime_requirements=("ocudu_websocket",),
        request_fields=("nci", "ssb_block_power_dbm", "plmn"),
        response_fields=("accepted", "safe_message"),
    ),
    ApiDescriptor(
        api_id="cli_handover",
        kind=RanApiKind.OCUDU_CLI_HANDOVER,
        roles=(RanApiRole.ACTION, RanApiRole.FEEDBACK),
        backend=RanApiBackend.OCUDU_CLI,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Trigger an OCUDU CLI handover through the benchmark-mediated command path.",
        action_type=RanActionType.TRIGGER_HANDOVER_CLI,
        wire_command="ho",
        runtime_requirements=("ocudu_cli",),
        request_fields=("serving_pci", "rnti", "target_pci"),
        response_fields=("accepted", "safe_message"),
    ),
    ApiDescriptor(
        api_id="cli_conditional_handover",
        kind=RanApiKind.OCUDU_CLI_CONDITIONAL_HANDOVER,
        roles=(RanApiRole.ACTION, RanApiRole.FEEDBACK),
        backend=RanApiBackend.OCUDU_CLI,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Trigger an OCUDU CLI conditional handover through the benchmark-mediated command path.",
        action_type=RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI,
        wire_command="cho",
        runtime_requirements=("ocudu_cli",),
        request_fields=("serving_pci", "rnti", "target_pcis", "timeout_s"),
        response_fields=("accepted", "safe_message"),
    ),
    ApiDescriptor(
        api_id="cli_cfo_control",
        kind=RanApiKind.OCUDU_CLI_CFO_CONTROL,
        roles=(RanApiRole.ACTION, RanApiRole.FEEDBACK),
        backend=RanApiBackend.OCUDU_CLI,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Set OCUDU CLI carrier frequency offset compensation through the benchmark-mediated command path.",
        action_type=RanActionType.SET_CFO_CLI,
        wire_command="cfo",
        runtime_requirements=("ocudu_cli",),
        request_fields=("sector_id", "cfo_hz"),
        response_fields=("accepted", "safe_message"),
    ),
    ApiDescriptor(
        api_id="cli_tx_time_offset_control",
        kind=RanApiKind.OCUDU_CLI_TX_TIME_OFFSET_CONTROL,
        roles=(RanApiRole.ACTION, RanApiRole.FEEDBACK),
        backend=RanApiBackend.OCUDU_CLI,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Set OCUDU CLI transmit time offset through the benchmark-mediated command path.",
        action_type=RanActionType.SET_TX_TIME_OFFSET_CLI,
        wire_command="tx_time_offset",
        runtime_requirements=("ocudu_cli",),
        request_fields=("sector_id", "tx_time_offset_us"),
        response_fields=("accepted", "safe_message"),
    ),
    ApiDescriptor(
        api_id="core_nf_lifecycle",
        kind=RanApiKind.CORE_NF_LIFECYCLE_CONTROL,
        roles=(RanApiRole.ACTION, RanApiRole.FEEDBACK),
        backend=RanApiBackend.CORE_CONTROL,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Restart a benchmark-owned Open5GS core network function or monolithic core service.",
        action_type=RanActionType.RESTART_CORE_NF,
        wire_command="benchmark_core_nf_restart",
        runtime_requirements=("open5gs",),
        request_fields=("nf",),
        response_fields=("accepted", "safe_message"),
    ),
    ApiDescriptor(
        api_id="core_ue_registration",
        kind=RanApiKind.CORE_UE_REGISTRATION_CONTROL,
        roles=(RanApiRole.ACTION, RanApiRole.FEEDBACK),
        backend=RanApiBackend.CORE_CONTROL,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Update a benchmark-owned Open5GS UE subscriber registration using a redacted auth profile identifier.",
        action_type=RanActionType.UPDATE_CORE_UE_REGISTRATION,
        wire_command="benchmark_core_ue_registration_update",
        runtime_requirements=("open5gs",),
        request_fields=("ue_id", "supi", "plmn", "dnn", "sst", "sd", "auth_profile_id"),
        response_fields=("accepted", "safe_message"),
        metadata={"secret_handling": "subscriber keys are resolved by the benchmark backend from auth_profile_id"},
    ),
    ApiDescriptor(
        api_id="json_metrics",
        kind=RanApiKind.OCUDU_JSON_METRICS,
        roles=(RanApiRole.EVIDENCE,),
        backend=RanApiBackend.JSON_METRICS,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Read redacted OCUDU JSON metrics freshness and continuity evidence.",
        observation_source=RanObservationSource.JSON_METRICS,
        wire_command="metrics_subscribe",
        runtime_requirements=("ocudu_websocket",),
        response_fields=("present", "stale", "sample_count", "parse_errors"),
    ),
    ApiDescriptor(
        api_id="e2_kpm_v05",
        kind=RanApiKind.E2SM_KPM_V05_OBSERVATION,
        roles=(RanApiRole.EVIDENCE, RanApiRole.ORACLE_EVIDENCE),
        backend=RanApiBackend.E2_KPM,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Read decoded FlexRIC E2SM-KPM v05 availability and PRB evidence.",
        observation_source=RanObservationSource.E2_KPM_V05,
        runtime_requirements=("flexric", "e2_kpm_decoder"),
        response_fields=("enabled", "kpm_indications", "has_prb_measurement"),
    ),
    ApiDescriptor(
        api_id="e2_ccc_prb_policy",
        kind=RanApiKind.E2SM_CCC_PRB_POLICY_CONTROL,
        roles=(RanApiRole.ACTION, RanApiRole.ORACLE_EVIDENCE),
        backend=RanApiBackend.E2_CONTROL,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Set E2SM-CCC O-RRMPolicyRatio through a benchmark-owned FlexRIC control xApp.",
        action_type=RanActionType.SET_PRB_POLICY_RATIO_CCC,
        wire_command="E2SM-CCC O-RRMPolicyRatio",
        runtime_requirements=("flexric", "e2_control_xapp"),
        request_fields=("min_prb_policy_ratio", "max_prb_policy_ratio", "plmn", "sst", "sd", "dedicated_ratio"),
        response_fields=("accepted", "safe_message"),
    ),
    ApiDescriptor(
        api_id="e2_rc_du_prb_quota",
        kind=RanApiKind.E2SM_RC_DU_PRB_QUOTA_CONTROL,
        roles=(RanApiRole.ACTION, RanApiRole.ORACLE_EVIDENCE),
        backend=RanApiBackend.E2_CONTROL,
        status=ApiCompatibilityStatus.COMPATIBLE,
        description="Set E2SM-RC DU style 2 action 6 PRB quota through FlexRIC.",
        action_type=RanActionType.SET_PRB_POLICY_RATIO_RC_DU,
        wire_command="E2SM-RC DU style 2 action 6",
        runtime_requirements=("flexric", "e2_control_xapp", "du_ue_identity"),
        request_fields=("min_prb_policy_ratio", "max_prb_policy_ratio", "plmn", "sst", "sd", "dedicated_ratio", "du_ue_id"),
        response_fields=("accepted", "safe_message"),
    ),
)


def load_api_catalog() -> dict[RanApiKind, ApiDescriptor]:
    return {descriptor.kind: descriptor for descriptor in DEFAULT_API_DESCRIPTORS}


def normalize_api_kind(value: str | RanApiKind) -> RanApiKind:
    return value if isinstance(value, RanApiKind) else RanApiKind(value)


def validate_api_selection(
    selected_kinds: list[str] | tuple[str, ...] | tuple[RanApiKind, ...],
    catalog: dict[RanApiKind, ApiDescriptor] | None = None,
) -> tuple[ApiDescriptor, ...]:
    catalog = catalog or load_api_catalog()
    descriptors: list[ApiDescriptor] = []
    for raw_kind in selected_kinds:
        kind = normalize_api_kind(raw_kind)
        if kind not in catalog:
            raise ValueError(f"Task selected unknown RAN API kind: {kind.value}")
        descriptor = catalog[kind]
        if descriptor.status != ApiCompatibilityStatus.COMPATIBLE:
            raise ValueError(f"Task selected incompatible RAN API kind: {kind.value}")
        descriptors.append(descriptor)
    return tuple(descriptors)


def build_api_projection(selected_kinds: list[str] | tuple[str, ...]) -> dict[str, Any]:
    descriptors = validate_api_selection(selected_kinds)
    projection = {
        "evidence_apis": [],
        "action_apis": [],
        "feedback_apis": [],
        "action_types": [],
        "observation_sources": [],
        "safe_error_classes": sorted({err.value for desc in descriptors for err in desc.safe_error_classes}),
    }
    for descriptor in descriptors:
        if not descriptor.agent_visible or descriptor.oracle_only:
            continue
        agent_view = descriptor.agent_dict()
        roles = set(descriptor.roles)
        if RanApiRole.EVIDENCE in roles:
            projection["evidence_apis"].append(agent_view)
        if RanApiRole.ACTION in roles:
            projection["action_apis"].append(agent_view)
            if descriptor.action_type is not None:
                projection["action_types"].append(descriptor.action_type.value)
        if RanApiRole.FEEDBACK in roles or RanApiRole.ACTION in roles:
            projection["feedback_apis"].append(
                {
                    "api_id": descriptor.api_id,
                    "kind": descriptor.kind.value,
                    "backend": descriptor.backend.value,
                    "safe_error_classes": [err.value for err in descriptor.safe_error_classes],
                }
            )
        if descriptor.observation_source is not None:
            projection["observation_sources"].append(descriptor.observation_source.value)
    projection["action_types"] = sorted(set(projection["action_types"]))
    projection["observation_sources"] = sorted(set(projection["observation_sources"]))
    return projection


def descriptor_for_action(action_type: RanActionType | str) -> ApiDescriptor | None:
    normalized = action_type if isinstance(action_type, RanActionType) else RanActionType(action_type)
    for descriptor in DEFAULT_API_DESCRIPTORS:
        if descriptor.action_type == normalized:
            return descriptor
    return None


def descriptors_for_sources(sources: list[str] | tuple[str, ...]) -> tuple[ApiDescriptor, ...]:
    normalized = {RanObservationSource(source) for source in sources}
    return tuple(
        descriptor
        for descriptor in DEFAULT_API_DESCRIPTORS
        if descriptor.observation_source is not None and descriptor.observation_source in normalized
    )
