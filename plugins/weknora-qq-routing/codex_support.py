"""Native Hook registration, scoped trust, and per-turn capabilities."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time

import psutil
from .policy import prompt_hash, source_digest

CAPS_ENV = 'HERMES_WEKNORA_QQ_CAPABILITIES'


def hook_options(python, home, capabilities):
    script = Path(__file__).with_name('codex_hook.py')
    argv = [str(python), str(script), '--source-sha256', source_digest(),
            '--hermes-home', str(home), '--capabilities-file', str(capabilities)]
    command = subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)
    definition = ('hooks.UserPromptSubmit=[{hooks=[{type="command",command='
                  + json.dumps(command) + ',timeout=5,additionalContextLimit=2500}]}]')
    return ['-c', definition, '-c', 'features.hooks=true']


def reviewed_hook_trust(client_factory, base, cwd):
    # Review exactly our versioned definition, not a global bypass. Probe only
    # lists local Hook metadata; it never starts a model turn or trusts others.
    with client_factory(base) as client:
        client.initialize()
        rows = client.request('hooks/list', {'cwds': [str(cwd)]}).get('data', [])
        ours = [h for r in rows for h in r.get('hooks', [])
                if str(Path(__file__).with_name('codex_hook.py')) in h.get('command', '')]
        if len(ours) != 1:
            raise RuntimeError('Native Codex Hook definition unavailable')
        hook = ours[0]
    # The native CLI's dotted-key parser does not unquote dotted path segments;
    # use a TOML inline map so the Hook key (which contains dots) stays intact.
    trust = ('hooks.state={' + json.dumps(hook['key']) + '={trusted_hash='
             + json.dumps(hook['currentHash']) + '}}')
    return ['-c', trust], hook['key']


def write_capabilities(path, home, mode, tools, prompt):
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    current = psutil.Process(os.getpid())
    data = {'profile_home': str(Path(home).resolve()), 'platform': 'qqbot', 'origin': 'user',
            'mode': mode, 'tools': tools, 'prompt_hash': prompt_hash(prompt),
            'pid': current.pid, 'created': current.create_time(), 'expires': time.time() + 180}
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.caps-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
