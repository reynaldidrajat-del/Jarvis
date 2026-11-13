import argparse
import re
import subprocess
import time
from functools import lru_cache
from threading import Lock

import numpy as np
import sounddevice as sd
import torch
import whisper
from whisper.audio import SAMPLE_RATE

WEB_AUDIO_FORMATS = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mov", "audio/wav": "wav"}
MAX_WEB_AUDIO_SECONDS = 60
WEB_TRANSCRIPTION = Lock()


@lru_cache(maxsize=1)
def load_local_whisper():
    torch.set_num_threads(6)
    return whisper.load_model("small", device="cpu")


def wasapi_devices():
    return next((api for api in sd.query_hostapis() if api["name"] == "Windows WASAPI"), None)


def capture_microphone(device, seconds, *, stop_event=None, on_started=None):
    info = sd.query_devices(device, kind="input")
    sample_rate = int(info["default_samplerate"])
    channels = min(2, info["max_input_channels"])
    blocks = []
    overruns = []

    def collect_audio(indata, _frames, _timestamp, status):
        blocks.append(indata.copy())
        if status.input_overflow:
            overruns.append(status)

    with sd.InputStream(
        device=device, channels=channels, samplerate=sample_rate,
        dtype="float32", callback=collect_audio,
    ):
        if on_started is None:
            print(f"MIKROFON AKTIF ({seconds} detik): {info['name']}", flush=True)
        else:
            on_started()
        if stop_event is None:
            time.sleep(seconds)
        else:
            stop_event.wait(seconds)
    if overruns:
        raise SystemExit("Sebagian audio terlewat. Ulangi uji mikrofon.")
    if not blocks:
        raise SystemExit("Perangkat tidak mengirim audio.")
    return np.concatenate(blocks), sample_rate, channels


def capture_with_echo_cancellation(device, seconds):
    from echoff import AecCapture, AecCaptureError, AecConfig

    wasapi = wasapi_devices()
    if not wasapi or wasapi["default_output_device"] < 0:
        raise SystemExit("Peredam gema memerlukan speaker keluaran default Windows WASAPI.")
    if device not in wasapi["devices"]:
        raise SystemExit("Untuk peredam gema, pilih mikrofon Windows WASAPI dari --list-devices.")

    microphone = sd.query_devices(device, kind="input")
    speaker = sd.query_devices(wasapi["default_output_device"], kind="output")
    config = AecConfig(backend="windows")
    blocks = []
    playback_hold_until = 0.0

    def collect_clean_audio(frame):
        nonlocal playback_hold_until
        ended = frame.microphone_ended_monotonic
        if not frame.reference_present or np.max(np.abs(frame.reference)) >= 0.003:
            playback_hold_until = ended + 0.45

        samples = np.asarray(frame.microphone_clean, dtype=np.float32)
        if ended < playback_hold_until:
            samples.fill(0)
        blocks.append(samples)

    def keep_loopback_active(outdata, _frames, _timestamp, _status):
        # Keep WASAPI loopback delivering frames even while the speaker is silent.
        outdata.fill(0)

    capture = AecCapture(
        config, on_frame=collect_clean_audio, microphone_device=microphone["name"],
        console_diagnostics=False,
    )
    try:
        with sd.OutputStream(
            device=speaker["index"], samplerate=speaker["default_samplerate"],
            channels=min(2, speaker["max_output_channels"]), dtype="float32",
            callback=keep_loopback_active,
        ), capture:
            print(f"MIKROFON AKTIF ({seconds} detik, peredam gema): {microphone['name']}", flush=True)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                capture.raise_if_failed()
                time.sleep(0.1)
    except AecCaptureError as error:
        raise SystemExit(f"Peredam gema gagal menerima audio: {error}") from error
    if not blocks:
        raise SystemExit("Peredam gema tidak menerima audio yang bisa diproses.")
    return np.concatenate(blocks), config.sample_rate, config.channels


def resample_for_whisper(frames, sample_rate, channels):

    resampled = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "f32le", "-ar", str(sample_rate), "-ac", str(channels), "-i", "pipe:0",
            "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1",
        ],
        input=frames.tobytes(),
        capture_output=True,
        check=True,
    )
    return np.frombuffer(resampled.stdout, dtype="<f4").copy()


def transcribe_microphone(model, audio, strict_quality, *, language=None):
    if not strict_quality:
        result = model.transcribe(audio, task="transcribe", fp16=False, language=language)
        return result["language"], result["text"].strip()

    result = model.transcribe(audio, task="transcribe", fp16=False, temperature=0, language=language)
    # Whisper uses these same limits to flag low-confidence or repetitive decoding.
    transcript = "".join(
        segment["text"] for segment in result["segments"]
        if segment["avg_logprob"] >= -1.0 and segment["compression_ratio"] <= 2.4
    )
    return result["language"], transcript.strip()


def restore_material_titles(text, titles):
    def match_word(word):
        folded = word.casefold()
        return "proses" if folded == "process" else folded

    spans = list(re.finditer(r"\b[^\W_]+\b", text))
    words = [match_word(span[0]) for span in spans]
    names = [(title, re.findall(r"\b[^\W_]+\b", title)) for title in titles]
    edits = []
    index = 0
    while index < len(spans):
        matches = []
        for title, terms in names:
            end = index + len(terms)
            if len(terms) < 2 or end > len(words):
                continue
            heard = words[index:end]
            expected = [match_word(term) for term in terms]
            # Allow a single-letter acronym error only after a matching title prefix.
            acronym = terms[-1]
            almost = (len(terms) >= 3 and 2 <= len(acronym) <= 5 and acronym.isupper() and
                      heard[:-1] == expected[:-1] and len(heard[-1]) == len(acronym) and
                      sum(a != b for a, b in zip(heard[-1], expected[-1])) == 1)
            if heard == expected or almost:
                matches.append((heard == expected, len(terms), title, end))
        if not matches:
            index += 1
            continue
        rank = max(match[:2] for match in matches)
        best = [match for match in matches if match[:2] == rank]
        if len(best) == 1:
            _, _, title, end = best[0]
            edits.append((spans[index].start(), spans[end - 1].end(), title))
            index = end
        else:
            index += 1
    for start, end, title in reversed(edits):
        text = text[:start] + title + text[end:]
    return text


def transcribe_browser_audio(data, mime, titles, *, language=None):
    try:
        decoded = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "pipe",
             "-f", WEB_AUDIO_FORMATS[mime], "-i", "pipe:0", "-t", str(MAX_WEB_AUDIO_SECONDS + 1),
             "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"],
            input=data, capture_output=True, check=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Jarvis gagal membaca rekaman mikrofon browser.") from None
    audio = np.frombuffer(decoded.stdout, dtype="<f4").copy()
    if len(audio) > SAMPLE_RATE * (MAX_WEB_AUDIO_SECONDS + .3):
        raise ValueError("Rekaman melebihi satu menit. Ulangi dengan kalimat yang lebih pendek.")
    if len(audio) < SAMPLE_RATE // 5 or np.max(np.abs(audio)) < .001:
        raise ValueError("Rekaman terlalu singkat atau terlalu pelan untuk dibaca.")
    _, text = transcribe_microphone(load_local_whisper(), audio, strict_quality=True, language=language)
    if not text:
        raise ValueError("Whisper belum menangkap ucapan dengan jelas. Coba ulangi dari awal.")
    return restore_material_titles(text, titles)


def main():
    parser = argparse.ArgumentParser(description="Transkripsi satu klip dari mikrofon.")
    parser.add_argument("--seconds", type=int, default=10, help="Durasi rekam dalam detik (1–30).")
    parser.add_argument("--device", type=int, help="Nomor perangkat input.")
    parser.add_argument("--list-devices", action="store_true", help="Tampilkan perangkat audio.")
    parser.add_argument(
        "--echo-cancel", action="store_true",
        help="Redam gema speaker default Windows dan proses ucapan setelah pemutaran berhenti.",
    )
    args = parser.parse_args()
    if args.list_devices:
        print(sd.query_devices())
        return
    if not 1 <= args.seconds <= 30:
        parser.error("--seconds harus antara 1 dan 30.")

    wasapi = wasapi_devices()
    default_device = wasapi["default_input_device"] if wasapi else -1
    if default_device < 0:
        default_device = sd.default.device[0]
    device = args.device if args.device is not None else default_device

    model = load_local_whisper()
    capture = capture_with_echo_cancellation if args.echo_cancel else capture_microphone
    frames, sample_rate, channels = capture(device, args.seconds)
    print("Mikrofon berhenti. Memeriksa audio.", flush=True)
    if np.max(np.abs(frames)) < 0.001:
        raise SystemExit("Mikrofon tidak menerima sinyal yang cukup.")

    audio = resample_for_whisper(frames, sample_rate, channels)
    language, transcript = transcribe_microphone(model, audio, args.echo_cancel)
    if not transcript:
        raise SystemExit("Whisper tidak mendeteksi ucapan yang dapat ditranskripsi dengan yakin.")
    print(f"Bahasa utama: {language}")
    print(f"Transkripsi: {transcript}")


if __name__ == "__main__":
    main()
