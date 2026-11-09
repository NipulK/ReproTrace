import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from api import create_app


class ApiTests(unittest.TestCase):
    def test_validation_and_missing_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_app(folder)) as client:
                self.assertEqual(client.get('/health').status_code, 200)
                self.assertEqual(len(client.get('/cases').json()), 5)
                for body in ({'case': 'unknown'}, {'case': 'click-2500', 'release': 'bad'},
                             {'case': 'click-2500', 'timeout': 0}):
                    self.assertEqual(client.post('/runs', json=body).status_code, 422)
                self.assertEqual(client.get('/runs/' + str(uuid4())).status_code, 404)

    def test_completed_results_artifacts_and_persistence(self):
        def fake_runner(command, **kwargs):
            output = Path(command[command.index('--output') + 1]) / 'attempt-1'
            output.mkdir(parents=True)
            (output / 'result.json').write_text(json.dumps({'outcome': 'reported_symptom_observed'}))
            (output / 'execution.stdout.txt').write_text('evidence')
            (output / 'secret.txt').write_text('hidden')
            return type('Result', (), {'returncode': 0})()
        with tempfile.TemporaryDirectory() as folder:
            with patch('api.subprocess.run', side_effect=fake_runner):
                with TestClient(create_app(folder)) as client:
                    response = client.post('/runs', json={'case': 'click-2500', 'release': 'buggy'})
                    self.assertEqual(response.status_code, 202)
                    run_id = response.json()['id']
                    url = '/runs/' + run_id
                    for _ in range(100):
                        if client.get(url).json()['status'] == 'completed':
                            break
                        time.sleep(.01)
                    result = client.get(url + '/results').json()
                    self.assertEqual(result['status'], 'completed')
                    self.assertEqual(result['results'][0]['outcome'], 'reported_symptom_observed')
                    files = client.get(url + '/artifacts').json()
                    item = next(x for x in files if x['path'].endswith('execution.stdout.txt'))
                    self.assertEqual(client.get(item['url']).text, 'evidence')
                    self.assertFalse(any(x['path'].endswith('secret.txt') for x in files))
                    self.assertEqual(client.get(url + '/artifacts/attempts/attempt-1/secret.txt').status_code, 404)
            with TestClient(create_app(folder)) as client:
                self.assertEqual(client.get('/runs/' + run_id).json()['status'], 'completed')

    def test_worker_error_is_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('api.subprocess.run', side_effect=OSError('test failure')):
                with TestClient(create_app(folder)) as client:
                    run_id = client.post('/runs', json={'case': 'click-2500'}).json()['id']
                    for _ in range(100):
                        result = client.get('/runs/' + run_id).json()
                        if result['status'] == 'failed':
                            break
                        time.sleep(.01)
                    self.assertEqual(result['status'], 'failed')
                    self.assertIn('test failure', result['error'])


if __name__ == '__main__':
    unittest.main()
