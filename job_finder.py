"""Run a job search from the command line (the dashboard starts it this way).

    python job_finder.py                       search for the titles and places saved in your profile
    python job_finder.py --update-existing     refresh the results already saved
    python job_finder.py --import-captures     import the jobs the browser extension saved

The code lives in the jobfinder/search/ package; jobfinder/search/runner.py is the search itself.
"""
from jobfinder.search.runner import command_line

if __name__ == "__main__":
    command_line()
