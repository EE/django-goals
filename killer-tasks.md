# Killer Task Detection

## Problem

When a worker process crashes catastrophically (segfault, OOM kill, etc.) while processing a goal, the goal remains in `WAITING_FOR_WORKER` state with no trace of what happened. The next worker picks up the same goal and crashes in the same way, creating an infinite loop of worker deaths without any audit trail.

This is particularly dangerous because:
- No progress record is created (transaction rolls back on crash)
- The problematic goal keeps getting retried indefinitely
- All workers eventually get killed by the same task
- No visibility into which specific goal is causing the crashes

## Solution: Out-of-Transaction Tracking

Track goal processing attempts using a separate database connection outside the main transaction that holds the goal lock.

### Approach

1. **Worker Tracking Table**: Create a new table to track active goal processing:
   ```sql
   CREATE TABLE worker_tracking (
       worker_id VARCHAR(100),
       goal_id UUID,
       started_at TIMESTAMP DEFAULT NOW(),
       PRIMARY KEY (worker_id, goal_id)
   );
   ```

2. **Immediate Tracking**: After selecting a goal but before processing:
   ```python
   # Main transaction selects and locks goal
   goal = Goal.objects.select_for_update(skip_locked=True).first()
   
   # Separate autocommit connection immediately records attempt
   tracking_conn.execute(
       "INSERT INTO worker_tracking (worker_id, goal_id) VALUES (%s, %s)",
       [worker_id, goal.id]
   )
   ```

3. **Cleanup on Success**: Before committing the main transaction:
   ```python
   # Remove tracking entry on successful completion
   tracking_conn.execute(
       "DELETE FROM worker_tracking WHERE worker_id = %s AND goal_id = %s",
       [worker_id, goal.id]
   )
   ```

4. **Killer Detection**: On worker startup, check for goals with multiple tracking entries:
   ```sql
   SELECT goal_id, COUNT(*) as attempt_count 
   FROM worker_tracking 
   GROUP BY goal_id 
   HAVING COUNT(*) >= 3  -- configurable threshold
   ```

### Properties

- **Crash-resistant**: Tracking survives worker crashes since it uses separate connection
- **Precise identification**: Know exactly which goal is killing workers
- **Self-cleaning**: Successful processing automatically removes tracking entries
- **Minimal overhead**: Single INSERT/DELETE per goal processing
