"""RIC provider constants and helpers for v4 E2 KPM runs."""

from __future__ import annotations

from typing import Any

RIC_PROVIDER_FLEXRIC = "flexric"

DEFAULT_FLEXRIC_OCUDU_REPO = "https://github.com/zhouyou-gu/flexric-ocudu-kpm-v05.git"
DEFAULT_FLEXRIC_OCUDU_REF = "main"
FLEXRIC_UPSTREAM_REPO = "https://gitlab.eurecom.fr/mosaic5g/flexric.git"
FLEXRIC_UPSTREAM_REF = "br-flexric"
FLEXRIC_UPSTREAM_COMMIT = "1a3903a7"
FLEXRIC_IMAGE = "skillful-ran/flexric-bench:br-flexric-1a3903a7-kpm-v5-ocudu-26_04"
FLEXRIC_SOURCE_DIRNAME = "flexric-ocudu-kpm-v05"
FLEXRIC_CONTEXT_PREP_SCRIPT = "tools/prepare_ocudu_kpm_v05_context.sh"
FLEXRIC_DOCKERFILE_REL = "docker/ocudu-kpm-v05/Dockerfile"
FLEXRIC_CONTAINER_PREFIX = "skillful-ran-bench-flexric"
KPM_XAPP_CONTAINER_PREFIX = "skillful-ran-bench-kpm-xapp"
E2_PCAP_CONTAINER_PREFIX = "skillful-ran-bench-e2-pcap"
RIC_PORT = 36421
FLEXRIC_E2AP_VERSION = "E2AP_V3"
FLEXRIC_SM_ENCODING_KPM = "ASN"
FLEXRIC_CMAKE_KPM_VERSION = "KPM_V5_00"
FLEXRIC_KPM_RELEASE = "KPM_V5_00"
FLEXRIC_KPM_ASN_RELEASE = "E2SM-KPM-R003-v05.00"
FLEXRIC_PATCH_LEVEL = "ocudu-kpm-v05"
FLEXRIC_OCUDU_ASN_HEADER = "include/ocudu/asn1/e2sm/e2sm_kpm_ies.h"
FLEXRIC_OCUDU_ASN_SOURCE = "lib/asn1/e2sm/e2sm_kpm_ies.cpp"

FLEXRIC_V4_CHECKS = {
    "flexric_docker_assets",
    "near_rt_ric_health",
    "ocudu_e2_config",
    "e2_setup_path",
    "e2_kpm_subscription",
    "e2_pcap_log_oracle",
}

def v4_checks_for_provider(provider: str) -> set[str]:
    if provider != RIC_PROVIDER_FLEXRIC:
        raise ValueError("Only the flexric RIC provider is supported")
    return set(FLEXRIC_V4_CHECKS)


def provider_setup_checks(provider: str) -> set[str]:
    if provider != RIC_PROVIDER_FLEXRIC:
        raise ValueError("Only the flexric RIC provider is supported")
    return {"flexric_docker_assets", "near_rt_ric_health"}


def flexric_workspace_paths(workspace: str) -> dict[str, str]:
    root = f"{workspace}/flexric"
    return {
        "root": root,
        "dockerfile": f"{root}/Dockerfile",
        "build_log": f"{root}/build.log",
        "manifest": f"{root}/manifest.json",
    }


def flexric_manifest(
    repo: str = DEFAULT_FLEXRIC_OCUDU_REPO,
    ref: str = DEFAULT_FLEXRIC_OCUDU_REF,
    commit: str = "",
    ocudu_repo: str = "",
    ocudu_ref: str = "",
    ocudu_commit: str = "",
) -> dict[str, Any]:
    return {
        "kind": "dockerized-flexric",
        "image": FLEXRIC_IMAGE,
        "repo": repo,
        "ref": ref,
        "commit": commit,
        "base_upstream_repo": FLEXRIC_UPSTREAM_REPO,
        "base_upstream_ref": FLEXRIC_UPSTREAM_REF,
        "base_upstream_commit": FLEXRIC_UPSTREAM_COMMIT,
        "ocudu_repo": ocudu_repo,
        "ocudu_ref": ocudu_ref,
        "ocudu_commit": ocudu_commit,
        "e2ap_version": FLEXRIC_E2AP_VERSION,
        "sm_encoding_kpm": FLEXRIC_SM_ENCODING_KPM,
        "kpm_version": FLEXRIC_CMAKE_KPM_VERSION,
        "kpm_release": FLEXRIC_KPM_RELEASE,
        "kpm_asn_release": FLEXRIC_KPM_ASN_RELEASE,
        "supports_e2sm_kpm_v05": True,
        "patch_level": FLEXRIC_PATCH_LEVEL,
        "decoder_source": "ocudu-generated-asn1-cpp",
        "kpm_indication_decode_per_syntax": "ATS_UNALIGNED_BASIC_PER",
        "kpm_subscription_encode_per_syntax": "ATS_ALIGNED_BASIC_PER",
        "ocudu_kpm_asn_sources": [
            FLEXRIC_OCUDU_ASN_HEADER,
            FLEXRIC_OCUDU_ASN_SOURCE,
        ],
        "ocudu_kpm_decoder_binary": "/usr/local/bin/ocudu-kpm-v05-decode",
        "ccc_prb_control_binary": "/usr/local/bin/ocudu-ccc-prb-control",
        "rc_du_prb_control_binary": "/usr/local/bin/ocudu-rc-du-prb-control",
        "xapp_db": "NONE_XAPP",
        "ric_binary": "/usr/local/bin/flexric/ric/nearRT-RIC",
        "xapp_search_root": "/opt/flexric/build/examples",
        "default_port": RIC_PORT,
    }
