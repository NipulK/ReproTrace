import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from api import create_app
from extraction import analyze, validate_extraction, ExtractionError

SNAPSHOT = {"title": "Empty default missing", "body": "Expected: show empty default. Actual: no default. Python 3.11.3."}


def sample():
    unknown = {"value": None, "evidence": []}
    return {
        "expected_behavior": {"value": "Show empty default.", "evidence": [
            {"source": "body", "quote": "Expected: show empty default."}]},
        "actual_behavior": {"value": "No default is shown.", "evidence": [
            {"source": "body", "quote": "Actual: no default."}]},
        "reproduction_steps": copy.deepcopy(unknown),
        "environment": {"value": "Python 3.11.3", "evidence": [
            {"source": "body", "quote": "Python 3.11.3."}]},
        "missing_information": ["Which Click version was used?"], "warnings": [],
    }


def response(data=None):
    return httpx.Response(200, json={"status": "completed", "id": "test-response", "model": "test-model",
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(data or sample())}]}]})


class ExtractionTests(unittest.TestCase):
    def test_unknowns_stay_unknown_and_quotes_match(self):
        result = validate_extraction(sample(), SNAPSHOT)
        self.assertIsNone(result["reproduction_steps"]["value"])
        self.assertEqual(result["environment"]["value"], "Python 3.11.3")

    def test_invalid_evidence_is_rejected(self):
        for mutation in ("absent_quote", "missing_evidence", "unknown_with_evidence", "extra_field"):
            value = sample()
            if mutation == "absent_quote":
                value["environment"]["evidence"][0]["quote"] = "Python 3.12 on Linux"
            elif mutation == "missing_evidence":
                value["environment"]["evidence"] = []
            elif mutation == "unknown_with_evidence":
                value["environment"]["value"] = None
            else:
                value["execute_command"] = "do not execute"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_extraction(value, SNAPSHOT)

    def test_provider_payload_and_valid_output(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "WORKBENCH_AI_MODEL": "test-model"}):
            with patch("extraction.httpx.post", return_value=response()) as post:
                result = analyze(SNAPSHOT)
                payload = post.call_args.kwargs["json"]
                self.assertFalse(payload["store"])
                self.assertEqual(json.loads(payload["input"]), SNAPSHOT)
                self.assertTrue(payload["text"]["format"]["strict"])
                self.assertEqual(result["usage"]["output_tokens"], 20)

    def test_provider_failures_are_safe(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            for status in (401, 403, 429, 500):
                with patch("extraction.httpx.post", return_value=httpx.Response(status, text="sensitive upstream body")):
                    with self.assertRaises(ExtractionError) as error:
                        analyze(SNAPSHOT)
                    self.assertNotIn("sensitive upstream body", str(error.exception))
            bodies = [{"status": "incomplete"}, {"status": "completed", "output": []},
                      {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}]
            for body in bodies:
                with patch("extraction.httpx.post", return_value=httpx.Response(200, json=body)):
                    with self.assertRaises(ExtractionError):
                        analyze(SNAPSHOT)
            with patch("extraction.httpx.post", side_effect=httpx.ReadTimeout("private error")):
                with self.assertRaises(ExtractionError) as error:
                    analyze(SNAPSHOT)
                self.assertEqual(error.exception.status_code, 504)

    def test_api_persists_analysis_without_touching_review(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                with TestClient(create_app(folder)) as client:
                    report = client.post("/reports", json={"source": "paste", "repository": "pallets/click", **SNAPSHOT}).json()
                    url = "/reports/" + report["id"]
                    client.put(url + "/review", json={"notes": "Human-only private note"})
                    with patch("extraction.httpx.post", return_value=response()) as post:
                        result = client.post(url + "/analyze")
                        self.assertEqual(result.status_code, 201, result.text)
                        self.assertNotIn("Human-only", post.call_args.kwargs["json"]["input"])
                    updated = client.get(url).json()
                    self.assertEqual(updated["review"]["notes"], "Human-only private note")
                    self.assertEqual(updated["snapshot_sha256"], report["snapshot_sha256"])
                    with patch("extraction.httpx.post", return_value=httpx.Response(429)):
                        self.assertEqual(client.post(url + "/analyze").status_code, 429)
                    rows = client.get(url + "/analyses").json()
                    self.assertEqual([row["status"] for row in rows], ["failed", "completed"])
                with TestClient(create_app(folder)) as client:
                    self.assertEqual(len(client.get(url + "/analyses").json()), 2)

    def test_missing_key_is_explicit_and_never_calls_provider(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with TestClient(create_app(folder)) as client, patch("extraction.httpx.post") as post:
                report = client.post("/reports", json={"source": "paste", "repository": "pallets/click", **SNAPSHOT}).json()
                self.assertFalse(client.get("/ai/config").json()["configured"])
                self.assertEqual(client.post('/reports/' + report['id'] + '/analyze').status_code, 503)
                post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
