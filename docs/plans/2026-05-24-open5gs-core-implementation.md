# Open5GS 5G core (split-NF) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder assets under `benchmark/provision/open5gs-core/` with a real split-NF Open5GS 5G core that comes up via one `docker compose up -d`, with AMF reachable on the docker network at a stable IP/port and a seeded subscriber ready for a future OCUDU gNB to attach via NGAP.

**Architecture:** 11 long-running containers (mongo + 10 Open5GS NFs from `gradiant/open5gs-*:2.7.0`) plus one one-shot Python seeder, all on a custom docker bridge `ran` (10.53.1.0/24) with per-NF static IPs. AMF NGAP (SCTP 38412) and mongo (TCP 27017) are published to the host loopback so the future live transport can reach them. AMF/SMF/UPF carry `cap_add: NET_ADMIN` for future `tc netem` latency injection. Compose project pinned to `open5gs` for stable container-name access.

**Tech Stack:** Docker Compose v2 schema 3.9; upstream `gradiant/open5gs-*:2.7.0` per-NF images; `mongo:7`; Python 3.12 + `pymongo` for the seeder; bash + `jq` for the acceptance script.

**Source spec:** [`benchmark/docs/specs/2026-05-24-open5gs-core-design.md`](../specs/2026-05-24-open5gs-core-design.md) — ground truth for all decisions, integration constraints, and the 7-item Definition of Done.

**Execution environment:** All file edits happen on the Mac (canonical clone at `/Users/charles_gu/Documents/GitHub/skillful-ran-workspace/`). End-to-end validation (Task 11) happens on 5090pc (Ubuntu 24.04, docker + nvidia-container-toolkit installed; clone at `~/skillful-ran-workspace/`) via git push/pull, then SSH-driven `docker compose up`.

---

### Task 1: Scaffolding — create dirs, delete stubs, baseline commit

**Files:**
- Create dirs: `benchmark/provision/open5gs-core/compose/configs/`, `benchmark/provision/open5gs-core/compose/seed/`, `benchmark/provision/open5gs-core/tests/`, `benchmark/tests/provision/`
- Delete: `benchmark/provision/open5gs-core/compose/open5gs/Dockerfile`, `open5gs.env`, `open5gs-5gc.yml`, `open5gs_entrypoint.sh`, `setup_tun.py`, `add_users.py`, `subscriber_db.csv.example`, then the empty `compose/open5gs/` directory

- [ ] **Step 1: Create the new directories**

```bash
mkdir -p benchmark/provision/open5gs-core/compose/configs \
         benchmark/provision/open5gs-core/compose/seed \
         benchmark/provision/open5gs-core/tests \
         benchmark/tests/provision
touch benchmark/tests/provision/__init__.py
```

- [ ] **Step 2: Delete the obsolete stub files**

```bash
git rm benchmark/provision/open5gs-core/compose/open5gs/Dockerfile \
       benchmark/provision/open5gs-core/compose/open5gs/open5gs.env \
       benchmark/provision/open5gs-core/compose/open5gs/open5gs-5gc.yml \
       benchmark/provision/open5gs-core/compose/open5gs/open5gs_entrypoint.sh \
       benchmark/provision/open5gs-core/compose/open5gs/setup_tun.py \
       benchmark/provision/open5gs-core/compose/open5gs/add_users.py \
       benchmark/provision/open5gs-core/compose/open5gs/subscriber_db.csv.example
rmdir benchmark/provision/open5gs-core/compose/open5gs/
```

- [ ] **Step 3: Verify the workspace state**

Run: `git status --short benchmark/provision/ benchmark/tests/provision/`
Expected: 7 lines starting with `D ` (deletions), plus `?? benchmark/tests/provision/__init__.py`. No other unexpected entries.

- [ ] **Step 4: Commit the scaffolding**

```bash
git add benchmark/tests/provision/__init__.py
git commit -m "provision/open5gs-core: drop build-from-source stubs, scaffold new layout

The compose/open5gs/ stubs were placeholders for a build-from-source
adapter path that was rejected in favor of upstream gradiant/open5gs-*
images (see spec 2026-05-24-open5gs-core-design.md §2 D2).
"
```

---

### Task 2: Seeder pure logic — `subscriber_document()` with TDD

**Files:**
- Create: `benchmark/tests/provision/test_subscriber_document.py`
- Create: `benchmark/provision/open5gs-core/compose/seed/add_users.py` (only the pure helper for this task)

The seeder splits into a pure helper (`subscriber_document`) that converts one CSV row dict into the Open5GS-format MongoDB document, and a runtime layer (CLI + pymongo) that calls it. TDD applies only to the pure helper.

- [ ] **Step 1: Write the failing test file**

Write `benchmark/tests/provision/test_subscriber_document.py`:

```python
"""Pure-logic tests for the Open5GS subscriber-seeder document mapping."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_DIR = REPO_ROOT / "benchmark" / "provision" / "open5gs-core" / "compose" / "seed"
if str(SEED_DIR) not in sys.path:
    sys.path.insert(0, str(SEED_DIR))

from add_users import subscriber_document  # noqa: E402


class SubscriberDocumentTests(unittest.TestCase):
    def _row(self, **overrides) -> dict[str, str]:
        base = {
            "imsi": "001010000000001",
            "k": "00112233445566778899aabbccddeeff",
            "opc": "63bfa50ee6523365ff14c1f45f88737d",
            "amf": "8000",
            "sqn": "000000000000",
            "plmn": "00101",
            "dnn": "internet",
            "sst": "1",
            "sd": "",
            "auth_profile_id": "ue1_test_profile",
            "ue_id": "ue1",
        }
        base.update(overrides)
        return base

    def test_basic_document_shape(self) -> None:
        doc = subscriber_document(self._row())
        self.assertEqual(doc["imsi"], "001010000000001")
        self.assertEqual(doc["security"]["k"], "00112233445566778899aabbccddeeff")
        self.assertEqual(doc["security"]["opc"], "63bfa50ee6523365ff14c1f45f88737d")
        self.assertEqual(doc["security"]["amf"], "8000")
        self.assertEqual(doc["security"]["sqn"], "000000000000")
        # Default subscribed AMBR (Open5GS schema)
        self.assertIn("ambr", doc)

    def test_slice_with_no_sd(self) -> None:
        doc = subscriber_document(self._row(sd=""))
        self.assertEqual(len(doc["slice"]), 1)
        slice0 = doc["slice"][0]
        self.assertEqual(slice0["sst"], 1)
        self.assertNotIn("sd", slice0)  # absent when empty

    def test_slice_with_sd(self) -> None:
        doc = subscriber_document(self._row(sd="123abc"))
        self.assertEqual(doc["slice"][0]["sd"], "123abc")

    def test_session_dnn(self) -> None:
        doc = subscriber_document(self._row(dnn="ims"))
        self.assertEqual(doc["slice"][0]["session"][0]["name"], "ims")

    def test_plmn_split_into_mcc_mnc(self) -> None:
        # Open5GS subscribers don't carry PLMN directly, but auth_profile_id
        # and the related metadata fields are normalized into the document.
        doc = subscriber_document(self._row(plmn="00101"))
        self.assertEqual(doc["meta"]["plmn"]["mcc"], "001")
        self.assertEqual(doc["meta"]["plmn"]["mnc"], "01")

    def test_auth_profile_id_preserved(self) -> None:
        doc = subscriber_document(self._row(auth_profile_id="ue42_lab"))
        self.assertEqual(doc["meta"]["auth_profile_id"], "ue42_lab")

    def test_ue_id_preserved(self) -> None:
        doc = subscriber_document(self._row(ue_id="ue42"))
        self.assertEqual(doc["meta"]["ue_id"], "ue42")

    def test_sst_coerced_to_int(self) -> None:
        doc = subscriber_document(self._row(sst="3"))
        self.assertEqual(doc["slice"][0]["sst"], 3)

    def test_invalid_imsi_rejected(self) -> None:
        with self.assertRaises(ValueError):
            subscriber_document(self._row(imsi=""))

    def test_invalid_k_length_rejected(self) -> None:
        with self.assertRaises(ValueError):
            subscriber_document(self._row(k="deadbeef"))  # not 32 hex chars

    def test_invalid_plmn_rejected(self) -> None:
        with self.assertRaises(ValueError):
            subscriber_document(self._row(plmn="123"))  # not 5 or 6 digits


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest benchmark.tests.provision.test_subscriber_document -v`
Expected: `FAIL` or `ERROR` — `ModuleNotFoundError: No module named 'add_users'` (because the seed file doesn't exist yet).

- [ ] **Step 3: Write the minimal `add_users.py` to make the tests pass**

Write `benchmark/provision/open5gs-core/compose/seed/add_users.py`:

```python
"""Subscriber-seeder for the Open5GS 5G core compose stack.

This file is consumed by the `subscriber-seeder` one-shot container declared
in `../docker-compose.open5gs.yml`. It reads a CSV at the path given by
`SUBSCRIBER_CSV` (default: ./subscriber_db.csv), maps each row into the
Open5GS subscriber document schema, and upserts every row into the
`open5gs.subscribers` MongoDB collection.

The mapping logic is exposed as the pure function `subscriber_document(row)`
so it can be unit-tested without a live mongo.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path
from typing import Any

_IMSI_RE = re.compile(r"^[0-9]{5,16}$")
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_PLMN_RE = re.compile(r"^[0-9]{5,6}$")
_HEX_AMF_RE = re.compile(r"^[0-9a-fA-F]{4}$")
_HEX_SQN_RE = re.compile(r"^[0-9a-fA-F]{12}$")


def subscriber_document(row: dict[str, str]) -> dict[str, Any]:
    """Convert one CSV row into an Open5GS subscriber MongoDB document.

    Required CSV columns: imsi, k, opc, amf, sqn, plmn, dnn, sst, sd,
    auth_profile_id, ue_id. `sd` may be empty to indicate "no slice
    differentiator."
    """

    imsi = row["imsi"].strip()
    k = row["k"].strip()
    opc = row["opc"].strip()
    amf = row["amf"].strip()
    sqn = row["sqn"].strip()
    plmn = row["plmn"].strip()
    dnn = row["dnn"].strip()
    sst_raw = row["sst"].strip()
    sd = row["sd"].strip()
    auth_profile_id = row["auth_profile_id"].strip()
    ue_id = row["ue_id"].strip()

    if not _IMSI_RE.match(imsi):
        raise ValueError(f"invalid imsi: {imsi!r}")
    if not _HEX32_RE.match(k):
        raise ValueError(f"invalid K (need 32 hex chars): {k!r}")
    if not _HEX32_RE.match(opc):
        raise ValueError(f"invalid OPc (need 32 hex chars): {opc!r}")
    if not _HEX_AMF_RE.match(amf):
        raise ValueError(f"invalid AMF (need 4 hex chars): {amf!r}")
    if not _HEX_SQN_RE.match(sqn):
        raise ValueError(f"invalid SQN (need 12 hex chars): {sqn!r}")
    if not _PLMN_RE.match(plmn):
        raise ValueError(f"invalid plmn (need 5 or 6 digits): {plmn!r}")
    try:
        sst = int(sst_raw)
    except ValueError as exc:
        raise ValueError(f"invalid sst: {sst_raw!r}") from exc

    mcc = plmn[:3]
    mnc = plmn[3:]

    slice0: dict[str, Any] = {
        "sst": sst,
        "default_indicator": True,
        "session": [
            {
                "name": dnn,
                "type": 3,  # IPv4
                "pcc_rule": [],
                "ambr": {
                    "uplink": {"value": 1, "unit": 3},     # 1 Gbps
                    "downlink": {"value": 1, "unit": 3},
                },
                "qos": {
                    "index": 9,
                    "arp": {
                        "priority_level": 8,
                        "pre_emption_capability": 1,
                        "pre_emption_vulnerability": 1,
                    },
                },
            }
        ],
    }
    if sd:
        slice0["sd"] = sd

    return {
        "imsi": imsi,
        "schema_version": 1,
        "msisdn": [],
        "imeisv": [],
        "mme_host": [],
        "mm_realm": [],
        "purge_flag": [],
        "access_restriction_data": 32,
        "subscriber_status": 0,
        "network_access_mode": 0,
        "subscribed_rau_tau_timer": 12,
        "ambr": {
            "uplink": {"value": 1, "unit": 3},
            "downlink": {"value": 1, "unit": 3},
        },
        "slice": [slice0],
        "security": {
            "k": k,
            "opc": opc,
            "amf": amf,
            "sqn": sqn,
            "op": None,
        },
        "meta": {
            "plmn": {"mcc": mcc, "mnc": mnc},
            "auth_profile_id": auth_profile_id,
            "ue_id": ue_id,
        },
    }


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return [row for row in reader]


def main(argv: list[str] | None = None) -> int:
    csv_path = Path(os.environ.get("SUBSCRIBER_CSV", "./subscriber_db.csv"))
    uri = os.environ.get("MONGO_URI", "mongodb://mongo:27017/")
    db_name = os.environ.get("MONGO_DB", "open5gs")

    if not csv_path.exists():
        print(f"add_users: csv not found: {csv_path}", file=sys.stderr)
        return 1
    rows = _csv_rows(csv_path)
    if not rows:
        print(f"add_users: csv has no rows: {csv_path}", file=sys.stderr)
        return 1

    try:
        from pymongo import MongoClient
    except ImportError:
        print("add_users: pymongo is not installed", file=sys.stderr)
        return 2

    client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    coll = client[db_name]["subscribers"]
    n = 0
    for row in rows:
        doc = subscriber_document(row)
        coll.replace_one({"imsi": doc["imsi"]}, doc, upsert=True)
        n += 1
    print(f"add_users: upserted {n} subscriber(s) into {db_name}.subscribers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests, verify they pass**

Run: `python3 -m unittest benchmark.tests.provision.test_subscriber_document -v`
Expected: `Ran 11 tests in ...s` followed by `OK`.

- [ ] **Step 5: Confirm the full benchmark test suite still passes (no regressions)**

Run: `python3 -m unittest discover benchmark/tests 2>&1 | tail -4`
Expected: `Ran 117 tests in ...s` (106 previous + 11 new) `OK`.

- [ ] **Step 6: Commit**

```bash
git add benchmark/tests/provision/__init__.py \
        benchmark/tests/provision/test_subscriber_document.py \
        benchmark/provision/open5gs-core/compose/seed/add_users.py
git commit -m "provision/open5gs-core: add subscriber-seeder pure logic + tests

subscriber_document(row) maps one CSV row to an Open5GS-shape
MongoDB document. Pure function; 11 unit tests covering happy path,
slice-with/without-sd, sst coercion, plmn split, invalid input
rejection. main() reads SUBSCRIBER_CSV / MONGO_URI env vars and
upserts to open5gs.subscribers. Runtime layer not yet exercised
end-to-end; that happens in Task 11.
"
```

---

### Task 3: Seeder runtime — Dockerfile + requirements

**Files:**
- Create: `benchmark/provision/open5gs-core/compose/seed/requirements.txt`
- Create: `benchmark/provision/open5gs-core/compose/seed/Dockerfile`

- [ ] **Step 1: Write `requirements.txt`**

Write `benchmark/provision/open5gs-core/compose/seed/requirements.txt`:

```text
pymongo==4.8.0
```

- [ ] **Step 2: Write `Dockerfile`**

Write `benchmark/provision/open5gs-core/compose/seed/Dockerfile`:

```dockerfile
# Open5GS subscriber-seeder image: a one-shot container that reads
# subscriber_db.csv and upserts the rows into the open5gs.subscribers
# MongoDB collection. Built locally by docker compose; not published.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY add_users.py ./
COPY subscriber_db.csv ./

ENV SUBSCRIBER_CSV=/app/subscriber_db.csv \
    MONGO_URI=mongodb://mongo:27017/ \
    MONGO_DB=open5gs

ENTRYPOINT ["python3", "/app/add_users.py"]
```

- [ ] **Step 3: Smoke-test the Dockerfile builds locally on Mac**

Run: `docker build -t skillful-ran-test/subscriber-seeder benchmark/provision/open5gs-core/compose/seed/`
Expected: succeeds; one new image. If Docker isn't installed on Mac, this step is optional — the real validation happens in Task 11 on 5090pc.
Cleanup: `docker rmi skillful-ran-test/subscriber-seeder` (if it ran).

- [ ] **Step 4: Commit**

```bash
git add benchmark/provision/open5gs-core/compose/seed/requirements.txt \
        benchmark/provision/open5gs-core/compose/seed/Dockerfile
git commit -m "provision/open5gs-core: seeder Dockerfile + requirements.txt

Tiny python:3.12-slim image, pinned pymongo, defaults that match the
compose env. Entrypoint is /app/add_users.py.
"
```

---

### Task 4: Subscriber CSV — default test UE

**Files:**
- Create: `benchmark/provision/open5gs-core/compose/seed/subscriber_db.csv`

- [ ] **Step 1: Write the CSV**

Write `benchmark/provision/open5gs-core/compose/seed/subscriber_db.csv`:

```text
imsi,k,opc,amf,sqn,plmn,dnn,sst,sd,auth_profile_id,ue_id
001010000000001,00112233445566778899aabbccddeeff,63bfa50ee6523365ff14c1f45f88737d,8000,000000000000,00101,internet,1,,ue1_test_profile,ue1
```

The K and OPc values are the standard srsRAN/Open5GS ZMQ test triplet — a public well-known value, deliberately committed; not a real subscriber.

- [ ] **Step 2: Verify the CSV parses through the seeder helper**

Run:
```bash
python3 -c "
import sys, csv
sys.path.insert(0, 'benchmark/provision/open5gs-core/compose/seed')
from add_users import subscriber_document
rows = list(csv.DictReader(open('benchmark/provision/open5gs-core/compose/seed/subscriber_db.csv')))
print(f'{len(rows)} row(s) parsed')
for row in rows:
    doc = subscriber_document(row)
    print(f'  imsi={doc[\"imsi\"]} dnn={doc[\"slice\"][0][\"session\"][0][\"name\"]} sst={doc[\"slice\"][0][\"sst\"]}')
"
```
Expected: `1 row(s) parsed` + a single line `imsi=001010000000001 dnn=internet sst=1`.

- [ ] **Step 3: Commit**

```bash
git add benchmark/provision/open5gs-core/compose/seed/subscriber_db.csv
git commit -m "provision/open5gs-core: default subscriber_db.csv (1 test UE)

Standard srsRAN/Open5GS ZMQ test triplet:
  IMSI 001010000000001 / K 0011..eeff / OPc 63bf..737d
  PLMN 00101 / DNN internet / SST 1 / no SD
  auth_profile_id=ue1_test_profile (matches simulated_ocudu defaults)

This is a public well-known value, deliberately committed.
"
```

---

### Task 5: Per-NF YAML configs — SBI-only NFs (NRF, SCP, AUSF, UDM, UDR, PCF, BSF)

**Files:**
- Create: `benchmark/provision/open5gs-core/compose/configs/nrf.yaml`
- Create: `benchmark/provision/open5gs-core/compose/configs/scp.yaml`
- Create: `benchmark/provision/open5gs-core/compose/configs/ausf.yaml`
- Create: `benchmark/provision/open5gs-core/compose/configs/udm.yaml`
- Create: `benchmark/provision/open5gs-core/compose/configs/udr.yaml`
- Create: `benchmark/provision/open5gs-core/compose/configs/pcf.yaml`
- Create: `benchmark/provision/open5gs-core/compose/configs/bsf.yaml`

All 7 share the standard SBI-server-on-7777 + SBI-client-to-NRF pattern. The deltas are tiny.

- [ ] **Step 1: Write `nrf.yaml` (no NRF client; this IS the NRF)**

Write `benchmark/provision/open5gs-core/compose/configs/nrf.yaml`:

```yaml
logger:
  level: info

global:
  max:
    ue: 1024
    peer: 64

nrf:
  serving:
    - plmn_id:
        mcc: 001
        mnc: 01
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
```

- [ ] **Step 2: Write `scp.yaml`**

Write `benchmark/provision/open5gs-core/compose/configs/scp.yaml`:

```yaml
logger:
  level: info

scp:
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
    client:
      nrf:
        - uri: http://10.53.1.4:7777
```

- [ ] **Step 3: Write `ausf.yaml`**

Write `benchmark/provision/open5gs-core/compose/configs/ausf.yaml`:

```yaml
logger:
  level: info

ausf:
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
    client:
      nrf:
        - uri: http://10.53.1.4:7777
      scp:
        - uri: http://10.53.1.5:7777
```

- [ ] **Step 4: Write `udm.yaml`**

Write `benchmark/provision/open5gs-core/compose/configs/udm.yaml`:

```yaml
logger:
  level: info

udm:
  hnet:
    - id: 1
      scheme: 1
      key: /opt/open5gs/etc/open5gs/hnet/curve25519-1.key
    - id: 2
      scheme: 2
      key: /opt/open5gs/etc/open5gs/hnet/secp256r1-2.key
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
    client:
      nrf:
        - uri: http://10.53.1.4:7777
      scp:
        - uri: http://10.53.1.5:7777
```

- [ ] **Step 5: Write `udr.yaml`**

Write `benchmark/provision/open5gs-core/compose/configs/udr.yaml`:

```yaml
logger:
  level: info

db_uri: mongodb://10.53.1.10/open5gs

udr:
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
    client:
      nrf:
        - uri: http://10.53.1.4:7777
      scp:
        - uri: http://10.53.1.5:7777
```

- [ ] **Step 6: Write `pcf.yaml`**

Write `benchmark/provision/open5gs-core/compose/configs/pcf.yaml`:

```yaml
logger:
  level: info

db_uri: mongodb://10.53.1.10/open5gs

pcf:
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
    client:
      nrf:
        - uri: http://10.53.1.4:7777
      scp:
        - uri: http://10.53.1.5:7777
  metrics:
    server:
      - address: 0.0.0.0
        port: 9090
```

- [ ] **Step 7: Write `bsf.yaml`**

Write `benchmark/provision/open5gs-core/compose/configs/bsf.yaml`:

```yaml
logger:
  level: info

bsf:
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
    client:
      nrf:
        - uri: http://10.53.1.4:7777
      scp:
        - uri: http://10.53.1.5:7777
```

- [ ] **Step 8: Lint all seven via `python -c yaml.safe_load`**

Run:
```bash
python3 -c "
import yaml, pathlib
d = pathlib.Path('benchmark/provision/open5gs-core/compose/configs')
for p in sorted(d.glob('*.yaml')):
    yaml.safe_load(p.read_text())
    print('ok:', p.name)
"
```
Expected: 7 lines `ok: <nf>.yaml`. If `yaml` isn't installed, run `pip install --user pyyaml` first.

- [ ] **Step 9: Commit**

```bash
git add benchmark/provision/open5gs-core/compose/configs/nrf.yaml \
        benchmark/provision/open5gs-core/compose/configs/scp.yaml \
        benchmark/provision/open5gs-core/compose/configs/ausf.yaml \
        benchmark/provision/open5gs-core/compose/configs/udm.yaml \
        benchmark/provision/open5gs-core/compose/configs/udr.yaml \
        benchmark/provision/open5gs-core/compose/configs/pcf.yaml \
        benchmark/provision/open5gs-core/compose/configs/bsf.yaml
git commit -m "provision/open5gs-core: SBI-only NF configs (NRF, SCP, AUSF, UDM, UDR, PCF, BSF)

All bind SBI on 0.0.0.0:7777 inside the container; UDR/PCF point at
mongo at 10.53.1.10; everyone else clients NRF at 10.53.1.4 and SCP
at 10.53.1.5. PLMN MCC=001 MNC=01 (NRF only carries the serving list).
"
```

---

### Task 6: Per-NF YAML configs — data plane (SMF, UPF)

**Files:**
- Create: `benchmark/provision/open5gs-core/compose/configs/smf.yaml`
- Create: `benchmark/provision/open5gs-core/compose/configs/upf.yaml`

- [ ] **Step 1: Write `smf.yaml`**

Write `benchmark/provision/open5gs-core/compose/configs/smf.yaml`:

```yaml
logger:
  level: info

smf:
  info:
    - s_nssai:
        - sst: 1
          dnn:
            - internet
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
    client:
      nrf:
        - uri: http://10.53.1.4:7777
      scp:
        - uri: http://10.53.1.5:7777
  pfcp:
    server:
      - address: 0.0.0.0
    client:
      upf:
        - address: 10.53.1.12
  gtpc:
    server:
      - address: 0.0.0.0
  gtpu:
    server:
      - address: 0.0.0.0
  session:
    - subnet: 10.45.0.0/16
      gateway: 10.45.0.1
      dnn: internet
  dns:
    - 8.8.8.8
    - 1.1.1.1
  mtu: 1400
```

- [ ] **Step 2: Write `upf.yaml`**

Write `benchmark/provision/open5gs-core/compose/configs/upf.yaml`:

```yaml
logger:
  level: info

upf:
  pfcp:
    server:
      - address: 0.0.0.0
    client:
      smf:
        - address: 10.53.1.3
  gtpu:
    server:
      - address: 0.0.0.0
  session:
    - subnet: 10.45.0.0/16
      gateway: 10.45.0.1
      dnn: internet
  metrics:
    server:
      - address: 0.0.0.0
        port: 9090
```

- [ ] **Step 3: YAML-validate both**

Run:
```bash
python3 -c "
import yaml, pathlib
for n in ('smf', 'upf'):
    p = pathlib.Path(f'benchmark/provision/open5gs-core/compose/configs/{n}.yaml')
    yaml.safe_load(p.read_text())
    print('ok:', p.name)
"
```
Expected: two lines `ok: smf.yaml` / `ok: upf.yaml`.

- [ ] **Step 4: Commit**

```bash
git add benchmark/provision/open5gs-core/compose/configs/smf.yaml \
        benchmark/provision/open5gs-core/compose/configs/upf.yaml
git commit -m "provision/open5gs-core: SMF + UPF configs (data plane)

SMF binds SBI/PFCP/GTPC/GTPU on 0.0.0.0; PFCP client points at UPF
at 10.53.1.12; session subnet 10.45.0.0/16 for UE IPs; DNS 8.8.8.8.
UPF mirrors SMF with PFCP client pointing back at SMF at 10.53.1.3.
"
```

---

### Task 7: Per-NF YAML config — AMF

**Files:**
- Create: `benchmark/provision/open5gs-core/compose/configs/amf.yaml`

AMF is the most config-heavy NF — NGAP server, GUAMI, served TAI list, served PLMN with slice support, NAS / security algorithms, access control. Match the seeded subscriber's PLMN/slice exactly.

- [ ] **Step 1: Write `amf.yaml`**

Write `benchmark/provision/open5gs-core/compose/configs/amf.yaml`:

```yaml
logger:
  level: info

amf:
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
    client:
      nrf:
        - uri: http://10.53.1.4:7777
      scp:
        - uri: http://10.53.1.5:7777
  ngap:
    server:
      - address: 0.0.0.0
  metrics:
    server:
      - address: 0.0.0.0
        port: 9090
  guami:
    - plmn_id:
        mcc: 001
        mnc: 01
      amf_id:
        region: 2
        set: 1
  tai:
    - plmn_id:
        mcc: 001
        mnc: 01
      tac: 1
  plmn_support:
    - plmn_id:
        mcc: 001
        mnc: 01
      s_nssai:
        - sst: 1
  security:
    integrity_order:
      - NIA2
      - NIA1
      - NIA0
    ciphering_order:
      - NEA0
      - NEA1
      - NEA2
  network_name:
    full: Open5GS
    short: Next
  amf_name: open5gs-amf0
  time:
    t3512:
      value: 540
```

- [ ] **Step 2: YAML-validate**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('benchmark/provision/open5gs-core/compose/configs/amf.yaml').read())" \
    && echo ok
```
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add benchmark/provision/open5gs-core/compose/configs/amf.yaml
git commit -m "provision/open5gs-core: AMF config (NGAP on 0.0.0.0, PLMN/TAC/slice match seeded UE)

GUAMI region 2 set 1; served TAI PLMN 00101 TAC 1; plmn_support
includes s-NSSAI sst=1 (matches seeded subscriber's slice).
Standard NAS security order NIA2/NIA1/NIA0 + NEA0/NEA1/NEA2.
"
```

---

### Task 8: docker-compose.open5gs.yml

**Files:**
- Create: `benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml`

- [ ] **Step 1: Write the compose file**

Write `benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml`:

```yaml
# Open5GS 5G core, split-NF deployment.
# Bring up:  docker compose -f docker-compose.open5gs.yml up -d
# Verify:    bash ../tests/check_core_ready.sh docker-compose.open5gs.yml
# Tear down: docker compose -f docker-compose.open5gs.yml down -v
#
# See benchmark/docs/specs/2026-05-24-open5gs-core-design.md for the full
# design rationale (split-NF, upstream gradiant images, static IPs, mongo
# loopback, NET_ADMIN caps, etc.).

name: open5gs

networks:
  ran:
    driver: bridge
    ipam:
      driver: default
      config:
        - subnet: 10.53.1.0/24

volumes:
  mongo-data: {}

x-nf-defaults: &nf-defaults
  restart: unless-stopped
  pull_policy: missing
  networks: [ran]

services:

  mongo:
    image: mongo:7
    container_name: open5gs-mongo
    restart: unless-stopped
    pull_policy: missing
    networks:
      ran:
        ipv4_address: 10.53.1.10
    ports:
      - "127.0.0.1:27017:27017/tcp"
    volumes:
      - mongo-data:/data/db
    healthcheck:
      test: ["CMD-SHELL", "mongosh --quiet --eval 'db.adminCommand({ping:1}).ok' | grep -q 1"]
      interval: 5s
      timeout: 3s
      retries: 24

  subscriber-seeder:
    build: ./seed
    container_name: open5gs-subscriber-seeder
    pull_policy: missing
    restart: "no"
    networks: [ran]
    depends_on:
      mongo: { condition: service_healthy }
    environment:
      MONGO_URI: mongodb://10.53.1.10:27017/
      MONGO_DB: open5gs
      SUBSCRIBER_CSV: /app/subscriber_db.csv

  nrf:
    <<: *nf-defaults
    image: gradiant/open5gs-nrf:2.7.0
    container_name: open5gs-nrf
    command: ["-c", "/opt/open5gs/etc/open5gs/nrf.yaml"]
    networks:
      ran:
        ipv4_address: 10.53.1.4
    volumes:
      - ./configs/nrf.yaml:/opt/open5gs/etc/open5gs/nrf.yaml:ro
    depends_on:
      mongo: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :7777"]
      interval: 5s
      timeout: 2s
      retries: 12

  scp:
    <<: *nf-defaults
    image: gradiant/open5gs-scp:2.7.0
    container_name: open5gs-scp
    command: ["-c", "/opt/open5gs/etc/open5gs/scp.yaml"]
    networks:
      ran:
        ipv4_address: 10.53.1.5
    volumes:
      - ./configs/scp.yaml:/opt/open5gs/etc/open5gs/scp.yaml:ro
    depends_on:
      nrf: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :7777"]
      interval: 5s
      timeout: 2s
      retries: 12

  ausf:
    <<: *nf-defaults
    image: gradiant/open5gs-ausf:2.7.0
    container_name: open5gs-ausf
    command: ["-c", "/opt/open5gs/etc/open5gs/ausf.yaml"]
    networks:
      ran:
        ipv4_address: 10.53.1.6
    volumes:
      - ./configs/ausf.yaml:/opt/open5gs/etc/open5gs/ausf.yaml:ro
    depends_on:
      nrf: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :7777"]
      interval: 5s
      timeout: 2s
      retries: 12

  udm:
    <<: *nf-defaults
    image: gradiant/open5gs-udm:2.7.0
    container_name: open5gs-udm
    command: ["-c", "/opt/open5gs/etc/open5gs/udm.yaml"]
    networks:
      ran:
        ipv4_address: 10.53.1.7
    volumes:
      - ./configs/udm.yaml:/opt/open5gs/etc/open5gs/udm.yaml:ro
    depends_on:
      nrf: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :7777"]
      interval: 5s
      timeout: 2s
      retries: 12

  udr:
    <<: *nf-defaults
    image: gradiant/open5gs-udr:2.7.0
    container_name: open5gs-udr
    command: ["-c", "/opt/open5gs/etc/open5gs/udr.yaml"]
    networks:
      ran:
        ipv4_address: 10.53.1.8
    volumes:
      - ./configs/udr.yaml:/opt/open5gs/etc/open5gs/udr.yaml:ro
    depends_on:
      nrf: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :7777"]
      interval: 5s
      timeout: 2s
      retries: 12

  pcf:
    <<: *nf-defaults
    image: gradiant/open5gs-pcf:2.7.0
    container_name: open5gs-pcf
    command: ["-c", "/opt/open5gs/etc/open5gs/pcf.yaml"]
    networks:
      ran:
        ipv4_address: 10.53.1.9
    volumes:
      - ./configs/pcf.yaml:/opt/open5gs/etc/open5gs/pcf.yaml:ro
    depends_on:
      nrf: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :7777"]
      interval: 5s
      timeout: 2s
      retries: 12

  bsf:
    <<: *nf-defaults
    image: gradiant/open5gs-bsf:2.7.0
    container_name: open5gs-bsf
    command: ["-c", "/opt/open5gs/etc/open5gs/bsf.yaml"]
    networks:
      ran:
        ipv4_address: 10.53.1.11
    volumes:
      - ./configs/bsf.yaml:/opt/open5gs/etc/open5gs/bsf.yaml:ro
    depends_on:
      nrf: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :7777"]
      interval: 5s
      timeout: 2s
      retries: 12

  upf:
    <<: *nf-defaults
    image: gradiant/open5gs-upf:2.7.0
    container_name: open5gs-upf
    command: ["-c", "/opt/open5gs/etc/open5gs/upf.yaml"]
    cap_add: [NET_ADMIN]
    networks:
      ran:
        ipv4_address: 10.53.1.12
    volumes:
      - ./configs/upf.yaml:/opt/open5gs/etc/open5gs/upf.yaml:ro
    depends_on:
      nrf: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :8805 || ss -unl4 | grep -q :8805"]
      interval: 5s
      timeout: 2s
      retries: 12

  smf:
    <<: *nf-defaults
    image: gradiant/open5gs-smf:2.7.0
    container_name: open5gs-smf
    command: ["-c", "/opt/open5gs/etc/open5gs/smf.yaml"]
    cap_add: [NET_ADMIN]
    networks:
      ran:
        ipv4_address: 10.53.1.3
    volumes:
      - ./configs/smf.yaml:/opt/open5gs/etc/open5gs/smf.yaml:ro
    depends_on:
      nrf: { condition: service_healthy }
      upf: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :7777"]
      interval: 5s
      timeout: 2s
      retries: 12

  amf:
    <<: *nf-defaults
    image: gradiant/open5gs-amf:2.7.0
    container_name: open5gs-amf
    command: ["-c", "/opt/open5gs/etc/open5gs/amf.yaml"]
    cap_add: [NET_ADMIN]
    networks:
      ran:
        ipv4_address: 10.53.1.2
    ports:
      - "38412:38412/sctp"
    volumes:
      - ./configs/amf.yaml:/opt/open5gs/etc/open5gs/amf.yaml:ro
    depends_on:
      nrf: { condition: service_healthy }
      smf: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "ss -tnl4 | grep -q :7777 && ss -ln | grep -q :38412"]
      interval: 5s
      timeout: 3s
      retries: 24
```

- [ ] **Step 2: Validate the compose file syntactically**

Run:
```bash
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml config --quiet
```
Expected: exits 0 with no output. (If `docker` isn't on the Mac PATH, run on 5090pc via `ssh zhouyou@10.34.23.184 'cd ~/skillful-ran-workspace && docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml config --quiet'`.)

- [ ] **Step 3: Commit**

```bash
git add benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml
git commit -m "provision/open5gs-core: docker-compose.open5gs.yml (split-NF stack)

11 long-running services (mongo + 10 NFs) + 1 one-shot seeder on
network ran (10.53.1.0/24) with static per-NF IPs. AMF NGAP SCTP
38412 published to host; mongo TCP 27017 published to 127.0.0.1
only. cap_add NET_ADMIN on AMF/SMF/UPF for future tc-netem stimulus.
Healthchecks gate startup order via depends_on/service_healthy.
Compose project name pinned to 'open5gs' for stable -p access.
"
```

---

### Task 9: Acceptance script `check_core_ready.sh`

**Files:**
- Create: `benchmark/provision/open5gs-core/tests/check_core_ready.sh`

- [ ] **Step 1: Write the script**

Write `benchmark/provision/open5gs-core/tests/check_core_ready.sh`:

```bash
#!/usr/bin/env bash
# All-in-one health verification for the Open5GS core compose stack.
# Returns 0 if all 5 checks pass, non-zero otherwise.
# See benchmark/docs/specs/2026-05-24-open5gs-core-design.md §7.2 for rationale.

set -euo pipefail

COMPOSE_FILE="${1:-benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml}"

# 1. all compose services are running and healthy (or have no healthcheck)
docker compose -f "$COMPOSE_FILE" ps --format json \
  | jq -se 'all(. as $s | ($s.State == "running" or $s.State == "exited") and ($s.Health == "healthy" or $s.Health == "" or $s.Health == null))' \
  || { echo "check 1 FAIL: not all services healthy"; exit 1; }

# 2. SCTP port 38412 bound inside AMF container
docker compose -f "$COMPOSE_FILE" exec -T amf ss -ln 2>/dev/null | grep -q ':38412' \
  || { echo "check 2 FAIL: AMF SCTP 38412 not bound"; exit 1; }

# 3. AMF log contains successful NF registration with NRF
docker compose -f "$COMPOSE_FILE" logs --no-color amf 2>/dev/null | grep -qiE '(NF registered|NF Profile|registered to NRF)' \
  || { echo "check 3 FAIL: AMF has not registered with NRF"; exit 1; }

# 4. seeded subscriber queryable via mongo
docker compose -f "$COMPOSE_FILE" exec -T mongo mongosh --quiet open5gs \
  --eval 'db.subscribers.findOne({imsi: "001010000000001"})' 2>/dev/null | grep -q 'imsi' \
  || { echo "check 4 FAIL: seeded subscriber not found in mongo"; exit 1; }

# 5. mongo reachable on host loopback (required by future control APIs)
python3 -c 'import socket; s=socket.socket(); s.settimeout(2); s.connect(("127.0.0.1", 27017)); s.close()' \
  || { echo "check 5 FAIL: mongo not reachable on 127.0.0.1:27017"; exit 1; }

echo "open5gs core ready"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x benchmark/provision/open5gs-core/tests/check_core_ready.sh`

- [ ] **Step 3: Lint the script with shellcheck (if available)**

Run: `shellcheck benchmark/provision/open5gs-core/tests/check_core_ready.sh 2>&1 || echo "(shellcheck not installed; skipping)"`
Expected: no warnings — or "(shellcheck not installed; skipping)". Fix any SC warnings that come up.

- [ ] **Step 4: Verify the script's basic shape (no execution yet — needs running compose)**

Run: `bash -n benchmark/provision/open5gs-core/tests/check_core_ready.sh && echo "bash syntax ok"`
Expected: `bash syntax ok`.

- [ ] **Step 5: Commit**

```bash
git add benchmark/provision/open5gs-core/tests/check_core_ready.sh
git commit -m "provision/open5gs-core: check_core_ready.sh — 5-check acceptance script

Verifies (1) all services running+healthy, (2) AMF SCTP 38412 bound,
(3) AMF NF-registration log line present, (4) seeded subscriber
queryable, (5) mongo reachable on 127.0.0.1:27017 from host.

Returns 0 only if all pass. Also doubles as the readiness check that
the future live-runtime adapter will call from conformance.py.
"
```

---

### Task 10: README rewrite + CLAUDE.md update

**Files:**
- Modify (rewrite): `benchmark/provision/open5gs-core/README.md`
- Modify: `CLAUDE.md`

There's currently no `benchmark/provision/open5gs-core/README.md` — the README that exists is at the parent `benchmark/provision/README.md`. We're adding a sub-README scoped to this slice.

- [ ] **Step 1: Write the new sub-README**

Write `benchmark/provision/open5gs-core/README.md`:

```markdown
# Open5GS 5G core (split-NF)

Real, deployable Open5GS 5G core for the OCUDUAgentBench live-runtime path.
Brings up 11 containers (`mongo` + 10 Open5GS NFs from
`gradiant/open5gs-*:2.7.0`) + a one-shot subscriber seeder.

For the why behind the choices (split-NF vs all-in-one, upstream images vs
build-from-source, mongo on loopback, NET_ADMIN cap on AMF/SMF/UPF, etc.),
see [`benchmark/docs/specs/2026-05-24-open5gs-core-design.md`](../../docs/specs/2026-05-24-open5gs-core-design.md).

## Bring up

```bash
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml up -d
```

`docker compose ps` should show all 11 services `running` (10 NFs + mongo)
plus `open5gs-subscriber-seeder` as `exited (0)` within ~30 s.

## Verify

```bash
bash benchmark/provision/open5gs-core/tests/check_core_ready.sh
# expected: "open5gs core ready"
```

The script runs five checks: all services healthy, AMF SCTP 38412 bound,
AMF registered with NRF, seeded subscriber queryable, mongo reachable on
`127.0.0.1:27017`.

## Tear down

```bash
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml down -v
```

The `-v` removes the `mongo-data` volume; subscribers re-seeded on next bring-up.

## Static IP map

A future OCUDU gNB attaches to AMF NGAP at `10.53.1.2:38412` (or via host
`38412/sctp`).

| NF | Static IP | Listens on |
| --- | --- | --- |
| `mongo` | `10.53.1.10` | `27017/tcp` (also published to host `127.0.0.1:27017`) |
| `amf` | `10.53.1.2` | `7777/tcp` (SBI), `38412/sctp` (NGAP; published to host `38412/sctp`) |
| `smf` | `10.53.1.3` | `7777/tcp` (SBI), `8805/udp` (PFCP), `2152/udp` (GTP-U) |
| `nrf` | `10.53.1.4` | `7777/tcp` (SBI) |
| `scp` | `10.53.1.5` | `7777/tcp` (SBI) |
| `ausf` | `10.53.1.6` | `7777/tcp` (SBI) |
| `udm` | `10.53.1.7` | `7777/tcp` (SBI) |
| `udr` | `10.53.1.8` | `7777/tcp` (SBI) |
| `pcf` | `10.53.1.9` | `7777/tcp` (SBI), `9090/tcp` (metrics) |
| `bsf` | `10.53.1.11` | `7777/tcp` (SBI) |
| `upf` | `10.53.1.12` | `8805/udp` (PFCP), `2152/udp` (GTP-U), `9090/tcp` (metrics) |

## Seeded UE

The seeder loads `compose/seed/subscriber_db.csv`. Default content is one
test UE matching the standard srsRAN/Open5GS ZMQ triplet (IMSI
`001010000000001`, K `0011..eeff`, OPc `63bf..737d`, PLMN `00101`, DNN
`internet`, SST 1, no SD). Add more rows to the CSV and re-run
`docker compose up -d --build subscriber-seeder` to upsert them.

## Future control APIs enabled by this slice

Per spec §9, this slice enables (but does not implement) the following
benchmark control surfaces in the future live transport:

* `RESTART_CORE_NF` — `docker compose -p open5gs restart <nf>`
* `UPDATE_CORE_UE_REGISTRATION` — pymongo upsert via `127.0.0.1:27017`
* `core_latency_profile` stimulus — `tc netem` inside AMF/SMF/UPF via NET_ADMIN
* `core_ue_registration_misconfig` stimulus — pymongo write of mismatched subscriber
* `core_runtime` observation — `docker compose -p open5gs ps --format json` + pymongo read

## Not in this slice

OCUDU gNB, srsUE, FlexRIC, the live transport code in
`benchmark/benchmark_api/`, and any task manifests declaring
`E.runtime_adapter = ocudu_live` are explicitly out of scope. See spec §10.
```

- [ ] **Step 2: Update CLAUDE.md to reflect the new state of Open5GS core**

Read `CLAUDE.md` first, then replace the `Live OCUDU WebSocket / CLI / E2 / Core` row in the OCUDU API status table with a more precise statement that Open5GS core is now deployable but the others are still scaffolds.

Use the Edit tool with this exact `old_string`:

```text
| Live OCUDU WebSocket / CLI / E2 / Core | ❌ not wired | scaffolds at `benchmark/provision/`; OCUDU build tree at `~/skillful-ran-benchmark-workspace/ocudu/{build,install}/` (root-owned, untouched) |
```

and `new_string`:

```text
| Live Open5GS 5G core | ⚠ **deployable** | `benchmark/provision/open5gs-core/` ships a split-NF docker compose + acceptance script; not yet wired to any task manifest. See [spec 2026-05-24-open5gs-core-design.md](skillful-ran-research/benchmark_design/) — actually `benchmark/docs/specs/2026-05-24-open5gs-core-design.md`. |
| Live OCUDU WebSocket / CLI / E2 | ❌ not wired | scaffolds at `benchmark/provision/`; OCUDU build tree at `~/skillful-ran-benchmark-workspace/ocudu/{build,install}/` (root-owned, untouched) |
```

(Note: if the exact `old_string` doesn't match the current CLAUDE.md verbatim, run `cat CLAUDE.md | grep -n "Live OCUDU"` first to find the exact line, then use Edit with the verified text.)

- [ ] **Step 3: Commit README + CLAUDE.md updates together**

```bash
git add benchmark/provision/open5gs-core/README.md CLAUDE.md
git commit -m "provision/open5gs-core: sub-README + CLAUDE.md status update

README documents bring-up, verify, tear-down, static-IP map, seeded UE,
and the future-control-API mapping from spec §9.

CLAUDE.md OCUDU-status table now distinguishes Open5GS core
(deployable from repo) from the still-unwired live OCUDU surfaces.
"
```

---

### Task 11: End-to-end validation on 5090pc

**Files:** none modified; this is the integration test.

- [ ] **Step 1: Push the work to GitHub**

```bash
git status                              # sanity: working tree should be clean after Task 10
git log --oneline -10                   # sanity: should see 10 commits from Tasks 1-10
git push origin main
```
Expected: push succeeds, all 10 commits land on `origin/main`.

- [ ] **Step 2: Pull on 5090pc**

Run:
```bash
ssh -n -o BatchMode=yes -i ~/.ssh/zhouyou5090pc zhouyou@10.34.23.184 \
    'cd ~/skillful-ran-workspace && git fetch origin && git reset --hard origin/main && git log --oneline -3'
```
Expected: HEAD now matches local; last 3 commits visible.

- [ ] **Step 3: Bring the stack up on 5090pc**

Run:
```bash
ssh -n -o BatchMode=yes -i ~/.ssh/zhouyou5090pc zhouyou@10.34.23.184 '
cd ~/skillful-ran-workspace
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml up -d --build
echo "--- compose ps ---"
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml ps
'
```
Expected: 12 services listed; mongo+NFs `running`+`healthy`; subscriber-seeder `exited (0)`. First-time bring-up may take ~30-60 s while images are pulled and the seeder image is built.

- [ ] **Step 4: Wait for full health (poll for up to 2 min)**

Run:
```bash
ssh -n -o BatchMode=yes -i ~/.ssh/zhouyou5090pc zhouyou@10.34.23.184 '
cd ~/skillful-ran-workspace
DEADLINE=$(( $(date +%s) + 120 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  COUNT_UNHEALTHY=$(docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml ps --format json | jq -r ".[] | select(.Health != \"\" and .Health != null and .Health != \"healthy\") | .Name" | wc -l)
  if [ "$COUNT_UNHEALTHY" -eq 0 ]; then echo READY; break; fi
  sleep 5
done
'
```
Expected: prints `READY` within 2 min.

- [ ] **Step 5: Run the acceptance script**

Run:
```bash
ssh -n -o BatchMode=yes -i ~/.ssh/zhouyou5090pc zhouyou@10.34.23.184 '
cd ~/skillful-ran-workspace
bash benchmark/provision/open5gs-core/tests/check_core_ready.sh
'
```
Expected: `open5gs core ready` printed, exit 0. If any check fails, the script prints `check <n> FAIL: <reason>` and exits non-zero — investigate that specific NF's logs via `docker compose logs <nf>`.

- [ ] **Step 6: Verify the future-API contact points are exercisable**

Run the live spot-checks of each future-API enabling primitive (per spec §9.1's "Verified by" column):

```bash
ssh -n -o BatchMode=yes -i ~/.ssh/zhouyou5090pc zhouyou@10.34.23.184 '
cd ~/skillful-ran-workspace
echo "--- compose project name discoverable as open5gs ---"
docker compose -p open5gs ps --services | sort
echo "--- mongo loopback reachable from host ---"
python3 -c "import socket; s=socket.socket(); s.settimeout(2); s.connect((\"127.0.0.1\", 27017)); s.close(); print(\"ok\")"
echo "--- NET_ADMIN cap available in amf ---"
docker compose -p open5gs exec -T amf tc qdisc show dev eth0
echo "--- amf NGAP SCTP 38412 reachable from host ---"
ss -nl | grep -E ":38412" || echo "(not in host ss output; check sudo ss -nl)"
'
```
Expected: 11 service names listed; `ok` for mongo; `qdisc noqueue 0: root refcnt 2` (or similar) for AMF tc; SCTP listener line for AMF.

- [ ] **Step 7: Tear down cleanly**

Run:
```bash
ssh -n -o BatchMode=yes -i ~/.ssh/zhouyou5090pc zhouyou@10.34.23.184 '
cd ~/skillful-ran-workspace
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml down -v
docker volume ls | grep open5gs && echo WARN: leftover volume || echo "clean teardown"
'
```
Expected: every container removed, `clean teardown` printed.

- [ ] **Step 8: If anything failed in steps 3-7, iterate**

This step has no commit. Symptom → likely cause → fix-and-retry-from-step-3 cycle:

| Symptom | Probable cause | Where to look |
| --- | --- | --- |
| `gradiant/open5gs-<nf>:2.7.0: not found` | Image tag drift | Pin to the closest available tag on `hub.docker.com/r/gradiant/open5gs-<nf>/tags`; update compose; retry |
| NRF healthy but AMF unhealthy | YAML config field name mismatch with 2.7.0 schema | `docker compose -p open5gs logs amf`; look for `parse error`; fix `configs/amf.yaml` |
| Seeder exits non-zero | Mongo schema mismatch | `docker compose -p open5gs logs subscriber-seeder`; fix `add_users.py` mapping; rerun `--build subscriber-seeder` |
| AMF SCTP not bound | NGAP server config typo | check `configs/amf.yaml` `ngap.server` block matches the gradiant 2.7.0 schema |
| `mongo` healthcheck fails | older `mongo:7` image has no `mongosh` | switch healthcheck to `mongo --eval ...` (older) or pin `mongo:7.0.14` |

Re-commit only the actual fix; squash unhelpful "wip" commits before pushing.

- [ ] **Step 9: Final commit if anything was iterated**

If any code changed during Step 8:

```bash
git add <files>
git commit -m "provision/open5gs-core: fix <specific issue>"
git push origin main
```

Otherwise this step is a no-op.

- [ ] **Step 10: Verify Definition-of-Done items**

Final cross-check against spec §8 (all 7 items must be ✓):

1. All files in spec §4.4 exist, stubs deleted, `git diff --check` clean → run `git status` (should be clean) + verify §4.4 files all listed in `git ls-files benchmark/provision/open5gs-core/`
2. `docker compose ... up -d` exits 0, all 12 services as expected → demonstrated in Step 3
3. `bash .../tests/check_core_ready.sh` prints `open5gs core ready` exit 0 → Step 5
4. `docker compose ... down -v` clean → Step 7
5. README at `benchmark/provision/open5gs-core/README.md` documents bring-up/tear-down/acceptance/static-IP map → Task 10 Step 1
6. CLAUDE.md updated → Task 10 Step 2
7. Mongo loopback reachable → acceptance script check 5 in Step 5

If all 7 pass, the slice is done. If any fail, that one becomes the next task.

---

## Self-Review (run by writer after plan written)

**Spec coverage:**

- §1 Context — informs the plan header; not an implementable item ✓
- §2 Decisions D1-D5 — all five are baked into the file choices (split NFs → 10 NF services in Task 8 compose; upstream images → no Dockerfile per NF; static IPs → ipv4_address blocks; CSV+pymongo → Tasks 2-4; process+port+log+sub+mongo done → Task 9 acceptance script's 5 checks) ✓
- §3 Architecture — realized by Task 8 (compose) ✓
- §4 Components — §4.1 by Tasks 5-7; §4.2 by Tasks 2-4; §4.3 by Task 8's healthchecks; §4.4 file layout matches plan's file-structure header ✓
- §5 Data flow — verified by Task 11 Steps 4-5 ✓
- §6 Error handling — restart policies in Task 8; symptom table in Task 11 Step 8 ✓
- §7 Testing — Task 9 (acceptance script), Task 2 (unit tests), Task 11 (integration) ✓
- §8 Definition of Done — all 7 items checked in Task 11 Step 10 ✓
- §9 Integration with later control APIs — primitives baked in (compose name, loopback mongo, NET_ADMIN, host SCTP), verified by Task 11 Step 6 ✓
- §10 Not in this slice — explicitly out of plan; nothing implemented ✓
- §11 Risks — symptom table in Task 11 Step 8 covers the listed risks ✓

**Placeholder scan:** no "TBD", no "add appropriate error handling", every code step has a code block with literal content, every command step has literal command + expected output. ✓

**Type consistency:** `subscriber_document` named identically in `test_subscriber_document.py`, `add_users.py`, the call sites in Task 2 Step 3, and the spec §4.2; all CSV column names (`imsi, k, opc, amf, sqn, plmn, dnn, sst, sd, auth_profile_id, ue_id`) match the CSV in Task 4, the test fixture in Task 2, and the spec §4.2; compose project name `open5gs` consistent across compose `name:` field (Task 8), README (Task 10), spec §9.2, and verification command (Task 11 Step 6). ✓

No issues found.
