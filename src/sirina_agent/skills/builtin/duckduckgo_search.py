from __future__ import annotations

from typing import Any

from ..base import SkillManifest, SkillResult


def normalize_results(raw_results: list[dict[str, Any]], limit: int = 5) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in raw_results[:limit]:
        normalized.append(
            {
                "title": str(item.get("title") or item.get("heading") or "").strip(),
                "url": str(item.get("href") or item.get("url") or "").strip(),
                "snippet": str(item.get("body") or item.get("snippet") or "").strip(),
                "timestamp": str(item.get("date") or item.get("timestamp") or "").strip(),
            }
        )
    return normalized


class DuckDuckGoSearchSkill:
    manifest = SkillManifest(
        name="internet_search",
        description="Search the internet using DuckDuckGo and return concise source results.",
        arguments_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": ["query"],
        },
        required_permissions=["network"],
        risk_level="medium",
        enabled=True,
    )

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        query = str(arguments["query"]).strip()
        limit = int(arguments.get("limit", 5))
        try:
            from duckduckgo_search import DDGS  # type: ignore

            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=limit))
            results = normalize_results(raw, limit)
            lines = [f"{idx + 1}. {r['title']}\n{r['url']}\n{r['snippet']}" for idx, r in enumerate(results)]
            return SkillResult(True, "\n\n".join(lines), {"results": results})
        except Exception as exc:
            return SkillResult(False, f"DuckDuckGo search failed: {exc}", {"error": str(exc)})
