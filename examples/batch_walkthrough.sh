#!/usr/bin/env bash
# Whole-group (batch) approval walkthrough for the Temporary Event Radio
# Spectrum API. Uses the same guard band as the main walkthrough (2 MHz).
# Usage: ./examples/batch_walkthrough.sh [BASE_URL]
set -euo pipefail

BASE="${1:-http://localhost:8000}"
J() { python3 -m json.tool; }

post() { curl -s -X POST "$BASE$1" -H 'Content-Type: application/json' -d "${2:-}"; }

submit() {
  # submit <name> <freq_low> <freq_high> -> prints the new application id
  post /api/applications "{
    \"name\":\"$1\",
    \"circle\":{\"cx\":0,\"cy\":0,\"radius\":1000},
    \"frequency\":{\"low\":$2,\"high\":$3},
    \"time_window\":{\"start\":\"2026-10-01T08:00:00Z\",\"end\":\"2026-10-01T12:00:00Z\"}
  }" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'
}

echo "== 1. submit three devices of one event (bands 3 MHz apart > 2 MHz guard) =="
A=$(submit "Stage A" 100 110)
B=$(submit "Stage B" 113 123)
C=$(submit "Stage C" 126 136)
echo "pending ids: $A $B $C"

echo "== 2. operator group-approves all three at expected revision 0 =="
post /api/applications/batch-approve \
  "{\"expected_revision\":0,\"application_ids\":[$A,$B,$C]}" | J

echo "== 3. as-of revision 2 restores the first two grants of the group =="
curl -s "$BASE/api/revision/2" | python3 -m json.tool

echo "== 4. malformed batch: empty list (422 validation_failed) =="
post /api/applications/batch-approve '{"expected_revision":3,"application_ids":[]}' | J || true

echo "== 5. malformed batch: duplicate ids (422 validation_failed) =="
post /api/applications/batch-approve \
  "{\"expected_revision\":3,\"application_ids\":[$A,$A]}" | J || true

echo "== 6. submit D (collides with the effective B) and an unknown id =="
D=$(submit "Rogue D" 113 123)
echo "D id: $D; requesting group [$D, 99999] at the STALE revision 0"
echo "(HTTP 409 batch_rejected; status line printed by curl -i below)"
curl -s -i -X POST "$BASE/api/applications/batch-approve" \
  -H 'Content-Type: application/json' \
  -d "{\"expected_revision\":0,\"application_ids\":[$D,99999]}" | sed -n '1p'
post /api/applications/batch-approve \
  "{\"expected_revision\":0,\"application_ids\":[$D,99999]}" | J || true

echo "== 7. current revision is still 3 and D is still pending =="
curl -s "$BASE/api/revision/current" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("current_revision:", d["current_revision"], "effective:", d["effective_application_ids"])'

echo "== 8. intra-group conflict: E and F overlap each other (whole group rejected) =="
E=$(submit "Stage E" 200 210)
F=$(submit "Stage F" 205 215)
post /api/applications/batch-approve \
  "{\"expected_revision\":3,\"application_ids\":[$E,$F]}" | J || true

echo "== 9. retry the clean pair alone at the current revision -> revisions 4 and 5 =="
G=$(submit "Stage G" 220 230)
H=$(submit "Stage H" 233 243)
post /api/applications/batch-approve \
  "{\"expected_revision\":3,\"application_ids\":[$G,$H]}" | J
