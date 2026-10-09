# TODO

## Bugs

Reproduced against 0.7.7.

- Adding preconditions locks them, so it waits for those being pursued,
  and deadlocks when it holds a goal whose counters the pursuit updates:
  `RetryMeLater(precondition_goals=[a, b])` when `a` depends on `b` and is newer
  (preconditions are locked newest first),
  or an ANY-mode goal scheduling something after its precondition still being pursued.
  Postgres aborts one side after `deadlock_timeout`.
  Ways to fix it are under Decide.

## Investigate

- Picking gets expensive with dead index entries at the head of the queue
  (finding 1 in `database-load.md`).
- `GOALS_MEMORY_LIMIT_MIB` in the threaded worker: the limit is per process,
  so handler threads overwrite each other's,
  and one can restore another's, leaving it on between handlers.
- A blocking worker notified of a goal that someone else holds locked
  (like `schedule()` adding it as a precondition) skips it and loses the notification,
  so the goal waits until a busy or threaded worker polls.

## Decide

- Retrying a `GIVEN_UP` goal allows a single attempt, because old failures still count.
- ANY mode with BLOCK, one precondition achieved and another one failed:
  the goal is blocked or pursued depending on which transition runs first,
  and when pursued, `FsckMiddleware` logs a waiting-for-failed bug.
  Elsewhere ANY mode doesn't take a wake-up back, so pursuing it would fit,
  and blocking only when all preconditions it still waits for failed.
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
- A Hypothesis state machine test: random API calls and worker transitions,
  with counters, `goals_fsck` and liveness checked after each step.
  A prototype of it found most of the recent counter bugs.
