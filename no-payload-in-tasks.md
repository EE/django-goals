# The Business Object Queue Coordination Pattern

## Core Observation

If your queue system generates unique IDs for each message, you can use these IDs as temporary foreign keys in your business objects. This creates precise coordination without coupling.

## How It Works

If you have a queue that returns an ID when you create a message, you can:
- Store that queue ID in your business object
- Have workers look up business objects by queue ID
- Keep queue messages minimal (just a signal + the ID)

This inverts the traditional approach where you put business IDs in queue messages.

## Interesting Properties

### The queue stays generic
Since the queue only carries signals and its own IDs, it doesn't need to know anything about your business domain. No business identifiers, no domain concepts, no growing message complexity.

### Business objects become queue-aware
By storing queue IDs in business objects, you can see queue participation through normal database queries. If an order has a shipping_task_id field, you know it's queued for shipping.

### Direct lookups
If you index the queue ID fields, workers can find their assigned work with simple equality queries. No scanning, no complex matching logic.

### Flexible relationships
You can model different cardinalities as needed:
- Multiple objects pointing to one task (batch processing)
- One object with multiple task IDs (parallel workflows)
- Zero-or-one object per task if you make the queue ID field unique

## Key Requirement

This approach needs a queue where you can create a task and get its ID before workers can process it. Some options:

- Database-backed queues where you control visibility (especially powerful when queue and business data share the same database - you can atomically create the task and assign its ID)
- Queues with delayed visibility features

If your queue immediately publishes messages to workers, you'd need additional coordination.

## Trade-offs

**Traditional approach** (business ID in queue):
- Queue messages know about business identifiers
- Message size varies with identifier complexity
- Natural for immediate processing

**Queue ID in business object**:
- Queue stays domain-agnostic
- Fixed message size
- Requires two-phase task creation
- Queue state visible in business queries

## When This Might Be Useful

- If you want minimal queue messages
- If you like seeing queue state in your business data
- If you have complex or composite business identifiers
- If you want one queue to serve multiple business domains
- If you already have a database with your business objects

# Task Queue Reference Patterns: Classic vs. Inverted Approaches

Background task queues enable asynchronous processing of operations. This document compares two fundamental approaches to managing the relationship between tasks and the objects they operate on.

**Classic Approach:** Tasks reference objects in their payload

```python
# Store object reference in task payload
enqueue('process_payment', {'order_id': order.id})

# Worker retrieves object using payload data
def process_payment(task):
    order_id = task.payload['order_id']
    order = Order.objects.get(id=order_id)
    # Process order...
```

Sometimes there is some sugar on top, like that:

```python
@task
def process_payment(order_id):
    order = Order.objects.get(id=order_id)
    # Process order...

process_payment.run_in_background(order_id=order.id)
```

**Inverted Approach**: Objects reference the tasks that process them

```python
with transaction.atomic():
    # Create task with minimal/no payload
    task_id = enqueue('process_payment')
    
    # Store task reference in object
    order.processing_task_id = task_id
    order.save()

# Worker retrieves object using task reference
def process_payment(task):
    order = Order.objects.get(processing_task_id=task.id)
    # Process order...
```    

On the first look this inverted approach doesn't look so good, but wait.

## Payload Validation Issues

**Classic Approach:**
```python
# Potential issues at creation time:
task_queue.enqueue('process_payment', {'order_id': order.id})  # Correct
task_queue.enqueue('process_payment', {'orderid': order.id})   # Wrong key (typo)
task_queue.enqueue('process_payment', {})                      # Missing key

# Issues discovered only at execution time
def process_payment(task):
    try:
        order_id = task.payload['order_id']
        order = Order.objects.get(id=order_id)
    except KeyError:
        logger.error("Missing order_id in payload")
        return
```

**Inverted Approach:**
```python
# No payload to validate
task_id = task_queue.enqueue('process_payment')
order.processing_task_id = task_id
order.save()

# Simple worker logic
def process_payment(task):
    order = Order.objects.get(processing_task_id=task.id)
    # Process order...
```

## Preventing Duplicate Processing

**Classic Approach:**
```python
# Need complex query to find existing tasks
existing_tasks = Task.objects.filter(
    type='process_payment', 
    status='pending',
    payload__contains={'order_id': order.id}
)
if not existing_tasks.exists():
    task_queue.enqueue('process_payment', {'order_id': order.id})
```

**Inverted Approach:**
```python
# Simple check on the object itself
if not order.processing_task_id:
    task_id = task_queue.enqueue('process_payment')
    order.processing_task_id = task_id
    order.save()
```

## Cancelling Tasks

**Classic Approach:**
```python
# Need to search tasks by payload contents
tasks = Task.objects.filter(
    type='process_payment',
    payload__contains={'order_id': order.id}
)
for task in tasks:
    task_queue.cancel(task.id)
```

**Inverted Approach:**
```python
# Task ID directly available from object
if order.processing_task_id:
    task_queue.cancel(order.processing_task_id)
    order.processing_task_id = None
    order.save()
```

## Finding Objects Being Processed

**Classic Approach:**
```python
# Multi-step process to find objects in processing
pending_tasks = Task.objects.filter(type='process_payment', status='pending')
order_ids = [task.payload.get('order_id') for task in pending_tasks]
in_process_orders = Order.objects.filter(id__in=order_ids)
```

**Inverted Approach:**
```python
# Direct query
in_process_orders = Order.objects.filter(processing_task_id__isnull=False)
```

## Conclusion

The classic approach (tasks reference objects) appears simpler initially but creates numerous edge cases and validation challenges. The inverted approach (objects reference tasks) aligns with database design principles and naturally solves common problems with less code and fewer edge cases.

The inverted pattern produces more robust systems through:

1. Database-enforced task-object relationships
2. Simplified worker functions
3. Easier tracking of processing state
4. Natural prevention of duplicate processing
5. Guaranteed access to current object state
