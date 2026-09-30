from datetime import datetime, timedelta, timezone

from app.adjudication import (
    REASON_FREQUENCY_INTERSECTION,
    REASON_WITHIN_GUARD_BAND,
    Circle,
    License,
    circles_intersect,
    closed_intervals_intersect,
    evaluate,
    frequency_conflicts,
    half_open_intervals_intersect,
    minimum_frequency_gap,
)

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=2)
T2 = T1
T3 = T0 + timedelta(hours=4)


def lic(
    app_id,
    cx=0.0,
    cy=0.0,
    r=10.0,
    flo=100.0,
    fhi=110.0,
    start=T0,
    end=T1,
):
    return License(
        application_id=app_id,
        circle=Circle(cx, cy, r),
        freq_low=flo,
        freq_high=fhi,
        start_time=start,
        end_time=end,
    )


class TestCircles:
    def test_nested_circles(self):
        assert circles_intersect(Circle(0, 0, 10), Circle(1, 1, 2))

    def test_touching_at_one_point_counts(self):
        # centre distance exactly 10 = radii sum 5+5
        assert circles_intersect(Circle(0, 0, 5), Circle(10, 0, 5))

    def test_disjoint(self):
        assert not circles_intersect(Circle(0, 0, 5), Circle(20, 0, 5))

    def test_two_dimensional_distance(self):
        # 3-4-5 triangle, radii 2+2=4 < 5 -> disjoint
        assert not circles_intersect(Circle(0, 0, 2), Circle(3, 4, 2))


class TestIntervals:
    def test_closed_endpoints_touching_intersect(self):
        assert closed_intervals_intersect(100, 110, 110, 120)
        assert closed_intervals_intersect(110, 120, 100, 110)

    def test_closed_disjoint(self):
        assert not closed_intervals_intersect(100, 109, 110, 120)

    def test_half_open_touching_does_not_overlap(self):
        assert not half_open_intervals_intersect(T0, T1, T2, T3)
        assert half_open_intervals_intersect(T0, T3, T1, T3 + timedelta(hours=1))

    def test_half_open_partial(self):
        assert half_open_intervals_intersect(
            T0, T3, T0 + timedelta(hours=1), T3 + timedelta(hours=1)
        )


class TestFrequency:
    def test_overlapping_bands(self):
        conflicts, intersects, gap, reason = frequency_conflicts(
            100, 110, 105, 115, guard_band=2.0
        )
        assert conflicts and intersects and gap == 0
        assert reason == REASON_FREQUENCY_INTERSECTION

    def test_endpoint_touch_intersects_closed_band(self):
        conflicts, intersects, gap, _ = frequency_conflicts(
            100, 110, 110, 120, guard_band=2.0
        )
        assert conflicts and intersects and gap == 0

    def test_gap_smaller_than_guard(self):
        # gap 1.5 < 2.0
        conflicts, intersects, gap, reason = frequency_conflicts(
            100, 110, 111.5, 120, guard_band=2.0
        )
        assert conflicts and not intersects and gap == 1.5
        assert reason == REASON_WITHIN_GUARD_BAND

    def test_gap_equal_to_guard_is_allowed(self):
        # Strictly smaller: gap == guard band must NOT conflict.
        conflicts, intersects, gap, reason = frequency_conflicts(
            100, 110, 112, 120, guard_band=2.0
        )
        assert not conflicts and not intersects and gap == 2.0 and reason is None

    def test_gap_larger_than_guard(self):
        conflicts, *_ = frequency_conflicts(100, 110, 120, 130, guard_band=2.0)
        assert not conflicts

    def test_zero_guard_band_disjoint_is_ok(self):
        assert minimum_frequency_gap(100, 110, 112, 120) == 2.0
        conflicts, *_ = frequency_conflicts(100, 110, 110.0001, 120, 0.0)
        assert not conflicts


class TestEvaluate:
    def test_requires_time_and_area_and_frequency(self):
        # same place/freq but adjacent (non-overlapping) times -> no conflict
        later = lic(2, start=T1, end=T3)
        assert evaluate(lic(1, end=T1), [later], guard_band=5.0) == []

        # same time/freq but disjoint areas -> no conflict
        far = lic(2, cx=100.0, cy=0.0, r=1.0)
        assert evaluate(lic(1), [far], guard_band=5.0) == []

        # same time/place, separated frequencies beyond guard -> no conflict
        separated = lic(2, flo=200, fhi=210)
        assert evaluate(lic(1), [separated], guard_band=2.0) == []

    def test_full_conflict(self):
        conflicts = evaluate(lic(1), [lic(2, flo=105, fhi=115)], 2.0)
        assert len(conflicts) == 1
        assert conflicts[0].other.application_id == 2
        assert conflicts[0].reason == REASON_FREQUENCY_INTERSECTION

    def test_guard_band_conflict_through_all_gates(self):
        other = lic(2, flo=111, fhi=112)  # gap 1 < guard 2
        (conflict,) = evaluate(lic(1), [other], 2.0)
        assert conflict.reason == REASON_WITHIN_GUARD_BAND
        assert conflict.minimum_frequency_gap == 1.0
        assert conflict.time_overlap and conflict.area_overlap
        assert not conflict.frequency_intersects
