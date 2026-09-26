import time

from astro_ingest import jobs


def wait(job):
    for _ in range(200):
        if job.snapshot()["status"] in ("succeeded", "failed"):
            return job.snapshot()
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_python_job_runs_and_logs(tmp_path):
    def work(progress):
        progress(50.0, "halfway")
        return {"answer": 42}

    job = jobs.create_python_job(work, tmp_path / "logs", kind="test")
    snap = wait(job)
    assert (snap["status"], snap["result"], snap["percent_complete"]) == ("succeeded", {"answer": 42}, 100.0)
    assert "halfway" in job.log_path.read_text()
    assert jobs.get_job(job.id) is job and job in jobs.list_all_jobs()


def test_failing_job_reports_error(tmp_path):
    def work(progress):
        raise RuntimeError("boom")

    snap = wait(jobs.create_python_job(work, tmp_path / "logs", kind="test"))
    assert snap["status"] == "failed" and "boom" in snap["error"]
