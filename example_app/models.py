from django.db import models

from django_goals.models import Goal

from .merge_sort import MergeSort
from .partition_sort import PartitionSort
from .proceed_on_errors import ErrorsBatch


__all__ = [
    'MergeSort',
    'PartitionSort',
    'GoalRelatedModel',
    'ErrorsBatch',
]


class GoalRelatedModel(models.Model):
    """
    Example model that references a goal with PROTECT FK.
    """
    goal = models.ForeignKey(Goal, on_delete=models.PROTECT)
