# TODO

## Bugs

Reproduced against 0.7.7.

- `goals_fsck` sets `waiting_for_count` of an ANY-mode goal back to 1
  when one precondition has woken it but others are still open,
  so the goal waits for another one.
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
