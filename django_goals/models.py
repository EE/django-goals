import datetime
import inspect
import logging
import threading
import time
import uuid

from django.conf import settings
from django.db import connections, models, transaction
from django.db.models.functions import Least
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from .limits import limit_memory, limit_time
from .notifications import (
    listen_goal_progress, notify_goal_progress, notify_goal_waiting_for_worker,
)


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
    # Goal is waiting on a precondition that wont be achieved
    NOT_GOING_TO_HAPPEN_SOON = 'not_going_to_happen_soon'


NOT_GOING_TO_HAPPEN_SOON_STATES = (
    GoalState.BLOCKED,
    GoalState.GIVEN_UP,
    GoalState.NOT_GOING_TO_HAPPEN_SOON,
)


WAITING_STATES = (
    GoalState.WAITING_FOR_DATE,
    GoalState.WAITING_FOR_PRECONDITIONS,
    GoalState.WAITING_FOR_WORKER,
)


class PreconditionsMode(models.TextChoices):
    ALL = 'all', _('All preconditions must be achieved before the goal can be pursued.')
    ANY = 'any', _('Goal can be pursued if any of the preconditions is achieved.')


class PreconditionFailureBehavior(models.TextChoices):
    BLOCK = 'block', _('Do not proceed if preconditions fail')
    PROCEED = 'proceed', _('Proceed with goal execution even if preconditions fail')


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
    preconditions_mode = models.CharField(
        max_length=3,
        choices=PreconditionsMode.choices,
        default=PreconditionsMode.ALL,
    )
    precondition_failure_behavior = models.CharField(
        max_length=10,
        choices=PreconditionFailureBehavior.choices,
        default=PreconditionFailureBehavior.BLOCK,
    )
    waiting_for_count = models.IntegerField(
        default=0,
        help_text=_('Number of precondition goals that must finish before this goal can be pursued.'),
    )
    waiting_for_not_achieved_count = models.IntegerField(  # for ALL mode this is the same as waiting_for_count
        default=0,
        help_text=_('Number of precondition goals that are not achieved yet.'),
    )
    waiting_for_failed_count = models.IntegerField(
        default=0,
        help_text=_('Number of precondition goals that failed.'),
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
                models.Q(waiting_for_count__lte=0),
                condition=models.Q(state=GoalState.WAITING_FOR_PRECONDITIONS),
                name='goals_waiting_for_precond_idx',
            ),
            models.Index(
                fields=['deadline'],
                condition=models.Q(state=GoalState.WAITING_FOR_WORKER),
                name='goals_waiting_for_worker_idx',
            ),
            models.Index(  # for blocking goals that are waiting for blocked preconds
                fields=['waiting_for_failed_count'],
                condition=models.Q(
                    state=GoalState.WAITING_FOR_PRECONDITIONS,
                    precondition_failure_behavior=PreconditionFailureBehavior.BLOCK,
                ),
                name='goals_waiting_for_failed_idx',
            ),
            models.Index(  # for unblocking goals when preconditions becomes unblocked
                fields=['waiting_for_failed_count'],
                condition=models.Q(state=GoalState.NOT_GOING_TO_HAPPEN_SOON),
                name='goals_unblocking_idx',
            ),
            models.Index(  # for deleting old done goals
                fields=['created_at'],
                condition=models.Q(state=GoalState.ACHIEVED),
                name='goals_achieved_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    waiting_for_count__lte=1,
                    preconditions_mode=PreconditionsMode.ANY,
                ) | models.Q(
                    preconditions_mode=PreconditionsMode.ALL,
                ),
                name='goals_waiting_for_count_any',
            ),
            models.CheckConstraint(
                condition=models.Q(precondition_failure_behavior__in=PreconditionFailureBehavior.values),
                name='goals_precondition_failure_behavior',
            ),
        ]

    def __str__(self):
        return f'{self.handler} ({self.state})'


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


@transaction.atomic
def block_goal(goal_id):
    """
    Mark the goal as blocked, so it will not be pursued.
    """
    goal = Goal.objects.select_for_update().get(id=goal_id)
    if goal.state not in WAITING_STATES:
        raise ValueError(f'Cannot block goal in state {goal.state}')
    _mark_as_failed([goal_id], target_state=GoalState.BLOCKED)
    return goal


@transaction.atomic
def unblock_retry_goal(goal_id):
    """
    Mark the goal as unblocked, so it can be pursued again.
    """
    goal = Goal.objects.select_for_update().get(id=goal_id)
    if goal.state not in NOT_GOING_TO_HAPPEN_SOON_STATES:
        raise ValueError(f'Cannot unblock/retry goal in state {goal.state}')
    _mark_as_unfailed([goal_id])
    return goal


def _mark_as_failed(goal_ids, target_state):
    """
    All goal must be in waititng state.
    You must be in a transaction and have a lock on the goals.
    """
    assert target_state in NOT_GOING_TO_HAPPEN_SOON_STATES
    if not goal_ids:
        return
    Goal.objects.filter(id__in=goal_ids).update(state=target_state)
    # update waiting-for failed count in dependent goals
    Goal.objects.filter(
        precondition_goals__id__in=goal_ids,
    ).update(
        waiting_for_failed_count=models.F('waiting_for_failed_count') + 1,
    )
    Goal.objects.filter(
        precondition_goals__id__in=goal_ids,
        precondition_failure_behavior=PreconditionFailureBehavior.PROCEED,
    ).update(
        waiting_for_count=models.F('waiting_for_count') - 1,
    )


def _mark_as_unfailed(goal_ids):
    """
    All goal_ids must be in NOT_GOING_TO_HAPPEN_SOON_STATES.
    You must be in a transaction and have a lock on the goals.
    """
    if not goal_ids:
        return
    Goal.objects.filter(id__in=goal_ids).update(state=GoalState.WAITING_FOR_DATE)
    # update waiting-for failed count in dependent goals
    Goal.objects.filter(
        precondition_goals__id__in=goal_ids,
    ).update(
        waiting_for_failed_count=models.F('waiting_for_failed_count') - 1,
    )


def worker(stop_event=None, max_progress_count=float('inf'), once=False):
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
    while (
        stop_event is None or
        not stop_event.is_set()
    ):
        if progress_count >= max_progress_count:
            logger.info('Max transitions reached, exiting')
            break

        transitions_done, local_progress_count = worker_turn(
            stop_event=stop_event,
            max_progress_count=max_progress_count - progress_count,
        )
        progress_count += local_progress_count

        if transitions_done == 0 and local_progress_count == 0:
            if once:
                logger.info('Nothing to do, exiting because of `once` flag')
                break
            # nothing could be done, let's go to sleep
            logger.debug('Nothing to do, sleeping for a bit')
            time.sleep(1)

    logger.info('Busy-wait worker exiting')


def worker_turn(now=None, stop_event=None, max_progress_count=float('inf')):
    """
    Worker turn is a single iteration of the worker.
    It will try to transition as many goals as possible.
    Returns a number of transitions done (all state changes)
    and a number of progress transitions done (real handler calls).
    """
    if now is None:
        now = timezone.now()
    transitions_done = 0
    transitions_done += handle_waiting_for_date(now)
    transitions_done += handle_waiting_for_preconditions()
    transitions_done += handle_waiting_for_failed_preconditions()
    transitions_done += handle_unblocked_goals()
    progress_count = 0
    while (
        stop_event is None or
        not stop_event.is_set()
    ):
        if progress_count >= max_progress_count:
            break
        did_a_thing = handle_waiting_for_worker()
        if not did_a_thing:
            break
        transitions_done += 1
        progress_count += 1
    remove_old_goals(now)
    return transitions_done, progress_count


@transaction.atomic
def handle_waiting_for_date(now=None):
    """
    Transition goals that are waiting for precondition date and the date has come.
    """
    if now is None:
        now = timezone.now()
    qs = Goal.objects.filter(
        state=GoalState.WAITING_FOR_DATE,
        precondition_date__lte=now,
    ).select_for_update(
        skip_locked=True,
        no_key=True,
    )
    ids = list(qs.values_list('id', flat=True))
    return Goal.objects.filter(
        id__in=ids,
    ).update(state=GoalState.WAITING_FOR_PRECONDITIONS)


@transaction.atomic
def handle_waiting_for_preconditions(goals_qs=None):
    """
    Transition goals that are waiting for precondition goals to be achieved
    and all (or any, depending on mode) preconditions are achieved.
    """
    if goals_qs is None:
        goals_qs = Goal.objects.all()
    transitions_done = 0

    new_waiting_for_worker = goals_qs.filter(
        state=GoalState.WAITING_FOR_PRECONDITIONS,
        waiting_for_count__lte=0,
    ).select_for_update(
        no_key=True,
        skip_locked=True,
    ).values_list('id', flat=True)
    new_waiting_for_worker = list(new_waiting_for_worker)
    if new_waiting_for_worker:
        Goal.objects.filter(id__in=new_waiting_for_worker).update(state=GoalState.WAITING_FOR_WORKER)
        with connections['default'].cursor() as cursor:
            for goal_id in new_waiting_for_worker:
                notify_goal_waiting_for_worker(cursor, goal_id)
    transitions_done += len(new_waiting_for_worker)

    return transitions_done


@transaction.atomic
def handle_waiting_for_failed_preconditions():
    """
    if a goal is waiting for preconditions that are failed, it's not going to happen soon
    """
    goals_qs = Goal.objects.all()
    transitions_done = 0

    new_failed = goals_qs.filter(
        state=GoalState.WAITING_FOR_PRECONDITIONS,
        precondition_failure_behavior=PreconditionFailureBehavior.BLOCK,
        waiting_for_failed_count__gt=0,
    ).select_for_update(
        no_key=True,
        skip_locked=True,
    ).values_list('id', flat=True)
    new_failed = list(new_failed)
    _mark_as_failed(new_failed, target_state=GoalState.NOT_GOING_TO_HAPPEN_SOON)
    transitions_done += len(new_failed)

    return transitions_done


@transaction.atomic
def handle_unblocked_goals():
    """
    Transition goals that have no failed preconditions, yet are marked as not going to happen soon.
    """
    qs = Goal.objects.filter(
        state=GoalState.NOT_GOING_TO_HAPPEN_SOON,
        waiting_for_failed_count__lte=0,
    ).select_for_update(
        skip_locked=True,
        no_key=True,
    )
    ids = list(qs.values_list('id', flat=True))
    _mark_as_unfailed(ids)
    return len(ids)


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

    if now < goal.precondition_date:
        logger.warning('Precondition date bug in goal %s. Precondition date is in the future', goal.id)
    if (
        goal.preconditions_mode == PreconditionsMode.ALL and
        goal.waiting_for_count != 0
    ):
        logger.warning('Waiting-for bug in goal %s. Expected 0, got %s', goal.id, goal.waiting_for_count)
    if (
        goal.preconditions_mode == PreconditionsMode.ANY and
        goal.waiting_for_count > 0
    ):
        logger.warning('Waiting-for (ANY mode) bug in goal %s. Expected 0 or less, got %s', goal.id, goal.waiting_for_count)
    if goal.waiting_for_failed_count < 0:
        logger.warning('Waiting-for-failed bug in goal %s. Expected 0 or more, got %s', goal.id, goal.waiting_for_failed_count)
    if (
        goal.precondition_failure_behavior == PreconditionFailureBehavior.BLOCK and
        goal.waiting_for_failed_count != 0
    ):
        logger.warning('Waiting-for-failed (BLOCK mode) bug in goal %s. Expected 0, got %s', goal.id, goal.waiting_for_failed_count)

    logger.info('Just about to pursue goal %s: %s', goal.id, goal.handler)
    start_time = time.monotonic()
    try:
        ret = follow_instructions(goal)

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
            _add_precondition_goals(goal, ret.precondition_goals)

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

    # decrease waiting-for counter in dependent goals
    if goal.state == GoalState.ACHIEVED:
        Goal.objects.filter(
            precondition_goals=goal,
        ).update(
            waiting_for_count=models.F('waiting_for_count') - 1,
            waiting_for_not_achieved_count=models.F('waiting_for_not_achieved_count') - 1,
        )

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

    # incrase waiting-for failed count in dependent goals
    if goal.state == GoalState.GIVEN_UP:
        # goal.state will be saved twice, but it's fine
        _mark_as_failed([goal.id], target_state=GoalState.GIVEN_UP)

    goal.save(update_fields=['state', 'precondition_date'])
    notify_goal_progress(goal.id, goal.state)
    return progress


class GoalsThreadLocal(threading.local):
    def __init__(self):
        self.current_goal = None


thread_local = GoalsThreadLocal()


@limit_time()
@limit_memory()
# This is a savepoint that protects worker transaction so that we can always report progress.
# Raw error in trnsaction would prevent from executing any queries inside it.
@transaction.atomic
def follow_instructions(goal):
    """
    Call the handler function with instructions.
    """
    func = import_string(goal.handler)
    instructions = goal.instructions
    if instructions is None:
        instructions = {}
    assert thread_local.current_goal is None
    thread_local.current_goal = goal
    try:
        return func(
            goal,
            *instructions.get('args', ()),
            **instructions.get('kwargs', {}),
        )
    except RetryMeLaterException as e:
        return RetryMeLater(**e.__dict__)
    finally:
        thread_local.current_goal = None


def get_retry_delay(failure_index):
    """
    Get the delay before retrying the goal.
    failure_index is how many times the goal has failed (before this time). So first time this is called with 0.
    """
    current_failure_index = failure_index + 1
    give_up_at = getattr(settings, 'GOALS_GIVE_UP_AT', 4)
    if current_failure_index >= give_up_at:
        return None
    return datetime.timedelta(seconds=10) * (2 ** failure_index)


def remove_old_goals(now=None):
    if now is None:
        now = timezone.now()
    retention_seconds = getattr(settings, 'GOALS_RETENTION_SECONDS', 60 * 60 * 24 * 7)
    if retention_seconds is None:
        return 0
    try:
        with transaction.atomic():
            ids_to_delete = Goal.objects.filter(
                state=GoalState.ACHIEVED,
                created_at__lt=now - datetime.timedelta(seconds=retention_seconds),
            ).select_for_update(
                skip_locked=True,
            ).values_list('id', flat=True)
            ids_to_delete = list(ids_to_delete[:100])
            if not ids_to_delete:
                return 0
            GoalDependency.objects.filter(precondition_goal_id__in=ids_to_delete).delete()
            GoalDependency.objects.filter(dependent_goal_id__in=ids_to_delete).delete()
            Goal.objects.filter(id__in=ids_to_delete).delete()
            logger.info('Deleted %s old, achieved goals', len(ids_to_delete))
            return len(ids_to_delete)
    except models.ProtectedError as e:
        logger.warning('When cleaning old goals: %s', e)
        return 0


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
    preconditions_mode=PreconditionsMode.ALL,
    precondition_failure_behavior=PreconditionFailureBehavior.BLOCK,
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

    if deadline is None and thread_local.current_goal is not None:
        deadline = thread_local.current_goal.deadline
    elif deadline is None:
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
        preconditions_mode=preconditions_mode,
        precondition_failure_behavior=precondition_failure_behavior,
    )
    if listen:
        listen_goal_progress(goal.id)

    with transaction.atomic():
        goal.save()
        _add_precondition_goals(goal, precondition_goals)
        if state == GoalState.WAITING_FOR_WORKER:
            with connections['default'].cursor() as cursor:
                notify_goal_waiting_for_worker(cursor, goal.id)

    return goal


def _add_precondition_goals(goal, precondition_goals):
    # We can be sure current waiting count are zero, because goal can add preconditions only
    # in the handler or when scheduling a new goal.
    # However, waiting_for_not_achieved_count can be non-zero when running in ANY mode,
    # and waiting_for_failed_count can be non-zero when running in PROCEED precond failure mode.
    goal.waiting_for_count = 0

    if precondition_goals is None:
        goal.save(update_fields=[
            'waiting_for_count',
        ])
        return

    # Lock and retrieve new precondition goals that aren't already dependencies
    # This locking is critical to prevent a race condition where:
    # 1. We check a precondition goal's state (not achieved)
    # 2. Before we create the dependency, that precondition becomes achieved
    # 3. The achievement event can't decrease our waiting_for_count because the dependency doesn't exist yet
    # 4. We set waiting_for_count=1 based on step 1's observation
    # 5. Result: waiting_for_count stays at 1 forever, never reaching zero
    new_precondition_goals = list(Goal.objects.filter(
        id__in=[g.id for g in precondition_goals],
    ).exclude(
        dependent_goals=goal,
    ).select_for_update(no_key=True))

    # add to our preconditions
    goal.precondition_goals.add(*new_precondition_goals)

    # update waiting-for counters
    for precondition_goal in new_precondition_goals:
        if precondition_goal.state != GoalState.ACHIEVED:
            goal.waiting_for_count += 1
            goal.waiting_for_not_achieved_count += 1
        if precondition_goal.state in NOT_GOING_TO_HAPPEN_SOON_STATES:
            goal.waiting_for_failed_count += 1

    if goal.precondition_failure_behavior == PreconditionFailureBehavior.PROCEED:
        # in PROCEED precond mode, failed preconditions are treated like achieved
        goal.waiting_for_count -= goal.waiting_for_failed_count

    if goal.preconditions_mode == PreconditionsMode.ANY:
        # cap waiting_for_count at 1 in ANY mode
        goal.waiting_for_count = min(goal.waiting_for_count, 1)
        # ensure we are waiting for something if there are any not achieved preconditions
        if goal.waiting_for_not_achieved_count > 0:
            goal.waiting_for_count = 1
        # Detect the case where some precondition completed in the span between
        # handler checked the preconditions and we locked them.
        # We assume that precondition_goals contain the version checked by the handler.
        # This is releavnt only for ANY precond mode - because we are interested in act of
        # precond becoming achieved, not the final state like in ALL mode.
        orig_preconds_by_id = {g.id: g for g in precondition_goals}
        for precondition_goal in new_precondition_goals:
            orig_goal = orig_preconds_by_id[precondition_goal.id]
            if (
                orig_goal.state != GoalState.ACHIEVED and
                precondition_goal.state == GoalState.ACHIEVED
            ) or (
                goal.precondition_failure_behavior == PreconditionFailureBehavior.PROCEED and
                orig_goal.state not in NOT_GOING_TO_HAPPEN_SOON_STATES and
                precondition_goal.state in NOT_GOING_TO_HAPPEN_SOON_STATES
            ):
                goal.waiting_for_count = 0

    goal.save(update_fields=[
        'waiting_for_count',
        'waiting_for_not_achieved_count',
        'waiting_for_failed_count',
    ])

    # move deadline earlier for preconditions, if needed
    update_goals_deadline(Goal.objects.filter(
        id__in=[g.id for g in new_precondition_goals],
    ), goal.deadline)


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
