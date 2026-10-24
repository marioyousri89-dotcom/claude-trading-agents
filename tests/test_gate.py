import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from auth_gate import Config, GateError, gate, session
from auth_gate.config import parse_allowlist
from auth_gate.providers import DeviceCode, Identity, Provider

POSIX = os.name == "posix"


class FakeProvider(Provider):
    name = "github"

    def __init__(self, polls=None, login="octocat", valid=True):
        self.polls = list(polls or [{"access_token": "tok"}])
        self.login = login
        self.valid = valid
        self.revoked = []

    def start(self):
        return DeviceCode("dev", "ABCD-1234", "https://example.test/device", 1, 60)

    def poll(self, device_code):
        return self.polls.pop(0)

    def identify(self, access_token):
        if not self.valid:
            return None
        return Identity("github", f"github:{self.login.lower()}", self.login)

    def revoke(self, access_token):
        self.revoked.append(access_token)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "session.json"
        self.config = Config(allowed=frozenset({"github:octocat"}), session_path=self.path, session_ttl=3600)
        self.provider = FakeProvider()
        patcher = mock.patch.object(Config, "provider", lambda _self, _name: self.provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep = mock.patch.object(gate.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_login_then_verify(self):
        identity = gate.login(self.config, "github", out=lambda *_: None)
        self.assertEqual(identity.subject, "github:octocat")
        if POSIX:
            self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(gate.verify(self.config), identity)

    def test_device_flow_handles_pending_and_slow_down(self):
        self.provider.polls = [
            {"error": "authorization_pending"},
            {"error": "slow_down", "interval": 10},
            {"access_token": "tok"},
        ]
        result = gate.run_device_flow(self.provider, out=lambda *_: None)
        self.assertEqual(result["access_token"], "tok")
        self.assertEqual(gate.time.sleep.call_args_list[-1].args[0], 10)

    def test_denied_sign_in(self):
        self.provider.polls = [{"error": "access_denied"}]
        with self.assertRaisesRegex(GateError, "denied"):
            gate.login(self.config, "github", out=lambda *_: None)

    def test_user_not_on_allowlist_is_rejected_and_token_revoked(self):
        self.provider.login = "intruder"
        with self.assertRaisesRegex(GateError, "allowlist"):
            gate.login(self.config, "github", out=lambda *_: None)
        self.assertEqual(self.provider.revoked, ["tok"])
        self.assertFalse(self.path.exists())

    def test_revoked_token_forces_relogin(self):
        gate.login(self.config, "github", out=lambda *_: None)
        self.provider.valid = False
        self.assertIsNone(gate.verify(self.config))
        self.assertFalse(self.path.exists())

    def test_expired_session(self):
        gate.login(self.config, "github", out=lambda *_: None)
        with mock.patch.object(session.time, "time", return_value=time.time() + 7200):
            self.assertIsNone(gate.verify(self.config))

    def test_token_expiry_caps_session(self):
        self.provider.polls = [{"access_token": "tok", "expires_in": 60}]
        gate.login(self.config, "github", out=lambda *_: None)
        current = session.load(self.path)
        self.assertLessEqual(current.expires_at - current.created_at, 60)

    def test_user_removed_from_allowlist_is_locked_out(self):
        gate.login(self.config, "github", out=lambda *_: None)
        narrowed = Config(allowed=frozenset({"google:a@b.com"}), session_path=self.path, session_ttl=3600)
        with self.assertRaisesRegex(GateError, "allowlist"):
            gate.verify(narrowed)

    def test_non_interactive_without_session_fails(self):
        with self.assertRaisesRegex(GateError, "Not signed in"):
            gate.require_login(self.config, interactive=False)

    @unittest.skipUnless(POSIX, "Unix file permissions only")
    def test_world_readable_session_file_is_refused(self):
        gate.login(self.config, "github", out=lambda *_: None)
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(GateError, "readable by other users"):
            gate.verify(self.config)

    @unittest.skipUnless(POSIX, "Unix file permissions only")
    def test_shared_session_directory_is_refused(self):
        os.chmod(self.tmp.name, 0o777)
        with self.assertRaisesRegex(GateError, "writable by other users"):
            gate.login(self.config, "github", out=lambda *_: None)

    def test_save_leaves_no_temp_files(self):
        gate.login(self.config, "github", out=lambda *_: None)
        self.assertEqual(os.listdir(self.tmp.name), ["session.json"])

    def test_network_error_becomes_gate_error(self):
        from auth_gate import http
        with mock.patch.object(http.urllib.request, "urlopen", side_effect=OSError("connection refused")):
            with self.assertRaisesRegex(GateError, "Network error contacting example.test"):
                http.get_json("https://example.test/user", "tok")

    def test_logout(self):
        gate.login(self.config, "github", out=lambda *_: None)
        self.assertTrue(gate.logout(self.config))
        self.assertEqual(self.provider.revoked, ["tok"])
        self.assertIsNone(gate.verify(self.config))


class ConfigTests(unittest.TestCase):
    def test_allowlist_parsing(self):
        self.assertEqual(
            parse_allowlist(" GitHub:Octocat , google:Me@X.com,"),
            frozenset({"github:octocat", "google:me@x.com"}),
        )

    def test_allowlist_rejects_bad_entries(self):
        with self.assertRaises(GateError):
            parse_allowlist("octocat")

    def test_windows_session_file_must_be_in_profile(self):
        from auth_gate import config
        outside = str(Path(tempfile.gettempdir()).resolve().parent / "shared" / "session.json")
        env = {"TRADING_GATE_ALLOWED": "github:octocat", "TRADING_GATE_SESSION_FILE": outside}
        with mock.patch.dict(os.environ, env), mock.patch.object(config, "WINDOWS", True), \
                mock.patch.object(config.Path, "home", return_value=Path(tempfile.gettempdir()) / "home"):
            with self.assertRaisesRegex(GateError, "user profile"):
                Config.from_env()
        with mock.patch.dict(os.environ, {**env, "TRADING_GATE_SESSION_FILE": "~/x/session.json"}), \
                mock.patch.object(config, "WINDOWS", True):
            self.assertEqual(Config.from_env().session_path, (Path.home() / "x" / "session.json").resolve())

    def test_empty_allowlist_fails_closed(self):
        with mock.patch.dict(os.environ, {"TRADING_GATE_ALLOWED": ""}):
            with self.assertRaisesRegex(GateError, "empty"):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()
