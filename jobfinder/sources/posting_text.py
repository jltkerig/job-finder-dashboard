"""A posting's text straight from the job system's data service, for sites whose pages build themselves with
JavaScript (a plain download gets only menus): National Labor Exchange, Workday and Oracle Cloud. "" when the URL
isn't one of these or the service doesn't answer; the caller then reads the page itself."""

import re
from urllib.parse import urlparse

import requests

from jobfinder.sources.employer_common import Http
from jobfinder.sources.job_sites import NationalLaborExchange


def _workday_info(url, http):
    """Workday's own record for a posting (jobPostingInfo), or {}."""
    # https://acme.wd5.myworkdayjobs.com/[en-US/]External/job/City/Title_R123 -> /wday/cxs/acme/External/job/City/Title_R123
    parts = urlparse(url)
    match = re.match(r"/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)(/job/.+)", parts.path)
    if not match:
        return {}
    tenant = parts.hostname.split(".")[0]
    response = http.get(f"https://{parts.hostname}/wday/cxs/{tenant}/{match.group(1)}{match.group(2)}")
    if response.status_code != 200:
        return {}
    return response.json().get("jobPostingInfo") or {}


def _workday(url, http):
    return str(_workday_info(url, http).get("jobDescription") or "")


def workday_remote_type(url):
    """Workday's remoteType ("Not Remote", "Remote", "Hybrid") for a myworkdayjobs.com posting, or None. Workday's page
    data says TELECOMMUTE for many onsite jobs, so this is the one to trust."""
    if not (urlparse(url or "").hostname or "").casefold().endswith("myworkdayjobs.com"):
        return None
    try:
        return _workday_info(url, Http(delay=0)).get("remoteType") or None
    except (requests.RequestException, ValueError):
        return None


def _oracle(url, http):
    # https://x.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/16994
    match = re.search(r"/sites/([^/]+)/job/(\d+)", url)
    if not match:
        return ""
    response = http.get(f"https://{urlparse(url).hostname}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
                        params={"expand": "all", "onlyData": "true", "finder": f'ById;Id="{match.group(2)}",siteNumber={match.group(1)}'})
    if response.status_code != 200:
        return ""
    items = response.json().get("items") or []
    if not items:
        return ""
    return "\n".join(str(items[0].get(key) or "") for key in
                     ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")).strip()


def posting_text(url):
    """The posting's HTML or text from its job system, or ""."""
    host = (urlparse(url or "").hostname or "").casefold()
    try:
        if host.endswith("usnlx.com"):
            return NationalLaborExchange().description(url)
        if host.endswith("myworkdayjobs.com"):
            return _workday(url, Http(delay=0))
        if host.endswith("oraclecloud.com"):
            return _oracle(url, Http(delay=0))
    except (requests.RequestException, ValueError, KeyError):
        return ""
    return ""
