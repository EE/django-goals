# Django Goals

Django Goals is a workflow engine for Django that treats tasks as goals to be achieved rather than procedures to execute. It uses PostgreSQL's transaction system to reliably distribute work across multiple workers - without requiring any additional infrastructure.

You can use Django Goals as a classic DAG workflow engine, where you define task dependencies upfront and the system executes them in the correct order.

When you need more flexibility, Django Goals allows you to dynamically add dependencies - modify the DAG while it is progressing. This pattern requires you to write idempotent handlers, but naturally handles failures and complex business workflows - tasks simply check their current state and decide what to do next.

## Features
- Define tasks as goals with preconditions (dates and other goals)
- Track goal states and progress
- Handle goal dependencies and automatically trigger downstream goals
- Retry failed goals with customizable retry strategies (e.g., exponential backoff)
- Asynchronous goal processing using a reliable worker system
- Integrate seamlessly with Django ORM for goal persistence and querying
- Customize goal execution and error handling
- Monitor and manage goals via Admin interface
- Support for tiered workers with deadline horizons to prioritize urgent tasks

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

from django_goals.models import schedule, AllDone, RetryMeLater

def my_goal_handler(goal, *args, **kwargs):
    # ...Your goal logic here...
    if some_condition:
        return RetryMeLater(precondition_goals=[...])
    # Return AllDone() when the goal is done according to the logic
    return AllDone()
```

### Scheduling goals

```python
# schedule_goals.py

from django.utils import timezone
from .handlers import my_goal_handler
from django_goals.models import schedule

goal = schedule(
    sample_goal_handler,
    args=[...],
    kwargs={...},
    precondition_date=timezone.now() + timezone.timedelta(days=1)
)
```

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
   - When handler returns `RetryMeLater(precondition_goals=None)`, the goal will retry immediately, regardles of preconditions' state

### Running Workers

Run the worker to process the goals. There are two types of workers: blocking and busy-wait.

You can mix worker types and you can spawn many of them.

Some work cannot be done by blocking worker, so **you must run at least one busy worker instance**. Blocking worker is useful for minimizing latency in certain setups.

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

#### Worker with Deadline Horizons

You can create workers that only process goals with deadlines within a specific time frame using the deadline horizon parameter:

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
- `none` - No deadline limit (same as just specifying a number)

You can use multiple `--threads` parameters to create workers with different horizons:

```bash
# Create a three-tier worker system
python manage.py goals_threaded_worker --threads 1:0s --threads 2:4h --threads 5
```

This would create:
- 1 critical worker (only handles goals due immediately)
- 2 urgent workers (only handles goals due within 4 hours)
- 5 regular workers (handle any goal regardless of deadline)

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

The state transitions are managed automatically based on the preconditions and the outcome of the handler function.

### Settings

`GOALS_MAX_PROGRESS_COUNT` - Maximum number of progress entries a goal can have. Useful for limiting impact of bugs in handler functions. Instead of spinning indefinitely and filling up the database, the goal will be marked as failed. Set it to `None` to disable the limit. Default is `100`.

`GOALS_RETENTION_SECONDS` - Number of seconds to keep achieved goals in the database for. Set to `None` to keep them indefinitely. Default is `60 * 60 * 24 * 7` (1 week).

`GOALS_DEFAULT_DEADLINE_SECONDS` - If the `schedule` function is called without a `deadline` argument, it is assigned deadline of `now() + timedelta(seconds=GOALS_DEFAULT_DEADLINE_SECONDS)`. Default is `60 * 60 * 24 * 7` (1 week).

`GOALS_MEMORY_LIMIT_MIB` - Maximum memory usage of a worker process. This is enforced using `resource` python module. Set to `None` to disable the limit. Default is `None`.

`GOALS_TIME_LIMIT_SECONDS` - Maximum time a handler function can run. If the handler function runs longer than this, it is terminated. Default is `None` (no limit).

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
