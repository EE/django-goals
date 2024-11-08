import datetime
import inspect
import logging
import time
import uuid

from django.conf import settings
from django.db import connections, models, transaction
from django.db.models.functions import Least
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
        help_text=_('Goal will not be pursued before this date.'),
    )
    precondition_goals = models.ManyToManyField(
        to='self',
        symmetrical=False,
        related_name='dependent_goals',
        through='GoalDependency',
        blank=True,
    )
    deadline = models.DateTimeField(
        default=timezone.now,
        help_text=_('Goals having deadline sooner will be pursued first.'),
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
                fields=['deadline'],
                condition=models.Q(state=GoalState.WAITING_FOR_WORKER),
                name='goals_waiting_for_worker_idx',
            ),
        ]

    def block(self):
        """
        Mark the goal as blocked, so it will not be pursued.
        """
        if self.state not in (GoalState.WAITING_FOR_DATE, GoalState.WAITING_FOR_PRECONDITIONS):
            raise ValueError(f'Cannot block goal in state {self.state}')
        self.state = GoalState.BLOCKED
        self.save(update_fields=['state'])

    def unblock(self):
        """
        Mark the goal as unblocked, so it can be pursued again.
        """
        if self.state != GoalState.BLOCKED:
            raise ValueError(f'Cannot unblock goal in state {self.state}')
        self.state = GoalState.WAITING_FOR_DATE
        self.save(update_fields=['state'])
        Goal.objects.filter(
            id__in=get_dependent_goal_ids([self.id]),
            state=GoalState.NOT_GOING_TO_HAPPEN_SOON,
        ).update(state=GoalState.WAITING_FOR_DATE)

    def retry(self):
        """
        Mark the goal as ready to be pursued again.
        """
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
    """
    The GoalDependency is a many-to-many through model for Goal.
    Its purpose is to define which goals need to be achieved before another goal can be pursued.
    """
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
    """
    GoalProgress represents a single attempt to achieve a goal.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name='progress')
    success = models.BooleanField()
    created_at = models.DateTimeField(default=timezone.now)
    time_taken = models.DurationField(null=True)
    message = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ('goal', '-created_at')


def worker(stop_event, max_progress_count=float('inf')):
    """
    Worker is a busy-wait function that will keep checking for goals to pursue.
    It will keep running until stop_event is set.
    Process:
    1. Check if there are goals that are waiting for date and the date has come.
    2. Check if there are goals that are waiting for preconditions and all preconditions are achieved.
    3. Check if there are goals that are waiting for worker and pick one to pursue.
    4. If nothing could be done, sleep for a bit.
    5. Repeat until stop_event is set.
    """
    logger.info('Busy-wait worker started')
    progress_count = 0
    while not stop_event.is_set():
        if progress_count >= max_progress_count:
            logger.info('Max transitions reached, exiting')
            break

        now = timezone.now()
        transitions_done, local_progress_count = worker_turn(
            now,
            max_progress_count=max_progress_count - progress_count,
        )
        progress_count += local_progress_count

        if transitions_done == 0 and local_progress_count == 0:
            # nothing could be done, let's go to sleep
            logger.debug('Nothing to do, sleeping for a bit')
            time.sleep(1)

    logger.info('Busy-wait worker exiting')


def worker_turn(now, max_progress_count=float('inf')):
    """
    Worker turn is a single iteration of the worker.
    It will try to transition as many goals as possible.
    Returns a number of transitions done (all state changes)
    and a number of progress transitions done (real handler calls).
    """
    transitions_done = 0
    transitions_done += handle_waiting_for_date(now)
    transitions_done += handle_waiting_for_preconditions()
    progress_count = 0
    while True:
        if progress_count >= max_progress_count:
            break
        did_a_thing = handle_waiting_for_worker_guarded()
        if not did_a_thing:
            break
        transitions_done += 1
        progress_count += 1
    remove_old_goals(now)
    return transitions_done, progress_count


def handle_waiting_for_worker_guarded():
    """
    Wrapper to catch exceptions and mark the goal as corrupted when it happens.
    Some exceptions might be caught and handled by the inner function,
    but transaction management error for example is not recoverable there.
    We need to catch it outside the transaction.
    """
    changed_goal = None
    try:
        progress = handle_waiting_for_worker()
    except Exception as e:  # pylint: disable=broad-except
        logger.exception('Worker failed')
        changed_goal = _handle_corrupted_progress(e)
    else:
        if progress is not None:
            changed_goal = progress.goal
    if changed_goal is not None:
        handle_waiting_for_preconditions(Goal.objects.filter(
            precondition_goals__id=changed_goal.id,
        ))
    return changed_goal is not None


def _handle_corrupted_progress(exc):
    """
    Find the goal that caused the exception and mark it as corrupted.
    """
    traceback = exc.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_name == 'handle_waiting_for_worker':
            break
        traceback = traceback.tb_next
    goal = frame.f_locals['goal']
    with transaction.atomic():
        Goal.objects.filter(id=goal.id).update(state=GoalState.CORRUPTED)
        notify_goal_progress(goal.id, GoalState.CORRUPTED)
    return goal


def handle_waiting_for_date(now):
    """
    Transition goals that are waiting for precondition date and the date has come.
    """
    return Goal.objects.filter(
        state=GoalState.WAITING_FOR_DATE,
        precondition_date__lte=now,
    ).update(state=GoalState.WAITING_FOR_PRECONDITIONS)


def handle_waiting_for_preconditions(goals_qs=None):
    """
    Transition goals that are waiting for precondition goals to be achieved
    and all preconditions are achieved.
    """
    if goals_qs is None:
        goals_qs = Goal.objects.all()
    transitions_done = 0

    with transaction.atomic():
        new_waiting_for_worker = goals_qs.filter(
            state=GoalState.WAITING_FOR_PRECONDITIONS,
        ).annotate(
            num_preconditions=models.Count('precondition_goals'),
            num_achieved_preconditions=models.Count('precondition_goals', filter=models.Q(
                precondition_goals__state=GoalState.ACHIEVED,
            )),
        ).filter(
            num_preconditions=models.F('num_achieved_preconditions'),
        )
        # GROUP BY in the original query is not allowed with FOR UPDATE needed to lock rows.
        # We need to wrap the original query.
        new_waiting_for_worker = Goal.objects.filter(
            id__in=new_waiting_for_worker,
        ).select_for_update(
            no_key=True,
        ).values_list('id', flat=True)
        new_waiting_for_worker = list(new_waiting_for_worker)
        if new_waiting_for_worker:
            Goal.objects.filter(id__in=new_waiting_for_worker).update(state=GoalState.WAITING_FOR_WORKER)
            with connections['default'].cursor() as cursor:
                for goal_id in new_waiting_for_worker:
                    notify_goal_waiting_for_worker(cursor, goal_id)
        transitions_done += len(new_waiting_for_worker)

    # if a goal is waiting for preconditions that are not going to happen soon, it's not going to happen soon either
    transitions_done += goals_qs.filter(
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
def handle_waiting_for_worker():
    """
    Transition goals that are waiting for a worker to pick them up.
    """
    now = timezone.now()
    # Get the first goal that is waiting for a worker
    goal = Goal.objects.filter(state=GoalState.WAITING_FOR_WORKER).order_by(
        'deadline',
    ).select_for_update(
        skip_locked=True,
        no_key=True,
    ).first()
    if goal is None:
        # nothing to do
        return None

    logger.info('Just about to pursue goal %s: %s', goal.id, goal.handler)
    start_time = time.monotonic()
    try:
        try:
            ret = follow_instructions(goal)
        except RetryMeLaterException as e:
            ret = RetryMeLater(**e.__dict__)

    except Exception:  # pylint: disable=broad-except
        logger.exception('Goal %s failed', goal.id)
        success = False
        message = ''
        failure_index = goal.progress.filter(success=False).count()
        retry_delay = get_retry_delay(failure_index)
        if retry_delay is None:
            goal.state = GoalState.GIVEN_UP
        else:
            goal.state = GoalState.WAITING_FOR_DATE
            goal.precondition_date = now + retry_delay

    else:
        if isinstance(ret, RetryMeLater):
            logger.info('Goal %s needs to be retried later. Message: %s', goal.id, ret.message)
            success = True
            message = ret.message
            goal.state = GoalState.WAITING_FOR_DATE
            if ret.precondition_date is not None:
                goal.precondition_date = max(goal.precondition_date, ret.precondition_date)
            add_precondition_goals(goal, ret.precondition_goals)

        elif isinstance(ret, AllDone):
            logger.info('Goal %s was achieved', goal.id)
            success = True
            message = ''
            goal.state = GoalState.ACHIEVED

        else:
            logger.warning('Goal %s handler returned unknown value, which is ignored', goal.id)
            success = True
            message = ''
            goal.state = GoalState.ACHIEVED

    time_taken = time.monotonic() - start_time

    progress = goal.progress.create(
        success=success,
        created_at=now,
        time_taken=datetime.timedelta(seconds=time_taken),
        message=message,
    )

    # check max progress count
    max_progress_count = getattr(settings, 'GOALS_MAX_PROGRESS_COUNT', 100)
    if (
        max_progress_count is not None and
        goal.state != GoalState.ACHIEVED and
        goal.progress.count() >= max_progress_count
    ):
        logger.warning('Goal %s reached max progress count, giving up', goal.id)
        goal.state = GoalState.GIVEN_UP

    goal.save(update_fields=['state', 'precondition_date'])
    notify_goal_progress(goal.id, goal.state)
    return progress


def follow_instructions(goal):
    """
    Call the handler function with instructions.
    """
    func = import_string(goal.handler)
    instructions = goal.instructions
    if instructions is None:
        instructions = {}
    return func(
        goal,
        *instructions.get('args', ()),
        **instructions.get('kwargs', {}),
    )


def get_retry_delay(failure_index):
    """
    Get the delay before retrying the goal.
    """
    max_failures = 3
    if failure_index >= max_failures:
        return None
    return datetime.timedelta(seconds=10) * (2 ** failure_index)


def remove_old_goals(now):
    retention_seconds = getattr(settings, 'GOALS_RETENTION_SECONDS', 60 * 60 * 24 * 7)
    if retention_seconds is None:
        return
    try:
        with transaction.atomic():
            goals_to_delete = Goal.objects.filter(
                state=GoalState.ACHIEVED,
                created_at__lt=now - datetime.timedelta(seconds=retention_seconds),
            )
            GoalDependency.objects.filter(precondition_goal__in=goals_to_delete).delete()
            goals_to_delete.delete()
    except models.ProtectedError as e:
        logger.warning('When cleaning old goals: %s', e)


class RetryMeLater:
    """
    Like a process yielding in operating system.
    """
    def __init__(self, precondition_date=None, precondition_goals=(), message=''):
        self.precondition_date = precondition_date
        self.precondition_goals = precondition_goals
        self.message = message


class RetryMeLaterException(RetryMeLater, Exception):
    pass


class AllDone:
    pass


def schedule(
    func, args=None, kwargs=None,
    precondition_date=None, precondition_goals=None, blocked=False,
    deadline=None,
    listen=False,
):
    """
    Schedule a goal to be pursued.
    """
    state = GoalState.WAITING_FOR_DATE

    instructions = {}
    if args is not None:
        instructions['args'] = args
    if kwargs is not None:
        instructions['kwargs'] = kwargs
    if not instructions:
        instructions = None

    if precondition_date is None:
        precondition_date = timezone.now()
        state = GoalState.WAITING_FOR_PRECONDITIONS
    if precondition_goals is None:
        precondition_goals = []
    if (
        not precondition_goals and
        state == GoalState.WAITING_FOR_PRECONDITIONS
    ):
        state = GoalState.WAITING_FOR_WORKER
    if blocked:
        state = GoalState.BLOCKED
    func_name = inspect.getmodule(func).__name__ + '.' + func.__name__

    if deadline is None:
        default_deadline_delta = datetime.timedelta(
            seconds=getattr(settings, 'GOALS_DEFAULT_DEADLINE_SECONDS', 7 * 24 * 60 * 60),
        )
        deadline = timezone.now() + default_deadline_delta

    goal = Goal(
        state=state,
        handler=func_name,
        instructions=instructions,
        precondition_date=precondition_date,
        deadline=deadline,
    )
    if listen:
        listen_goal_progress(goal.id)

    with transaction.atomic():
        goal.save()
        add_precondition_goals(goal, precondition_goals)
        if state == GoalState.WAITING_FOR_WORKER:
            with connections['default'].cursor() as cursor:
                notify_goal_waiting_for_worker(cursor, goal.id)

    return goal


def add_precondition_goals(goal, precondition_goals):
    goal.precondition_goals.add(*precondition_goals)
    update_goals_deadline(goal.precondition_goals.all(), goal.deadline)


def update_goals_deadline(goals_qs, deadline):
    goals_to_be_updated = list(goals_qs.filter(
        deadline__gt=deadline,
    ).exclude(
        state=GoalState.ACHIEVED,
    ))
    Goal.objects.filter(
        id__in=[goal.id for goal in goals_to_be_updated],
    ).update(deadline=Least('deadline', models.Value(deadline)))
    for goal in goals_to_be_updated:
        update_goals_deadline(goal.precondition_goals.all(), deadline)


def notify_goal_waiting_for_worker(cursor, goal_id):
    """
    Notify that the goal is waiting for a worker to pick it up.
    """
    cursor.execute("NOTIFY goal_waiting_for_worker, %s", [str(goal_id)])


def notify_goal_progress(goal_id, state):
    """
    Notify that the goal has changed its state.
    """
    with connections['default'].cursor() as cursor:
        channel = get_goal_progress_channel(goal_id)
        cursor.execute(f"NOTIFY {channel}, %s", [
            state,
        ])


def listen_goal_progress(goal_id):
    """
    Listen for goal progress notifications.
    """
    with connections['default'].cursor() as cursor:
        channel = get_goal_progress_channel(goal_id)
        cursor.execute(f'LISTEN {channel}')


def get_goal_progress_channel(goal_id):
    """
    Get the channel name for goal progress notifications.
    """
    return f'goal_progress_{goal_id.hex}'


def wait():
    """
    Wait for a goal progress notification.
    """
    pg_conn = connections['default'].connection
    notification_generator = pg_conn.notifies()
    for notification in notification_generator:
        notification_generator.close()
    return notification  # pylint: disable=undefined-loop-variable


def get_dependent_goal_ids(goal_ids):
    """
    Get the IDs of goals that depend on the given goals.
    """
    goal_ids = list(goal_ids)
    qs = GoalDependency.objects.raw(
        """
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
        """,
        {'goal_ids': goal_ids},
    )
    return [obj.id for obj in qs]
