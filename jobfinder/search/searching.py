"""Asking the SearXNG search engine for pages, one query at a time."""

import time

import requests

from jobfinder.search import docker
from jobfinder.search import shared
from jobfinder.search.shared import SEARXNG_URL, USA_ONLY, timed


_last_search_time = 0.0


# Engines SearXNG said it could not use on its most recent search, e.g. ["duckduckgo: CAPTCHA"].
last_search_health = {"unresponsive": []}


def search_blocked_message(empty_queries):
    engines = ", ".join(last_search_health["unresponsive"][:4]) or "no engine gave a reason"
    return (f"Search engines returned nothing for {empty_queries} queries in a row ({engines}). "
            "They are probably rate-limiting Job Finder; try again in about an hour.")


def search_searxng(query, page=1):
    with timed("Search engine queries (including polite pauses)"):
        return _search_searxng(query, page)


def _search_searxng(query, page=1):
    global _last_search_time
    if not docker.check_searxng_timer():
        return []

    # Spacing requests out keeps engines like DuckDuckGo from answering with a CAPTCHA.
    wait = shared.QUERY_DELAY - (time.monotonic() - _last_search_time)
    if wait > 0:
        time.sleep(wait)

    if USA_ONLY:
        search_query = f"{query} United States"

    else:
        search_query = query

    params = {
        "q": search_query,
        "format": "json",
        "language": "en-US",
        "pageno": page,
    }

    try:
        response = requests.get(
            SEARXNG_URL,
            params=params,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        last_search_health["unresponsive"] = [
            f"{item[0]}: {item[1]}" for item in data.get("unresponsive_engines", []) if len(item) >= 2]

        return data.get("results", [])

    except requests.RequestException as error:
        print()
        print("Could not search SearXNG.")

        print(error)

        return []

    except ValueError:
        print()
        print("SearXNG did not return JSON.")

        return []

    finally:
        _last_search_time = time.monotonic()
