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
docker compose -f "$COMPOSE_FILE" logs --no-color amf 2>/dev/null | grep -iE '(NF registered|NF Profile|registered to NRF)' >/dev/null \
  || { echo "check 3 FAIL: AMF has not registered with NRF"; exit 1; }

# 4. seeded subscriber queryable via mongo
docker compose -f "$COMPOSE_FILE" exec -T mongo mongosh --quiet open5gs \
  --eval 'db.subscribers.findOne({imsi: "001010000000001"})' 2>/dev/null | grep -q 'imsi' \
  || { echo "check 4 FAIL: seeded subscriber not found in mongo"; exit 1; }

# 5. mongo reachable on host loopback (required by future control APIs)
python3 -c 'import socket; s=socket.socket(); s.settimeout(2); s.connect(("127.0.0.1", 27017)); s.close()' \
  || { echo "check 5 FAIL: mongo not reachable on 127.0.0.1:27017"; exit 1; }

echo "open5gs core ready"
