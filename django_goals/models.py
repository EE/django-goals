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


class TaskState(models.TextChoices):
    # Task is explicitly marked not to be executed
    BLOCKED = 'blocked'
    # Task cannot be executed yet, because it is allowed to go only after future date
    WAITING_FOR_DATE = 'waiting_for_date'
    # Task cannot be executed yet, because it is waiting for other tasks to be completed
    WAITING_FOR_PRECONDITIONS = 'waiting_for_preconditions'
    # Task is ready to be executed
    WAITING_FOR_WORKER = 'waiting_for_worker'
    # Successfully executed
    DONE = 'done'
    # Too many failed attempts to execute the task
    GIVEN_UP = 'given_up'

    # transaction error happened during task execution, so we cant even properly store failure
    CORRUPTED = 'corrupted'

    # task is waiting on a precondition that is blocked or failed
    NOT_GOING_TO_HAPPEN_SOON = 'not_going_to_happen_soon'


class Task(models.Model):
    """
    Task is one-off unit of executing instructions.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state = models.CharField(
        max_length=30,
        db_index=True,
        choices=TaskState.choices,
        default=TaskState.WAITING_FOR_DATE,
    )
    task_type = models.CharField(max_length=100)
    instructions = models.JSONField(null=True)
    precondition_date = models.DateTimeField(
        default=timezone.now,
        help_text=_(
            'Task will not be executed before this date. '
            'Also used as priority for tasks that are waiting for worker - '
            'tasks with earlier date will be preferred.'
        ),
    )
    precondition_tasks = models.ManyToManyField(
        to='self',
        symmetrical=False,
        related_name='dependent_tasks',
        through='TaskDependency',
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(
                fields=['precondition_date'],
                condition=models.Q(state=TaskState.WAITING_FOR_DATE),
                name='tasks_waiting_for_date_idx',
            ),
            models.Index(
                fields=['precondition_date'],
                condition=models.Q(state=TaskState.WAITING_FOR_WORKER),
                name='tasks_waiting_for_worker_idx',
            ),
        ]

    def block(self):
        if self.state not in (TaskState.WAITING_FOR_DATE, TaskState.WAITING_FOR_PRECONDITIONS):
            raise ValueError(f'Cannot block task in state {self.state}')
        self.state = TaskState.BLOCKED
        self.save(update_fields=['state'])

    def unblock(self):
        if self.state != TaskState.BLOCKED:
            raise ValueError('Task is not blocked')
        self.state = TaskState.WAITING_FOR_DATE
        self.save(update_fields=['state'])
        Task.objects.filter(
            id__in=get_dependent_task_ids([self.id]),
            state=TaskState.NOT_GOING_TO_HAPPEN_SOON,
        ).update(state=TaskState.WAITING_FOR_DATE)

    def retry(self):
        if self.state not in (TaskState.GIVEN_UP, TaskState.CORRUPTED):
            raise ValueError(f'Cannot retry task in state {self.state}')
        self.state = TaskState.WAITING_FOR_DATE
        self.save(update_fields=['state'])
        dependent_task_ids = get_dependent_task_ids([self.id])
        Task.objects.filter(
            id__in=dependent_task_ids,
            state=TaskState.NOT_GOING_TO_HAPPEN_SOON,
        ).update(state=TaskState.WAITING_FOR_DATE)
        return dependent_task_ids


class TaskDependency(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dependent_task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='dependencies')
    precondition_task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name='dependents')

    class Meta:
        unique_together = (
            ('dependent_task', 'precondition_task'),
        )


class TaskExecution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='executions')
    success = models.BooleanField()
    created_at = models.DateTimeField(default=timezone.now)
    time_taken = models.DurationField(null=True)

    class Meta:
        ordering = ('task', '-created_at')


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
    transitions_done += handle_waiting_for_date_tasks(now)
    transitions_done += handle_waiting_for_preconditions_tasks()
    while True:
        try:
            did_a_thing = handle_waiting_for_worker_tasks(now)
        except Exception as e:  # pylint: disable=broad-except
            logger.exception('Worker failed')
            _handle_corrupted_task(e)
            did_a_thing = True
        if not did_a_thing:
            break
        transitions_done += 1
    return transitions_done


def _handle_corrupted_task(exc):
    # retrieve task from the traceback and mark it as corrupted
    traceback = exc.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_name == 'handle_waiting_for_worker_tasks':
            break
        traceback = traceback.tb_next
    task = frame.f_locals['task']
    Task.objects.filter(id=task.id).update(state=TaskState.CORRUPTED)


def handle_waiting_for_date_tasks(now):
    return Task.objects.filter(
        state=TaskState.WAITING_FOR_DATE,
        precondition_date__lte=now,
    ).update(state=TaskState.WAITING_FOR_PRECONDITIONS)


def handle_waiting_for_preconditions_tasks():
    transitions_done = 0

    transitions_done += Task.objects.filter(
        state=TaskState.WAITING_FOR_PRECONDITIONS,
    ).annotate(
        num_preconditions=models.Count('precondition_tasks'),
        num_done_preconditions=models.Count('precondition_tasks', filter=models.Q(
            precondition_tasks__state=TaskState.DONE,
        )),
    ).filter(
        num_preconditions=models.F('num_done_preconditions'),
    ).update(state=TaskState.WAITING_FOR_WORKER)

    # if a task is waiting for preconditions that are not going to happen soon, it's not going to happen soon either
    transitions_done += Task.objects.filter(
        state=TaskState.WAITING_FOR_PRECONDITIONS,
        precondition_tasks__state__in=(
            TaskState.BLOCKED,
            TaskState.GIVEN_UP,
            TaskState.CORRUPTED,
            TaskState.NOT_GOING_TO_HAPPEN_SOON,
        ),
    ).update(state=TaskState.NOT_GOING_TO_HAPPEN_SOON)

    return transitions_done


@transaction.atomic
def handle_waiting_for_worker_tasks(now):
    task = Task.objects.filter(state=TaskState.WAITING_FOR_WORKER).order_by(
        'precondition_date',
    ).select_for_update(skip_locked=True).first()
    if task is None:
        # nothing to do
        return False

    logger.info('Just about to execute task %s: %s', task.id, task.task_type)
    start_time = time.monotonic()
    try:
        ret = follow_instructions(task)

    except Exception:  # pylint: disable=broad-except
        logger.exception('Task %s failed', task.id)
        success = False
        failure_index = task.executions.filter(success=False).count()
        retry_delay = get_retry_delay(failure_index)
        if retry_delay is None:
            task.state = TaskState.GIVEN_UP
        else:
            task.state = TaskState.WAITING_FOR_DATE
            task.precondition_date = now + retry_delay

    else:
        if isinstance(ret, RetryMeLater):
            logger.info('Task %s needs to be retried later', task.id)
            success = True
            task.state = TaskState.WAITING_FOR_DATE
            # move scheduled time forward to avoid starving other tasks, in the case this one wants to be retried often
            task.precondition_date = now
            task.precondition_tasks.add(*ret.precondition_tasks)

        elif isinstance(ret, AllDone):
            logger.info('Task %s is done', task.id)
            success = True
            task.state = TaskState.DONE

        else:
            logger.warning('Task %s returned a value, which is ignored', task.id)
            success = True
            task.state = TaskState.DONE

    time_taken = time.monotonic() - start_time

    TaskExecution.objects.create(
        task=task,
        success=success,
        created_at=now,
        time_taken=datetime.timedelta(seconds=time_taken),
    )
    task.save(update_fields=['state', 'created_at', 'precondition_date'])
    return True


def follow_instructions(task):
    func = import_string(task.task_type)
    instructions = task.instructions
    return func(task, *instructions['args'], **instructions['kwargs'])


def get_retry_delay(failure_index):
    max_failures = 3
    if failure_index >= max_failures:
        return None
    return datetime.timedelta(seconds=10) * (2 ** failure_index)


class RetryMeLater:
    """
    Like a process yielding in operating system.
    """
    def __init__(self, precondition_tasks=()):
        self.precondition_tasks = precondition_tasks


class AllDone:
    pass


def schedule(
    func, args=None, kwargs=None,
    precondition_date=None, precondition_tasks=None, blocked=False,
):
    if args is None:
        args = []
    if kwargs is None:
        kwargs = {}
    if precondition_date is None:
        precondition_date = timezone.now()
    if precondition_tasks is None:
        precondition_tasks = []
    func_name = inspect.getmodule(func).__name__ + '.' + func.__name__

    with transaction.atomic():
        task = Task.objects.create(
            state=TaskState.BLOCKED if blocked else TaskState.WAITING_FOR_DATE,
            task_type=func_name,
            instructions={
                'args': args,
                'kwargs': kwargs,
            },
            precondition_date=precondition_date,
        )
        task.precondition_tasks.set(precondition_tasks)

    return task


def get_dependent_task_ids(task_ids):
    task_ids = list(task_ids)
    qs = TaskDependency.objects.raw(
        '''
        WITH RECURSIVE dependent_tasks AS (
            SELECT id FROM django_goals_task
            WHERE id = ANY(%(task_ids)s)
        UNION
            SELECT django_goals_task.id
            FROM django_goals_task
            JOIN django_goals_taskdependency
            ON django_goals_task.id = django_goals_taskdependency.dependent_task_id
            JOIN dependent_tasks
            ON django_goals_taskdependency.precondition_task_id = dependent_tasks.id
        )
        SELECT id FROM dependent_tasks
        ''',
        {'task_ids': task_ids},
    )
    return [obj.id for obj in qs]
