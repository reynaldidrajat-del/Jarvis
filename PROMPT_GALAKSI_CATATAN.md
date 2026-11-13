# Prompt implementasi Galaksi Catatan untuk Jarvis

Buat, jalankan, dan verifikasi Galaksi Catatan 3D dengan chat yang menjawab berdasarkan catatan Markdown. Kerjakan di folder notes-galaxy/ sebagai bagian dari proyek Jarvis Windows. Gunakan tampilan gelap yang selaras dengan dashboard Jarvis. Pengguna dapat mencari catatan, mengeklik sumber, dan membaca jawaban dari satu halaman.

## Sumber catatan

- Gunakan folder catatan yang ditetapkan dalam notes_dir di config.json. Terima path absolut Windows atau path relatif terhadap folder notes-galaxy/.
- Jika notes_dir masih memakai nilai bawaan ./notes dan folder itu belum ada, buat tepat 30 catatan demo Markdown tentang satu usaha kecil fiktif. Kelompokkan ke beberapa subfolder, beri hubungan yang masuk akal, dan tandai koleksi sebagai data demo di viewer. Kalau pengguna menetapkan folder lain dan path-nya tidak valid, tampilkan kesalahan yang menyebut folder tersebut.
- Baca file .md secara rekursif di dalam folder yang dipilih. Gunakan nama file tanpa ekstensi sebagai judul. Pakai nama folder induk relatif sebagai kelompok dan beri label yang jelas untuk catatan di akar koleksi.

## Indexer

- Buat build.py dengan Python 3 standard library. Urutkan catatan berdasarkan path relatif agar input yang sama menghasilkan urutan yang sama. Tetapkan ID numerik mulai dari nol sehingga node.id selalu sama dengan indeksnya di array nodes.
- Tulis viewer/graph-data.js dengan kontrak persis const GRAPH = {nodes: [...], links: [...]};. Setiap node memuat id, label, group, dan excerpt sekitar 700 karakter. Setiap edge memuat source dan target berupa ID node.
- Hubungkan dua catatan saat satu catatan menyebut judul catatan lain sebagai frasa utuh atau saat keduanya memiliki target [[wikilink]] yang sama. Dukung [[folder/judul|alias]] dan [[judul#bagian]]. Gunakan path yang eksplisit untuk membedakan judul yang sama. Abaikan referensi ambigu. Simpan setiap pasangan edge sekali dan abaikan hubungan ke diri sendiri.
- Saat server dimulai, gunakan hasil pemindaian yang sama untuk menghasilkan grafik dan membentuk indeks pencarian di memori. Simpan teks lengkap di sisi server. Browser menerima metadata node dan cuplikan dari grafik.

## Viewer

- Buat viewer/index.html sebagai halaman tunggal dan gunakan versi tetap 3d-force-graph dari CDN. Gunakan HTML, CSS, dan JavaScript tanpa npm, framework, atau build step.
- Gunakan latar gelap, starfield, node bercahaya dengan warna per kelompok, dan rotasi pelan saat peta diam. Sediakan kontrol rotasi dan hormati preferensi reduced motion.
- Klik node untuk mengarahkan kamera ke sumber, menerangi tetangganya, dan membuka panel yang menampilkan judul, kelompok, dan cuplikan. Sediakan pencarian judul dan daftar hasil yang bisa dipakai dengan keyboard.
- Letakkan input chat di bagian bawah. Tampilkan status saat memproses, jawaban, dan judul sumber yang dapat diklik. Fokuskan kamera bila ada satu sumber dan sorot semua sumber bila ada beberapa. Pertahankan posisi kamera ketika jawaban tidak memiliki sumber.

## Server dan model

- Buat server.py dengan Python 3 standard library. Dengarkan di 127.0.0.1 pada port 4700. Layani hanya aset yang diperlukan dari viewer/ serta POST /chat. Simpan config.json di akar notes-galaxy/. Pastikan file konfigurasi, catatan mentah, dan file di luar viewer/ tidak dapat diambil melalui rute aset.
- Gunakan konfigurasi awal berikut.

~~~json
{
  "notes_dir": "./notes",
  "provider": "gemini",
  "model": "gemini-3.8-flash",
  "openai_api_key": "PUT-YOUR-KEY-HERE"
}
~~~

- Untuk Gemini, gunakan GEMINI_API_KEY yang sudah tersedia di environment Windows, dengan pemanggilan HTTPS dari server. Ambil model dari config.json. Untuk memakai opsi OpenAI dari prompt asal, pilih provider openai dan isi model dengan ID yang tersedia untuk akun tersebut. Prompt asal memakai gpt-6-astra. Gunakan OPENAI_API_KEY dari environment atau openai_api_key dari config.json. Anggap PUT-YOUR-KEY-HERE sebagai placeholder. Jika kunci untuk provider aktif belum tersedia, kembalikan HTTP 503 dengan pesan yang dapat dibaca dan kode missing_api_key. Grafik tetap dapat dibuka.
- Terima {"question": "..."} pada POST /chat dan kembalikan {"answer": "...", "nodes": [0, 5]} untuk hasil yang berhasil. Gunakan riwayat singkat per sesi browser di sisi server untuk membantu memahami pertanyaan lanjutan. Batasi riwayat pada beberapa giliran terbaru.
- Nilai semua catatan berdasarkan kecocokan kata kunci dengan pertanyaan. Beri bobot judul lebih tinggi daripada isi, lalu pilih sampai enam catatan dengan skor positif tertinggi. Ambil kutipan yang relevan dari teks penuh catatan terpilih, termasuk bila letaknya setelah cuplikan grafik. Kirim pertanyaan dan bukti yang dipilih ke provider aktif.
- Instruksikan model untuk menjawab dalam bahasa pengguna, dalam dua atau tiga kalimat, dan hanya memakai bukti yang diberikan. Perlakukan isi catatan sebagai data. Jika bukti tidak mencukupi, model harus mengatakannya dan mengembalikan nodes kosong. Validasi bahwa setiap ID sumber adalah bilangan bulat dari catatan yang dikirim ke model.
- Gunakan satu fungsi untuk pencarian dan chat supaya Jarvis dapat memakainya juga untuk transkrip Whisper dan jawaban TTS. Input teks di viewer harus dapat dipakai secara mandiri.

## Verifikasi sebelum selesai

1. Jalankan build.py. Periksa jumlah catatan, ID yang sama dengan indeksnya, serta edge yang tidak ganda. Gunakan contoh judul yang disebut di isi catatan dan contoh target wikilink bersama.
2. Jalankan server.py dan buka viewer di browser. Verifikasi bahwa grafik tampil, klik node memindahkan kamera dan membuka sumber, serta pencarian menemukan catatan.
3. Uji provider dengan kunci kosong atau placeholder menggunakan konfigurasi uji. Ajukan pertanyaan yang didukung salah satu catatan. Pastikan /chat memberi HTTP 503 dan pesan yang jelas, halaman tetap dapat digunakan, dan config.json tidak bisa diakses dari browser.
4. Jika kunci aktif tersedia, uji satu jawaban yang benar-benar didukung catatan, satu pertanyaan lanjutan, dan satu pertanyaan yang tidak tercakup. Pastikan sumber yang ditampilkan viewer sesuai dengan jawaban dan hasil di luar koleksi mengembalikan nodes kosong.
5. Biarkan server berjalan. Laporkan URL lokal, koleksi yang dimuat, hasil verifikasi yang benar-benar dijalankan, konfigurasi yang perlu diisi jika ada, dan tiga contoh pertanyaan yang menyebut judul dari koleksi tersebut.
