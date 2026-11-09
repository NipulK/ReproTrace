"""Local, single-process API for curated reproduction jobs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from workbench import CASES, ROOT
from reports import ReportInput, ReviewInput, make_snapshot, snapshot_hash


class Submission(BaseModel):
    case: str
    release: Literal["buggy", "fixed", "both"] = "both"
    timeout: int = Field(default=30, ge=1, le=60)


def now():
    return datetime.now(timezone.utc).isoformat()


def create_app(data_dir=None, wheelhouse=None):
    data = Path(data_dir or os.environ.get("WORKBENCH_DATA", ROOT / "api-data")).resolve()
    wheels = wheelhouse or os.environ.get("WORKBENCH_WHEELHOUSE")

    def connect():
        connection = sqlite3.connect(str(data / "jobs.sqlite3"), timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def update(job_id, status, error=None):
        with connect() as db:
            db.execute("UPDATE jobs SET status=?, updated_at=?, error=? WHERE id=?",
                       (status, now(), error, job_id))

    def get_job(job_id):
        with connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (str(job_id),)).fetchone()
        if row is None:
            raise HTTPException(404, "Run not found")
        return dict(row)

    def worker(job_id, submission):
        update(job_id, "running")
        folder = data / job_id
        folder.mkdir()
        command = [sys.executable, str(ROOT / "workbench.py"), "run", submission.case,
                   "--release", submission.release, "--timeout", str(submission.timeout),
                   "--output", str(folder / "attempts")]
        if wheels:
            command += ["--wheelhouse", str(Path(wheels).resolve())]
        try:
            with (folder / "runner.stdout.txt").open("wb") as out:
                with (folder / "runner.stderr.txt").open("wb") as err:
                    process = subprocess.run(command, stdout=out, stderr=err, timeout=1100)
            # A completed job may contain an unsuccessful reproduction.
            reports = list((folder / "attempts").glob("*/result.json"))
            expected_count = 2 if submission.release == "both" else 1
            if process.returncode in (0, 1) and len(reports) == expected_count:
                update(job_id, "completed")
            else:
                update(job_id, "failed", "Runner did not produce all expected results")
        except subprocess.TimeoutExpired:
            update(job_id, "failed", "Runner exceeded its overall time limit")
        except Exception as exc:
            update(job_id, "failed", type(exc).__name__ + ": " + str(exc))

    @asynccontextmanager
    async def lifespan(app):
        data.mkdir(parents=True, exist_ok=True)
        with connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, release TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, error TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, captured_at TEXT NOT NULL,
                snapshot TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL,
                review TEXT NOT NULL, reviewed_at TEXT)""")
            db.execute("UPDATE jobs SET status='interrupted', updated_at=?, error=? "
                       "WHERE status IN ('queued','running')",
                       (now(), "Previous API process stopped before job completion"))
        app.state.pool = ThreadPoolExecutor(max_workers=1)
        try:
            yield
        finally:
            app.state.pool.shutdown(wait=True)

    app = FastAPI(title="ReproTrace", version="0.2.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/intake", include_in_schema=False)
    def intake():
        return FileResponse(ROOT / "static" / "intake.html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/cases")
    def cases():
        return [{"id": key, "package": value[0], "buggy": value[1], "fixed": value[2]}
                for key, value in CASES.items()]

    def read_report(report_id):
        with connect() as db:
            row = db.execute("SELECT * FROM reports WHERE id=?", (str(report_id),)).fetchone()
        if row is None:
            raise HTTPException(404, "Report not found")
        report = dict(row)
        report["snapshot"] = json.loads(report["snapshot"])
        report["review"] = json.loads(report["review"])
        return report

    @app.post("/reports", status_code=201)
    def create_report(submission: ReportInput):
        snapshot = make_snapshot(submission)
        report_id = str(uuid4())
        with connect() as db:
            db.execute("INSERT INTO reports VALUES (?,?,?,?,?,?)", (
                report_id, now(), json.dumps(snapshot), snapshot_hash(snapshot),
                ReviewInput().model_dump_json(), None))
        return read_report(report_id)

    @app.get("/reports")
    def list_reports(limit: int = Query(default=20, ge=1, le=100)):
        with connect() as db:
            rows = db.execute("SELECT id, captured_at, snapshot, reviewed_at FROM reports "
                              "ORDER BY captured_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": row["id"], "captured_at": row["captured_at"],
                 "reviewed_at": row["reviewed_at"], "title": json.loads(row["snapshot"])["title"],
                 "repository": json.loads(row["snapshot"])["repository"]} for row in rows]

    @app.get("/reports/{report_id}")
    def report_detail(report_id: UUID):
        return read_report(report_id)

    @app.put("/reports/{report_id}/review")
    def review_report(report_id: UUID, review: ReviewInput):
        read_report(report_id)
        with connect() as db:
            db.execute("UPDATE reports SET review=?, reviewed_at=? WHERE id=?",
                       (review.model_dump_json(), now(), str(report_id)))
        return read_report(report_id)

    @app.post("/runs", status_code=202)
    def submit(submission: Submission):
        if submission.case not in CASES:
            raise HTTPException(422, "Unknown case; select an ID from GET /cases")
        job_id = str(uuid4())
        with connect() as db:
            # Bound the local queue, including the active job.
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= 10:
                raise HTTPException(429, "Local queue is full; retry after a job finishes")
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",
                       (job_id, submission.case, submission.release, "queued", now(), now(), None))
        try:
            app.state.pool.submit(worker, job_id, submission)
        except RuntimeError:
            update(job_id, "failed", "Worker unavailable")
            raise HTTPException(503, "Worker unavailable")
        return {"id": job_id, "status": "queued", "status_url": "/runs/" + job_id,
                "results_url": "/runs/" + job_id + "/results"}

    @app.get("/runs")
    def list_runs(limit: int = Query(default=20, ge=1, le=100)):
        with connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]

    @app.get("/runs/{job_id}")
    def status(job_id: UUID):
        return get_job(job_id)

    @app.get("/runs/{job_id}/results")
    def results(job_id: UUID):
        job = get_job(job_id)
        reports = []
        # Read artifacts only once the worker has finished writing them.
        if job["status"] in ("completed", "failed", "interrupted"):
            for path in sorted((data / str(job_id) / "attempts").glob("*/result.json")):
                try:
                    report = json.loads(path.read_text())
                except (ValueError, OSError):
                    continue
                report["attempt_id"] = path.parent.name
                reports.append(report)
        return {"id": str(job_id), "status": job["status"], "error": job["error"],
                "results": reports}

    @app.get("/runs/{job_id}/artifacts")
    def artifacts(job_id: UUID):
        get_job(job_id)
        folder = data / str(job_id)
        files = list(folder.glob("runner.*.txt"))
        files += list((folder / "attempts").glob("*/*"))
        allowed = {"reproduce.py", "driver.py", "requirements.txt", "observation.json", "result.json"}
        return [{"path": str(path.relative_to(folder)), "url":
                 "/runs/" + str(job_id) + "/artifacts/" + path.relative_to(folder).as_posix()}
                for path in sorted(files) if path.is_file() and
                (path.name in allowed or path.name.endswith((".stdout.txt", ".stderr.txt")))]

    @app.get("/runs/{job_id}/artifacts/{artifact_path:path}")
    def download(job_id: UUID, artifact_path: str):
        available = {item["path"] for item in artifacts(job_id)}
        if artifact_path not in available:
            raise HTTPException(404, "Artifact not found")
        folder = (data / str(job_id)).resolve()
        path = (folder / artifact_path).resolve()
        if folder not in path.parents or not path.is_file():
            raise HTTPException(404, "Artifact not found")
        return FileResponse(path, filename=path.name)

    return app


app = create_app()
