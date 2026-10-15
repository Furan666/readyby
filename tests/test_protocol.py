"""Exercise the real MCP HTTP transport and the separate review boundary."""
import json
import logging
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient
from backend.server import create_app

logging.disable(logging.CRITICAL)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.app = create_app(self.path)
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8877')
        self.client.__enter__()
        self.headers = {'Accept':'application/json, text/event-stream', 'Content-Type':'application/json'}
        self.counter = 0
        reply = self.rpc('initialize', {'protocolVersion':'2025-11-25', 'capabilities':{}, 'clientInfo':{'name':'ReadyBy-test','version':'0.1'}})
        self.assertEqual(reply['result']['protocolVersion'], '2025-11-25')
        self.headers['MCP-Protocol-Version'] = '2025-11-25'
        response = self.client.post('/mcp', headers=self.headers, json={'jsonrpc':'2.0','method':'notifications/initialized'})
        self.assertEqual(response.status_code, 202)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def rpc(self, method, params):
        self.counter += 1
        response = self.client.post('/mcp', headers=self.headers, json={'jsonrpc':'2.0', 'id':self.counter, 'method':method, 'params':params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def call(self, name, arguments=None):
        reply = self.rpc('tools/call', {'name':name, 'arguments':arguments or {}})
        self.assertNotIn('error', reply, reply)
        self.assertFalse(reply['result'].get('isError', False), reply)
        return reply['result']['structuredContent']

    def review_headers(self):
        token = self.client.get('/api/session').json()['review_token']
        return {'X-ReadyBy-Token': token, 'Origin':'http://127.0.0.1:8877'}

    def test_real_tools_and_no_commit_tool(self):
        tools = self.rpc('tools/list', {})['result']['tools']
        self.assertEqual({t['name'] for t in tools}, {'read_departure','draft_update','recovery_choices'})
        self.assertEqual(self.call('read_departure')['plan']['objective']['finish'], 59)
        bad = self.rpc('tools/call', {'name':'commit', 'arguments':{}})
        self.assertTrue('error' in bad or bad['result'].get('isError'))

    def test_round_trip_and_reviewed_recovery(self):
        initial = self.call('read_departure')
        interruption = self.call('draft_update', {'changes':{'now':48, 'completed':['alex_bathroom','lunch'], 'blocks':[{'resource':'Bathroom','start':48,'end':52}]}})
        self.assertEqual(interruption['plan']['status'], 'INFEASIBLE')
        self.assertEqual(interruption['earliest_deadline'], 65)
        self.assertEqual(self.call('read_departure'), initial, 'Draft must not change active state')
        body = {'draft_id':interruption['id'], 'base_revision':interruption['base_revision']}
        self.assertEqual(self.client.post('/api/commit', json=body).status_code, 403)
        headers = self.review_headers()
        self.assertEqual(self.client.post('/api/commit', headers=headers, json=body).status_code, 200)
        options = self.call('recovery_choices')['choices']
        self.assertEqual(len(options), 2)
        wash = next(item for item in options if item['title'] == 'Use a 3-minute wash')
        self.assertEqual(wash['plan']['objective']['finish'], 60)
        self.assertEqual(self.call('read_departure')['plan']['status'], 'INFEASIBLE')
        body = {'draft_id':wash['id'], 'base_revision':wash['base_revision']}
        self.assertEqual(self.client.post('/api/commit', headers=headers, json=body).status_code, 200)
        self.assertEqual(self.client.post('/api/commit', headers=headers, json=body).status_code, 409)
        final = self.call('read_departure')
        self.assertEqual(final['snapshot']['completed'], ['alex_bathroom','lunch'])
        self.assertEqual(final['plan']['objective']['finish'], 60)
        self.assertEqual(final['revision'], initial['revision'] + 2)

    def test_invalid_tool_arguments_do_not_mutate(self):
        initial = self.call('read_departure')
        for changes in ({'now':True}, {'publish':'yes'}, {'completed':'lunch'}):
            response = self.rpc('tools/call', {'name':'draft_update','arguments':{'changes':changes}})
            self.assertTrue(response['result'].get('isError'), response)
        self.assertEqual(self.call('read_departure'), initial)

    def test_host_origin_and_review_token_boundaries(self):
        headers = self.review_headers()
        for origin in ['https://example.org', 'null']:
            response = self.client.post('/api/reset', headers={**headers,'Origin':origin})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/session', headers={'Host':'evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/reset',headers={**headers,'sec-fetch-site':'cross-site'}).status_code,403)
        self.assertEqual(self.client.post('/api/reset',headers={**headers,'X-ReadyBy-Token':'wrong'}).status_code,403)
        self.assertEqual(self.call('read_departure')['revision'], 1)

    def test_non_ascii_token_and_chunked_body_are_refused(self):
        response = self.client.post('/api/reset', headers=[(b'X-ReadyBy-Token', b'\xff')])
        self.assertEqual(response.status_code, 403)
        payload = json.dumps({'draft_id':'x' * 40000, 'base_revision':1}).encode()
        response = self.client.post('/api/commit', headers={**self.review_headers(), 'Content-Type':'application/json'}, content=iter([payload]))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.call('read_departure')['revision'], 1)

    def test_invalid_protocol_and_oversize_body(self):
        response = self.client.post('/mcp', headers={**self.headers,'MCP-Protocol-Version':'not-a-version'}, json={'jsonrpc':'2.0','id':99,'method':'tools/list'})
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/mcp',headers=self.headers,content='x' * 32769)
        self.assertEqual(response.status_code,413)


if __name__ == '__main__':
    unittest.main()
