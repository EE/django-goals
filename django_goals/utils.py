from django.db import models

from .models import Goal, GoalState


def is_goal_completed(goal):
    return (
        goal is None or  # completed some time ago and the goal was garbage collected
        goal.state == GoalState.ACHIEVED
    )


def is_goal_processing(goal):
    return goal is not None and goal.state in (
        GoalState.WAITING_FOR_DATE,
        GoalState.WAITING_FOR_PRECONDITIONS,
        GoalState.WAITING_FOR_WORKER,
        GoalState.BLOCKED,  # we assume it will be unblocked and continue processing
    )


def is_goal_error(goal):
    return goal is not None and goal.state in (
        GoalState.CORRUPTED,
        GoalState.GIVEN_UP,
        GoalState.NOT_GOING_TO_HAPPEN_SOON,
    )


class GoalRelatedMixin(models.Model):
    class Meta:
        abstract = True

    processed_goal = models.OneToOneField(
        to=Goal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    @property
    def is_completed(self):
        return is_goal_completed(self.processed_goal)

    is_done = is_completed

    @property
    def is_processing(self):
        return is_goal_processing(self.processed_goal)

    @property
    def is_error(self):
        return is_goal_error(self.processed_goal)
