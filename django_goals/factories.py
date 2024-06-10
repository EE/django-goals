import factory

from .models import Task


class TaskFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Task
        skip_postgeneration_save = True

    @factory.post_generation
    def precondition_tasks(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            self.precondition_tasks.set(extracted)
