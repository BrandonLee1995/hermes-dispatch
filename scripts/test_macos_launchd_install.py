import importlib.util
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('install-macos-launchd-guard.py'))
install=importlib.util.module_from_spec(spec);spec.loader.exec_module(install)


@unittest.skipUnless(sys.platform=='darwin','macOS launcher only')
class InstallerTests(unittest.TestCase):
    def fixture(self,base):
        user=Path(base);root=user/'.hermes';core=root/'hermes-agent/hermes';core.parent.mkdir(parents=True);core.write_text('core unchanged')
        launcher=user/'.local/bin/hermes';launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/bash\n# QQ bootstrap preserved\nunset PYTHONHOME\nexec "python" "'+str(core)+'" "$@"\n');launcher.chmod(0o755)
        p=user/'Library/LaunchAgents/ai.hermes.gateway.plist';p.parent.mkdir(parents=True)
        p.write_bytes(plistlib.dumps({'ProgramArguments':['python','-m','hermes_cli.main','gateway','run'],'LimitLoadToSessionType':['Aqua','Background']}))
        return user,root,core,launcher,p

    def test_install_idempotence_and_exact_restore_preserve_core_and_qq(self):
        with tempfile.TemporaryDirectory() as t:
            user,root,core,launcher,plist=self.fixture(t);before={p:p.read_bytes() for p in (core,launcher,plist)}
            with patch.object(Path,'home',return_value=user),patch.object(install.mod,'keyring_enabled',return_value=False),patch.object(install.mod,'loaded_domain',return_value=f'gui/{os.getuid()}'):
                result=install.install(root,launcher)
                self.assertIn('QQ bootstrap preserved',launcher.read_text())
                self.assertEqual(install.install(root,launcher)['status'],'already-installed')
                install.restore(root,Path(result['backup']))
            for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def test_gui_missing_fails_before_live_changes(self):
        with tempfile.TemporaryDirectory() as t:
            user,root,core,launcher,plist=self.fixture(t);before=launcher.read_bytes()
            with patch.object(Path,'home',return_value=user),patch.object(install.mod,'keyring_enabled',return_value=True),patch.object(install.mod,'loaded_domain',return_value=f'user/{os.getuid()}'),patch.object(install.mod,'require_domain',side_effect=RuntimeError('GUI missing')):
                with self.assertRaisesRegex(RuntimeError,'GUI missing'):install.install(root,launcher)
            self.assertEqual(launcher.read_bytes(),before)
            self.assertFalse((root/'scripts/dispatch-macos-launchd.py').exists())

    def test_symlink_launcher_is_rejected_without_editing_core(self):
        with tempfile.TemporaryDirectory() as t:
            user,root,core,launcher,plist=self.fixture(t);launcher.unlink();launcher.symlink_to(core)
            with self.assertRaises(ValueError):install.install(root,launcher)
            self.assertEqual(core.read_text(),'core unchanged')


if __name__=='__main__':unittest.main()
