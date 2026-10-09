# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Help › Astronomy quiz: one multiple-choice question at a time, with a topic picture on the left."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from platesolver.core.quiz import (LEVELS, QuizProgress, available_quizzes, chosen_quiz, load_bank, pick_question,
                                   progress_file, shuffled_options)
from platesolver.ui.theme import ACCENT, ERROR, MUTED, OK

PIC_W, PIC_H = 130, 260          # twice as high as wide

OPTION_STYLE = """
QPushButton {{ text-align: left; padding: 8px 12px; border: 1px solid #343b47; border-radius: 6px;
              background: #1f242c; color: #e6e9ef; }}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:disabled {{ color: #e6e9ef; }}
"""
RIGHT_STYLE = "QPushButton { text-align: left; padding: 8px 12px; border: 2px solid %s; border-radius: 6px; background: #1d3326; color: #e6e9ef; }" % OK
WRONG_STYLE = "QPushButton { text-align: left; padding: 8px 12px; border: 2px solid %s; border-radius: 6px; background: #3a1f22; color: #e6e9ef; }" % ERROR
FADED_STYLE = "QPushButton { text-align: left; padding: 8px 12px; border: 1px solid #2a303a; border-radius: 6px; background: #1a1e25; color: #6c7380; }"


class QuizDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Astronomy quiz")
        self.settings = settings
        self.quiz_key = chosen_quiz(settings)
        try:
            self.bank = load_bank(self.quiz_key)
        except FileNotFoundError:
            self.quiz_key = "builtin:quiz.dat"
            self.bank = load_bank(self.quiz_key)
        self.progress = QuizProgress(progress_file(self.quiz_key))
        self.question = None
        self.correct_index = -1
        self.buttons: list[QPushButton] = []

        self.picture = QLabel()
        self.picture.setFixedSize(PIC_W, PIC_H)
        self.picture.setAlignment(Qt.AlignTop)
        self.picture.setStyleSheet("border-radius: 6px;")

        self.topic_label = QLabel()
        self.topic_label.setStyleSheet(f"color: {MUTED};")
        self.score_label = QLabel()
        self.score_label.setStyleSheet(f"color: {MUTED};")
        self.question_label = QLabel()
        self.question_label.setWordWrap(True)
        self.question_label.setStyleSheet("font-size: 12pt; font-weight: 600;")
        self.question_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.options_box = QVBoxLayout()
        self.options_box.setSpacing(6)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.RichText)
        self.feedback.setMinimumHeight(48)
        self.feedback.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.quiz_combo = QComboBox()
        self._fill_quizzes()
        self.quiz_combo.currentIndexChanged.connect(self._quiz_changed)
        make_btn = QPushButton("Create a quiz…")
        make_btn.setToolTip("Make your own quiz from a spreadsheet")
        make_btn.clicked.connect(self._make_quiz)
        self.topic_combo = QComboBox()
        self._fill_topics()
        self.topic_combo.currentIndexChanged.connect(self._topic_changed)
        self.next_btn = QPushButton("Next question")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self.next_question)
        reset_btn = QPushButton("Reset score")
        reset_btn.clicked.connect(self._reset)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        head = QHBoxLayout()
        head.addWidget(self.topic_label, 1)
        head.addWidget(self.score_label)
        right = QVBoxLayout()
        right.addLayout(head)
        right.addWidget(self.question_label)
        right.addSpacing(4)
        right.addLayout(self.options_box)
        right.addWidget(self.feedback)
        right.addStretch(1)
        body = QHBoxLayout()
        body.addWidget(self.picture, 0, Qt.AlignTop)
        body.addSpacing(10)
        body.addLayout(right, 1)
        foot = QHBoxLayout()
        foot.addWidget(QLabel("Topic:"))
        foot.addWidget(self.topic_combo, 1)
        foot.addSpacing(12)
        foot.addWidget(reset_btn)
        foot.addWidget(self.next_btn)
        foot.addWidget(close_btn)
        top = QHBoxLayout()
        top.addWidget(QLabel("Quiz:"))
        top.addWidget(self.quiz_combo, 1)
        top.addWidget(make_btn)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(body, 1)
        lay.addLayout(foot)
        self.resize(800, 420)
        self.setMinimumWidth(720)
        self._update_score()
        self.next_question()

    # ------------------------------------------------------------------
    def _fill_quizzes(self):
        self.quiz_combo.blockSignals(True)
        self.quiz_combo.clear()
        for key, label, _ in available_quizzes():
            self.quiz_combo.addItem(label, key)
        i = self.quiz_combo.findData(self.quiz_key)
        if i < 0:
            self.quiz_combo.addItem(self.bank.title, self.quiz_key)
            i = self.quiz_combo.count() - 1
        self.quiz_combo.setCurrentIndex(i)
        self.quiz_combo.blockSignals(False)

    def _fill_topics(self):
        self.topic_combo.blockSignals(True)
        self.topic_combo.clear()
        self.topic_combo.addItem(f"All topics ({len(self.bank.questions)})", "")
        for t in self.bank.topics:
            self.topic_combo.addItem(f"{t.name} ({self.bank.count(t.id)})", t.id)
        i = self.topic_combo.findData(self.progress.topic)
        self.topic_combo.setCurrentIndex(max(i, 0))
        self.topic_combo.blockSignals(False)

    def _quiz_changed(self):
        key = self.quiz_combo.currentData()
        try:
            bank = load_bank(key)
        except Exception as exc:
            QMessageBox.warning(self, "Quiz", f"This quiz can't be opened: {exc}")
            self._fill_quizzes()
            return
        self.quiz_key, self.bank = key, bank
        self.settings.set("quiz", key)
        self.progress = QuizProgress(progress_file(key))
        self._fill_topics()
        self._update_score()
        self.next_question()

    def _make_quiz(self):
        from platesolver.ui.quiz_maker import QuizMakerDialog
        dlg = QuizMakerDialog(self)
        dlg.exec()
        if dlg.created_key:
            self.quiz_key = dlg.created_key
            self._fill_quizzes()
            self._quiz_changed()

    def _topic_changed(self):
        self.progress.topic = self.topic_combo.currentData() or ""
        self.progress.save()
        self.next_question()

    def _reset(self):
        if QMessageBox.question(self, "Reset score", "Clear your score and start asking all questions again?") \
                == QMessageBox.Yes:
            self.progress.reset()
            self._update_score()

    def _update_score(self):
        p = self.progress
        self.score_label.setText(f"Score {p.correct} / {p.answered}" + (f"  ({p.percent} %)" if p.answered else ""))

    def _set_picture(self, topic_id: str):
        t = self.bank.topic(topic_id)
        pm = QPixmap()
        if t and t.image:
            pm.loadFromData(t.image)
        if pm.isNull():
            self.picture.clear()
            self.picture.setStyleSheet("background: #1b1f26; border-radius: 6px;")
            return
        dpr = self.devicePixelRatioF()
        pm = pm.scaled(int(PIC_W * dpr), int(PIC_H * dpr), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self.picture.setPixmap(pm)

    def next_question(self):
        topic = self.topic_combo.currentData() or ""
        q, started_over = pick_question(self.bank, self.progress, topic, self.settings.get("levels") or "123",
                                        bool(self.settings.get("no_repeat")))
        for b in self.buttons:
            self.options_box.removeWidget(b)
            b.hide()
            b.deleteLater()
        self.buttons = []
        self.feedback.clear()
        if q is None:
            self.question = None
            self.question_label.setText("No questions match the chosen topic and level "
                                        "(Settings › Astronomy quiz).")
            return
        self.question = q
        t = self.bank.topic(q.topic)
        self.topic_label.setText(f"{t.name if t else q.topic}  ·  {LEVELS.get(q.level, '')}")
        self._set_picture(q.topic)
        self.question_label.setText(q.text)
        options, self.correct_index = shuffled_options(q, bool(self.settings.get("shuffle")))
        for i, text in enumerate(options):
            b = QPushButton(text.replace("&", "&&"))
            b.setToolTip(text)
            b.setStyleSheet(OPTION_STYLE.format(accent=ACCENT))
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.answer(i))
            self.options_box.addWidget(b)
            self.buttons.append(b)
        if started_over:
            self.feedback.setText(f"<span style='color:{MUTED}'>You have had every question in this "
                                  "selection, so they start over.</span>")
        self.next_btn.setEnabled(False)

    def answer(self, index: int):
        if self.question is None or not self.buttons[0].isEnabled():
            return
        ok = index == self.correct_index
        self.progress.record(self.question, ok)
        for i, b in enumerate(self.buttons):
            b.setEnabled(False)
            b.setCursor(Qt.ArrowCursor)
            b.setStyleSheet(RIGHT_STYLE if i == self.correct_index else WRONG_STYLE if i == index else FADED_STYLE)
        verdict = (f"<b style='color:{OK}'>Correct!</b>" if ok else
                   f"<b style='color:{ERROR}'>Not quite.</b> The answer is "
                   f"<b>{html.escape(self.question.correct_text)}</b>.")
        text = verdict
        if self.settings.get("explain") and self.question.explain:
            text += f"<br>{html.escape(self.question.explain)}"
        self.feedback.setText(text)
        self._update_score()
        self.next_btn.setEnabled(True)
        self.next_btn.setFocus()

    def keyPressEvent(self, event):
        k = event.key()
        if Qt.Key_1 <= k <= Qt.Key_5 and self.buttons and self.buttons[0].isEnabled():
            i = k - Qt.Key_1
            if i < len(self.buttons):
                self.answer(i)
                return
        super().keyPressEvent(event)
