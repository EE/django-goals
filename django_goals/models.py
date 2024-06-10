import datetime
import inspect
import logging
import time
import uuid

from django.db import models, transaction
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _


logger = logging.getLogger(__name__)


class GoalState(models.TextChoices):
    # Goal is explicitly marked not to be pursued
    BLOCKED = 'blocked'
    # Goal cannot be pursued yet, because it is allowed only after future date
    WAITING_FOR_DATE = 'waiting_for_date'
    # Goal cannot be pursued yet, because other goals need to be achieved first
    WAITING_FOR_PRECONDITIONS = 'waiting_for_preconditions'
    # Goal is ready to be pursued. We are waiting for a worker to pick it up
    WAITING_FOR_WORKER = 'waiting_for_worker'
    # The goal has been achieved
    ACHIEVED = 'achieved'
    # Too many failed attempts when pursuing the goal
    GIVEN_UP = 'given_up'
    # transaction error happened during execution, so we cant even properly store failure
    CORRUPTED = 'corrupted'
    # Goal is waiting on a precondition that wont be achieved
    NOT_GOING_TO_HAPPEN_SOON = 'not_going_to_happen_soon'


class Goal(models.Model):
    """
    Goal represents a state we want to achieve.
    Goal will be pursued by calling a handler function.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state = models.CharField(
        max_length=30,
        db_index=True,
        choices=GoalState.choices,
        default=GoalState.WAITING_FOR_DATE,
    )
    handler = models.CharField(max_length=100)
    instructions = models.JSONField(null=True)
    precondition_date = models.DateTimeField(
        default=timezone.now,
        help_text=_(
            'Goal will not be pursued before this date. '
            'Also used as priority for goals that are waiting for worker - '
            'goals with earlier date will be preferred.'
        ),
    )
    precondition_goals = models.ManyToManyField(
        to='self',
        symmetrical=False,
        related_name='dependent_goals',
        through='GoalDependency',
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(
                fields=['precondition_date'],
                condition=models.Q(state=GoalState.WAITING_FOR_DATE),
                name='goals_waiting_for_date_idx',
            ),
            models.Index(
                fields=['precondition_date'],
                condition=models.Q(state=GoalState.WAITING_FOR_WORKER),
                name='goals_waiting_for_worker_idx',
            ),
        ]

    def block(self):
        if self.state not in (GoalState.WAITING_FOR_DATE, GoalState.WAITING_FOR_PRECONDITIONS):
            raise ValueError(f'Cannot block goal in state {self.state}')
        self.state = GoalState.BLOCKED
        self.save(update_fields=['state'])

    def unblock(self):
        if self.state != GoalState.BLOCKED:
            raise ValueError(f'Cannot unblock goal in state {self.state}')
        self.state = GoalState.WAITING_FOR_DATE
        self.save(update_fields=['state'])
        Goal.objects.filter(
            id__in=get_dependent_goal_ids([self.id]),
            state=GoalState.NOT_GOING_TO_HAPPEN_SOON,
        ).update(state=GoalState.WAITING_FOR_DATE)

    def retry(self):
        if self.state not in (GoalState.GIVEN_UP, GoalState.CORRUPTED):
            raise ValueError(f'Cannot retry goal in state {self.state}')
        self.state = GoalState.WAITING_FOR_DATE
        self.save(update_fields=['state'])
        dependent_goal_ids = get_dependent_goal_ids([self.id])
        Goal.objects.filter(
            id__in=dependent_goal_ids,
            state=GoalState.NOT_GOING_TO_HAPPEN_SOON,
        ).update(state=GoalState.WAITING_FOR_DATE)
        return dependent_goal_ids


class GoalDependency(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dependent_goal = models.ForeignKey(
        to=Goal,
        on_delete=models.CASCADE,
        related_name='dependencies',
    )
    precondition_goal = models.ForeignKey(
        to=Goal,
        on_delete=models.PROTECT,
        related_name='dependents',
    )

    class Meta:
        unique_together = (
            ('dependent_goal', 'precondition_goal'),
        )


class GoalProgress(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name='progress')
    success = models.BooleanField()
    created_at = models.DateTimeField(default=timezone.now)
    time_taken = models.DurationField(null=True)

    class Meta:
        ordering = ('goal', '-created_at')


def worker(stop_event):
    logger.info('Worker starting')
    while not stop_event.is_set():
        now = timezone.now()
        transitions_done = worker_turn(now)
        if transitions_done == 0:
            # nothing could be done, let's go to sleep
            logging.debug('Nothing to do, sleeping for a bit')
            time.sleep(1)
    logger.info('Worker exiting')


def worker_turn(now):
    transitions_done = 0
    transitions_done += handle_waiting_for_date(now)
    transitions_done += handle_waiting_for_preconditions()
    while True:
        try:
            did_a_thing = handle_waiting_for_worker(now)
        except Exception as e:  # pylint: disable=broad-except
            logger.exception('Worker failed')
            _handle_corrupted_progress(e)
            did_a_thing = True
        if not did_a_thing:
            break
        transitions_done += 1
    return transitions_done


def _handle_corrupted_progress(exc):
    # retrieve goal from the traceback and mark it as corrupted
    traceback = exc.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_name == 'handle_waiting_for_worker':
            break
        traceback = traceback.tb_next
    goal = frame.f_locals['goal']
    Goal.objects.filter(id=goal.id).update(state=GoalState.CORRUPTED)


def handle_waiting_for_date(now):
    return Goal.objects.filter(
        state=GoalState.WAITING_FOR_DATE,
        precondition_date__lte=now,
    ).update(state=GoalState.WAITING_FOR_PRECONDITIONS)


def handle_waiting_for_preconditions():
    transitions_done = 0

    transitions_done += Goal.objects.filter(
        state=GoalState.WAITING_FOR_PRECONDITIONS,
    ).annotate(
        num_preconditions=models.Count('precondition_goals'),
        num_achieved_preconditions=models.Count('precondition_goals', filter=models.Q(
            precondition_goals__state=GoalState.ACHIEVED,
        )),
    ).filter(
        num_preconditions=models.F('num_achieved_preconditions'),
    ).update(state=GoalState.WAITING_FOR_WORKER)

    # if a goal is waiting for preconditions that are not going to happen soon, it's not going to happen soon either
    transitions_done += Goal.objects.filter(
        state=GoalState.WAITING_FOR_PRECONDITIONS,
        precondition_goals__state__in=(
            GoalState.BLOCKED,
            GoalState.GIVEN_UP,
            GoalState.CORRUPTED,
            GoalState.NOT_GOING_TO_HAPPEN_SOON,
        ),
    ).update(state=GoalState.NOT_GOING_TO_HAPPEN_SOON)

    return transitions_done


@transaction.atomic
def handle_waiting_for_worker(now):
    goal = Goal.objects.filter(state=GoalState.WAITING_FOR_WORKER).order_by(
        'precondition_date',
    ).select_for_update(skip_locked=True).first()
    if goal is None:
        # nothing to do
        return False

    logger.info('Just about to pursue goal %s: %s', goal.id, goal.handler)
    start_time = time.monotonic()
    try:
        ret = follow_instructions(goal)

    except Exception:  # pylint: disable=broad-except
        logger.exception('Goal %s failed', goal.id)
        success = False
        failure_index = goal.progress.filter(success=False).count()
        retry_delay = get_retry_delay(failure_index)
        if retry_delay is None:
            goal.state = GoalState.GIVEN_UP
        else:
            goal.state = GoalState.WAITING_FOR_DATE
            goal.precondition_date = now + retry_delay

    else:
        if isinstance(ret, RetryMeLater):
            logger.info('Goal %s needs to be retried later', goal.id)
            success = True
            goal.state = GoalState.WAITING_FOR_DATE
            # move scheduled time forward to avoid starving other goals, in the case this one wants to be retried often
            goal.precondition_date = now
            goal.precondition_goals.add(*ret.precondition_goals)

        elif isinstance(ret, AllDone):
            logger.info('Goal %s was achieved', goal.id)
            success = True
            goal.state = GoalState.ACHIEVED

        else:
            logger.warning('Goal %s handler returned unknown value, which is ignored', goal.id)
            success = True
            goal.state = GoalState.ACHIEVED

    time_taken = time.monotonic() - start_time

    GoalProgress.objects.create(
        goal=goal,
        success=success,
        created_at=now,
        time_taken=datetime.timedelta(seconds=time_taken),
    )
    goal.save(update_fields=['state', 'created_at', 'precondition_date'])
    return True


def follow_instructions(goal):
    func = import_string(goal.handler)
    instructions = goal.instructions
    return func(goal, *instructions['args'], **instructions['kwargs'])


def get_retry_delay(failure_index):
    max_failures = 3
    if failure_index >= max_failures:
        return None
    return datetime.timedelta(seconds=10) * (2 ** failure_index)


class RetryMeLater:
    """
    Like a process yielding in operating system.
    """
    def __init__(self, precondition_goals=()):
        self.precondition_goals = precondition_goals


class AllDone:
    pass


def schedule(
    func, args=None, kwargs=None,
    precondition_date=None, precondition_goals=None, blocked=False,
):
    if args is None:
        args = []
    if kwargs is None:
        kwargs = {}
    if precondition_date is None:
        precondition_date = timezone.now()
    if precondition_goals is None:
        precondition_goals = []
    func_name = inspect.getmodule(func).__name__ + '.' + func.__name__

    with transaction.atomic():
        goal = Goal.objects.create(
            state=GoalState.BLOCKED if blocked else GoalState.WAITING_FOR_DATE,
            handler=func_name,
            instructions={
                'args': args,
                'kwargs': kwargs,
            },
            precondition_date=precondition_date,
        )
        goal.precondition_goals.set(precondition_goals)

    return goal


def get_dependent_goal_ids(goal_ids):
    goal_ids = list(goal_ids)
    qs = GoalDependency.objects.raw(
        '''
        WITH RECURSIVE dependent_goals AS (
            SELECT id FROM django_goals_goal
            WHERE id = ANY(%(goal_ids)s)
        UNION
            SELECT django_goals_goal.id
            FROM django_goals_goal
            JOIN django_goals_goaldependency
            ON django_goals_goal.id = django_goals_goaldependency.dependent_goal_id
            JOIN dependent_goals
            ON django_goals_goaldependency.precondition_goal_id = dependent_goals.id
        )
        SELECT id FROM dependent_goals
        ''',
        {'goal_ids': goal_ids},
    )
    return [obj.id for obj in qs]
