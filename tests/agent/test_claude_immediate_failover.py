"""Offline production-pool regression: no provider requests or real auth-store writes."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from agent.credential_pool import CredentialPool, PooledCredential
from agent.agent_runtime_helpers import recover_with_credential_pool


class ClaudeImmediateFailover(unittest.TestCase):
    def make_agent(self, count=3):
        entries = [PooledCredential.from_dict('anthropic', {
            'id': f'account-{i}', 'label': f'claude-{i+1}', 'priority': i,
            'source': 'manual', 'auth_type': 'api_key', 'access_token': f'offline-key-{i}',
            'base_url': 'https://api.anthropic.com',
        }) for i in range(count)]
        pool = CredentialPool('anthropic', entries)
        # Only external persistence/singleton seams are replaced; selection, cooldowns,
        # error identity, rotation and recovery are the deployed production modules.
        pool._persist = lambda **kwargs: None
        pool._sync_entry_from_auth_store = lambda entry: entry
        first = pool.select()
        agent = SimpleNamespace(provider='anthropic', model='claude-sonnet-5-5',
            base_url='https://api.anthropic.com', _credential_pool=pool,
            _credential_pool_entry_id=first.id, api_key=first.runtime_api_key)
        agent.notices = []
        agent.tool_progress_callback = lambda *args, **kw: agent.notices.append((args, kw))
        def swap(entry):
            agent.api_key = entry.runtime_api_key
            agent._credential_pool_entry_id = entry.id
            return True
        agent._swap_credential = swap
        return agent

    def test_first_429_tries_next_before_backoff_and_notifies(self):
        agent = self.make_agent()
        reset = time.time() + 600
        recovered, retried = recover_with_credential_pool(agent, status_code=429,
            has_retried_429=False, error_context={'reset_at': reset, 'message': 'Too many requests'})
        self.assertTrue(recovered, 'first Claude 429 must rotate before sleeping')
        self.assertFalse(retried)
        self.assertEqual(agent._credential_pool_entry_id, 'account-1')
        self.assertEqual(agent._credential_pool.entries()[0].model_cooldowns[agent.model], reset)
        self.assertEqual(agent.notices[0][0][0], 'provider.account')
        self.assertIn('Switched to claude-2', agent.notices[0][0][2])

    def test_all_limited_each_distinct_account_once_and_earliest_reset(self):
        agent = self.make_agent()
        reset = time.time() + 600
        attempted = []
        for i in range(3):
            attempted.append(agent._credential_pool_entry_id)
            recovered, _ = recover_with_credential_pool(agent, status_code=429,
                has_retried_429=False, error_context={'reset_at': reset + i * 60})
            self.assertEqual(recovered, i < 2)
        self.assertEqual(len(set(attempted)), 3)
        self.assertFalse(agent._credential_pool.has_available(model=agent.model))
        self.assertEqual(agent._credential_pool.next_available_at(model=agent.model), reset)
        self.assertIn('All 3 Claude accounts limited', agent.notices[-1][0][2])
        self.assertEqual(agent.notices[-1][0][3]['retry_at'], reset)

    def test_external_429_seam_next_account_succeeds_without_sleep(self):
        import httpx
        import anthropic
        from agent.agent_runtime_helpers import extract_api_error_context
        agent = self.make_agent()
        requests = []
        def handler(request):
            requests.append(request.headers.get('x-api-key'))
            if len(requests) == 1:
                return httpx.Response(429, headers={'retry-after': '600'}, json={
                    'type': 'error', 'error': {'type': 'rate_limit_error', 'message': 'Too many requests'}})
            return httpx.Response(200, json={'id': 'offline', 'type': 'message', 'role': 'assistant',
                'model': agent.model, 'content': [{'type': 'text', 'text': 'READY'}],
                'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            def request():
                return anthropic.Anthropic(api_key=agent.api_key, http_client=http, max_retries=0).messages.create(
                    model=agent.model, max_tokens=4, messages=[{'role': 'user', 'content': 'READY'}])
            try:
                request()
                self.fail('injected 429 not observed')
            except anthropic.RateLimitError as error:
                context = extract_api_error_context(error)
                recovered, _ = recover_with_credential_pool(agent, status_code=error.status_code,
                    has_retried_429=False, error_context=context)
                self.assertTrue(recovered)
            result = request()
        self.assertEqual(result.content[0].text, 'READY')
        self.assertEqual(requests, ['offline-key-0', 'offline-key-1'])
        self.assertGreater(agent._credential_pool.entries()[0].model_cooldowns[agent.model], time.time()+590)

    def test_all_limited_backoff_uses_earliest_pool_reset(self):
        from agent.turn_recovery import compute_error_backoff
        agent = self.make_agent()
        reset = time.time() + 120
        for i in range(3):
            recover_with_credential_pool(agent, status_code=429, has_retried_429=False,
                error_context={'reset_at': reset+i*100})
        agent._buffer_diagnostic_status = lambda *args: None
        agent._emit_diagnostic_wait = lambda *args: None
        agent._client_log_context = lambda: 'offline test'
        with patch('agent.retry_utils.provider_retry_after_seconds', return_value=600), patch('time.time', return_value=reset-120):
            wait = compute_error_backoff(agent, Exception('offline'), retry_count=1,
                max_retries=3, is_rate_limited=True, is_zai_coding_overload=False,
                base_url=agent.base_url, model=agent.model)
        self.assertEqual(wait, 120)

    def test_notice_persists_only_safe_metadata_in_profile(self):
        import tempfile, os, pathlib, json
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {'HERMES_HOME': home}):
            agent = self.make_agent()
            agent.session_id = 'api_offline_notice'
            recover_with_credential_pool(agent, status_code=429, has_retried_429=False,
                error_context={'reset_at': time.time()+600, 'message': 'do not persist provider errors'})
            target = pathlib.Path(home) / 'sessions/provider-status/api_offline_notice.json'
            persisted = json.loads(target.read_text())
            self.assertEqual(persisted['next_account'], 'claude-2')
            self.assertNotIn('offline-key', target.read_text())
            self.assertNotIn('do not persist', target.read_text())
            self.assertEqual(agent.model, 'claude-sonnet-5-5')
            self.assertEqual(agent.provider, 'anthropic')

    def test_duplicate_token_never_retried_as_another_account(self):
        agent = self.make_agent()
        agent._credential_pool._entries[1].access_token = agent.api_key
        recovered, _ = recover_with_credential_pool(agent, status_code=429,
            has_retried_429=False, error_context={'reset_at': time.time()+600})
        self.assertTrue(recovered)
        self.assertEqual(agent._credential_pool_entry_id, 'account-2')

if __name__ == '__main__':
    unittest.main()
