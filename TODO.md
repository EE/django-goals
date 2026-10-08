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

## Investigate

- Picking gets expensive with dead index entries at the head of the queue
  (finding 1 in `database-load.md`).

## Decide

- Retrying a `GIVEN_UP` goal allows a single attempt, because old failures still count.
- ANY mode with BLOCK: one failed precondition blocks the goal
  even when another one is achieved.

## Chores

- Release 0.8.0, which drops Django < 5.2.
- Busy and threaded workers each list the transition handlers,
  so a new handler must be added to both.
- The threaded worker records pickups even with killer task detection off,
  2 transactions per goal nobody needs (finding 2 in `database-load.md`).
- `NOTIFY` with a goal id in its text rules out prepared statements
  and adds a `pg_stat_statements` entry per goal (finding 3).
- `test_memory_limit[256-True]` fails with `psycopg[binary]` installed.
