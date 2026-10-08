# TODO

## Bugs

Reproduced against 0.7.7.

- `goals_fsck` sets `waiting_for_count` of an ANY-mode goal back to 1
  when one precondition has woken it but others are still open,
  so the goal waits for another one.
- Adding preconditions locks them, so it waits for those being pursued.
  `RetryMeLater(precondition_goals=[a, b])` deadlocks
  when `a` depends on `b` and `b` is being achieved:
  the worker achieving `b` updates counters of `a`, already locked by the retrying one.
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
- Busy and threaded workers each list the transition handlers,
  so a new handler must be added to both.
