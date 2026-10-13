"""Native UserPromptSubmit hook. No network calls and no credential reads."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

import psutil
from policy import prompt_hash, render, source_digest

CAPS_ENV = 'HERMES_WEKNORA_QQ_CAPABILITIES'


def context(event, env, digest):
    if digest != source_digest() or event.get('hook_event_name') != 'UserPromptSubmit':
        return ''
    path = env.get(CAPS_ENV)
    home = env.get('HERMES_HOME')
    if not path or not home:
        return ''
    root = Path(home).resolve()
    file = Path(path).resolve()
    try:
        file.relative_to(root / 'state/weknora-routing')
        if os.name != 'nt' and file.stat().st_mode & 0o077:
            return ''
        data = json.loads(file.read_text(encoding='utf-8'))
        if (data.get('profile_home') != str(root) or data.get('platform') != 'qqbot'
                or data.get('origin') != 'user' or data.get('mode') not in {'codex', 'both'}
                or data.get('expires', 0) < time.time()
                or data.get('prompt_hash') != prompt_hash(event.get('prompt', ''))):
            return ''
        if abs(psutil.Process(data['pid']).create_time() - data['created']) > .01:
            return ''
        return render(data.get('tools', []))
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return ''


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--hermes-home')
    parser.add_argument('--capabilities-file')
    args = parser.parse_args()
    env = dict(os.environ)
    if args.hermes_home:
        env['HERMES_HOME'] = args.hermes_home
    if args.capabilities_file:
        env[CAPS_ENV] = args.capabilities_file
    try:
        text = context(json.load(sys.stdin), env, args.source_sha256)
    except (ValueError, TypeError):
        text = ''
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
                                           'additionalContext': text}} if text else {}))
