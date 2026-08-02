from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import uuid4


@dataclass
class RecurringTask:
    id: str
    schedule: str
    prompt: str
    enabled: bool
    created_at: str
    next_run_at: str
    last_run_at: str | None = None
    last_finished_at: str | None = None
    last_result: str = "never run"


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self._lock = RLock()

    def list(self) -> list[RecurringTask]:
        with self._lock:
            return self._load()

    def add(self, schedule: str, prompt: str, now: datetime | None = None) -> RecurringTask:
        current = (now or datetime.now().astimezone()).astimezone()
        task = RecurringTask(
            id=f"task_{uuid4().hex[:8]}",
            schedule=schedule.strip(),
            prompt=prompt.strip(),
            enabled=True,
            created_at=current.isoformat(),
            next_run_at=next_run(schedule, current).isoformat(),
        )
        with self._lock:
            tasks = self._load()
            tasks.append(task)
            self._save(tasks)
        return task

    def update_enabled(self, task_id: str, enabled: bool) -> RecurringTask | None:
        return self._update(task_id, lambda task: setattr(task, "enabled", enabled))

    def delete(self, task_id: str) -> bool:
        with self._lock:
            tasks = self._load()
            retained = [task for task in tasks if task.id != task_id]
            if len(retained) == len(tasks):
                return False
            self._save(retained)
            return True

    def due(self, now: datetime | None = None) -> list[RecurringTask]:
        current = (now or datetime.now().astimezone()).astimezone()
        return [
            task for task in self.list()
            if task.enabled and datetime.fromisoformat(task.next_run_at).astimezone() <= current
        ]

    def mark_started(self, task_id: str, now: datetime | None = None) -> RecurringTask | None:
        current = (now or datetime.now().astimezone()).astimezone()

        def apply(task: RecurringTask) -> None:
            task.last_run_at = current.isoformat()
            task.last_result = "running"
            task.next_run_at = next_run(task.schedule, current).isoformat()

        return self._update(task_id, apply)

    def mark_finished(self, task_id: str, result: str, now: datetime | None = None) -> RecurringTask | None:
        current = (now or datetime.now().astimezone()).astimezone()

        def apply(task: RecurringTask) -> None:
            task.last_finished_at = current.isoformat()
            task.last_result = result[:500]

        return self._update(task_id, apply)

    def _update(self, task_id: str, callback) -> RecurringTask | None:
        with self._lock:
            tasks = self._load()
            selected = next((task for task in tasks if task.id == task_id), None)
            if selected is None:
                return None
            callback(selected)
            self._save(tasks)
            return selected

    def _load(self) -> list[RecurringTask]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [RecurringTask(**item) for item in data if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self, tasks: list[RecurringTask]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps([asdict(task) for task in tasks], indent=2), encoding="utf-8")
        temporary.replace(self.path)


def next_run(schedule: str, after: datetime) -> datetime:
    value = schedule.strip().lower()
    interval = re.fullmatch(r"every\s+(\d+)\s*(minute|minutes|hour|hours|day|days)", value)
    if interval:
        amount = int(interval.group(1))
        if amount < 1:
            raise ValueError("Recurring interval must be positive.")
        unit = interval.group(2)
        return after + timedelta(**{("minutes" if "minute" in unit else "hours" if "hour" in unit else "days"): amount})
    daily = re.fullmatch(r"(?:every\s+day|daily)\s+at\s+(\d{1,2}):(\d{2})", value)
    if daily:
        hour, minute = map(int, daily.groups())
        if hour > 23 or minute > 59:
            raise ValueError("Daily time must use HH:MM in local time.")
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate if candidate > after else candidate + timedelta(days=1)
    fields = value.split()
    if len(fields) == 5:
        return _next_cron(fields, after)
    raise ValueError("Use 'every N minutes|hours|days', 'daily at HH:MM', or a five-field cron expression.")


def _next_cron(fields: list[str], after: datetime) -> datetime:
    minute, hour, day, month, weekday = (
        _cron_values(field, minimum, maximum)
        for field, minimum, maximum in zip(fields, (0, 0, 1, 1, 0), (59, 23, 31, 12, 6))
    )
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = candidate + timedelta(days=366 * 5)
    while candidate <= deadline:
        cron_weekday = (candidate.weekday() + 1) % 7
        if (
            candidate.minute in minute
            and candidate.hour in hour
            and candidate.day in day
            and candidate.month in month
            and cron_weekday in weekday
        ):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("Cron expression has no run time within five years.")


def _cron_values(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            step = int(raw_step)
            if step < 1:
                raise ValueError("Cron step must be positive.")
        if part == "*":
            start, end = minimum, maximum
        elif "-" in part:
            start, end = map(int, part.split("-", 1))
        else:
            start = end = int(part)
        if start < minimum or end > maximum or start > end:
            raise ValueError("Cron field is outside its valid range.")
        values.update(range(start, end + 1, step))
    return values


def parse_recurring_prompt(text: str) -> tuple[str, str] | None:
    match = re.match(
        r"^(every\s+\d+\s+(?:minutes?|hours?|days?)|(?:every\s+day|daily)\s+at\s+\d{1,2}:\d{2})"
        r"\s*[,;:]\s*(.+)$",
        text.strip(),
        re.IGNORECASE,
    )
    return (match.group(1), match.group(2)) if match else None


def format_tasks(tasks: list[RecurringTask]) -> str:
    if not tasks:
        return "No recurring tasks."
    return "\n".join(
        f"{task.id}  {'on' if task.enabled else 'paused'}  next={task.next_run_at}  {task.schedule} :: {task.prompt}"
        for task in tasks
    )
