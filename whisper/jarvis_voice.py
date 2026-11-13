"""Generate Jarvis reply audio with the local VoiceStudio service."""

import json
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


VOICE_URL = "http://127.0.0.1:3900/v1/audio/speech"
VOICE_DESCRIPTION = (
    "An original mature male assistant with a warm lower register, crisp articulation, "
    "measured pace and restrained dry wit. Refined British English when speaking English; "
    "natural Indonesian diction when speaking Indonesian."
)
MAX_VOICE_BYTES = 12 * 1024 * 1024


class LocalOnly(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def synthesize_reply(text, language):
    body = {"model": "voxcpm2", "voice": "default", "input": text, "language": language,
            "description": VOICE_DESCRIPTION, "seed": 42, "num_step": 4, "response_format": "wav"}
    request = Request(VOICE_URL, data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({}), LocalOnly()).open(request, timeout=75) as response:
            audio = response.read(MAX_VOICE_BYTES + 1)
    except (OSError, URLError):
        raise RuntimeError("VoiceStudio belum bisa menghasilkan audio.") from None
    if len(audio) > MAX_VOICE_BYTES or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
        raise RuntimeError("VoiceStudio tidak mengirim audio WAV yang valid.")
    return audio
