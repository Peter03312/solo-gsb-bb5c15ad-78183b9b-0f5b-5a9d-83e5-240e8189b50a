"""Pydantic request schemas.

Structural problems (missing / wrong-typed fields) are reported by FastAPI
and normalised in main.py.  Cross-field semantic rules are checked in
semantic_validation so the whole request is rejected with explicit codes:

* ``radius`` must be positive;
* ``freq_low`` may not exceed ``freq_high`` (equal is allowed, a single
  frequency);
* ``start_time`` must be strictly earlier than ``end_time``.

The batch approval additionally requires a non-empty list of positive
application IDs; the duplicate-ID rule is enforced in the endpoint.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field


class CircleIn(BaseModel):
    cx: float
    cy: float
    radius: float


class FrequencyBandIn(BaseModel):
    low: float
    high: float


class TimeWindowIn(BaseModel):
    # Timezone-aware ISO-8601 instants (UTC recommended, e.g. 2026-10-01T08:00:00Z).
    start: datetime
    end: datetime


class ApplicationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    circle: CircleIn
    frequency: FrequencyBandIn
    time_window: TimeWindowIn


class ApprovalRequest(BaseModel):
    # Revision the client based its preview decision on; required.
    expected_revision: int = Field(ge=0)


class BatchApprovalRequest(BaseModel):
    # Revision the operator based the group decision on; required.
    expected_revision: int = Field(ge=0)
    # Non-empty list of pending application IDs, approved in list order.
    # Duplicates are rejected structurally (see the batch endpoint for the
    # whole-request 422 envelope).
    application_ids: list[Annotated[int, Field(ge=1)]] = Field(min_length=1)


class RevocationRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)
