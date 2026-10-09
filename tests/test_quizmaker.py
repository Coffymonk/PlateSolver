# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Making a quiz from a spreadsheet."""
import csv
import os
from pathlib import Path

import pytest
from PIL import Image

from platesolver.core import quizmaker
from platesolver.core.quiz import QuizBank, load_bank


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))


def write_csv(path, rows, delim=";", encoding="utf-8-sig"):
    with open(path, "w", newline="", encoding=encoding) as fh:
        csv.writer(fh, delimiter=delim).writerows(rows)


def test_template_reads_back_as_a_valid_quiz(tmp_path):
    for name in ("t.xlsx", "t.csv"):
        path = quizmaker.write_template(tmp_path / name)
        draft = quizmaker.read_spreadsheet(path, "Examples")
        assert draft.ok, draft.errors
        assert draft.summary() == "3 questions in 2 topics" and [t.name for t in draft.bank.topics] == ["The Moon",
                                                                                                         "My telescope"]


def test_rows_are_checked_with_plain_messages(tmp_path):
    rows = [quizmaker.COLUMNS,
            ["Moon", "1", "Good question?", "Right", "Wrong A", "Wrong B", "", "", "Why.", ""],
            ["Moon", "4", "Bad level?", "Right", "W1", "W2", "", "", "", ""],
            ["Moon", "", "Too few wrong answers?", "Right", "Only one", "", "", "", "", ""],
            ["", "", "", "", "", "", "", "", "", ""],
            ["Moon", "2", "", "Right", "W1", "W2", "", "", "", ""],
            ["Moon", "2", "Same answers?", "Yes", "yes", "No", "", "", "", ""]]
    write_csv(tmp_path / "q.csv", rows)
    d = quizmaker.read_spreadsheet(tmp_path / "q.csv")
    assert not d.ok and len(d.bank.questions) == 1
    text = " ".join(d.errors)
    assert "Row 3: level must be 1, 2 or 3" in text
    assert "Row 4: needs at least two wrong answers (has 1)" in text
    assert "Row 6: the question is empty" in text
    assert "Row 7: two answers are the same" in text


def test_comma_csv_in_windows_encoding_with_picture_and_warnings(tmp_path):
    Image.new("RGB", (300, 200), (200, 50, 50)).save(tmp_path / "moon.jpg")
    rows = [["Question", "Correct answer", "Wrong 1", "Wrong 2", "Topic", "Picture"],
            ["Vad är Månen?", "En måne", "En planet", "All of the above", "Månen", "moon.jpg"],
            ["Vad är Månen?", "En måne", "En planet", "En stjärna", "Månen", ""],
            ["Hur långt bort?", "384 000 km", "1 km", "1 ljusår", "Månen", "missing.png"]]
    write_csv(tmp_path / "sv.csv", rows, delim=",", encoding="cp1252")
    d = quizmaker.read_spreadsheet(tmp_path / "sv.csv", "Månquiz")
    assert d.ok and d.summary() == "2 questions in 1 topic"
    assert any("all of the above" in w for w in d.warnings)
    assert any("same question appears earlier" in w for w in d.warnings)
    assert d.bank.questions[0].text == "Vad är Månen?" and d.bank.questions[0].level == 1
    img = d.bank.topics[0].image
    from io import BytesIO
    with Image.open(BytesIO(img)) as im:
        assert im.size == (240, 480) and im.getpixel((120, 240))[0] > 150     # the user's red picture


def test_missing_columns_and_save(tmp_path):
    write_csv(tmp_path / "bad.csv", [["Fråga", "Svar"], ["a", "b"]])
    d = quizmaker.read_spreadsheet(tmp_path / "bad.csv")
    assert not d.ok and "heading row" in d.errors[0]
    path = quizmaker.write_template(tmp_path / "t.xlsx")
    d = quizmaker.read_spreadsheet(path)
    out = quizmaker.save_quiz(d, "My: club/quiz?")
    assert out.name == "My clubquiz.dat" and out.parent.name == "My quizzes"
    bank = QuizBank.load(out)
    assert bank.title == "My: club/quiz?" and len(bank.questions) == 3
    assert b"Moon" not in out.read_bytes()                       # stored unreadable, like the built-in quizzes
    assert len(load_bank("user:My clubquiz.dat").questions) == 3


def test_auto_picture_differs_per_topic():
    a, b = quizmaker.auto_picture("Stars"), quizmaker.auto_picture("Planets")
    assert a.size == (240, 480) and list(a.getdata())[:500] != list(b.getdata())[:500]


def test_maker_dialog(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from platesolver.ui.quiz_maker import QuizMakerDialog
    app = QApplication.instance() or QApplication([])
    dlg = QuizMakerDialog()
    path = quizmaker.write_template(tmp_path / "Club quiz.xlsx")
    dlg.load(path)
    assert dlg.create_btn.isEnabled() and dlg.name.text() == "Club quiz"
    dlg._create()
    assert dlg.created_key == "user:Club quiz.dat" and "Created" in dlg.result_message
