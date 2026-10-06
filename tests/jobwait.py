"""Waiting for an app job in tests: by the clock, not a number of polls, so a slow CI runner can't cut it short."""

import time


def wait_job(client, job_id: str, timeout: float = 60.0) -> dict:
    """The job's final snapshot; fails the test if it hasn't finished within `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        snap = client.get(f"/jobs/{job_id}").json()
        if snap["status"] in ("succeeded", "failed"):
            return snap
        assert time.monotonic() < deadline, f"job {job_id} still {snap['status']} after {timeout:.0f} s"
        time.sleep(0.02)
