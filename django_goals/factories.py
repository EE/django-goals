from typing import Iterable

import factory

from .models import Goal, GoalProgress


class GoalFactory(factory.django.DjangoModelFactory[Goal]):
    class Meta:
        model = Goal
        skip_postgeneration_save = True

    @factory.post_generation  # type: ignore
    def precondition_goals(self, create: bool, extracted: Iterable[Goal]) -> None:
        if not create:
            return

        if extracted:
            self.precondition_goals.set(extracted)


class GoalProgressFactory(factory.django.DjangoModelFactory[GoalProgress]):
    class Meta:
        model = GoalProgress

    goal = factory.SubFactory(GoalFactory)
    success = True
