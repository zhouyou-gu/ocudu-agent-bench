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
