"""Deterministic in-memory expansion for high-resolution task variants."""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

from benchmark_api.task_definition import PrivateTask, clone_task_with_overrides


DIAGNOSTIC_METRICS = (
    "action_timing_similarity",
    "expected_action_payload_similarity",
    "post_action_evidence_similarity",
)


def generate_variant_tasks(
    *,
    anchors: dict[str, PrivateTask],
    seed: int = 1,
    count: int | None = None,
    suite: str = "generated",
    task_sets_dir: Path | str,
    family: str | None = None,
) -> dict[str, PrivateTask]:
    root = Path(task_sets_dir)
    registry = _load_registry(root)
    policy = _load_policy(root, suite)
    registry_sha256 = _file_sha256(root / "generated" / "axis_registry.json")
    policy_sha256 = _file_sha256(root / "generated" / "suite_policies.json")
    axes = [axis for axis in registry["axes"] if axis.get("anchor_task_id") in anchors]
    if family is not None:
        axes = [axis for axis in axes if _public_family(axis.get("family", "")) == family.strip().lower()]
    if not axes:
        if family is not None:
            return {}
        raise ValueError("no variant axes match the requested suite/family")
    requested_count = count if count is not None else int(policy.get("default_count", 200))
    if requested_count <= 0:
        raise ValueError("generated variant count must be positive")
    if count is None and not policy.get("allow_duplicates_after_exhaustion", False):
        finite_capacity = _axis_space_capacity(axes)
        if finite_capacity is not None:
            requested_count = min(requested_count, finite_capacity)

    rng = random.Random(seed)
    ordered_axes = list(axes)
    rng.shuffle(ordered_axes)
    tasks: dict[str, PrivateTask] = {}
    seen_vectors: set[str] = set()
    axis_offsets: dict[str, int] = {}
    task_index = 1
    while len(tasks) < requested_count:
        available_axes = _available_axes(ordered_axes, axis_offsets, policy)
        if not available_axes:
            raise ValueError("variant axis space exhausted before requested count")
        axis = _select_axis(available_axes, tasks.values(), policy, task_index, axis_offsets)
        candidate = _candidate_from_axis(axis, axis_offsets.get(_axis_key(axis), 0), seed, task_index)
        axis_offsets[_axis_key(axis)] = axis_offsets.get(_axis_key(axis), 0) + 1
        vector_key = _vector_key(axis, candidate)
        attempts = 0
        while vector_key in seen_vectors and attempts < 1000:
            candidate = _candidate_from_axis(axis, axis_offsets.get(_axis_key(axis), 0), seed, task_index + attempts + 1)
            axis_offsets[_axis_key(axis)] = axis_offsets.get(_axis_key(axis), 0) + 1
            vector_key = _vector_key(axis, candidate)
            attempts += 1
        if vector_key in seen_vectors and not policy.get("allow_duplicates_after_exhaustion", False):
            raise ValueError("variant axis space exhausted before requested count")
        seen_vectors.add(vector_key)
        task = _build_variant(
            anchors[str(axis["anchor_task_id"])],
            axis,
            candidate,
            suite,
            seed,
            task_index,
            registry_sha256=registry_sha256,
            policy_sha256=policy_sha256,
        )
        tasks[task.task_id] = task
        task_index += 1
    return tasks


def _load_registry(task_sets_dir: Path) -> dict[str, Any]:
    path = task_sets_dir / "generated" / "axis_registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("axes"), list):
        raise ValueError("axis registry must contain an axes list")
    return data


def _load_policy(task_sets_dir: Path, suite: str) -> dict[str, Any]:
    path = task_sets_dir / "generated" / "suite_policies.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    suites = data.get("suites", {}) if isinstance(data, dict) else {}
    policy = suites.get(suite)
    if not isinstance(policy, dict):
        raise ValueError(f"generated suite policy not found: {suite}")
    return dict(policy)


def _available_axes(
    axes: list[dict[str, Any]],
    axis_offsets: dict[str, int],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    if policy.get("allow_duplicates_after_exhaustion"):
        return axes
    available = []
    for axis in axes:
        capacity = _axis_capacity(axis)
        if capacity is None or axis_offsets.get(_axis_key(axis), 0) < capacity:
            available.append(axis)
    return available


def _axis_capacity(axis: dict[str, Any]) -> int | None:
    levels = axis.get("levels")
    if isinstance(levels, list) and levels:
        return len(levels)
    transform = str((axis.get("sampler") or {}).get("transform", ""))
    capacities = {
        "prb_ratio": 33 * 16,
        "ssb_power": 10 * 10_000,
        "cfo_target": 19,
        "tx_offset_target": 101,
        "handover_identity": 78 * 12 * 4096,
        "cho_targets": 78 * 3 * 4096,
        "core_nf_target": 4,
        "core_ue_mismatch": 4,
        "no_action_symptoms": 26 * 31 * 51 * 5,
    }
    return capacities.get(transform)


def _axis_space_capacity(axes: list[dict[str, Any]]) -> int | None:
    total = 0
    for axis in axes:
        capacity = _axis_capacity(axis)
        if capacity is None:
            return None
        total += capacity
    return total


def _select_axis(
    axes: list[dict[str, Any]],
    existing_tasks: Any,
    policy: dict[str, Any],
    index: int,
    axis_offsets: dict[str, int],
) -> dict[str, Any]:
    if policy.get("selection") != "balanced":
        return axes[(index - 1) % len(axes)]
    balance_by = set(policy.get("balance_by") or ("family",))
    family_counts: dict[str, int] = {}
    failure_mode_counts: dict[str, int] = {}
    for task in existing_tasks:
        variant = task.M.get("variant", {})
        family = _public_family(str(variant.get("family", "")))
        family_counts[family] = family_counts.get(family, 0) + 1
        for mode in variant.get("expected_failure_modes", []):
            mode_key = str(mode)
            failure_mode_counts[mode_key] = failure_mode_counts.get(mode_key, 0) + 1

    def balance_key(axis: dict[str, Any]) -> tuple[Any, ...]:
        keys: list[Any] = []
        keys.append(axis_offsets.get(_axis_key(axis), 0))
        if "family" in balance_by:
            keys.append(family_counts.get(_public_family(str(axis.get("family", ""))), 0))
        if "expected_failure_modes" in balance_by:
            modes = [str(mode) for mode in axis.get("expected_failure_modes", [])]
            if modes:
                keys.append(min(failure_mode_counts.get(mode, 0) for mode in modes))
                keys.append(sum(failure_mode_counts.get(mode, 0) for mode in modes) / len(modes))
            else:
                keys.extend((0, 0.0))
        keys.append(axes.index(axis))
        return tuple(keys)

    return min(
        axes,
        key=balance_key,
    )


def _candidate_from_axis(axis: dict[str, Any], offset: int, seed: int, global_index: int) -> dict[str, Any]:
    levels = axis.get("levels")
    if isinstance(levels, list) and levels:
        level = levels[offset % len(levels)]
        return {
            "kind": "level",
            "level": str(level["level"]),
            "values": dict(level.get("values", {})),
            "overrides": dict(level.get("overrides", {})),
            "legacy_task_id": level.get("legacy_task_id"),
        }
    sampler = axis.get("sampler")
    if isinstance(sampler, dict):
        values = _sample_axis_values(str(sampler["transform"]), seed, global_index, offset, axis)
        return {"kind": "sample", "level": str(values["level"]), "values": values, "overrides": {}}
    raise ValueError(f"variant axis has neither levels nor sampler: {axis.get('axis')}")


def _build_variant(
    anchor: PrivateTask,
    axis: dict[str, Any],
    candidate: dict[str, Any],
    suite: str,
    seed: int,
    index: int,
    *,
    registry_sha256: str,
    policy_sha256: str,
) -> PrivateTask:
    U = json.loads(json.dumps(anchor.U))
    I = json.loads(json.dumps(anchor.I))
    J = json.loads(json.dumps(anchor.J))
    E = json.loads(json.dumps(anchor.E))
    G = anchor.G
    overrides = candidate.get("overrides") or {}
    if overrides:
        E = json.loads(json.dumps(overrides.get("E", E)))
        U = json.loads(json.dumps(overrides.get("U", U)))
        I = json.loads(json.dumps(overrides.get("I", I)))
        J = json.loads(json.dumps(overrides.get("J", J)))
        G = str(overrides.get("G", G))
    else:
        transform = str(axis.get("sampler", {}).get("transform", ""))
        _apply_transform(transform, U, J, candidate["values"])

    for metric in DIAGNOSTIC_METRICS:
        if metric not in J.setdefault("raw_metrics", []):
            J["raw_metrics"].append(metric)
    J.pop("variant_axes", None)

    variant_hash = hashlib.sha1(
        json.dumps(
            {
                "seed": seed,
                "index": index,
                "anchor": anchor.task_id,
                "axis": axis.get("axis"),
                "level": candidate["level"],
                "values": candidate["values"],
                "legacy": candidate.get("legacy_task_id"),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:6]
    family = str(axis["family"])
    task_id = f"generated_s{index:04d}_{variant_hash}_v1"
    M = {
        "task_set": "generated",
        "family": _public_family(family),
        "role": "primary",
        "variant": {
            "family": family,
            "axis": str(axis["axis"]),
            "level": candidate["level"],
            "anchor_task_id": anchor.task_id,
            "suite": suite,
            "variant_id": f"s{index:04d}_{variant_hash}",
            "seed": seed,
            "axis_values": candidate["values"],
            "expected_failure_modes": list(axis.get("expected_failure_modes", [])),
            "axis_registry_sha256": registry_sha256,
            "suite_policies_sha256": policy_sha256,
        },
    }
    if candidate.get("legacy_task_id"):
        M["variant"]["legacy_task_id"] = candidate["legacy_task_id"]
    return clone_task_with_overrides(
        anchor,
        task_id=task_id,
        goal=G,
        E=E,
        U=U,
        I=I,
        J=J,
        M=M,
        source=Path("<generated>") / task_id / "task.json",
    )


def _sample_axis_values(transform: str, seed: int, index: int, offset: int, axis: dict[str, Any]) -> dict[str, Any]:
    if transform == "prb_ratio":
        min_ratio = 18 + (offset % 33)
        max_ratio = min_ratio + 35 + ((offset // 33) % 16)
        return {"level": f"{min_ratio}_{max_ratio}", "min_prb_policy_ratio": min_ratio, "max_prb_policy_ratio": max_ratio}
    if transform == "ssb_power":
        power = -18 + (offset % 10)
        nci = 6_733_800 + ((offset // 10) % 10_000)
        return {"level": f"{power}dbm_nci{nci}", "ssb_block_power_dbm": power, "nci": nci}
    if transform == "cfo_target":
        cfo = float(-1800 + ((offset % 19) * 100))
        return {"level": f"{int(cfo)}hz", "cfo_hz": cfo}
    if transform == "tx_offset_target":
        offset_us = round(2.0 + ((offset % 101) / 10.0), 1)
        return {"level": f"{offset_us}us", "tx_time_offset_us": offset_us}
    if transform == "handover_identity":
        serving = 2 + (offset % 78)
        target = serving + 1 + ((offset // 78) % 12)
        rnti = f"0x{0x4100 + ((offset // (78 * 12)) % 4096):04x}"
        return {"level": f"pci{serving}_to_{target}", "serving_pci": serving, "target_pci": target, "rnti": rnti}
    if transform == "cho_targets":
        serving = 2 + (offset % 78)
        target_count = 2 + ((offset // 78) % 3)
        targets = [serving + idx + 1 for idx in range(target_count)]
        return {"level": f"{target_count}_targets_pci{serving}", "serving_pci": serving, "target_pcis": targets, "rnti": f"0x{0x4100 + ((offset // (78 * 3)) % 4096):04x}"}
    if transform == "core_nf_target":
        nf = ["amf", "smf", "upf", "open5gs"][offset % 4]
        return {"level": nf, "nf": nf}
    if transform == "core_ue_mismatch":
        field = ["supi", "plmn", "dnn", "auth_profile_id"][offset % 4]
        values = {"supi": "001010000000099", "plmn": "99999", "dnn": "ims", "auth_profile_id": "wrong_profile"}
        return {"level": field, "mismatch_field": field, "mismatch_value": values[field]}
    if transform == "no_action_symptoms":
        queue_pressure = round(0.1 + ((offset % 26) / 100.0), 2)
        prb_utilization = round(0.25 + (((offset // 26) % 31) / 100.0), 2)
        return {
            "level": f"mild_{queue_pressure}_{prb_utilization}",
            "queue_pressure": queue_pressure,
            "prb_utilization": prb_utilization,
            "sinr_db": round(15.0 + ((((offset // (26 * 31)) % 51)) / 10.0), 1),
            "cqi": 8 + ((offset // (26 * 31 * 51)) % 5),
        }
    raise ValueError(f"unknown variant transform: {transform}")


def _apply_transform(transform: str, U: dict[str, Any], J: dict[str, Any], values: dict[str, Any]) -> None:
    if transform == "prb_ratio":
        _apply_prb_ratio(U, J, values)
    elif transform == "ssb_power":
        _apply_ssb_power(U, J, values)
    elif transform == "cfo_target":
        _apply_cfo_target(U, J, values)
    elif transform == "tx_offset_target":
        _apply_tx_offset_target(U, J, values)
    elif transform == "handover_identity":
        _apply_handover_identity(U, J, values)
    elif transform == "cho_targets":
        _apply_cho_targets(U, J, values)
    elif transform == "core_nf_target":
        _apply_core_nf_target(U, J, values)
    elif transform == "core_ue_mismatch":
        _apply_core_ue_mismatch(U, J, values)
    elif transform == "no_action_symptoms":
        _apply_no_action_symptoms(U, values)
    else:
        raise ValueError(f"unknown variant transform: {transform}")


def _events(U: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [event for event in U.get("events", []) if event.get("kind") == kind]


def _apply_prb_ratio(U: dict[str, Any], J: dict[str, Any], values: dict[str, Any]) -> None:
    for event in _events(U, "slice_demand_shift"):
        params = event.setdefault("parameters", {})
        if str(params.get("demand_level", "")).strip().lower() in {"nominal", "low"}:
            continue
        params["min_prb_policy_ratio"] = values["min_prb_policy_ratio"]
        params["max_prb_policy_ratio"] = values["max_prb_policy_ratio"]
        params["queue_pressure"] = round(values["min_prb_policy_ratio"] / 100.0, 2)
        params["prb_utilization"] = round(values["max_prb_policy_ratio"] / 100.0, 2)
    _update_expected_action_fields(J, "SET_PRB_POLICY_RATIO_WS", values)
    _update_expected_action_fields(J, "SET_PRB_POLICY_RATIO_CCC", values)
    _update_post_fields(
        J,
        {
            "slice_runtime.current_prb_policy.min_prb_policy_ratio": values["min_prb_policy_ratio"],
            "slice_runtime.current_prb_policy.max_prb_policy_ratio": values["max_prb_policy_ratio"],
        },
    )


def _apply_ssb_power(U: dict[str, Any], J: dict[str, Any], values: dict[str, Any]) -> None:
    for event in _events(U, "cell_identity_change"):
        if event.get("apply_steps") == [1]:
            continue
        event.setdefault("parameters", {})["nci"] = values["nci"]
    for event in _events(U, "radio_condition_profile"):
        params = event.setdefault("parameters", {})
        if "target_ssb_block_power_dbm" in params or str(params.get("profile", "")).startswith("coverage"):
            params["target_ssb_block_power_dbm"] = values["ssb_block_power_dbm"]
    _update_expected_action_fields(J, "SET_SSB_BLOCK_POWER_WS", values)
    _update_post_fields(
        J,
        {
            "radio_runtime.current_ssb_block_power_dbm": values["ssb_block_power_dbm"],
            "radio_runtime.ssb_power_cell.nci": values["nci"],
        },
    )


def _apply_cfo_target(U: dict[str, Any], J: dict[str, Any], values: dict[str, Any]) -> None:
    for event in _events(U, "radio_condition_profile"):
        event.setdefault("parameters", {})["target_cfo_hz"] = values["cfo_hz"]
    _update_expected_action_fields(J, "SET_CFO_CLI", values)
    _update_post_fields(J, {"radio_runtime.cfo_hz": values["cfo_hz"], "radio_runtime.cfo_corrected": True})


def _apply_tx_offset_target(U: dict[str, Any], J: dict[str, Any], values: dict[str, Any]) -> None:
    for event in _events(U, "radio_condition_profile"):
        event.setdefault("parameters", {})["target_tx_time_offset_us"] = values["tx_time_offset_us"]
    _update_expected_action_fields(J, "SET_TX_TIME_OFFSET_CLI", values)
    _update_post_fields(
        J,
        {
            "radio_runtime.tx_time_offset_us": values["tx_time_offset_us"],
            "radio_runtime.tx_time_offset_corrected": True,
        },
    )


def _apply_handover_identity(U: dict[str, Any], J: dict[str, Any], values: dict[str, Any]) -> None:
    target_pci = values["target_pci"]
    target_pcis = [target_pci]
    for event in _events(U, "mobility_path"):
        params = event.setdefault("parameters", {})
        params["serving_pci"] = values["serving_pci"]
        params["target_pcis"] = list(target_pcis)
        params["rnti"] = values["rnti"]
    _update_expected_action_fields(
        J,
        "TRIGGER_HANDOVER_CLI",
        {
            "serving_pci": values["serving_pci"],
            "rnti": values["rnti"],
            "target_pci": target_pci,
        },
    )
    _replace_post_fields(J, {"ue_identity.serving_pci": target_pci})


def _apply_cho_targets(U: dict[str, Any], J: dict[str, Any], values: dict[str, Any]) -> None:
    for event in _events(U, "mobility_path"):
        params = event.setdefault("parameters", {})
        params["serving_pci"] = values["serving_pci"]
        params["target_pcis"] = list(values["target_pcis"])
        params["rnti"] = values["rnti"]
    _update_expected_action_fields(
        J,
        "TRIGGER_CONDITIONAL_HANDOVER_CLI",
        {
            "serving_pci": values["serving_pci"],
            "rnti": values["rnti"],
            "target_pcis": list(values["target_pcis"]),
            "timeout_s": 5,
        },
    )
    _replace_post_fields(
        J,
        {
            "ue_identity.conditional_handover_plan.serving_pci": values["serving_pci"],
            "ue_identity.conditional_handover_plan.target_pcis": list(values["target_pcis"]),
        },
    )


def _apply_core_nf_target(U: dict[str, Any], J: dict[str, Any], values: dict[str, Any]) -> None:
    nf = values["nf"]
    for event in _events(U, "core_latency_profile"):
        event.setdefault("parameters", {})["degraded_nf"] = nf
    _update_expected_action_fields(J, "RESTART_CORE_NF", {"nf": nf})
    _replace_post_fields(J, {f"core_runtime.restart_counts.{nf}": 1, "core_runtime.last_restarted_nf": nf})


def _apply_core_ue_mismatch(U: dict[str, Any], J: dict[str, Any], values: dict[str, Any]) -> None:
    desired = {
        "ue_id": "ue1",
        "supi": "001010000000001",
        "plmn": "00101",
        "dnn": "internet",
        "sst": 1,
        "sd": None,
        "auth_profile_id": "ue1_test_profile",
    }
    for event in _events(U, "core_ue_registration_misconfig"):
        params = event.setdefault("parameters", {})
        params["mismatch_field"] = values["mismatch_field"]
        params["mismatch_value"] = values["mismatch_value"]
    _update_expected_action_fields(J, "UPDATE_CORE_UE_REGISTRATION", desired)
    _replace_post_fields(
        J,
        {
            "core_runtime.ue_registration.status": "registered",
            f"core_runtime.ue_registration.current.{values['mismatch_field']}": desired[values["mismatch_field"]],
        },
    )


def _apply_no_action_symptoms(U: dict[str, Any], values: dict[str, Any]) -> None:
    for event in _events(U, "slice_demand_shift"):
        params = event.setdefault("parameters", {})
        params["demand_level"] = "nominal"
        params["queue_pressure"] = values["queue_pressure"]
        params["prb_utilization"] = values["prb_utilization"]
    for event in _events(U, "radio_condition_profile"):
        params = event.setdefault("parameters", {})
        params["profile"] = "mild_transient"
        params["sinr_db"] = values["sinr_db"]
        params["cqi"] = values["cqi"]


def _update_expected_action_fields(J: dict[str, Any], action_type: str, values: dict[str, Any]) -> None:
    for expectation in J.get("expected_action_fields", []):
        if expectation.get("action_type") != action_type:
            continue
        fields = expectation.setdefault("fields", {})
        for field in list(fields):
            if field in values:
                fields[field] = values[field]


def _update_post_fields(J: dict[str, Any], fields: dict[str, Any]) -> None:
    for expectation in J.get("expected_post_action_evidence", []):
        expectation.setdefault("fields", {}).update(fields)


def _replace_post_fields(J: dict[str, Any], fields: dict[str, Any]) -> None:
    for expectation in J.get("expected_post_action_evidence", []):
        expectation["fields"] = dict(fields)


def _axis_key(axis: dict[str, Any]) -> str:
    return f"{axis.get('anchor_task_id')}::{axis.get('family')}::{axis.get('axis')}"


def _vector_key(axis: dict[str, Any], candidate: dict[str, Any]) -> str:
    return json.dumps(
        {
            "anchor": axis.get("anchor_task_id"),
            "family": axis.get("family"),
            "axis": axis.get("axis"),
            "level": candidate.get("level"),
            "values": candidate.get("values", {}),
            "legacy": candidate.get("legacy_task_id"),
        },
        sort_keys=True,
    )


def _public_family(family: str) -> str:
    mapping = {
        "slice_prb": "prb",
        "prb_control": "prb",
        "ssb_coverage": "ssb",
        "ssb_control": "ssb",
        "backend_selection": "backend",
        "evidence_gating": "prb",
        "core_control": "core",
        "radio_cli": "radio_cli",
        "mobility": "mobility",
        "restraint": "restraint",
        "diagnosis": "diagnosis",
        "isolation": "isolation",
        "harness": "harness",
    }
    return mapping.get(str(family), _slug(str(family)))


def _anchor_slug(task_id: str) -> str:
    slug = re.sub(r"_v[0-9]+$", "", task_id)
    slug = re.sub(r"^base_", "", slug)
    return _slug(slug)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "variant"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
