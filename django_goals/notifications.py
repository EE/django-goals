from django.db import connection, connections


def notify_goal_waiting_for_worker(cursor, goal_id):
    """
    Notify that the goal is waiting for a worker to pick it up.
    """
    cursor.execute("NOTIFY goal_waiting_for_worker, %s", [str(goal_id)])


def listen_goal_waiting_for_worker():
    with connection.cursor() as cursor:
        cursor.execute("LISTEN goal_waiting_for_worker")


def notify_goal_progress(goal_id, state):
    """
    Notify that the goal has changed its state.
    """
    with connections['default'].cursor() as cursor:
        channel = get_goal_progress_channel(goal_id)
        cursor.execute(f"NOTIFY {channel}, %s", [
            state,
        ])


def listen_goal_progress(goal_id):
    """
    Listen for goal progress notifications.
    """
    with connections['default'].cursor() as cursor:
        channel = get_goal_progress_channel(goal_id)
        cursor.execute(f'LISTEN {channel}')


def get_goal_progress_channel(goal_id):
    """
    Get the channel name for goal progress notifications.
    """
    return f'goal_progress_{goal_id.hex}'


def wait():
    """
    Wait for a goal progress notification.
    """
    pg_conn = connections['default'].connection
    notification_generator = pg_conn.notifies()
    for notification in notification_generator:
        notification_generator.close()
    return notification  # pylint: disable=undefined-loop-variable
