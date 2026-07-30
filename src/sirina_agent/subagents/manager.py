from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class SubagentManager:
    def __init__(self, config, provider_factory: Callable[[], Any]) -> None:
        self.config = config
        self.root = Path(config.root_dir).expanduser().resolve()
        self.provider_factory = provider_factory
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(config.max_concurrent_jobs)),
            thread_name_prefix="ulysses-subagent",
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted_jobs()

    def create(self, name: str, purpose: str, prompt: str) -> dict[str, Any]:
        name = self._valid_name(name)
        purpose = purpose.strip()
        prompt = prompt.strip()
        if not purpose or not prompt:
            raise ValueError("Sub-agent purpose and prompt are required.")
        with self._lock:
            if len(self.list_agents()) >= int(self.config.max_agents):
                raise ValueError("The configured sub-agent limit has been reached.")
            agent_dir = self._agent_dir(name)
            if agent_dir.exists():
                raise ValueError(f"Sub-agent `{name}` already exists.")
            now = self._now()
            agent_dir.mkdir(parents=True)
            for child in ("workspace", "files", "tasks"):
                (agent_dir / child).mkdir()
            record = {"name": name, "purpose": purpose, "created_at": now, "updated_at": now}
            self._write_json(agent_dir / "agent.json", record)
            (agent_dir / "prompt.md").write_text(prompt + "\n", encoding="utf-8")
            return record

    def delete(self, name: str) -> None:
        name = self._valid_name(name)
        with self._lock:
            agent_dir = self._agent_dir(name)
            if not (agent_dir / "agent.json").is_file():
                raise KeyError(f"Sub-agent `{name}` does not exist.")
            active = [job for job in self.list_jobs(name) if job["status"] in {"queued", "running"}]
            if active:
                raise ValueError(f"Sub-agent `{name}` has active jobs and cannot be deleted.")
            shutil.rmtree(agent_dir)

    def list_agents(self) -> list[dict[str, Any]]:
        agents = []
        if not self.root.exists():
            return agents
        for path in sorted(self.root.glob("*/agent.json")):
            try:
                record = self._read_json(path)
                record["jobs"] = len(list((path.parent / "tasks").glob("*/job.json")))
                agents.append(record)
            except (OSError, ValueError, TypeError):
                continue
        return agents

    def delegate(self, name: str, task: str, context: str = "") -> dict[str, Any]:
        name = self._valid_name(name)
        task = task.strip()
        if not task:
            raise ValueError("A sub-agent task is required.")
        agent_dir = self._agent_dir(name)
        if not (agent_dir / "agent.json").is_file():
            raise KeyError(f"Sub-agent `{name}` does not exist.")
        job_id = f"job_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
        job_dir = self._confined(agent_dir / "tasks" / job_id, agent_dir / "tasks")
        job_dir.mkdir(parents=True)
        job = {
            "id": job_id,
            "agent": name,
            "task": task,
            "context": context.strip(),
            "status": "queued",
            "created_at": self._now(),
            "updated_at": self._now(),
            "reported": False,
        }
        self._write_json(job_dir / "job.json", job)
        (job_dir / "request.md").write_text(task + "\n", encoding="utf-8")
        self._executor.submit(self._run_job, name, job_id)
        return job

    def list_jobs(self, agent: str | None = None) -> list[dict[str, Any]]:
        paths = []
        if agent:
            paths = list((self._agent_dir(self._valid_name(agent)) / "tasks").glob("*/job.json"))
        else:
            paths = list(self.root.glob("*/tasks/*/job.json"))
        jobs = []
        for path in paths:
            try:
                jobs.append(self._read_json(path))
            except (OSError, ValueError, TypeError):
                continue
        return sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True)

    def completed_reports(self) -> list[dict[str, Any]]:
        reports = []
        for job in self.list_jobs():
            if job.get("status") not in {"completed", "failed"} or job.get("reported"):
                continue
            response_path = self._job_dir(job["agent"], job["id"]) / "response.md"
            if response_path.is_file():
                reports.append({**job, "response": response_path.read_text(encoding="utf-8")})
            else:
                reports.append(job)
        return reports

    def mark_reported(self, job_ids: list[str]) -> None:
        wanted = set(job_ids)
        with self._lock:
            for job in self.list_jobs():
                if job.get("id") not in wanted:
                    continue
                path = self._job_dir(job["agent"], job["id"]) / "job.json"
                job["reported"] = True
                job["updated_at"] = self._now()
                self._write_json(path, job)

    def summary(self) -> str:
        agents = self.list_agents()
        jobs = self.list_jobs()
        active = sum(job.get("status") in {"queued", "running"} for job in jobs)
        completed = sum(job.get("status") == "completed" for job in jobs)
        return f"{len(agents)} agents / {active} active / {completed} completed"

    def status_detail(self, max_jobs: int = 3, max_task_chars: int = 42) -> str:
        jobs = self.list_jobs()
        active = [job for job in jobs if job.get("status") in {"queued", "running"}]
        inactive = [job for job in jobs if job.get("status") not in {"queued", "running"}]
        selected = (active + inactive)[: max(0, max_jobs)]
        lines = [self.summary()]
        if not selected:
            return "\n".join([*lines, "Delegated jobs", "none"])
        lines.append("Delegated jobs")
        for job in selected:
            task = " ".join(str(job.get("task") or "").split())
            if len(task) > max_task_chars:
                task = task[: max(1, max_task_chars - 3)].rstrip() + "..."
            lines.append(f"[{job.get('status', 'unknown')}] {job.get('agent', 'unknown')}")
            lines.append(f"  {task or 'No task description'}")
        remaining = max(0, len(jobs) - len(selected))
        if remaining:
            lines.append(f"  +{remaining} more")
        return "\n".join(lines)

    def _run_job(self, name: str, job_id: str) -> None:
        job_path = self._job_dir(name, job_id) / "job.json"
        job = self._read_json(job_path)
        job["status"] = "running"
        job["started_at"] = self._now()
        job["updated_at"] = self._now()
        self._write_json(job_path, job)
        try:
            response = self._complete(name, job)
            (job_path.parent / "response.md").write_text(response.rstrip() + "\n", encoding="utf-8")
            job["status"] = "completed"
            job["completed_at"] = self._now()
        except Exception as exc:  # noqa: BLE001 - job failures must be persisted, not escape the worker
            job["status"] = "failed"
            job["error"] = str(exc)[:1000]
        job["updated_at"] = self._now()
        self._write_json(job_path, job)

    def _complete(self, name: str, job: dict[str, Any]) -> str:
        agent_dir = self._agent_dir(name)
        prompt = (agent_dir / "prompt.md").read_text(encoding="utf-8")
        workspace = agent_dir / "workspace"
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are the persistent sub-agent `{name}` and report only to Ulysses.\n\n{prompt}\n\n"
                    "Complete the assigned job and return a concise completion report with outcome, evidence, files created, "
                    "uncertainties, and recommended next steps. Do not address the end user. You cannot create or delegate agents, "
                    "run shell commands, or access files outside your workspace. Use workspace tools when useful."
                ),
            },
            {
                "role": "user",
                "content": f"Assigned by Ulysses:\n{job['task']}\n\nParent context:\n{job.get('context') or 'None supplied.'}",
            },
        ]
        tools = self._workspace_schemas()
        provider = self.provider_factory()
        for _ in range(max(1, int(self.config.max_tool_rounds))):
            response = provider.complete(messages, tools=tools)
            message = response["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                content = str(message.get("content") or "").strip()
                if not content:
                    raise RuntimeError("Sub-agent returned an empty completion report.")
                return content
            messages.append(message)
            for index, call in enumerate(calls):
                function = call.get("function") or {}
                call_id = call.get("id") or f"workspace_{index}"
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    result = self._workspace_tool(workspace, function.get("name", ""), arguments)
                except Exception as exc:  # noqa: BLE001 - return tool errors to the sub-agent for recovery
                    result = f"Workspace tool failed: {exc}"
                messages.append(
                    {"role": "tool", "tool_call_id": call_id, "name": function.get("name", ""), "content": result}
                )
        raise RuntimeError("Sub-agent exceeded its workspace tool-round limit.")

    def _workspace_tool(self, workspace: Path, name: str, arguments: dict[str, Any]) -> str:
        relative = str(arguments.get("path") or ".")
        path = self._confined(workspace / relative, workspace)
        if name == "workspace_list":
            if not path.exists():
                return "Path does not exist."
            entries = sorted(item.relative_to(workspace).as_posix() for item in path.rglob("*") if item.is_file())
            return "\n".join(entries[:500]) or "Workspace is empty."
        if name == "workspace_read":
            if not path.is_file():
                raise ValueError("Requested workspace file does not exist.")
            return path.read_text(encoding="utf-8")[: int(self.config.max_file_chars)]
        if name == "workspace_write":
            content = str(arguments.get("content") or "")
            if len(content) > int(self.config.max_file_chars):
                raise ValueError("Workspace file exceeds the configured size limit.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} characters to {path.relative_to(workspace).as_posix()}."
        raise ValueError(f"Unknown workspace tool `{name}`.")

    @staticmethod
    def _workspace_schemas() -> list[dict[str, Any]]:
        path = {"type": "string", "description": "Relative path inside this sub-agent workspace."}
        return [
            {
                "type": "function",
                "function": {
                    "name": "workspace_list",
                    "description": "List workspace files.",
                    "parameters": {"type": "object", "properties": {"path": path}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "workspace_read",
                    "description": "Read a UTF-8 workspace file.",
                    "parameters": {"type": "object", "properties": {"path": path}, "required": ["path"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "workspace_write",
                    "description": "Write a UTF-8 workspace file.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": path, "content": {"type": "string"}},
                        "required": ["path", "content"],
                    },
                },
            },
        ]

    def _recover_interrupted_jobs(self) -> None:
        for job in self.list_jobs():
            if job.get("status") not in {"queued", "running"}:
                continue
            path = self._job_dir(job["agent"], job["id"]) / "job.json"
            job["status"] = "failed"
            job["error"] = "Ulysses stopped before this job completed. Delegate it again to retry."
            job["updated_at"] = self._now()
            self._write_json(path, job)

    def _agent_dir(self, name: str) -> Path:
        return self._confined(self.root / name, self.root)

    def _job_dir(self, name: str, job_id: str) -> Path:
        if not re.fullmatch(r"job_[A-Za-z0-9_]+", job_id):
            raise ValueError("Invalid sub-agent job ID.")
        return self._confined(self._agent_dir(self._valid_name(name)) / "tasks" / job_id, self.root)

    @staticmethod
    def _valid_name(name: str) -> str:
        normalized = name.strip().lower().replace("-", "_")
        if not NAME_PATTERN.fullmatch(normalized):
            raise ValueError(
                "Sub-agent name must be 2-64 lowercase letters, numbers, or underscores and start with a letter."
            )
        return normalized

    @staticmethod
    def _confined(path: Path, root: Path) -> Path:
        resolved = path.resolve()
        root = root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("Path escapes the configured sub-agent workspace.")
        return resolved

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
