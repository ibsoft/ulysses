from __future__ import annotations

import re
import warnings
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from ..base import SkillManifest, SkillResult


def normalize_results(raw_results: list[dict[str, Any]], limit: int = 5) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in raw_results:
        url = _normalize_result_url(str(item.get("href") or item.get("url") or "").strip())
        if not url:
            continue
        normalized.append(
            {
                "title": str(item.get("title") or item.get("heading") or "").strip(),
                "url": url,
                "snippet": str(item.get("body") or item.get("snippet") or "").strip(),
                "timestamp": str(item.get("date") or item.get("timestamp") or "").strip(),
            }
        )
        if len(normalized) >= limit:
            break
    return normalized


def _normalize_result_url(value: str) -> str:
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urlparse(value)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        redirected = parse_qs(parsed.query).get("uddg", [""])[0]
        value = unquote(redirected)
        parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value


class DuckDuckGoSearchSkill:
    manifest = SkillManifest(
        name="internet_search",
        description=(
            "Search the internet and return concise, ranked source results. Use queries for independent searches "
            "that should be executed together, such as research covering several domains."
        ),
        arguments_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "One search query."},
                "queries": {
                    "type": "array",
                    "description": "Up to six independent search queries to execute and group in one result.",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 6,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results returned for each query.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "additionalProperties": False,
        },
        required_permissions=["network"],
        risk_level="medium",
        enabled=True,
        activity_label="searching",
    )

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        queries = _requested_queries(arguments)
        if not queries:
            return SkillResult(False, "At least one non-empty search query is required.", {"results": []})
        try:
            limit = max(1, min(int(arguments.get("limit", 5)), 10))
        except (TypeError, ValueError):
            return SkillResult(False, "Search result limit must be an integer from 1 to 10.", {"results": []})

        errors: list[str] = []
        searches = [
            ("_search_duckduckgo_html", _search_duckduckgo_html),
            ("_search_with_ddgs", _search_with_ddgs),
            ("_search_with_duckduckgo_search", _search_with_duckduckgo_search),
        ]
        grouped_results: list[dict[str, Any]] = []
        for query in queries:
            raw: list[dict[str, Any]] = []
            if _is_latest_linux_kernel_query(query):
                try:
                    raw.extend(_search_kernel_org_latest())
                except Exception as exc:
                    errors.append(f"_search_kernel_org_latest({query!r}): {exc}")
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
                if len(_dedupe_results(raw)) >= limit:
                    break
            results = _rank_results(normalize_results(_dedupe_results(raw), limit * 8), query)[:limit]
            grouped_results.append({"query": query, "results": results})

        results = [{**result, "query": group["query"]} for group in grouped_results for result in group["results"]]
        if not results:
            message = f"No search results found for: {', '.join(queries)}"
            return SkillResult(
                False, message, {"queries": queries, "results": [], "groups": grouped_results, "errors": errors}
            )

        sections = []
        for group in grouped_results:
            if not group["results"]:
                continue
            lines = [
                f"{idx + 1}. {result['title']}\n{result['url']}\n{result['snippet']}"
                for idx, result in enumerate(group["results"])
            ]
            if len(queries) > 1:
                sections.append(f"Search: {group['query']}\n\n" + "\n\n".join(lines))
            else:
                sections.append("\n\n".join(lines))
        return SkillResult(
            True,
            "\n\n".join(sections),
            {"queries": queries, "results": results, "groups": grouped_results, "errors": errors},
        )


def _requested_queries(arguments: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    query = arguments.get("query")
    if query is not None:
        values.append(query)
    queries = arguments.get("queries")
    if isinstance(queries, list):
        values.extend(queries[:6])
    elif queries is not None:
        values.append(queries)
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return list(dict.fromkeys(cleaned))[:6]


def _candidate_queries(query: str) -> list[str]:
    queries: list[str] = []
    if _is_latest_linux_kernel_query(query):
        queries.extend(
            [
                "site:kernel.org latest stable linux kernel",
                "kernel.org latest stable linux kernel version",
            ]
        )
    lowered = query.lower()
    if any(term in lowered for term in ("subdomain", "dns", "ip address", "hostnames")):
        domains = list(dict.fromkeys(re.findall(r"(?<![@\w.-])(?:[a-z0-9-]+\.)+[a-z]{2,63}\b", lowered)))
        for domain in domains[:4]:
            queries.extend(
                [
                    f"site:{domain} -www",
                    f'"{domain}" subdomains DNS',
                    f'site:crt.sh "{domain}"',
                ]
            )
    queries.append(query)
    return list(dict.fromkeys(queries))[:12]


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
    terms = set(re.findall(r"[a-z0-9][a-z0-9.-]{2,}", lowered))

    def score(result: dict[str, str]) -> tuple[int, str]:
        url = result["url"].lower()
        title = result["title"].lower()
        snippet = result["snippet"].lower()
        host = urlparse(url).netloc.lower()
        value = sum(5 for term in terms if term in title)
        value += sum(3 for term in terms if term in url)
        value += sum(1 for term in terms if term in snippet)
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
    for backend in ("duckduckgo", "brave", "bing", "auto"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                warnings.simplefilter("ignore", RuntimeWarning)
                with DDGS(timeout=6) as ddgs:
                    raw = list(ddgs.text(query, backend=backend, max_results=limit))
            if raw:
                return raw
        except Exception as exc:
            errors.append(f"{backend}: {exc}")
    raise RuntimeError("; ".join(errors) or "ddgs returned no results")


def _search_kernel_org_latest() -> list[dict[str, Any]]:
    import httpx

    response = httpx.get("https://www.kernel.org/finger_banner", follow_redirects=True, timeout=10)
    response.raise_for_status()
    text = " ".join(line.strip() for line in response.text.splitlines() if line.strip())
    return [
        {
            "title": "The latest stable Linux kernel from kernel.org",
            "href": "https://kernel.org/finger_banner",
            "body": text,
        }
    ]


def _search_with_duckduckgo_search(query: str, limit: int) -> list[dict[str, Any]]:
    from duckduckgo_search import DDGS  # type: ignore

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        warnings.simplefilter("ignore", RuntimeWarning)
        with DDGS(timeout=6) as ddgs:
            return list(ddgs.text(query, max_results=limit))


def _search_duckduckgo_html(query: str, limit: int) -> list[dict[str, Any]]:
    import httpx
    from lxml import html  # type: ignore

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    response = httpx.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
        follow_redirects=True,
        timeout=15,
    )
    response.raise_for_status()
    document = html.fromstring(response.text)
    results: list[dict[str, Any]] = []
    for row in document.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' result ')]")[:limit]:
        title_nodes = row.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' result__a ')]")
        if not title_nodes:
            continue
        title = title_nodes[0].text_content().strip()
        href = title_nodes[0].get("href") or ""
        snippet_nodes = row.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' result__snippet ')]")
        snippet = snippet_nodes[0].text_content().strip() if snippet_nodes else ""
        results.append({"title": title, "href": href, "body": snippet})
    return results
