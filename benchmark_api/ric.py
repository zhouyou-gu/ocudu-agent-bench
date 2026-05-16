"""RIC provider constants and helpers for v4 E2 KPM runs."""

from __future__ import annotations

from typing import Any

RIC_PROVIDER_FLEXRIC = "flexric"
RIC_PROVIDER_DRAX_EXISTING = "drax-existing"

FLEXRIC_REPO = "https://gitlab.eurecom.fr/mosaic5g/flexric.git"
FLEXRIC_BRANCH = "br-flexric"
FLEXRIC_COMMIT = "1a3903a7"
FLEXRIC_IMAGE = "skillful-ran/flexric-bench:br-flexric-1a3903a7-kpm-v3"
FLEXRIC_CONTAINER_PREFIX = "skillful-ran-bench-flexric"
KPM_XAPP_CONTAINER_PREFIX = "skillful-ran-bench-kpm-xapp"
E2_PCAP_CONTAINER_PREFIX = "skillful-ran-bench-e2-pcap"
RIC_PORT = 36421
FLEXRIC_E2AP_VERSION = "E2AP_V3"
FLEXRIC_CMAKE_KPM_VERSION = "KPM_V3"
FLEXRIC_KPM_RELEASE = "KPM_V3_00"
DRAX_KPM_RELEASE = "E2SM-KPM-R003-v05.00"

FLEXRIC_V4_CHECKS = {
    "flexric_docker_assets",
    "near_rt_ric_health",
    "ocudu_e2_config",
    "e2_setup_path",
    "e2_kpm_subscription",
    "e2_pcap_log_oracle",
}
DRAX_V4_CHECKS = {
    "drax_cluster_access",
    "drax_e2_endpoint_config",
    "drax_kpm_xapp_api",
    "ocudu_e2_config",
    "e2_setup_path",
    "e2_kpm_subscription",
    "e2_pcap_log_oracle",
}


def v4_checks_for_provider(provider: str) -> set[str]:
    if provider == RIC_PROVIDER_DRAX_EXISTING:
        return set(DRAX_V4_CHECKS)
    return set(FLEXRIC_V4_CHECKS)


def provider_setup_checks(provider: str) -> set[str]:
    if provider == RIC_PROVIDER_DRAX_EXISTING:
        return {"drax_cluster_access", "drax_e2_endpoint_config", "drax_kpm_xapp_api"}
    return {"flexric_docker_assets", "near_rt_ric_health"}


def flexric_workspace_paths(workspace: str) -> dict[str, str]:
    root = f"{workspace}/flexric"
    return {
        "root": root,
        "dockerfile": f"{root}/Dockerfile",
        "build_log": f"{root}/build.log",
        "manifest": f"{root}/manifest.json",
    }


def drax_workspace_paths(workspace: str) -> dict[str, str]:
    root = f"{workspace}/drax"
    return {
        "root": root,
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
        "kpm_version": FLEXRIC_CMAKE_KPM_VERSION,
        "kpm_release": FLEXRIC_KPM_RELEASE,
        "xapp_db": "NONE_XAPP",
        "ric_binary": "/usr/local/bin/flexric/ric/nearRT-RIC",
        "xapp_search_root": "/opt/flexric/build/examples",
        "default_port": RIC_PORT,
    }


def drax_manifest(config: Any) -> dict[str, Any]:
    drax = config.drax
    return {
        "kind": "existing-drax-ric",
        "provider": RIC_PROVIDER_DRAX_EXISTING,
        "kpm_release": DRAX_KPM_RELEASE,
        "cluster_access": "containerized-kubectl",
        "kubectl_image": drax.kubectl_image,
        "namespace": drax.namespace,
        "kubeconfig": drax.kubeconfig,
        "e2_endpoint": drax.e2_endpoint,
        "e2_bind_addr": drax.e2_bind_addr,
        "kpm_api_url": drax.kpm_api_url,
        "owns_ric_lifecycle": False,
        "owns_xapp_lifecycle": False,
    }


def parse_e2_endpoint(endpoint: str) -> tuple[str, int]:
    if not endpoint or ":" not in endpoint:
        raise ValueError("drax.e2-endpoint must have HOST:PORT form")
    host, port_text = endpoint.rsplit(":", 1)
    if not host:
        raise ValueError("drax.e2-endpoint host is empty")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("drax.e2-endpoint port must be an integer") from exc
    if port <= 0 or port > 65535:
        raise ValueError("drax.e2-endpoint port must be in [1, 65535]")
    return host, port


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
RUN git clone --branch {FLEXRIC_BRANCH} {FLEXRIC_REPO} /opt/flexric \\
  && cd /opt/flexric \\
  && git checkout {FLEXRIC_COMMIT} \\
  && mkdir -p build \\
  && cd build \\
  && cmake -DE2AP_VERSION={FLEXRIC_E2AP_VERSION} -DKPM_VERSION={FLEXRIC_CMAKE_KPM_VERSION} -DXAPP_DB=NONE_XAPP .. \\
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
