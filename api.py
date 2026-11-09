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
import threading
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from workbench import CASES, ROOT
from reports import ReportInput, ReviewInput, make_snapshot, snapshot_hash
from extraction import analyze, configuration, ExtractionError
from generation import generate, review_hash


class Submission(BaseModel):
    case: str
    release: Literal["buggy", "fixed", "both"] = "both"
    timeout: int = Field(default=30, ge=1, le=60)


def now():
    return datetime.now(timezone.utc).isoformat()


def create_app(data_dir=None, wheelhouse=None):
    data = Path(data_dir or os.environ.get("WORKBENCH_DATA", ROOT / "api-data")).resolve()
    wheels = wheelhouse or os.environ.get("WORKBENCH_WHEELHOUSE")
    analysis_slot = threading.BoundedSemaphore(1)

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
            db.execute("""CREATE TABLE IF NOT EXISTS analyses (
                id TEXT PRIMARY KEY, report_id TEXT NOT NULL, created_at TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, duration_seconds REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS generated_tests (
                id TEXT PRIMARY KEY, report_id TEXT NOT NULL, created_at TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL, review_sha256 TEXT NOT NULL,
                saved_review TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, duration_seconds REAL NOT NULL)""")
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

    @app.get("/ai/config")
    def ai_configuration():
        return configuration()

    @app.get("/reports/{report_id}/analyses")
    def report_analyses(report_id: UUID):
        read_report(report_id)
        with connect() as db:
            rows = db.execute("SELECT * FROM analyses WHERE report_id=? "
                              "ORDER BY created_at DESC LIMIT 10", (str(report_id),)).fetchall()
        return [dict(row, payload=json.loads(row["payload"])) for row in rows]

    @app.post("/reports/{report_id}/analyze", status_code=201)
    def analyze_report(report_id: UUID):
        report = read_report(report_id)
        if not configuration()["configured"]:
            raise HTTPException(503, "Set OPENAI_API_KEY in the server environment, then restart the API.")
        if not analysis_slot.acquire(blocking=False):
            raise HTTPException(429, "Another report is being analyzed. Wait for it to finish.")
        analysis_id, started, created = str(uuid4()), time.monotonic(), now()
        error = None
        try:
            payload = analyze(report["snapshot"])
            status = "completed"
        except ExtractionError as exc:
            payload, status, error = {"error": str(exc), **configuration()}, "failed", exc
        finally:
            analysis_slot.release()
        duration = round(time.monotonic() - started, 3)
        with connect() as db:
            db.execute("INSERT INTO analyses VALUES (?,?,?,?,?,?,?)", (
                analysis_id, str(report_id), created, report["snapshot_sha256"],
                status, json.dumps(payload), duration))
        if error:
            raise HTTPException(error.status_code, str(error))
        return {"id": analysis_id, "report_id": str(report_id), "created_at": created,
                "snapshot_sha256": report["snapshot_sha256"], "status": status,
                "payload": payload, "duration_seconds": duration}

    @app.get("/reports/{report_id}/tests")
    def generated_tests(report_id: UUID):
        report = read_report(report_id)
        with connect() as db:
            rows = db.execute("SELECT * FROM generated_tests WHERE report_id=? "
                              "ORDER BY created_at DESC LIMIT 10", (str(report_id),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            item["saved_review"] = json.loads(item["saved_review"])
            item["review_is_current"] = item["review_sha256"] == review_hash(report["review"])
            result.append(item)
        return result

    @app.post("/reports/{report_id}/tests", status_code=201)
    def generate_test(report_id: UUID):
        report = read_report(report_id)
        if not report["reviewed_at"] or not all(report["review"][key].strip()
                for key in ("expected_behavior", "actual_behavior")):
            raise HTTPException(409, "Save a review with expected and actual behavior before generating a test.")
        if not configuration()["configured"]:
            raise HTTPException(503, "Configure OPENAI_API_KEY and restart the API.")
        if not analysis_slot.acquire(blocking=False):
            raise HTTPException(429, "Another AI request is running. Wait for it to finish.")
        test_id, started, created = str(uuid4()), time.monotonic(), now()
        error = None
        try:
            payload = generate(report)
            status = "completed"
        except ExtractionError as exc:
            payload, status, error = {"error": str(exc)}, "failed", exc
        finally:
            analysis_slot.release()
        duration, digest = round(time.monotonic() - started, 3), review_hash(report["review"])
        with connect() as db:
            db.execute("INSERT INTO generated_tests VALUES (?,?,?,?,?,?,?,?,?)", (
                test_id, str(report_id), created, report["snapshot_sha256"], digest,
                json.dumps(report["review"]), status, json.dumps(payload), duration))
        if error:
            raise HTTPException(error.status_code, str(error))
        latest = read_report(report_id)
        return {"id": test_id, "report_id": str(report_id), "created_at": created,
                "snapshot_sha256": report["snapshot_sha256"], "review_sha256": digest,
                "saved_review": report["review"], "status": status, "payload": payload,
                "duration_seconds": duration,
                "review_is_current": digest == review_hash(latest["review"])}

    @app.get("/tests/{test_id}/file")
    def download_generated_test(test_id: UUID):
        with connect() as db:
            row = db.execute("SELECT payload, status FROM generated_tests WHERE id=?", (str(test_id),)).fetchone()
        if row is None:
            raise HTTPException(404, "Generated test not found")
        payload = json.loads(row["payload"])
        code = payload.get("draft", {}).get("code")
        if row["status"] != "completed" or not code:
            raise HTTPException(409, "This attempt has no downloadable test")
        content = "# AI-generated draft. Not executed or verified. Review before running.\n" + code
        return Response(content, media_type="text/x-python", headers={
            "Content-Disposition": 'attachment; filename="test_reproduction_' + str(test_id) + '.py"'})

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
