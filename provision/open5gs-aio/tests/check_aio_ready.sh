#!/usr/bin/env bash
# Acceptance check for the all-in-one Open5GS stack.
# 3 checks: 5gc running, AMF SCTP bound, seeded subscriber present in the
# in-container mongo. Returns 0 if all pass.
#
# The AIO stack runs mongo INSIDE the 5gc container (per its entrypoint),
# so the loopback 127.0.0.1:27017 check from the split-NF acceptance does
# not apply here -- mongo isn't host-exposed for the AIO stack. live_core
# is NOT supposed to target this stack (see open5gs-aio/README.md).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")"/../../../.. && pwd)"
COMPOSE_FILE="${REPO_ROOT}/benchmark/provision/open5gs-aio/compose/docker-compose.open5gs-aio.yml"

# 1. 5gc container running and healthy
docker compose -f "$COMPOSE_FILE" ps 5gc --format json \
  | python3 -c 'import sys,json; d=json.loads(sys.stdin.read() or "{}"); s=d.get("State"); h=d.get("Health",""); sys.exit(0 if s=="running" and h in ("healthy","") else 1)' \
  || { echo "check 1 FAIL: 5gc container not running+healthy"; exit 1; }

# 2. AMF SCTP 38412 bound inside the 5gc container
docker compose -f "$COMPOSE_FILE" exec -T 5gc ss -ln 2>/dev/null | grep -q ':38412' \
  || { echo "check 2 FAIL: AMF SCTP 38412 not bound"; exit 1; }

# 3. seeded subscriber queryable in the embedded mongo
docker compose -f "$COMPOSE_FILE" exec -T 5gc mongosh --quiet open5gs \
  --eval 'db.subscribers.findOne({imsi: "001010000000001"})' 2>/dev/null | grep -q 'imsi' \
  || { echo "check 3 FAIL: seeded subscriber not found in embedded mongo"; exit 1; }

echo "open5gs aio core ready"
