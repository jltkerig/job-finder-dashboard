"""Asking a search engine for pages, one query at a time: Brave Search when an API key is saved on the Tuning page,
otherwise SearXNG in Docker. Job Finder sets no query limit of its own; if Brave refuses (plan quota, bad key), the
search switches to SearXNG and carries on."""

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


BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


def brave_key():
    return str(shared.settings.get("brave_api_key") or "").strip()


def search_searxng(query, page=1):
    with timed("Search engine queries (including polite pauses)"):
        if docker.brave_mode:
            results = _search_brave(query, page)
            if results is not None:
                return results
            if not switch_to_searxng():
                return []
        return _search_searxng(query, page)


def switch_to_searxng():
    """Brave refused: start Docker and SearXNG and use them for the rest of the search."""
    docker.brave_mode = False
    print()
    print("Switching to SearXNG for the rest of this search.")
    return docker.start_docker_desktop() and docker.start_searxng()


def _brave_get(params):
    return requests.get(BRAVE_URL, params=params, timeout=20,
                        headers={"Accept": "application/json", "X-Subscription-Token": brave_key()})


def _search_brave(query, page=1):
    """One page of Brave results shaped like SearXNG's ({url, title, content}), [] when there are none, or None
    when Brave refused and the search should switch engines."""
    global _last_search_time
    if shared.stop_requested():
        return []
    wait = shared.QUERY_DELAY - (time.monotonic() - _last_search_time)
    if wait > 0:
        time.sleep(wait)
    params = {"q": (f"{query} United States" if USA_ONLY else query)[:400], "count": 20,
              "offset": max(0, min(9, page - 1)), "country": "us", "search_lang": "en"}
    try:
        response = _brave_get(params)
        if response.status_code == 429:
            time.sleep(2)  # may just be the per-second rate: wait and try once more before giving up on Brave
            response = _brave_get(params)
        if response.status_code in (401, 402, 403, 429):
            print()
            print(f"Brave Search refused the query (HTTP {response.status_code}).")
            return None
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        print()
        print("Could not search Brave.")
        print(error)
        return []
    finally:
        _last_search_time = time.monotonic()
    return [{"url": item["url"], "title": item.get("title") or "", "content": item.get("description") or ""}
            for item in (data.get("web") or {}).get("results") or [] if item.get("url")]


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
