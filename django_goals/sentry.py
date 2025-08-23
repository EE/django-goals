import sentry_sdk


class Middleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, goal, *args, **kwargs):
        headers = (goal.instructions or {}).get('sentry', {})
        transaction = sentry_sdk.continue_trace(
            headers,
            op='queue.process',
            name=goal.handler,
        )
        with sentry_sdk.start_transaction(transaction):
            transaction.set_data('messaging.message.id', str(goal.id))
            transaction.set_data('messaging.destination.name', goal.handler)
            progress = self.get_response(goal, *args, **kwargs)
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
    def __init__(self, schedule):
        self.schedule = schedule

    def __call__(self, goal, *args, **kwargs):
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
            return self.schedule(goal, *args, **kwargs)
