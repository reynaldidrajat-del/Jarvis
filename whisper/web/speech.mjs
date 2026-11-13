export const FINISH_MS = 900;
const RELEASE_TIMEOUT_MS = 3500;
const HOLD_RESULT_SETTLE_MS = 140;

const INTERRUPTS = /^(stop|wait|berhenti|tunggu|jeda)[.!?,\s]*$/iu;
const WAKE_WORD = /\b(?:jarvis|jervis)\b[\s,.:!?-]*/iu;

export function speechInterrupt(text) {
  return INTERRUPTS.test(text) ? text.toLowerCase().replace(/[.!?,\s]+$/u, "") : "";
}

export function createSpeech(options, browser = window) {
  const Recognition = browser.webkitSpeechRecognition || browser.SpeechRecognition;
  const synthesis = browser.speechSynthesis;
  let enabled = false;
  let activeUtterance = null;
  let replyAudio = null;
  const replyPlayer = options.replyAudio && typeof browser.Audio === "function" ? new browser.Audio() : null;
  let recognition = null;
  let audioStream = null;
  let timer;
  let wakeTimer;
  let restartTimer;
  let wakeMode = false;
  let holdMode = false;
  let released = false;
  let audioReady = false;
  let capturedAudio = false;
  let awake = false;
  let previous = "";
  let segment = "";

  function stopSpeaking() {
    if (replyAudio) {
      const current = replyAudio;
      replyAudio = null;
      current.controller.abort();
      clearReplyPlayer(current);
      options.onSpeaking(false);
    }
    if (activeUtterance) {
      activeUtterance = null;
      synthesis.cancel();
      options.onSpeaking(false);
    }
  }

  function clearReplyPlayer(current) {
    replyPlayer.onended = replyPlayer.onerror = replyPlayer.onplaying = null;
    replyPlayer.pause();
    replyPlayer.removeAttribute("src");
    replyPlayer.load();
    if (current.url) browser.URL.revokeObjectURL(current.url);
  }

  function speakReply(text, settings = {}) {
    if (options.muted || !enabled || !text) return false;
    if (!replyPlayer || text.length > 1000) return speak(text, settings);
    stopSpeaking();
    const current = {controller: new AbortController(), url: null};
    replyAudio = current;
    const finish = (ok) => {
      if (replyAudio !== current) return;
      replyAudio = null;
      clearReplyPlayer(current);
      options.onSpeaking(false);
      if (ok) settings.onDone?.(true);
      else if (!speak(text, settings)) settings.onDone?.(false);
    };
    options.replyAudio(text, settings.language, current.controller.signal).then(async (audio) => {
      if (replyAudio !== current) return;
      current.url = browser.URL.createObjectURL(audio);
      replyPlayer.onended = () => finish(true);
      replyPlayer.onerror = () => finish(false);
      replyPlayer.onplaying = () => { if (replyAudio === current) options.onSpeaking(true); };
      replyPlayer.src = current.url;
      await replyPlayer.play();
    }).catch(() => finish(false));
    return true;
  }

  function speak(text, {language = "en-GB", onDone, warmup = false} = {}) {
    if (options.muted || !enabled || !synthesis || !browser.SpeechSynthesisUtterance || !text) return false;
    if (!warmup) stopSpeaking();
    const utterance = new browser.SpeechSynthesisUtterance(text);
    utterance.lang = language;
    const voices = synthesis.getVoices();
    const exact = voices.filter((voice) => voice.lang.toLowerCase() === language.toLowerCase());
    const prefix = language.split("-")[0].toLowerCase() + "-";
    const fallback = voices.filter((voice) => voice.lang.toLowerCase().startsWith(prefix));
    const voice = exact.find((item) => item.localService) || exact[0] ||
      fallback.find((item) => item.localService) || fallback[0];
    if (voice) utterance.voice = voice;
    if (warmup) {
      utterance.volume = 0;
    } else {
      activeUtterance = utterance;
      const finish = (ok) => {
        if (activeUtterance !== utterance) return;
        activeUtterance = null;
        options.onSpeaking(false);
        onDone?.(ok);
      };
      utterance.onstart = () => {
        if (activeUtterance === utterance) options.onSpeaking(true);
      };
      utterance.onend = () => finish(true);
      utterance.onerror = () => finish(false);
    }
    if (synthesis.paused) synthesis.resume();
    synthesis.speak(utterance);
    return true;
  }

  function activate() {
    if (!enabled) {
      enabled = true;
      speak(" ", {warmup: true});
    }
  }

  function rawTranscript() {
    if (!previous || !segment) return previous || segment;
    const head = previous.split(/\s+/u), tail = segment.split(/\s+/u);
    const normalise = (word) => word.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
    for (let overlap = Math.min(head.length, tail.length, 12); overlap >= 2; overlap--) {
      if (head.slice(-overlap).every((word, index) => normalise(word) === normalise(tail[index]))) {
        return [...head, ...tail.slice(overlap)].join(" ");
      }
    }
    return previous + " " + segment;
  }

  function transcript() {
    const raw = rawTranscript();
    if (!wakeMode) return raw;
    const match = WAKE_WORD.exec(raw);
    return match ? raw.slice(match.index + match[0].length).trim() : "";
  }

  function setAudioReady(value) {
    if (audioReady === value) return;
    audioReady = value;
    options.onAudioReady?.(value);
  }

  function stopListening() {
    clearTimeout(timer);
    clearTimeout(wakeTimer);
    clearTimeout(restartTimer);
    timer = undefined;
    restartTimer = undefined;
    if (!recognition) return;
    const active = recognition;
    recognition = null;
    audioStream?.getTracks().forEach((track) => track.stop());
    audioStream = null;
    previous = "";
    segment = "";
    wakeMode = false;
    holdMode = false;
    released = false;
    capturedAudio = false;
    awake = false;
    active.abort();
    options.onListening(false);
    setAudioReady(false);
  }

  function finishHeldSpeech() {
    const text = transcript();
    stopListening();
    const interrupt = speechInterrupt(text);
    if (interrupt) {
      options.onInterrupt(interrupt);
    } else if (text) {
      options.onQuestion(text);
    } else {
      options.onError("Belum ada suara yang terdeteksi. Tahan tombol sambil bicara.");
    }
  }

  function releaseListening() {
    if (!recognition || !holdMode || released) return;
    released = true;
    if (!capturedAudio && !transcript()) {
      stopListening();
      options.onError("Mikrofon belum siap saat tombol dilepas. Tahan sampai muncul “Mic siap”.");
      return;
    }
    if (restartTimer !== undefined) {
      finishHeldSpeech();
      return;
    }
    // stop() lets the recognizer emit the last words before its end event.
    timer = setTimeout(finishHeldSpeech, RELEASE_TIMEOUT_MS);
    try {
      recognition.stop();
    } catch {
      finishHeldSpeech();
    }
  }

  function armFinish() {
    if (holdMode) return;
    clearTimeout(timer);
    if (!transcript()) return;
    timer = setTimeout(() => {
      const text = transcript();
      stopListening();
      options.onQuestion(text);
    }, FINISH_MS);
  }

  function startListening({wake = false, hold = false, language = options.language} = {}) {
    if (!Recognition || recognition) return;
    const active = new Recognition();
    recognition = active;
    wakeMode = wake;
    holdMode = hold;
    released = false;
    awake = false;
    previous = "";
    segment = "";
    active.lang = language;
    active.continuous = true;
    active.interimResults = true;
    function startRecognizer() {
      if (audioStream) active.start(audioStream.getAudioTracks()[0]);
      else active.start();
    }
    active.onaudiostart = () => {
      if (recognition !== active || released) return;
      capturedAudio = true;
      setAudioReady(true);
    };
    active.onaudioend = () => {
      if (recognition === active) setAudioReady(false);
    };
    active.onresult = (event) => {
      if (recognition !== active) return;
      const results = Array.from(event.results);
      const finalWords = results.slice(event.resultIndex)
        .filter((result) => result.isFinal)
        .map((result) => result[0].transcript.trim());
      const updated = results.map((result) => result[0].transcript.trim()).filter(Boolean).join(" ");
      const previousSegment = segment;
      if (holdMode && !updated && previousSegment) return;
      segment = updated;
      if (holdMode) {
        if (updated !== previousSegment) options.onTranscript(transcript());
        return;
      }
      if (wakeMode && !awake && WAKE_WORD.test(rawTranscript())) {
        awake = true;
        options.onWake?.();
        wakeTimer = setTimeout(() => {
          if (!transcript()) stopListening();
        }, 7500);
      }
      if (wakeMode && !awake) return;
      const interrupt = wakeMode
        ? (finalWords.length && INTERRUPTS.test(transcript()) ? transcript() : "")
        : finalWords.find((words) => INTERRUPTS.test(words));
      if (interrupt) {
        stopListening();
        options.onInterrupt(interrupt.toLowerCase().replace(/[.!?,\s]+$/u, ""));
        return;
      }
      if (updated === previousSegment) return;
      options.onTranscript(transcript());
      armFinish();
    };
    active.onspeechstart = () => {
      if (recognition === active && !holdMode) {
        clearTimeout(timer);
        timer = undefined;
      }
    };
    active.onspeechend = () => {
      if (recognition === active) armFinish();
    };
    active.onerror = ({error}) => {
      if (recognition !== active) return;
      if (error === "no-speech" && (wakeMode || holdMode || transcript())) return;
      stopListening();
      const errors = {
        "not-allowed": "Izinkan mikrofon di browser, lalu klik tombol mikrofon lagi.",
        "service-not-allowed": "Pengenalan suara tidak tersedia di browser ini. Coba Chrome atau ketik pertanyaanmu.",
        "audio-capture": "Browser tidak menemukan mikrofon. Periksa perangkat input Windows.",
        network: "Layanan pengenalan suara browser tidak terhubung.",
        "no-speech": "Belum ada suara yang terdeteksi.",
        "language-not-supported": "Bahasa mikrofon tidak didukung oleh browser ini.",
      };
      options.onError(errors[error] || "Browser tidak dapat mengenali suara.");
    };
    active.onend = () => {
      if (recognition !== active) return;
      setAudioReady(false);
      if (holdMode && released) {
        // Capture has ended. Leave a short window for a late final result from the browser.
        clearTimeout(timer);
        timer = setTimeout(finishHeldSpeech, HOLD_RESULT_SETTLE_MS);
        return;
      }
      if (holdMode || (wakeMode && !transcript())) {
        previous = holdMode || awake ? rawTranscript() : "";
        segment = "";
        restartTimer = setTimeout(() => {
          restartTimer = undefined;
          if (recognition !== active) return;
          try {
            startRecognizer();
          } catch {
            stopListening();
            options.onError("Mikrofon browser belum bisa dilanjutkan.");
          }
        }, holdMode ? 0 : 300);
        return;
      }
      if (!transcript()) {
        stopListening();
        options.onError("Belum ada suara yang terdeteksi.");
        return;
      }
      previous = transcript();
      segment = "";
      if (timer === undefined) armFinish();
      try {
        // Some browsers end a session after a final phrase despite continuous mode.
        // Keep the original quiet timer and listen for a continuation.
        startRecognizer();
      } catch {
        armFinish();
      }
    };
    options.onListening(true);
    (async () => {
      try {
        if (options.openMicrophone) {
          const stream = await options.openMicrophone();
          if (recognition !== active) {
            stream.getTracks().forEach((track) => track.stop());
            return;
          }
          audioStream = stream;
          const track = stream.getAudioTracks()[0];
          options.onAudioDevice?.(track.label);
          track.addEventListener("ended", () => {
            if (recognition !== active) return;
            stopListening();
            options.onError("Mikrofon terputus. Sambungkan lagi sebelum bicara.");
          }, {once: true});
        }
        startRecognizer();
      } catch (error) {
        if (recognition !== active) return;
        stopListening();
        options.onError(error.name === "NotAllowedError" ? "Izinkan mikrofon di browser, lalu tahan Bicara lagi." :
          "Browser belum bisa membuka mikrofon pilihan. Periksa headset atau coba mode Campuran.");
      }
    })();
  }

  return {
    activate, speak, speakReply, stopSpeaking, startListening, stopListening, releaseListening,
    canListen: Boolean(Recognition),
    canSpeak: Boolean(replyPlayer || (synthesis && browser.SpeechSynthesisUtterance)),
    isSpeaking: () => activeUtterance !== null || replyAudio !== null,
    isPreparingReply: () => replyAudio !== null && replyPlayer.paused,
    pause: () => { if (activeUtterance && !synthesis.paused) synthesis.pause(); },
    resume: () => { if (activeUtterance && synthesis.paused) synthesis.resume(); },
  };
}
