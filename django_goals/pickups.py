import logging
import queue
import threading
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


logger = logging.getLogger(__name__)


class GoalPickup(models.Model):
    """
    Stores what goals were picked in a transaction-independent and work-independent way.
    We use this to detect "killer tasks".
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    goal = models.ForeignKey(to='Goal', on_delete=models.CASCADE, related_name='pickups')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-created_at',)


class PickupMonitorThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.event_queue = queue.Queue()

    def run(self):
        logger.info('Pickup monitor thread started')

        while True:
            try:
                event, goal_id = self.event_queue.get(timeout=1)
            except queue.Empty:
                continue
            except queue.ShutDown:
                break

            if event == 'pickup':
                GoalPickup.objects.create(goal_id=goal_id)
            elif event == 'release':
                GoalPickup.objects.filter(goal_id=goal_id).delete()

        logger.info('Pickup monitor thread exiting')

    def pickup(self, goal_id):
        self.event_queue.put(('pickup', goal_id))

    def release(self, goal_id):
        self.event_queue.put(('release', goal_id))

    def shutdown(self):
        self.event_queue.shutdown()


class Middleware:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def __call__(self, goal, now, pickup_monitor=None):
        from .models import GoalState, _mark_as_failed

        # is it a killer task?
        GOALS_MAX_PICKUPS = getattr(settings, 'GOALS_MAX_PICKUPS', None)
        if (
            GOALS_MAX_PICKUPS is not None and
            goal.pickups.count() >= GOALS_MAX_PICKUPS
        ):
            logger.warning('Goal %s is a killer task, not pursuing it', goal.id)
            _mark_as_failed([goal.id], target_state=GoalState.IT_IS_A_KILLER_TASK)
            return None

        if pickup_monitor is not None:
            pickup_monitor.pickup(goal.id)

        progress = self.wrapped(goal, now, pickup_monitor=pickup_monitor)

        if pickup_monitor is not None:
            pickup_monitor.release(goal.id)

        return progress
