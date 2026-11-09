# ReproTrace — Bug Reproduction Workbench

## FastAPI service

### AI providers

### Generate reproduction tests from a saved review

After saving expected and actual behavior in the report review, click
Generate pytest draft. Unsaved review edits must be saved first. The request
sends the original title/body, repository name, and saved review to the selected provider.
It does not fetch repository code, inspect fixes, install packages, or execute
the generated code. API availability and version assumptions must be reviewed.

POST /reports/{id}/tests generates and stores a draft or clarification questions.
GET /reports/{id}/tests lists the latest ten attempts. GET /tests/{id}/file
downloads a generated .py draft. Each record preserves the source snapshot
hash, complete saved review, review hash, model metadata, and prompt version.
Drafts become marked outdated when the saved review changes. A provider failure
is recorded separately and does not replace a previously successful draft.

Drafts require valid Python syntax, a top-level test_ function and an assert.
A restrictive import/call filter rejects some obvious side effects. This is NOT
a sandbox, security guarantee, or validation of correctness: generated drafts
remain unexecuted and must not be passed to the trusted-fixture CLI. An isolated
execution worker and human-reviewed test oracle are separate future milestones.
No endpoint runs generated code. A model can return needs_information with no
code when the reviewed report is insufficient.

### Extraction configuration

1. Install requirements-api.txt (includes python-dotenv).
2. Copy .env.example to .env only if .env does not already exist.
3. For Gemini, obtain a key at https://aistudio.google.com/apikey and save:

```dotenv
WORKBENCH_AI_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
```

In AI Studio, sign in, create/select a project, and create an API key. Use a
project showing Free tier for free-tier testing. Do not enable billing merely to
use this MVP. Availability and quotas depend on your account and region. API
requests use the selected project's billing tier: the app cannot enforce or
verify that it is free. No automatic paid-provider fallback occurs.

Google currently lists Gemini 3.5 Flash-Lite text input/output as free of charge
on its free tier: https://ai.google.dev/gemini-api/docs/pricing
Free-tier data may be used to improve Google products; use public/non-sensitive
reports and review Google's terms. Keys stay server-side and are never returned
by GET /ai/config. Gemini credentials are sent in headers, not request URLs.

To use OpenAI instead, set WORKBENCH_AI_PROVIDER=openai, OPENAI_API_KEY, and
OPENAI_MODEL=gpt-4o-mini. WORKBENCH_AI_MODEL, if explicitly set, overrides either
provider's model; remove that legacy setting when switching providers. The code
uses OpenAI by default when no provider is selected, for backward compatibility;
the example and project .env explicitly select Gemini.

4. Start the server from this directory:

```sh
.api-venv/bin/python -m uvicorn api:app --env-file .env --host 127.0.0.1 --port 8000
```

VS Code's Workbench API configuration also loads .env. Restart after changing
credentials. GET /ai/config reports selected provider, model, and key presence,
not whether credentials are valid or free quota is available.

Open a saved report at /intake and click Analyze report. This sends only the
saved title/body to the selected provider, not manual review notes. OpenAI uses
Responses with store=false; Gemini uses generateContent with a JSON schema.
No repository fetch, tool execution, or automatic patching occurs. Output is
validated with the same Pydantic and evidence checks for either provider.
Provider documentation:
https://developers.openai.com/api/docs/guides/structured-outputs
https://ai.google.dev/gemini-api/docs/structured-output

Each populated extracted field needs verbatim title/body evidence. Unknown
fields stay null. Exact quote matching prevents invented citations; it does not
prove that an interpretation is correct. Read the suggestion and its quote before
applying it. Applying fills a draft field only. Replacing nonempty draft text
requires a second click; Save review persists your chosen edits. Clarification
questions are appended explicitly. Source snapshots and existing saved reviews
are never changed by analysis itself.

POST /reports/{id}/analyze returns a saved analysis, or a clear provider error.
GET /reports/{id}/analyses returns the latest ten attempts, including failures.
Successful attempts store suggestions, prompt version, model/response identifiers,
usage metadata, duration, and snapshot hash. Failed attempts store a sanitized
error. Missing-key requests return 503 without contacting the provider. One analysis
runs at a time per API process; the request waits for it, with a 90-second HTTP
timeout. This local MVP does not resume in-flight analysis after a crash.

Tests use mocked OpenAI and Gemini responses and do not incur API charges. A live extraction
must be checked separately after configuring a valid key; no placeholder output
is shown as real AI analysis.

### Report intake

Open http://127.0.0.1:8000/intake to paste a report or import a public issue
from pallets/click or pallets/werkzeug. Source title/body, capture time,
upstream timestamps (when available), and a snapshot SHA-256 are saved.
Human review fields are editable separately; the original snapshot stays intact.
Imports use GitHub's public API without credentials and report rate-limit errors.
Only current title/body are captured, not attachments, comments, or historical
versions. A capture timestamp is not an issue-creation-time snapshot, so these
imports must not be treated as leakage-free historical benchmark inputs.

API: POST /reports accepts either:

```json
{"source":"github","issue_url":"https://github.com/pallets/click/issues/2500"}
```

or source=paste with repository, title, and body. GET /reports lists captures;
GET /reports/{id} returns the snapshot and review; PUT /reports/{id}/review
replaces the review fields. Unknown details start empty, and no AI extraction or
automatic execution occurs in this stage. To run a curated fixture, return to
Experiments. Report-linked generated tests are a future milestone.

The local dashboard is at http://127.0.0.1:8000/. Select a case and release,
start an experiment, and inspect its observations and downloadable evidence.
Active runs refresh every two seconds. Recent runs are loaded from SQLite;
the selected run is included in the URL so it can be bookmarked or reopened.
The frontend uses HTML, CSS, and vanilla JavaScript served by FastAPI, with no
Node build or additional frontend dependencies. AI generation is a later step;
this dashboard executes the five manually curated fixtures.

From this directory:

```sh
python3 -m venv .api-venv
.api-venv/bin/python -m pip install -r requirements-api.txt
.api-venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/docs for interactive API documentation. VS Code also
has a `Workbench API` debug configuration (requires the Python debugger extension).

```sh
curl -X POST http://127.0.0.1:8000/runs \
  -H 'Content-Type: application/json' \
  -d '{"case":"werkzeug-2842","release":"both","timeout":30}'
curl http://127.0.0.1:8000/runs
```

The POST returns HTTP 202 and a run ID. Substitute that ID below:

```sh
curl http://127.0.0.1:8000/runs/RUN_ID
curl http://127.0.0.1:8000/runs/RUN_ID/results
curl http://127.0.0.1:8000/runs/RUN_ID/artifacts
```

Endpoints: GET /health, GET /cases, POST /runs, GET /runs (bounded `limit`),
GET /runs/{id}, GET /runs/{id}/results, GET /runs/{id}/artifacts, and downloads
at the URLs returned in the artifact listing.

Jobs move from queued to running to completed or failed. `completed` means the
runner produced its results, not that the bug was reproduced. Inspect each
result's outcome and matches_expectation. Results are exposed after completion;
log artifacts can be read while the job is running. SQLite and artifacts persist
under api-data/. Interrupted jobs are marked interrupted on next startup.

Use one Uvicorn process, without reload, for this local MVP. The queue accepts
up to ten pending/active jobs and executes one at a time. Graceful shutdown waits
for queued jobs. This is not a durable external queue or an authenticated public
service; keep it on localhost. The API accepts only the curated case IDs, never
user-supplied commands, paths, or arbitrary code.

Set WORKBENCH_DATA to override storage and WORKBENCH_WHEELHOUSE to install from
an offline wheel directory. Run tests with:

```sh
.api-venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

## CLI runner (next milestone)

The runner creates a fresh virtual environment per attempt and saves artifacts
under `runs/`. Run from this directory:

```sh
python3 workbench.py list
python3 workbench.py run werkzeug-2842
python3 workbench.py run all
```

Both releases run by default. Use `--release buggy` or `--release fixed` for one.
Use `--output /absolute/path` to choose artifact storage, `--timeout 30` for the
execution limit, and `--setup-timeout 180` for each setup stage. The temporary
environment is deleted after execution; use `--keep-env` to retain it.

Each attempt saves the script, SHA-256 hash, pinned requirements, package
inventory (`freeze.stdout.txt`), structured observation, stage logs, and
`result.json`. The runner exits 0 only when all selected attempts match their
expected outcomes. Thus an expected buggy-release failure counts as a successful
fixture check, while installation failure never counts as reproduction.

The report distinguishes a matching known symptom, corrected behavior,
inconclusive execution, timeout, and environment failure. Matching a symptom
uses fixture-specific checks; this is not a general AI bug validator.

For offline installation, put all required package wheels in a directory:

```sh
python3 workbench.py run all --wheelhouse /absolute/path/to/wheels
python3 -m unittest discover -s . -p 'test_workbench.py' -v
```

This runner executes only the five trusted fixtures. A virtual environment
separates Python dependencies; it does not sandbox code or restrict its network
or operating-system permissions. Add a VM-based worker before accepting
arbitrary repositories or AI-generated code. These cases were verified with
Python 3.9.6; another Python version should be recorded and checked independently.

Verified on Python 3.9.6 on macOS. These scripts use published releases,
not source checkouts. They require no running web server or network after
installation. Run commands from this directory. Use a separate environment
for this historical-package exercise, not an existing application environment.

## Setup

```sh
python3 -m venv .venv
.venv/bin/python --version
```

For every case: install the buggy release, execute the case, read the failure,
install the fixed release, and execute the exact same case again. Exit 1 is
expected on the buggy release; exit 0 and PASS are expected on the fixed one.
An installation failure is not a successful reproduction.

## 1. Werkzeug #2842: None conversion raises instead of returning default

Source: https://github.com/pallets/werkzeug/issues/2842
Fix: https://github.com/pallets/werkzeug/pull/2843

```sh
.venv/bin/python -m pip install Werkzeug==3.0.1 MarkupSafe==2.1.5
.venv/bin/python cases/reproduce.py werkzeug-2842
.venv/bin/python -m pip install Werkzeug==3.0.2
.venv/bin/python cases/reproduce.py werkzeug-2842
```

Input: MultiDict containing quantity=None, converted using int, default=7.
Before: TypeError from int(None). After: returns 7.
This example adapts the original report to use an explicit default.

## 2. Werkzeug #2834: disabled slash merging still redirects

Source: https://github.com/pallets/werkzeug/issues/2834

```sh
.venv/bin/python -m pip install Werkzeug==3.0.1 MarkupSafe==2.1.5
.venv/bin/python cases/reproduce.py werkzeug-2834
.venv/bin/python -m pip install Werkzeug==3.0.2
.venv/bin/python cases/reproduce.py werkzeug-2834
```

Create Map(), then set map.merge_slashes=False, then add /api/path.
Match /api//path. Before: 308 redirect. After: 404 Not Found.
Setting the property after construction is essential for this reproduction;
passing merge_slashes=False in the Map constructor does not trigger it.
The original Flask example is reduced to Werkzeug routing directly.

## 3. Werkzeug #2980: invalid typed cache value remains a string

Source: https://github.com/pallets/werkzeug/issues/2980
Release evidence: https://github.com/pallets/werkzeug/blob/3.1.0/CHANGES.rst

```sh
.venv/bin/python -m pip install Werkzeug==3.0.6 MarkupSafe==2.1.5
.venv/bin/python cases/reproduce.py werkzeug-2980
.venv/bin/python -m pip install Werkzeug==3.1.0
.venv/bin/python cases/reproduce.py werkzeug-2980
```

Parse max-age=invalid and inspect .max_age.
Before: string 'invalid'. After: None.
This is one incorrect-value behavior within a broader cache-control cleanup,
not an independent minimal issue report. Retain that distinction in your data.

## 4. Click #2500: empty default missing from help

Source: https://github.com/pallets/click/issues/2500
Release evidence: https://github.com/pallets/click/blob/8.1.8/CHANGES.rst

```sh
.venv/bin/python -m pip install click==8.1.7
.venv/bin/python cases/reproduce.py click-2500
.venv/bin/python -m pip install click==8.1.8
.venv/bin/python cases/reproduce.py click-2500
```

Create an option with default="" and show_default=True. Invoke --help with
CliRunner. Before: no displayed default. After: [default: ""].
This is a semantic output failure; the CLI itself exits successfully on both
versions. Our assertion makes the buggy reproduction process exit 1.

## 5. Click #2294: reused custom argument decorator loses its class

Source: https://github.com/pallets/click/issues/2294
Release evidence: https://github.com/pallets/click/blob/8.1.8/CHANGES.rst

```sh
.venv/bin/python -m pip install click==8.1.3
.venv/bin/python cases/reproduce.py click-2294
.venv/bin/python -m pip install click==8.1.4
.venv/bin/python cases/reproduce.py click-2294
```

Define a custom Argument accepting help, create one decorator with cls set to
that class, and apply the same decorator to two commands.
Before: second application raises TypeError for unexpected keyword help.
After: both commands contain CustomArgument instances.

## Evidence and next steps

verification.json records all ten executions, including stdout, stderr, and
exit codes. Preserve these as development fixtures, not a held-out benchmark:
the reproduction scripts were written with knowledge of the fixes.

After each independent verification, record Python version, operating system,
pip freeze, input script hash, command, timestamps, and result. For example:

```sh
.venv/bin/python -m pip freeze > environment.txt
.venv/bin/python cases/reproduce.py click-2294 > stdout.txt 2> stderr.txt
```

For source-level experiments, clone the official repositories into a separate
working directory, check out the matching release tag, and record git rev-parse
HEAD. Install the selected checkout in a clean environment. Do not assume the
release pair identifies the exact fixing commit. Use the linked fix history
when you later need adjacent pre-fix/post-fix commits.

Build the CLI runner next: accept case ID and version, prepare an environment,
run this script, record observations, and export artifacts. These are trusted
manual fixtures; running arbitrary repository code requires the separate
isolated-worker architecture described in the project roadmap.
