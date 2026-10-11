import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from g923 import hooks, ping
from tests.test_drive import axis, make, tap

PING = Path(__file__).resolve().parent.parent / "bin" / "g923-ping"


class HookStateTest(unittest.TestCase):
    def test_done_plays_feedback(self):
        d = make()
        d.hook_event("busy")
        d.hook_event("done")
        self.assertEqual(d.claude_state, "idle")
        self.assertIn("done", d.fb.events)

    def test_attention_until_user_responds(self):
        d = make()
        d.hook_event("attention")
        self.assertEqual(d.claude_state, "attention")
        tap(d, 288)  # ✕ = Yes
        self.assertEqual(d.claude_state, "busy")

    def test_pedal_also_answers(self):
        d = make()
        d.hook_event("attention")
        for v in (255, 0):
            axis(d, "ABS_RZ", v)
        self.assertEqual(d.claude_state, "busy")

    def test_clear_from_post_tool_use(self):
        d = make()
        d.hook_event("attention")
        d.hook_event("clear")
        self.assertEqual(d.claude_state, "busy")

    def test_repeated_attention_notifies_once(self):
        d = make()
        d.hook_event("attention")
        d.hook_event("attention")
        self.assertEqual(d.fb.events.count("Claude needs you"), 1)


class SocketTest(unittest.TestCase):
    def test_ping_reaches_daemon(self):
        d = make()
        with tempfile.TemporaryDirectory() as tmp:
            sock = os.path.join(tmp, "g923.sock")
            d.cfg["feedback"]["socket"] = sock

            async def go():
                server = await d.serve_hooks()
                env = dict(os.environ, G923_SOCKET=sock)
                proc = await asyncio.create_subprocess_exec(str(PING), "attention", env=env)
                await proc.wait()
                await asyncio.sleep(0.1)
                server.close()

            asyncio.run(go())
        self.assertEqual(d.claude_state, "attention")

    def test_ping_forwards_what_the_hook_says(self):
        d = make()
        hook_input = {"cwd": "/home/me/my-app", "tool_name": "Bash",
                      "tool_input": {"command": "npm   test", "description": "Run tests"}}
        with tempfile.TemporaryDirectory() as tmp:
            sock = os.path.join(tmp, "g923.sock")
            d.cfg["feedback"]["socket"] = sock

            async def go():
                server = await d.serve_hooks()
                env = dict(os.environ, G923_SOCKET=sock)
                proc = await asyncio.create_subprocess_exec(
                    str(PING), "tool", env=env, stdin=asyncio.subprocess.PIPE)
                await proc.communicate(json.dumps(hook_input).encode())
                await asyncio.sleep(0.1)
                server.close()

            asyncio.run(go())
        self.assertEqual(d.session, "my-app")
        self.assertEqual(d.tool, {"name": "Bash", "detail": "npm test"})

    def test_ping_without_daemon_is_silent(self):
        env = dict(os.environ, G923_SOCKET="/nonexistent/g923.sock")
        r = subprocess.run([str(PING), "done"], env=env, capture_output=True, timeout=5)
        self.assertEqual((r.returncode, r.stderr), (0, b""))


class PingMessageTest(unittest.TestCase):
    def test_plain_words_without_hook_input(self):
        self.assertEqual(ping.message("done", None), "done")

    def test_prompt_is_one_trimmed_line(self):
        msg = json.loads(ping.message("busy", {"prompt": "fix\n  the   bug " + "x" * 300}))
        self.assertEqual(msg["event"], "busy")
        self.assertTrue(msg["prompt"].startswith("fix the bug x"))
        self.assertEqual(len(msg["prompt"]), ping.LIMIT)

    def test_tool_detail_picks_the_useful_field(self):
        self.assertEqual(ping.detail({"file_path": "/repo/g923/hud.py", "content": "..."}), "hud.py")
        self.assertEqual(ping.detail({"pattern": "watch", "path": "/repo"}), "watch")
        self.assertEqual(ping.detail({"url": "https://example.com"}), "https://example.com")
        self.assertEqual(ping.detail({"todos": []}), "")

    def test_standalone_copy_matches_the_module(self):
        module = (Path(ping.__file__).read_text()).split("import json", 1)[1]
        self.assertEqual(PING.read_text().split("import json", 1)[1], module)


class SettingsMergeTest(unittest.TestCase):
    OTHER = {"matcher": "", "hooks": [{"type": "command", "command": "node other.js"}]}

    def test_install_keeps_other_hooks_and_is_idempotent(self):
        s = {"hooks": {"Stop": [dict(self.OTHER)]}, "model": "opus"}
        hooks.update(s, "/x/g923-ping", install=True)
        hooks.update(s, "/x/g923-ping", install=True)
        self.assertEqual(len(s["hooks"]["Stop"]), 2)
        self.assertEqual(s["hooks"]["Stop"][0], self.OTHER)
        self.assertIn('"/x/g923-ping" done', s["hooks"]["Stop"][1]["hooks"][0]["command"])
        self.assertEqual(s["model"], "opus")

    def test_installs_every_event(self):
        s = hooks.update({}, "/x/g923-ping", install=True)
        self.assertEqual(set(s["hooks"]), set(hooks.EVENTS))
        self.assertIn('"/x/g923-ping" tool', s["hooks"]["PreToolUse"][0]["hooks"][0]["command"])

    def test_remove_leaves_only_other_hooks(self):
        s = {"hooks": {"Stop": [dict(self.OTHER)]}}
        hooks.update(s, "/x/g923-ping", install=True)
        hooks.update(s, "/x/g923-ping", install=False)
        self.assertEqual(s, {"hooks": {"Stop": [self.OTHER]}})


if __name__ == "__main__":
    unittest.main()


REPO = Path(__file__).resolve().parent.parent


@unittest.skipUnless((REPO / ".venv" / "bin" / "python").exists(), "needs the repo .venv")
class LauncherTest(unittest.TestCase):
    def test_launcher_works_outside_the_repo(self):
        launcher = REPO / "bin" / "g923d"
        r = subprocess.run([str(launcher), "--help"], cwd="/", capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
