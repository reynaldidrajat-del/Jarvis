# Jarvis roadmap

Jarvis is a Windows voice assistant that uses local Whisper for transcription, Gemini 3.8 Flash for reasoning, and Gemini TTS for spoken replies. Hold the talk button, speak, and release to get an answer or run a command in the same interaction. When an answer draws on a project source, the galaxy should show it while Jarvis speaks.

## Current build

- Run **Jalankan Jarvis.cmd** to open the local browser dashboard. The browser handles dictation and speech. The Windows app runs the PowerPoint controls and its **Buka dashboard browser** button reopens the same dashboard.
- The browser opens on the knowledge galaxy. Every Markdown note is a node, and the active deck has a single presentation node. Open **Presentasi MT** to see its slides and PowerPoint controls. A slide citation opens the relevant slide.
- Hold **Bicara** or Space to record one utterance and release to submit it. **Dengarkan** waits for “Jarvis” in the active tab and uses a 900 ms quiet window. **Suara jawaban** controls spoken replies. **Kirim** and Enter send typed input through the same chat route.
- The dashboard speaks replies and PowerPoint narration using Web Speech. Add ?mute=1 to the dashboard URL for a silent tab or ?lang=en-GB to use English dictation. Indonesian is the default dictation language.
- Say or type **remember that** or **ingat bahwa** to save a dated Markdown capture in notes-galaxy/notes/captures/. The new node appears in the browser immediately and can be cited in the next answer. Markdown added to notes-galaxy/notes/ is detected automatically.
- Jarvis saves submitted messages and speech transcripts in the local notes-galaxy/memory.sqlite3 database. Memory answers use relevant excerpts and identify the source in the dashboard.
- Gemini can reply conversationally, answer from the selected deck, or choose an available desktop action. Jarvis speaks the answer or actual action result.
- The PowerPoint presenter follows manual slide jumps and can pause, resume, or end into Q&A. The galaxy highlights source slides while Jarvis prepares and plays the reply.

## 1. Make push-to-talk conversational

- [x] Route typed and spoken input through the same flow. Let Gemini answer conversationally, look up a source, ask a follow-up question, or request an available desktop action.
- [x] Display the transcript immediately. Show whether Jarvis is listening, processing, performing an action, or speaking.
- [x] Reuse Gemini TTS for short spoken answers and action results. Keep the reply visible if audio playback fails. Use concise replies with occasional dry humor in conversation and a professional voice in presentation Q&A.
- [x] Use the actual tool result when reporting what happened on the desktop.

**Acceptance** — Hold Bicara or Space, pause mid-sentence, finish the question, and release. Confirm that only the complete question is sent. In Dengarkan mode, say “Jarvis, stop” or “Jarvis, wait” to interrupt immediately. In a ?mute=1 tab, confirm that the reply appears silently.

## 2. Build the galaxy around the PowerPoint deck

- [x] Use `Presentasi Reynaldi Drajat (2).pptx` as the first collection. Give each visible slide a clickable node with its original slide number, title, and available text. Use deck sections when present and group slides by order for this deck.
- [x] Pass validated slide references from Gemini to the galaxy. Focus the camera on a single supporting slide or highlight all cited slides together. Keep the view steady for small talk and unsupported answers.
- [x] Show the cited slide text when a node is selected. Start the camera move as soon as the answer and references are ready, so the map is already showing the evidence when playback starts.
- [x] Handle follow-up questions using the recent conversation and current deck.

**Acceptance** — Ask about a fact in the deck by voice. Hear the answer and see the correct source node. Ask a question that needs more than one slide and verify that each cited node is highlighted. Ask about something outside the deck and hear Jarvis say that the material does not cover it.

## 3. Finish the presenter rehearsal

- [x] Add Pause and Resume so the presenter can speak and then continue the narration from the same position.
- [x] Add End & Start Q&A as a distinct control. A manual slide jump stops the old narration and starts the destination slide.
- [x] Use the galaxy during Q&A and check that the answer can point to the supporting slide. Add slide images as evidence for questions about diagrams or screenshots.
- [ ] Run a rehearsal with the actual deck and headset. Include a forward skip, a backward jump, a pause and resume, and an audience question after the presentation ends.

**Acceptance** — Present the deck, skip a section, pause to speak, resume, then enter Q&A. Jarvis answers aloud and takes the audience to the supporting slide in the galaxy.

**Verified** — A button-release test with recorded speech ran through local Whisper, Gemini, slide 8, and muted playback. Gemini cited slides 8 and 17 for a comparison, answered a visual question from an exported slide image, and reported insufficient evidence on a question outside the deck. PowerPoint tests verified the slide 1 → 3 → 1 path, preserved playback position across pause and resume, and entered Q&A through the End button. The Baseus headset was detected. A live rehearsal with the presenter is pending.

## 4. Expand once the rehearsal works

- [ ] Import a chosen folder of Markdown notes and project documents into the same galaxy. Keep each source clickable and preserve its link to the original file.
- [ ] Add reusable Agent Desktop tools for reading a window and interacting with its controls. Verify each action from the resulting window state. Test a complete task in a second app, such as finding and playing a song in Spotify.
- [ ] Consider wake-word activation, on-demand screen questions, and Focus Lock after the core voice and source flows are reliable.

## Open presentation decisions

- Esc currently ends the slideshow and returns to Jarvis. Confirm whether it should start Q&A instead.
- Q&A currently uses all visible slides in the deck, including slides skipped during the session. Confirm whether to limit it to slides shown.
