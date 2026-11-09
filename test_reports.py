import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from api import create_app


class ReportTests(unittest.TestCase):
    def test_snapshot_preserved_across_review_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_app(folder)) as client:
                response = client.post('/reports', json={'source':'paste', 'repository':'pallets/click',
                    'title':'Example report', 'body':'Original text with <script>markup</script>'})
                self.assertEqual(response.status_code, 201)
                original = response.json()
                self.assertFalse(any(original['review'].values()))
                review = client.put('/reports/'+original['id']+'/review',
                                    json={'expected_behavior':'An explicitly reviewed expectation'}).json()
                self.assertEqual(review['snapshot'], original['snapshot'])
                self.assertEqual(review['snapshot_sha256'], original['snapshot_sha256'])
                self.assertEqual(len(client.get('/reports').json()), 1)
                self.assertEqual(client.get('/reports/'+str(uuid4())).status_code,404)
            with TestClient(create_app(folder)) as client:
                result=client.get('/reports/'+original['id']).json()
                self.assertEqual(result['review']['expected_behavior'],'An explicitly reviewed expectation')

    def test_reject_invalid_sources_before_network(self):
        with tempfile.TemporaryDirectory() as folder, TestClient(create_app(folder)) as client:
            with patch('reports.httpx.get') as get:
                for url in ('http://localhost/secret','https://github.com/other/repo/issues/1',
                            'https://github.com/pallets/click/issues/1?redirect=1',
                            'https://github.com/pallets/click/pull/1'):
                    self.assertEqual(client.post('/reports',json={'source':'github','issue_url':url}).status_code,422)
                self.assertEqual(client.post('/reports',json={'source':'paste','title':' ','body':'x'}).status_code,422)
                get.assert_not_called()

    def test_github_import_and_upstream_errors(self):
        with tempfile.TemporaryDirectory() as folder, TestClient(create_app(folder)) as client:
            payload={'source':'github','issue_url':'https://github.com/pallets/click/issues/2500'}
            with patch('reports.httpx.get',return_value=httpx.Response(200,json={'title':'Empty default','body':'Example text'})) as get:
                response=client.post('/reports',json=payload)
                self.assertEqual(response.status_code,201)
                self.assertEqual(response.json()['snapshot']['issue_number'],2500)
                self.assertEqual(get.call_args.args[0],'https://api.github.com/repos/pallets/click/issues/2500')
            for upstream, body, expected in ((403,{},429),(404,{},404),(500,{},502),
                      (200,{'title':'PR','pull_request':{}},422),(200,{'invalid':'response'},502)):
                with patch('reports.httpx.get',return_value=httpx.Response(upstream,json=body)):
                    self.assertEqual(client.post('/reports',json=payload).status_code,expected)
            self.assertEqual(len(client.get('/reports').json()),1)


if __name__=='__main__':
    unittest.main()
