import json
import os
import unittest
from unittest.mock import patch

import httpx

from extraction import analyze, configuration, Extraction, ExtractionError
from generation import generate
from gemini import inline_schema
from test_extraction import sample, SNAPSHOT
from test_generation import draft


def response(value):
    return httpx.Response(200, json={"candidates":[{"finishReason":"STOP","content":{
        "parts":[{"text":json.dumps(value)}]}}],"modelVersion":"gemini-mock",
        "usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":20}})


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{'WORKBENCH_AI_PROVIDER':'gemini',
            'GEMINI_API_KEY':'test-google-key','GEMINI_MODEL':'gemini-3.5-flash-lite','WORKBENCH_AI_MODEL':''})
        self.environment.start();self.addCleanup(self.environment.stop)

    def test_selected_provider_requires_its_own_key(self):
        self.assertEqual(configuration()['provider'],'gemini')
        with patch.dict(os.environ,{'GEMINI_API_KEY':'','OPENAI_API_KEY':'existing-openai-key'}):
            self.assertFalse(configuration()['configured'])
            with patch('gemini.httpx.post') as post:
                with self.assertRaises(ExtractionError):analyze(SNAPSHOT)
                post.assert_not_called()

    def test_gemini_extraction_header_schema_and_metadata(self):
        with patch('gemini.httpx.post',return_value=response(sample())) as post:
            result=analyze(SNAPSHOT)
            self.assertEqual(result['provider'],'gemini')
            self.assertEqual(result['usage']['promptTokenCount'],10)
            self.assertNotIn('test-google-key',post.call_args.args[0])
            self.assertEqual(post.call_args.kwargs['headers']['x-goog-api-key'],'test-google-key')
            payload=post.call_args.kwargs['json']
            self.assertIn('generativelanguage.googleapis.com',post.call_args.args[0])
            self.assertNotIn('$ref',json.dumps(payload['generationConfig']['responseJsonSchema']))
            self.assertEqual(json.loads(payload['contents'][0]['parts'][0]['text']),SNAPSHOT)
            self.assertEqual(result['suggestions']['environment']['value'],'Python 3.11.3')

    def test_generation_uses_saved_review_and_does_not_execute(self):
        report={'snapshot':{'repository':'pallets/click',**SNAPSHOT},
                'review':{'expected_behavior':'Expected','actual_behavior':'Actual'}}
        with patch('gemini.httpx.post',return_value=response(draft())) as post:
            result=generate(report)
            self.assertEqual(result['provider'],'gemini')
            self.assertFalse(result['executed'])
            payload=json.loads(post.call_args.kwargs['json']['contents'][0]['parts'][0]['text'])
            self.assertEqual(payload['saved_review'],report['review'])

    def test_invalid_evidence_and_provider_errors(self):
        invalid=sample();invalid['environment']['evidence'][0]['quote']='fabricated source'
        with patch('gemini.httpx.post',return_value=response(invalid)):
            with self.assertRaises(ExtractionError):analyze(SNAPSHOT)
        for status in (400,401,403,404,429,500):
            with patch('gemini.httpx.post',return_value=httpx.Response(status,text='private upstream error')):
                with self.assertRaises(ExtractionError) as error:analyze(SNAPSHOT)
                self.assertNotIn('private upstream',str(error.exception))
                if status==429:self.assertEqual(error.exception.status_code,429)

    def test_blocked_incomplete_and_malformed_responses(self):
        for body in ({'candidates':[]},{'candidates':[{'finishReason':'MAX_TOKENS'}]},
                     {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'not JSON'}]}}]}):
            with patch('gemini.httpx.post',return_value=httpx.Response(200,json=body)):
                with self.assertRaises(ExtractionError):analyze(SNAPSHOT)
        schema=inline_schema(Extraction.model_json_schema())
        self.assertIn('expected_behavior',schema['required'])


if __name__=='__main__':unittest.main()
