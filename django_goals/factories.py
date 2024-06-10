import factory

from .models import Goal


class GoalFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Goal
        skip_postgeneration_save = True

    @factory.post_generation
    def precondition_goals(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            self.precondition_goals.set(extracted)
