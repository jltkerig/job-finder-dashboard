"""The one place that opens a connection to the Job Finder database.

The login comes from the environment (.env): DB_HOST, DB_PORT, DB_USER, DB_PASSWORD and DB_NAME. Every module asks
for a connection here instead of repeating the login, so there is a single spot to change it. The tests replace
mysql.connector.connect, which this module calls each time, so they can never reach the real database.
"""
import os
from contextlib import contextmanager

import mysql.connector


def settings(database=True):
    """The connection arguments. database=False connects to the server only (used to create the database)."""
    login = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
    }
    if database:
        login["database"] = os.getenv("DB_NAME", "job_finder")
    return login


def connect(database=True):
    return mysql.connector.connect(**settings(database))


@contextmanager
def cursor(dictionary=False, commit=False):
    """A cursor that is always closed along with its connection: `with db.cursor(commit=True) as cur: ...`."""
    connection = connect()
    cur = connection.cursor(dictionary=dictionary)
    try:
        yield cur
        if commit:
            connection.commit()
    finally:
        cur.close()
        connection.close()
