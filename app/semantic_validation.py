"""Whole-request semantic validation.

Any failure rejects the entire submission; the list contains one entry per
violated rule.
"""
from __future__ import annotations

import math

from .schemas import ApplicationCreate

RADIUS_NOT_POSITIVE = "radius_not_positive"
FREQUENCY_LOW_ABOVE_HIGH = "frequency_low_above_high"
TIME_START_NOT_BEFORE_END = "time_start_not_before_end"
VALUE_NOT_FINITE = "value_not_finite"
NAIVE_DATETIME = "naive_datetime"
BATCH_EMPTY = "application_ids_empty"
BATCH_DUPLICATE = "application_ids_not_unique"



def validate_application(payload: ApplicationCreate) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    numbers = {
        "circle.cx": payload.circle.cx,
        "circle.cy": payload.circle.cy,
        "circle.radius": payload.circle.radius,
        "frequency.low": payload.frequency.low,
        "frequency.high": payload.frequency.high,
    }
    for field, value in numbers.items():
        if not math.isfinite(value):
            errors.append(
                {
                    "field": field,
                    "code": VALUE_NOT_FINITE,
                    "message": f"{field} must be a finite number",
                }
            )

    if payload.circle.radius <= 0:
        errors.append(
            {
                "field": "circle.radius",
                "code": RADIUS_NOT_POSITIVE,
                "message": "radius must be strictly positive",
            }
        )

    if payload.frequency.low > payload.frequency.high:
        errors.append(
            {
                "field": "frequency.low",
                "code": FREQUENCY_LOW_ABOVE_HIGH,
                "message": "frequency low bound must not exceed high bound",
            }
        )

    start = payload.time_window.start
    end = payload.time_window.end
    if start.tzinfo is None or end.tzinfo is None:
        errors.append(
            {
                "field": "time_window",
                "code": NAIVE_DATETIME,
                "message": "time_window.start and end must carry a timezone (use UTC, e.g. ...Z)",
            }
        )
    elif start >= end:
        # Equal instants are also rejected: start must be strictly earlier.
        errors.append(
            {
                "field": "time_window.start",
                "code": TIME_START_NOT_BEFORE_END,
                "message": "time_window start must be strictly earlier than end",
            }
        )

    return errors


def validate_batch_application_ids(application_ids: list[int]) -> list[dict[str, str]]:
    """Whole-request rules for a group approval.

    The list must be non-empty and contain no duplicates (existence/status of
    each id are transactional checks performed under the revision lock).
    """
    errors: list[dict[str, str]] = []
    if not application_ids:
        errors.append(
            {
                "field": "application_ids",
                "code": BATCH_EMPTY,
                "message": "application_ids must contain at least one id",
            }
        )

    seen: set[int] = set()
    duplicates: list[int] = []
    for app_id in application_ids:
        if app_id in seen and app_id not in duplicates:
            duplicates.append(app_id)
        seen.add(app_id)
    for app_id in duplicates:
        errors.append(
            {
                "field": "application_ids",
                "code": BATCH_DUPLICATE,
                "message": f"application id {app_id} appears more than once",
                "application_id": app_id,
            }
        )

    return errors
