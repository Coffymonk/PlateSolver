# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Astronomy quiz: question file format, progress and question picking.

The questions live in one binary file, ``quiz.dat``. It is compressed and scrambled so the answers
can't simply be read in a text editor (this is not encryption, just keeping the answers out of sight).
It also carries one picture per topic. A newer quiz file can replace the old one at any time:

  * replace ``platesolver/data/quiz.dat`` in the program folder, or
  * put a ``quiz.dat`` in the PlateSolver settings folder, or
  * point Settings › Astronomy quiz › Quiz file at it.

``tools/build_quiz.py`` turns editable JSON files into a quiz file, and can unpack one again.

File layout (all integers little-endian):
    4 bytes   b"PSQZ"
    2 bytes   format version (1)
    4 bytes   CRC-32 of the unscrambled, uncompressed JSON
    4 bytes   scramble seed
    rest      zlib-compressed JSON, XOR-ed with a keystream derived from the seed
"""
from __future__ import annotations

import base64
import json
import random
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from platesolver.core.settings import ACTION, BOOL, CHOICE, FILE, SettingField, SettingsSection

MAGIC = b"PSQZ"
FORMAT_VERSION = 1
_KEY = 0x5A17C0DE
LEVELS = {1: "Beginner", 2: "Intermediate", 3: "Advanced"}
BUILTIN_FILE = Path(__file__).resolve().parent.parent / "data" / "quiz.dat"


# ---------------------------------------------------------------- file format
def _keystream(seed: int, n: int) -> bytes:
    return random.Random(seed ^ _KEY).randbytes(n)


def _xor(data: bytes, seed: int) -> bytes:
    ks = _keystream(seed, len(data))
    return (int.from_bytes(data, "little") ^ int.from_bytes(ks, "little")).to_bytes(len(data), "little")


def pack(content: dict) -> bytes:
    """Quiz content (dict, see QuizBank.from_dict) -> bytes of a quiz file."""
    raw = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    seed = random.getrandbits(32)
    body = _xor(zlib.compress(raw, 9), seed)
    return MAGIC + struct.pack("<HII", FORMAT_VERSION, zlib.crc32(raw), seed) + body


def unpack(data: bytes) -> dict:
    if len(data) < 14 or data[:4] != MAGIC:
        raise ValueError("not a PlateSolver quiz file")
    version, crc, seed = struct.unpack("<HII", data[4:14])
    if version > FORMAT_VERSION:
        raise ValueError(f"quiz file format {version} needs a newer PlateSolver")
    try:
        raw = zlib.decompress(_xor(data[14:], seed))
    except zlib.error as exc:
        raise ValueError("quiz file is damaged") from exc
    if zlib.crc32(raw) != crc:
        raise ValueError("quiz file is damaged (checksum mismatch)")
    return json.loads(raw.decode("utf-8"))


def question_id(text: str) -> str:
    """Stable id from the question text, so progress survives a file update."""
    return f"{zlib.crc32(' '.join(text.lower().split()).encode('utf-8')):08x}"


# ---------------------------------------------------------------- content
@dataclass
class Topic:
    id: str
    name: str
    image: bytes = b""            # PNG, twice as high as wide


@dataclass
class Question:
    id: str
    topic: str
    text: str
    options: list[str]
    answer: int
    explain: str
    level: int

    @property
    def correct_text(self) -> str:
        return self.options[self.answer]


@dataclass
class QuizBank:
    title: str = "Astronomy quiz"
    edition: str = ""
    topics: list[Topic] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    source: str = ""

    @classmethod
    def from_dict(cls, d: dict, source: str = "") -> "QuizBank":
        topics = [Topic(t["id"], t["name"], base64.b64decode(t.get("image", "") or b""))
                  for t in d.get("topics", [])]
        known = {t.id for t in topics}
        qs = []
        for q in d.get("questions", []):
            opts = list(q["o"])
            a = int(q["a"])
            if not (2 <= len(opts) <= 6 and 0 <= a < len(opts)):
                continue
            t = q.get("t", "")
            if t not in known:
                topics.append(Topic(t, t.replace("_", " ").title()))
                known.add(t)
            qs.append(Question(q.get("id") or question_id(q["q"]), t, q["q"], opts, a,
                               q.get("e", ""), int(q.get("l", 2))))
        return cls(d.get("title", "Astronomy quiz"), str(d.get("edition", "")), topics, qs, source)

    def to_dict(self) -> dict:
        return {
            "title": self.title, "edition": self.edition,
            "topics": [{"id": t.id, "name": t.name, "image": base64.b64encode(t.image).decode("ascii")}
                       for t in self.topics],
            "questions": [{"id": q.id, "t": q.topic, "q": q.text, "o": q.options, "a": q.answer,
                           "e": q.explain, "l": q.level} for q in self.questions],
        }

    @classmethod
    def load(cls, path: Path) -> "QuizBank":
        return cls.from_dict(unpack(Path(path).read_bytes()), str(path))

    def save(self, path: Path) -> None:
        Path(path).write_bytes(pack(self.to_dict()))

    def topic(self, topic_id: str) -> Topic | None:
        return next((t for t in self.topics if t.id == topic_id), None)

    def count(self, topic_id: str = "") -> int:
        return sum(1 for q in self.questions if not topic_id or q.topic == topic_id)


BUILTIN_QUIZZES = (("quiz.dat", "Astronomy and astrophotography"),
                   ("quiz_astrophysics.dat", "Astrophysics and cosmology"))
DEFAULT_QUIZ = "builtin:quiz.dat"
MY_QUIZZES = "My quizzes"


def my_quizzes_dir(create: bool = True) -> Path:
    """Where quizzes made by the user are kept: %APPDATA%\\PlateSolver\\My quizzes."""
    from platesolver.core import paths
    d = paths.app_data_dir() / MY_QUIZZES
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


_title_cache: dict[tuple[str, float], str] = {}


def quiz_title(path: Path) -> str:
    """The quiz's own title (stored in the file), read once per file version."""
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return path.stem
    if key not in _title_cache:
        try:
            bank = QuizBank.load(path)
            _title_cache[key] = f"{bank.title or path.stem} ({len(bank.questions)} questions)"
        except Exception:
            _title_cache[key] = f"{path.name} (can't be read)"
    return _title_cache[key]


def available_quizzes(extra_file: str = "") -> list[tuple[str, str, Path]]:
    """(key, label, path) for every quiz: the built-in ones first, then the user's own."""
    from platesolver.core import paths
    out = [(f"builtin:{name}", label, BUILTIN_FILE.parent / name) for name, label in BUILTIN_QUIZZES
           if (BUILTIN_FILE.parent / name).is_file()]
    legacy = paths.app_data_dir() / "quiz.dat"           # older versions: a replacement quiz.dat here
    if legacy.is_file():
        out.append(("legacy:quiz.dat", f"My quiz: {quiz_title(legacy)}", legacy))
    for f in sorted(my_quizzes_dir().glob("*.dat"), key=lambda x: x.name.lower()):
        out.append((f"user:{f.name}", f"My quiz: {quiz_title(f)}", f))
    if extra_file and Path(extra_file).is_file():
        out.append((f"file:{extra_file}", f"File: {quiz_title(Path(extra_file))}", Path(extra_file)))
    return out


def quiz_path(key: str) -> Path:
    """The file for a quiz key ('builtin:quiz.dat', 'user:My stars.dat', 'file:C:/x.dat' or a plain path)."""
    from platesolver.core import paths
    key = str(key or "")
    kind, _, name = key.partition(":")
    if kind == "builtin":
        return BUILTIN_FILE.parent / name
    if kind == "user":
        return my_quizzes_dir() / name
    if kind == "legacy":
        return paths.app_data_dir() / "quiz.dat"
    if kind == "file":
        return Path(name)
    return Path(key) if key else BUILTIN_FILE


def quiz_file_candidates(setting: str = "") -> list[Path]:
    out = [quiz_path(setting)] if setting else []
    out.append(BUILTIN_FILE)
    return out


def load_bank(setting: str = "") -> QuizBank:
    """The chosen quiz (a quiz key or a file path); the built-in quiz if that can't be read."""
    errors = []
    for p in quiz_file_candidates(setting):
        if p.is_file():
            try:
                bank = QuizBank.load(p)
                if bank.questions:
                    return bank
                errors.append(f"{p}: no questions")
            except Exception as exc:
                errors.append(f"{p.name}: {exc}")
        else:
            errors.append(f"{p.name}: not found")
    raise FileNotFoundError("No quiz file found. " + "; ".join(errors))


def chosen_quiz(settings) -> str:
    """The quiz key from the settings (older versions stored a file path under 'file')."""
    key = str(settings.get("quiz") or "")
    if not key:
        old = str(settings.get("file") or "")
        key = f"file:{old}" if old else DEFAULT_QUIZ
    return key


def progress_file(key: str) -> Path:
    """Each quiz keeps its own score; the first built-in quiz keeps the original file name."""
    import hashlib
    from platesolver.core import paths
    if key in ("", DEFAULT_QUIZ):
        return paths.app_data_dir() / "quiz_progress.json"
    tag = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return paths.app_data_dir() / f"quiz_progress_{tag}.json"


# ---------------------------------------------------------------- progress
class QuizProgress:
    """Which questions have been asked, and the score, kept in quiz_progress.json."""

    def __init__(self, path: Path | None = None):
        if path is None:
            from platesolver.core import paths
            path = paths.app_data_dir() / "quiz_progress.json"
        self.path = path
        self.asked: set[str] = set()
        self.answered = 0
        self.correct = 0
        self.by_topic: dict[str, list[int]] = {}
        self.topic = ""
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            self.asked = set(d.get("asked", []))
            self.answered, self.correct = int(d.get("answered", 0)), int(d.get("correct", 0))
            self.by_topic = {k: list(v) for k, v in d.get("by_topic", {}).items()}
            self.topic = d.get("topic", "")
        except Exception:
            pass

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps({
                "asked": sorted(self.asked), "answered": self.answered, "correct": self.correct,
                "by_topic": self.by_topic, "topic": self.topic}), encoding="utf-8")
        except OSError:
            pass

    def record(self, q: Question, ok: bool) -> None:
        self.asked.add(q.id)
        self.answered += 1
        self.correct += int(ok)
        t = self.by_topic.setdefault(q.topic, [0, 0])
        t[0] += 1
        t[1] += int(ok)
        self.save()

    def reset(self) -> None:
        self.asked.clear()
        self.answered = self.correct = 0
        self.by_topic.clear()
        self.save()

    @property
    def percent(self) -> int:
        return round(100 * self.correct / self.answered) if self.answered else 0


def level_filter(setting: str) -> set[int]:
    return {int(c) for c in str(setting) if c in "123"} or {1, 2, 3}


def pick_question(bank: QuizBank, progress: QuizProgress, topic: str = "", levels: str = "123",
                  no_repeat: bool = True, rng: random.Random | None = None) -> tuple[Question | None, bool]:
    """A random question for the topic and levels. Returns (question, started_over).

    With no_repeat, questions already asked are skipped until every question in the selection has
    been asked; then the selection starts over (started_over = True).
    """
    rng = rng or random
    lv = level_filter(levels)
    pool = [q for q in bank.questions if (not topic or q.topic == topic) and q.level in lv]
    if not pool:
        return None, False
    started_over = False
    if no_repeat:
        fresh = [q for q in pool if q.id not in progress.asked]
        if not fresh:
            progress.asked -= {q.id for q in pool}
            progress.save()
            fresh, started_over = pool, True
        pool = fresh
    return rng.choice(pool), started_over


def shuffled_options(q: Question, shuffle: bool = True, rng: random.Random | None = None) -> tuple[list[str], int]:
    """Options in display order and the index of the right one."""
    order = list(range(len(q.options)))
    if shuffle:
        (rng or random).shuffle(order)
    return [q.options[i] for i in order], order.index(q.answer)


# ---------------------------------------------------------------- settings
class QuizSettings(SettingsSection):
    section_id = "quiz"
    name = "Astronomy quiz"
    description = ("Quizzes to learn about astronomy (Help › Astronomy quiz). Two quizzes are built in; you can also "
                   "make your own from a spreadsheet and they appear in the list below.")

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("quiz", "Quiz", CHOICE, DEFAULT_QUIZ,
                         choices=[(k, label) for k, label, _ in available_quizzes()],
                         help="Built-in quizzes first, then your own ('My quiz: …'). Each quiz keeps its own score."),
            SettingField("levels", "Questions", CHOICE, "123",
                         choices=[("123", "All levels"), ("1", "Beginner"), ("12", "Beginner and intermediate"),
                                  ("2", "Intermediate"), ("23", "Intermediate and advanced"), ("3", "Advanced")]),
            SettingField("no_repeat", "Don't repeat a question until all have been asked", BOOL, True),
            SettingField("shuffle", "Shuffle the answer options", BOOL, True),
            SettingField("explain", "Show an explanation after each answer", BOOL, True),
            SettingField("create", "Create a quiz from a spreadsheet…", ACTION, ui=True,
                         help="Write your own questions in Excel, Numbers or Google Sheets (one row per question) and "
                              "PlateSolver turns them into a quiz file. Help › Manual › Astronomy quiz explains how.",
                         action=_create_quiz_action),
            SettingField("folder", "Open the My quizzes folder", ACTION, ui=True,
                         help="Where your own quiz files are kept. Copy a .dat file here to add a quiz someone "
                              "shared with you.", action=_open_folder_action),
        ]

    def describe(self, values: dict) -> str:
        try:
            bank = load_bank(values.get("quiz") or DEFAULT_QUIZ)
            return f"{bank.title}: {len(bank.questions)} questions in {len(bank.topics)} topics" + \
                   (f" (edition {bank.edition})" if bank.edition else "")
        except Exception as exc:
            return str(exc)


def _create_quiz_action(values: dict, parent) -> str:
    from platesolver.ui.quiz_maker import QuizMakerDialog
    dlg = QuizMakerDialog(parent)
    dlg.exec()
    return dlg.result_message


def _open_folder_action(values: dict, parent) -> str:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    d = my_quizzes_dir()
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
    return str(d)


def _validate_file(value) -> tuple[bool, str]:
    if not value:
        return True, ""
    try:
        bank = QuizBank.load(Path(value))
        return True, f"{len(bank.questions)} questions"
    except Exception as exc:
        return False, str(exc)
