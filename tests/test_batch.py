"""End-to-end tests for the atomic batch-approval endpoint.

Covers: happy path with continuous revisions and as-of snapshots, request
shape rules (empty / non-positive / duplicate IDs), the all-or-nothing
failure modes (missing, not pending, stale revision, interference against
the effective set and within the group), and serialisation against
concurrent single approvals and revocations.
"""
from factories import make_payload


def submit(client, **overrides):
    payload = make_payload(**overrides)
    r = client.post("/api/applications", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def approve_one(client, app_id, revision):
    return client.post(
        f"/api/applications/{app_id}/approve",
        json={"expected_revision": revision},
    )


def batch(client, ids, revision):
    return client.post(
        "/api/applications/batch-approve",
        json={"expected_revision": revision, "application_ids": ids},
    )


def nonconflicting(client, name, freq, circle=(5000.0, 5000.0, 10.0)):
    """A license far away in area: never interferes with the default apps."""
    return submit(client, name=name, circle=circle, freq=freq)


class TestBatchHappyPath:
    def test_approves_all_in_order_with_continuous_revisions(self, client):
        a = nonconflicting(client, "A", (100, 110))
        b = nonconflicting(client, "B", (200, 210))
        c = nonconflicting(client, "C", (300, 310))

        r = batch(client, [a, b, c], 0)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["approved"] is True
        assert data["count"] == 3
        assert data["from_revision"] == 0
        assert data["revision"] == 3
        assert [item["application_id"] for item in data["applications"]] == [a, b, c]
        assert [item["revision"] for item in data["applications"]] == [1, 2, 3]
        for item in data["applications"]:
            assert item["application"]["status"] == "approved"
            assert item["application"]["approval_revision"] == item["revision"]

    def test_single_item_batch_advances_one_revision(self, client):
        a = nonconflicting(client, "A", (100, 110))
        r = batch(client, [a], 0)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["revision"] == 1 and data["count"] == 1

    def test_intermediate_revisions_restore_prefix_of_group(self, client):
        a = nonconflicting(client, "A", (100, 110))
        b = nonconflicting(client, "B", (200, 210))
        c = nonconflicting(client, "C", (300, 310))
        batch(client, [a, b, c], 0).raise_for_status()

        # Querying any revision assigned by the batch restores the effective
        # set exactly as it was when that item got approved (a prefix).
        assert client.get("/api/revision/1").json()["effective_application_ids"] == [a]
        assert client.get("/api/revision/2").json()["effective_application_ids"] == [a, b]
        snap3 = client.get("/api/revision/3").json()
        assert snap3["effective_application_ids"] == [a, b, c]
        assert [ev["revision"] for ev in snap3["approvals"]] == [1, 2, 3]
        assert [ev["application_id"] for ev in snap3["approvals"]] == [a, b, c]

        current = client.get("/api/revision/current").json()
        assert current["current_revision"] == 3
        assert current["effective_application_ids"] == [a, b, c]

    def test_batch_after_earlier_history_starts_at_current_revision(self, client):
        a = nonconflicting(client, "A", (100, 110))
        approve_one(client, a, 0).raise_for_status()  # revision 1

        b = nonconflicting(client, "B", (200, 210))
        c = nonconflicting(client, "C", (300, 310))
        r = batch(client, [b, c], 1)
        assert r.status_code == 200, r.text
        assert [item["revision"] for item in r.json()["applications"]] == [2, 3]
        assert client.get("/api/revision/2").json()["effective_application_ids"] == [a, b]


class TestBatchRequestShape:
    def test_empty_list_rejected_422(self, client):
        r = batch(client, [], 0)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_non_positive_id_rejected_422(self, client):
        r = client.post(
            "/api/applications/batch-approve",
            json={"expected_revision": 0, "application_ids": [0]},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_missing_fields_rejected_422(self, client):
        r = client.post(
            "/api/applications/batch-approve", json={"application_ids": [1]}
        )
        assert r.status_code == 422

    def test_duplicate_ids_rejected_whole_request_422(self, client):
        a = nonconflicting(client, "A", (100, 110))
        r = batch(client, [a, a], 0)
        assert r.status_code == 422
        err = r.json()["error"]
        assert err["code"] == "validation_failed"
        detail = err["details"][0]
        assert detail["code"] == "duplicate_application_ids"
        assert detail["duplicates"] == [a]

        # Nothing happened: revision stays 0, application still pending.
        assert client.get("/api/revision/current").json()["current_revision"] == 0
        assert client.get(f"/api/applications/{a}").json()["status"] == "pending"

    def test_three_duplicates_lists_each_once(self, client):
        a = nonconflicting(client, "A", (100, 110))
        b = nonconflicting(client, "B", (200, 210))
        r = batch(client, [a, b, a, b, a], 0)
        assert r.status_code == 422
        assert r.json()["error"]["details"][0]["duplicates"] == [a, b]


class TestBatchFailuresAreAtomic:
    def test_missing_id_rejects_whole_group(self, client):
        a = nonconflicting(client, "A", (100, 110))
        r = batch(client, [a, 999], 0)
        assert r.status_code == 404
        err = r.json()["error"]
        assert err["code"] == "not_found"
        assert err["details"]["applications"] == [{"id": 999}]
        # Even the valid first item must not be approved.
        assert client.get(f"/api/applications/{a}").json()["status"] == "pending"
        assert client.get("/api/revision/current").json()["current_revision"] == 0

    def test_already_approved_item_rejects_whole_group(self, client):
        a = nonconflicting(client, "A", (100, 110))
        b = nonconflicting(client, "B", (200, 210))
        approve_one(client, a, 0).raise_for_status()  # revision 1

        r = batch(client, [a, b], 1)
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "not_pending"
        entries = err["details"]["applications"]
        assert entries == [{"id": a, "status": "approved", "approval_revision": 1}]
        assert client.get(f"/api/applications/{b}").json()["status"] == "pending"
        assert client.get("/api/revision/current").json()["current_revision"] == 1

    def test_revoked_item_reports_status(self, client):
        a = nonconflicting(client, "A", (100, 110))
        approve_one(client, a, 0).raise_for_status()
        client.post(f"/api/applications/{a}/revoke", json={}).raise_for_status()
        b = nonconflicting(client, "B", (200, 210))

        r = batch(client, [b, a], 2)
        assert r.status_code == 409
        entries = r.json()["error"]["details"]["applications"]
        assert entries == [
            {"id": a, "status": "revoked", "approval_revision": 1}
        ]

    def test_stale_revision_rejects_whole_group(self, client):
        a = nonconflicting(client, "A", (100, 110))
        b = nonconflicting(client, "B", (200, 210))
        # advance revision 0 -> 1 before the batch arrives
        other = nonconflicting(client, "other", (400, 410))
        approve_one(client, other, 0).raise_for_status()

        r = batch(client, [a, b], 0)
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "stale_revision"
        assert err["details"] == {"expected_revision": 0, "current_revision": 1}
        assert client.get(f"/api/applications/{a}").json()["status"] == "pending"
        assert client.get(f"/api/applications/{b}").json()["status"] == "pending"

    def test_interference_against_effective_set_rejects_all(self, client):
        # Effective grant at the default spot/freq; batch items are far away
        # except b which overlaps the effective grant.
        eff = submit(client, name="eff", circle=(0, 0, 10), freq=(100, 110))
        approve_one(client, eff, 0).raise_for_status()  # revision 1

        a = nonconflicting(client, "A", (200, 210))
        b = submit(client, name="B", circle=(0, 0, 10), freq=(105, 115))
        c = nonconflicting(client, "C", (300, 310))

        r = batch(client, [a, b, c], 1)
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "interference"
        assert err["details"]["application_id"] == b
        conflict = err["details"]["conflicts"][0]
        assert conflict["application"]["id"] == eff
        assert conflict["origin"] == "effective"
        assert conflict["reason"] == "frequency_intersection"

        # Atomicity: neither a (checked earlier) nor c got approved.
        for app_id in (a, b, c):
            assert client.get(f"/api/applications/{app_id}").json()["status"] == "pending"
        assert client.get("/api/revision/current").json()["current_revision"] == 1

    def test_intra_group_interference_rejects_all(self, client):
        a = nonconflicting(client, "A", (200, 210))
        # b overlaps a in area/frequency at the default location/time; both
        # far from any effective grant.
        b = submit(client, name="B", circle=(5000.0, 5000.0, 10), freq=(205, 215))
        c = nonconflicting(client, "C", (300, 310))

        r = batch(client, [a, b, c], 0)
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "interference"
        assert err["details"]["application_id"] == b
        conflict = err["details"]["conflicts"][0]
        assert conflict["application"]["id"] == a
        assert conflict["origin"] == "batch"

        for app_id in (a, b, c):
            assert client.get(f"/api/applications/{app_id}").json()["status"] == "pending"
        assert client.get("/api/revision/current").json()["current_revision"] == 0

    def test_intra_group_guard_band_violation(self, client):
        # Guard band is 2 MHz (tests/conftest.py): gap 1 < 2.
        a = nonconflicting(client, "A", (200, 201))
        b = nonconflicting(client, "B", (202, 203))
        r = batch(client, [a, b], 0)
        assert r.status_code == 409
        conflict = r.json()["error"]["details"]["conflicts"][0]
        assert conflict["application"]["id"] == a
        assert conflict["origin"] == "batch"
        assert conflict["reason"] == "within_guard_band"
        assert conflict["trigger"]["minimum_frequency_gap"] == 1.0

    def test_items_exactly_at_guard_band_coexist(self, client):
        # Gap exactly 2.0 == guard band: allowed (strictly smaller triggers).
        a = nonconflicting(client, "A", (200, 201))
        b = nonconflicting(client, "B", (203, 204))
        r = batch(client, [a, b], 0)
        assert r.status_code == 200, r.text

    def test_items_in_disjoint_areas_coexist(self, client):
        # Same time and frequency but coverage circles far apart.
        a = nonconflicting(client, "A", (100, 110), circle=(5000, 5000, 10))
        b = nonconflicting(client, "B", (100, 110), circle=(-5000, -5000, 10))
        r = batch(client, [a, b], 0)
        assert r.status_code == 200, r.text

    def test_non_overlapping_times_coexist(self, client):
        # Same place and frequency but half-open windows only touch: no overlap.
        a = submit(
            client,
            name="A",
            circle=(5000, 5000, 10),
            freq=(100, 110),
            start="2026-10-01T08:00:00Z",
            end="2026-10-01T10:00:00Z",
        )
        b = submit(
            client,
            name="B",
            circle=(5000, 5000, 10),
            freq=(100, 110),
            start="2026-10-01T10:00:00Z",
            end="2026-10-01T12:00:00Z",
        )
        r = batch(client, [a, b], 0)
        assert r.status_code == 200, r.text


class TestBatchConcurrency:
    def test_batch_and_single_approval_share_revision_verdict(self, client):
        # Two overlapping (mutually interfering) pending applications.
        a = submit(client, name="A", circle=(0, 0, 10), freq=(100, 110))
        b = submit(client, name="B", circle=(0, 0, 10), freq=(100, 110))

        # A single approval lands first (revision 0 -> 1); the batch based on
        # revision 0 must lose the shared verdict and stay atomic.
        approve_one(client, a, 0).raise_for_status()
        r = batch(client, [b], 0)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "stale_revision"

        # Retrying with the fresh revision re-adjudicates under the lock and
        # finds the interference created by the winner.
        r2 = batch(client, [b], 1)
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == "interference"
        assert client.get("/api/revision/current").json()["current_revision"] == 1

    def test_batch_then_single_approval_sees_new_effective_set(self, client):
        a = nonconflicting(client, "A", (100, 110))
        b = nonconflicting(client, "B", (200, 210))
        batch(client, [a, b], 0).raise_for_status()  # revisions 1, 2

        # A single approval carrying the pre-batch revision is stale.
        d = submit(client, name="D", circle=(0, 0, 10), freq=(100, 110))
        stale = approve_one(client, d, 0)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "stale_revision"

    def test_batch_and_revoke_share_the_lock(self, client):
        a = nonconflicting(client, "A", (100, 110))
        approve_one(client, a, 0).raise_for_status()  # revision 1
        b = nonconflicting(client, "B", (200, 210))
        c = nonconflicting(client, "C", (300, 310))

        # A revoke advances the revision first; the batch based on 1 is stale.
        client.post(f"/api/applications/{a}/revoke", json={}).raise_for_status()
        r = batch(client, [b, c], 1)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "stale_revision"

        fresh = batch(client, [b, c], 2)
        assert fresh.status_code == 200, fresh.text
        assert [item["revision"] for item in fresh.json()["applications"]] == [3, 4]
