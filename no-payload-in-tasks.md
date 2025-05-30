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
