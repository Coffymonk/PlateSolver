# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Build or unpack PlateSolver's quiz file (quiz.dat).

    python tools/build_quiz.py extract platesolver/data/quiz.dat my_quiz
        Unpacks the quiz file into a folder of editable files:
          topics.json        list of topics: {"id", "name", "image"} (image = PNG file name)
          <topic id>.json    the questions of that topic
          <topic id>.png     the topic's picture (shown left of the question, twice as high as wide)

    python tools/build_quiz.py build my_quiz quiz.dat [--edition 2026-10]
        Checks every question and writes a new quiz file.

Question format (in each <topic id>.json, a list of):
    {"q": "Question?", "options": ["right", "wrong", "wrong"], "answer": 0,
     "explain": "Why the answer is right.", "level": 1}
  - 3 to 5 options, "answer" is the 0-based index of the right one (options are shuffled when shown)
  - level 1 = beginner, 2 = intermediate, 3 = advanced
  - avoid "all/none of the above", since the options are shuffled

To use a new quiz file, replace platesolver/data/quiz.dat with it, or copy it to the PlateSolver
settings folder (Tools › Open settings folder), or choose it in Settings › Astronomy quiz.
Progress is kept per question text, so unchanged questions keep their "already asked" status.
Keep the extracted folder private if you don't want the answers to be readable.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from platesolver.core.quiz import Question, QuizBank, Topic, question_id  # noqa: E402


def check(q: dict, where: str) -> list[str]:
    errs = []
    for k in ("q", "options", "answer", "explain", "level"):
        if k not in q:
            errs.append(f"{where}: missing '{k}'")
    if errs:
        return errs
    n = len(q["options"])
    if not 3 <= n <= 5:
        errs.append(f"{where}: needs 3 to 5 options (has {n})")
    if not isinstance(q["answer"], int) or not 0 <= q["answer"] < n:
        errs.append(f"{where}: 'answer' must be 0..{n - 1}")
    if q["level"] not in (1, 2, 3):
        errs.append(f"{where}: 'level' must be 1, 2 or 3")
    if len({o.strip().lower() for o in q["options"]}) != n:
        errs.append(f"{where}: two options are the same")
    low = " ".join(q["options"]).lower()
    if "all of the above" in low or "none of the above" in low:
        errs.append(f"{where}: 'of the above' options don't work when options are shuffled")
    return errs


def build(src: Path, out: Path, edition: str, title: str) -> int:
    topics_file = src / "topics.json"
    if not topics_file.exists():
        print(f"{topics_file} not found")
        return 1
    bank = QuizBank(title=title, edition=edition or datetime.date.today().isoformat())
    errors, seen = [], {}
    for t in json.loads(topics_file.read_text(encoding="utf-8")):
        img = src / t.get("image", f"{t['id']}.png")
        bank.topics.append(Topic(t["id"], t["name"], img.read_bytes() if img.is_file() else b""))
        qfile = src / f"{t['id']}.json"
        if not qfile.exists():
            errors.append(f"{qfile.name} not found")
            continue
        for i, q in enumerate(json.loads(qfile.read_text(encoding="utf-8"))):
            where = f"{qfile.name} #{i + 1}"
            e = check(q, where)
            if e:
                errors += e
                continue
            qid = question_id(q["q"])
            if qid in seen:
                errors.append(f"{where}: same question as {seen[qid]}")
                continue
            seen[qid] = where
            bank.questions.append(Question(qid, t["id"], q["q"].strip(), [o.strip() for o in q["options"]],
                                           q["answer"], q["explain"].strip(), q["level"]))
    if errors:
        print("Not built - please fix:\n  " + "\n  ".join(errors))
        return 1
    bank.save(out)
    print(f"Wrote {out}: {len(bank.questions)} questions in {len(bank.topics)} topics, edition {bank.edition}")
    return 0


def extract(dat: Path, dst: Path) -> int:
    bank = QuizBank.load(dat)
    dst.mkdir(parents=True, exist_ok=True)
    topics = []
    for t in bank.topics:
        if t.image:
            (dst / f"{t.id}.png").write_bytes(t.image)
        topics.append({"id": t.id, "name": t.name, "image": f"{t.id}.png"})
        qs = [{"q": q.text, "options": q.options, "answer": q.answer, "explain": q.explain, "level": q.level}
              for q in bank.questions if q.topic == t.id]
        (dst / f"{t.id}.json").write_text(json.dumps(qs, ensure_ascii=False, indent=1), encoding="utf-8")
    (dst / "topics.json").write_text(json.dumps(topics, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Extracted {len(bank.questions)} questions in {len(topics)} topics to {dst} (edition {bank.edition})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a quiz file from a folder")
    b.add_argument("source")
    b.add_argument("output")
    b.add_argument("--edition", default="")
    b.add_argument("--title", default="Astronomy quiz")
    e = sub.add_parser("extract", help="unpack a quiz file into a folder")
    e.add_argument("quizfile")
    e.add_argument("folder")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        return build(Path(a.source), Path(a.output), a.edition, a.title)
    return extract(Path(a.quizfile), Path(a.folder))


if __name__ == "__main__":
    sys.exit(main())
