"""Rebelz testleri. Çalıştırma: python -m unittest discover -s tests -v

Gerçek bir model gerekmez: ajan testleri betik yazılmış bir sahte modelle,
istemci testleri ise gerçek HTTP üzerinde çalışan sahte bir OpenAI sunucusuyla yapılır.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rebelz.agent import Agent, parse_text_tool_calls  # noqa: E402
from rebelz.config import Settings  # noqa: E402
from rebelz.llm import ChatClient, LLMError  # noqa: E402
from rebelz.server import create_server  # noqa: E402
from rebelz.service import ChatService  # noqa: E402
from rebelz.tools import (  # noqa: E402
    ToolRegistry,
    build_default_tools,
    html_to_text,
    parse_duckduckgo_html,
    resolve_in_workspace,
)

ALLOW = lambda name, args: True  # noqa: E731
DENY = lambda name, args: False  # noqa: E731


class ScriptedLLM:
    """Her çağrıda sıradaki yanıtı döndürür; gelen mesajları kaydeder."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self.replies:
            raise AssertionError("Beklenmeyen ek model çağrısı")
        return self.replies.pop(0)


def text(content):
    return {"role": "assistant", "content": content}


def native_call(name, arguments, call_id="c1"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)}}],
    }


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name) / "ws"
        self.registry = ToolRegistry(build_default_tools(self.workspace))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, replies, approve=ALLOW, **kwargs):
        llm = ScriptedLLM(replies)
        return Agent(llm, self.registry, approve, **kwargs), llm

    def test_plain_answer(self):
        agent, llm = self.make([text("Merhaba!")])
        self.assertEqual(agent.ask("selam"), "Merhaba!")
        self.assertEqual(agent.messages[-1], {"role": "assistant", "content": "Merhaba!"})
        self.assertEqual(agent.trace, [])

    def test_native_tool_call_then_answer(self):
        agent, llm = self.make([native_call("write_file", {"path": "not.txt", "content": "abc"}), text("Tamam")])
        self.assertEqual(agent.ask("dosya yaz"), "Tamam")
        self.assertEqual((self.workspace / "not.txt").read_text(encoding="utf-8"), "abc")
        second_call_roles = [m["role"] for m in llm.calls[1]["messages"]]
        self.assertIn("tool", second_call_roles)
        self.assertEqual(agent.trace[0]["tool"], "write_file")

    def test_text_fallback_tool_call(self):
        fenced = 'Bakıyorum.\n```json\n{"name": "list_dir", "arguments": {"path": "."}}\n```'
        agent, llm = self.make([text(fenced), text("Klasör boş")])
        agent.native_tools = False
        self.assertEqual(agent.ask("ne var"), "Klasör boş")
        self.assertTrue(any(m["role"] == "system" and "JSON" in m["content"] for m in llm.calls[0]["messages"]))
        self.assertIsNone(llm.calls[0]["tools"])
        self.assertTrue(any("Araç sonucu (list_dir)" in m["content"] for m in agent.messages))

    def test_fallback_triggered_by_http_400(self):
        class RejectTools:
            def __init__(self):
                self.calls = []

            def chat(self, messages, tools=None):
                self.calls.append(tools is not None)
                if tools is not None:
                    raise LLMError("HTTP 400", status=400)
                return text("tools olmadan cevap")

        llm = RejectTools()
        agent = Agent(llm, self.registry, ALLOW)
        self.assertEqual(agent.ask("merhaba"), "tools olmadan cevap")
        self.assertFalse(agent.native_tools)
        self.assertEqual(llm.calls, [True, False])

    def test_unknown_tool_and_bad_arguments_are_reported(self):
        agent, llm = self.make([native_call("hayalet", {}), native_call("now", {"x": 1}), text("bitti")])
        agent.ask("dene")
        results = [m["content"] for m in agent.messages if m["role"] == "tool"]
        self.assertIn("yok", results[0])
        self.assertIn("geçersiz argümanlar", results[1])

    def test_confirm_mode_denies_write(self):
        agent, _ = self.make([native_call("write_file", {"path": "x.txt", "content": "1"}), text("olmadı")],
                             approve=DENY)
        agent.ask("yaz")
        self.assertFalse((self.workspace / "x.txt").exists())
        self.assertIn("onaylanmadı", agent.trace[0]["result"])

    def test_path_traversal_is_blocked(self):
        agent, _ = self.make([native_call("read_file", {"path": "../../etc/passwd"}), text("ok")])
        agent.ask("oku")
        self.assertIn("Çalışma alanı dışındaki", agent.trace[0]["result"])

    def test_repeated_identical_calls_stop_loop(self):
        same = native_call("list_dir", {"path": "."})
        replies = [dict(same, tool_calls=[dict(same["tool_calls"][0], id=f"c{i}")]) for i in range(10)]
        agent, llm = self.make(replies)
        result = agent.ask("döngü")
        self.assertIn("döngü engellendi", result)
        self.assertEqual(len(llm.calls), 4)  # 1 orijinal + 3 tekrar, sonra durur

    def test_max_steps_limit(self):
        replies = [native_call("now", {}, call_id=f"c{i}") for i in range(5)]
        agent, _ = self.make(replies, max_steps=2)
        result = agent.ask("zaman")
        self.assertIn("Adım limitine ulaşıldı", result)

    def test_context_trim_keeps_current_user_message(self):
        agent, _ = self.make([text("ok")], context_chars=800)
        for i in range(30):
            agent.messages.append({"role": "user", "content": f"eski mesaj {i} " + "x" * 50})
            agent.messages.append({"role": "assistant", "content": "y" * 50})
        agent.messages.append({"role": "user", "content": "SON SORU"})
        agent._trim()
        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertEqual(agent.messages[-1]["content"], "SON SORU")
        self.assertEqual(agent.messages[1]["role"], "user")
        self.assertLess(len(agent.messages), 62)

    def test_reset_clears_history(self):
        agent, _ = self.make([text("a")])
        agent.ask("merhaba")
        agent.reset()
        self.assertEqual(agent.messages, [{"role": "system", "content": agent.system_prompt}])


class ParsingTests(unittest.TestCase):
    def test_parse_text_tool_calls_variants(self):
        known = {"now", "read_file"}
        found = parse_text_tool_calls('{"name":"now","arguments":{}} ve {"name":"read_file","arguments":"{\\"path\\":\\"a\\"}"}', known)
        self.assertEqual(found, [("now", {}, None), ("read_file", {"path": "a"}, None)])

    def test_ignores_unknown_or_plain_json(self):
        self.assertEqual(parse_text_tool_calls('{"name": "uydurma", "arguments": {}}', {"now"}), [])
        self.assertEqual(parse_text_tool_calls('{"sonuc": 42}', {"now"}), [])

    def test_html_to_text_strips_scripts(self):
        markup = "<html><script>var x=1;</script><style>p{}</style><h1>Başlık</h1><p>Merhaba &amp; dünya</p></html>"
        result = html_to_text(markup)
        self.assertIn("Başlık", result)
        self.assertIn("Merhaba & dünya", result)
        self.assertNotIn("var x", result)
        self.assertNotIn("<", result)

    def test_duckduckgo_parsing(self):
        markup = (
            '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=1">Örnek <b>Başlık</b></a>'
            '<a class="result__snippet" href="x">Kısa <b>özet</b></a>'
        )
        results = parse_duckduckgo_html(markup, 5)
        self.assertEqual(results, [{"title": "Örnek Başlık", "url": "https://example.com/a", "snippet": "Kısa özet"}])

    def test_resolve_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(resolve_in_workspace(root, "a/b.txt"), (root / "a" / "b.txt").resolve())
            with self.assertRaises(ValueError):
                resolve_in_workspace(root, "../outside")
            with self.assertRaises(ValueError):
                resolve_in_workspace(root, "/etc/passwd")


class ToolTests(unittest.TestCase):
    def test_shell_runs_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(build_default_tools(Path(tmp)))
            result = registry.execute("shell", {"command": "pwd && echo merhaba"}, ALLOW)
            self.assertIn("merhaba", result)
            self.assertIn("çıkış kodu 0", result)
            self.assertIn(str(Path(tmp).resolve()), result)

    def test_memory_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(build_default_tools(Path(tmp)))
            self.assertEqual(registry.execute("recall", {}, ALLOW), "Henüz kayıtlı not yok.")
            registry.execute("remember", {"note": "Annemin doğum günü 3 Mayıs"}, ALLOW)
            self.assertIn("3 Mayıs", registry.execute("recall", {"query": "doğum"}, ALLOW))

    def test_web_fetch_rejects_non_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(build_default_tools(Path(tmp)))
            self.assertIn("Yalnızca http/https", registry.execute("web_fetch", {"url": "file:///etc/passwd"}, ALLOW))


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.autonomy, "confirm")
        self.assertEqual(settings.max_steps, 0)
        self.assertEqual(settings.base_url, "http://127.0.0.1:8080/v1")
        self.assertIsNone(settings.web_token)

    def test_env_overrides_and_validation(self):
        settings = Settings.from_env({"REBELZ_AUTONOMY": "FULL", "REBELZ_MAX_STEPS": "12",
                                      "REBELZ_BASE_URL": "http://x:1/v1/"})
        self.assertEqual(settings.autonomy, "full")
        self.assertEqual(settings.max_steps, 12)
        self.assertEqual(settings.base_url, "http://x:1/v1")
        with self.assertRaises(ValueError):
            Settings.from_env({"REBELZ_AUTONOMY": "yolo"})
        with self.assertRaises(ValueError):
            Settings.from_env({"REBELZ_MAX_STEPS": "abc"})


class FakeOpenAIServer:
    """Gerçek HTTP ile /v1/chat/completions yanıtı veren sahte sunucu."""

    def __init__(self, responder):
        self.responder = responder
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                outer.requests.append(body)
                status, payload = outer.responder(body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def ok_message(message):
    return 200, {"choices": [{"message": message}]}


class ClientTests(unittest.TestCase):
    def test_chat_client_talks_openai_protocol(self):
        with FakeOpenAIServer(lambda body: ok_message(text("selam dünya"))) as server:
            client = ChatClient(server.url, "qwen", "local", timeout=5)
            message = client.chat([{"role": "user", "content": "selam"}], tools=[{"type": "function"}])
            self.assertEqual(message["content"], "selam dünya")
            request = server.requests[0]
            self.assertEqual(request["model"], "qwen")
            self.assertEqual(request["tool_choice"], "auto")
            self.assertFalse(request["stream"])

    def test_http_error_carries_status(self):
        with FakeOpenAIServer(lambda body: (400, {"error": "tools unsupported"})) as server:
            with self.assertRaises(LLMError) as ctx:
                ChatClient(server.url, "m", timeout=5).chat([{"role": "user", "content": "x"}])
            self.assertEqual(ctx.exception.status, 400)

    def test_connection_error_is_friendly(self):
        client = ChatClient("http://127.0.0.1:9/v1", "m", timeout=2)
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "x"}])
        self.assertIn("bağlanılamadı", str(ctx.exception))

    def test_full_agent_loop_over_http(self):
        def responder(body):
            has_tool_result = any(m.get("role") == "tool" for m in body["messages"])
            if not has_tool_result:
                return ok_message(native_call("now", {}))
            return ok_message(text("Saat bilgisini aldım."))

        with tempfile.TemporaryDirectory() as tmp, FakeOpenAIServer(responder) as server:
            registry = ToolRegistry(build_default_tools(Path(tmp)))
            agent = Agent(ChatClient(server.url, "m", timeout=5), registry, ALLOW)
            self.assertEqual(agent.ask("saat kaç?"), "Saat bilgisini aldım.")
            self.assertEqual(agent.trace[0]["tool"], "now")


class ServerTests(unittest.TestCase):
    def _serve(self, settings, responder=None):
        llm_server = FakeOpenAIServer(responder or (lambda b: ok_message(text("cevap"))))
        llm_server.__enter__()
        settings = Settings(**{**settings.__dict__, "base_url": llm_server.url})
        service = ChatService(settings)
        server = create_server(settings, service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), llm_server.__exit__(None, None, None)))
        return f"http://127.0.0.1:{server.server_address[1]}"

    def _post(self, url, payload, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_index_health_and_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._serve(Settings(workspace=Path(tmp)))
            with urllib.request.urlopen(base + "/") as response:
                self.assertIn("Rebelz", response.read().decode("utf-8"))
            with urllib.request.urlopen(base + "/api/health") as response:
                self.assertTrue(json.loads(response.read())["ok"])
            status, data = self._post(base + "/api/chat", {"session": "s1", "message": "selam"})
            self.assertEqual(status, 200)
            self.assertEqual(data["reply"], "cevap")
            status, data = self._post(base + "/api/chat", {"session": "s1", "message": ""})
            self.assertEqual(status, 400)

    def test_token_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._serve(Settings(workspace=Path(tmp), web_token="gizli"))
            status, _ = self._post(base + "/api/chat", {"message": "selam"})
            self.assertEqual(status, 401)
            status, data = self._post(base + "/api/chat", {"message": "selam"}, token="yanlis")
            self.assertEqual(status, 401)
            status, data = self._post(base + "/api/chat", {"message": "selam"}, token="gizli")
            self.assertEqual(status, 200)

    def test_public_bind_requires_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                create_server(Settings(workspace=Path(tmp), web_host="0.0.0.0", web_port=0))

    def test_llm_failure_returns_502(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(workspace=Path(tmp), base_url="http://127.0.0.1:9/v1", llm_timeout=2)
            server = create_server(settings, ChatService(settings))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(lambda: (server.shutdown(), server.server_close()))
            status, data = self._post(f"http://127.0.0.1:{server.server_address[1]}/api/chat", {"message": "x"})
            self.assertEqual(status, 502)
            self.assertIn("bağlanılamadı", data["error"])


if __name__ == "__main__":
    unittest.main()
