#!/usr/bin/env bash
# End-to-end curl walkthrough for the Temporary Event Radio Spectrum API.
# Usage: ./examples/walkthrough.sh [BASE_URL]
set -euo pipefail

BASE="${1:-http://localhost:8000}"
J() { python3 -m json.tool; }

post() { curl -s -X POST "$BASE$1" -H 'Content-Type: application/json' -d "${2:-}"; }

echo "== health =="
curl -s "$BASE/health"; echo

echo "== 1. invalid submission (422, whole request rejected) =="
post /api/applications '{
  "name":"bad",
  "circle":{"cx":0,"cy":0,"radius":-5},
  "frequency":{"low":200,"high":100},
  "time_window":{"start":"2026-10-01T10:00:00Z","end":"2026-10-01T08:00:00Z"}
}' | J

echo "== 2. submit A =="
A=$(post /api/applications '{
  "name":"Festival A",
  "circle":{"cx":0,"cy":0,"radius":1000},
  "frequency":{"low":100,"high":110},
  "time_window":{"start":"2026-10-01T08:00:00Z","end":"2026-10-01T12:00:00Z"}
}')
echo "$A" | J
A_ID=$(echo "$A" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

echo "== 3. preview A =="
post "/api/applications/$A_ID/preview" | J

echo "== 4. approve A at revision 0 =="
post "/api/applications/$A_ID/approve" '{"expected_revision":0}' | J

echo "== 5. submit B (1 MHz gap < 2 MHz guard band) =="
B=$(post /api/applications '{
  "name":"Marathon B",
  "circle":{"cx":100,"cy":0,"radius":1000},
  "frequency":{"low":111,"high":112},
  "time_window":{"start":"2026-10-01T09:00:00Z","end":"2026-10-01T11:00:00Z"}
}')
B_ID=$(echo "$B" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
post "/api/applications/$B_ID/preview" | J

echo "== 6. stale approval (expected 0, current 1) =="
post "/api/applications/$B_ID/approve" '{"expected_revision":0}' | J || true

echo "== 7. current revision but interference =="
post "/api/applications/$B_ID/approve" '{"expected_revision":1}' | J || true

echo "== 8. revoke A =="
post "/api/applications/$A_ID/revoke" '{"reason":"postponed"}' | J

echo "== 9. approve B at revision 2 =="
post "/api/applications/$B_ID/approve" '{"expected_revision":2}' | J

echo "== 9b. submit two more non-conflicting devices (far away, guard-band safe) =="
C=$(post /api/applications '{
  "name":"Stage C",
  "circle":{"cx":5000,"cy":5000,"radius":10},
  "frequency":{"low":300,"high":310},
  "time_window":{"start":"2026-10-01T09:00:00Z","end":"2026-10-01T11:00:00Z"}
}')
C_ID=$(echo "$C" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
D=$(post /api/applications '{
  "name":"Stage D",
  "circle":{"cx":5000,"cy":5000,"radius":10},
  "frequency":{"low":400,"high":410},
  "time_window":{"start":"2026-10-01T09:00:00Z","end":"2026-10-01T11:00:00Z"}
}')
D_ID=$(echo "$D" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
# E stays pending: used below to demonstrate the stale-revision rejection.
E=$(post /api/applications '{
  "name":"Stage E",
  "circle":{"cx":5000,"cy":5000,"radius":10},
  "frequency":{"low":500,"high":510},
  "time_window":{"start":"2026-10-01T09:00:00Z","end":"2026-10-01T11:00:00Z"}
}')
E_ID=$(echo "$E" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

echo "== 9c. batch-approve [$C_ID,$D_ID] at revision 3 (atomic, revisions 4 and 5) =="
post /api/applications/batch-approve \
  "{\"expected_revision\":3,\"application_ids\":[$C_ID,$D_ID]}" | J

echo "== 9d. batch rejections: duplicate id (422) and stale revision (409) =="
post /api/applications/batch-approve \
  "{\"expected_revision\":5,\"application_ids\":[$C_ID,$C_ID]}" | J || true
post /api/applications/batch-approve \
  "{\"expected_revision\":3,\"application_ids\":[$E_ID]}" | J || true

echo "== 9e. as-of revision 4 restores the batch prefix [B, C] =="
curl -s "$BASE/api/revision/4" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("revision:", d["revision"], "effective:", d["effective_application_ids"])'

echo "== 10. history snapshots =="
curl -s "$BASE/api/revision/1" | J
curl -s "$BASE/api/revision/2" | J
curl -s "$BASE/api/revision/current" | J
