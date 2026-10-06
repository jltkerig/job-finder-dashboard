"""Imported first by every test module: tests can never reach the real Job Finder database.

Every database call in Job Finder goes through mysql.connector.connect, so that is replaced with one that
raises mysql.connector.Error (which the app already handles). A test that needs a database patches
connect itself, which still works.
"""
import os
import tempfile

# Tests read and write their files in a temporary folder, never in the real user-data/ and logs/ (set before jobfinder.paths loads).
_TEMP = tempfile.mkdtemp(prefix="jobfinder-tests-")
os.environ["JOBFINDER_USER_DIR"] = os.path.join(_TEMP, "user-data")
os.environ["JOBFINDER_LOG_DIR"] = os.path.join(_TEMP, "logs")

import mysql.connector


def _no_real_database(*args, **kwargs):
    raise mysql.connector.Error("tests have no database")


mysql.connector.connect = _no_real_database


# Tests must not spend Brave Search queries either: every Brave call goes through this one function, so it is
# replaced here. A test of the Brave search patches it again with its own fake.
from jobfinder.search import searching  # noqa: E402


def _no_real_search(*args, **kwargs):
    raise RuntimeError("tests have no Brave Search")


searching._brave_get = _no_real_search
