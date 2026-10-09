# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Astronomy quiz: file format, the built-in questions, picking and progress, the build tool."""
import json
import random
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from platesolver.core.quiz import (BUILTIN_FILE, QuizBank, QuizProgress, Question, Topic, load_bank, pack,
                                   pick_question, shuffled_options, unpack)

ROOT = Path(__file__).resolve().parent.parent


def test_builtin_quiz_has_1000_valid_questions_and_pictures():
    bank = QuizBank.load(BUILTIN_FILE)
    assert len(bank.questions) == 1000 and len(bank.topics) == 10
    assert len({q.id for q in bank.questions}) == 1000
    for q in bank.questions:
        assert 3 <= len(q.options) <= 5 and 0 <= q.answer < len(q.options)
        assert q.level in (1, 2, 3) and q.explain
        assert len({o.lower() for o in q.options}) == len(q.options)
    for t in bank.topics:
        assert bank.count(t.id) == 100
        im = Image.open(__import__("io").BytesIO(t.image))
        assert im.height == 2 * im.width


def test_file_is_not_readable_and_detects_damage():
    data = BUILTIN_FILE.read_bytes()
    assert data[:4] == b"PSQZ"
    assert b"Messier" not in data and b"Andromeda" not in data       # answers not visible
    broken = bytearray(data)
    broken[500] ^= 0xFF
    with pytest.raises(ValueError):
        unpack(bytes(broken))
    with pytest.raises(ValueError):
        unpack(b"hello world, not a quiz")
    newer = data[:4] + struct.pack("<H", 99) + data[6:]
    with pytest.raises(ValueError, match="newer"):
        unpack(newer)


def small_bank():
    qs = [Question(f"id{i}", "a" if i < 4 else "b", f"Question {i}?", ["right", "wrong 1", "wrong 2"], 0,
                   "because", 1 + i % 3) for i in range(6)]
    return QuizBank("Test", "1", [Topic("a", "Topic A"), Topic("b", "Topic B")], qs)


def test_round_trip_and_replacement_file(tmp_path, monkeypatch):
    bank = small_bank()
    bank.save(tmp_path / "q.dat")
    again = QuizBank.load(tmp_path / "q.dat")
    assert [q.text for q in again.questions] == [q.text for q in bank.questions]
    # the chosen quiz (a key or a file path), falling back to the built-in one
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    assert len(load_bank("").questions) == 1000
    assert len(load_bank(str(tmp_path / "q.dat")).questions) == 6
    assert len(load_bank(str(BUILTIN_FILE)).questions) == 1000
    assert len(load_bank("user:missing.dat").questions) == 1000


def test_no_repeats_until_all_asked_then_start_over(tmp_path):
    bank, prog = small_bank(), QuizProgress(tmp_path / "p.json")
    rng = random.Random(1)
    seen = []
    for _ in range(4):
        q, over = pick_question(bank, prog, "a", "123", True, rng)
        assert not over and q.topic == "a"
        prog.record(q, True)
        seen.append(q.id)
    assert len(set(seen)) == 4
    q, over = pick_question(bank, prog, "a", "123", True, rng)
    assert over
    # levels filter
    q, _ = pick_question(bank, prog, "", "3", True, rng)
    assert q.level == 3
    assert pick_question(bank, prog, "zzz", "123", True, rng) == (None, False)
    # progress is saved
    p2 = QuizProgress(tmp_path / "p.json")
    assert p2.answered == 4 and p2.correct == 4 and p2.by_topic["a"] == [4, 4]


def test_shuffled_options_keep_the_right_answer():
    q = small_bank().questions[0]
    for seed in range(20):
        opts, right = shuffled_options(q, True, random.Random(seed))
        assert opts[right] == "right" and sorted(opts) == sorted(q.options)


def test_build_tool_extract_edit_build(tmp_path):
    tool = ROOT / "tools" / "build_quiz.py"
    out = tmp_path / "src"
    subprocess.run([sys.executable, str(tool), "extract", str(BUILTIN_FILE), str(out)], check=True)
    assert (out / "topics.json").exists() and (out / "stars.png").exists()
    qs = json.loads((out / "stars.json").read_text(encoding="utf-8"))
    qs.append({"q": "Which star is closest to the Sun?", "options": ["Proxima Centauri", "Sirius", "Vega"],
               "answer": 0, "explain": "Proxima Centauri is about 4.2 light-years away.", "level": 1})
    (out / "stars.json").write_text(json.dumps(qs), encoding="utf-8")
    r = subprocess.run([sys.executable, str(tool), "build", str(out), str(tmp_path / "new.dat"),
                        "--edition", "test"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    bank = QuizBank.load(tmp_path / "new.dat")
    assert len(bank.questions) == 1001 and bank.edition == "test"
    # a broken question is refused with a clear message
    qs.append({"q": "Bad?", "options": ["a", "b"], "answer": 5, "explain": "x", "level": 1})
    (out / "stars.json").write_text(json.dumps(qs), encoding="utf-8")
    r = subprocess.run([sys.executable, str(tool), "build", str(out), str(tmp_path / "bad.dat")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "stars.json #102" in r.stdout and not (tmp_path / "bad.dat").exists()


def test_quiz_dialog(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from platesolver.core.quiz import QuizSettings
    from platesolver.core.settings import SettingsStore
    from platesolver.ui.quiz_dialog import QuizDialog
    store = SettingsStore(tmp_path / "s.json")
    d = QuizDialog(store.section(QuizSettings()))
    assert d.question is not None and 3 <= len(d.buttons) <= 5
    assert d.picture.pixmap() is not None and not d.picture.pixmap().isNull()
    assert not d.next_btn.isEnabled()
    d.answer(d.correct_index)
    assert d.progress.correct == 1 and d.next_btn.isEnabled() and "Correct" in d.feedback.text()
    d.next_question()
    d.answer((d.correct_index + 1) % len(d.buttons))
    assert d.progress.answered == 2 and d.progress.correct == 1 and "Not quite" in d.feedback.text()
    d.topic_combo.setCurrentIndex(d.topic_combo.findData("history"))
    assert d.question.topic == "history"
    d.close()
    app.processEvents()



def test_quiz_library_and_progress_per_quiz(tmp_path, monkeypatch):
    from platesolver.core.quiz import (available_quizzes, chosen_quiz, my_quizzes_dir, progress_file)
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    keys = [k for k, _, _ in available_quizzes()]
    assert keys[:2] == ["builtin:quiz.dat", "builtin:quiz_astrophysics.dat"]
    astro = load_bank("builtin:quiz_astrophysics.dat")
    assert astro.title == "Astrophysics and cosmology" and len(astro.questions) == 1000 and len(astro.topics) == 10
    assert all(t.image for t in astro.topics)
    assert not {q.id for q in astro.questions} & {q.id for q in load_bank("builtin:quiz.dat").questions}
    small_bank().save(my_quizzes_dir() / "Club quiz.dat")
    lib = available_quizzes()
    assert lib[-1][0] == "user:Club quiz.dat" and lib[-1][1].startswith("My quiz:")
    assert len(load_bank("user:Club quiz.dat").questions) == 6
    assert progress_file("builtin:quiz.dat").name == "quiz_progress.json"
    assert progress_file("user:Club quiz.dat") != progress_file("builtin:quiz_astrophysics.dat")
    assert chosen_quiz({"quiz": "", "file": "C:/old.dat"}) == "file:C:/old.dat"
    assert chosen_quiz({}) == "builtin:quiz.dat"
