"""States a remote job says its applicant must live in.

Career sites often list a remote job once per state it is open to ("Work At Home-Massachusetts",
"Remote - TX"), or say "open to residents of the following states: ...". restriction_states() turns
those into a set of state codes; an empty set means no restriction was named.
"""
import re

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
CODES = set(STATES.values())

_NAME = "|".join(re.escape(name) for name in sorted(STATES, key=len, reverse=True))
_CODE = "|".join(sorted(CODES))
_STATE = rf"(?:{_NAME}|{_CODE})"
_STATE_ANY = re.compile(rf"\b({_NAME})\b|(?<![A-Za-z])({_CODE})(?![A-Za-z])", re.I)
_STATE_NAME_ONLY = re.compile(rf"\b({_NAME})\b", re.I)

# The words that mark a place as a work-from-home location.
_REMOTE_LABEL = r"(?:work(?:ing)?[ -]*(?:at|from)[ -]*home|remote|virtual|telecommut\w*|telework\w*|wfh|home[ -]*based)"
_LABEL_THEN_STATE = re.compile(rf"\b{_REMOTE_LABEL}\s*(?:[-–—:,/(]|\bin\b)\s*({_STATE})\b\)?", re.I)
_STATE_THEN_LABEL = re.compile(rf"(?<![A-Za-z])({_STATE})\s*[-–—:,/(]\s*{_REMOTE_LABEL}\b", re.I)
_REMOTE_PLACE = re.compile(rf"\b{_REMOTE_LABEL}\b", re.I)

# "open to residents of the following states: CA, NY, TX"
_STATE_LIST_LEAD = re.compile(
    r"(?:hiring|hire|hires|eligible|approved|open|available|residents?|candidates|applicants)\s+(?:only\s+)?(?:in|to|for|from|of)\s+"
    r"(?:the\s+)?(?:following|these|listed)\s+states?[^:.\n]{0,40}[:\-]"
    r"|(?:remote|work(?:ing)? from home)[^.\n]{0,50}\b(?:in|within)\s+(?:the\s+)?(?:following\s+)?states?\s*(?:of\s*)?[:\-]"
    r"|(?:eligible|approved)\s+(?:states?|locations?)\s*[:\-]", re.I)
# Pay-transparency lists ("salary for the following states") name states without limiting who can apply.
_PAY_CONTEXT = re.compile(r"salary|\bpay\b|compensation|wage|range|\brate\b|bonus|benefit", re.I)


def _codes(text, names_only=False):
    pattern = _STATE_NAME_ONLY if names_only else _STATE_ANY
    found = set()
    for match in pattern.finditer(text):
        word = next(group for group in match.groups() if group)
        found.add(STATES.get(word.casefold()) or word.upper())
    return found


def is_remote_place(place):
    """True for a location string that is a work-from-home label ("Work At Home-Florida", "Remote - TX")."""
    return bool(_REMOTE_PLACE.search(str(place or "")))


def place_states(places):
    """State codes named by work-from-home location strings; other locations name none."""
    found = set()
    for place in places or []:
        place = str(place or "")
        for pattern in (_LABEL_THEN_STATE, _STATE_THEN_LABEL):
            for match in pattern.finditer(place):
                word = match.group(1)
                found.add(STATES.get(word.casefold()) or word.upper())
    return found


def list_states(text):
    """State codes in a sentence such as 'open to candidates in the following states: CA, NY, TX'."""
    text = str(text or "")[:8000]
    found = set()
    for lead in _STATE_LIST_LEAD.finditer(text):
        if _PAY_CONTEXT.search(text[max(0, lead.start() - 90): lead.end()]):
            continue
        window = text[lead.end(): lead.end() + 260]
        # Stop at the end of the list: a blank line or a sentence that is not a state list.
        window = re.split(r"\n\s*\n|\.\s+[A-Z]", window)[0]
        found |= _codes(window)
    return found


def restriction_states(places=(), text=""):
    """Every state a remote job is limited to, from its location strings and its description."""
    return place_states(places) | list_states(text)
