"""Tests for the whole-group approval endpoint (POST /api/applications/batch-approve).

Covers:
* all-or-nothing commit with consecutive revisions in input order;
* as-of snapshots for every revision produced by a group;
* interference against the effective set and within the group (earlier
  members adjudicate first);
* simultaneous failure categories (invalid id / status / stale / conflicts);
* empty / duplicate id lists rejected as malformed requests;
* shared revision adjudication with the single-item approve and revoke
  endpoints (no partial grants, no interfering effective licenses).
"""
from factories import make_payload


def submit(client, **kwargs):
    return client.post(
        "/api/applications", json=make_payload(**kwargs)
    ).json()


def non_overlapping_freqs(base_low=100.0, width=10.0, guard=2.0):
    """Closed bands separated by more than the 2 MHz test guard band."""
    gap = guard + 1.0  # 3 MHz > guard band 2 MHz
    low = base_low
    high = low + width
    while True:
        yield (low, high)
        low = high + gap
        high = low + width


class TestBatchHappyPath:
    def test_group_approval_consecutive_revisions_in_order(self, client):
        bands = non_overlapping_freqs()
        a = submit(client, name="A", freq=next(bands))
        b = submit(client, name="B", freq=next(bands))
        c = submit(client, name="C", freq=next(bands))

        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [a["id"], b["id"], c["id"]]},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["approved"] is True
        assert data["expected_revision"] == 0
        assert data["from_revision"] == 0
        assert data["revision"] == 3
        assert data["revisions"] == [1, 2, 3]
        assert data["count"] == 3
        assert [item["application_id"] for item in data["results"]] == [
            a["id"],
            b["id"],
            c["id"],
        ]
        assert [item["revision"] for item in data["results"]] == [1, 2, 3]
        for item, app_id in zip(data["results"], [a["id"], b["id"], c["id"]]):
            assert item["application"]["id"] == app_id
            assert item["application"]["status"] == "approved"
            assert item["application"]["approval_revision"] == item["revision"]

    def test_snapshot_at_each_group_revision_restores_prefix(self, client):
        bands = non_overlapping_freqs()
        ids = [submit(client, name=f"E{i}", freq=next(bands))["id"] for i in range(3)]

        client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": ids},
        ).raise_for_status()

        # Querying any revision of the group restores exactly the prefix
        # effective at the moment that item was approved.
        assert client.get("/api/revision/1").json()["effective_application_ids"] == [
            ids[0]
        ]
        snap2 = client.get("/api/revision/2").json()
        assert snap2["effective_application_ids"] == ids[:2]
        snap3 = client.get("/api/revision/3").json()
        assert snap3["effective_application_ids"] == ids
        assert [ev["revision"] for ev in snap3["approvals"]] == [1, 2, 3]

        current = client.get("/api/revision/current").json()
        assert current["current_revision"] == 3
        assert current["effective_application_ids"] == ids

    def test_single_item_batch_equivalent_to_single_approval(self, client):
        a = submit(client, name="A")
        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [a["id"]]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["revision"] == 1
        assert client.get(
            f"/api/applications/{a['id']}"
        ).json()["approval_revision"] == 1


class TestBatchInterference:
    def test_conflict_with_effective_license_rejects_whole_group(self, client):
        bands = non_overlapping_freqs()
        a = submit(client, name="A", freq=next(bands))
        # A is an effective license at revision 1.
        client.post(
            f"/api/applications/{a['id']}/approve",
            json={"expected_revision": 0},
        ).raise_for_status()

        # B is clear of A; C uses the default [100,110] and collides with A.
        b = submit(client, name="B", freq=next(bands))
        c = submit(client, name="C")

        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 1, "application_ids": [b["id"], c["id"]]},
        )
        assert r.status_code == 409
        body = r.json()["error"]
        assert body["code"] == "batch_rejected"
        details = body["details"]
        assert details["revised"] is False
        assert details["current_revision"] == 1

        interference = details["interference"]
        assert len(interference) == 1
        entry = interference[0]
        assert entry["application_id"] == c["id"]
        assert entry["batch_index"] == 1
        conflict = entry["conflicts"][0]
        assert conflict["scope"] == "effective"
        assert conflict["application"]["id"] == a["id"]
        assert conflict["reason"] == "frequency_intersection"

        # Nothing was approved and no revision was consumed.
        assert client.get("/api/revision/current").json()["current_revision"] == 1
        for app_id in (b["id"], c["id"]):
            assert (
                client.get(f"/api/applications/{app_id}").json()["status"]
                == "pending"
            )
        # The clean member can immediately be approved again at the same rev.
        again = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 1, "application_ids": [b["id"]]},
        )
        assert again.status_code == 200, again.text
        assert again.json()["revision"] == 2

    def test_conflict_within_group_rejects_whole_group(self, client):
        bands = non_overlapping_freqs()
        a = submit(client, name="A", freq=next(bands))          # [100,110]
        b = submit(client, name="B", freq=next(bands))          # [113,123]
        # C shares B's band but stays 3 MHz clear of A (> 2 MHz guard).
        c = submit(client, name="C", freq=(113, 123))

        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [a["id"], b["id"], c["id"]]},
        )
        assert r.status_code == 409
        details = r.json()["error"]["details"]
        (entry,) = details["interference"]
        assert entry["application_id"] == c["id"]
        assert entry["batch_index"] == 2
        (conflict,) = entry["conflicts"]
        assert conflict["scope"] == "batch"
        assert conflict["batch_index"] == 1
        assert conflict["application"]["id"] == b["id"]

        assert client.get("/api/revision/current").json()["current_revision"] == 0
        for app_id in (a["id"], b["id"], c["id"]):
            assert (
                client.get(f"/api/applications/{app_id}").json()["status"]
                == "pending"
            )

    def test_later_member_does_not_pollute_earlier_member_report(self, client):
        # A and B overlap completely; C is on a separate band. Only B (the
        # later claimant of the colliding pair) is reported as interfering.
        a = submit(client, name="A")
        b = submit(client, name="B")
        c = submit(client, name="C", freq=(200, 210))

        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [a["id"], b["id"], c["id"]]},
        )
        assert r.status_code == 409
        reported = {
            entry["application_id"]
            for entry in r.json()["error"]["details"]["interference"]
        }
        assert reported == {b["id"]}

    def test_effective_and_batch_conflicts_both_reported(self, client):
        # Geometry along the x axis (default time/frequency overlap fully):
        #   effective A at x=0 r=5, batch B at x=5 r=5, batch C at x=15 r=5
        # B touches A (effective) and B/C overlap (distance 10 < 15); A and C
        # are strictly disjoint (distance 15 > 5+5). So B reports only
        # scope=effective and C only scope=batch.
        a = submit(client, name="A", circle=(0, 0, 5))
        client.post(
            f"/api/applications/{a['id']}/approve", json={"expected_revision": 0}
        ).raise_for_status()
        b = submit(client, name="B", circle=(5, 0, 5))
        c = submit(client, name="C", circle=(15, 0, 5))

        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 1, "application_ids": [b["id"], c["id"]]},
        )
        assert r.status_code == 409
        entries = {
            entry["application_id"]: entry
            for entry in r.json()["error"]["details"]["interference"]
        }
        assert set(entries) == {b["id"], c["id"]}
        b_scopes = {conflict["scope"] for conflict in entries[b["id"]]["conflicts"]}
        assert b_scopes == {"effective"}
        c_scopes = {conflict["scope"] for conflict in entries[c["id"]]["conflicts"]}
        assert c_scopes == {"batch"}
        assert entries[c["id"]]["conflicts"][0]["batch_index"] == 0

    def test_batch_respects_guard_band_and_time_area_rules(self, client):
        # Effective grant [100,110]; batch of two disjoint-in-everything
        # members succeeds even with a group of one item.
        a = submit(client, name="A")
        client.post(
            f"/api/applications/{a['id']}/approve", json={"expected_revision": 0}
        ).raise_for_status()
        # gap exactly 2.0 == guard band -> allowed
        safe = submit(client, name="safe", freq=(112, 120))
        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 1, "application_ids": [safe["id"]]},
        )
        assert r.status_code == 200, r.text


class TestBatchFailures:
    def test_unknown_ids_listed_as_not_found(self, client):
        a = submit(client, name="A")
        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [a["id"], 999]},
        )
        assert r.status_code == 409
        details = r.json()["error"]["details"]
        assert details["applications_not_found"] == [
            {"application_id": 999, "batch_index": 1}
        ]
        assert "interference" not in details
        assert client.get("/api/revision/current").json()["current_revision"] == 0
        assert client.get(f"/api/applications/{a['id']}").json()["status"] == "pending"

    def test_not_pending_statuses_reported(self, client):
        bands = non_overlapping_freqs()
        a = submit(client, name="A", freq=next(bands))
        client.post(
            f"/api/applications/{a['id']}/approve", json={"expected_revision": 0}
        ).raise_for_status()
        client.post(
            f"/api/applications/{a['id']}/revoke", json={"reason": "moved"}
        ).raise_for_status()
        approved = submit(client, name="approved-one", freq=next(bands))
        client.post(
            f"/api/applications/{approved['id']}/approve",
            json={"expected_revision": 2},
        ).raise_for_status()
        pending = submit(client, name="pending", freq=next(bands))

        r = client.post(
            "/api/applications/batch-approve",
            json={
                "expected_revision": 3,
                "application_ids": [a["id"], approved["id"], pending["id"]],
            },
        )
        assert r.status_code == 409
        not_pending = r.json()["error"]["details"]["applications_not_pending"]
        by_id = {row["application_id"]: row for row in not_pending}
        assert by_id[a["id"]]["status"] == "revoked"
        assert by_id[a["id"]]["batch_index"] == 0
        assert by_id[approved["id"]]["status"] == "approved"
        assert by_id[approved["id"]]["approval_revision"] == 3
        # The pending member is not listed as a failure.
        assert pending["id"] not in by_id
        # ... but the whole group still does not go through.
        assert client.get(f"/api/applications/{pending['id']}").json()[
            "status"
        ] == "pending"

    def test_stale_revision_category(self, client):
        a = submit(client, name="A")
        # Revision advances to 1 via single approval ...
        other = submit(client, name="other", freq=next(non_overlapping_freqs(200)))
        client.post(
            f"/api/applications/{other['id']}/approve",
            json={"expected_revision": 0},
        ).raise_for_status()

        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [a["id"]]},
        )
        assert r.status_code == 409
        details = r.json()["error"]["details"]
        assert details["stale_revision"] == {
            "expected_revision": 0,
            "current_revision": 1,
        }
        assert client.get("/api/revision/current").json()["current_revision"] == 1

    def test_stale_plus_other_categories_reported_together(self, client):
        a = submit(client, name="A")
        # A becomes effective at revision 1.
        client.post(
            f"/api/applications/{a['id']}/approve", json={"expected_revision": 0}
        ).raise_for_status()
        # B collides with A and the group additionally carries a stale
        # expected revision and a bad id.
        b = submit(client, name="B")
        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [b["id"], 424242]},
        )
        assert r.status_code == 409
        details = r.json()["error"]["details"]
        assert details["stale_revision"]["current_revision"] == 1
        assert [row["application_id"] for row in details["applications_not_found"]] == [
            424242
        ]
        assert [
            entry["application_id"] for entry in details["interference"]
        ] == [b["id"]]
        # Nothing consumed.
        assert client.get("/api/revision/current").json()["current_revision"] == 1

    def test_empty_id_list_is_validation_error(self, client):
        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": []},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"
        codes = [d["code"] for d in r.json()["error"]["details"]]
        assert "application_ids_empty" in codes

    def test_duplicate_ids_are_validation_error(self, client):
        a = submit(client, name="A")
        b = submit(client, name="B")
        r = client.post(
            "/api/applications/batch-approve",
            json={
                "expected_revision": 0,
                "application_ids": [a["id"], b["id"], a["id"]],
            },
        )
        assert r.status_code == 422
        details = r.json()["error"]["details"]
        assert [d["code"] for d in details] == ["application_ids_not_unique"]
        assert details[0]["application_id"] == a["id"]

    def test_missing_body_field_is_structural_validation_error(self, client):
        r = client.post("/api/applications/batch-approve", json={"expected_revision": 0})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_negative_expected_revision_rejected(self, client):
        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": -1, "application_ids": [1]},
        )
        assert r.status_code == 422


class TestBatchConcurrencySemantics:
    def test_single_approval_beating_batch_leaves_no_partial_grant(self, client):
        # Two disjoint members; a concurrent single approval consumes the
        # revision the batch was based on. Sequential calls reproduce the
        # locked outcome (same adjudication as the documented race).
        bands = non_overlapping_freqs()
        a = submit(client, name="A", freq=next(bands))
        b = submit(client, name="B", freq=next(bands))

        # Racer: a disjoint-frequency single approval at revision 1.
        racer = submit(client, name="racer", freq=(300, 310))
        client.post(
            f"/api/applications/{racer['id']}/approve",
            json={"expected_revision": 0},
        ).raise_for_status()

        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [a["id"], b["id"]]},
        )
        assert r.status_code == 409
        assert r.json()["error"]["details"]["stale_revision"] == {
            "expected_revision": 0,
            "current_revision": 1,
        }
        # The group is entirely untouched; retrying at the new revision works.
        retry = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 1, "application_ids": [a["id"], b["id"]]},
        )
        assert retry.status_code == 200, retry.text
        assert retry.json()["revisions"] == [2, 3]

    def test_batch_beating_single_approval_serialises(self, client):
        bands = non_overlapping_freqs()
        a = submit(client, name="A", freq=next(bands))
        b = submit(client, name="B", freq=next(bands))
        # c2 uses B's exact band: once the batch commits, the single approval
        # at the new revision must be refused as interference; a separate
        # pending application on the same stale revision is refused stale.
        c2 = submit(client, name="C2", freq=(113, 123))
        c3 = submit(client, name="C3", freq=(200, 201))

        batch = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [a["id"], b["id"]]},
        )
        assert batch.status_code == 200
        # Single approval now sees revision 2 and must lose against B.
        single = client.post(
            f"/api/applications/{c2['id']}/approve",
            json={"expected_revision": 2},
        )
        assert single.status_code == 409
        assert single.json()["error"]["code"] == "interference"
        # Stale variant also refused (never grants).
        stale = client.post(
            f"/api/applications/{c3['id']}/approve",
            json={"expected_revision": 0},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "stale_revision"

        # Effective set is exactly the batch result; no interfering licenses.
        effective = client.get("/api/revision/current").json()[
            "effective_application_ids"
        ]
        assert effective == [a["id"], b["id"]]

    def test_revoke_concurrent_with_batch_shares_revision(self, client):
        bands = non_overlapping_freqs()
        a = submit(client, name="A", freq=next(bands))
        client.post(
            f"/api/applications/{a['id']}/approve", json={"expected_revision": 0}
        ).raise_for_status()
        b = submit(client, name="B", freq=next(bands))

        # Revocation advances the revision first.
        client.post(
            f"/api/applications/{a['id']}/revoke", json={"reason": "moved"}
        ).raise_for_status()

        stale_batch = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 1, "application_ids": [b["id"]]},
        )
        assert stale_batch.status_code == 409
        assert stale_batch.json()["error"]["details"]["stale_revision"][
            "current_revision"
        ] == 2
        fresh = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 2, "application_ids": [b["id"]]},
        )
        assert fresh.status_code == 200
        assert fresh.json()["revision"] == 3

    def test_batch_then_revoke_history_remains_intact(self, client):
        bands = non_overlapping_freqs()
        ids = [submit(client, name=f"E{i}", freq=next(bands))["id"] for i in range(2)]
        client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": ids},
        ).raise_for_status()
        client.post(
            f"/api/applications/{ids[0]}/revoke", json={"reason": "x"}
        ).raise_for_status()

        # Mid-group snapshot still reconstructs the prefix only.
        snap1 = client.get("/api/revision/1").json()
        assert snap1["effective_application_ids"] == [ids[0]]
        snap2 = client.get("/api/revision/2").json()
        assert snap2["effective_application_ids"] == ids
        snap3 = client.get("/api/revision/3").json()
        assert snap3["effective_application_ids"] == [ids[1]]
        assert snap3["approvals"][0]["revoked_at_revision"] == 3

    def test_batch_route_not_captured_by_item_route(self, client):
        # /api/applications/batch-approve must resolve to the group endpoint,
        # not to /api/applications/{application_id}.
        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": []},
        )
        assert r.status_code == 422
