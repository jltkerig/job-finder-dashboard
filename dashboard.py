from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import mysql.connector
from flask import Flask, render_template
from mysql.connector import Error
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

app = Flask(__name__)


DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "job_finder")


def get_companies():
    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )

        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                name,
                domain,
                career_url,
                source_url,
                country,
                state,
                usa_confidence,
                date_found,
                last_checked
            FROM companies
            ORDER BY date_found DESC
            """)

        return cursor.fetchall()

    except Error as error:
        print()
        print("Could not read companies " "from the database.")
        print(error)

        return []

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/")
def home():
    now = datetime.now(ZoneInfo("America/New_York"))

    print()
    print("=" * 60)

    print("PAGE REFRESH — " f"{now.strftime('%B %d, %Y at %I:%M:%S %p %Z')}")

    print("=" * 60)

    companies = get_companies()

    return render_template(
        "index.html",
        companies=companies,
    )


if __name__ == "__main__":
    app.run(debug=True)
