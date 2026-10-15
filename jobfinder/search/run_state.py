"""What one search shares between its steps: what was asked for, and the counts so far.
"""


from jobfinder.sources.job_listings import ANY_TITLE, ANY_TITLE_LABEL


def title_label(title):
    return ANY_TITLE_LABEL if title == ANY_TITLE else title


class SearchRun:
    def __init__(self, database, state, cities, selected_titles, job_titles, query_titles, any_title,
                 selected_states, statewide_states, city_targets, rejected_urls):
        self.database = database
        self.state = state
        self.cities = cities
        self.selected_titles = selected_titles
        self.job_titles = job_titles
        self.query_titles = query_titles
        self.any_title = any_title
        self.selected_states = selected_states
        self.statewide_states = statewide_states
        self.city_targets = city_targets
        self.rejected_urls = rejected_urls
        self.fetched_pages = set()
        self.seen_openings = set()
        self.seen_result_urls = set()
        self.results_checked = 0
        self.candidate_number = 0
        self.pages_checked = 0
        self.websites_checked = 0
        self.companies_saved = 0
        self.passed_count = 0
        self.blocked_sites = 0
        self.existing_companies = 0
        self.no_career_page = 0
        self.employer_saved_total = 0
        self.site_saved_total = 0
        self.boards_discovered = 0
        self.known_employers = []
        self.known_boards = set()
        self.unreadable_boards = set()
        self.read_ahead = None  # the boards being read ahead of their turn (see search_employer_boards)
        self.employer_http = None
        self.same_job_index = None
        self.blocked_message = None
