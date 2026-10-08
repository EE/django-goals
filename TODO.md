# TODO

## Bugs

Reproduced against 0.7.7.

- `goals_fsck` sets `waiting_for_count` of an ANY-mode goal back to 1
  when one precondition has woken it but others are still open,
  so the goal waits for another one.
- Adding preconditions locks them, so it waits for those being pursued,
  and deadlocks when it holds a goal whose counters the pursuit updates:
  `RetryMeLater(precondition_goals=[a, b])` when `a` depends on `b` and is newer
  (preconditions are locked newest first),
  or an ANY-mode goal scheduling something after its precondition still being pursued.
  Postgres aborts one side after `deadlock_timeout`.
  Ways to fix it are under Decide.
- `goals_fsck` holds a goal while locking its preconditions,
  so it deadlocks with each one being achieved meanwhile.
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
- Adding preconditions without waiting for locks.
  Like deadline propagation: skip locked preconditions,
  add their dependencies as not counted yet,
  and count them in the background once their preconditions can be locked.
  Transitions would update counters only through counted dependencies.
  Locking `FOR KEY SHARE` when adding and `FOR UPDATE` before updating dependents works too,
  but takes raw SQL, a statement per goal,
  and makes workers wait for transactions adding preconditions.

## Chores

- Release 0.8.0, which drops Django < 5.2.
- Busy and threaded workers each list the transition handlers,
  so a new handler must be added to both.
- The threaded worker records pickups even with killer task detection off,
  2 transactions per goal nobody needs (finding 2 in `database-load.md`).
- `NOTIFY` with a goal id in its text rules out prepared statements
  and adds a `pg_stat_statements` entry per goal (finding 3).
- `test_memory_limit[256-True]` fails with `psycopg[binary]` installed.
