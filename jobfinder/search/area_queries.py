"""Web search queries for a search with no job title: any jobs in the chosen cities and state."""


def area_queries(state, city_names, board_sites):
    """Board-site queries first (each board found is read in full), then city and statewide job queries."""
    state = (state or "").strip()
    places = [city for city in city_names if city] or ([state] if state else [])
    queries = [f'site:{site} "{place}"' for place in places for site in board_sites]
    for city in city_names:
        if not city:
            continue
        queries.extend([
            f'jobs "{city}" "{state}"',
            f'now hiring "{city}" "{state}"',
            f'careers "{city}" "{state}"',
        ])
    if state:
        queries.extend([
            f"jobs {state}",
            f"hiring {state}",
            f'"{state}" careers',
            f"state government jobs {state}",
            f"university jobs {state}",
            f"hospital health system careers {state}",
        ])
    return list(dict.fromkeys(queries))
