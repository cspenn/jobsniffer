from __future__ import annotations

from jobsniffer.exception import IndeedException
from jobsniffer.indeed import graphql as indeed_graphql
from jobsniffer.indeed.detail import fetch_job_detail
from jobsniffer.indeed.parse import (
    compensation_from_extracted_salary,
    compensation_from_salary_info_model,
    date_posted_from_epoch_millis,
    extract_detail_description,
    is_job_remote,
    job_types_from_taxonomy,
)
from jobsniffer.indeed.search import fetch_search_page
from jobsniffer.model import (
    DescriptionFormat,
    JobPost,
    JobResponse,
    Location,
    Scraper,
    ScraperInput,
    Site,
)
from jobsniffer.util import (
    create_logger,
    create_session,
    extract_emails_from_text,
    markdown_converter,
)

log = create_logger("Indeed")


class Indeed(Scraper):
    def __init__(
        self,
        proxies: list[str] | str | None = None,
        ca_cert: str | None = None,
        user_agent: str | None = None,
    ):
        """Initializes IndeedScraper.

        Primary path is the mosaic HTML search + /viewjob detail flow
        (jobsniffer.indeed.search/detail/parse); the GraphQL API
        (jobsniffer.indeed.graphql) is a fallback used only when the HTML
        search returns no results at all, e.g. Indeed serving a
        block/CAPTCHA page -- see docs/2026-07-27-jobsniffer-modernization-
        plan.md, Phase 3a, for why HTML is primary (the search response
        alone was found to carry only truncated snippets and estimated
        salaries; the detail fetch is what supplies the full description
        and authoritative salary the graphql path already returned
        directly).

        scraper_input/base_url are threaded through the scrape/helper
        methods as explicit parameters rather than stored on self: the
        upstream pattern of stashing them as instance state made every
        helper's actual dependencies implicit (and unverifiable by mypy,
        since they're only guaranteed set after scrape() runs).
        """
        super().__init__(Site.INDEED, proxies=proxies, ca_cert=ca_cert, user_agent=user_agent)
        self.session = create_session(proxies=proxies, ca_cert=ca_cert)

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        """Scrapes Indeed for jobs matching scraper_input criteria."""
        if scraper_input.country is None:
            raise IndeedException("Indeed requires a country to resolve its domain")
        domain, api_country_code = scraper_input.country.indeed_domain_value
        base_url = f"https://{domain}.indeed.com"

        job_list = self._scrape_html(scraper_input, base_url)
        if not job_list:
            log.info("HTML search returned no results, falling back to GraphQL")
            job_list = self._scrape_graphql(scraper_input, base_url, api_country_code)

        return JobResponse(
            jobs=job_list[
                scraper_input.offset : scraper_input.offset + scraper_input.results_wanted
            ]
        )

    def _scrape_html(self, scraper_input: ScraperInput, base_url: str) -> list[JobPost]:
        job_list: list[JobPost] = []
        seen_keys: set[str] = set()
        start = 0
        target = scraper_input.results_wanted + scraper_input.offset

        while len(seen_keys) < target:
            results = fetch_search_page(
                self.session,
                base_url=base_url,
                search_term=scraper_input.search_term,
                location=scraper_input.location,
                distance=scraper_input.distance,
                hours_old=scraper_input.hours_old,
                start=start,
                timeout=scraper_input.request_timeout,
            )
            if not results:
                break

            new_this_page = 0
            for job in results:
                job_key = job.get("jobkey")
                if not job_key or job_key in seen_keys:
                    continue
                seen_keys.add(job_key)
                new_this_page += 1
                post = self._build_job_post(job, scraper_input, base_url)
                if post:
                    job_list.append(post)
                if len(seen_keys) >= target:
                    break

            if new_this_page == 0:
                # Every result on this page was already seen -- Indeed
                # isn't advancing (end of results), stop rather than loop
                # on the same page forever.
                break
            start += len(results)

        return job_list

    def _build_job_post(
        self, job: dict, scraper_input: ScraperInput, base_url: str
    ) -> JobPost | None:
        job_key = job["jobkey"]
        detail_body = fetch_job_detail(
            self.session,
            base_url=base_url,
            job_key=job_key,
            timeout=scraper_input.request_timeout,
        )

        description = None
        compensation = None
        if detail_body:
            description = extract_detail_description(detail_body)
            compensation = compensation_from_salary_info_model(
                detail_body.get("salaryInfoModel")
            )
        if description is None:
            # Detail fetch failed or the page shape changed -- fall back
            # to the truncated search-result snippet rather than an empty
            # description (P8: degrade, don't silently drop the field).
            description = job.get("snippet")
        if compensation is None:
            compensation = compensation_from_extracted_salary(job)

        if description and scraper_input.description_format == DescriptionFormat.MARKDOWN:
            description = markdown_converter(description)

        return JobPost(
            id=f"in-{job_key}",
            title=job.get("title", "N/A"),
            company_name=job.get("company"),
            job_url=f"{base_url}/viewjob?jk={job_key}",
            location=Location(
                city=job.get("jobLocationCity"),
                state=job.get("jobLocationState"),
                country=scraper_input.country,
            ),
            description=description,
            job_type=job_types_from_taxonomy(job.get("taxonomyAttributes")),
            compensation=compensation,
            date_posted=date_posted_from_epoch_millis(job.get("pubDate")),
            is_remote=is_job_remote(job),
            emails=extract_emails_from_text(description) if description else None,
        )

    def _scrape_graphql(
        self, scraper_input: ScraperInput, base_url: str, api_country_code: str
    ) -> list[JobPost]:
        job_list: list[JobPost] = []
        seen_keys: set[str] = set()
        cursor: str | None = None
        target = scraper_input.results_wanted + scraper_input.offset

        while len(seen_keys) < target:
            results, cursor = indeed_graphql.fetch_page(
                self.session,
                api_url="https://apis.indeed.com/graphql",
                api_country_code=api_country_code,
                search_term=scraper_input.search_term,
                location=scraper_input.location,
                distance=scraper_input.distance,
                hours_old=scraper_input.hours_old,
                easy_apply=scraper_input.easy_apply,
                job_type=scraper_input.job_type,
                is_remote=scraper_input.is_remote,
                cursor=cursor,
                timeout=scraper_input.request_timeout,
            )
            if not results:
                break

            for result in results:
                job = result["job"]
                if job["key"] in seen_keys:
                    continue
                seen_keys.add(job["key"])
                post = indeed_graphql.job_post_from_graphql_result(
                    job,
                    base_url=base_url,
                    description_format=scraper_input.description_format,
                )
                if post:
                    job_list.append(post)
                if len(seen_keys) >= target:
                    break

            if not cursor:
                break

        return job_list
