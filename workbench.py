"""CLI for trusted, curated fixtures. Uses only the Python standard library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
CASES = {
    "werkzeug-2842": ("Werkzeug", "3.0.1", "3.0.2"),
    "werkzeug-2834": ("Werkzeug", "3.0.1", "3.0.2"),
    "werkzeug-2980": ("Werkzeug", "3.0.6", "3.1.0"),
    "click-2500": ("click", "8.1.7", "8.1.8"),
    "click-2294": ("click", "8.1.3", "8.1.4"),
}

# Runs inside the target environment, reporting exceptions as data.
DRIVER = '''import importlib.util, json, sys, traceback
from pathlib import Path
from importlib.metadata import version
spec = importlib.util.spec_from_file_location("fixture", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
package, function = module.CASES[sys.argv[2]]
record = {"package": package, "version": version(package), "exception": None}
try:
    function()
except Exception as exc:
    record["exception"] = {"type": type(exc).__name__, "message": str(exc)}
    traceback.print_exc()
Path(sys.argv[3]).write_text(json.dumps(record, indent=2))
sys.exit(1 if record["exception"] else 0)
'''


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def execute(command, directory, name, timeout, env):
    start = time.monotonic()
    with (directory / (name + ".stdout.txt")).open("wb") as out:
        with (directory / (name + ".stderr.txt")).open("wb") as err:
            try:
                result = subprocess.run(command, stdout=out, stderr=err,
                                        timeout=timeout, env=env, cwd=directory)
                status, code = "completed", result.returncode
            except subprocess.TimeoutExpired:
                status, code = "timed_out", None
            except OSError as exc:
                err.write(str(exc).encode())
                status, code = "launch_failed", None
    return {"command": [str(x) for x in command], "status": status,
            "exit_code": code, "duration_seconds": round(time.monotonic() - start, 3)}


def classify(case, record, stdout):
    """Match this curated symptom, never equate arbitrary failure with reproduction."""
    exc = record["exception"]
    if exc is None:
        return "corrected_behavior_observed"
    kind, message = exc["type"], exc["message"]
    matches = {
        "werkzeug-2842": kind == "TypeError" and "NoneType" in message,
        "werkzeug-2834": kind == "AssertionError" and "Observed status: 308" in stdout,
        "werkzeug-2980": kind == "AssertionError" and "Observed: 'invalid'" in stdout,
        "click-2500": kind == "AssertionError" and "--name TEXT" in stdout
                       and "[default:" not in stdout,
        "click-2294": kind == "TypeError" and "unexpected keyword argument 'help'" in message,
    }
    return "reported_symptom_observed" if matches[case] else "inconclusive"


def run_case(case, role, args):
    package, buggy, fixed = CASES[case]
    version = buggy if role == "buggy" else fixed
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = args.output.resolve() / (stamp + "-" + case + "-" + role + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True)
    script = directory / "reproduce.py"
    shutil.copy2(ROOT / "cases" / "reproduce.py", script)
    (directory / "driver.py").write_text(DRIVER)
    requirements = [package + "==" + version]
    if package == "Werkzeug":
        requirements.append("MarkupSafe==2.1.5")
    (directory / "requirements.txt").write_text("\n".join(requirements) + "\n")
    expected = "reported_symptom_observed" if role == "buggy" else "corrected_behavior_observed"
    report = {
        "case": case, "release_role": role, "package": package, "version": version,
        "source_url": "https://github.com/pallets/" + package.lower() + "/issues/" + case.split("-")[1],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "host_python": sys.version, "platform": platform.platform(),
        "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "expected_outcome": expected, "outcome": "environment_failure",
        "matches_expectation": False, "stages": [],
        "scope": "trusted curated fixture; virtualenv is not a security sandbox",
    }
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PYTHON") and key != "VIRTUAL_ENV"}
    env.update(PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    venv = directory / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    def stage(name, command, timeout):
        item = execute(command, directory, name, timeout, env)
        item["name"] = name
        report["stages"].append(item)
        return item
    try:
        setup = stage("venv", [sys.executable, "-m", "venv", str(venv)], args.setup_timeout)
        if setup["exit_code"] == 0:
            command = [str(python), "-m", "pip", "install", "--no-input", "--retries", "0"]
            if args.wheelhouse:
                command += ["--no-index", "--find-links", str(args.wheelhouse.resolve())]
            command += ["-r", str(directory / "requirements.txt")]
            install = stage("install", command, args.setup_timeout)
            if install["exit_code"] == 0:
                freeze = stage("freeze", [str(python), "-m", "pip", "freeze", "--all"], 30)
                if freeze["exit_code"] == 0:
                    metadata = directory / "observation.json"
                    test = stage("execution", [str(python), str(directory / "driver.py"),
                                 str(script), case, str(metadata)], args.timeout)
                    if test["status"] == "timed_out":
                        report["outcome"] = "timed_out"
                    elif metadata.exists() and test["exit_code"] in (0, 1):
                        observation = json.loads(metadata.read_text())
                        stdout = (directory / "execution.stdout.txt").read_text(errors="replace")
                        if observation["version"] == version:
                            report["outcome"] = classify(case, observation, stdout)
                        else:
                            report["outcome"] = "environment_failure"
                    else:
                        report["outcome"] = "inconclusive"
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["matches_expectation"] = report["outcome"] == expected
        write_json(directory / "result.json", report)
        if not args.keep_env:
            shutil.rmtree(venv, ignore_errors=True)
    print(case, role, "→", report["outcome"], flush=True)
    print("  Artifacts:", directory, flush=True)
    return report["matches_expectation"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List curated cases and release pairs")
    run = commands.add_parser("run", help="Execute a case or all fixtures")
    run.add_argument("case", choices=list(CASES) + ["all"])
    run.add_argument("--release", choices=["buggy", "fixed", "both"], default="both")
    run.add_argument("--output", type=Path, default=ROOT / "runs")
    run.add_argument("--wheelhouse", type=Path, help="Install offline from a directory of wheels")
    run.add_argument("--timeout", type=int, default=30, help="Execution timeout in seconds")
    run.add_argument("--setup-timeout", type=int, default=180)
    run.add_argument("--keep-env", action="store_true")
    args = parser.parse_args()
    if args.command == "list":
        for case, (package, buggy, fixed) in CASES.items():
            print(case, package, buggy, "→", fixed)
        return 0
    if args.timeout <= 0 or args.setup_timeout <= 0:
        parser.error("Timeouts must be positive")
    cases = list(CASES) if args.case == "all" else [args.case]
    roles = ["buggy", "fixed"] if args.release == "both" else [args.release]
    results = [run_case(case, role, args) for case in cases for role in roles]
    print("Matched expected outcomes: {}/{}".format(sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
