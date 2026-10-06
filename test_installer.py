import asyncio
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import installer


class FakeLockdown:
    closed = False
    all_values = {"DeviceName": "Test iPhone"}
    async def close(self):
        self.closed = True


class FakeAfc:
    def __init__(self, *args):
        self.written = bytearray()
        self.removed = False
        self.closed_handle = False
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def makedirs(self, path):
        pass
    async def fopen(self, path, mode):
        return 42
    async def fwrite(self, handle, data):
        self.written.extend(data)
    async def fclose(self, handle):
        self.closed_handle = True
    async def rm_single(self, path, force):
        self.removed = True


class FakeProxy:
    def __init__(self, fail=False):
        self.fail = fail
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def send_package(self, command, options, handler, path):
        assert command == "Install"
        handler(50, ())  # The library passes an extra argument.
        if self.fail:
            raise RuntimeError("installation failed")
        handler(100, ())


class InstallerTests(unittest.TestCase):
    def exercise(self, fail):
        import tempfile
        lock, afc, events = FakeLockdown(), FakeAfc(), []
        async def connect(notify):
            return lock
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.ipa"
            data = b"x" * (1024 * 1024 + 12)
            path.write_bytes(data)
            with patch.object(installer, "verify_ipa", return_value="1.0"), \
                 patch.object(installer, "connect_device", side_effect=connect), \
                 patch("pymobiledevice3.services.afc.AfcService", return_value=afc), \
                 patch("pymobiledevice3.services.installation_proxy.InstallationProxyService", return_value=FakeProxy(fail)):
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "installation failed"):
                        asyncio.run(installer.install_package(path, events.append))
                else:
                    asyncio.run(installer.install_package(path, events.append))
            self.assertEqual(bytes(afc.written), data)
            self.assertTrue(afc.removed and afc.closed_handle and lock.closed)
            transfer = [e for e in events if e.get("stage") == "transfer" and e["type"] == "progress"]
            self.assertEqual(transfer[-1]["progress"], 1)
            self.assertEqual(transfer[-1]["bytes"], len(data))
            done = [e for e in events if e.get("stage") == "install" and e.get("state") == "done"]
            self.assertEqual(bool(done), not fail)

    def test_upload_progress_and_install_completion(self):
        self.exercise(False)

    def test_failed_install_closes_connections_and_removes_staging(self):
        self.exercise(True)


if __name__ == "__main__":
    unittest.main()
