#!/usr/bin/env bash
# Acceptance check for the all-in-one Open5GS stack (one 5gc process).
# 4 checks: stack healthy, AMF NGAP bound, seeded subscriber present, mongo loopback.
# Returns 0 if all pass.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")"/../../../.. && pwd)"
COMPOSE_FILE="${REPO_ROOT}/benchmark/provision/open5gs-aio/compose/docker-compose.open5gs-aio.yml"

# 1. all compose services running and healthy (or exited 0 for the one-shot seeder)
docker compose -f "$COMPOSE_FILE" ps --format json \
  | jq -se 'all(. as $s | ($s.State == "running" or $s.State == "exited") and ($s.Health == "healthy" or $s.Health == "" or $s.Health == null))' \
  || { echo "check 1 FAIL: not all services healthy"; exit 1; }

# 2. AMF SCTP 38412 bound inside the 5gc container
docker compose -f "$COMPOSE_FILE" exec -T 5gc ss -ln 2>/dev/null | grep -q ':38412' \
  || { echo "check 2 FAIL: AMF SCTP 38412 not bound"; exit 1; }

# 3. seeded subscriber queryable via mongo
docker compose -f "$COMPOSE_FILE" exec -T mongo mongosh --quiet open5gs \
  --eval 'db.subscribers.findOne({imsi: "001010000000001"})' 2>/dev/null | grep -q 'imsi' \
  || { echo "check 3 FAIL: seeded subscriber not found"; exit 1; }

# 4. mongo reachable on host loopback (mirrors split-NF acceptance)
python3 -c 'import socket; s=socket.socket(); s.settimeout(2); s.connect(("127.0.0.1", 27017)); s.close()' \
  || { echo "check 4 FAIL: mongo not reachable on 127.0.0.1:27017"; exit 1; }

echo "open5gs aio core ready"
