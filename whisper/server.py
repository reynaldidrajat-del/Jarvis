import hashlib
import json
import os
import queue
import secrets
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from urllib.parse import unquote, urlsplit

from jarvis_memory import material_key


# JARVIS SOUL — edit this block to change the character, spoken style, and stock lines.
PERSONA = """\
You are Jarvis, a dry, impeccably polite British butler with a razor wit.
Use British English when speaking English and reply naturally in Indonesian when the user speaks Indonesian.
Address the user as 'sir' occasionally, at most once per reply, and skip it if you used it in a recent turn.
Give the requested facts plainly, with at most one short, dry line of wit. Prefer one concise sentence.
Use a second only if separating the aside from the facts makes the answer clearer. If a good line does not
suggest itself, be brief. Never force a joke, flatter, or pad the answer.
Paraphrase notes in your own words for speech. Preserve necessary names, numbers and technical terms,
and leave the original prose and excerpts on screen.
If the notes cannot answer, admit it plainly and with dignity. Never invent a fact or source or pass off a
related note as evidence. Keep social replies short and independent of the user's notes.
"""
BOOT_LINE = "Good {period}, sir. {count:,} {noun} indexed, all present and accounted for."
BOOT_PERIODS = ((5, "morning"), (12, "afternoon"), (18, "evening"))
BOOT_NOUNS = ("note", "notes")
BOOT_LANGUAGE = "en-GB"
HELLO_LINES = ("At your service, and mercifully free of meetings.", "Siap membantu, dan untungnya bebas rapat.")
NO_NOTE_LINES = ("I'm afraid your notes don't cover that.", "Catatan Anda belum memuat jawabannya.")
NO_SLIDE_LINES = ("I'm afraid the presentation doesn't cover that.", "Materi presentasi belum memuat jawabannya.")
CAPTURE_LINES = ("Filed away, and the galaxy can stop worrying.", "Sudah saya arsipkan. Galaksi boleh tenang sekarang.")
CONVERSATION_VOICE = "Speak in the language of the supplied text, with restrained warmth and dry timing. Use a British accent in English."
# END JARVIS SOUL


def boot_greeting(count, *, now=None):
    hour = (now if now is not None else datetime.now()).hour
    period = next((label for start, label in reversed(BOOT_PERIODS) if hour >= start), BOOT_PERIODS[-1][1])
    return BOOT_LINE.format(period=period, count=count, noun=BOOT_NOUNS[count != 1])


ASSETS = {
    "": ("index.html", "text/html; charset=utf-8"),
    "style.css": ("style.css", "text/css; charset=utf-8"),
    "app.js": ("app.js", "text/javascript; charset=utf-8"),
    "speech.mjs": ("speech.mjs", "text/javascript; charset=utf-8"),
    "recording.mjs": ("recording.mjs", "text/javascript; charset=utf-8"),
}
MAX_SCREEN_JPEG = 6 * 1024 * 1024
MAX_AUDIO_UPLOAD = 4 * 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = hashlib.sha256(os.fsencode(PROJECT_ROOT)).hexdigest()[:16]
SESSION_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis" / f"browser-{PROJECT_ID}.json"


class BrowserNarrator:
    def __init__(self, client, scripts, paused, muted):
        self.client = client
        self.scripts = scripts
        self.paused = paused
        self.muted = muted
        self.last_seen = monotonic()
        self.active = None

    @property
    def current(self):
        active = self.active
        return active[0] if active else None

    def __call__(self, number, stop, still_presenting):
        script = self.scripts[number]
        turn = secrets.token_urlsafe(12)
        result = queue.Queue(maxsize=1)
        self.active = ({"client": self.client, "turn": turn, "text": script, "slide": number}, result)
        # Muted tabs preserve slide timing without synthesizing audio.
        limit = max(2, len(script.split()) / 2.5) if self.muted else 90
        elapsed = 0
        last = monotonic()
        try:
            while not stop.is_set() and still_presenting():
                now = monotonic()
                if now - self.last_seen > 90:
                    raise RuntimeError("Tab yang membacakan presentasi terputus.")
                if not self.paused.is_set():
                    elapsed += now - last
                    if not result.empty():
                        if result.get_nowait():
                            return True
                        raise RuntimeError("Browser gagal membacakan slide.")
                    if elapsed >= limit:
                        if self.muted:
                            return True
                        raise RuntimeError("Browser belum menyelesaikan narasi slide.")
                last = now
                stop.wait(.12)
            return False
        finally:
            self.active = None

    def complete(self, client, turn, ok):
        active = self.active
        if self.muted or client != self.client or not active or active[0]["turn"] != turn or type(ok) is not bool:
            return False
        try:
            active[1].put_nowait(ok)
        except queue.Full:
            pass
        return True


class JarvisWeb:
    def __init__(self, app):
        self.app = app
        self.path = f"/{secrets.token_urlsafe(24)}/"
        self.requests = queue.Queue()
        self.state = {}
        self.deck = {"slides": []}
        self.deck_key = None
        self.deck_version = 0
        self.next_note_scan = 0
        self.narrator = None
        self.assets = {
            route: ((Path(__file__).with_name("web") / name).read_bytes(), mime)
            for route, (name, mime) in ASSETS.items()
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.url = self.origin + self.path
        self.poll()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        try:
            self.write_session_file()
        except OSError:
            print("Preflight could not record the dashboard URL. Use preflight.py --url.", flush=True)

    def write_session_file(self):
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=SESSION_FILE.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                json.dump({"url": self.url, "notes_dir": str(self.app.memory.folder)}, handle)
            except OSError:
                handle.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(SESSION_FILE)
        finally:
            temporary.unlink(missing_ok=True)

    def open(self):
        webbrowser.open(self.url)

    def poll(self):
        for _ in range(12):
            try:
                action, body, result = self.requests.get_nowait()
            except queue.Empty:
                break
            try:
                result.put(self.perform(action, body))
            except Exception:
                result.put(False)
        app = self.app
        if monotonic() >= self.next_note_scan:
            self.next_note_scan = monotonic() + 2
            try:
                app.memory.refresh()
            except OSError:
                # Keep serving the last valid graph while a note is being edited.
                pass
        key = (str(app.deck.path), app.deck.stamp) if app.deck else None
        if key != self.deck_key:
            slides = [
                {"number": slide.number, "title": slide.title, "section": slide.section,
                 "text": slide.text, "notes": slide.notes}
                for slide in app.deck.slides
            ] if app.deck else []
            self.deck = {"name": app.deck.path.stem if app.deck else "", "slides": slides,
                         "sourceKey": material_key(app.deck.path) if app.deck else None}
            self.deck_key = key
            self.deck_version += 1
        status = app.status.get()
        presenting = app.finish_button.instate(("!disabled",))
        if app.presentation_question_pending:
            phase = "thinking"
        elif app.presentation_paused.is_set():
            phase = "paused"
        elif status.startswith(("Jarvis sedang berbicara", "Presentasi sedang membacakan")):
            phase = "speaking"
        else:
            phase = "thinking" if app.busy else "ready"
        self.state = {
            "status": status, "phase": phase, "busy": app.busy, "qa": app.in_qa,
            "deckVersion": self.deck_version, "transcript": app.web_transcript, "reply": app.web_reply,
            "memoryVersion": app.memory.version,
            "presenting": presenting, "paused": app.presentation_paused.is_set(),
            "questionPending": app.presentation_question_pending,
            "canStop": app.stop_button.instate(("!disabled",)),
            "narration": self.narrator.current if self.narrator and not app.presentation_stop.is_set() else None,
        }
        app.root.after(100, self.poll)

    def perform(self, action, body):
        app = self.app
        during_presentation = (action == "ask" and app.finish_button.instate(("!disabled",))
                               and not app.presentation_stop.is_set())
        if (action in {"ask", "remember", "see"} and (not app.busy or during_presentation)
                and not app.presentation_question_pending):
            if action == "see" and not isinstance(body.get("jpeg"), bytes):
                return False
            question = body.get("text" if action == "remember" else "question")
            if not isinstance(question, str) or len(question) > 4000:
                return False
            if action == "remember":
                prefix = "ingat bahwa " if body.get("language") == "id" else "remember that "
                question = prefix + question
            if not question.strip():
                return False
            turn = body.get("turn", "")
            if not isinstance(turn, str) or len(turn) > 80:
                return False
            question = question.strip()
            if action == "ask" and body.get("spoken") is True:
                from jarvis_presenter import clean_spoken_question

                question = clean_spoken_question(question)
                if not question:
                    return False
            if during_presentation:
                if not app.presentation_paused.is_set():
                    app.toggle_pause()
                narration = self.narrator.current if self.narrator else None
                app.submit_question(question, browser_turn=turn, during_presentation=True,
                                    active_slide=narration["slide"] if narration else None)
                return True
            if action == "ask" and self.start_requested_presentation(question, turn, body):
                return True
            app.submit_question(question, browser_turn=turn, general=body.get("context") == "knowledge",
                                screen_jpeg=body["jpeg"] if action == "see" else None)
        elif action == "choose" and not app.busy and not app.presentation_question_pending:
            app.root.after_idle(app.choose_deck)
        elif action == "present" and app.present_button.instate(("!disabled",)) and not app.presentation_question_pending:
            client = body.get("client")
            if not isinstance(client, str) or not 0 < len(client) <= 80:
                return False
            self.start_browser_presentation(client, body.get("mute") is True)
        elif action == "narration-done" and self.narrator:
            return self.narrator.complete(body.get("client"), body.get("turn"), body.get("ok"))
        elif action == "pause" and app.pause_button.instate(("!disabled",)):
            paused = body.get("paused")
            if app.presentation_question_pending and paused is not True:
                return False
            if type(paused) is not bool or paused != app.presentation_paused.is_set():
                app.toggle_pause()
        elif action == "finish" and app.finish_button.instate(("!disabled",)):
            app.end_and_start_qa()
        elif action == "stop" and app.stop_button.instate(("!disabled",)):
            app.stop_presentation()
        else:
            return False
        return True

    def start_browser_presentation(self, client, muted):
        self.narrator = BrowserNarrator(client, self.app.narration, self.app.presentation_paused, muted)
        self.app.start_presentation(narrator=self.narrator)

    def start_requested_presentation(self, question, turn, body):
        from jarvis_presenter import presentation_request, resolve_presentation

        client = body.get("client")
        if not isinstance(client, str) or not 0 < len(client) <= 80:
            return False
        title = presentation_request(question)
        if title is None:
            return False
        app = self.app

        def reply(answer, *, starting=False):
            app.web_transcript = question
            kind = "presentation_reply" if starting else "reply"
            app.messages.put((kind, (question, answer, "", [], turn, None, [])))

        try:
            deck_path = resolve_presentation(title, app.deck)
        except RuntimeError as error:
            reply(str(error))
            return True

        def complete(error):
            if error:
                reply(error)
                return
            self.start_browser_presentation(client, body.get("mute") is True)
            reply(f"Memulai slideshow {deck_path.stem} di PowerPoint.", starting=True)

        app.prepare_deck(deck_path, complete=complete)
        return True

    def handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send(self, content, mime, status=200):
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(content)

            def route(self):
                if self.headers.get("Host") != bridge.origin.removeprefix("http://"):
                    return None
                path = urlsplit(self.path).path
                if path.startswith(bridge.path):
                    return path[len(bridge.path):]
                return None

            def json(self, value, status=200):
                self.send(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8", status)

            def do_GET(self):
                route = self.route()
                if route in bridge.assets:
                    self.send(*bridge.assets[route])
                elif route == "api/state":
                    narrator = bridge.narrator
                    if narrator and self.headers.get("X-Jarvis-Client") == narrator.client:
                        narrator.last_seen = monotonic()
                    self.json(bridge.state)
                elif route == "api/deck":
                    self.json(bridge.deck)
                elif route == "api/notes":
                    graph = bridge.app.memory.graph
                    self.json({**graph, "greeting": boot_greeting(len(graph["nodes"])),
                               "greetingLanguage": BOOT_LANGUAGE})
                else:
                    self.json({"ok": False}, 404)

            def do_POST(self):
                route = self.route()
                if not route or (route not in {"chat", "remember", "see"} and not route.startswith("api/")) or self.headers.get("Origin") != bridge.origin:
                    self.json({"ok": False}, 403)
                    return
                if route == "api/transcribe":
                    self.transcribe()
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= (MAX_SCREEN_JPEG if route == "see" else 18000):
                        raise ValueError
                    if route == "see":
                        if self.headers.get_content_type() != "image/jpeg":
                            self.json({"ok": False, "error": "Jarvis memerlukan gambar image/jpeg."}, 415)
                            return
                        question = unquote(self.headers.get("X-Jarvis-Question", ""), errors="strict").strip()
                        turn = self.headers.get("X-Jarvis-Turn", "")
                        if not 0 < len(question) <= 4000 or not 0 < len(turn) <= 80:
                            raise ValueError
                        jpeg = self.rfile.read(size)
                        if len(jpeg) != size or not jpeg.startswith(b"\xff\xd8\xff") or not jpeg.endswith(b"\xff\xd9"):
                            raise ValueError
                        data = {"question": question, "turn": turn, "jpeg": jpeg}
                    else:
                        data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict):
                        raise ValueError
                except (ValueError, UnicodeError):
                    self.json({"ok": False}, 400)
                    return
                if route == "api/reply-audio":
                    self.reply_audio(data)
                    return
                result = queue.Queue(maxsize=1)
                action = {"chat": "ask", "remember": "remember", "see": "see"}.get(route, route[4:])
                bridge.requests.put((action, data, result))
                try:
                    accepted = result.get(timeout=5)
                except queue.Empty:
                    accepted = False
                self.json({"ok": accepted}, 200 if accepted else 409)

            def reply_audio(self, data):
                from jarvis_voice import synthesize_reply

                message = data.get("text")
                language = data.get("language")
                if not isinstance(message, str) or not 0 < len(message.strip()) <= 1000 or language not in ("id", "en"):
                    self.json({"ok": False}, 400)
                    return
                try:
                    audio = synthesize_reply(message.strip(), language)
                except RuntimeError:
                    self.json({"ok": False}, 503)
                    return
                self.send(audio, "audio/wav")

            def transcribe(self):
                from uji_mikrofon import WEB_AUDIO_FORMATS, WEB_TRANSCRIPTION, transcribe_browser_audio

                mime = self.headers.get_content_type()
                if mime not in WEB_AUDIO_FORMATS:
                    self.json({"error": "Format rekaman mikrofon browser belum didukung."}, 415)
                    return
                mode = self.headers.get("X-Jarvis-Language", "mix")
                if mode not in ("id", "en", "mix"):
                    self.json({"error": "Pilih bahasa ID, EN, atau Campuran."}, 400)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= MAX_AUDIO_UPLOAD:
                        raise ValueError
                except ValueError:
                    self.json({"error": "Rekaman kosong atau terlalu besar."}, 400)
                    return
                data = self.rfile.read(size)
                if len(data) != size:
                    self.json({"error": "Rekaman tidak terkirim lengkap."}, 400)
                    return
                if not WEB_TRANSCRIPTION.acquire(blocking=False):
                    self.json({"error": "Whisper sedang membaca rekaman lain. Coba lagi sebentar."}, 409)
                    return
                try:
                    titles = [node["label"] for node in bridge.app.memory.graph["nodes"] if node.get("slides")]
                    text = transcribe_browser_audio(data, mime, titles, language=None if mode == "mix" else mode)
                except ValueError as error:
                    self.json({"error": str(error)}, 422)
                except (OSError, RuntimeError):
                    self.json({"error": "Whisper lokal belum siap membaca rekaman."}, 503)
                else:
                    self.json({"text": text})
                finally:
                    WEB_TRANSCRIPTION.release()

        return Handler
