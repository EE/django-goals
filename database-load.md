# Database Load

The database is the hardest part to scale.
This note estimates how much work goals and workers give it, from `goals_perftest` runs in October 2026.

## Measuring

```bash
./manage.py goals_perftest wide 2000 --worker busy --processes 4
./manage.py goals_perftest wide 2000 --worker threaded --processes 4 --threads 16 --handler-time 0.1
./manage.py goals_perftest idle 10 --worker threaded --processes 4 --threads 16
```

The command's docstring says what it reports.
Per-statement numbers need `pg_stat_statements` with `track_planning = on`
and a `pg_stat_statements.max` above the default 5000 (we used 20000).
It vacuums goal tables before each run, so runs don't depend on earlier ones.

Setup: PostgreSQL 16 on the same 4-core machine as workers, Python 3.13, psycopg 3.3 in its pure Python implementation.
Handlers do nothing unless stated.
Repeated runs differ by up to about 20%, so only large effects count.

## Cost per goal

| | independent, busy | butterfly (2 dependents), busy | independent, threaded, 64 threads, 100 ms handlers |
|---|---|---|---|
| transactions | 1.03 | 1.39 | 3.1 (2.1 with COMMIT, 1 in autocommit) |
| statements | 9.1 | 11.3 | 13.4 |
| WAL | 0.9 kB | 2.7 kB | 1.5 kB |
| rows | 1 inserted, 1 updated | 1 inserted, 4 updated | 2 inserted, 1 updated, 1 deleted |
| buffer pages touched | 56 | 104 | 145 |
| server CPU | 1.7 ms | 2.3 ms | 3.1 - 3.7 ms |
| of it executing / planning | 0.21 / 0.48 ms | 0.58 ms / - | 0.94 / 0.90 ms |

The rest of server CPU is probably parsing, the protocol and commits.

## Cost of idle workers

Workers poll whether there is work or not.

| per second of an idle | busy worker | thread of a threaded worker |
|---|---|---|
| transactions | 7.0 | 1.4 |
| statements | 22 | 4.2 |
| server CPU | 3.75 ms | 0.70 ms |

## Estimating for a given database

Transactions, statements, WAL and rows don't depend on hardware.
1000 goals/s with busy workers takes about 1000 commits/s and 1 MB/s of WAL.
Each commit that wrote something flushes WAL, so on network disks flush latency matters most.
Postgres flushes concurrent commits together.
Idle workers only read, so they cost CPU, not flushes.

Server CPU scales roughly with core speed, so take it as an order of magnitude:
1000 goals/s with busy workers takes about 1.7 cores, and 100 idle busy workers about 0.4 of a core.

For a specific database, run `goals_perftest` against a staging copy, with workers on another machine.
Everything but server CPU is counted remotely too.

## Findings

### 1. Picking gets expensive with dead index entries at the head of the queue

The most promising one to look into.
Workers pick goals with `... ORDER BY deadline LIMIT 1 FOR NO KEY UPDATE SKIP LOCKED`, using `goals_waiting_for_worker_idx`.
Pick cost per goal, independent goals, 100 ms handlers, threaded workers in 4 processes:

| workers | after earlier runs, no vacuum | after vacuum |
|---|---|---|
| 16 | 0.82 ms, 94 pages | 0.26 ms, 54 pages |
| 32 | 0.64 ms, 107 pages | 0.66 ms, 91 pages |
| 64 | 3.16 ms, 164 pages | 0.34 ms, 82 pages |

With 4 busy workers and no-op handlers a pick takes 0.04 ms and about 10 pages.
After vacuum the cost doesn't grow with the number of workers.

Hypothesis, not verified:

- Pursuing a goal changes its state, so the index entry of its waiting version points to a dead row until vacuum.
  Pursued goals have the earliest deadlines, so these entries sit at the head of the index, and every pick walks through them.
- A scan marks such an entry dead (LP_DEAD), so later scans skip it without visiting the table,
  but only once the update is older than every open transaction.
  With 64 handlers holding 100 ms transactions, recently pursued goals can't be marked yet.
- Goals deleted by earlier runs add more dead entries at the head.

If it holds, picking in production gets slower as autovacuum lags on the goal table
(by default it waits for dead rows to reach 20% of the table),
and with long transactions anywhere in the database.

To verify:

- `EXPLAIN (ANALYZE, BUFFERS)` of the pick on a vacuumed and on a bloated table,
  and `pgstatindex` or `pageinspect` on `goals_waiting_for_worker_idx`.
- Pick cost for handler times 0, 0.1 and 0.5 s at a fixed number of workers.
- Pick cost after runs without vacuum (`goals_perftest` would need to skip its own).
- A long transaction open in another connection during a run.

### 2. The threaded worker records pickups with killer task detection off

`PickupMonitorThread` inserts and deletes a `GoalPickup` row per goal, in two transactions,
even when `GOALS_MAX_PICKUPS` is `None` (the default) and nobody reads them.
That's why the threaded worker takes about 3.1 transactions per goal and the busy one 1.03.

### 3. Planning takes more server CPU than executing

Django sends statements as text, so Postgres plans each one, and the goal table has 10 indexes to consider.
Prepared statements (psycopg's server-side binding) would cut planning,
but `NOTIFY` with a goal id in its text takes no parameters.
`SELECT pg_notify(%s, %s)` would.

That text, and Django's savepoint names, also differ from goal to goal,
so every goal adds entries to `pg_stat_statements`, pushing out other statements in users' databases.

### 4. Idle workers poll

It grows with the number of workers, not with work.
A worker sleeping on `LISTEN` wouldn't poll.

### 5. Goal updates aren't HOT

State and counter columns are indexed, so every update writes into the goal table's indexes, about 0.55 kB of WAL.
Achieving a goal with 2 dependents updates 3 goal rows.

### 6. Many workers

With 500 ms handlers, 64 and 128 workers reach about 90% of the throughput of workers always busy.
The database executes under 1% of the time, without lock waits or deadlocks.
With 100 ms handlers, 64 workers reach about 280 goals/s, and then this machine runs out of CPU before the database shows contention.
Finding the database's limit needs workers on other machines.
The first contention to watch is `Lock: object` waits, about 1% of samples at 64 workers,
probably `NOTIFY` taking a database-wide lock at commit.

## Not the database

- A single worker is limited by Python: 4.5 ms of worker CPU per goal with no-op handlers, about 9 round trips.
- Threads help only handlers that block, like remote API calls.
  With no-op handlers 1 thread does 126 goals/s and 4 threads 148 (psycopg's C implementation).
- With psycopg's pure Python implementation every libpq call gives up the GIL, and threads wait for it:
  4 threads did 61 goals/s with it and 148 with `psycopg[binary]`.
- A dependency level takes about 1 s with the threaded worker, which sleeps 1 s when idle, and 23 ms with the busy one.
  A blocking worker would need goals to reach `WAITING_FOR_WORKER` when their last precondition is achieved,
  which only background transitions do now.
