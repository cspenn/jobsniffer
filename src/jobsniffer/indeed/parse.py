"""jobsniffer.indeed.parse

Pure parsing functions for Indeed's two-step HTML flow (see
docs/2026-07-27-jobsniffer-modernization-plan.md, Phase 3a):

1. Search: GET /jobs?... returns an HTML page with job identity, a
   *truncated* description snippet, and an estimated salary, embedded as
   window.mosaic.providerData["mosaic-provider-jobcards"] = {...} --
   confirmed against tests/fixtures/indeed.jsonl: the page carries 40
   jobs with `snippet` (truncated) and `salarySnippet`/`extractedSalary`
   (estimates), but exactly one `sanitizedJobDescription` -- the job open
   in the detail pane, not all 40.
2. Detail: GET /viewjob?jk={jobkey}&from=vjs returns JSON with the full
   `sanitizedJobDescription` and an authoritative `salaryInfoModel`.

Neither step alone has both identity and full content -- this module has
no HTTP dependency; jobsniffer.indeed.search/detail call it after making
the actual request.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from jobsniffer.model import Compensation, CompensationInterval, JobType
from jobsniffer.util import get_enum_from_job_type

_INTERVAL_BY_SALARY_TYPE = {
    "YEARLY": CompensationInterval.YEARLY,
    "MONTHLY": CompensationInterval.MONTHLY,
    "WEEKLY": CompensationInterval.WEEKLY,
    "DAILY": CompensationInterval.DAILY,
    "HOURLY": CompensationInterval.HOURLY,
}


def extract_provider_data(html: str, provider_key: str) -> dict | None:
    """Extracts and parses the JSON object assigned to
    window.mosaic.providerData["<provider_key>"] in an Indeed search page.

    Returns None if the marker isn't present -- the page didn't render
    that provider (blocked, CAPTCHA, markup change, or a provider that
    genuinely has no data on this page), which callers must treat as "no
    data available", not synthesize a fabricated empty result for (P8).
    """
    marker = f'window.mosaic.providerData["{provider_key}"]='
    idx = html.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    json_str = _extract_balanced_json_object(html, start)
    return json.loads(json_str)


def _extract_balanced_json_object(text: str, start_idx: int) -> str:
    """Scans forward from start_idx (which must index the object's
    opening '{') respecting JSON string escaping, so braces inside string
    values (e.g. inside an HTML snippet) don't miscount. Returns the
    substring up to and including the matching closing brace.
    """
    depth = 0
    in_string = False
    escape = False
    for i in range(start_idx, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start_idx : i + 1]
    raise ValueError(f"Unbalanced JSON object starting at index {start_idx}")


def parse_search_results(html: str) -> list[dict]:
    """Returns the raw per-job dicts from mosaic-provider-jobcards's
    `results` list, or [] if the provider wasn't present on the page."""
    data = extract_provider_data(html, "mosaic-provider-jobcards")
    if data is None:
        return []
    model = data.get("metaData", {}).get("mosaicProviderJobCardsModel", {})
    return model.get("results", [])


def job_types_from_taxonomy(taxonomy_attributes: list[dict] | None) -> list[JobType]:
    """Search results carry job type under
    taxonomyAttributes[i].attributes[j].label where the group's own
    `label` is "job-types" (e.g. "Full-time", "Part-time")."""
    if not taxonomy_attributes:
        return []
    job_types: list[JobType] = []
    for group in taxonomy_attributes:
        if group.get("label") != "job-types":
            continue
        for attribute in group.get("attributes", []):
            job_type = get_enum_from_job_type(
                attribute["label"].replace("-", "").replace(" ", "").lower()
            )
            if job_type:
                job_types.append(job_type)
    return job_types


def compensation_from_extracted_salary(job: dict) -> Compensation | None:
    """Search-result-level salary: an ESTIMATE (source: EXTRACTION), used
    only until the detail fetch's authoritative salaryInfoModel replaces
    it -- see compensation_from_salary_info_model."""
    extracted = job.get("extractedSalary")
    if not extracted:
        return None
    min_amount = extracted.get("min")
    max_amount = extracted.get("max")
    if min_amount is None and max_amount is None:
        return None
    interval = _INTERVAL_BY_SALARY_TYPE.get(extracted.get("type", ""))
    currency = job.get("salarySnippet", {}).get("currency", "USD")
    return Compensation(
        interval=interval, min_amount=min_amount, max_amount=max_amount, currency=currency
    )


def compensation_from_salary_info_model(salary_info: dict | None) -> Compensation | None:
    """Detail-fetch salary from /viewjob's salaryInfoModel -- the
    authoritative figure that should replace any search-result estimate."""
    if not salary_info:
        return None
    min_amount = salary_info.get("salaryMin")
    max_amount = salary_info.get("salaryMax")
    if min_amount is None and max_amount is None:
        return None
    interval = _INTERVAL_BY_SALARY_TYPE.get(salary_info.get("salaryType", ""))
    return Compensation(
        interval=interval,
        min_amount=min_amount,
        max_amount=max_amount,
        currency=salary_info.get("salaryCurrency", "USD"),
    )


def date_posted_from_epoch_millis(epoch_millis: int | None) -> date | None:
    """Search results carry `pubDate` as milliseconds since the epoch."""
    if epoch_millis is None:
        return None
    return datetime.fromtimestamp(epoch_millis / 1000, tz=UTC).date()


def is_job_remote(job: dict) -> bool:
    """Searches the location string and remote flag/keywords a search
    result carries. Detail-level description text is folded in by the
    caller (jobsniffer.indeed.search), which also has the fetched
    description available."""
    if job.get("remoteLocation"):
        return True
    remote_keywords = ("remote", "work from home", "wfh")
    haystack = f"{job.get('title', '')} {job.get('formattedLocation', '')}".lower()
    return any(keyword in haystack for keyword in remote_keywords)


def extract_detail_description(detail_body: dict) -> str | None:
    """The full job description from /viewjob's response. Two fields
    carry it (jobInfoModel.sanitizedJobDescription and the
    js-match-insights-provider mirror in mosaicData) -- confirmed
    identical content in tests/fixtures/indeed.jsonl (modulo whitespace);
    the primary field is used, the mirror only as a fallback if Indeed
    ever stops populating the primary one."""
    try:
        return detail_body["jobInfoWrapperModel"]["jobInfoModel"][
            "sanitizedJobDescription"
        ]
    except (KeyError, TypeError):
        pass
    try:
        return detail_body["mosaicData"]["serverContextData"]["request"]["data"][
            "metaData"
        ]["js-match-insights-provider"]["jobDescription"]
    except (KeyError, TypeError):
        return None
