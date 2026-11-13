import {createSpeech, speechInterrupt} from "./speech.mjs";
import {createHoldRecorder, openJarvisMicrophone} from "./recording.mjs";

const byId = (id) => document.getElementById(id);
const canvas = byId("galaxy");
const context = canvas.getContext("2d");
const map = document.querySelector(".map");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const palette = ["#69dfc5", "#8ea9ff", "#cf9bff", "#f3bc75", "#76c2eb", "#e995b7"];
const origin = {x: 0, y: 0, z: 0};
const camera = {x: 0, y: 0, z: 0, zoom: 1, yaw: -.18, pitch: .09};
const target = {...camera};
const phases = {ready: "SIAP", listening: "MENDENGARKAN", thinking: "MEMPROSES", speaking: "BERBICARA", paused: "DIJEDA"};
let slides = [];
let edges = [];
let slideLegend = [];
let presentationNode = null;
let scope = "knowledge";
let notes = new Map();
let noteOrder = [];
let noteEdges = [];
let sources = new Set();
let neighbors = new Set();
let projected = [];
let selected = null;
let matches = null;
let rotation = false;
let drag = null;
let width = 0;
let height = 0;
let view = null;
let deckVersion = -1;
let memoryVersion = -1;
let latestReply = null;
let renderedReply = "";
let currentState = null;
let lastFrame = 0;
const settings = new URLSearchParams(location.search);
const muted = settings.get("mute") === "1";
const requestedLanguage = settings.get("lang")?.toLowerCase() || "";
let voiceMode = requestedLanguage.startsWith("en") ? "en" : requestedLanguage === "mix" ? "mix" : "id";
const recognitionLanguage = () => voiceMode === "en" ? "en-GB" : "id-ID";
const client = crypto.randomUUID();
let listening = false;
let micReady = false;
let micName = "";
let speaking = false;
let handsFree = false;
let talkHold = null;
let finishingSpeech = false;
let wakeDetected = false;
let voiceEnabled = !muted;
let voiceStatus = "";
let pendingTurn = null;
let narrationTurn = null;
let preview = "";
let welcome = null;
let welcomeActivated = false;
let welcomeDone = false;
let screenStream = null;
let screenEnded = false;
let screenReply = null;
const screenVideo = document.createElement("video");
screenVideo.muted = true;
screenVideo.playsInline = true;
const SCREEN_QUESTION = /\b(?:(?:my|this|the|your|shared) screen|screen shar(?:e|ing)|what (?:am i looking at|do you (?:see|think of this|think of that))|(?:fix|change|read|review|explain) (?:this|that)(?: on screen)?|layar (?:ini|saya|yang)|(?:lihat|baca|cek|bagikan) layar|apa yang (?:sedang )?(?:kamu|anda|saya) lihat|menurutmu (?:ini|yang ini))\b/iu;

function random(seed) {
  return ((Math.sin(seed * 127.1 + 19.3) * 43758.5453) % 1 + 1) % 1;
}
const stars = Array.from({length: 235}, (_, i) => [random(i), random(i + 1200), random(i + 3100)]);
const noteId = (key) => "note:" + key;
const visibleNodes = () => scope === "presentation" ? slides :
  [...(presentationNode && !notes.has(presentationNode.number) ? [presentationNode] : []), ...notes.values()];
const nodeTitle = (node) => ["presentation", "material"].includes(node.kind) ? node.title :
  (node.kind === "note" ? "Catatan · " : "Slide " + node.number + " · ") + node.title;

function makeGraph(deck) {
  const groups = new Map();
  deck.slides.forEach((slide, index) => {
    const key = slide.section || Math.floor(index / 7);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(slide);
  });
  slides = [];
  edges = [];
  slideLegend = [];
  let previousHub;
  [...groups.entries()].forEach(([key, items], groupIndex) => {
    const color = palette[groupIndex % palette.length];
    const orbit = groupIndex * 2.39996 + 2.52;
    const radius = groups.size > 1 ? .68 * Math.sqrt((groupIndex + .35) / groups.size) : 0;
    const centerX = Math.cos(orbit) * radius;
    const centerY = Math.sin(orbit) * radius * .75;
    const centerZ = Math.sin(groupIndex * 2.3) * .24;
    let hub;
    let previous;
    items.forEach((slide, index) => {
      const angle = index * 2.39996 + orbit;
      const spread = index ? .12 + .16 * Math.sqrt(index / items.length) : 0;
      const node = {
        ...slide, color, hub: index === 0,
        x: centerX + Math.cos(angle) * spread,
        y: centerY + Math.sin(angle) * spread * .82,
        z: centerZ + (random(slide.number) - .5) * .36,
      };
      slides.push(node);
      if (index === 0) hub = node;
      if (previous) edges.push([previous, node]);
      if (index > 1) edges.push([hub, node]);
      previous = node;
    });
    if (previousHub) edges.push([previousHub, hub]);
    previousHub = hub;
    slideLegend.push({
      name: typeof key === "string" ? key : "Slide " + items[0].number + "–" + items.at(-1).number,
      count: items.length, color,
    });
  });
  presentationNode = slides.length ? {
    number: deck.sourceKey ? noteId(deck.sourceKey) : "presentation:active", kind: "presentation",
    title: /^presentasi reynaldi drajat\b/iu.test(deck.name) ? "Presentasi MT" : deck.name || "Presentasi",
    text: slides.length + " slide · Buka untuk melihat materi dan menjalankan PowerPoint.",
    searchText: [deck.name, ...slides.map((slide) => slide.title)].join(" "),
    color: "#79c9ff", hub: true, x: 0, y: 0, z: 0,
  } : null;
  if (!presentationNode) scope = "knowledge";
  updateCollection();
  sources = new Set();
  neighbors = new Set();
  overview();
  renderSlideList();
}

function updateCollection() {
  const inside = scope === "presentation";
  byId("scope-title").textContent = inside ? presentationNode.title : "Pengetahuan";
  byId("knowledge-back").hidden = !inside;
  byId("presenter").hidden = !inside && !currentState?.presenting;
  byId("choose").hidden = !inside && Boolean(presentationNode);
  byId("source-summary").textContent = inside ? "Daftar slide" : "Daftar pengetahuan";
  byId("search").placeholder = inside ? "Cari slide…" : "Cari pengetahuan…";
  byId("search").setAttribute("aria-label", inside ? "Cari slide" : "Cari pengetahuan");
  byId("slide-count").textContent = inside ? slides.length + " slide" : visibleNodes().length + " sumber pengetahuan";
  byId("map-empty").hidden = visibleNodes().length > 0;
  byId("map-empty").textContent = "Simpan catatan dengan “ingat bahwa …” atau pilih deck.";
  const groups = inside ? slideLegend : [
    {name: "Catatan", count: noteOrder.filter((node) => node.kind === "note").length, color: "#f3ca7a"},
    {name: "Materi external", count: noteOrder.filter((node) => node.kind !== "note").length, color: "#79c9ff"},
    {name: "Presentasi", count: presentationNode && !notes.has(presentationNode.number) ? 1 : 0, color: "#79c9ff"},
  ];
  const legend = byId("legend");
  legend.replaceChildren();
  for (const group of groups.filter(({count}) => count > 0)) {
    const row = document.createElement("div");
    row.className = "legend-row";
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.setProperty("--group-color", group.color);
    const count = document.createElement("span");
    count.className = "legend-count";
    count.textContent = group.count;
    row.append(dot, group.name, count);
    legend.append(row);
  }
}

function updateNotes(graph) {
  const previous = notes;
  notes = new Map();
  noteOrder = graph.nodes.map((record) => {
    const number = noteId(record.key);
    const existing = previous.get(number);
    const angle = random(record.id + 4200) * Math.PI * 2;
    const distance = .35 + random(record.id + 5500) * .35;
    const active = number === presentationNode?.number;
    const point = active ? presentationNode : existing ||
      {x: Math.cos(angle) * distance, y: Math.sin(angle) * distance * .78, z: .25};
    const node = {...point, number, kind: active ? "presentation" : record.slides ? "material" : "note",
      title: record.label, text: record.excerpt, slides: record.slides, group: record.group,
      color: record.slides ? "#79c9ff" : "#f3ca7a", hub: true};
    notes.set(number, node);
    return node;
  });
  noteEdges = graph.links.map(({source, target}) => [noteOrder[source], noteOrder[target]]);
  if (selected && !visibleNodes().some((node) => node.number === selected)) overview();
  updateCollection();
  renderSlideList();
}

function animateCapture(capture) {
  const node = notes.get(noteId(capture.key));
  if (!node || reducedMotion.matches) return;
  const linked = noteEdges.find(([left, right]) => left === node || right === node);
  const neighbor = linked && (linked[0] === node ? linked[1] : linked[0]);
  const related = notes.get(noteId(capture.related)) || neighbor;
  const from = related ? {x: related.x, y: related.y, z: related.z} : origin;
  const angle = Math.atan2(node.y, node.x);
  node.x = from.x + .13 * Math.cos(angle);
  node.y = from.y + .13 * Math.sin(angle);
  node.z = from.z + .08;
  node.birth = {at: performance.now(), from};
}

function resize() {
  const ratio = Math.min(devicePixelRatio, 2);
  const bounds = canvas.getBoundingClientRect();
  const mapBounds = map.getBoundingClientRect();
  width = bounds.width;
  height = bounds.height;
  view = {x: mapBounds.left - bounds.left, y: mapBounds.top - bounds.top, w: mapBounds.width, h: mapBounds.height};
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
}
new ResizeObserver(resize).observe(canvas);

function draw(time) {
  requestAnimationFrame(draw);
  if (!view) return;
  const elapsed = Math.min(time - lastFrame, 50);
  lastFrame = time;
  if (rotation && !drag && !reducedMotion.matches) target.yaw += elapsed * .00022;
  const ease = reducedMotion.matches ? 1 : 1 - Math.pow(.85, elapsed / 16.7);
  for (const property of Object.keys(camera)) camera[property] += (target[property] - camera[property]) * ease;

  context.clearRect(0, 0, width, height);
  for (const [x, y, brightness] of stars) {
    context.fillStyle = "rgba(142,205,201," + (.04 + brightness * .30) + ")";
    context.fillRect(x * width, y * height, brightness > .93 ? 2 : 1, brightness > .93 ? 2 : 1);
  }

  const horizontal = view.x + view.w * .5;
  const vertical = view.y + view.h * .44;
  const scale = Math.min(view.w * .51, view.h * .56) * camera.zoom;
  context.strokeStyle = "#294a482a";
  context.lineWidth = 1;
  for (const radius of [.38, .68, .96]) {
    context.beginPath();
    context.ellipse(horizontal, vertical, scale * radius, scale * radius * .72, -.18, 0, Math.PI * 2);
    context.stroke();
  }

  const cy = Math.cos(camera.yaw), sy = Math.sin(camera.yaw);
  const cp = Math.cos(camera.pitch), sp = Math.sin(camera.pitch);
  const points = new Map();
  for (const node of visibleNodes()) {
    const age = node.birth ? time - node.birth.at : Infinity;
    const growth = reducedMotion.matches ? 1 : Math.min(1, Math.max(0, age / 460));
    const from = node.birth?.from || node;
    const x = from.x + (node.x - from.x) * growth - camera.x;
    const y = from.y + (node.y - from.y) * growth - camera.y;
    const z = from.z + (node.z - from.z) * growth - camera.z;
    const rotatedZ = z * cy - x * sy;
    const depth = rotatedZ * cp - y * sp;
    const perspective = 1 / Math.max(.48, 1 - depth * .35);
    points.set(node.number, {
      node, depth, age, radius: (node.kind === "presentation" ? 12 : node.hub ? 6.8 : 4.7) * perspective + Math.min(camera.zoom, 2) * 1.4,
      x: horizontal + (x * cy + z * sy) * scale * perspective,
      y: vertical + (y * cp + rotatedZ * sp) * scale * perspective,
    });
  }
  projected = [...points.values()].sort((a, b) => a.depth - b.depth);
  context.save();
  context.beginPath();
  context.rect(view.x, view.y, view.w, view.h);
  context.clip();
  for (const [a, b] of scope === "presentation" ? edges : noteEdges) {
    const first = points.get(a.number), second = points.get(b.number);
    const active = selected === a.number || selected === b.number || (sources.has(a.number) && sources.has(b.number));
    context.strokeStyle = active ? "#6ee9d6a0" : a.color === b.color ? a.color + "46" : "#789ba45a";
    context.lineWidth = active ? 1.3 : .7;
    context.beginPath();
    context.moveTo(first.x, first.y);
    context.lineTo(second.x, second.y);
    context.stroke();
  }
  for (const point of projected) {
    const {node, x, y, radius, age} = point;
    const active = sources.has(node.number) || selected === node.number;
    const linked = neighbors.has(node.number);
    const dim = !active && !linked && ((matches && !matches.has(node.number)) || sources.size);
    context.globalAlpha = dim ? .37 : 1;
    const glowSize = active ? 3.8 : linked ? 2.9 : 2.2;
    const glow = context.createRadialGradient(x, y, radius / 2, x, y, radius * glowSize);
    glow.addColorStop(0, node.color + (active ? "b2" : linked ? "86" : "55"));
    glow.addColorStop(1, node.color + "00");
    context.fillStyle = glow;
    context.beginPath();
    context.arc(x, y, radius * glowSize, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = node.color;
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fill();
    if (active) {
      context.strokeStyle = "#c4fff2";
      context.lineWidth = 1.3;
      context.beginPath();
      context.arc(x, y, radius + 5, 0, Math.PI * 2);
      context.stroke();
    }
    if (!reducedMotion.matches && age < 460) {
      context.strokeStyle = "rgba(255,218,146," + Math.max(0, 1 - age / 460) + ")";
      context.lineWidth = 2;
      context.beginPath();
      context.arc(x, y, radius + 4 + Math.max(0, age) / 18, 0, Math.PI * 2);
      context.stroke();
    }
  }
  context.globalAlpha = 1;
  context.font = '12px Inter, "Segoe UI", sans-serif';
  context.textAlign = "center";
  const placed = [];
  const labelOrder = [...projected].reverse().sort((a, b) => Number(sources.has(b.node.number) || selected === b.node.number) - Number(sources.has(a.node.number) || selected === a.node.number));
  for (const {node, x, y, radius} of labelOrder) {
    const active = sources.has(node.number) || selected === node.number;
    if (!active && !neighbors.has(node.number) && ((matches && !matches.has(node.number)) || sources.size)) continue;
    const title = node.title.length > 26 ? node.title.slice(0, 25) + "…" : node.title;
    const label = node.kind ? title : active || node.hub ? node.number + "  " + title : String(node.number);
    const labelWidth = context.measureText(label).width + 7;
    const labelY = y + radius + 13;
    const rect = [x - labelWidth / 2, labelY - 12, labelWidth, 15];
    if (!active && placed.some(([left, top, w, h]) =>
      rect[0] < left + w && rect[0] + rect[2] > left && rect[1] < top + h && rect[1] + rect[3] > top)) continue;
    placed.push(rect);
    context.fillStyle = active ? "#e5fff9" : "#9ebbb9";
    context.fillText(label, x, labelY);
  }
  context.restore();
}
requestAnimationFrame(draw);

function overview() {
  selected = null;
  Object.assign(target, origin, {zoom: 1, yaw: -.18, pitch: .09});
  inspect();
}

function changeScope(next, {preserveCamera = false} = {}) {
  if (scope === next) return;
  scope = next;
  sources = new Set();
  neighbors = new Set();
  byId("search").value = "";
  updateCollection();
  if (preserveCamera) selected = null;
  else overview();
  renderSlideList();
}

function focusSlide(number) {
  if (number === presentationNode?.number) {
    changeScope("presentation");
    byId("knowledge-back").focus();
    return;
  }
  const note = notes.get(number);
  const node = note || slides.find((slide) => slide.number === number);
  if (!node) return;
  changeScope(note ? "knowledge" : "presentation");
  focusNode(node);
}

function focusNode(node) {
  selected = node.number;
  Object.assign(target, {x: node.x, y: node.y, z: node.z, zoom: 1.65});
  setRotation(false);
  inspect(node);
  for (const button of byId("slide-list").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.number === String(node.number)));
  }
}

function inspect(node) {
  const output = byId("inspection");
  output.replaceChildren();
  if (node) {
    const title = document.createElement("h3");
    title.textContent = nodeTitle(node);
    output.append(title);
  }
  const text = document.createElement("p");
  text.textContent = node ? node.text || node.notes || "Slide ini berisi materi visual." : sources.size > 1 ?
    "Pilih salah satu sumber yang disorot untuk membacanya." :
    scope === "presentation" ? "Pilih slide untuk membaca isinya." : "Pilih satu node untuk membaca sumbernya.";
  output.append(text);
  if (node?.notes && node.text) {
    const notes = document.createElement("p");
    notes.textContent = node.notes;
    output.append(notes);
  }
  if (node?.slides?.length) {
    const list = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = node.slides.length + " slide";
    list.append(summary);
    for (const slide of node.slides) {
      const entry = document.createElement("details");
      const heading = document.createElement("summary");
      heading.textContent = nodeTitle(slide);
      const body = document.createElement("p");
      body.textContent = [slide.text, slide.notes].filter(Boolean).join("\n\n") || "Slide ini berisi materi visual.";
      entry.append(heading, body);
      list.append(entry);
    }
    output.append(list);
  }
}

function renderSlideList() {
  const query = byId("search").value.trim().toLocaleLowerCase("id");
  const filtered = visibleNodes().filter((node) =>
    (nodeTitle(node) + " " + node.text + " " + (node.searchText || "")).toLocaleLowerCase("id").includes(query));
  matches = query ? new Set(filtered.map((slide) => slide.number)) : null;
  const list = byId("slide-list");
  list.replaceChildren();
  for (const slide of filtered) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = nodeTitle(slide);
    button.dataset.number = slide.number;
    button.setAttribute("aria-pressed", String(slide.number === selected));
    button.addEventListener("click", () => focusSlide(slide.number));
    list.append(button);
  }
  if (query) list.closest("details").open = true;
  if (!filtered.length) list.textContent = query ? "Sumber tidak ditemukan." : "Belum ada sumber.";
}

function setRotation(value) {
  rotation = value;
  byId("rotate").setAttribute("aria-pressed", String(rotation));
}

byId("fit").addEventListener("click", () => { overview(); renderSlideList(); });
byId("knowledge-back").addEventListener("click", () => {
  changeScope("knowledge");
  byId("slide-list").querySelector("button")?.focus();
});
byId("rotate").addEventListener("click", () => setRotation(!rotation));
byId("search").addEventListener("input", renderSlideList);
canvas.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  canvas.setPointerCapture(event.pointerId);
  drag = {x: event.clientX, y: event.clientY, yaw: target.yaw, pitch: target.pitch, moved: false};
});
canvas.addEventListener("pointermove", (event) => {
  if (!drag) return;
  const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
  if (!drag.moved && Math.hypot(dx, dy) < 4) return;
  drag.moved = true;
  setRotation(false);
  target.yaw = drag.yaw + dx * .009;
  target.pitch = Math.max(-1.05, Math.min(1.05, drag.pitch - dy * .007));
  canvas.classList.add("dragging");
});
canvas.addEventListener("pointerup", (event) => {
  if (drag && !drag.moved) {
    const bounds = canvas.getBoundingClientRect();
    const x = event.clientX - bounds.left, y = event.clientY - bounds.top;
    const hit = projected.findLast((point) => Math.hypot(point.x - x, point.y - y) < point.radius + 8);
    if (hit) focusSlide(hit.node.number);
  }
  drag = null;
  canvas.classList.remove("dragging");
});
canvas.addEventListener("pointercancel", () => { drag = null; canvas.classList.remove("dragging"); });
canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  target.zoom = Math.max(.55, Math.min(3.2, target.zoom * Math.exp(-event.deltaY * .0015)));
}, {passive: false});

async function action(name, data = {}) {
  const route = name === "ask" ? "chat" : name === "remember" ? "remember" : "api/" + name;
  const response = await fetch(route, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error("Jarvis belum bisa menerima tindakan ini. Coba lagi saat siap.");
}

function showActionError(error) {
  voiceStatus = error.message;
  showStatus();
}

function speakReply(text, options) {
  return voiceEnabled && speech.speakReply(text, options);
}

function endScreenShare(stream) {
  if (screenStream !== stream) return;
  screenStream = null;
  screenEnded = true;
  stream.getTracks().forEach((track) => track.stop());
  screenVideo.srcObject = null;
  byId("screen-live").hidden = true;
  screenButton.textContent = "Bagikan layar";
  screenButton.setAttribute("aria-pressed", "false");
  voiceStatus = "Berbagi layar telah berakhir.";
  showStatus();
}

const screenButton = byId("screen");
screenButton.disabled = !navigator.mediaDevices?.getDisplayMedia;
if (screenButton.disabled) screenButton.title = "Browser ini tidak mendukung berbagi layar.";
screenButton.addEventListener("click", async () => {
  if (screenStream) {
    endScreenShare(screenStream);
    return;
  }
  screenButton.disabled = true;
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: false});
    const track = stream.getVideoTracks()[0];
    if (!track || track.readyState !== "live") throw new Error("Layar yang dipilih sudah tidak tersedia.");
    screenStream = stream;
    screenEnded = false;
    track.addEventListener("ended", () => endScreenShare(stream), {once: true});
    byId("screen-live").hidden = false;
    screenButton.textContent = "Berhenti berbagi";
    screenButton.setAttribute("aria-pressed", "true");
    voiceStatus = "";
    screenVideo.srcObject = stream;
    await screenVideo.play();
    showStatus();
  } catch (error) {
    if (stream && screenStream === stream) endScreenShare(stream);
    else stream?.getTracks().forEach((track) => track.stop());
    showActionError(new Error(error.name === "NotAllowedError" ? "Berbagi layar dibatalkan." : "Browser gagal memulai berbagi layar."));
  } finally {
    screenButton.disabled = false;
  }
});

async function askScreen(question, turn, stream) {
  const track = stream?.getVideoTracks()[0];
  if (!track || track.readyState !== "live") {
    if (stream) endScreenShare(stream);
    throw new Error(screenEnded ? "Berbagi layar telah berakhir. Bagikan lagi agar saya bisa melihatnya." :
      "Layar belum dibagikan. Pilih Bagikan layar agar saya bisa melihatnya.");
  }
  if (track.muted || screenVideo.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || !screenVideo.videoWidth) {
    throw new Error("Cuplikan layar belum siap. Tunggu sebentar lalu tanya lagi.");
  }
  const frame = document.createElement("canvas");
  const {videoWidth: width, videoHeight: height} = screenVideo;
  const scale = Math.min(1, 3072 / Math.max(width, height));
  frame.width = Math.max(1, Math.round(width * scale));
  frame.height = Math.max(1, Math.round(height * scale));
  frame.getContext("2d").drawImage(screenVideo, 0, 0, frame.width, frame.height);
  const jpeg = await new Promise((resolve) => frame.toBlob(resolve, "image/jpeg", .91));
  if (pendingTurn !== turn) return;
  if (screenStream !== stream || track.readyState !== "live") {
    endScreenShare(stream);
    throw new Error("Berbagi layar telah berakhir. Bagikan lagi agar saya bisa melihatnya.");
  }
  if (jpeg?.type !== "image/jpeg") throw new Error("Jarvis gagal membuat cuplikan JPEG.");
  const response = await fetch("see", {method: "POST", body: jpeg, headers: {
    "Content-Type": jpeg.type, "X-Jarvis-Question": encodeURIComponent(question), "X-Jarvis-Turn": turn,
  }});
  if (!response.ok) throw new Error(response.status === 409 ? "Jarvis masih memproses tugas. Coba lagi saat siap." :
    "Jarvis gagal mengirim cuplikan JPEG ke fitur layar.");
}

function speakWelcome() {
  if (!welcome || !welcomeActivated || welcomeDone || !voiceEnabled || document.hidden ||
      currentState?.presenting || listening || finishingSpeech || pendingTurn || speaking) return;
  welcomeDone = true;
  speakReply(welcome.text, {language: welcome.language});
}

function presentationControl(question) {
  if (!currentState?.presenting) return false;
  const text = question.toLowerCase().replace(/[^\p{L}\s]/gu, " ").trim()
    .replace(/^(?:(?:jarvis|halo|tolong|please)\s+)+/u, "").replace(/\s+/gu, " ");
  if (/^(?:lanjut(?:kan)?|resume|continue)(?: (?:narasi|presentasi|slideshow|presentation))?$/u.test(text)) {
    voiceStatus = "";
    action("pause", {paused: false}).catch(showActionError);
    return true;
  }
  if (/^(?:jeda|pause|stop|berhenti)(?: (?:narasi|presentasi|slideshow|presentation))?$/u.test(text)) {
    interrupt("jeda");
    return true;
  }
  if (/^(?:akhiri|end) (?:presentasi|slideshow|presentation)$/u.test(text)) {
    action("finish").catch(showActionError);
    return true;
  }
  return false;
}

async function submitQuestion(question, {spoken = false} = {}) {
  if (!question || pendingTurn) return false;
  if (presentationControl(question)) return true;
  const remember = question.match(/^(remember\s+that|ingat(?:lah)?\s+bahwa)\b\s*[,:\u2014-]?\s*/iu);
  const stream = screenStream;
  const useScreen = !remember && (stream || SCREEN_QUESTION.test(question));
  if (currentState?.questionPending || (currentState?.busy && (!currentState.presenting || remember || useScreen))) {
    showActionError(new Error("Jarvis masih memproses tugas. Ucapkan stop atau tunggu, atau coba lagi saat siap."));
    if (remember && !currentState.presenting) speakReply("Catatan belum tersimpan. Coba lagi saat Jarvis siap.", {language: "id-ID"});
    return false;
  }
  welcomeDone = true;
  byId("welcome").hidden = true;
  speech.stopListening();
  speech.stopSpeaking();
  narrationTurn = null;
  voiceStatus = "";
  screenReply = null;
  const turn = crypto.randomUUID();
  pendingTurn = turn;
  preview = question;
  showReply(null, question);
  showStatus();
  try {
    if (remember) {
      await action("remember", {text: question.slice(remember[0].length), turn,
        language: remember[1].toLowerCase().startsWith("remember") ? "en" : "id"});
    } else if (useScreen) {
      await askScreen(question, turn, stream);
    } else {
      await action("ask", {question, turn, context: scope, client, mute: muted, spoken});
    }
    return true;
  } catch (error) {
    if (pendingTurn !== turn) return false;
    pendingTurn = null;
    if (remember) {
      const message = "Catatan belum tersimpan. Coba lagi.";
      showActionError(new Error(message));
      speakReply(message, {language: "id-ID"});
    } else if (useScreen) {
      preview = "";
      screenReply = {question, answer: error.message, sources: []};
      showReply(screenReply, question);
      speakReply(screenReply.answer, {language: "id-ID"});
      showStatus();
    } else {
      showActionError(error);
    }
    return false;
  }
}

byId("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("question");
  const draft = input.value;
  if (await submitQuestion(draft.trim()) && input.value === draft) input.value = "";
});

for (const name of ["choose", "present", "pause", "finish", "stop"]) {
  byId(name).addEventListener("click", () => {
    voiceStatus = "";
    if (name === "present") {
      speech.stopListening();
      speech.stopSpeaking();
    }
    const data = name === "present" ? {client, mute: muted} : name === "pause" ? {paused: !currentState?.paused} : {};
    action(name, data).catch(showActionError);
  });
}

function answerLanguage(text, fallback = recognitionLanguage()) {
  const words = text.toLowerCase().match(/\p{L}+/gu) || [];
  const indonesian = words.filter((word) => /^(adalah|yang|dan|untuk|dari|saya|anda|kamu|dengan|tidak|bisa|sudah|ini|itu|saat|karena|sebagai|pada|belum|juga|serta|silakan|terima|kasih|catatan|menurut|apa|berapa)$/u.test(word)).length;
  const english = words.filter((word) => /^(the|and|you|your|can|are|was|with|of|to|from|not|have|this|that|hello|hi|thanks|good|morning|what|which|does|tell|please|how)$/u.test(word)).length;
  if (indonesian === english) return fallback;
  return indonesian > english ? "id-ID" : "en-GB";
}

function interrupt(word) {
  pendingTurn = null;
  preview = "";
  screenReply = null;
  const pause = /^(wait|tunggu|jeda)$/u.test(word);
  if (currentState?.presenting) {
    if (narrationTurn) speech.pause();
    action("pause", {paused: true}).catch(showActionError);
  } else {
    speech.stopSpeaking();
  }
  voiceStatus = currentState?.presenting ? "Narasi dijeda. Pilih Lanjutkan untuk meneruskan." :
    pause ? "Jarvis menunggu." : "Suara Jarvis dihentikan.";
  showReply(currentState?.reply, "");
  showStatus();
}

const talk = byId("talk");
const listen = byId("listen");
const voice = byId("voice");
const languagePicker = byId("speech-mode");
languagePicker.value = voiceMode;
const speech = createSpeech({
  muted,
  async replyAudio(text, language, signal) {
    const response = await fetch("api/reply-audio", {
      method: "POST", headers: {"Content-Type": "application/json"}, signal,
      body: JSON.stringify({text, language: language?.startsWith("id") ? "id" : "en"}),
    });
    if (!response.ok || !response.headers.get("Content-Type")?.startsWith("audio/wav")) {
      throw new Error("VoiceStudio audio unavailable.");
    }
    return response.blob();
  },
  openMicrophone: navigator.mediaDevices?.getUserMedia ? openJarvisMicrophone : null,
  onListening(value) {
    listening = value;
    if (!value) {
      micReady = false;
      micName = "";
      wakeDetected = false;
      talkHold = null;
      finishingSpeech = false;
    }
    showStatus();
  },
  onAudioReady(value) {
    micReady = value;
    showStatus();
  },
  onAudioDevice(name) {
    micName = name || "";
  },
  onWake() {
    wakeDetected = true;
    showStatus();
  },
  onSpeaking(value) {
    speaking = value;
    showStatus();
  },
  onTranscript(text) {
    preview = text;
    screenReply = null;
    showReply(null, text);
  },
  onQuestion: (text) => submitQuestion(text, {spoken: true}),
  onInterrupt: interrupt,
  onError(message) {
    handsFree = false;
    voiceStatus = message;
    showStatus();
  },
});

function clearHeldRecorder() {
  talkHold = null;
  micReady = false;
  micName = "";
  listening = false;
  finishingSpeech = false;
}

const recorder = createHoldRecorder({
  onReady(name) {
    micName = name || "";
    micReady = true;
    showStatus();
  },
  async onAudio(audio) {
    micReady = false;
    listening = false;
    showStatus();
    try {
      const response = await fetch("api/transcribe", {method: "POST", body: audio, headers: {
        "Content-Type": audio.type, "X-Jarvis-Language": voiceMode,
      }});
      const result = await response.json();
      if (!response.ok || !result.text) throw new Error(result.error || "Whisper belum memberikan transkripsi.");
      finishingSpeech = false;
      const word = speechInterrupt(result.text);
      if (word) interrupt(word);
      else await submitQuestion(result.text, {spoken: true});
    } catch (error) {
      showActionError(error);
    } finally {
      finishingSpeech = false;
      showStatus();
    }
  },
  onError(message) {
    clearHeldRecorder();
    showActionError(new Error(message));
  },
});

languagePicker.addEventListener("change", () => {
  if (talkHold !== null || finishingSpeech) {
    languagePicker.value = voiceMode;
    return;
  }
  voiceMode = languagePicker.value;
  const url = new URL(location.href);
  url.searchParams.set("lang", voiceMode);
  history.replaceState(null, "", url);
  voiceStatus = "";
  if (listening) speech.stopListening();
  resumeHandsFree();
  showStatus();
});

function activateSpeech(event) {
  if (!event.isTrusted) return;
  speech.activate();
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest("#talk, #listen, #send, #present, #voice, #screen") ||
      event.type === "keydown" && (target?.closest("#composer") ||
        event.key === " " && !target?.closest("input, textarea, select, [contenteditable], button, summary, a"))) return;
  welcomeActivated = true;
  speakWelcome();
}

document.addEventListener("click", activateSpeech, {capture: true});
document.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") activateSpeech(event);
}, {capture: true});
window.addEventListener("pagehide", () => {
  recorder.cancel();
  speech.stopListening();
  speech.stopSpeaking();
  if (screenStream) endScreenShare(screenStream);
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && talkHold !== null) cancelTalk(talkHold);
  else if (document.hidden && handsFree) speech.stopListening();
  else resumeHandsFree();
});

function beginTalk(input) {
  if (!currentState || !recorder.canRecord || talkHold !== null || finishingSpeech) return false;
  handsFree = false;
  if (listening) speech.stopListening();
  talkHold = input;
  voiceStatus = "";
  preview = "";
  if (currentState?.presenting) {
    if (narrationTurn) speech.pause();
    else speech.stopSpeaking();
    if (!currentState.paused) action("pause", {paused: true}).catch(showActionError);
  } else {
    speech.stopSpeaking();
  }
  listening = true;
  micReady = false;
  recorder.start();
  showStatus();
  return listening && talkHold === input;
}

function endTalk(input) {
  if (talkHold === null || talkHold !== input) return false;
  talkHold = null;
  finishingSpeech = true;
  recorder.release();
  showStatus();
  return true;
}

function cancelTalk(input) {
  if (talkHold === null || talkHold !== input) return;
  recorder.cancel();
  clearHeldRecorder();
  preview = "";
  renderedReply = "";
  voiceStatus = "Perekaman terputus saat tombol masih ditahan. Tahan lagi untuk mengulang ucapan.";
  showStatus();
}

talk.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  if (event.isTrusted) speech.activate();
  if (beginTalk(event.pointerId)) {
    event.preventDefault();
    talk.focus({preventScroll: true});
    talk.setPointerCapture(event.pointerId);
  }
});
talk.addEventListener("pointerup", (event) => endTalk(event.pointerId));
for (const name of ["pointercancel", "lostpointercapture"]) {
  talk.addEventListener(name, (event) => cancelTalk(event.pointerId));
}

function talkKey(event) {
  if (event.code === "Space" || event.key === " ") return "Space";
  if (event.key === "Enter") return "Enter";
  return null;
}

document.addEventListener("keydown", (event) => {
  const key = talkKey(event);
  if (!key || event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
  if (event.target !== talk && (key === "Enter" ||
      event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable], button, summary, a"))) return;
  if (talkHold === key || (!event.repeat && beginTalk(key))) event.preventDefault();
});
document.addEventListener("keyup", (event) => {
  if (endTalk(talkKey(event))) event.preventDefault();
});
window.addEventListener("blur", () => cancelTalk(talkHold));

function resumeHandsFree() {
  if (handsFree && currentState && !document.hidden && !currentState.busy &&
      !listening && !finishingSpeech && !pendingTurn && !speech.isSpeaking()) {
    speech.startListening({wake: true, language: recognitionLanguage()});
  }
}

listen.addEventListener("click", () => {
  handsFree = !handsFree;
  voiceStatus = "";
  preview = "";
  speech.stopListening();
  if (handsFree && !currentState?.presenting) speech.stopSpeaking();
  resumeHandsFree();
  showStatus();
});

voice.addEventListener("click", () => {
  if (muted) return;
  voiceEnabled = !voiceEnabled;
  if (!voiceEnabled && !narrationTurn) speech.stopSpeaking();
  showStatus();
});

function showStatus() {
  if (!currentState) return;
  const state = currentState;
  let phase = state.phase;
  const presentationQA = state.qa && scope === "presentation";
  let status = voiceStatus || (state.qa && phase === "ready"
    ? (presentationQA ? "Tahan Bicara atau ketik pertanyaan tentang presentasi."
      : "Siap menerima pertanyaan dan perintah.")
    : state.status);
  if (!voiceStatus && scope === "knowledge" && phase === "ready" && status.includes("Naskah untuk ")) {
    status = "Siap menerima pertanyaan dan perintah.";
  }
  if (finishingSpeech) {
    phase = "thinking";
    status = "Whisper lokal sedang membaca rekaman…";
  } else if (listening && !micReady) {
    phase = "thinking";
    status = "Menyiapkan mikrofon… Tunggu sampai tertulis “Mic siap”.";
  } else if (listening) {
    phase = "listening";
    status = handsFree ? (wakeDetected ? "Silakan bicara." : "Menunggu panggilan “Jarvis”.") :
      `Mic siap${micName ? ` · ${micName}` : ""}. Bicara sambil menahan tombol, lalu lepas setelah kata terakhir.`;
  } else if (speaking && !state.paused) {
    phase = "speaking";
    status = "Jarvis sedang berbicara…";
  } else if (pendingTurn) {
    phase = "thinking";
    status = "Jarvis sedang memproses pertanyaan…";
  } else if (speech.isPreparingReply()) {
    phase = "thinking";
    status = "VoiceStudio sedang membuat audio balasan…";
  } else if (muted && phase === "speaking") {
    phase = "thinking";
    status = "Presentasi berjalan tanpa suara.";
  }
  const label = presentationQA && phase === "ready" ? "TANYA JAWAB" : phases[phase];
  const badge = byId("phase");
  if (badge.textContent !== label) badge.textContent = label;
  badge.dataset.phase = phase;
  byId("core").dataset.phase = phase;
  if (byId("status").textContent !== status) byId("status").textContent = status;
  talk.disabled = !recorder.canRecord;
  talk.setAttribute("aria-pressed", String(talkHold !== null));
  languagePicker.disabled = talkHold !== null || finishingSpeech;
  listen.disabled = !speech.canListen || talkHold !== null || finishingSpeech;
  listen.setAttribute("aria-pressed", String(handsFree));
  listen.title = voiceMode === "mix" ? "Dengarkan memakai ID. Gunakan Bicara untuk mode Campuran." : "Dengarkan kata pemicu Jarvis";
  voice.disabled = muted || !speech.canSpeak;
  voice.setAttribute("aria-pressed", String(voiceEnabled && speech.canSpeak));
  voice.textContent = voiceEnabled && speech.canSpeak ? "Suara jawaban" : "Jawaban senyap";
  talk.title = recorder.canRecord ? "Tahan tombol ini atau Spasi untuk bicara" : "Browser ini tidak mendukung perekaman mikrofon";
  byId("send").disabled = (state.busy && !state.presenting) || state.questionPending || Boolean(pendingTurn);
}

function syncNarration(state) {
  const narration = state.narration;
  if (!narration || narration.client !== client) {
    if (narrationTurn) speech.stopSpeaking();
    narrationTurn = null;
    return;
  }
  if (muted) return;
  if (narrationTurn && narrationTurn !== narration.turn) {
    speech.stopSpeaking();
    narrationTurn = null;
  }
  if (state.paused || listening || finishingSpeech || pendingTurn) {
    if (narrationTurn === narration.turn) speech.pause();
    return;
  }
  if (narrationTurn === narration.turn) {
    speech.resume();
    return;
  }
  // Let a spoken Q&A answer finish before handing the audio back to the slide.
  if (speech.isSpeaking()) return;
  narrationTurn = narration.turn;
  const done = (ok) => {
    action("narration-done", {client, turn: narration.turn, ok}).catch(() => {});
  };
  if (!speech.speak(narration.text, {language: "id-ID", onDone: done})) done(false);
}

function showReply(reply, transcript) {
  const content = byId("answer-panel");
  content.hidden = !reply && !transcript;
  byId("transcript").textContent = transcript || reply?.question || "";
  byId("answer").textContent = reply?.answer || "";
  const details = byId("details");
  details.textContent = reply?.sources.length ? "" : reply?.details || "";
  details.hidden = !details.textContent;
  const citations = byId("citations");
  citations.replaceChildren();
  const referenced = [...replyNotes(reply), ...(reply?.sources || [])];
  if (reply?.capture) referenced.push(noteId(reply.capture.key));
  for (const number of referenced) {
    const button = document.createElement("button");
    button.type = "button";
    const note = notes.get(number);
    button.textContent = note ? nodeTitle(note) : "Slide " + number;
    button.addEventListener("click", () => focusSlide(number));
    citations.append(button);
  }
}

function replyNotes(reply) {
  return (reply?.nodes || []).flatMap((index, rank) => {
    // Keys keep a citation attached to the same note if the graph was reindexed.
    const key = reply.memorySources?.[rank];
    const number = key ? noteId(key) : noteOrder[index]?.number;
    return notes.has(number) ? [number] : [];
  });
}

function showNoteProof(cited) {
  changeScope("knowledge", {preserveCamera: true});
  sources = new Set(cited);
  neighbors = new Set();
  setRotation(false);
  if (cited.length >= 4) {
    // Freeze even if the camera was still easing toward its last destination.
    Object.assign(target, camera);
    selected = null;
    inspect();
    renderSlideList();
    return;
  }
  const top = notes.get(cited[0]);
  for (const [left, right] of noteEdges) {
    if (left.number === top.number) neighbors.add(right.number);
    if (right.number === top.number) neighbors.add(left.number);
  }
  focusNode(top);
}

async function refresh() {
  try {
    const response = await fetch("api/state", {cache: "no-store", headers: {"X-Jarvis-Client": client}});
    if (!response.ok) throw new Error();
    const state = await response.json();
    currentState = state;
    if (state.deckVersion !== deckVersion) {
      const deckResponse = await fetch("api/deck", {cache: "no-store"});
      if (!deckResponse.ok) throw new Error();
      makeGraph(await deckResponse.json());
      deckVersion = state.deckVersion;
      memoryVersion = -1;
      latestReply = null;
    }
    const reply = state.reply;
    if (state.memoryVersion !== memoryVersion) {
      const response = await fetch("api/notes", {cache: "no-store"});
      if (!response.ok) throw new Error();
      const graph = await response.json();
      updateNotes(graph);
      if (!welcome && graph.greeting) {
        welcome = {text: graph.greeting, language: graph.greetingLanguage};
        const line = byId("welcome");
        line.textContent = welcome.text;
        line.lang = welcome.language;
        line.hidden = welcomeDone;
        speakWelcome();
      }
      memoryVersion = state.memoryVersion;
      renderedReply = "";
    }
    syncNarration(state);
    byId("presenter").hidden = scope !== "presentation" && !state.presenting;
    byId("choose").disabled = state.busy || state.questionPending;
    byId("present").disabled = state.busy || state.questionPending || !slides.length || (!muted && !speech.canSpeak);
    byId("pause").disabled = !state.presenting || state.questionPending || Boolean(pendingTurn);
    byId("finish").disabled = !state.presenting;
    byId("pause").textContent = state.paused ? "Lanjutkan narasi" : "Jeda narasi";
    byId("stop").disabled = !state.canStop;
    byId("stop").textContent = state.qa ? "Akhiri tanya jawab" : "Akhiri slideshow";

    if (reply?.number !== latestReply) {
      const liveCapture = latestReply !== null || pendingTurn === reply?.turn;
      latestReply = reply?.number;
      const cited = replyNotes(reply);
      neighbors = new Set();
      if (reply?.capture) {
        changeScope("knowledge");
        const id = noteId(reply.capture.key);
        sources = new Set([id]);
        setRotation(false);
        if (liveCapture) animateCapture(reply.capture);
        setTimeout(() => { if (latestReply === reply.number) focusSlide(id); }, liveCapture && !reducedMotion.matches ? 460 : 0);
      } else if (cited.length) {
        showNoteProof(cited);
      } else if (reply?.sources?.length) {
        sources = new Set(scope === "presentation" ? reply.sources :
          (presentationNode ? [presentationNode.number] : []));
        if (sources.size === 1) {
          const node = visibleNodes().find(({number}) => sources.has(number));
          if (node) focusNode(node);
        } else if (sources.size > 1) {
          setRotation(false);
          overview();
          renderSlideList();
        }
      } else {
        sources = new Set();
      }
    }
    const speakPending = pendingTurn && reply?.turn === pendingTurn;
    if (speakPending) {
      pendingTurn = null;
      preview = "";
      if (reply.presentation && presentationNode) changeScope("presentation");
    }
    const transcript = screenReply?.question || preview || state.transcript;
    const pending = !screenReply && ((listening && Boolean(preview)) || Boolean(pendingTurn) ||
      (state.busy && transcript && transcript !== reply?.question));
    const replyKey = JSON.stringify([screenReply?.answer || (pending ? null : reply?.number), transcript]);
    if (renderedReply !== replyKey) {
      showReply(screenReply || (pending ? null : reply), transcript);
      renderedReply = replyKey;
    }
    if (speakPending && voiceEnabled && !reply.presentation) {
      const onDone = (ok) => {
        if (!ok) showActionError(new Error("Jawaban tersedia di layar. Browser gagal membacakannya."));
      };
      if (!speakReply(reply.answer, {language: answerLanguage(reply.answer, answerLanguage(reply.question)), onDone})) onDone(false);
    }
    showStatus();
    resumeHandsFree();
  } catch {
    currentState = null;
    byId("phase").textContent = "TERPUTUS";
    byId("status").textContent = "Buka kembali dashboard dari aplikasi Jarvis.";
    talk.disabled = true;
    listen.disabled = true;
    byId("send").disabled = true;
  } finally {
    setTimeout(refresh, 220);
  }
}
refresh();
