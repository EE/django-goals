from django.db import models

from django_goals.models import Goal


class GoalRelatedModel(models.Model):
    goal = models.ForeignKey(Goal, on_delete=models.PROTECT)
