import datetime
from typing import Iterable

import sentry_sdk

import django_goals.models

from .types import PursueGoalT, ScheduleGoalT


class Middleware:
    def __init__(self, get_response: PursueGoalT):
        self.get_response = get_response

    def __call__(
        self,
        goal: 'django_goals.models.Goal',
        now: datetime.datetime,
        pickup_monitor: object | None = None,
    ) -> 'django_goals.models.GoalProgress | None':
        headers = (goal.instructions or {}).get('sentry', {})
        transaction = sentry_sdk.continue_trace(
            headers,
            op='queue.process',
            name=goal.handler,
        )
        with sentry_sdk.start_transaction(transaction):
            transaction.set_data('messaging.message.id', str(goal.id))
            transaction.set_data('messaging.destination.name', goal.handler)
            progress = self.get_response(goal, now, pickup_monitor=pickup_monitor)
            transaction.set_data('goal.final_state', goal.state)
            if progress:
                transaction.set_data('goal.progress.success', progress.success)
                transaction.set_data('goal.progress.message', progress.message)
                transaction.set_data(
                    'goal.progress.time_taken',
                    progress.time_taken.total_seconds() if progress.time_taken else None,
                )
            return progress


class ScheduleMiddleware:
    def __init__(self, schedule: ScheduleGoalT) -> None:
        self.schedule = schedule

    def __call__(
        self,
        goal: 'django_goals.models.Goal',
        precondition_goals: Iterable['django_goals.models.Goal'] | None,
        listen: bool,
    ) -> 'django_goals.models.Goal':
        goal.instructions = goal.instructions or {}
        goal.instructions['sentry'] = {
            "sentry-trace": sentry_sdk.get_traceparent(),
            "baggage": sentry_sdk.get_baggage(),
        }
        with sentry_sdk.start_span(
            op='queue.schedule',
            name='Django Goals schedule',
        ) as span:
            span.set_data('messaging.message.id', str(goal.id))
            span.set_data('messaging.destination.name', goal.handler)
            return self.schedule(goal, precondition_goals, listen)
