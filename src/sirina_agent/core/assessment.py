from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class AssessmentCheck:
    id: str
    category: str
    command: str


@dataclass(frozen=True)
class AssessmentResult:
    check: AssessmentCheck
    output: str
    ok: bool


@dataclass(frozen=True)
class AssessmentFinding:
    severity: str
    title: str
    evidence: str
    remediation: str


TOOL_PACKAGES = {
    "curl": "curl",
    "dig": "dnsutils",
    "nmap": "nmap",
    "whatweb": "whatweb",
    "sslscan": "sslscan",
    "nikto": "nikto",
    "nuclei": "nuclei",
}


def assessment_checks(target: str, preferred_command: str | None = None) -> list[AssessmentCheck]:
    host = normalized_host(target)
    https_target = f"https://{host}"
    checks = [
        AssessmentCheck("dns", "Discovery", f"dig +short {host}"),
        AssessmentCheck("http-headers", "HTTP", f"curl -sS -L --max-time 30 -D - -o /dev/null {https_target}"),
        AssessmentCheck("service-scan", "Network", f"nmap -sT -sV --version-light -Pn --open --top-ports 1000 {host}"),
        AssessmentCheck("web-fingerprint", "Web", f"whatweb --no-errors {https_target}"),
        AssessmentCheck("tls", "TLS", f"sslscan --no-colour {host}:443"),
        AssessmentCheck("web-misconfiguration", "Web", f"nikto -host {https_target} -nointeractive"),
        AssessmentCheck(
            "template-scan",
            "Web",
            f"nuclei -u {https_target} -severity info,low,medium,high,critical -jsonl -silent -no-interactsh",
        ),
    ]
    if preferred_command and preferred_command not in {check.command for check in checks}:
        checks.append(AssessmentCheck("requested-check", "Requested", preferred_command))
    return checks


def normalized_host(target: str) -> str:
    value = target.strip().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
    return value.strip(".,;:")


def missing_tool_packages(results: list[AssessmentResult]) -> list[str]:
    packages = set()
    for result in results:
        if "command not found:" not in result.output.lower():
            continue
        argv = shlex.split(result.check.command)
        if argv and argv[0] in TOOL_PACKAGES:
            packages.add(TOOL_PACKAGES[argv[0]])
    return sorted(packages)


def missing_tool_installer_script() -> str:
    return '''#!/usr/bin/env python3
import subprocess
import sys


failed = []
for package in sys.argv[1:]:
    print(f"Installing {package}...", flush=True)
    completed = subprocess.run(["apt-get", "install", "-y", package], check=False)
    if completed.returncode:
        failed.append(package)

if failed:
    print("Packages not installed: " + ", ".join(failed), file=sys.stderr)
    raise SystemExit(1)
'''


def render_assessment_report(target: str, results: list[AssessmentResult]) -> str:
    host = normalized_host(target)
    issued_at = datetime.now(UTC)
    report_id = f"UVA-{issued_at.strftime('%Y%m%d')}-{re.sub(r'[^A-Za-z0-9]+', '-', host).strip('-').upper()[:32]}"
    completed_results = [result for result in results if _is_customer_reportable_result(result)]
    findings: list[AssessmentFinding] = []
    for result in completed_results:
        findings.extend(_findings_for(result))
    findings = _deduplicate_findings(findings)
    counts = _severity_counts(findings)
    overall_risk = _overall_risk(findings)
    assessed_areas = sorted({result.check.category for result in completed_results})
    assessed_text = ", ".join(assessed_areas) if assessed_areas else "the agreed external assessment scope"
    substantive_findings = sum(counts[level] for level in ("Critical", "High", "Medium", "Low"))

    if substantive_findings:
        executive_conclusion = (
            f"The assessment identified **{substantive_findings} security finding(s)** requiring corrective action. "
            f"The overall observed risk rating is **{overall_risk}**, driven by the highest-rated validated observation."
        )
    else:
        executive_conclusion = (
            "No Critical, High, Medium, or Low severity vulnerability was confirmed within the tested scope and assessment window. "
            "Informational observations, where present, should be considered as part of routine security hardening."
        )

    management_summary = _management_summary(counts, overall_risk)
    technical_summary = (
        f"Testing covered {assessed_text}. The technical review produced {len(findings)} documented observation(s): "
        f"{counts['Critical']} Critical, {counts['High']} High, {counts['Medium']} Medium, {counts['Low']} Low, and "
        f"{counts['Informational']} Informational. Findings are evidence-led and limited to conditions observed against `{host}` "
        "during the stated assessment window."
    )

    risk_rows = "\n".join(
        f"| {severity} | {counts[severity]} | {_risk_disposition(severity)} |"
        for severity in ("Critical", "High", "Medium", "Low", "Informational")
    )
    finding_rows = []
    detail_sections = []
    for index, finding in enumerate(findings, 1):
        finding_id = f"UVA-{index:03d}"
        severity = _normalized_severity(finding.severity)
        finding_rows.append(
            f"| {finding_id} | {severity} | {_table_cell(finding.title)} | `{host}` | {_table_cell(finding.remediation, 140)} |"
        )
        detail_sections.append(
            f"### {finding_id} - {finding.title}\n\n"
            f"| Attribute | Detail |\n"
            f"| --- | --- |\n"
            f"| Severity | **{severity}** |\n"
            f"| Affected Asset | `{host}` |\n"
            f"| Evidence Confidence | {_confidence_for(finding)} |\n\n"
            "**Description**\n\n"
            f"{_description_for(finding)}\n\n"
            "**Technical Evidence**\n\n"
            f"{finding.evidence}\n\n"
            "**Business Impact**\n\n"
            f"{_business_impact_for(finding)}\n\n"
            "**Technical Impact**\n\n"
            f"{_impact_for(finding)}\n\n"
            "**Recommendation**\n\n"
            f"{finding.remediation}\n\n"
            "**Retest Criteria**\n\n"
            f"{_verification_for(finding)}"
        )

    if not finding_rows:
        finding_rows.append(
            f"| - | Informational | No reportable vulnerability confirmed | `{host}` | Maintain the current patching and review cycle. |"
        )
    details_text = "\n\n".join(detail_sections) or (
        "No detailed vulnerability finding was confirmed. This conclusion applies only to the defined scope, test method, and assessment window."
    )
    finding_register_text = "\n".join(finding_rows)
    evidence_sections = [
        f"### {_customer_check_name(result.check)}\n\n"
        f"**Assessment Area:** {result.check.category}\n\n"
        f"```text\n{_customer_evidence_excerpt(result.output)}\n```"
        for result in completed_results
        if result.output.strip()
    ]
    evidence_text = "\n\n".join(evidence_sections) or "No supplementary evidence excerpt is included in this report."
    return (
        f"# External Vulnerability Assessment Report\n\n"
        f"## {host}\n\n"
        "**Classification: Confidential - Customer Delivery**\n\n"
        "## Document Control\n\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| Report Reference | `{report_id}` |\n"
        f"| Assessment Target | `{host}` |\n"
        f"| Assessment Type | External, unauthenticated vulnerability assessment |\n"
        f"| Report Status | Final |\n"
        f"| Issue Date | {issued_at.strftime('%d %B %Y')} |\n"
        "| Prepared By | Ulysses Cyber Sentinel |\n"
        "| Distribution | Authorized customer stakeholders only |\n\n"
        "## Executive Summary\n\n"
        f"The objective of this engagement was to identify externally observable security weaknesses affecting `{host}` and "
        "provide prioritized, actionable remediation guidance. "
        f"{executive_conclusion}\n\n"
        "## Management Summary\n\n"
        f"{management_summary}\n\n"
        "## Technical Summary\n\n"
        f"{technical_summary}\n\n"
        "### Risk Profile\n\n"
        "| Severity | Count | Management Treatment |\n"
        "| --- | ---: | --- |\n"
        f"{risk_rows}\n\n"
        "## Scope and Engagement Profile\n\n"
        f"- **In-scope asset:** `{host}`\n"
        "- **Assessment perspective:** External and unauthenticated\n"
        "- **Testing approach:** Non-destructive vulnerability identification and configuration review\n"
        "- **Scope boundary:** The named host and services directly exposed by that host\n"
        "- **Assessment window:** Point-in-time review ending on the report issue date\n\n"
        "## Methodology\n\n"
        "The engagement used a risk-based methodology informed by OWASP Web Security Testing Guide principles and NIST SP 800-115. "
        "Activities included external asset resolution, HTTP response and security-control review, exposed-service identification, "
        "light service-version analysis, web technology fingerprinting, TLS configuration review, web-server configuration testing, "
        "and signature-based vulnerability checks. Potential findings were correlated with the collected technical evidence before inclusion.\n\n"
        "## Severity Rating Method\n\n"
        "| Rating | Definition |\n"
        "| --- | --- |\n"
        "| Critical | Immediate and material risk of system compromise, sensitive-data exposure, or severe service impact. |\n"
        "| High | Significant weakness with a credible exploitation path and substantial business impact. |\n"
        "| Medium | Security weakness requiring remediation but generally dependent on conditions or additional attack steps. |\n"
        "| Low | Defense-in-depth weakness with limited direct impact. |\n"
        "| Informational | Security-relevant observation or hardening opportunity without a demonstrated direct exploit. |\n\n"
        "## Findings Register\n\n"
        "| ID | Severity | Finding | Affected Asset | Recommended Action |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"{finding_register_text}\n\n"
        "## Detailed Findings\n\n"
        f"{details_text}\n\n"
        "## Remediation Roadmap\n\n"
        "1. **Immediate (0-7 days):** Contain and remediate Critical and High findings; validate exposure after change.\n"
        "2. **Near term (up to 30 days):** Address Medium findings and review externally exposed services for business necessity.\n"
        "3. **Planned improvement (up to 90 days):** Complete Low-severity hardening and incorporate Informational observations into standards.\n"
        "4. **Ongoing:** Maintain patch governance, certificate lifecycle management, attack-surface monitoring, and periodic reassessment.\n\n"
        "## Retest and Closure\n\n"
        "A focused retest should be performed after remediation. Closure requires evidence that each reported condition is no longer observable, "
        "that the corrective control is operating as intended, and that remediation has not introduced a regression. Retest results should reference "
        "the finding identifiers in this report.\n\n"
        "## Technical Evidence Appendix\n\n"
        f"{evidence_text}\n\n"
        "## Assumptions and Limitations\n\n"
        "This engagement was external, unauthenticated, non-destructive, and limited to the named asset. Results represent a point-in-time view. "
        "Authenticated authorization controls, tenant isolation, business logic, source code, internal network paths, and user-role-specific behavior "
        "were outside this assessment profile unless separately commissioned. The absence of a reported finding does not provide assurance that no "
        "other vulnerability exists outside the tested scope or assessment window.\n\n"
        "## Confidentiality Notice\n\n"
        "This document contains security-sensitive information intended solely for authorized customer stakeholders. Distribution should be controlled "
        "in accordance with the customer's information-classification and incident-management requirements.\n"
    )


def _normalized_severity(severity: str) -> str:
    normalized = severity.strip().title()
    if normalized in {"Info", "Information", "Informational"}:
        return "Informational"
    if normalized in {"Critical", "High", "Medium", "Low"}:
        return normalized
    return "Informational"


def _severity_counts(findings: list[AssessmentFinding]) -> dict[str, int]:
    counts = {severity: 0 for severity in ("Critical", "High", "Medium", "Low", "Informational")}
    for finding in findings:
        counts[_normalized_severity(finding.severity)] += 1
    return counts


def _overall_risk(findings: list[AssessmentFinding]) -> str:
    present = {_normalized_severity(finding.severity) for finding in findings}
    for severity in ("Critical", "High", "Medium", "Low", "Informational"):
        if severity in present:
            return severity
    return "No material finding confirmed"


def _management_summary(counts: dict[str, int], overall_risk: str) -> str:
    if counts["Critical"] or counts["High"]:
        return (
            f"The observed security posture is rated **{overall_risk} risk**. Executive ownership is required for immediate remediation, "
            "with accountable action owners, target dates, and verification evidence assigned to each Critical or High finding. "
            "Exposure-reduction measures should be prioritized while permanent corrective controls are implemented."
        )
    if counts["Medium"]:
        return (
            f"The observed security posture is rated **{overall_risk} risk**. No Critical or High issue was confirmed; however, Medium-severity "
            "conditions require a tracked remediation plan. Management should assign ownership, incorporate the work into the next security "
            "maintenance cycle, and require independent retest before closure."
        )
    if counts["Low"]:
        return (
            "No Critical, High, or Medium issue was confirmed. The observed findings are primarily defense-in-depth improvements. Management "
            "should address them through normal security hardening and change-management processes and maintain periodic external reassessment."
        )
    return (
        "No material vulnerability was confirmed in the defined assessment profile. Management should retain the current security governance, "
        "patching, monitoring, and reassessment cycle because this conclusion is limited to the named asset and point-in-time test window."
    )


def _risk_disposition(severity: str) -> str:
    return {
        "Critical": "Executive escalation and immediate containment/remediation.",
        "High": "Priority remediation with near-term executive oversight.",
        "Medium": "Tracked remediation within the next security maintenance cycle.",
        "Low": "Planned defense-in-depth improvement.",
        "Informational": "Review and incorporate into security standards where appropriate.",
    }[severity]


def _confidence_for(finding: AssessmentFinding) -> str:
    lowered = f"{finding.title} {finding.evidence}".lower()
    if "nikto" in lowered or "nuclei" in lowered or "reported" in lowered:
        return "Moderate - scanner observation requiring remediation validation"
    return "High - directly observed configuration or exposure"


def _description_for(finding: AssessmentFinding) -> str:
    return (
        f"The assessment identified **{finding.title}** on the in-scope external asset. "
        "The condition was documented from the observed response or exposed service behavior and rated according to its plausible security impact."
    )


def _business_impact_for(finding: AssessmentFinding) -> str:
    severity = _normalized_severity(finding.severity)
    if severity in {"Critical", "High"}:
        return "The condition may expose the organization to material operational disruption, unauthorized access, sensitive-data compromise, or regulatory impact."
    if severity == "Medium":
        return "The condition increases the likelihood or impact of a successful attack and may require additional controls or attack steps to exploit."
    if severity == "Low":
        return "The condition weakens defense in depth and may increase attack effectiveness when combined with another weakness."
    return "No direct business compromise was demonstrated; the observation is relevant to security hygiene and control maturity."


def _verification_for(finding: AssessmentFinding) -> str:
    return (
        "Repeat the relevant external test after remediation and confirm that the reported condition and associated evidence are no longer observable. "
        "Retain configuration or change evidence with the retest record."
    )


def _customer_check_name(check: AssessmentCheck) -> str:
    names = {
        "dns": "External Asset Resolution",
        "http-headers": "HTTP Security Control Review",
        "service-scan": "Exposed Service Review",
        "web-fingerprint": "Web Technology Identification",
        "tls": "TLS Configuration Review",
        "web-misconfiguration": "Web Server Configuration Review",
        "template-scan": "Vulnerability Signature Review",
        "requested-check": "Commissioned Supplemental Review",
    }
    return names.get(check.id, check.id.replace("-", " ").title())


def _customer_evidence_excerpt(output: str, max_chars: int = 2500) -> str:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", output).strip()
    if not cleaned:
        return "No supplementary excerpt recorded."
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "\n...[evidence excerpt truncated]"


def _result_status(result: AssessmentResult) -> str:
    lowered = result.output.lower()
    if "sudo password" in lowered or "confirmation token" in lowered:
        return "Awaiting approval"
    if "command not found:" in lowered:
        return "Tool unavailable"
    if "not in the allowlist" in lowered or "not allowed" in lowered or "requires confirmation" in lowered:
        return "Blocked"
    if "timed out" in lowered:
        return "Timed out"
    if not result.ok:
        return "Failed"
    return "Completed"


def _is_customer_reportable_result(result: AssessmentResult) -> bool:
    if _result_status(result) != "Completed":
        return False
    if result.check.category == "Recovery" or result.check.id == "install-missing-tools":
        return False
    lowered = result.output.lower()
    operational_markers = (
        "confirmation token",
        "enter sudo password",
        "installing package",
        "packages not installed",
        "not in the allowlist",
        "command not found:",
    )
    return not any(marker in lowered for marker in operational_markers)


def _findings_for(result: AssessmentResult) -> list[AssessmentFinding]:
    if not result.ok:
        return []
    output = result.output
    if result.check.id == "http-headers":
        return _header_findings(output)
    if result.check.id == "service-scan":
        return _nmap_findings(output)
    if result.check.id == "tls":
        return _tls_findings(output)
    if result.check.id == "web-misconfiguration":
        return _nikto_findings(output)
    if result.check.id == "template-scan":
        return _nuclei_findings(output)
    return []


def _header_findings(output: str) -> list[AssessmentFinding]:
    response_blocks = re.split(r"(?=HTTP/\d(?:\.\d)?\s+\d{3})", output, flags=re.IGNORECASE)
    final_response = next((block for block in reversed(response_blocks) if block.lstrip().upper().startswith("HTTP/")), output)
    headers = {match.group(1).lower() for match in re.finditer(r"(?im)^([a-z0-9-]+)\s*:", final_response)}
    expected = {
        "strict-transport-security": ("Low", "HSTS header not observed", "Enable HSTS with an appropriate max-age after validating HTTPS coverage."),
        "content-security-policy": ("Low", "Content Security Policy header not observed", "Deploy a restrictive CSP and remove unsafe directives where practical."),
        "x-content-type-options": ("Low", "X-Content-Type-Options header not observed", "Set X-Content-Type-Options: nosniff."),
        "x-frame-options": ("Low", "Clickjacking protection header not observed", "Set frame-ancestors in CSP or X-Frame-Options as a compatibility control."),
        "referrer-policy": ("Informational", "Referrer-Policy header not observed", "Set a Referrer-Policy appropriate for the application."),
        "permissions-policy": ("Informational", "Permissions-Policy header not observed", "Disable browser capabilities the application does not use."),
    }
    return [
        AssessmentFinding(severity, title, f"Header `{name}` was absent from the final response headers.", remediation)
        for name, (severity, title, remediation) in expected.items()
        if headers and name not in headers
    ]


def _nmap_findings(output: str) -> list[AssessmentFinding]:
    findings = []
    sensitive = {21: "FTP", 23: "Telnet", 445: "SMB", 1433: "MSSQL", 3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL", 6379: "Redis", 9200: "Elasticsearch", 27017: "MongoDB"}
    for match in re.finditer(r"(?m)^(\d+)/tcp\s+open\s+([^\s]+)(?:\s+(.+))?$", output):
        port = int(match.group(1))
        service = match.group(2)
        detail = (match.group(3) or "").strip()
        if port in sensitive:
            findings.append(
                AssessmentFinding(
                    "Medium",
                    f"Potentially sensitive service exposed: {sensitive[port]} on TCP/{port}",
                    f"Nmap reported `{port}/tcp open {service} {detail}`.",
                    "Confirm business need, restrict source networks, require strong authentication, and patch the service.",
                )
            )
    return findings


def _tls_findings(output: str) -> list[AssessmentFinding]:
    findings = []
    lowered = output.lower()
    if re.search(r"ssl(?:v2|v3)\s+enabled|tlsv1\.0\s+enabled|tlsv1\.1\s+enabled", lowered):
        findings.append(AssessmentFinding("Medium", "Legacy TLS protocol enabled", "TLS scan reported an obsolete protocol as enabled.", "Disable SSLv2, SSLv3, TLS 1.0, and TLS 1.1; retain TLS 1.2/1.3."))
    if "heartbleed" in lowered and re.search(r"heartbleed[^\n]*(vulnerable|yes)", lowered):
        findings.append(AssessmentFinding("Critical", "TLS Heartbleed vulnerability reported", "TLS scanner reported the endpoint as vulnerable to Heartbleed.", "Patch the TLS library immediately, replace keys and certificates, and rotate potentially exposed secrets."))
    return findings


def _nikto_findings(output: str) -> list[AssessmentFinding]:
    findings = []
    for line in output.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped.startswith("+") or any(term in lowered for term in ("target ip", "target hostname", "start time", "end time", "requests:")):
            continue
        if any(term in lowered for term in ("vulnerable", "cve-", "osvdb-", "allowed http methods", "directory indexing")):
            severity = "Medium" if any(term in lowered for term in ("vulnerable", "cve-", "directory indexing")) else "Low"
            findings.append(AssessmentFinding(severity, "Nikto security observation", stripped.lstrip("+ "), "Validate the scanner observation manually, then patch or harden the affected component."))
    return findings


def _nuclei_findings(output: str) -> list[AssessmentFinding]:
    findings = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        info = item.get("info") or {}
        severity = str(info.get("severity") or item.get("severity") or "informational").title()
        title = str(info.get("name") or item.get("template-id") or "Nuclei template match")
        matched = str(item.get("matched-at") or item.get("host") or "Target matched template")
        findings.append(AssessmentFinding(severity, title, f"Nuclei matched `{matched}`.", "Review the template evidence, validate the condition, and remediate the affected component."))
    return findings


def _deduplicate_findings(findings: list[AssessmentFinding]) -> list[AssessmentFinding]:
    unique = {}
    for finding in findings:
        unique[(finding.severity, finding.title, finding.evidence)] = finding
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Informational": 4, "Info": 4}
    return sorted(unique.values(), key=lambda item: (order.get(item.severity, 5), item.title))


def _impact_for(finding: AssessmentFinding) -> str:
    if finding.severity in {"Critical", "High"}:
        return "Successful exploitation may materially compromise confidentiality, integrity, or availability."
    if finding.severity == "Medium":
        return "The condition increases attack surface or may enable compromise under plausible circumstances."
    if finding.severity == "Low":
        return "The condition weakens defense in depth and may assist another attack."
    return "This is a hardening or visibility observation with no direct exploit confirmed."


def _table_cell(text: str, max_chars: int = 180) -> str:
    cleaned = " ".join(text.strip().split()).replace("|", "\\|")
    return cleaned if len(cleaned) <= max_chars else cleaned[: max_chars - 3].rstrip() + "..."
