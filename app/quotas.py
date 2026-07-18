from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UsageCounter


def _date(day: str | date | None) -> date:
    if isinstance(day, date):
        return day
    return date.fromisoformat(day) if day else datetime.now(UTC).date()


def reserve(session: Session, kind: str, limit: int, *, day: str | date | None = None) -> bool:
    target_day = _date(day)
    counter = session.scalar(
        select(UsageCounter).where(
            UsageCounter.kind == kind, UsageCounter.day == target_day
        )
    )
    if counter is None:
        counter = UsageCounter(kind=kind, day=target_day, used=0)
        session.add(counter)
    if counter.used >= limit:
        session.rollback()
        return False
    counter.used += 1
    session.commit()
    return True


def used(session: Session, kind: str, *, day: str | date | None = None) -> int:
    value = session.scalar(
        select(UsageCounter.used).where(
            UsageCounter.kind == kind, UsageCounter.day == _date(day)
        )
    )
    return value or 0

