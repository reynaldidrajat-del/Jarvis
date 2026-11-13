const AUDIO_TYPES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"];
const MAX_HOLD_MS = 60_000;

export async function openJarvisMicrophone(browser = window) {
  const devices = browser.navigator.mediaDevices;
  const base = {echoCancellation: true, noiseSuppression: true};
  const preferredDevice = async () => (await devices.enumerateDevices()).find((device) =>
    device.kind === "audioinput" && device.deviceId !== "default" && device.deviceId !== "communications" &&
    /baseus.*bowie/i.test(device.label));
  const openPreferred = (device) => devices.getUserMedia({audio: {...base, deviceId: {exact: device.deviceId}}});
  const known = await preferredDevice();
  if (known) return openPreferred(known);

  // The first permission grant can expose labels that were hidden by the browser.
  const stream = await devices.getUserMedia({audio: base});
  const preferred = await preferredDevice().catch(() => null);
  if (!preferred || /baseus.*bowie/i.test(stream.getAudioTracks()[0]?.label || "")) return stream;
  stream.getTracks().forEach((track) => track.stop());
  return openPreferred(preferred);
}

export function createHoldRecorder({onReady, onAudio, onError}, browser = window) {
  const {MediaRecorder} = browser;
  const devices = browser.navigator.mediaDevices;
  const supported = Boolean(MediaRecorder && devices?.getUserMedia);
  const mimeType = supported && AUDIO_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
  let session = null;

  function cancel() {
    const active = session;
    session = null;
    if (!active) return;
    clearTimeout(active.timer);
    if (active.recorder && active.recorder.state !== "inactive") active.recorder.stop();
    active.stream?.getTracks().forEach((track) => track.stop());
  }

  function fail(message) {
    cancel();
    onError(message);
  }

  async function start() {
    if (session || !mimeType) return;
    const active = {chunks: [], released: false};
    session = active;
    try {
      const stream = await openJarvisMicrophone(browser);
      if (session !== active) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      active.stream = stream;
      const recorder = new MediaRecorder(stream, {mimeType, audioBitsPerSecond: 64000});
      active.recorder = recorder;
      recorder.onstart = () => {
        if (session !== active || active.released) return;
        active.timer = setTimeout(() => fail("Rekaman melebihi satu menit. Tahan lagi dan ucapkan kalimat yang lebih pendek."), MAX_HOLD_MS);
        onReady(stream.getAudioTracks()[0]?.label);
      };
      recorder.ondataavailable = ({data}) => {
        if (session === active && data.size) active.chunks.push(data);
      };
      recorder.onstop = () => {
        if (session !== active) return;
        const audio = new Blob(active.chunks, {type: recorder.mimeType || mimeType});
        cancel();
        if (!active.released) onError("Mikrofon terputus saat tombol masih ditahan. Ulangi ucapanmu.");
        else if (!audio.size) onError("Rekaman kosong. Tunggu sampai Mic siap, lalu bicara.");
        else onAudio(audio);
      };
      recorder.onerror = () => {
        if (session === active) fail("Browser gagal merekam mikrofon. Coba lagi.");
      };
      recorder.start();
    } catch (error) {
      if (session === active) fail(error.name === "NotAllowedError" ? "Izinkan mikrofon di browser, lalu tahan Bicara lagi." :
        "Browser belum bisa membuka mikrofon. Periksa perangkat input dan izin mikrofon.");
    }
  }

  function release() {
    if (!session?.recorder || session.recorder.state !== "recording") {
      fail("Mikrofon belum siap saat tombol dilepas. Tahan sampai muncul “Mic siap”.");
      return;
    }
    session.released = true;
    clearTimeout(session.timer);
    session.recorder.stop();
  }

  return {canRecord: Boolean(mimeType), start, release, cancel};
}
