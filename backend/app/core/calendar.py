"""Typed calendar configuration shared by deployment and request semantics."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectCalendarPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    timezone: str
    period_kind: Literal["assessment", "fiscal", "calendar"]
    boundary_month: int = Field(ge=1, le=12)
    boundary_day: int = Field(ge=1, le=31)
    label_start_offset: int = Field(default=0, ge=-2, le=2)
    label_year_span: int = Field(default=1, ge=0, le=1)
    default_for_current: bool = False

    @model_validator(mode="after")
    def valid_calendar(self) -> ProjectCalendarPolicy:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown calendar timezone") from exc
        date(2001, self.boundary_month, self.boundary_day)
        return self

    def current_period(self, instant: datetime) -> RequestedPeriod:
        if instant.tzinfo is None:
            raise ValueError("Calendar reference time must include a timezone")
        local = instant.astimezone(ZoneInfo(self.timezone))
        year = local.year - ((local.month, local.day) < (self.boundary_month, self.boundary_day))
        start = year + self.label_start_offset
        return RequestedPeriod(
            kind=self.period_kind,
            start_year=start,
            end_year=start + self.label_year_span,
            source_text="project_calendar",
        )


class RequestedPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["assessment", "fiscal", "calendar", "bare"]
    start_year: int
    end_year: int
    source_text: str
