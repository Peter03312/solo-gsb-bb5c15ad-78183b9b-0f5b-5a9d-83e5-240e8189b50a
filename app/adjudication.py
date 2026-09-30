"""Pure adjudication rules for radio applications.

An application grants:

* a coverage **circle** in planar coordinates ``(cx, cy, r)``;
* a **closed** frequency interval ``[freq_low, freq_high]``;
* a **half-open** UTC time interval ``[start_time, end_time)``.

Two *effective* (approved and not later revoked) applications interfere iff
ALL of the following hold:

1. the time intervals intersect;
2. the coverage circles intersect;
3. the closed frequency bands intersect, OR the minimum gap between them is
   strictly smaller than the non-negative guard band.

The module is free of framework / database code so the rules can be unit
tested directly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

# Conflict reason codes (also documented in the README).
REASON_FREQUENCY_INTERSECTION = "frequency_intersection"
REASON_WITHIN_GUARD_BAND = "within_guard_band"


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    radius: float


@dataclass(frozen=True)
class License:
    """An effective approved application, as seen by the adjudicator."""

    application_id: int
    circle: Circle
    freq_low: float
    freq_high: float
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class Conflict:
    other: License
    time_overlap: bool
    area_overlap: bool
    frequency_intersects: bool
    minimum_frequency_gap: float
    reason: str  # REASON_*; only set when all three trigger conditions hold


def circles_intersect(a: Circle, b: Circle) -> bool:
    """Planar disks intersect iff centre distance <= sum of radii.

    Touching at one point (distance exactly equal to the sum) counts as an
    intersection.
    """
    distance = math.hypot(a.cx - b.cx, a.cy - b.cy)
    return distance <= a.radius + b.radius


def closed_intervals_intersect(
    low_a: float, high_a: float, low_b: float, high_b: float
) -> bool:
    """Closed intervals: touching endpoints intersect (a1 <= b0 and b1 <= a0)."""
    return low_a <= high_b and low_b <= high_a


def half_open_intervals_intersect(
    start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime
) -> bool:
    """[start_a, end_a) vs [start_b, end_b): end == start does NOT overlap."""
    return start_a < end_b and start_b < end_a


def minimum_frequency_gap(
    low_a: float, high_a: float, low_b: float, high_b: float
) -> float:
    """0 when the closed bands intersect, otherwise the gap between them."""
    if closed_intervals_intersect(low_a, high_a, low_b, high_b):
        return 0.0
    return low_b - high_a if high_a < low_b else low_a - high_b


def frequency_conflicts(
    low_a: float,
    high_a: float,
    low_b: float,
    high_b: float,
    guard_band: float,
) -> tuple[bool, bool, float, str | None]:
    """Return (conflicts, intersects, minimum_gap, reason).

    ``guard_band`` must be non-negative.  A gap exactly equal to the guard
    band is allowed ("strictly smaller than" triggers the conflict).
    """
    if closed_intervals_intersect(low_a, high_a, low_b, high_b):
        return True, True, 0.0, REASON_FREQUENCY_INTERSECTION
    gap = minimum_frequency_gap(low_a, high_a, low_b, high_b)
    if gap < guard_band:
        return True, False, gap, REASON_WITHIN_GUARD_BAND
    return False, False, gap, None


def evaluate(
    candidate: License, effective: list[License], guard_band: float
) -> list[Conflict]:
    """All interference conflicts between ``candidate`` and effective grants.

    Time and area intersection are *necessary* conditions; frequency overlap
    (or guard-band violation) is the third.  Every checked grant is returned
    with its trigger flags so callers can preview exactly which conditions
    fired.
    """
    conflicts: list[Conflict] = []
    for other in effective:
        t_overlap = half_open_intervals_intersect(
            candidate.start_time,
            candidate.end_time,
            other.start_time,
            other.end_time,
        )
        a_overlap = circles_intersect(candidate.circle, other.circle)
        if not (t_overlap and a_overlap):
            continue
        freq_conflicts, freq_intersects, gap, reason = frequency_conflicts(
            candidate.freq_low,
            candidate.freq_high,
            other.freq_low,
            other.freq_high,
            guard_band,
        )
        if freq_conflicts:
            conflicts.append(
                Conflict(
                    other=other,
                    time_overlap=True,
                    area_overlap=True,
                    frequency_intersects=freq_intersects,
                    minimum_frequency_gap=gap,
                    reason=reason or REASON_FREQUENCY_INTERSECTION,
                )
            )
    return conflicts
