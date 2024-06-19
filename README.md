# Django Goals

Django Goals is a task queue library for Django that allows you to define and manage complex task workflows using a goal-oriented approach. It provides a powerful and flexible way to structure and execute tasks with dependencies, retries, and asynchronous processing.

## Features
- Define tasks as goals with preconditions (dates and other goals)
- Track goal states and progress
- Handle goal dependencies and automatically trigger downstream goals
- Retry failed goals with customizable retry strategies (e.g., exponential backoff)
- Asynchronous goal processing using a reliable worker system
- Integrate seamlessly with Django ORM for goal persistence and querying
- Customize goal execution and error handling
- Monitor and manage goals via Admin interface

## Simple Workflow for Using Django Goals

1. **Define goal handler**: Define a handler function that contains the logic for achieving a goal.
2. **Schedule the goal**: Schedule the goal with the handler function and any preconditions.
3. **Run the worker**: Run a worker to process the goals.
4. **Customize as needed**: Customize goal execution, retries, and error handling as needed.
5. **Monitor and manage goals**: Use the Django admin interface to monitor and manage goals.

## Installation

Install the package using pip:

```bash
pip install https://github.com/EE/django-goals/archive/refs/heads/main.zip
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

### Running Workers

Run the worker to process the goals. There are two types of workers: blocking and busy-wait.

#### Blocking Worker

The blocking worker listens for notifications and processes goals when they are ready.

```bash
python manage.py goals_blocking_worker
```

#### Busy-Wait Worker

The busy-wait worker continuously checks for goals to process.

```bash
python manage.py goals_busy_worker
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
- **CORRUPTED** - A transaction error happened during execution, so we can't properly store the failure.
- **NOT_GOING_TO_HAPPEN_SOON** - The goal is waiting for a precondition that won't be achieved soon.

The state transitions are managed automatically based on the preconditions and the outcome of the handler function.


## A real-life example: use Django Goals in e-commerce

Imagine you have a Django application for an e-commerce site. You want to send a follow-up email to customers who haven't completed their purchase after adding items to their cart. This email should only be sent if certain conditions are met (e.g., a specific time has passed since the items were added to the cart).

Here's how you can use Django Goals to achieve this.

**1. Define Your Goal Handler**

First, define the handler function that will send the follow-up email. This function will be called when the goal is processed.

```python
# handlers.py

from django_goals.models import AllDone
from django.core.mail import send_mail

def send_follow_up_email(goal, customer_email, cart_id):
    # Logic to send the follow-up email
    send_mail(
        'Complete Your Purchase',
        'You have items in your cart. Complete your purchase now!',
        'from@example.com',
        [customer_email],
        fail_silently=False,
    )
    return AllDone()
```

**2. Schedule the Goal**

Next, schedule the goal to be processed at a later time. For example, schedule it to run 24 hours after the items were added to the cart.

```python
# schedule_goal.py

from django.utils import timezone
from django_goals.models import schedule
from .handlers import send_follow_up_email

def schedule_follow_up_email(customer_email, cart_id):
    # Schedule the goal to run 24 hours later
    precondition_date = timezone.now() + timezone.timedelta(hours=24)
    goal = schedule(
        send_follow_up_email,
        args=[customer_email, cart_id],
        kwargs={},
        precondition_date=precondition_date
    )
    print(f"Scheduled follow-up email for cart {cart_id} to be sent to {customer_email}")
```

**3. Run the Worker**

Run the worker to process the scheduled goals. This can be done using the blocking worker or the busy-wait worker.

Blocking Worker

```bash
python manage.py goals_blocking_worker
```

Busy-Wait Worker

```bash
python manage.py goals_busy_worker
```

**4. Integrate with Your Application**

Integrate the scheduling function with your application logic. For example, call schedule_follow_up_email when a user adds items to their cart.

```python
# views.py

from .schedule_goal.py import schedule_follow_up_email

def add_to_cart(request):
    # Logic to add items to cart
    ...
    customer_email = request.user.email
    cart_id = ... # get the cart id
    schedule_follow_up_email(customer_email, cart_id)
    return HttpResponse("Items added to cart")
```

**5. Monitor Goals**

Use the Django admin interface to monitor and manage the goals.

**Summary**

In this example, we created a simple task to send a follow-up email to customers who haven't completed their purchase. We used Django Goals to schedule this task to run after 24 hours, defined the logic for sending the email in a handler function, and integrated the scheduling into our application logic. Finally, we ran the worker to process the goals and used the Django admin interface to monitor and manage them.

### More design info

Goals are designed to be repeatable, so they can be triggered again without issues even after completion without a catastrophe.

A single task/goal can be executed in many "pieces". For example, the handler function can dynamically decide to terminate the execution and request processing at a later date. Preconditions can be modified in each execution. In other words, a worker may pursue the goal in many tries and modify preconditions in each try.

We use Django ORM to store state. No other component is required, like a Redis queue or something. This is for simplicity of deployment.

Tasks are executed inside DB transactions. We rely on transactions to distribute and lock tasks across workers. In particular, there is no "executing right now" task state because it can never be observed due to running in a transaction.

The system is intended to be simple in terms of code. We try to focus on narrow and simple (yet powerful) functionality and export out-of-scope concerns to other components. For example, we are not implementing worker reincarnation strategy or error reporting.
