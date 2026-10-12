"""No launchd mutations: exercise the real CLI guard with a Background caller."""
import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('guard', Path(__file__).with_name('macos_launchd.py'))
guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)


def gateway(root, profile='default'):
    label = 'ai.hermes.gateway' + ('-' + profile if profile != 'default' else '')
    home = root if profile == 'default' else root / 'profiles' / profile
    return SimpleNamespace(get_hermes_home=lambda: home, get_launchd_label=lambda: label,
        _probe_launchd_domain_for_label=lambda label: f'user/{os.getuid()}',
        generate_launchd_plist=lambda: plistlib.dumps({'ProgramArguments':['python','-m','hermes_cli.main','-p',profile,'gateway','run'], 'LimitLoadToSessionType':['Aqua','Background']}).decode(),
        _launchctl_bootstrap=Mock(), _launchd_fallback_to_detached=Mock(),
        launchd_stop=Mock(), launchd_restart=Mock(), run_gateway=Mock(),
        launchd_start=Mock(), _wait_for_launchd_service_pid=Mock(return_value=22))


@unittest.skipUnless(sys.platform == 'darwin', 'macOS launchd only')
class GuardTests(unittest.TestCase):
    def test_background_start_retains_domain_after_independent_stop(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);g=gateway(root);domain=f'gui/{os.getuid()}'
            guard.remember_domain(root,g.get_launchd_label(),domain)
            with patch.object(guard,'keyring_enabled',return_value=False), patch.object(guard,'loaded_domain',return_value=None):
                guard.patch_gateway(g,root,root/'guard.py')
                self.assertEqual(g._probe_launchd_domain_for_label(g.get_launchd_label()),domain)

    def test_named_keyring_moves_to_gui_but_stop_removes_actual_user_job(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);g=gateway(root,'sales');domain=f'user/{os.getuid()}'
            with patch.object(guard,'keyring_enabled',return_value=True), patch.object(guard,'loaded_domain',return_value=domain):
                guard.patch_gateway(g,root,root/'guard.py')
                self.assertEqual(g._probe_launchd_domain_for_label(g.get_launchd_label()),f'gui/{os.getuid()}')
                g.launchd_stop()
                self.assertEqual(g._resolved_launchd_domain,domain)
                self.assertEqual(guard.read_policy(root)[g.get_launchd_label()],f'gui/{os.getuid()}')

    def test_file_profile_keeps_user_domain_and_plist_session_types(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);g=gateway(root,'sales');domain=f'user/{os.getuid()}'
            guard.remember_domain(root,g.get_launchd_label(),domain)
            with patch.object(guard,'keyring_enabled',return_value=False), patch.object(guard,'loaded_domain',return_value=None):
                guard.patch_gateway(g,root,root/'guard.py')
                self.assertEqual(g._probe_launchd_domain_for_label(g.get_launchd_label()),domain)
                p=plistlib.loads(g.generate_launchd_plist().encode())
                self.assertEqual(p['LimitLoadToSessionType'],['Aqua','Background'])
                self.assertIn('-p',p['ProgramArguments']);self.assertIn('sales',p['ProgramArguments'])

    def test_refresh_remains_guarded_and_aqua_only(self):
        text=plistlib.dumps({'ProgramArguments':['python','-m','hermes_cli.main','-p','sales','gateway','run'], 'EnvironmentVariables':{guard.DOMAIN_ENV:f'user/{os.getuid()}'}}).decode()
        first=guard.guard_plist(text,Path('/host/guard.py'),True)
        self.assertEqual(guard.guard_plist(first,Path('/host/guard.py'),True),first)
        p=plistlib.loads(first.encode());self.assertEqual(p['LimitLoadToSessionType'],['Aqua'])
        self.assertNotIn(guard.DOMAIN_ENV,p['EnvironmentVariables'])

    def test_gui_unavailable_and_background_fallback_fail_closed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);g=gateway(root)
            with patch.object(guard,'keyring_enabled',return_value=True), patch.object(guard.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'Background','')):
                guard.patch_gateway(g,root,root/'guard.py')
                with self.assertRaisesRegex(RuntimeError,'unavailable'):g._launchctl_bootstrap(f'gui/{os.getuid()}','p',g.get_launchd_label())
                with self.assertRaisesRegex(RuntimeError,'fallback'):g._launchd_fallback_to_detached('broken')
                with self.assertRaisesRegex(RuntimeError,'Aqua'):g.run_gateway()

    def test_domain_override_cannot_target_another_user(self):
        with self.assertRaises(ValueError):guard.checked_domain('gui/999999')

    def test_policy_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);path=guard.policy_path(root);path.parent.mkdir()
            other=root/'other';other.write_text('{}');path.symlink_to(other)
            with self.assertRaises(ValueError):guard.read_policy(root)

    def test_restart_waits_for_api_port_and_registration_before_start(self):
        from gateway import status,config
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);g=gateway(root);domain=f'gui/{os.getuid()}'
            guard.remember_domain(root,g.get_launchd_label(),domain)
            api=SimpleNamespace(enabled=True,extra={'host':'127.0.0.1','port':8642})
            def command(args,**kwargs):return subprocess.CompletedProcess(args,0 if len(args)==3 and args[2]==domain else 1,'','')
            with patch.object(guard,'keyring_enabled',return_value=True),patch.object(guard,'loaded_domain',return_value=domain),patch.object(guard.subprocess,'run',side_effect=command),patch.object(status,'get_running_pid',return_value=11),patch.object(config,'load_gateway_config',return_value=SimpleNamespace(platforms={config.Platform('api_server'):api})),patch.object(guard.os,'kill',side_effect=ProcessLookupError),patch.object(guard.socket,'socket') as socket,patch.object(guard.time,'sleep') as sleep:
                socket.return_value.__enter__.return_value.bind.side_effect=[OSError('busy'),None]
                guard.patch_gateway(g,root,root/'guard.py');g.launchd_restart()
                sleep.assert_called_once();g.launchd_start.assert_called_once()
                g._wait_for_launchd_service_pid.assert_called_once_with(g.get_launchd_label(),11,timeout=30,domain=domain)


if __name__=='__main__':unittest.main()
