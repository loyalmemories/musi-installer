import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import download


class FakeProcess:
    def __init__(self, args, **kwargs):
        assert "secret-password" not in repr(args)
        assert "secret-password" not in repr(kwargs)
        self.stdin = io.StringIO()
        self.result = None
        self.killed = False

    @property
    def stdout(self):
        request = json.loads(self.stdin.getvalue().splitlines()[0])
        yield json.dumps({"type": "2fa"}) + "\n"
        self.code = json.loads(self.stdin.getvalue().splitlines()[1])["code"]
        destination = Path(request["output"])
        destination.write_bytes(b"fake-package")
        yield json.dumps({"type": "downloaded", "path": str(destination)}) + "\n"

    def poll(self):
        return self.result

    def wait(self):
        self.result = -1 if self.killed else 0
        return self.result

    def kill(self):
        self.killed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class DownloadTests(unittest.TestCase):
    def test_two_factor_and_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(download.Path, "home", return_value=Path(temporary)), \
                 patch.object(download.Path, "is_file", return_value=True), \
                 patch.object(download.subprocess, "Popen", side_effect=FakeProcess):
                result = download.scarica("user@example.com", "secret-password", lambda: "123456", lambda _: None)
                self.assertEqual(result.parent, Path(temporary) / "Downloads" / "Musi")
                self.assertEqual(result.read_bytes(), b"fake-package")

    def test_cancel_two_factor_kills_process(self):
        processes = []
        def factory(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            return process
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(download.Path, "home", return_value=Path(temporary)), \
                 patch.object(download.Path, "is_file", return_value=True), \
                 patch.object(download.subprocess, "Popen", side_effect=factory):
                with self.assertRaisesRegex(RuntimeError, "cancelled"):
                    download.scarica("user@example.com", "secret-password", lambda: None, lambda _: None)
                self.assertTrue(processes[0].killed)


if __name__ == "__main__":
    unittest.main()
