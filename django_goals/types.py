from __future__ import annotations

import datetime
from typing import Iterable, Protocol

import django_goals.models


class PursueGoalT(Protocol):
    def __call__(
        self,
        goal: django_goals.models.Goal,
        now: datetime.datetime,
        pickup_monitor: object | None = None
    ) -> django_goals.models.GoalProgress | None:
        ...


class ScheduleGoalT(Protocol):
    def __call__(
        self,
        goal: django_goals.models.Goal,
        precondition_goals: Iterable[django_goals.models.Goal] | None,
        listen: bool,
    ) -> django_goals.models.Goal:
        ...
