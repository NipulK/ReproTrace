import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from api import create_app
from generated_runner import classify_execution, parse_junit
from test_generation import draft


class ExecutionTests(unittest.TestCase):
    def test_outcome_distinguishes_failures_errors_and_empty_runs(self):
        counts={'tests':1,'failures':0,'errors':0,'skipped':0}
        self.assertEqual(classify_execution(0,counts),'passed')
        self.assertEqual(classify_execution(1,{**counts,'failures':1}),'test_failed')
        for code,data in ((2,counts),(0,{**counts,'tests':0}),(0,{**counts,'skipped':1}),
                          (1,{**counts,'errors':1})):
            self.assertEqual(classify_execution(code,data),'inconclusive')

    def test_parse_junit_counts_assertion_and_import_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'junit.xml'
            path.write_text('<testsuites><testsuite><testcase><failure/></testcase><testcase><error/></testcase></testsuite></testsuites>')
            self.assertEqual(parse_junit(path),{'tests':2,'failures':1,'errors':1,'skipped':0})

    def test_api_execution_history_stale_review_and_runtime_failure(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'OPENAI_API_KEY':'test-key'}):
            with TestClient(create_app(folder)) as client:
                report=client.post('/reports',json={'source':'paste','repository':'pallets/click','title':'Example','body':'Example'}).json()
                url='/reports/'+report['id']
                client.put(url+'/review',json={'expected_behavior':'Expected','actual_behavior':'Actual'})
                with patch('api.generate',return_value={'draft':draft(),'model':'mock','executed':False}):
                    test=client.post(url+'/tests').json()
                endpoint='/tests/'+test['id']+'/runs'
                with patch('api.runtime_status',return_value={'available':False,'detail':'Start Docker'}):
                    self.assertEqual(client.post(endpoint,json={'profile':'click-2500'}).status_code,503)
                self.assertEqual(client.post(endpoint,json={'profile':'arbitrary-command'}).status_code,422)
                self.assertEqual(client.post(endpoint,json={'profile':'werkzeug-2842'}).status_code,422)
                payload={'results':[{'role':'buggy','outcome':'test_failed'},{'role':'fixed','outcome':'passed'}],'regression_signal':True}
                with patch('api.runtime_status',return_value={'available':True}),patch('api.execute_pair',return_value=payload):
                    response=client.post(endpoint,json={'profile':'click-2500'})
                    self.assertEqual(response.status_code,202,response.text)
                    for _ in range(100):
                        result=client.get(response.json()['status_url']).json()
                        if result['status']=='completed':break
                        time.sleep(.01)
                    self.assertTrue(result['payload']['regression_signal'])
                self.assertEqual(len(client.get(endpoint).json()),1)
                client.put(url+'/review',json={'expected_behavior':'Changed','actual_behavior':'Actual'})
                self.assertEqual(client.post(endpoint,json={'profile':'click-2500'}).status_code,409)


if __name__=='__main__':unittest.main()
