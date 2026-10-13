"""Reviewer must follow effective profile config for old and cached threads."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('reviewer_bridge_test', Path(__file__).with_name('approval_bridge.py'))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

def main():
    calls = []
    reviewer = 'auto_review'
    def original(client, method, params=None, *args, **kwargs):
        calls.append((method, params))
        if method == 'config/read':
            return {'config': {'approvals_reviewer': reviewer}}
        return {'method': method, 'params': params}
    wrapped = bridge.wrap_approval_reviewer_request(original)
    client = SimpleNamespace()
    for method in ('thread/start', 'thread/resume', 'turn/start'):
        supplied = {'cwd': '/department/profile/project'} if method != 'turn/start' else {'threadId': 'old-thread'}
        before = dict(supplied)
        result = wrapped(client, method, supplied)
        assert result['params']['approvalsReviewer'] == 'auto_review'
        assert supplied == before
        assert calls[-2] == ('config/read', {'includeLayers': False, 'cwd': '/department/profile/project'})
        assert 'approvalPolicy' not in result['params'] and 'sandboxPolicy' not in result['params']
    reviewer = 'user'
    assert wrapped(client, 'turn/start', {'threadId': 'old-thread'})['params']['approvalsReviewer'] == 'user'
    count = len(calls)
    assert wrapped(client, 'turn/start', {'approvalsReviewer': 'auto_review'})['params']['approvalsReviewer'] == 'auto_review'
    assert len(calls) == count + 1
    reviewer = None
    assert 'approvalsReviewer' not in wrapped(client, 'turn/start', {})['params']
    def broken(client, method, params=None, *args, **kwargs):
        if method == 'config/read': raise RuntimeError('unavailable')
        return params
    assert bridge.wrap_approval_reviewer_request(broken)(client, 'turn/start', {}) == {}
    count = len(calls)
    wrapped(client, 'hooks/list', {})
    assert len(calls) == count + 1
    print('PASS: resumed/cached threads, effective profile isolation, explicit reviewer precedence, no tool/sandbox overrides')

if __name__ == '__main__': main()
