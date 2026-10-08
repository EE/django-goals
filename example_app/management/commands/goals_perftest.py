"""
Measure how fast workers get through a graph of goals, and what it costs the database.

    ./manage.py goals_perftest butterfly 8 --worker threaded --threads 4
    ./manage.py goals_perftest wide 3000 --worker threaded --processes 4 --threads 16 --handler-time 0.1
    ./manage.py goals_perftest idle 10 --worker busy --processes 4

Handlers do nothing, or sleep for --handler-time seconds, like waiting for a remote API.
The idle scenario runs workers with no goals for `size` seconds.

Database cost is mostly in units that don't depend on hardware:
statements, transactions (counted by their COMMIT, so not autocommit ones), WAL, buffer pages and rows.
Server CPU does depend on it, and is measured only when the server runs on this machine.
Per-statement numbers need the pg_stat_statements extension.

The graph is built and committed before workers start.
Run it with no other goals waiting - the workers would pursue them too.
"""
import collections
import functools
import os
import re
import subprocess
import sys
import threading
import time
from argparse import ArgumentParser
from contextlib import contextmanager
from typing import Any, Callable, Iterator
from urllib.parse import urlparse, urlunparse

import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction
from django.utils import timezone

from django_goals.models import (
    WAITING_STATES, AllDone, Goal, GoalDependency, GoalPickup, GoalProgress,
    schedule,
)


APPLICATION_NAME = 'goals_perftest_worker'
HANDLER = f'{__name__}.pursue'


def pursue(goal: Goal, seconds: float) -> AllDone:
    if seconds:
        time.sleep(seconds)
    return AllDone()


AddGoalT = Callable[..., Goal]


def build_idle(size: int, add: AddGoalT) -> int:
    return 0


def build_wide(size: int, add: AddGoalT) -> int:
    for _ in range(size):
        add()
    return 1


def build_chain(size: int, add: AddGoalT) -> int:
    goal = add()
    for _ in range(size - 1):
        goal = add(precondition_goals=[goal])
    return size


def build_butterfly(size: int, add: AddGoalT) -> int:
    """
    `size + 1` stages of `2 ** size` goals, between a start goal and an end goal.
    Goal `i` of a stage depends on goals `i` and `i ^ 2 ** k` of the previous one,
    so goals of the last stage depend on all goals of the first one.
    """
    start = add()
    stage = [add(precondition_goals=[start]) for _ in range(2 ** size)]
    for k in range(size):
        stage = [
            add(precondition_goals=[stage[i], stage[i ^ 2 ** k]])
            for i in range(2 ** size)
        ]
    add(precondition_goals=stage)
    return size + 3


# Scenarios build goals with `add` and return the number of dependency levels.
SCENARIOS: dict[str, Callable[[int, AddGoalT], int]] = {
    'idle': build_idle,
    'wide': build_wide,
    'chain': build_chain,
    'butterfly': build_butterfly,
}


class Command(BaseCommand):
    help = 'Measure how fast workers get through a graph of goals, and what it costs the database'

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument('scenario', choices=SCENARIOS)
        parser.add_argument('size', type=int)
        parser.add_argument('--worker', choices=['busy', 'threaded'], default='busy')
        parser.add_argument('--processes', type=int, default=1)
        parser.add_argument('--threads', type=int, default=1, help='Threads per threaded worker process')
        parser.add_argument('--handler-time', type=float, default=0, help='Seconds each handler sleeps')

    def handle(
        self, scenario: str, size: int, worker: str, processes: int, threads: int, handler_time: float,
        **options: object,
    ) -> None:
        if Goal.objects.filter(state__in=WAITING_STATES).exists():
            raise CommandError('Some goals are waiting already. Workers would pursue them too.')
        # don't depend on dead rows left by earlier runs
        with connection.cursor() as cursor:
            for model in (Goal, GoalDependency, GoalProgress, GoalPickup):
                cursor.execute(f'VACUUM ANALYZE {model._meta.db_table}')

        created_after = timezone.now()
        start = time.monotonic()
        with transaction.atomic():
            levels = SCENARIOS[scenario](size, functools.partial(schedule, HANDLER, kwargs={'seconds': handler_time}))
        build_time = time.monotonic() - start
        goals = Goal.objects.filter(handler=HANDLER, created_at__gte=created_after)
        goals_count = goals.count()
        self.stdout.write(
            f'{scenario} {size}: {goals_count} goals, {levels} levels, built in {build_time:.1f}s, '
            f'psycopg {psycopg.pq.__impl__} implementation',
        )

        if worker == 'busy':
            command = ['goals_busy_worker']
            threads = 1
        else:
            command = ['goals_threaded_worker', '--threads', str(threads)]
        progress = GoalProgress.objects.filter(goal__in=goals)
        try:
            with monitor_activity() as activity, run_workers(command, processes) as workers:
                # Measure from the first goal pursued (or after a while, when idle), skipping worker startup.
                if goals_count:
                    wait(progress.exists, workers)
                else:
                    time.sleep(3)
                has_statements = reset_statements()
                before = {**get_counters(), **measure_cpu(workers, activity)}
                progress_before = progress.count()
                if goals_count:
                    wait(lambda: not goals.filter(state__in=WAITING_STATES).exists(), workers)
                else:
                    time.sleep(size)
                cpu = measure_cpu(workers, activity)
            # workers exited, so their statistics are flushed
            after = {**get_counters(), **cpu}
            used = {key: value - before[key] for key, value in after.items()}
            statements, evicted = get_statements() if has_statements else ([], 0)

            self.stdout.write(f'{worker} worker, {processes} process(es) x {threads} thread(s)')
            if goals_count:
                duration = self.report_goals(goals, levels, processes * threads, handler_time)
                unit, per = 'goal', progress.count() - progress_before
            else:
                duration = size
                unit, per = 'worker second', processes * threads * size
            self.stdout.write(
                f'worker CPU {used["cpu"]:.1f}s ({used["cpu"] / duration / processes:.0%} of a core per process), '
                f'{used["deadlocks"]} deadlocks',
            )
            self.report_waits(activity.waits)
            if per:
                self.report_database(unit, per, used, statements, evicted)
            else:
                self.stdout.write('all goals were pursued before measuring started, make the graph bigger')
        finally:
            with transaction.atomic():
                GoalDependency.objects.filter(dependent_goal__in=goals).delete()
                goals.delete()

    def report_goals(
        self, goals: 'models.QuerySet[Goal]', levels: int, workers: int, handler_time: float,
    ) -> float:
        stats = GoalProgress.objects.filter(goal__in=goals).aggregate(
            count=models.Count('id'),
            first=models.Min('created_at'),
            last=models.Max(models.ExpressionWrapper(
                models.F('created_at') + models.F('time_taken'),
                output_field=models.DateTimeField(),
            )),
            handler_time=models.Avg('time_taken'),
        )
        duration: float = (stats['last'] - stats['first']).total_seconds()
        line = f'{duration:.2f}s, {stats["count"] / duration:.0f} goals/s'
        if levels > 1:
            line += f', {duration / levels * 1000:.0f} ms per level'
        if handler_time and levels == 1:
            # Skip workers starting and running out of work: rate between 10% and 90% of goals started.
            started = sorted(GoalProgress.objects.filter(goal__in=goals).values_list('created_at', flat=True))
            first, last = len(started) // 10, len(started) * 9 // 10
            rate = (last - first) / (started[last] - started[first]).total_seconds()
            ideal = workers / handler_time
            line += f', {rate:.0f} goals/s steady, {rate / ideal:.0%} of {ideal:.0f} with workers always busy'
        self.stdout.write(line)
        self.stdout.write(f'handler {stats["handler_time"].total_seconds() * 1000:.2f} ms avg')
        return duration

    def report_waits(self, waits: collections.Counter[str]) -> None:
        self.stdout.write('worker connections spend time:')
        total = sum(waits.values())
        for wait_description, count in waits.most_common(6):
            self.stdout.write(f'  {count / total:6.1%}  {wait_description}')

    def report_database(
        self, unit: str, per: int, counters: dict[str, float], statements: list[dict[str, Any]], evicted: int,
    ) -> None:
        self.stdout.write(f'database cost per {unit}:')
        if counters['server_cpu']:
            self.stdout.write(f'  {counters["server_cpu"] / per * 1000:.2f} ms server CPU')
        if statements:
            calls = sum(s['calls'] for s in statements)
            commits = sum(s['calls'] for s in statements if s['query'] == 'COMMIT')
            self.stdout.write(
                f'  {calls / per:.1f} statements, {commits / per:.2f} transactions with COMMIT, '
                f'{sum(s["time"] for s in statements) / per:.3f} ms executing, '
                f'{sum(s["plan_time"] for s in statements) / per:.3f} ms planning, '
                f'{sum(s["blocks"] for s in statements) / per:.0f} buffer pages touched, '
                f'{sum(s["dirtied"] for s in statements) / per:.1f} dirtied',
            )
        self.stdout.write(
            f'  {counters["wal_bytes"] / per / 1024:.2f} kB WAL, '
            f'rows: {counters["inserted"] / per:.1f} inserted, {counters["updated"] / per:.1f} updated '
            f'({counters["hot_updated"] / max(counters["updated"], 1):.0%} HOT), {counters["deleted"] / per:.1f} deleted',
        )
        if not statements:
            self.stdout.write('  install pg_stat_statements for statements, transactions and execution time')
            return
        if evicted:
            self.stdout.write(f'  pg_stat_statements evicted statements {evicted} times. Raise pg_stat_statements.max.')
        self.stdout.write(f'  calls  ms exec  pages  WAL B  per {unit}, top statements by execution time:')
        for s in sorted(statements, key=lambda s: -s['time'])[:10]:
            self.stdout.write(
                f'  {s["calls"] / per:5.2f}  {s["time"] / per:7.3f}  {s["blocks"] / per:5.1f}  '
                f'{s["wal_bytes"] / per:5.0f}  {" ".join(s["query"].split())[:100]}',
            )


@contextmanager
def run_workers(command: list[str], processes: int) -> Iterator[list['subprocess.Popen[bytes]']]:
    # workers connect to the database we are connected to, also in tests
    database_url = urlparse(os.environ['DATABASE_URL'])._replace(path=connection.settings_dict['NAME'])
    env = {
        **os.environ,
        'DATABASE_URL': str(urlunparse(database_url)),
        'PGAPPNAME': APPLICATION_NAME,
    }
    workers = [
        subprocess.Popen(
            [sys.executable, str(settings.BASE_DIR / 'manage.py'), *command],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(processes)
    ]
    try:
        yield workers
    finally:
        for worker in workers:
            worker.terminate()
        for worker in workers:
            worker.wait()


def wait(condition: Callable[[], bool], workers: list['subprocess.Popen[bytes]']) -> None:
    while not condition():
        if any(worker.poll() is not None for worker in workers):
            raise CommandError('A worker exited. Run it by hand to see why.')
        time.sleep(0.01)


class Activity:
    def __init__(self) -> None:
        self.waits: collections.Counter[str] = collections.Counter()
        self.pids: set[int] = set()  # server processes serving workers


@contextmanager
def monitor_activity() -> Iterator[Activity]:
    """
    Count what worker connections are doing, sampled from pg_stat_activity.
    """
    activity = Activity()
    stop = threading.Event()

    def sample() -> None:
        try:
            with connection.cursor() as cursor:
                while not stop.wait(0.01):
                    cursor.execute("""
                        SELECT pid, state, wait_event_type, wait_event
                        FROM pg_stat_activity
                        WHERE application_name = %s
                    """, [APPLICATION_NAME])
                    for pid, state, wait_event_type, wait_event in cursor.fetchall():
                        activity.pids.add(pid)
                        activity.waits[describe_wait(state, wait_event_type, wait_event)] += 1
        finally:
            connection.close()

    sampler = threading.Thread(target=sample)
    sampler.start()
    try:
        yield activity
    finally:
        stop.set()
        sampler.join()


def describe_wait(state: str, wait_event_type: str | None, wait_event: str | None) -> str:
    if state == 'idle':
        return 'worker, between transactions (Python, sleeping)'
    if state == 'idle in transaction':
        return 'worker, in transaction (Python, holding locks)'
    if wait_event_type is None:
        return 'database, executing'
    return f'database, {wait_event_type}: {wait_event}'


def measure_cpu(workers: list['subprocess.Popen[bytes]'], activity: Activity) -> dict[str, float]:
    return {
        'cpu': get_cpu_seconds([worker.pid for worker in workers]),
        'server_cpu': get_cpu_seconds(get_server_pids(activity.pids.copy())),
    }


def get_cpu_seconds(pids: list[int]) -> float:
    ticks = 0
    for pid in pids:
        with open(f'/proc/{pid}/stat', encoding='ascii') as stat:
            fields = stat.read().rsplit(')', 1)[1].split()
        ticks += int(fields[11]) + int(fields[12])  # utime, stime
    return ticks / os.sysconf('SC_CLK_TCK')


def get_server_pids(pids: set[int]) -> list[int]:
    """
    Server processes we can see, because the server runs on this machine.
    """
    server_pids = []
    for pid in pids:
        try:
            with open(f'/proc/{pid}/comm', encoding='ascii') as comm:
                if comm.read().strip() == 'postgres':
                    server_pids.append(pid)
        except OSError:
            pass
    return server_pids


def get_counters() -> dict[str, int]:
    """
    Cumulative database counters. We only read, so the change comes from workers.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                pg_current_wal_lsn() - '0/0',
                sum(n_tup_ins), sum(n_tup_upd), sum(n_tup_hot_upd), sum(n_tup_del),
                (SELECT deadlocks FROM pg_stat_database WHERE datname = current_database())
            FROM pg_stat_user_tables
            WHERE relname LIKE 'django\\_goals\\_%'
        """)
        row = cursor.fetchone()
    assert row is not None
    keys = ['wal_bytes', 'inserted', 'updated', 'hot_updated', 'deleted', 'deadlocks']
    return {key: int(value) for key, value in zip(keys, row)}


def reset_statements() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'")
        if cursor.fetchone() is None:
            return False
        cursor.execute('SELECT pg_stat_statements_reset()')
    return True


def get_statements() -> tuple[list[dict[str, Any]], int]:
    """
    Statements run since reset_statements(), except ours - they read statistics and wait for goals.
    Statements differing only in a savepoint name or a goal id are summed up.
    Also returns how many times statements were evicted, losing their numbers.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                query, calls, total_exec_time, total_plan_time,
                shared_blks_hit + shared_blks_read, shared_blks_dirtied, wal_bytes
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
            AND query NOT LIKE '%%pg\\_%%'
            AND query NOT LIKE %s
        """, ['%"handler" = %'])
        keys = ['calls', 'time', 'plan_time', 'blocks', 'dirtied', 'wal_bytes']
        statements: dict[str, dict[str, Any]] = {}
        for query, *values in cursor.fetchall():
            query = re.sub(r'SAVEPOINT "\w+"', 'SAVEPOINT ...', query)
            query = re.sub(r"^NOTIFY (\w+?)(_[0-9a-f]{32})?, '.*'$", r'NOTIFY \1..., ...', query)
            statement = statements.setdefault(query, {'query': query, **dict.fromkeys(keys, 0)})
            for key, value in zip(keys, values):
                statement[key] += value
        cursor.execute('SELECT dealloc FROM pg_stat_statements_info')
        evicted: int = cursor.fetchone()[0]
    return list(statements.values()), evicted
