#!/usr/bin/env python3
"""Explicit, read-only capability-gated opt-in before the normal profile updater."""
import argparse
import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import tomllib
import uuid

from dotenv import dotenv_values
import yaml
from hermes_dispatch_update import FileLock, assert_real_directory, is_link_or_reparse

spec = importlib.util.spec_from_file_location('routing_policy', Path(__file__).resolve().parents[1] / 'plugins/weknora-qq-routing/policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
PLUGIN = 'weknora-qq-routing'
INVENTORY = {'list_knowledge_bases', 'list_shared_knowledge_bases'}


def expand(value, env):
    if isinstance(value, str):
        return re.sub(r'\$\{([^}]+)\}', lambda m: env[m[1]], value)
    if isinstance(value, dict): return {k: expand(v, env) for k, v in value.items()}
    if isinstance(value, list): return [expand(v, env) for v in value]
    return value


async def probe(cfg, env):
    from mcp import ClientSession
    from mcp.client import streamable_http as transport
    identity = policy.http_identity(cfg, env)
    if identity is None or not identity[0]: raise ValueError('unresolved-http-identity')
    httpx = getattr(transport, 'httpx2', None) or transport.httpx
    async with httpx.AsyncClient(headers=identity[1], timeout=15, follow_redirects=False) as http:
        async with transport.streamable_http_client(identity[0], http_client=http) as streams:
            read, write = tuple(streams)[:2]
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                # shortcut: paginated tool catalogs are skipped until a fleet endpoint needs them.
                if listed.model_dump(by_alias=True).get('nextCursor'): raise ValueError('paginated-tool-catalog')
                names = {t.name for t in listed.tools}
                names -= set(cfg.get('disabled_tools') or [])
                if cfg.get('enabled_tools') is not None: names &= set(cfg['enabled_tools'])
                if not names & policy.SEARCH or not INVENTORY <= names:
                    raise ValueError('missing-search-or-scope-tools')
                for name in sorted(INVENTORY):
                    result = await session.call_tool(name, {})
                    if result.model_dump(by_alias=True).get('isError'): raise ValueError('inventory-denied')
                    bodies = [json.loads(c.text) for c in result.content if c.type == 'text']
                    if not bodies or any(not isinstance(b, dict) or b.get('success') is not True or not isinstance(b.get('data'), list) for b in bodies):
                        raise ValueError('inventory-not-confirmed')
                return sorted(names & (policy.SEARCH | policy.READ))


def manage(home, apply=False):
    if is_link_or_reparse(home) or not home.is_dir(): raise ValueError('unsafe-profile')
    path = home/'config.yaml'
    if is_link_or_reparse(path): raise ValueError('unsafe-config')
    before = path.read_bytes()
    config = yaml.safe_load(before) or {}
    plugins = config.get('plugins') or {}
    if PLUGIN in (plugins.get('disabled') or []): return {'status': 'skipped', 'reason': 'explicitly-disabled'}
    mode = plugins.get('entries', {}).get(PLUGIN, {}).get('settings', {}).get('mode')
    if mode == 'off': return {'status': 'skipped', 'reason': 'explicitly-off'}
    env_path = home/'.env'; env_before = env_path.read_bytes() if env_path.exists() else b''
    profile_env = {k: v for k, v in dotenv_values(home/'.env').items() if v is not None}
    if home.parent.name == 'profiles' and not profile_env.get('CODEX_HOME'):
        return {'status': 'skipped', 'reason': 'no-profile-codex-home'}
    env = {**os.environ, **profile_env}
    codex_path = Path(env.get('CODEX_HOME') or Path.home()/'.codex')/'config.toml'
    codex_before = codex_path.read_bytes() if codex_path.exists() else b''
    codex = tomllib.loads(codex_before.decode())
    hermes = expand(config.get('mcp_servers') or {}, env)
    candidates = expand(codex.get('mcp_servers') or {}, env)
    # The release uses codex_app_server, so only its effective connection can qualify.
    results = []; selected = None
    for name, cfg in candidates.items():
        if 'weknora' not in name.casefold() or not policy.enabled(cfg.get('enabled', True)): continue
        if name in hermes and not policy.enabled(hermes[name].get('enabled', True)): continue
        if not cfg.get('url'): results.append({'server': name, 'reason': 'unsupported-transport'}); continue
        matches = [v for n, v in hermes.items() if 'weknora' in n.casefold() and policy.enabled(v.get('enabled', True)) and policy.same_endpoint(v, cfg, env)]
        if name in hermes and not matches:
            results.append({'server': name, 'reason': 'profile-endpoint-mismatch'}); continue
        try:
            tools = asyncio.run(asyncio.wait_for(probe(cfg, env), 45))
        except Exception:
            # Exceptions may embed URLs, tokens and knowledge content; never print them.
            results.append({'server': name, 'reason': 'capability-or-access-unconfirmed'}); continue
        selected = (name, cfg, bool(matches), tools); break
    if selected is None: return {'status': 'skipped', 'reason': 'no-accessible-connection', 'checks': results}
    name, cfg, matched, tools = selected
    report = {'status': 'eligible', 'server': name, 'tools': tools, 'scope_reads': sorted(INVENTORY)}
    if not apply: return report
    if not isinstance(plugins.get('enabled'), list): raise ValueError('explicit-plugin-list-required')
    state_path = home/'gateway_state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get('active_agents'): return {**report, 'status': 'deferred', 'reason': 'active-agents'}
    if path.read_bytes() != before or (env_path.read_bytes() if env_path.exists() else b'') != env_before or (codex_path.read_bytes() if codex_path.exists() else b'') != codex_before:
        return {**report, 'status': 'deferred', 'reason': 'config-changed'}
    if not matched:
        url, headers = policy.http_identity(cfg, env)
        config.setdefault('mcp_servers', {})[name] = {'url': url, 'headers': headers, 'enabled': True, 'tools': {'include': tools}}
    if PLUGIN not in plugins['enabled']: plugins['enabled'].append(PLUGIN)
    plugins.setdefault('entries', {}).setdefault(PLUGIN, {}).setdefault('settings', {})['mode'] = 'hermes'
    config['plugins'] = plugins
    if yaml.safe_load(before) == config: return {**report, 'status': 'already-enabled'}
    backup = home/'state'/('weknora-optin-' + uuid.uuid4().hex + '.yaml')
    backup.parent.mkdir(exist_ok=True)
    with backup.open('xb') as f:
        os.chmod(backup, 0o600); f.write(before)
    with tempfile.NamedTemporaryFile(dir=home, delete=False) as f:
        temporary = Path(f.name); f.write(yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode())
    try: os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)
    return {**report, 'status': 'enabled', 'backup': str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if is_link_or_reparse(args.home) or not args.home.is_dir():
        print(json.dumps({'status': 'error', 'reason': 'unsafe-profile'})); return 1
    try:
        assert_real_directory(args.home/'state', 'profile state', create=True)
        with FileLock(args.home/'state/hermes-dispatch-update.lock'):
            result = manage(args.home, args.apply)
    except Exception as exc:
        result = {'status': 'error', 'reason': type(exc).__name__}
    print(json.dumps(result))
    return 1 if result['status'] == 'error' else 0

if __name__ == '__main__': raise SystemExit(main())
