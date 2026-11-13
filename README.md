# Jarvis v3

Asisten Windows dengan dashboard suara, galaksi catatan Markdown, kendali aplikasi desktop, dan presentasi PowerPoint. Whisper memproses dikte secara lokal. Gemini memahami perintah dan menjawab pertanyaan. VoiceStudio dapat membacakan balasan dashboard melalui VoxCPM2.

## Menjalankan dashboard di Windows

Gunakan Python 3.12 dengan Tkinter dan pastikan `ffmpeg` tersedia di `PATH`. Jalankan dari akar proyek.

```powershell
py -3.12 -m venv whisper/.venv
& ./whisper/.venv/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
& ./whisper/.venv/Scripts/python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

Siapkan `GEMINI_API_KEY` di environment Windows dan jalankan `Jalankan Jarvis.cmd`. Peluncur membuka URL dashboard lokal yang dibuat untuk sesi tersebut. Model Whisper `small` diunduh saat pertama kali diperlukan.

- Tahan **Bicara** atau spasi untuk merekam. Lepas untuk mengirim atau ketik pertanyaan di dashboard.
- Letakkan catatan Markdown di `notes-galaxy/notes/` dan deck di `materi external/`. Kendali presentasi memerlukan Microsoft PowerPoint desktop.
- Ucapkan **ingat bahwa** untuk menyimpan catatan baru. Jawaban yang memakai catatan akan menandai sumbernya di galaksi.

## Agent Desktop dan VoiceStudio

Ambil versi dependensi yang dipakai proyek melalui submodule.

```powershell
git submodule update --init
```

- [Agent Desktop](https://github.com/vercel-labs/agent-desktop) dapat dipasang dengan `npm install -g agent-desktop`. Jarvis mencari executable di `PATH` jika build lokal `agent-dekstop/target/release/agent-desktop.exe` belum tersedia.
- Untuk VoiceStudio dari sumber, ikuti [panduan instalasi Windows](https://github.com/debpalash/VoiceStudio/blob/main/docs/install/windows.md), jalankan `Jalankan VoiceStudio.cmd`, lalu pasang VoxCPM2 di VoiceStudio. Jarvis memakai API lokal di `127.0.0.1:3900`. Jika layanan suara belum siap, balasan dashboard dapat dibacakan oleh browser.
