"""Pages of 25 for a long list (the Saved Résumés and Cover Letters on the main page)."""

PER_PAGE = 25


def paginate(items, page, per_page=PER_PAGE):
    """(the items on that page, the page number used, how many pages there are).

    A page past either end is moved back inside, so a stale link (the last page after some files were deleted) still
    shows something."""
    pages = max(1, -(-len(items) // per_page))
    page = min(max(int(page or 1), 1), pages)
    start = (page - 1) * per_page
    return items[start:start + per_page], page, pages


def page_links(page, pages, around=2):
    """Page numbers to show as links, with None where some are left out: 1, None, 4, 5, 6, 7, 8, None, 20."""
    wanted = sorted({1, pages, *range(max(1, page - around), min(pages, page + around) + 1)})
    links, last = [], 0
    for number in wanted:
        if number - last > 1:
            links.append(None)
        links.append(number)
        last = number
    return links
