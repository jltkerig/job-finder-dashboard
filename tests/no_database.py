"""Imported first by every test module: tests can never reach the real Job Finder database.

Every database call in Job Finder goes through mysql.connector.connect, so that is replaced with one that
raises mysql.connector.Error (which the app already handles). A test that needs a database patches
connect itself, which still works.
"""
import mysql.connector


def _no_real_database(*args, **kwargs):
    raise mysql.connector.Error("tests have no database")


mysql.connector.connect = _no_real_database
