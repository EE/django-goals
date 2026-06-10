# Django Goals

Django Goals is a database-native workflow engine for Django. Unlike traditional task queues that need Redis or RabbitMQ, it uses PostgreSQL's transaction system to coordinate work - no additional infrastructure required. Tasks are stateful Django models that can pause, resume, and dynamically modify their dependencies while running.

You can use Django Goals as a classic DAG workflow engine, where you define task dependencies upfront and the system executes them in the correct order.

When you need more flexibility, Django Goals allows you to dynamically add dependencies - modify the DAG while it is progressing. This pattern requires you to write idempotent handlers, but naturally handles failures and complex business workflows - tasks simply check their current state and decide what to do next.

## Features
- **No extra infrastructure** - your PostgreSQL database is the queue. No Redis, no RabbitMQ, no separate result store.
- **Transactional by design** - a task's database writes commit atomically with its state change, and a task scheduled inside `transaction.atomic()` becomes visible to workers only if your transaction commits. The "task ran but the data was rolled back" class of bugs disappears.
- **Workflows, not just tasks** - goals declare preconditions (dates and other goals), and a running goal can pause itself and add new preconditions mid-flight, growing the DAG dynamically.
- **Everything is a Django model** - inspect, query, and manage goals with the ORM and the admin like any other data.
- **Middleware, just like web requests** - handler execution and scheduling are wrapped by configurable middleware, the same pattern Django uses for views. Think of Django Goals as a "background request queue": the handler plays the view, and cross-cutting concerns like Sentry tracing plug in around it.
- **Built for failure** - automatic retries, failure propagation through the dependency graph, and detection of "killer tasks" that crash workers without leaving a trace.
- **Deadline-driven prioritization** - goals with the nearest deadlines are picked up first, and tiered workers can reserve capacity for urgent work.

## Requirements

- PostgreSQL with the `psycopg` (version 3) driver. Django Goals relies on `SELECT ... FOR UPDATE SKIP LOCKED` and `LISTEN`/`NOTIFY`; other databases are not supported.
- Django 4.2+, Python 3.13+

## Installation

Install the package using pip:

```bash
pip install django-goals
```

Add django_goals to your INSTALLED_APPS in your Django settings:

```python
INSTALLED_APPS = [
    ...,
    'django_goals',
    'django_object_actions',  # django-goals dependency
]
```

Run the migrations to create the necessary database tables:

```bash
python manage.py migrate
```

## Setup and Usage

### Defining a Goal

Define a goal by scheduling it with a handler function. The handler function contains the logic for achieving the goal.

```python
# handlers.py

from django_goals.models import AllDone, RetryMeLater

def my_goal_handler(goal, *args, **kwargs):
    # ...Your goal logic here...
    if some_condition:
        return RetryMeLater(precondition_goals=[...])
    # Return AllDone() when the goal is done according to the logic
    return AllDone()
```

The handler signals the outcome by its return value:

- `AllDone()` - the goal is achieved.
- `RetryMeLater(precondition_date=..., precondition_goals=..., message=...)` - call the handler again later, optionally after a date and/or after other goals are achieved. This is not a failure - think of it as a process yielding control. When returning is inconvenient, you can equivalently `raise RetryMeLaterException(...)`.
- Raising an exception - the attempt failed and will be retried with exponential backoff (see [Monitoring and Managing Goals](#monitoring-and-managing-goals)).

Handlers run inside a database transaction, which is rolled back if the handler raises. A handler may be called multiple times for the same goal, so write handlers to be idempotent: check the current state and decide what remains to be done.

### Scheduling goals

```python
# schedule_goals.py

from django.utils import timezone
from .handlers import my_goal_handler
from django_goals.models import schedule

goal = schedule(
    my_goal_handler,
    args=[...],
    kwargs={...},
    precondition_date=timezone.now() + timezone.timedelta(days=1)
)
```

The first argument may be the handler callable (its fully-qualified name is derived automatically) or a string that is used directly as the handler's fully-qualified name (`"myapp.handlers.my_goal_handler"`). The string form is handy when the handler isn't importable at schedule time.

Other `schedule()` arguments:

- `args`, `kwargs` - arguments passed to the handler. Must be JSON-serializable.
- `precondition_goals` - goals that must be achieved before this one is pursued.
- `preconditions_mode` - how multiple precondition goals are combined (see below).
- `precondition_failure_behavior` - what happens when a precondition fails (see below).
- `deadline` - goals with sooner deadlines are picked up first. Defaults to `now() + GOALS_DEFAULT_DEADLINE_SECONDS`, or to the current goal's deadline when scheduling from inside a handler. A goal's deadline propagates recursively to its preconditions, moving theirs earlier if needed.
- `blocked=True` - create the goal in the `BLOCKED` state.

`schedule()` only writes to the database, so you can call it inside `transaction.atomic()` together with your business-data changes - the goal becomes visible to workers if and only if your transaction commits.

### Preconditions Mode

When scheduling a goal with preconditions, you can specify how these preconditions should be evaluated using `preconditions_mode`:

```python
from django_goals.models import schedule, PreconditionsMode

goal = schedule(
    my_handler,
    precondition_goals=[goal1, goal2],
    preconditions_mode=PreconditionsMode.ANY  # or PreconditionsMode.ALL (default)
)
```

There are two modes available:

- **ALL** (default) - All preconditions must be achieved before the goal can be pursued.
- **ANY** - Goal can be pursued if any of its preconditions is achieved.

#### ANY Mode Behavior

In ANY mode, there can be a situation when a goal's handler will be invoked while some of the goal's preconditions are not achieved. This has some special characteristics:

1. **Achievement without all preconditions done** - A goal can be achieved (`AllDone()`) even if some of its preconditions are not met.

2. **RetryMeLater Behavior**:
   - When handler returns `RetryMeLater()` with no precondition goals (`precondition_goals=[]`), the system will:
     - Retry immediately if all existing preconditions are achieved
     - Wait for any not-achieved precondition otherwise
   - When handler returns `RetryMeLater(precondition_goals=None)`, the goal will retry immediately, regardless of preconditions' state

See `example_app/partition_sort.py` for a quicksort-like workflow built on ANY mode.

### Precondition Failure Behavior

By default (`PreconditionFailureBehavior.BLOCK`), when a precondition goal fails, the dependent goal transitions to `NOT_GOING_TO_HAPPEN_SOON` and is not pursued - failure propagates down the dependency graph, and retrying the failed goal unblocks dependents again. With `precondition_failure_behavior=PreconditionFailureBehavior.PROCEED`, failed preconditions are treated like achieved ones: the goal is pursued once every precondition has either succeeded or failed, and the handler can inspect them and decide what to do.

### Running Workers

Run the worker to process the goals. There are three worker commands:

| Command | What it does |
|---|---|
| `goals_busy_worker` | Single-threaded worker. Polls for work, performs all state transitions, executes handlers, cleans up old goals. |
| `goals_threaded_worker` | Multi-threaded worker. One thread handles state transitions and cleanup; the remaining threads execute handlers, optionally restricted by deadline horizon. Also records goal pickups for killer-task detection. |
| `goals_blocking_worker` | Latency-optimized worker. Sleeps on PostgreSQL `LISTEN`/`NOTIFY` and executes handlers the moment a goal becomes ready. Does **not** perform date/precondition transitions or cleanup. |

You can mix worker types and you can spawn many of them.

Because the blocking worker only executes handlers for goals that are already ready, **you must run at least one busy or threaded worker** - those are the workers that move goals through date and precondition transitions and delete old goals. Blocking worker is useful for minimizing latency in certain setups.

#### Busy-Wait Worker

The busy-wait worker continuously checks for goals to process.

```bash
python manage.py goals_busy_worker
```

You can instruct the worker to exit after some work is done. Useful for minimizing impact of memory leaks.

```bash
python manage.py goals_busy_worker --max-progress-count 100
```

"Progress" is a single handler call, including failures (transaction recoverable errors) and "corruptions" (transaction non-recoverable errors).

A quick way to replace exited workers is to use `yes | xargs -P <how many workers>`

```bash
yes | xargs -I -L1 -P4 -- ./manage.py goals_busy_worker --max-progress-count 100
```

#### Threaded Worker

The threaded worker runs multiple worker threads in a single process. You can create workers that only process goals with deadlines within a specific time frame using the deadline horizon parameter:

```bash
# Run 2 workers that only handle goals with deadlines within the next 30 minutes
# and 3 workers that handle all goals
python manage.py goals_threaded_worker --threads 2:30m --threads 3
```

This is useful for ensuring that urgent goals (with near deadlines) always have dedicated workers available, even when all other workers are busy with long-running tasks.

The `--threads` parameter accepts two formats:
- `N` - Create N workers with no deadline horizon (will process any goal)
- `N:HORIZON` - Create N workers with the specified horizon (will only process goals with deadlines within that time)

Horizon format examples:
- `5s` - 5 seconds
- `30m` - 30 minutes
- `2h` - 2 hours
- `1d` - 1 day
- `1w` - 1 week
- `none` - No deadline limit (same as just specifying a number)

You can use multiple `--threads` parameters to create workers with different horizons:

```bash
# Create a three-tier worker system
python manage.py goals_threaded_worker --threads 1:0s --threads 2:4h --threads 5
```

Pass `--once` to exit when all threads run out of work - useful in tests and batch jobs.

#### Blocking Worker

The blocking worker listens for notifications and processes goals when they are ready.

```bash
python manage.py goals_blocking_worker
```

### Monitoring and Managing Goals

Django Goals provides an admin interface for monitoring and managing goals. You can see the state of each goal, retry failed goals, block or unblock goals, and view the progress of each goal.


Goals can be in various states:

- **BLOCKED** - Goal is explicitly marked not to be pursued.
- **WAITING_FOR_DATE** - Goal cannot be pursued yet because it is allowed only after a future date.
- **WAITING_FOR_PRECONDITIONS** - Goal cannot be pursued yet because other goals need to be achieved first.
- **WAITING_FOR_WORKER** - Goal is ready to be pursued and we are waiting for a worker to pick it up.
- **ACHIEVED** - The goal has been achieved.
- **GIVEN_UP** - There have been too many failed attempts when pursuing the goal.
- **NOT_GOING_TO_HAPPEN_SOON** - The goal is waiting for a precondition that won't be achieved soon.
- **IT_IS_A_KILLER_TASK** - The goal was picked up too many times without ever recording progress, suggesting it crashes workers.

The state transitions are managed automatically based on the preconditions and the outcome of the handler function.

When a handler raises an exception, the goal is retried after 10, 20, 40, ... seconds (doubling each time); after `GOALS_GIVE_UP_AT` failed attempts it transitions to `GIVEN_UP`. Independently, a goal that accumulates `GOALS_MAX_PROGRESS_COUNT` progress records without being achieved is also given up.

A handler that crashes the whole worker process (segfault, OOM kill) leaves no progress record at all, so the goal would be retried indefinitely, killing every worker in turn. To detect this, the threaded worker records each pickup outside the worker transaction; a goal that accumulates `GOALS_MAX_PICKUPS` pickups without completing is marked `IT_IS_A_KILLER_TASK` and no longer pursued.

There are also management commands for operations work:

- `python manage.py goals_retry [--limit N]` - retry all goals in the `GIVEN_UP` state.
- `python manage.py goals_fsck` - verify and fix the denormalized precondition counters on all goals.

### Settings

`GOALS_GIVE_UP_AT` - Number of failed attempts after which a goal transitions to `GIVEN_UP`. Default is `4`.

`GOALS_MAX_PROGRESS_COUNT` - Maximum number of progress entries a goal can have. Useful for limiting impact of bugs in handler functions. Instead of spinning indefinitely and filling up the database, the goal will be marked as failed. Set it to `None` to disable the limit. Default is `100`.

`GOALS_MAX_PICKUPS` - Maximum number of times a goal may be picked up by a worker without completing before it is marked as a killer task. Set to `None` to disable killer task detection. Default is `None`.

`GOALS_RETENTION_SECONDS` - Number of seconds to keep achieved goals in the database for. Set to `None` to keep them indefinitely. Default is `60 * 60 * 24 * 7` (1 week).

`GOALS_DEFAULT_DEADLINE_SECONDS` - If the `schedule` function is called without a `deadline` argument, it is assigned deadline of `now() + timedelta(seconds=GOALS_DEFAULT_DEADLINE_SECONDS)`. Default is `60 * 60 * 24 * 7` (1 week).

`GOALS_MEMORY_LIMIT_MIB` - Maximum memory usage of a worker process. This is enforced using `resource` python module. Set to `None` to disable the limit. Default is `None`.

`GOALS_TIME_LIMIT_SECONDS` - Maximum time a handler function can run. If the handler runs longer, a `TimesUp` exception is raised in it (via `SIGALRM`), which counts as a regular failure. Default is `None` (no limit).

`GOALS_MIDDLEWARE` - List of middleware wrapping goal execution, analogous to Django's request middleware. Default is `['django_goals.pickups.Middleware', 'django_goals.models.FsckMiddleware']`. If you use Sentry, prepend `'django_goals.sentry.Middleware'` to get a `queue.process` transaction for every handler call.

`GOALS_SCHEDULE_MIDDLEWARE` - List of middleware wrapping `schedule()`. Default is `[]`. Sentry users can add `'django_goals.sentry.ScheduleMiddleware'` to connect scheduling and execution with distributed tracing.

## Performance

### Production Scale Experience

Django Goals has been successfully deployed in production environments with the following characteristics:

- **Verified Worker Scale**: Tested with 48 concurrent workers (12 Heroku 1x pro dynos × 4 workers)
- **Database Performance**: Operates smoothly on Heroku standard-0 PostgreSQL plan under this load
- **Scalability Potential**: Based on database performance metrics, the system is estimated to handle up to ~150 concurrent workers without significant degradation

### Database Optimization

The system is designed with database performance in mind:

- Optimized database layout with indexes for all state transitions
- No full table scans during standard operations
- Uses PostgreSQL's SKIP LOCKED feature to maintain responsiveness even under load
- Each worker requires one database connection, which is typically the main bottleneck

### Task Duration Considerations

While the system is optimized for shorter tasks, it can handle longer-running tasks with some considerations:

- Recommended: Tasks that complete within seconds to minutes
- Possible: Tasks running up to 10 minutes have been successfully used in production
- System remains responsive during long-running tasks due to:
  - Goal-level locking (rather than table-level)
  - SKIP LOCKED query optimization for concurrent goal processing

## More design info

A single task/goal can be executed in many "pieces". For example, the handler function can dynamically decide to terminate the execution and request processing at a later date. Preconditions can be modified in each execution. In other words, a worker may pursue the goal in many tries and modify preconditions in each try.

We use Django ORM to store state. No other component is required, like a Redis queue or something. This is for simplicity of deployment.

Tasks are executed inside DB transactions. We rely on transactions to distribute and lock tasks across workers. In particular, there is no "executing right now" task state because it can never be observed due to running in a transaction.

The system is intended to be simple in terms of code. We try to focus on narrow and simple (yet powerful) functionality and export out-of-scope concerns to other components. For example, we are not implementing worker reincarnation strategy or error reporting.
