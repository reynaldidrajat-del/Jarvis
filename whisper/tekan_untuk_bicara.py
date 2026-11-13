import queue
import re
import sqlite3
import sys
import threading
import tkinter as tk
import winreg
from ctypes import windll
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk

import numpy as np
import sounddevice as sd

from jarvis_actions import (
    agent_desktop,
    answer_from_memory,
    answer_social,
    fill_excel_diagonal,
    find_windows_app,
    format_excel_rows,
    localised_line,
    plan_with_gemini,
    search_local_files,
)
from jarvis_galaxy import SlideGalaxy
from jarvis_memory import MATERIALS_ROOT, JarvisMemory, REMEMBER, material_key, material_title
from jarvis_vision import answer_from_screen
from jarvis_presenter import (
    answer_question,
    audio_for,
    load_deck,
    play_audio,
    prepare_audio,
    prepare_narration,
    present_deck,
    referenced_material,
)
from server import CAPTURE_LINES, CONVERSATION_VOICE, HELLO_LINES, JarvisWeb
from uji_mikrofon import (
    capture_microphone,
    load_local_whisper,
    resample_for_whisper,
    transcribe_microphone,
    wasapi_devices,
)


HEADSET = "Headset (Baseus Bowie E13)"
MAX_RECORDING_SECONDS = 30
TEAM_FARM_SPELLING = re.compile(r"\bteam[ \t]+(?:fam|farm)\b", re.IGNORECASE)
RECALL = re.compile(
    r"\b(?:my (?:notes|memory)|what did i (?:say|tell)|what have i (?:said|told)"
    r"|catatan saya|ingatan kamu|yang (?:pernah|sudah|tadi) saya (?:bilang|katakan|sampaikan|catat))\b",
    re.I,
)
GREETING = re.compile(r"(?:halo|hai|hi|hello|hey|good (?:morning|afternoon|evening)|selamat (?:pagi|siang|sore|malam))(?:[, ]+jarvis)?[.!?\s]*", re.I)
JOKE_REQUEST = re.compile(r"^(?:(?:please|tolong|can you|could you)\s+)?(?:tell me (?:a|another) joke\b|(?:ceritakan|kasih)\s+(?:saya\s+)?(?:lelucon|joke)\b)", re.I)
SOCIAL = re.compile(r"(?:how are you(?: doing)?|how's it going|apa kabar|thanks|thank you|terima kasih|makasih)(?:[, ]+jarvis)?[.!?\s]*", re.I)
APPLICATIONS = {
    "excel": ("EXCEL.EXE", "Microsoft Excel"),
    "outlook": ("OUTLOOK.EXE", "Outlook Classic"),
    "word": ("WINWORD.EXE", "Microsoft Word"),
}


def open_application(app):
    normalized = re.sub(r"^(?:microsoft|ms)\s+", "", app.casefold()).strip()
    known = APPLICATIONS.get(normalized)
    if known:
        executable, label = known
        try:
            key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as app_path:
                path = winreg.QueryValue(app_path, None)
        except OSError:
            return f"{label} tidak ditemukan di Windows."
    else:
        installed = find_windows_app(app)
        label, path = installed["name"], installed["path"]

    reply = agent_desktop("launch", path, "--activate", "--timeout", "12000")
    if reply.get("ok") and reply.get("data", {}).get("window"):
        return f"{label} sudah terbuka."
    return f"Agent Desktop tidak bisa memastikan jendela {label} terbuka."


class PushToTalk:
    def __init__(self, root, *, load_whisper=True):
        self.root = root
        self.messages = queue.Queue()
        self.stop_recording = None
        self.button_held = False
        self.model = None
        self.busy = False
        self.deck = None
        self.narration = {}
        self.in_qa = False
        self.qa_history = []
        self.conversation = []
        self.memory = JarvisMemory(deck_loader=load_deck)
        self.reply_stop = threading.Event()
        self.presentation_stop = threading.Event()
        self.presentation_paused = threading.Event()
        self.presentation_question_pending = False
        self.finish_to_qa = False
        self.web = None
        self.web_transcript = ""
        self.web_reply = None
        self.web_reply_number = 0

        root.title("Jarvis — Tekan untuk Bicara")
        root.geometry("1150x640")
        root.minsize(900, 520)
        root.protocol("WM_DELETE_WINDOW", self.close)

        layout = ttk.Frame(root, padding=20)
        layout.pack(fill="both", expand=True)
        layout.columnconfigure(1, weight=1)
        layout.rowconfigure(0, weight=1)
        panel = ttk.Frame(layout, padding=(0, 0, 12, 0), width=475)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.pack_propagate(False)
        self.galaxy = SlideGalaxy(layout)
        self.galaxy.grid(row=0, column=1, sticky="nsew")
        ttk.Label(panel, text="Jarvis", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Button(panel, text="Buka dashboard browser", command=self.open_browser).pack(anchor="w", pady=(4, 0))
        ttk.Label(panel, text="Tahan tombol atau spasi. Lepas saat selesai bicara.").pack(anchor="w", pady=(4, 16))

        self.button = ttk.Button(panel, text="Tahan untuk bicara", state="disabled", takefocus=True)
        self.button.pack(fill="x", ipady=10)
        self.button.bind("<ButtonPress-1>", self.press)
        self.button.bind("<ButtonRelease-1>", self.release)
        self.button.bind("<KeyPress-space>", self.press)
        self.button.bind("<KeyRelease-space>", self.release)
        self.button.bind("<FocusOut>", self.release)

        ttk.Label(panel, text="Tanya atau beri perintah").pack(anchor="w", pady=(14, 4))
        text_bar = ttk.Frame(panel)
        text_bar.pack(fill="x")
        self.command_input = ttk.Entry(text_bar)
        self.command_input.pack(side="left", fill="x", expand=True)
        self.command_input.bind("<Return>", self.submit_text)
        self.run_button = ttk.Button(text_bar, text="Kirim", command=self.submit_text)
        self.run_button.pack(side="left", padx=(8, 0))

        presentation_bar = ttk.Frame(panel)
        presentation_bar.pack(fill="x", pady=(12, 0))
        self.deck_button = ttk.Button(presentation_bar, text="Siapkan PowerPoint", command=self.choose_deck)
        self.deck_button.pack(side="left")
        self.present_button = ttk.Button(presentation_bar, text="Mulai presentasi", command=self.start_presentation, state="disabled")
        self.present_button.pack(side="left", padx=8)
        playback_bar = ttk.Frame(panel)
        playback_bar.pack(fill="x", pady=(6, 0))
        self.pause_button = ttk.Button(playback_bar, text="Jeda", command=self.toggle_pause, state="disabled")
        self.pause_button.pack(side="left")
        self.finish_button = ttk.Button(playback_bar, text="Selesai & mulai tanya jawab",
                                        command=self.end_and_start_qa, state="disabled")
        self.finish_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(playback_bar, text="Hentikan", command=self.stop_presentation, state="disabled")
        self.stop_button.pack(side="left")

        self.status = tk.StringVar(value="Menyiapkan Whisper dan headset…" if load_whisper else "Menyiapkan Jarvis…")
        status_label = ttk.Label(panel, textvariable=self.status)
        status_label.pack(anchor="w", fill="x", pady=(10, 10))
        panel.bind("<Configure>", lambda event: status_label.configure(wraplength=max(200, event.width - 40)))
        ttk.Label(panel, text="Perintah dan hasil").pack(anchor="w", pady=(0, 4))
        self.output = scrolledtext.ScrolledText(
            panel, height=5, wrap="word", state="disabled", font=("Segoe UI", 11), padx=8, pady=6,
        )
        self.output.pack(fill="both", expand=True)

        if load_whisper:
            threading.Thread(target=self.prepare, daemon=True).start()
        else:
            self.button.configure(text="Gunakan mikrofon di dashboard browser")
        root.after(40, self.poll_messages)

    def prepare(self):
        try:
            wasapi = wasapi_devices()
            devices = [sd.query_devices(index) for index in wasapi["devices"]] if wasapi else []
            matches = [device for device in devices if device["name"] == HEADSET and device["max_input_channels"]]
            if len(matches) != 1:
                raise RuntimeError("Sambungkan headset Baseus Bowie E13, lalu buka ulang jendela ini.")
            self.device = matches[0]["index"]
            self.model = load_local_whisper()
            self.messages.put(("ready", ""))
        except Exception as error:
            self.messages.put(("microphone_error", str(error)))

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.command_input.configure(state=state)
        self.run_button.configure(state=state)
        self.deck_button.configure(state=state)
        self.present_button.configure(state="normal" if not busy and self.deck is not None else "disabled")
        self.stop_button.configure(state="normal" if not busy and self.in_qa else "disabled")
        self.pause_button.configure(text="Jeda", state="disabled")
        self.finish_button.configure(state="disabled")
        if not busy and self.model is not None:
            self.button.configure(state="normal", text="Tahan untuk bicara")

    def submit_text(self, _event=None):
        command = self.command_input.get().strip()
        if command and not self.busy:
            self.command_input.delete(0, "end")
            self.submit_question(command)
        return "break"

    def submit_question(self, command, *, browser_turn=None, general=False, screen_jpeg=None,
                        during_presentation=False, active_slide=None):
        self.web_transcript = ""
        if during_presentation:
            self.presentation_question_pending = True
            self.status.set("Mencari jawaban dari materi presentasi…")
        else:
            self.set_busy(True)
            self.button.configure(state="disabled")
        threading.Thread(target=self.process_command, args=(command,),
                         kwargs={"browser_turn": browser_turn, "general": general, "screen_jpeg": screen_jpeg,
                                 "during_presentation": during_presentation, "active_slide": active_slide},
                         daemon=not self.in_qa).start()

    def open_browser(self):
        if self.web is None:
            self.web = JarvisWeb(self)
            print(f"Dashboard Jarvis — {self.web.url}", flush=True)
        self.web.open()

    def press(self, _event=None):
        if not self.busy and not self.button_held and self.model is not None:
            self.button_held = True
            self.web_transcript = ""
            self.button.focus_set()
            self.set_busy(True)
            self.stop_recording = threading.Event()
            self.status.set("Membuka mikrofon…")
            threading.Thread(target=self.record, args=(self.stop_recording,), daemon=not self.in_qa).start()
        return "break"

    def release(self, _event=None):
        self.button_held = False
        if self.stop_recording is not None and not self.stop_recording.is_set():
            self.stop_recording.set()
            self.show_processing()
        return "break"

    def show_processing(self):
        self.button.configure(state="disabled", text="Memproses ucapan…")
        self.status.set("Mikrofon berhenti. Whisper sedang mengubah audio menjadi teks.")

    def record(self, stop_recording):
        try:
            # WASAPI needs COM initialized on the thread that opens the headset.
            if windll.ole32.CoInitializeEx(None, 0) < 0:
                raise RuntimeError("Windows gagal menyiapkan input headset.")
            try:
                frames, rate, channels = capture_microphone(
                    self.device, MAX_RECORDING_SECONDS, stop_event=stop_recording,
                    on_started=lambda: self.messages.put(("recording", "")),
                )
            finally:
                windll.ole32.CoUninitialize()
            stop_recording.set()
            self.messages.put(("processing", ""))
            if np.max(np.abs(frames)) < 0.001:
                raise RuntimeError("Suara belum terdengar jelas. Tahan tombol lebih lama sambil bicara.")
            audio = resample_for_whisper(frames, rate, channels)
            _, transcript = transcribe_microphone(self.model, audio, strict_quality=True)
            if not transcript:
                raise RuntimeError("Whisper belum yakin dengan ucapannya. Coba ulangi kalimatnya.")
            self.process_command(transcript, correct_spelling=True)
        except (Exception, SystemExit) as error:
            self.messages.put(("error", str(error)))

    def process_command(self, transcript, *, correct_spelling=False, browser_turn=None, general=False, screen_jpeg=None,
                        during_presentation=False, active_slide=None):
        corrected = TEAM_FARM_SPELLING.sub("Team Farm", transcript) if correct_spelling else transcript
        self.messages.put(("transcript_ready", corrected))
        capture = None
        memory_sources = []
        capture_prefix = REMEMBER.match(corrected)
        try:
            if screen_jpeg is not None:
                self.messages.put(("action_progress", "Jarvis sedang membaca cuplikan layar…"))
                answer = answer_from_screen(corrected, screen_jpeg)
                details, sources = "", []
            elif capture_prefix:
                content = corrected[capture_prefix.end():]
                capture = self.memory.capture(content)
                answer = localised_line(CAPTURE_LINES, capture_prefix[0])
                details, sources = "", []
            else:
                warning = ""
                try:
                    turn_id = self.memory.record(transcript)
                except (OSError, sqlite3.Error):
                    turn_id = None
                    warning = "Percakapan ini belum tersimpan ke ingatan."
                try:
                    memories = self.memory.recall(corrected, turn_id)
                except (OSError, sqlite3.Error):
                    memories = []
                    warning = "\n".join(filter(None, [warning, "Ingatan belum bisa dibaca."]))
                answer, details, sources, memory_sources = self.handle_command(
                    corrected, memories, general=general, during_presentation=during_presentation, active_slide=active_slide)
                details = "\n\n".join(filter(None, [details, warning]))
        except Exception as error:
            if isinstance(error, RuntimeError):
                answer = str(error)
            elif capture_prefix:
                answer = ("I couldn't save that note. Please try again." if corrected.lstrip().lower().startswith("remember")
                          else "Catatan belum tersimpan. Coba lagi.")
            else:
                answer = "Jarvis gagal memproses permintaan."
            details, sources = "", []
        self.messages.put(("reply", (corrected, answer, details, sources, browser_turn, capture, memory_sources)))
        status = "Siap menerima pertanyaan atau perintah."
        try:
            if browser_turn is None and not self.reply_stop.is_set():
                self.messages.put(("action_progress", "Menyiapkan suara Jarvis…"))
                sound = audio_for(answer, style=CONVERSATION_VOICE)
                self.messages.put(("action_progress", "Jarvis sedang berbicara…"))
                play_audio(sound, self.reply_stop)
        except (OSError, RuntimeError):
            status = "Jawaban tersedia di layar. Audio Jarvis gagal diputar."
        self.messages.put(("presentation_question_done" if during_presentation else "command_done", status))

    def choose_deck(self):
        project = Path(__file__).resolve().parents[1]
        initial = self.deck.path if self.deck else None
        path = filedialog.askopenfilename(
            parent=self.root, title="Pilih deck PowerPoint",
            initialdir=initial.parent if initial else MATERIALS_ROOT if MATERIALS_ROOT.is_dir() else project,
            initialfile=initial.name if initial else "",
            filetypes=[("PowerPoint", "*.pptx")],
        )
        if path:
            self.prepare_deck(path)

    def prepare_deck(self, path, *, complete=None):
        if self.busy:
            return
        self.set_busy(True)
        self.button.configure(state="disabled")
        self.status.set("Membaca isi slide dan menyiapkan naskah narasi…")

        def work():
            try:
                deck = load_deck(path)
                self.messages.put(("deck_ready", (deck, prepare_narration(deck), complete)))
            except Exception as error:
                self.messages.put(("deck_error", (str(error), complete)))

        threading.Thread(target=work, daemon=True).start()

    def start_presentation(self, *, narrator=None):
        if self.busy or self.deck is None:
            return
        self.in_qa = False
        self.qa_history.clear()
        self.presentation_stop.clear()
        self.presentation_paused.clear()
        self.finish_to_qa = False
        self.set_busy(True)
        self.button.configure(state="disabled")
        self.stop_button.configure(text="Hentikan", state="normal")
        self.status.set("Menyiapkan slideshow…" if narrator else "Menyiapkan audio sebelum slideshow…")

        def progress(message):
            self.messages.put(("action_progress", message))

        def work():
            try:
                sounds = {} if narrator else prepare_audio(self.narration, self.presentation_stop, progress)
                self.messages.put(("presentation_started", ""))
                completed = present_deck(self.deck, sounds, self.presentation_stop, progress,
                                         paused=self.presentation_paused, narrator=narrator)
                self.messages.put(("presentation_finished", completed))
            except Exception as error:
                self.messages.put(("error", str(error)))

        threading.Thread(target=work).start()

    def toggle_pause(self):
        if self.presentation_paused.is_set():
            self.presentation_paused.clear()
            self.pause_button.configure(text="Jeda")
            self.status.set("Melanjutkan narasi…")
        else:
            self.presentation_paused.set()
            self.pause_button.configure(text="Lanjutkan")
            self.status.set("Narasi dijeda. Pilih Lanjutkan untuk meneruskan.")

    def end_and_start_qa(self):
        self.finish_to_qa = True
        self.stop_presentation()
        self.status.set("Mengakhiri slideshow dan menyiapkan tanya jawab…")

    def stop_presentation(self):
        if self.in_qa:
            self.in_qa = False
            self.qa_history.clear()
            self.stop_button.configure(text="Hentikan", state="disabled")
            self.status.set("Tanya jawab selesai. Perintah Jarvis siap digunakan.")
        else:
            self.presentation_stop.set()
            self.pause_button.configure(state="disabled")
            self.finish_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.status.set("Menghentikan presentasi…")

    def handle_command(self, transcript, memories=(), *, general=False, during_presentation=False, active_slide=None):
        action = None
        presentation_qa = during_presentation or (self.in_qa and not general)
        deck = self.deck
        if GREETING.fullmatch(transcript.strip()):
            action = localised_line(HELLO_LINES, transcript)
        elif SOCIAL.fullmatch(transcript.strip()) or JOKE_REQUEST.match(transcript.strip()):
            action = answer_social(transcript, self.conversation)
        elif RECALL.search(transcript):
            action = {"name": "answer_from_memory"}
        else:
            asks_about_material = re.search(r"\b(?:jelaskan|explain|ringkas|summarize|apa|bagaimana|berapa|what|how)\b", transcript, re.I)
            named = referenced_material(transcript, self.memory.graph["nodes"]) if asks_about_material else None
            if named is not None:
                deck = deck if deck and deck.path == named else load_deck(named)
                action = {"name": "answer_from_deck"}
            elif presentation_qa:
                action = {"name": "answer_from_deck"}
            else:
                self.messages.put(("action_progress", "Jarvis sedang memahami permintaan…"))
                action = plan_with_gemini(transcript, deck_name=deck.path.stem if deck else None,
                                          history=self.conversation, memories=memories)

        memory_sources = []
        if isinstance(action, dict) and action.get("name") == "answer_from_memory":
            self.messages.put(("action_progress", "Jarvis sedang membaca ingatan…"))
            answer, ids = answer_from_memory(transcript, memories, self.conversation)
            records_by_id = {record["id"]: record for record in memories}
            matched = [records_by_id[key] for key in ids]
            memory_sources = [record["id"] for record in matched if record["kind"] == "note"]
            details = "\n".join(f"{record['title']} — {record['text'][:500]}" for record in matched
                                if record["kind"] == "conversation")
            sources = []
        elif isinstance(action, str):
            answer, details, sources = action, "", []
        elif action.get("name") == "answer_from_deck":
            if deck is None:
                return "Pilih deck PowerPoint dulu agar saya bisa melihat materinya.", "", [], []
            self.messages.put(("action_progress", "Mencari jawaban dari materi presentasi…"))
            active_deck = deck is self.deck
            history = self.qa_history if active_deck else []
            answer, sources = answer_question(deck, transcript, history, current_number=active_slide if active_deck else None)
            details = f"{material_title(deck.path)} · Slide {', '.join(map(str, sources))}" if sources else ""
            if active_deck:
                self.qa_history = (history + [{"question": transcript, "answer": answer, "slides": sources}])[-3:]
            if (general or not active_deck) and sources and (key := material_key(deck.path)):
                memory_sources = [key]
            if not active_deck:
                sources = []
        else:
            answer, details = self.execute_action(action)
            sources = []

        self.conversation = (self.conversation + [{"question": transcript, "answer": answer, "slides": sources}])[-3:]
        return answer, details, sources, memory_sources

    def execute_action(self, action):
        args = action.get("args")
        if not isinstance(args, dict):
            raise RuntimeError("Argumen perintah dari Gemini belum lengkap.")
        if action.get("name") == "ask_clarification":
            question = args.get("question")
            if not isinstance(question, str) or not question.strip().endswith("?") or len(question) > 220:
                raise RuntimeError("Perintah belum jelas. Coba sebutkan tindakan dan aplikasinya.")
            return "Perlu diperjelas. " + " ".join(question.split()), ""
        if action.get("name") == "search_local_files":
            self.messages.put(("action_progress", "Mencari file di komputer ini…"))
            return search_local_files(args)
        if action.get("name") == "format_excel_rows":
            self.messages.put(("action_progress", "Membuat workbook baru dan memformat baris di Excel…"))
            return format_excel_rows(args), ""
        if action.get("name") == "fill_excel_diagonal":
            self.messages.put(("action_progress", "Membuat workbook baru dan mengisi sel diagonal di Excel…"))
            return fill_excel_diagonal(args), ""
        name = args.get("name")
        if action.get("name") == "open_app" and isinstance(name, str) and 0 < len(name.strip()) <= 100:
            self.messages.put(("action_progress", "Mencari aplikasi Windows untuk Agent Desktop…"))
            return open_application(name.strip()), ""
        raise RuntimeError("Tindakan yang dipilih Gemini belum tersedia di Jarvis.")

    def show_output(self, text):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.configure(state="disabled")

    def poll_messages(self):
        while not self.messages.empty():
            kind, value = self.messages.get_nowait()
            if kind == "recording":
                if self.stop_recording is not None and not self.stop_recording.is_set():
                    self.button.configure(text="Lepas untuk transkripsi")
                    self.status.set("Merekam dari headset Baseus Bowie E13.")
                    print("MEREKAM — Baseus Bowie E13", flush=True)
            elif kind == "processing":
                self.show_processing()
            elif kind == "action_progress":
                if not self.presentation_paused.is_set():
                    self.status.set(value)
            elif kind == "transcript_ready":
                self.web_transcript = value
                self.show_output(value)
                print(f"Perintah: {value}", flush=True)
            elif kind in {"reply", "presentation_reply"}:
                question, answer, details, sources, browser_turn, capture, memory_sources = value
                ids_by_key = {node["key"]: node["id"] for node in self.memory.graph["nodes"]}
                memory_sources = [key for key in memory_sources if key in ids_by_key]
                self.web_reply_number += 1
                self.web_reply = {
                    "number": self.web_reply_number, "question": question,
                    "answer": answer, "details": details, "sources": sources, "turn": browser_turn,
                    "capture": capture, "memorySources": memory_sources,
                    "nodes": [ids_by_key[key] for key in memory_sources],
                    "presentation": kind == "presentation_reply",
                }
                self.galaxy.show_sources(sources)
                content = f"{question}\n\nJarvis\n{answer}"
                self.show_output(f"{content}\n\n{details}" if details else content)
            elif kind == "deck_ready":
                self.deck, self.narration, complete = value
                self.web_reply = None
                self.web_transcript = ""
                self.in_qa = False
                self.qa_history.clear()
                self.conversation.clear()
                self.galaxy.set_deck(self.deck)
                self.stop_button.configure(text="Hentikan")
                self.set_busy(False)
                self.show_output("\n\n".join(f"Slide {number}\n{script}" for number, script in self.narration.items()))
                self.status.set(f"{self.deck.path.name}. Naskah untuk {len(self.narration)} slide siap ditinjau.")
                print(f"Naskah siap untuk {len(self.narration)} slide.", flush=True)
                if complete is not None:
                    complete(None)
            elif kind == "deck_error":
                error, complete = value
                self.set_busy(False)
                self.status.set(error)
                print(f"Gagal menyiapkan deck: {error}", flush=True)
                if complete is not None:
                    complete(error)
            elif kind == "presentation_started":
                if not self.presentation_stop.is_set():
                    self.pause_button.configure(state="normal")
                    self.finish_button.configure(state="normal")
            elif kind == "presentation_finished":
                self.presentation_paused.clear()
                self.in_qa = value or self.finish_to_qa
                self.stop_button.configure(text="Akhiri tanya jawab" if self.in_qa else "Hentikan")
                self.set_busy(False)
                self.status.set(
                    "Presentasi selesai. Ketik pertanyaan atau tahan tombol bicara untuk tanya jawab."
                    if self.in_qa else "Presentasi dihentikan. Naskah siap dipakai kembali."
                )
                if self.in_qa:
                    self.root.lift()
                    self.command_input.delete(0, "end")
                    self.command_input.focus_set()
            elif kind == "presentation_question_done":
                self.presentation_question_pending = False
                if self.presentation_paused.is_set() and not self.presentation_stop.is_set():
                    self.status.set("Narasi dijeda. Pilih Lanjutkan untuk meneruskan.")
            elif kind == "ready":
                if not self.busy:
                    self.set_busy(False)
                    self.status.set("Siap menerima perintah suara atau teks.")
                    if self.root.focus_get() in (None, self.root):
                        self.button.focus_set()
                print("JARVIS SIAP — Baseus Bowie E13", flush=True)
            elif kind == "microphone_error":
                self.button.configure(text="Mikrofon headset belum siap")
                if not self.busy:
                    self.status.set(value + " Perintah teks tetap tersedia.")
                print(f"Mikrofon: {value}", flush=True)
            else:
                self.stop_recording = None
                self.set_busy(False)
                if kind == "error":
                    self.presentation_paused.clear()
                    self.status.set(value)
                    print(f"Gagal: {value}", flush=True)
                elif kind == "command_done":
                    self.status.set(value)
        self.root.after(40, self.poll_messages)

    def close(self):
        self.reply_stop.set()
        self.presentation_stop.set()
        if self.stop_recording is not None:
            self.stop_recording.set()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    browser_mode = "--browser" in sys.argv
    app = PushToTalk(root, load_whisper=not browser_mode)
    decks = list(Path(__file__).resolve().parents[1].glob("*.pptx"))
    if not decks:
        decks = [path for path in MATERIALS_ROOT.rglob("*.pptx") if material_title(path) == "Presentasi MT"]
    if len(decks) == 1:
        root.after_idle(app.prepare_deck, decks[0])
    if browser_mode:
        root.iconify()
        root.after_idle(app.open_browser)
    root.mainloop()
