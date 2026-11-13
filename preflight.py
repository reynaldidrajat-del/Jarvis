"""Exercise the live Jarvis dashboard and its configured model provider."""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "whisper"))

from jarvis_memory import NOTES_ROOT
from jarvis_vision import MODEL_ENDPOINTS, provider_headers, read_vision_config
from server import ASSETS, SESSION_FILE


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


HTTP = build_opener(NoRedirect())
SCREEN_FIXTURE = ROOT / "whisper" / "fixtures" / "preflight-screen.jpg"
VOICE_FIXTURE = ROOT / "whisper" / "uji-audio" / "campuran.mp3"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def json_object(body):
    value = json.loads(body)
    require(isinstance(value, dict), "Expected a JSON object.")
    return value


class Preflight:
    def __init__(self, url, notes_dir, timeout):
        self.url = url
        self.origin = ""
        self.notes_dir = notes_dir
        self.timeout = timeout
        self.graph = None

    def fetch(self, route, *, data=None, headers=None, absolute=False, timeout=None):
        require(self.origin, "Start the Jarvis dashboard or pass its live URL with --url.")
        url = (self.origin + route) if absolute else (self.url + route)
        request = Request(url, data=data, headers={"Cache-Control": "no-cache", **(headers or {})})
        with HTTP.open(request, timeout=timeout if timeout is not None else min(12, self.timeout)) as response:
            return response.headers, response.read()

    def get_json(self, route):
        return json_object(self.fetch(route)[1])

    def viewer(self):
        parts = urlsplit(self.url)
        require(parts.scheme == "http" and parts.netloc == f"127.0.0.1:{parts.port}" and
                not parts.query and not parts.fragment and parts.path.startswith("/"),
                "Start Jarvis in browser mode or pass its http://127.0.0.1:<port>/<session>/ URL with --url.")
        self.origin = f"http://{parts.netloc}"
        headers, body = self.fetch("")
        require(headers.get_content_type() == "text/html" and b'id="galaxy"' in body,
                "The dashboard did not serve the Jarvis viewer.")
        self.get_json("api/state")
        return "Viewer HTML and live state loaded."

    def graph_data(self):
        graph = self.get_json("api/notes")
        nodes, links = graph.get("nodes"), graph.get("links")
        require(isinstance(nodes, list) and bool(nodes), "The graph has zero nodes or an invalid nodes array.")
        require(all(isinstance(node, dict) and type(node.get("id")) is int and node["id"] == index and
                    isinstance(node.get("key"), str) and isinstance(node.get("label"), str)
                    for index, node in enumerate(nodes)), "The graph contains malformed or misindexed nodes.")
        require(isinstance(links, list) and all(isinstance(link, dict) and all(
                    type(link.get(end)) is int and 0 <= link[end] < len(nodes) for end in ("source", "target"))
                    for link in links), "The graph contains malformed links.")
        return graph

    def load_graph(self):
        self.graph = self.graph_data()
        return f"{len(self.graph['nodes'])} indexed nodes."

    def turn(self, route, body=None, *, jpeg=None, question=None):
        token = secrets.token_hex(16)
        headers = {"Origin": self.origin}
        if jpeg is None:
            payload = json.dumps({**body, "turn": token}).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            payload = jpeg
            headers.update({"Content-Type": "image/jpeg", "X-Jarvis-Question": quote(question, safe=""),
                            "X-Jarvis-Turn": token})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if not self.get_json("api/state").get("busy"):
                try:
                    acknowledgement = json_object(self.fetch(route, data=payload, headers=headers)[1])
                    require(acknowledgement.get("ok") is True, f"/{route} did not accept the request.")
                    break
                except HTTPError as error:
                    if error.code != 409:
                        raise
            time.sleep(.2)
        else:
            raise RuntimeError(f"/{route} could not start while Jarvis was busy.")

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            state = self.get_json("api/state")
            reply = state.get("reply")
            if not state.get("busy") and isinstance(reply, dict) and reply.get("turn") == token:
                answer = reply.get("answer")
                nodes = reply.get("nodes")
                require(isinstance(answer, str) and bool(answer.strip()) and isinstance(nodes, list) and
                        all(type(index) is int and index >= 0 for index in nodes) and len(nodes) == len(set(nodes)),
                        f"/{route} did not return a nonempty answer and valid nodes array.")
                return reply
            time.sleep(.2)
        raise RuntimeError(f"/{route} accepted the request but no matching reply arrived.")

    def verify_source(self, reply, key):
        nodes = self.graph_data()["nodes"]
        indices = reply["nodes"]
        require(all(index < len(nodes) for index in indices) and any(nodes[index]["key"] == key for index in indices),
                "The answer did not cite the expected live graph node.")

    def chat(self):
        require(self.graph, "A live graph is needed to choose a grounded question.")
        choices = [node for node in self.graph["nodes"] if isinstance(node.get("excerpt"), str) and node["excerpt"].strip()]
        require(choices, "No indexed note has text to ask about.")
        note = next((node for node in choices if node["label"] == "Booking Entry"), choices[0])
        if note["label"] == "Booking Entry":
            question = "In my notes, what is the purpose of Booking Entry according to the Booking Entry material?"
        else:
            question = f'In my notes, what does the note titled "{note["label"]}" say? State one specific fact from it.'
        reply = self.turn("chat", {"question": question, "context": "knowledge"})
        self.verify_source(reply, note["key"])
        return f"Answered from an indexed note with {len(reply['nodes'])} cited node(s)."

    def models(self, provider, key, path):
        request = Request(MODEL_ENDPOINTS[provider] + path, headers=provider_headers(provider, key))
        try:
            with HTTP.open(request, timeout=min(15, self.timeout)) as response:
                return json_object(response.read())
        except HTTPError as error:
            code = ""
            try:
                detail = json_object(error.read(16384)).get("error", {})
                candidate = (detail.get("status") or detail.get("code")) if isinstance(detail, dict) else None
                if isinstance(candidate, str) and re.fullmatch(r"[a-zA-Z_]{1,60}", candidate):
                    code = f" {candidate}"
            except (ValueError, RuntimeError):
                pass
            raise RuntimeError(f"{provider} returned HTTP {error.code}{code}.") from None

    def api_key(self):
        provider, _, key = read_vision_config()
        if provider == "gemini":
            result = self.models(provider, key, "?pageSize=1")
            valid = isinstance(result.get("models"), list)
        else:
            result = self.models(provider, key, "")
            valid = result.get("object") == "list" and isinstance(result.get("data"), list)
        require(valid, f"{provider} did not return a valid authenticated models response.")
        return f"{provider} authenticated the configured credential."

    def model(self):
        provider, model, key = read_vision_config()
        result = self.models(provider, key, "/" + quote(model, safe=""))
        if provider == "gemini":
            methods = result.get("supportedGenerationMethods")
            valid = result.get("name") == f"models/{model}" and isinstance(methods, list) and "generateContent" in methods
        else:
            valid = result.get("object") == "model" and result.get("id") == model
        require(valid, f"{provider} did not confirm access to the configured model ID.")
        return f"{provider} confirmed access to {model}."

    def remember(self):
        marker = "preflight" + secrets.token_hex(6)
        verification = "orchid-" + secrets.token_hex(4)
        captures = (self.notes_dir / "captures").resolve()
        require(captures.parent == self.notes_dir, "The captures folder is outside the configured notes directory.")
        text = (f"Preflight record {marker} verifies immediate local retrieval.\n\n"
                f"The verification word for record {marker} is {verification}.")
        try:
            reply = self.turn("remember", {"text": text, "language": "en"})
            capture = reply.get("capture")
            require(isinstance(capture, dict) and isinstance(capture.get("key"), str),
                    "/remember did not report a saved Markdown file.")
            key = capture["key"]
            note = (self.notes_dir / key).resolve()
            require(note.parent == captures and note.suffix == ".md" and note.is_file() and
                    text in note.read_text(encoding="utf-8"), "The expected capture was not written to disk.")

            question = f"In my notes, what exact verification word is stored in preflight record {marker}?"
            reply = self.turn("chat", {"question": question, "context": "knowledge"})
            require(verification in reply["answer"].casefold(), "The next /chat answer did not retrieve the saved verification word.")
            self.verify_source(reply, key)
        finally:
            for candidate in captures.glob(f"*-preflight-record-{marker}-*.md"):
                if candidate.resolve().parent == captures and text in candidate.read_text(encoding="utf-8"):
                    candidate.unlink()
        return "Real Markdown written, cited by the next /chat turn, and removed."

    def see(self):
        jpeg = SCREEN_FIXTURE.read_bytes()
        require(jpeg.startswith(b"\xff\xd8\xff") and jpeg.endswith(b"\xff\xd9"), "The screen fixture is not a JPEG.")
        reply = self.turn("see", jpeg=jpeg, question="What inventory sheet is visible, and how many units are listed?")
        answer = reply["answer"]
        if answer.startswith(("OpenAI ", "Gemini ", "Kunci ", "Model layar", "Jarvis belum bisa membaca konfigurasi")):
            raise RuntimeError(answer)
        require("orchid" in answer.casefold() and re.search(r"\b7[,.\s]?342\b", answer) and not reply["nodes"],
                "The live /see answer did not identify the label and quantity visible in the JPEG.")
        return "The model read Orchid Cafe and 7342 from a real image/jpeg request."

    def assets(self):
        for route, (filename, mime) in ASSETS.items():
            headers, served = self.fetch(route)
            require(headers.get_content_type() == mime.split(";")[0] and
                    served == (ROOT / "whisper" / "web" / filename).read_bytes(),
                    f"{filename} differs from the served asset. Restart Jarvis and rerun.")
        return f"All {len(ASSETS)} served viewer assets match disk byte for byte."

    def config_private(self):
        for route, absolute in [("config.json", False), ("web/config.json", False), ("api/config.json", False),
                                ("/config.json", True), ("/notes-galaxy/config.json", True)]:
            require(self.origin, "Cannot verify browser isolation until the live viewer is reachable.")
            target = self.origin + route if absolute else self.url + route
            try:
                with HTTP.open(Request(target), timeout=min(8, self.timeout)) as response:
                    status = response.status
            except HTTPError as error:
                status = error.code
            require(status in (401, 403, 404, 410), f"SECURITY FAILURE: {route} is not blocked (HTTP {status}).")
        return "Browser-facing config paths are blocked."

    def local_transcription(self):
        try:
            audio = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(VOICE_FIXTURE),
                 "-ac", "1", "-c:a", "libopus", "-f", "webm", "pipe:1"],
                capture_output=True, check=True, timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError("FFmpeg could not prepare the mixed-language WebM fixture.") from None
        _, body = self.fetch("api/transcribe", data=audio, timeout=self.timeout,
                             headers={"Origin": self.origin, "Content-Type": "audio/webm;codecs=opus"})
        text = json_object(body).get("text")
        require(isinstance(text, str) and re.findall(r"\w+", text.casefold()) == ["tolong", "open", "ms", "word", "ya"],
                "Whisper did not transcribe the complete mixed-language test sentence.")
        return "Local Whisper read the full Indonesian–English WebM fixture."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Live Jarvis browser URL. Defaults to the local session published by Jarvis.")
    parser.add_argument("--notes-dir", type=Path, help="Override the live notes directory for capture verification.")
    parser.add_argument("--timeout", type=float, default=65, help="Maximum seconds to wait for a turn (default: 65).")
    args = parser.parse_args()
    if not 0 < args.timeout <= 600:
        parser.error("--timeout must be greater than zero and at most 600 seconds")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        session = json_object(SESSION_FILE.read_bytes())
    except (OSError, ValueError, RuntimeError):
        session = {}
    session_url = session.get("url") if isinstance(session.get("url"), str) else ""
    url = args.url or session_url
    session_notes = session.get("notes_dir") if url == session_url and isinstance(session.get("notes_dir"), str) else None
    notes_dir = args.notes_dir or session_notes
    notes_dir = Path(notes_dir or os.environ.get("JARVIS_NOTES_DIR") or NOTES_ROOT).resolve()
    checks = Preflight(url.rstrip("/") + "/", notes_dir, args.timeout)
    ordered = [
        ("Viewer server", checks.viewer),
        ("Graph data", checks.load_graph),
        ("/chat", checks.chat),
        ("Config API key", checks.api_key),
        ("Configured model", checks.model),
        ("/remember → /chat", checks.remember),
        ("/see with JPEG", checks.see),
        ("Served files match disk", checks.assets),
        ("Browser cannot reach config.json", checks.config_private),
        ("Local Whisper transcription", checks.local_transcription),
    ]
    passed = failed = 0
    for label, check in ordered:
        try:
            details = check()
        except HTTPError as error:
            failed += 1
            print(f"✗ {label} — HTTP {error.code}", flush=True)
        except (URLError, OSError, ValueError, RuntimeError) as error:
            failed += 1
            print(f"✗ {label} — {error}", flush=True)
        else:
            passed += 1
            print(f"✓ {label} — {details}", flush=True)
    print(f"{passed} pass, {failed} fail, 0 warn", flush=True)
    return int(failed > 0)


if __name__ == "__main__":
    sys.exit(main())
