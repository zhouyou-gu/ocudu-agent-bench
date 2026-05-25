#!/usr/bin/env bash
# Acceptance check for the OCUDU gNB + srsUE pair.
#
# Pre-condition: the open5gs core stack must already be up (so the external
# network open5gs_ran exists and AMF NGAP at 10.53.1.2:38412 is reachable).
#
# Sequence:
#   1. gnb container is running
#   2. gnb log shows AMF NGAP connection established
#   3. ue container is running
#   4. ue log shows successful network attach (PDU session established)
#   5. ue can ping 10.45.1.1 (the SMF session gateway) from inside netns ue1
#
# Returns 0 if all 5 pass.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")"/../../../.. && pwd)"
GNB_UE_COMPOSE="${REPO_ROOT}/benchmark/provision/ocudu-gnb-ue/compose/docker-compose.gnb-ue.yml"
PROJECT=ocudu_gnb_ue

# 1. gnb container running
docker compose -f "$GNB_UE_COMPOSE" ps gnb --format json \
  | python3 -c 'import sys,json; d=json.loads(sys.stdin.read() or "{}"); sys.exit(0 if d.get("State")=="running" else 1)' \
  || { echo "check 1 FAIL: gnb container not running"; exit 1; }

# 2. gnb log shows AMF connection (NGAP setup successful)
docker compose -f "$GNB_UE_COMPOSE" logs --no-color gnb 2>/dev/null \
  | grep -qiE 'NGAP setup procedure completed|NG setup procedure successful|AMF connection established|connection to AMF established' \
  || { echo "check 2 FAIL: gnb has not established AMF connection"; exit 1; }

# 3. ue container running
docker compose -f "$GNB_UE_COMPOSE" ps ue --format json \
  | python3 -c 'import sys,json; d=json.loads(sys.stdin.read() or "{}"); sys.exit(0 if d.get("State")=="running" else 1)' \
  || { echo "check 3 FAIL: ue container not running"; exit 1; }

# 4. ue log shows attach (registration accepted + PDU session OK)
docker compose -f "$GNB_UE_COMPOSE" logs --no-color ue 2>/dev/null \
  | grep -qiE 'PDU session establishment successful|PDU Session Establishment Accept|Random Access Complete.*ra-rnti|Network attach successful' \
  || { echo "check 4 FAIL: ue has not completed network attach"; exit 1; }

# 5. ue can ping the SMF session gateway from inside its netns
docker compose -f "$GNB_UE_COMPOSE" exec -T ue ip netns exec ue1 ping -c 2 -W 3 10.45.1.1 >/dev/null 2>&1 \
  || { echo "check 5 FAIL: ue cannot ping 10.45.1.1"; exit 1; }

echo "ocudu gnb + srsue attached and ping ok"
