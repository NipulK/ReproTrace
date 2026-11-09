import os
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from api import create_app
from generation import validate_draft, review_hash
from generation import generate
from extraction import ExtractionError
import httpx


def draft():
    return {"status":"draft", "code":"def test_default():\n    assert 1 == 1\n",
            "assertion_rationale":["Test-only mock assertion"], "dependencies":["pytest"],
            "setup_notes":[], "assumptions":[], "missing_information":[]}


class GenerationTests(unittest.TestCase):
    def test_provider_receives_frozen_review_without_running_code(self):
        report={'snapshot':{'repository':'pallets/click','title':'Example','body':'Original report'},
                'review':{'expected_behavior':'Expected','actual_behavior':'Actual'}}
        response=httpx.Response(200,json={'status':'completed','model':'mock-model',
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(draft())}]}]})
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-key'}):
            with patch('generation.httpx.post',return_value=response) as post:
                result=generate(report)
                self.assertFalse(result['executed'])
                request=post.call_args.kwargs['json']
                self.assertEqual(json.loads(request['input'])['saved_review'],report['review'])
                self.assertFalse(request['store'])
            with patch('generation.httpx.post',return_value=httpx.Response(200,json={'status':'incomplete'})):
                with self.assertRaises(ExtractionError):generate(report)

    def test_syntax_and_structure_validation(self):
        self.assertEqual(validate_draft(draft())["status"],"draft")
        for code in ('not python!', 'def example():\n    assert True',
                     'def test_no_assert():\n    pass',
                     'import subprocess\ndef test_bad():\n    assert True',
                     'def test_bad():\n    eval("1")\n    assert True'):
            value=draft();value['code']=code
            with self.subTest(code=code), self.assertRaises((ValueError,SyntaxError)):
                validate_draft(value)

    def test_missing_information_requires_questions_and_no_code(self):
        value=draft();value.update(status='needs_information',code=None,missing_information=['Which input triggers this?'])
        self.assertEqual(validate_draft(value)['status'],'needs_information')
        value['code']='def test_example():\n    assert True'
        with self.assertRaises(ValueError):validate_draft(value)

    def test_saved_review_required_and_download_provenance(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'OPENAI_API_KEY':'test-key'}):
            with TestClient(create_app(folder)) as client:
                report=client.post('/reports',json={'source':'paste','repository':'pallets/click',
                    'title':'Example','body':'A reproducible failure'}).json()
                url='/reports/'+report['id']
                with patch('api.generate') as generate:
                    self.assertEqual(client.post(url+'/tests').status_code,409)
                    generate.assert_not_called()
                review={'expected_behavior':'Expected result','actual_behavior':'Observed result'}
                saved=client.put(url+'/review',json=review).json()
                payload={'draft':draft(),'model':'mock-test-only','executed':False,'validation':'syntax_and_structure_checked'}
                with patch('api.generate',return_value=payload) as generate:
                    result=client.post(url+'/tests')
                    self.assertEqual(result.status_code,201,result.text)
                    self.assertEqual(generate.call_args.args[0]['review'],saved['review'])
                    generated=result.json()
                    self.assertEqual(generated['review_sha256'],review_hash(saved['review']))
                self.assertEqual(client.get(url).json()['review'],saved['review'])
                download=client.get('/tests/'+generated['id']+'/file')
                self.assertEqual(download.status_code,200)
                self.assertIn('Not executed',download.text)
                self.assertTrue(client.get(url+'/tests').json()[0]['review_is_current'])
                client.put(url+'/review',json={**review,'notes':'Changed interpretation'})
                self.assertFalse(client.get(url+'/tests').json()[0]['review_is_current'])
            with TestClient(create_app(folder)) as client:
                self.assertEqual(len(client.get(url+'/tests').json()),1)


if __name__=='__main__': unittest.main()
