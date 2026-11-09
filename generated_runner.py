"""Run generated tests only inside constrained Docker containers."""
import hashlib
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from uuid import uuid4

from workbench import CASES

RESULT_MARKER = "REPROTRACE_RESULT_JSON:"
CONTAINER_RUNNER = '''import subprocess, pathlib, base64, json, sys
try:
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=short",
        "-p", "no:cacheprovider", "--junitxml=/tmp/junit.xml", "/case/test_reproduction.py"], timeout=29)
    code = result.returncode
except subprocess.TimeoutExpired:
    code = 124
path = pathlib.Path("/tmp/junit.xml")
xml = path.read_bytes() if path.is_file() and path.stat().st_size <= 1048576 else b""
print("\\nREPROTRACE_RESULT_JSON:" + json.dumps({"exit_code": code, "junit": base64.b64encode(xml).decode()}), flush=True)
sys.exit(code)
'''


def docker_environment():
    # Never forward application credentials to docker subprocesses/containers.
    return {key: value for key, value in os.environ.items()
            if key in ("PATH", "HOME", "TMPDIR", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")}


def runtime_status():
    if not shutil.which("docker"):
        return {"available": False, "detail": "Install and start Docker Desktop to run generated tests."}
    try:
        result = subprocess.run(["docker", "info", "--format", "{{.OSType}}"],
            capture_output=True, text=True, timeout=5, env=docker_environment())
        if result.returncode == 0 and result.stdout.strip() == "linux":
            return {"available": True, "detail": "Docker Linux containers; generated code runs without network access."}
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"available": False, "detail": "Start Docker Desktop, then retry. Host execution is disabled."}


def run_command(command, folder, name, timeout):
    with (folder / (name + ".stdout.txt")).open("wb") as out:
        with (folder / (name + ".stderr.txt")).open("wb") as err:
            return subprocess.run(command, stdout=out, stderr=err, timeout=timeout,
                                  env=docker_environment())


def parse_junit(path):
    root = ET.fromstring(path.read_bytes())
    cases = list(root.iter("testcase"))
    return {"tests": len(cases),
            "failures": sum(case.find("failure") is not None for case in cases),
            "errors": sum(case.find("error") is not None for case in cases),
            "skipped": sum(case.find("skipped") is not None for case in cases)}


def classify_execution(exit_code, counts):
    if exit_code == 0 and counts["tests"] > 0 and not any(counts[key] for key in ("failures", "errors", "skipped")):
        return "passed"
    if exit_code == 1 and counts["failures"] > 0 and counts["errors"] == 0:
        return "test_failed"
    return "inconclusive"


def execute_pair(code, profile, directory, progress):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    test_path = directory / "test_reproduction.py"
    test_path.write_text(code)
    test_path.chmod(0o644)
    package, buggy, fixed = CASES[profile]
    summary = {"profile": profile, "test_sha256": hashlib.sha256(code.encode()).hexdigest(),
               "isolation": "Docker; no network; non-root; read-only root; memory/CPU/PID limits",
               "results": [], "regression_signal": False,
               "interpretation": "Execution evidence requires review; this is not automatic bug confirmation."}
    for role, version in (("buggy", buggy), ("fixed", fixed)):
        folder = directory / role
        folder.mkdir()
        record = {"role": role, "package": package, "version": version,
                  "outcome": "environment_failure", "exit_code": None}
        started = time.monotonic()
        container = "reprotrace-" + uuid4().hex
        try:
            progress("Preparing " + role + " environment (" + package + " " + version + ")", summary)
            image = "reprotrace/" + package.lower() + ":" + version + "-py311-v2"
            context = folder / "build"
            context.mkdir()
            requirements = [package + "==" + version, "pytest==8.3.5"]
            if package == "Werkzeug":
                requirements.append("MarkupSafe==2.1.5")
            dockerfile = ("FROM python:3.11-slim\nRUN python -m pip install --no-cache-dir " +
                          " ".join(requirements) + "\nWORKDIR /tmp\nCOPY runner.py /opt/reprotrace_runner.py\n")
            (context / "Dockerfile").write_text(dockerfile)
            (context / "runner.py").write_text(CONTAINER_RUNNER)
            build = run_command(["docker", "build", "--tag", image, str(context)], folder, "build", 300)
            if build.returncode:
                raise RuntimeError("Pinned environment build failed; inspect build logs.")
            metadata = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                capture_output=True, text=True, timeout=10, env=docker_environment())
            record["image_id"] = metadata.stdout.strip()
            progress("Running " + role + " test", summary)
            command = ["docker", "create", "--name", container, "--network", "none",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "65534:65534", "--memory", "256m", "--memory-swap", "256m",
                "--cpus", "1", "--pids-limit", "64", "--ulimit", "fsize=1048576:1048576",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=67108864,mode=1777",
                "--log-driver", "local", "--log-opt", "max-size=1m", "--log-opt", "max-file=2",
                "--mount", "type=bind,source=" + str(test_path) + ",target=/case/test_reproduction.py,readonly",
                "--env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "--env", "PYTHONDONTWRITEBYTECODE=1",
                image, "timeout", "--kill-after=1s", "32s", "python", "/opt/reprotrace_runner.py"]
            created = run_command(command, folder, "create", 30)
            if created.returncode:
                raise RuntimeError("Container creation failed; inspect create logs.")
            with (folder / "execution.stdout.txt").open("wb") as execution_out, (folder / "execution.stderr.txt").open("wb") as execution_err:
                attached = subprocess.Popen(["docker", "start", "--attach", container], stdout=execution_out,
                    stderr=execution_err, env=docker_environment())
                deadline = time.monotonic() + 35
                while attached.poll() is None:
                    if time.monotonic() > deadline:
                        record["outcome"] = "timed_out"
                        raise TimeoutError("Container exceeded its execution limit.")
                    if sum(path.stat().st_size for path in folder.glob("execution.*.txt")) > 2 * 1024 * 1024:
                        record["outcome"] = "output_limit"
                        raise TimeoutError("Test exceeded the output limit.")
                    time.sleep(0.1)
            if sum(path.stat().st_size for path in folder.glob("execution.*.txt")) > 2 * 1024 * 1024:
                record["outcome"] = "output_limit"
                raise TimeoutError("Test exceeded the output limit.")
            if attached.returncode in (125, 126, 127, 128):
                raise RuntimeError("Container failed to start. Inspect execution stderr.")
            inspect = subprocess.run(["docker", "inspect", container, "--format", "{{json .State}}"],
                capture_output=True, text=True, timeout=10, env=docker_environment())
            state = json.loads(inspect.stdout)
            record["exit_code"] = state["ExitCode"]
            if state["ExitCode"] in (124, 137) and not state.get("OOMKilled"):
                record["outcome"] = "timed_out"
            elif state.get("OOMKilled"):
                record["outcome"] = "memory_limit"
            else:
                stdout = (folder / "execution.stdout.txt").read_text(errors="replace")
                prefix, marker, message = stdout.rpartition(RESULT_MARKER)
                if marker:
                    structured = json.loads(message.strip())
                    xml = base64.b64decode(structured["junit"], validate=True)
                    if len(xml) > 1048576 or structured["exit_code"] != record["exit_code"]:
                        raise ValueError("Execution report mismatch")
                    (folder / "junit.xml").write_bytes(xml)
                    counts = parse_junit(folder / "junit.xml")
                    record["counts"] = counts
                    record["outcome"] = classify_execution(record["exit_code"], counts)
                    (folder / "execution.stdout.txt").write_text(prefix)
                else:
                    record["outcome"] = "inconclusive"
        except (RuntimeError, TimeoutError, subprocess.TimeoutExpired, OSError, ValueError, ET.ParseError) as exc:
            record["error"] = str(exc)
        finally:
            subprocess.run(["docker", "rm", "--force", container], capture_output=True,
                           timeout=15, env=docker_environment())
            if 'attached' in locals() and attached.poll() is None:
                try:
                    attached.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    attached.kill()
                    attached.wait()
            record["duration_seconds"] = round(time.monotonic() - started, 3)
            # Bound the amount returned to the dashboard, retaining original files.
            record["logs"] = {path.name: path.open("rb").read(200000).decode(errors="replace")
                              for path in folder.glob("*.txt")}
            summary["results"].append(record)
            progress(role.capitalize() + " execution finished", summary)
    summary["regression_signal"] = (summary["results"][0]["outcome"] == "test_failed" and
                                    summary["results"][1]["outcome"] == "passed")
    return summary
