from datetime import datetime

from sirina_agent.core.tasks import TaskStore, next_run, parse_recurring_prompt


def test_interval_task_persists_and_becomes_due(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    now = datetime.fromisoformat("2026-08-02T12:00:00+03:00")

    task = store.add("every 10 minutes", "check service health", now=now)

    assert task.next_run_at == "2026-08-02T12:10:00+03:00"
    assert TaskStore(tmp_path / "tasks.json").list()[0].prompt == "check service health"
    assert store.due(datetime.fromisoformat("2026-08-02T12:10:00+03:00"))[0].id == task.id


def test_daily_and_cron_next_run_use_local_timezone():
    now = datetime.fromisoformat("2026-08-02T12:00:00+03:00")

    assert next_run("daily at 09:30", now).isoformat() == "2026-08-03T09:30:00+03:00"
    assert next_run("15 14 * * *", now).isoformat() == "2026-08-02T14:15:00+03:00"


def test_natural_recurring_prompt_is_split_into_schedule_and_prompt():
    assert parse_recurring_prompt("Every 30 minutes, check the web service") == (
        "Every 30 minutes",
        "check the web service",
    )


def test_task_controls_and_run_metadata(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    now = datetime.fromisoformat("2026-08-02T12:00:00+03:00")
    task = store.add("every 1 hours", "status", now=now)

    assert not store.update_enabled(task.id, False).enabled
    assert store.update_enabled(task.id, True).enabled
    assert store.mark_started(task.id, now).last_result == "running"
    assert store.mark_finished(task.id, "healthy", now).last_result == "healthy"
    assert store.delete(task.id)
    assert store.list() == []
