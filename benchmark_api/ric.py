"""RIC provider constants and helpers for v4 E2 KPM runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

RIC_PROVIDER_FLEXRIC = "flexric"

FLEXRIC_REPO = "https://gitlab.eurecom.fr/mosaic5g/flexric.git"
FLEXRIC_BRANCH = "br-flexric"
FLEXRIC_COMMIT = "1a3903a7"
FLEXRIC_IMAGE = "skillful-ran/flexric-bench:br-flexric-1a3903a7-kpm-v5-ocudu-26_04"
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
FLEXRIC_OCUDU_ASN_BUNDLE_SOURCES = [
    "lib/asn1/e2sm/e2sm_kpm_ies.cpp",
    "lib/asn1/e2sm/e2sm_common_ies.cpp",
    "lib/asn1/asn1_utils.cpp",
]
FLEXRIC_OCUDU_DECODER_SUPPORT_SOURCES = [
    "lib/support/byte_buffer.cpp",
    "external/fmt/src/format.cc",
    "lib/ocudulog/ocudulog.cpp",
    "lib/ocudulog/ocudulog_c.cpp",
    "lib/ocudulog/backend_worker.cpp",
    "lib/ocudulog/event_trace.cpp",
    "lib/ocudulog/formatters/json_formatter.cpp",
    "lib/ocudulog/formatters/text_formatter.cpp",
]

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


def flexric_manifest() -> dict[str, Any]:
    return {
        "kind": "dockerized-flexric",
        "image": FLEXRIC_IMAGE,
        "repo": FLEXRIC_REPO,
        "branch": FLEXRIC_BRANCH,
        "commit": FLEXRIC_COMMIT,
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
        "xapp_db": "NONE_XAPP",
        "ric_binary": "/usr/local/bin/flexric/ric/nearRT-RIC",
        "xapp_search_root": "/opt/flexric/build/examples",
        "default_port": RIC_PORT,
    }


def generate_flexric_kpm_v05_patch_script() -> str:
    script = Path(__file__).resolve().parents[1] / "provision" / "flexric" / "apply_kpm_v05_patch.py"
    return script.read_text(encoding="utf-8")


def generate_ocudu_kpm_v05_decoder_source() -> str:
    source = Path(__file__).resolve().parents[1] / "provision" / "flexric" / "ocudu_kpm_v05_decode.cpp"
    return source.read_text(encoding="utf-8")


def generate_flexric_dockerfile() -> str:
    manifest = flexric_manifest()
    return f"""FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ca-certificates git build-essential gcc-10 g++-10 swig cmake cmake-curses-gui \\
    libsctp-dev python3 python3-dev pkg-config libconfig-dev libconfig++-dev libpcre2-dev tcpdump \\
  && rm -rf /var/lib/apt/lists/*
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-10 100 \\
  && update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-10 100
COPY ocudu-asn1/ /opt/ocudu-asn1/
COPY patches/ /opt/flexric-bench/patches/
RUN g++ -std=c++17 \\
  -I/opt/ocudu-asn1/include -I/opt/ocudu-asn1/external -I/opt/ocudu-asn1/external/fmt/include \\
  -I/opt/ocudu-asn1/include/ocudu/ocudulog/bundled -I/opt/ocudu-asn1/include/ocudu/ocudulog/formatters \\
  -I/opt/ocudu-asn1/lib/ocudulog -I/opt/ocudu-asn1/lib/ocudulog/formatters -I/opt/ocudu-asn1/lib/ocudulog/sinks \\
  /opt/flexric-bench/patches/ocudu_kpm_v05_decode.cpp \\
  /opt/ocudu-asn1/lib/asn1/e2sm/e2sm_kpm_ies.cpp \\
  /opt/ocudu-asn1/lib/asn1/e2sm/e2sm_common_ies.cpp \\
  /opt/ocudu-asn1/lib/asn1/asn1_utils.cpp \\
  /opt/ocudu-asn1/lib/support/byte_buffer.cpp \\
  /opt/ocudu-asn1/external/fmt/src/format.cc \\
  /opt/ocudu-asn1/lib/ocudulog/ocudulog.cpp \\
  /opt/ocudu-asn1/lib/ocudulog/ocudulog_c.cpp \\
  /opt/ocudu-asn1/lib/ocudulog/backend_worker.cpp \\
  /opt/ocudu-asn1/lib/ocudulog/event_trace.cpp \\
  /opt/ocudu-asn1/lib/ocudulog/formatters/json_formatter.cpp \\
  /opt/ocudu-asn1/lib/ocudulog/formatters/text_formatter.cpp \\
  -lpthread -o /usr/local/bin/ocudu-kpm-v05-decode
RUN git clone --branch {FLEXRIC_BRANCH} {FLEXRIC_REPO} /opt/flexric \\
  && cd /opt/flexric \\
  && git checkout {FLEXRIC_COMMIT} \\
  && python3 /opt/flexric-bench/patches/apply_kpm_v05_patch.py /opt/flexric /opt/ocudu-asn1 \\
  && mkdir -p build \\
  && cd build \\
  && cmake -DE2AP_VERSION={FLEXRIC_E2AP_VERSION} -DSM_ENCODING_KPM={FLEXRIC_SM_ENCODING_KPM} -DKPM_VERSION={FLEXRIC_CMAKE_KPM_VERSION} -DXAPP_DB=NONE_XAPP -DUNIT_TEST=OFF .. \\
  && make -j$(nproc) \\
  && make install \\
  && ldconfig
RUN mkdir -p /opt/flexric-bench \\
  && python3 - <<'PY'
import json
import os
import pathlib
manifest = {manifest!r}
root = pathlib.Path("/opt/flexric/build/examples")
xapps = []
for path in root.rglob("*"):
    if path.is_file() and os.access(path, os.X_OK):
        name = path.name.lower()
        if ("kpm" in name and "moni" in name) or "oran_moni" in name:
            xapps.append(str(path))
manifest["kpm_xapp_candidates"] = sorted(xapps)
pathlib.Path("/opt/flexric-bench/manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
PY
RUN printf '%s\\n' \\
    '#!/bin/sh' \\
    'if command -v nearRT-RIC >/dev/null 2>&1; then exec nearRT-RIC "$@"; fi' \\
    'if [ -x /usr/local/bin/flexric/ric/nearRT-RIC ]; then exec /usr/local/bin/flexric/ric/nearRT-RIC "$@"; fi' \\
    'if [ -x /opt/flexric/build/examples/ric/nearRT-RIC ]; then exec /opt/flexric/build/examples/ric/nearRT-RIC "$@"; fi' \\
    'echo "nearRT-RIC binary not found" >&2' \\
    'exit 127' \\
  > /usr/local/bin/flexric-ric \\
  && chmod +x /usr/local/bin/flexric-ric
WORKDIR /opt/flexric
CMD ["flexric-ric"]
"""
