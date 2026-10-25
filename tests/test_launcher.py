import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import launch


class DesktopRestartTests(unittest.TestCase):
    def test_only_this_checkouts_servers_are_selected(self):
        python = str(launch.ROOT / '.venv' / 'Scripts' / 'python.exe')
        commands = [
            f'"{python}" -m webapp.desktop_server',
            f'{python} -m uvicorn webapp.server:app --port 8765',
            f'{python} -m uvicorn webapp.server:app --port 8000',
            f'{python} -m unrelated',
            r'C:\other-project\.venv\Scripts\python.exe -m webapp.desktop_server',
            f'{python} -m webapp.desktop_server_other',
        ]
        processes = [dict(ProcessId=i, ParentProcessId=100, CommandLine=line)
                     for i, line in enumerate(commands)]
        with patch('launch.subprocess.run', return_value=SimpleNamespace(stdout=json.dumps(processes))):
            self.assertEqual([p['ProcessId'] for p in launch.desktop_processes()], [0, 1])

    def test_stop_tree_once_and_ignore_stale_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'runtime').mkdir()
            (root / 'runtime/server.pid').write_text('99999')
            processes = [dict(ProcessId=10, ParentProcessId=1),
                         dict(ProcessId=11, ParentProcessId=10)]
            with patch('launch.ROOT', root), patch('launch.sys.platform', 'win32'), \
                    patch('launch.desktop_processes', return_value=processes), \
                    patch('launch.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run, \
                    patch('launch.socket.socket') as socket:
                socket.return_value.__enter__.return_value.connect_ex.return_value = 1
                launch.stop_existing_server()
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0], ['taskkill.exe', '/PID', '10', '/T', '/F'])
                self.assertFalse((root / 'runtime/server.pid').exists())

    def test_unrelated_port_owner_is_not_stopped(self):
        with patch('launch.sys.platform', 'win32'), \
                patch('launch.desktop_processes', return_value=[]), \
                patch('launch.subprocess.run') as run, patch('launch.socket.socket') as socket:
            socket.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(RuntimeError, 'Port 8765'):
                launch.stop_existing_server()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
