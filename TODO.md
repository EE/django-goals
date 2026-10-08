# TODO

## Bugs

Reproduced against 0.7.7.

- `_mark_as_failed` and `_mark_as_unfailed` change a dependent's counters by 1
  even when several of its preconditions are in the same batch.
  A diamond C ← {A, B} then gets stuck
  in `NOT_GOING_TO_HAPPEN_SOON` (BLOCK) or `WAITING_FOR_PRECONDITIONS` (PROCEED)
  until `goals_fsck` runs.
- PROCEED mode: unfailing a precondition does not restore `waiting_for_count` of its dependents,
  so they run before it is achieved.
- PROCEED mode: `_add_precondition_goals` subtracts all failed preconditions
  from a count of the new ones only,
  so `RetryMeLater(precondition_goals=[x])` does not wait for `x`
  once an earlier precondition has failed.
- `GOALS_TIME_LIMIT_SECONDS` makes the threaded worker fail every goal:
  `signal.signal()` raises outside the main thread.
- `PickupMonitorThread` dies on the first database error,
  silently disabling killer task detection.

## Decide

- Retrying a `GIVEN_UP` goal allows a single attempt, because old failures still count.
- ANY mode with BLOCK: one failed precondition blocks the goal
  even when another one is achieved.

## Chores

- Release 0.8.0, which drops Django < 5.2.
- `pypi.yml` fails on every push without a version bump, because TestPyPI rejects existing files.
