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

from .capabilities import SubagentCapabilityError, SubagentSkillBroker

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class SubagentManager:
    def __init__(self, config, provider_factory: Callable[[], Any], skill_registry=None, logger=None) -> None:
        self.config = config
        self.root = Path(config.root_dir).expanduser().resolve()
        self.provider_factory = provider_factory
        self.capabilities = SubagentSkillBroker(config, skill_registry, logger)
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(config.max_concurrent_jobs)),
            thread_name_prefix="ulysses-subagent",
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted_jobs()

    def reconfigure(self, config) -> None:
        self.config = config
        self.capabilities.config = config

    def create(
        self,
        name: str,
        purpose: str,
        prompt: str,
        allowed_skills: list[str] | None = None,
    ) -> dict[str, Any]:
        name = self._valid_name(name)
        purpose = purpose.strip()
        prompt = prompt.strip()
        if not purpose or not prompt:
            raise ValueError("Sub-agent purpose and prompt are required.")
        grants = self.capabilities.validate_grants(allowed_skills or [])
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
            record = {
                "name": name,
                "purpose": purpose,
                "allowed_skills": grants,
                "created_at": now,
                "updated_at": now,
            }
            self._write_json(agent_dir / "agent.json", record)
            (agent_dir / "prompt.md").write_text(prompt + "\n", encoding="utf-8")
            return record

    def update(
        self,
        name: str,
        purpose: str | None = None,
        prompt: str | None = None,
        allowed_skills: list[str] | None = None,
    ) -> dict[str, Any]:
        name = self._valid_name(name)
        agent_dir = self._agent_dir(name)
        path = agent_dir / "agent.json"
        if not path.is_file():
            raise KeyError(f"Sub-agent `{name}` does not exist.")
        grants = None if allowed_skills is None else self.capabilities.validate_grants(allowed_skills)
        with self._lock:
            record = self._read_json(path)
            if purpose is not None:
                if not purpose.strip():
                    raise ValueError("Sub-agent purpose cannot be empty.")
                record["purpose"] = purpose.strip()
            if prompt is not None:
                if not prompt.strip():
                    raise ValueError("Sub-agent prompt cannot be empty.")
                (agent_dir / "prompt.md").write_text(prompt.strip() + "\n", encoding="utf-8")
            if grants is not None:
                record["allowed_skills"] = grants
            record["updated_at"] = self._now()
            self._write_json(path, record)
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

    def delegate(
        self,
        name: str,
        task: str,
        context: str = "",
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        name = self._valid_name(name)
        task = task.strip()
        if not task:
            raise ValueError("A sub-agent task is required.")
        agent_dir = self._agent_dir(name)
        if not (agent_dir / "agent.json").is_file():
            raise KeyError(f"Sub-agent `{name}` does not exist.")
        agent = self._read_json(agent_dir / "agent.json")
        agent_skills = list(agent.get("allowed_skills") or [])
        requested_skills = agent_skills if skills is None else skills
        grants = self.capabilities.validate_grants(requested_skills)
        outside_agent_policy = sorted(set(grants) - set(agent_skills))
        if outside_agent_policy:
            raise ValueError("Job requested skills outside the sub-agent policy: " + ", ".join(outside_agent_policy))
        job_id = f"job_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
        job_dir = self._confined(agent_dir / "tasks" / job_id, agent_dir / "tasks")
        job_dir.mkdir(parents=True)
        job = {
            "id": job_id,
            "agent": name,
            "task": task,
            "context": context.strip(),
            "granted_skills": grants,
            "skill_calls": 0,
            "active_skill": None,
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
            grants = list(job.get("granted_skills") or [])
            if grants:
                lines.append(f"  Skills: {', '.join(grants)}")
            if job.get("active_skill"):
                lines.append(f"  Using: {job['active_skill']}")
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
                    "run shell commands, approve confirmations, or access files outside your workspace. Use workspace tools and "
                    "only the explicitly delegated Ulysses skills when useful."
                ),
            },
            {
                "role": "user",
                "content": f"Assigned by Ulysses:\n{job['task']}\n\nParent context:\n{job.get('context') or 'None supplied.'}",
            },
        ]
        granted_skills = list(job.get("granted_skills") or [])
        tools = [*self._workspace_schemas(), *self.capabilities.schemas(granted_skills)]
        skill_calls = int(job.get("skill_calls") or 0)
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
                tool_name = str(function.get("name") or "")
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if tool_name in SubagentSkillBroker.WORKSPACE_TOOLS:
                        result = self._workspace_tool(workspace, tool_name, arguments)
                    else:
                        if skill_calls >= int(self.config.max_skill_calls_per_job):
                            raise SubagentCapabilityError("The delegated skill-call limit has been reached.")
                        skill_calls += 1
                        job["skill_calls"] = skill_calls
                        self._set_active_skill(job, tool_name)
                        try:
                            result, event = self.capabilities.execute(
                                tool_name,
                                arguments,
                                agent=name,
                                job_id=job["id"],
                                granted_skills=granted_skills,
                            )
                        finally:
                            self._set_active_skill(job, None)
                        self._record_skill_call(name, job["id"], event)
                        job["updated_at"] = self._now()
                        self._write_json(self._job_dir(name, job["id"]) / "job.json", job)
                except Exception as exc:  # noqa: BLE001 - return tool errors to the sub-agent for recovery
                    result = f"Sub-agent tool failed: {exc}"
                    if tool_name not in SubagentSkillBroker.WORKSPACE_TOOLS:
                        self._record_skill_call(
                            name,
                            job["id"],
                            {"skill": tool_name, "ok": False, "error": str(exc)[:500]},
                        )
                messages.append({"role": "tool", "tool_call_id": call_id, "name": tool_name, "content": result})
        raise RuntimeError("Sub-agent exceeded its tool-round limit.")

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

    def _record_skill_call(self, agent: str, job_id: str, event: dict[str, Any]) -> None:
        record = {"timestamp": self._now(), **event}
        path = self._job_dir(agent, job_id) / "skill-calls.jsonl"
        with self._lock, path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")

    def _set_active_skill(self, job: dict[str, Any], skill: str | None) -> None:
        job["active_skill"] = skill
        job["updated_at"] = self._now()
        self._write_json(self._job_dir(job["agent"], job["id"]) / "job.json", job)

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
