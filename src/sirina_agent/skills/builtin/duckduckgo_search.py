from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus, urlparse

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
        if not query:
            return SkillResult(False, "Search query cannot be empty.", {"results": []})

        errors: list[str] = []
        raw: list[dict[str, Any]] = []
        if _is_latest_linux_kernel_query(query):
            try:
                raw.extend(_search_kernel_org_latest())
            except Exception as exc:
                errors.append(f"_search_kernel_org_latest({query!r}): {exc}")
            else:
                results = normalize_results(raw, limit)
                lines = [f"{idx + 1}. {r['title']}\n{r['url']}\n{r['snippet']}" for idx, r in enumerate(results)]
                return SkillResult(True, "\n\n".join(lines), {"results": results, "errors": errors})
        searches = [
            ("_search_with_ddgs", _search_with_ddgs),
            ("_search_with_duckduckgo_search", _search_with_duckduckgo_search),
            ("_search_duckduckgo_html", _search_duckduckgo_html),
        ]
        for candidate_query in _candidate_queries(query):
            for search_name, search in searches:
                try:
                    provider_results = search(candidate_query, limit)
                except Exception as exc:
                    errors.append(f"{search_name}({candidate_query!r}): {exc}")
                    continue
                if provider_results:
                    raw.extend(provider_results)
                    break

        results = _rank_results(normalize_results(_dedupe_results(raw), limit * 4), query)[:limit]
        if not results:
            detail = "; ".join(errors)
            message = f"No search results found for: {query}"
            if detail:
                message += f"\nSearch errors: {detail}"
            return SkillResult(False, message, {"results": [], "errors": errors})

        lines = [f"{idx + 1}. {r['title']}\n{r['url']}\n{r['snippet']}" for idx, r in enumerate(results)]
        return SkillResult(True, "\n\n".join(lines), {"results": results, "errors": errors})


def _candidate_queries(query: str) -> list[str]:
    queries = [query]
    if _is_latest_linux_kernel_query(query):
        queries.extend(
            [
                "site:kernel.org latest stable linux kernel",
                "kernel.org latest stable linux kernel version",
            ]
        )
    return list(dict.fromkeys(queries))


def _is_latest_linux_kernel_query(query: str) -> bool:
    lowered = query.lower()
    return "latest" in lowered and "linux" in lowered and "kernel" in lowered


def _dedupe_results(raw_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_results:
        url = str(item.get("href") or item.get("url") or "").strip()
        title = str(item.get("title") or item.get("heading") or "").strip()
        key = url or title
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _rank_results(results: list[dict[str, str]], query: str) -> list[dict[str, str]]:
    lowered = query.lower()

    def score(result: dict[str, str]) -> tuple[int, str]:
        url = result["url"].lower()
        title = result["title"].lower()
        snippet = result["snippet"].lower()
        host = urlparse(url).netloc.lower()
        value = 0
        if _is_latest_linux_kernel_query(lowered):
            if host.endswith("kernel.org"):
                value += 100
            if "finger_banner" in url or "latest stable" in title or "latest stable" in snippet:
                value += 50
            if "wikipedia.org" in host:
                value -= 25
        return (-value, result["title"])

    return sorted(results, key=score)


def _search_with_ddgs(query: str, limit: int) -> list[dict[str, Any]]:
    from ddgs import DDGS  # type: ignore

    errors: list[str] = []
    results: list[dict[str, Any]] = []
    for backend in ("duckduckgo", "brave", "bing", "auto"):
        try:
            with DDGS(timeout=6) as ddgs:
                raw = list(ddgs.text(query, backend=backend, max_results=limit))
            if raw:
                results.extend(raw)
        except Exception as exc:
            errors.append(f"{backend}: {exc}")
    if results:
        return results
    raise RuntimeError("; ".join(errors) or "ddgs returned no results")


def _search_kernel_org_latest() -> list[dict[str, Any]]:
    import httpx

    response = httpx.get("https://www.kernel.org/finger_banner", follow_redirects=True, timeout=10)
    response.raise_for_status()
    text = " ".join(line.strip() for line in response.text.splitlines() if line.strip())
    return [{"title": "The latest stable Linux kernel from kernel.org", "href": "https://kernel.org/finger_banner", "body": text}]


def _search_with_duckduckgo_search(query: str, limit: int) -> list[dict[str, Any]]:
    from duckduckgo_search import DDGS  # type: ignore

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=limit))


def _search_duckduckgo_html(query: str, limit: int) -> list[dict[str, Any]]:
    import httpx
    from lxml import html  # type: ignore

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    response = httpx.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
        follow_redirects=True,
        timeout=15,
    )
    response.raise_for_status()
    document = html.fromstring(response.text)
    results: list[dict[str, Any]] = []
    for row in document.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' result ')]"
    )[:limit]:
        title_nodes = row.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), ' result__a ')]"
        )
        if not title_nodes:
            continue
        title = title_nodes[0].text_content().strip()
        href = title_nodes[0].get("href") or ""
        snippet_nodes = row.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), ' result__snippet ')]"
        )
        snippet = snippet_nodes[0].text_content().strip() if snippet_nodes else ""
        results.append({"title": title, "href": href, "body": snippet})
    return results
