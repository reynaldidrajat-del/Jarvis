import logging
import os
import re
import secrets
import sqlite3
import tempfile
import threading
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path


NOTES_ROOT = Path(__file__).resolve().parents[1] / "notes-galaxy" / "notes"
MATERIALS_ROOT = NOTES_ROOT.parents[1] / "materi external"
CLIENT_TIMEZONE = timezone(timedelta(hours=7), "Asia/Bangkok")
REMEMBER = re.compile(r"^\s*(?:remember\s+that\b|ingat(?:lah)?\s+bahwa\b)\s*[,:\u2014-]?\s*", re.I)
WIKILINK = re.compile(r"\[\[([^]\n]+)\]\]")
WORD = re.compile(r"[^\W_]+", re.UNICODE)
STOP_WORDS = frozenset("""
    a an and are as at be been but by can could did do does for from had has have how i in is it
    me my of on or our please should that the their them there these they this to up was were what
    when where which who will with would you your remember jarvis tell
    ada adalah agar akan apa apakah atau bahwa bisa dalam dan dari dengan di ini itu kapan ke
    kenapa mana mereka pada saat saya sebutkan sudah tentang tolong untuk yang saya ingat pernah
""".split())
WORDS = {"milliseconds": "ms", "millisecond": "ms", "milidetik": "ms"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    received_at TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terms (
    word TEXT NOT NULL,
    turn_id INTEGER NOT NULL REFERENCES turns(id),
    PRIMARY KEY (word, turn_id)
);
"""


@dataclass(frozen=True)
class Note:
    key: str
    title: str
    group: str
    text: str
    updated_at: str
    slides: tuple = ()


def words(text):
    return set(WORDS.get(word, word) for word in WORD.findall(text.casefold()) if word not in STOP_WORDS)


def read_notes(folder):
    notes = []
    for path in sorted(folder.rglob("*.md"), key=lambda item: (item.as_posix().casefold(), item.as_posix())):
        if not path.is_file() or not path.resolve().is_relative_to(folder):
            continue
        relative = path.relative_to(folder)
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        heading = re.match(r"# (.+)", text)
        title = heading[1].strip() if relative.parts[0] == "captures" and heading else path.stem
        group = relative.parent.as_posix() if relative.parent != Path(".") else "Catatan"
        updated_at = datetime.fromtimestamp(path.stat().st_mtime, CLIENT_TIMEZONE).isoformat(timespec="seconds")
        notes.append(Note(relative.as_posix(), title, group, text, updated_at))
    return tuple(notes)


def material_key(path):
    if path.is_relative_to(MATERIALS_ROOT):
        return (Path(MATERIALS_ROOT.name) / path.relative_to(MATERIALS_ROOT)).as_posix()
    return None


def material_title(path):
    return "Presentasi MT" if re.match(r"^presentasi reynaldi drajat\b", path.stem, re.I) else path.stem


def slide_content(slide):
    return f"Slide {slide.number}: {slide.title}\n{slide.text}\n{slide.notes}".strip()


def note_excerpt(note, terms, question_parts):
    if note.slides:
        title_terms = words(note.title)
        local_terms = max(question_parts, key=lambda part: len(part & title_terms), default=terms)
        if local_terms - title_terms and len(local_terms & title_terms) >= min(2, len(title_terms)):
            terms = local_terms
        ranked = sorted(note.slides, key=lambda slide: (
            3 * len(terms & words(slide.title)) + len(terms & words(slide_content(slide)))
        ), reverse=True)
        # The cover can name the procedure; the next slide often defines it.
        cover = note.slides[0]
        candidates = dict.fromkeys([*ranked[:2], *note.slides[1:2], *ranked[2:3]])
        passages = [slide_content(slide)[:890] for slide in candidates if slide != cover][:3]
        return "\n\n".join([slide_content(cover)[:300], *passages])
    lines = note.text.splitlines()
    hit_lines = sorted(range(len(lines)), key=lambda index: -len(terms & words(lines[index])))
    indices = set(range(min(4, len(lines))))
    for index in hit_lines[:4]:
        indices.update(range(max(0, index - 1), min(len(lines), index + 2)))
    return "\n".join(lines[index][:700] for index in sorted(indices))[:3000]


def graph_for(notes):
    titles = defaultdict(list)
    paths = {}
    for index, note in enumerate(notes):
        titles[note.title.casefold()].append(index)
        paths[Path(note.key).with_suffix("").as_posix().casefold()] = index
    mentions = {
        re.compile(r"(?<!\w)" + re.escape(title) + r"(?!\w)", re.I): ids[0]
        for title, ids in titles.items() if len(ids) == 1
    }
    pairs = set()
    shared = defaultdict(set)

    def connect(source, target):
        if source != target:
            pairs.add((min(source, target), max(source, target)))

    for index, note in enumerate(notes):
        body = re.sub(r"^# .+\n", "", note.text, count=1)
        for pattern, target in mentions.items():
            if pattern.search(body):
                connect(index, target)
        for wiki in WIKILINK.findall(body):
            name = wiki.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/").casefold()
            target = paths.get(name)
            candidates = titles.get(name, [])
            if target is None and len(candidates) == 1:
                target = candidates[0]
            if target is not None:
                connect(index, target)
                shared[target].add(index)
            elif name and len(candidates) < 2:
                shared[name].add(index)
    for indices in shared.values():
        for left, right in combinations(sorted(indices), 2):
            connect(left, right)
    return {
        "nodes": [
            {"id": index, "key": note.key, "label": note.title, "group": note.group,
             "excerpt": note.text[:700],
             **({"slides": [{"number": slide.number, "title": slide.title,
                             "text": slide.text, "notes": slide.notes} for slide in note.slides]}
                if note.slides else {})}
            for index, note in enumerate(notes)
        ],
        "links": [{"source": source, "target": target} for source, target in sorted(pairs)],
    }


class JarvisMemory:
    def __init__(self, folder=None, *, deck_loader=None):
        self.folder = Path(folder or os.environ.get("JARVIS_NOTES_DIR") or NOTES_ROOT).resolve()
        self.deck_loader = deck_loader
        self.materials = {}
        self.indexed_materials = ()
        self.database = self.folder.parent / "memory.sqlite3"
        self.lock = threading.RLock()
        self.notes = ()
        self.graph = graph_for(self.notes)
        self.version = 0
        self.stamp = None

    def refresh_materials(self):
        if self.deck_loader is None:
            return ()
        materials = {}
        for path in sorted(MATERIALS_ROOT.rglob("*.pptx")):
            if path.name.startswith("~$") or not path.resolve().is_relative_to(MATERIALS_ROOT):
                continue
            previous = self.materials.get(path)
            try:
                stat = path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
                if previous and previous[0] == stamp:
                    materials[path] = previous
                    continue
                deck = self.deck_loader(path)
            except (OSError, RuntimeError) as error:
                if previous:
                    materials[path] = previous
                logging.getLogger(__name__).warning("Skipping changed material %s: %s", path.name, error)
                continue
            content = "\n\n".join(map(slide_content, deck.slides))
            updated_at = datetime.fromtimestamp(stat.st_mtime, CLIENT_TIMEZONE).isoformat(timespec="seconds")
            materials[path] = stamp, Note(material_key(path), material_title(path), "Materi external",
                                          content, updated_at, deck.slides)
        self.materials = materials
        return tuple(note for _, note in materials.values())

    def refresh(self):
        with self.lock:
            stamp = []
            for path in sorted(self.folder.rglob("*.md")):
                if path.is_file() and path.resolve().is_relative_to(self.folder):
                    stat = path.stat()
                    stamp.append((path, stat.st_mtime_ns, stat.st_size))
            materials = self.refresh_materials()
            if stamp != self.stamp or materials != self.indexed_materials:
                notes = read_notes(self.folder) + materials
                if notes != self.notes:
                    self.graph = graph_for(notes)
                    self.notes = notes
                    self.version += 1
                self.stamp = stamp
                self.indexed_materials = materials
            return self.version, self.graph

    def capture(self, text):
        if not text.strip():
            raise RuntimeError("Sebutkan apa yang perlu saya ingat setelah “remember that”.")
        now = datetime.now(CLIENT_TIMEZONE)
        title = " ".join(text.split()[:7]).rstrip(".,!?;:")[:85] or "Catatan"
        slug = "-".join(WORD.findall(title.casefold()))[:65] or "catatan"
        with self.lock:
            self.refresh()
            terms = words(text)
            best = max(self.notes, key=lambda note: len(terms & words(note.text)), default=None)
            related = best.key if best and terms & words(best.text) else None
            folder = self.folder / "captures"
            folder.mkdir(parents=True, exist_ok=True)
            if not folder.resolve().is_relative_to(self.folder):
                raise OSError("Folder captures berada di luar folder catatan.")
            path = folder / f"{now:%Y-%m-%d}-{slug}-{secrets.token_hex(6)}.md"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as temp:
                    temporary = Path(temp.name)
                    temp.write(f"# {title}\n\nTanggal: {now:%Y-%m-%d}\n\n{text.strip()}\n")
                temporary.rename(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            try:
                self.refresh()
            except OSError as error:
                raise RuntimeError("Catatan tersimpan, tetapi belum bisa masuk galaksi. Coba lagi nanti.") from error
            return {"key": path.relative_to(self.folder).as_posix(), "related": related, "title": title}

    def record(self, text):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript(SCHEMA)
            cursor = db.execute("INSERT INTO turns(received_at, text) VALUES (?, ?)",
                                (datetime.now(CLIENT_TIMEZONE).isoformat(timespec="seconds"), text))
            turn = cursor.lastrowid
            db.executemany("INSERT INTO terms(word, turn_id) VALUES (?, ?)",
                           ((word, turn) for word in words(text)))
        return turn

    def recall(self, query, before):
        self.refresh()
        terms = words(query)
        if not terms:
            return []
        question_parts = [words(part) for part in re.split(r"[,;?]\s+|\n", query)]
        matches = []
        with self.lock:
            notes = self.notes
        for note in notes:
            title_matches = terms & words(note.title)
            body_matches = terms & words(note.text)
            score = 3 * len(title_matches) + len(body_matches)
            if score:
                matches.append((score, {"id": note.key, "kind": "note", "title": note.title,
                                        "text": note_excerpt(note, terms, question_parts),
                                        "date": note.updated_at}))
        if before and (len(terms) > 1 or len(next(iter(terms))) >= 4):
            keywords = sorted(terms)[:60]
            placeholders = ",".join("?" for _ in keywords)
            minimum = min(2, len(keywords))
            with closing(sqlite3.connect(self.database)) as db:
                rows = db.execute(
                    f"SELECT turns.id, text, received_at, COUNT(*) AS score FROM terms "
                    f"JOIN turns ON turns.id = terms.turn_id WHERE word IN ({placeholders}) AND turns.id < ? "
                    f"GROUP BY turns.id HAVING COUNT(*) >= ? ORDER BY score DESC, turns.id DESC LIMIT 16",
                    (*keywords, before, minimum),
                ).fetchall()
            seen = {query.casefold(), *(note.text.casefold() for note in notes)}
            for turn, text, date, score in rows:
                normalized = text.casefold()
                if normalized in seen or REMEMBER.match(text):
                    continue
                seen.add(normalized)
                matches.append((score, {"id": f"turn:{turn}", "kind": "conversation",
                                        "title": "Percakapan " + date[:10], "text": text[:3000], "date": date}))
        matches.sort(key=lambda match: (match[0], match[1]["date"]), reverse=True)
        return [record for _, record in matches[:6]]
