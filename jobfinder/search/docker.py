"""Starting and stopping Docker Desktop and the SearXNG search engine, and deciding when a search must
stop.
"""

import subprocess
import time

import requests

from jobfinder.search import shared
from jobfinder.search.shared import (
    BASE_DIR,
    SEARXNG_COMPOSE_FILE,
    SEARXNG_CONTAINER,
    SEARXNG_MAX_RUNTIME,
    START_DOCKER_AUTOMATICALLY,
    STOP_DOCKER_WHEN_FINISHED,
)

searxng_start_time = None
# True while Brave Search does the web search, so no SearXNG container is expected to be running.
brave_mode = False
docker_started_by_program = False
stop_announced = False


def run_command(command):
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )

    except FileNotFoundError:
        return None


def run_docker_command(arguments):
    return run_command(["docker"] + arguments)


def docker_engine_running():
    result = run_docker_command(["info"])

    if result is None:
        return False

    return result.returncode == 0


def start_docker_desktop():
    global docker_started_by_program

    if docker_engine_running():
        print("Docker is already running.")
        return True

    if not START_DOCKER_AUTOMATICALLY:
        print()
        print("Docker is not running.")
        print("Start Docker Desktop and " "run Job Finder again.")
        return False

    print("Starting Docker Desktop...")

    result = run_docker_command(
        [
            "desktop",
            "start",
        ]
    )

    if result is None:
        print("Docker command was not found.")
        return False

    if result.returncode != 0:
        print("Docker Desktop could not " "be started automatically.")

        print(result.stderr.strip())

        return False

    docker_started_by_program = True

    print("Waiting for Docker...")

    for _ in range(60):
        if docker_engine_running():
            print("Docker is running.")
            return True

        time.sleep(2)

    print("Docker did not become ready.")

    return False


def stop_docker_desktop():
    if not STOP_DOCKER_WHEN_FINISHED:
        return

    if not docker_started_by_program:
        print("Docker was already running " "before Job Finder started.")
        print("Leaving Docker running.")
        return

    print()
    print("Stopping Docker Desktop...")

    result = run_docker_command(
        [
            "desktop",
            "stop",
        ]
    )

    if result is not None and result.returncode == 0:
        print("Docker Desktop stopped.")

    else:
        print("Docker Desktop could not " "be stopped automatically.")


def searxng_is_running():
    result = run_docker_command(
        [
            "ps",
            "--filter",
            f"name={SEARXNG_CONTAINER}",
            "--format",
            "{{.Names}}",
        ]
    )

    if result is None:
        return False

    containers = result.stdout.strip().splitlines()

    return SEARXNG_CONTAINER in containers


def searxng_exists():
    result = run_docker_command(
        [
            "ps",
            "-a",
            "--filter",
            f"name={SEARXNG_CONTAINER}",
            "--format",
            "{{.Names}}",
        ]
    )

    if result is None:
        return False

    containers = result.stdout.strip().splitlines()

    return SEARXNG_CONTAINER in containers


def wait_for_searxng():
    print("Waiting for SearXNG...")

    for _ in range(30):
        try:
            response = requests.get(
                "http://localhost:8080",
                timeout=2,
            )

            if response.ok:
                return True

        except requests.RequestException:
            pass

        time.sleep(1)

    return False


def start_searxng():
    global searxng_start_time

    if searxng_is_running():
        print("SearXNG is already running.")

        searxng_start_time = time.time()

        return True

    print("Starting SearXNG...")

    result = run_docker_command(
        [
            "compose",
            "--env-file",
            str(BASE_DIR / ".env"),
            "-f",
            str(SEARXNG_COMPOSE_FILE),
            "up",
            "-d",
        ]
    )

    if result is None:
        return False

    if result.returncode != 0:
        print("Could not start SearXNG.")
        print(result.stderr.strip())

        return False

    if not wait_for_searxng():
        print("SearXNG did not become ready.")

        return False

    searxng_start_time = time.time()

    print("SearXNG is running.")

    print("Maximum runtime: " f"{shared.settings['searxng_timeout_minutes']} " "minutes.")

    return True


def stop_searxng():
    if not searxng_is_running():
        return

    print()
    print("Stopping SearXNG...")

    result = run_docker_command(
        [
            "stop",
            SEARXNG_CONTAINER,
        ]
    )

    if result is not None and result.returncode == 0:
        print("SearXNG stopped.")


def search_stop_reason(saved, blocked_message):
    """Why the search ended, with what to change when that is something the user can act on."""
    if shared.stop_requested():
        return "Stopped by user"
    if saved >= shared.MAX_SEARCH_RESULTS:
        return f"{saved} of {shared.MAX_SEARCH_RESULTS} distinct jobs found"
    if blocked_message:
        return blocked_message
    minutes = round(SEARXNG_MAX_RUNTIME / 60)
    if searxng_start_time is not None and time.time() - searxng_start_time >= SEARXNG_MAX_RUNTIME:
        return f"Search time limit reached ({minutes} minutes). Raise the time limit on the Tuning page to search longer."
    if not check_searxng_timer():
        return "The search engine stopped unexpectedly. Check that Docker Desktop is running, then start the search again."
    return ("Search sources exhausted: every query was checked. Try more job titles, more cities or a larger radius "
            "to find more.")


def check_searxng_timer():
    global stop_announced

    if shared.stop_requested():
        if not stop_announced:
            stop_announced = True
            print()
            print("Stop requested.")

        return False

    if not brave_mode and not searxng_is_running():
        print()
        print("SearXNG has stopped.")

        return False

    if searxng_start_time is None:
        return True

    elapsed = time.time() - searxng_start_time

    if elapsed >= SEARXNG_MAX_RUNTIME:
        print()
        print("SearXNG reached its " "time limit.")

        stop_searxng()

        return False

    return True
