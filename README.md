# Django Goals

Django Goals is a task queue library for Django that allows you to define and manage complex task workflows using a goal-oriented approach. It provides a powerful and flexible way to structure and execute tasks with dependencies, retries, and asynchronous processing.

## Features
- Define tasks as goals with preconditions (dates and other goals)
- Track goal states and progress
- Handle goal dependencies and automatically trigger downstream goals
- Retry failed goals with customizable retry strategies (e.g., exponential backoff)
- Asynchronous goal processing using a reliable worker system
- Seamless integration with Django ORM for goal persistence and querying
- Customizable goal execution and error handling
- Admin interface for monitoring and managing goals

## Design
Tasks are intended to be idempotent and thus they are named "goals" here. Even when a goal is completed (reached) it might be triggered again without a catastrophe.

A single task/goal can be executed in many "pieces." For example, the handler function can dynamically decide to terminate the execution and request processing at a later date. Preconditions can be modified in each execution. In other words, a worker may pursue the goal in many tries and modify preconditions in each try.

We use Django ORM to store state. No other component is required, like a Redis queue or something. This is for simplicity of deployment.

Tasks are executed inside DB transactions. We rely on transactions to distribute and lock tasks across workers. In particular, there is no "executing right now" task state because it can never be observed due to running in a transaction.

The system is intended to be simple in terms of code. We try to focus on narrow and simple (yet powerful) functionality and export out-of-scope concerns to other components. For example, we are not implementing worker reincarnation strategy or error reporting.

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
]
```

Run the migrations to create the necessary database tables:

```bash
python manage.py migrate
```

## Usage

### Defining a Goal

Define a goal by scheduling it with a handler function. The handler function contains the logic for achieving the goal.

```python
# models.py

from django_goals.models import schedule, AllDone

def my_goal_handler(goal, *args, **kwargs):
    # Your goal logic here
    return AllDone()

# Schedule the goal
goal = schedule(my_goal_handler, args=[...], kwargs={...}, precondition_date=timezone.now())
```

### Handling Goal States

Goals can be in various states such as blocked, waiting_for_date, waiting_for_preconditions, waiting_for_worker, achieved, given_up, and corrupted. The state transitions are managed automatically based on the preconditions and the outcome of the handler function.

### Running Workers
Run the worker to process the goals. There are two types of workers: blocking and busy-wait.

### Blocking Worker
The blocking worker listens for notifications and processes goals when they are ready.

```bash
python manage.py goals_blocking_worker
```

### Busy-Wait Worker

The busy-wait worker continuously checks for goals to process.

```bash
python manage.py goals_busy_worker
```

### Monitoring and Managing Goals

Django Goals provides an admin interface for monitoring and managing goals. You can see the state of each goal, retry failed goals, block or unblock goals, and view the progress of each goal.

```python
# admin.py

from django.contrib import admin
from django_goals.admin import GoalAdmin
from .models import Goal

admin.site.register(Goal, GoalAdmin)
```

### Example

Here is a complete example of setting up and using Django Goals:

Define your goal handler functions:

```python

# handlers.py

from django_goals.models import AllDone, RetryMeLater

def sample_goal_handler(goal, *args, **kwargs):
    # Logic to achieve the goal
    if some_condition:
        return RetryMeLater(precondition_goals=[...])
    return AllDone()
```

Schedule goals:

```python
# schedule_goals.py

from django.utils import timezone
from .handlers import sample_goal_handler
from django_goals.models import schedule

goal = schedule(
    sample_goal_handler,
    args=[...],
    kwargs={...},
    precondition_date=timezone.now() + timezone.timedelta(days=1)
)
```

Run the worker:

```bash
python manage.py goals_blocking_worker
```

Monitor goals in the Django admin interface by adding them to admin.py:

```python
# admin.py

from django.contrib import admin
from django_goals.admin import GoalAdmin
from .models import Goal

admin.site.register(Goal, GoalAdmin)
```

Use Django Goals in your project by following the steps above.

