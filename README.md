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

Tasks are intended to be idempotent and thus they are named "goals" here. Even when a goal is completed (reached) it hypotetically might be triggered again without a catastrophe.

Signle task/goal can be executed in many "pieces". For example handler function can dynamically decide to terminate the execution and request processing at a later date. Preconditions can be modified in each execution. In other words a worker may pursue the goal in many tries, and modify preconditions in each try.

We use Django ORM to store state. No other component is required, like a redis queue or something.
This is for simplicty of deployment.

Tasks are executed inside DB transactions. We rely on transactions to distribute and lock tasks across workers. In particular there is no "executing right now" task state, because it can be never observed due to running in transaction.

System is intended to be simple in terms of code. We try to focus on narrow and simple (yet powerful) functionality and export out-of-scope concers to other components. For example we are not implementing worker reincarnation strategy or error reporting.
