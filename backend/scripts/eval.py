#!/usr/bin/env python
"""Measure the pipeline against hand-labelled workbooks.

This is the project's real success metric. It runs the same analysis code the
API runs (`ingest.analyse` + `inference.relationships`) over every workbook in
the fixtures directory that has a sibling `.expected.json`, and prints:

    column type accuracy   -- target > 0.80
    relationship recall    -- target > 0.70
    relationship precision -- reported so a recall win by over-proposing is visible

Run it with no arguments for the summary, with --verbose for every miss.

    uv run python scripts/eval.py               # deterministic only, no API calls
    uv run python scripts/eval.py --llm         # + stage 4b relationship ranking
    uv run python scripts/eval.py --verbose
    uv run python scripts/eval.py --fixtures path/to/real/workbooks

Without --llm nothing leaves the machine, so the deterministic baseline stays
fast and free to re-run on every change. With --llm the measured candidates are
additionally judged by the deep agent, which is what separates a real foreign key
from coincidental value overlap.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.analyse import AnalysedTable, analyse_workbook  # noqa: E402
from inference.keys import choose_primary_key  # noqa: E402
from inference.relationships import find_candidates  # noqa: E402

DEFAULT_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

TYPE_TARGET = 0.80
RECALL_TARGET = 0.70


@dataclass
class Tally:
    correct: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.correct += 1 if ok else 0

    @property
    def ratio(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def __str__(self) -> str:
        return f"{self.ratio:.2f}   ({self.correct}/{self.total})"


@dataclass
class FixtureReport:
    name: str
    entities: Tally = field(default_factory=Tally)
    types: Tally = field(default_factory=Tally)
    keys: Tally = field(default_factory=Tally)
    rel_recall: Tally = field(default_factory=Tally)
    rel_precision: Tally = field(default_factory=Tally)
    misses: list[str] = field(default_factory=list)


def _rel_key(d: dict) -> tuple[str, str, str, str]:
    return (d["from_sheet"], d["from_column"], d["to_sheet"], d["to_column"])


def evaluate(xlsx: Path, truth: dict, *, use_llm: bool = False) -> FixtureReport:
    report = FixtureReport(name=xlsx.stem)
    tables: list[AnalysedTable] = analyse_workbook(xlsx)
    by_name = {t.name: t for t in tables}

    # --- entities ---------------------------------------------------------
    for expected in truth["entities"]:
        sheet = expected["sheet"]
        found = sheet in by_name
        report.entities.add(found)
        if not found:
            report.misses.append(
                f"entity NOT FOUND: {sheet!r} "
                f"(detected: {', '.join(sorted(by_name)) or 'nothing'})"
            )

    for produced in tables:
        if produced.name not in {e["sheet"] for e in truth["entities"]}:
            report.misses.append(f"entity SPURIOUS: {produced.name!r} was not expected")

    # --- column types -----------------------------------------------------
    for expected in truth["entities"]:
        table = by_name.get(expected["sheet"])
        for header, want in expected["columns"].items():
            if table is None:
                report.types.add(False)
                continue
            column = table.column(header)
            if column is None:
                report.types.add(False)
                report.misses.append(
                    f"column MISSING: {expected['sheet']}.{header!r} "
                    f"(expected {want}; detected columns: "
                    f"{', '.join(c.header for c in table.columns)})"
                )
                continue
            ok = column.data_type == want
            report.types.add(ok)
            if not ok:
                report.misses.append(
                    f"type WRONG:    {expected['sheet']}.{header!r} "
                    f"expected {want}, got {column.data_type} "
                    f"(conf {column.confidence:.2f}) -- {column.note}"
                )

        # Primary key. `null` is a real expected answer, not a skip.
        if table is not None:
            want_pk = expected.get("primary_key")
            got = choose_primary_key(table.columns)
            got_pk = got.header if got else None
            ok = got_pk == want_pk
            report.keys.add(ok)
            if not ok:
                report.misses.append(
                    f"key WRONG:     {expected['sheet']} expected {want_pk!r}, got {got_pk!r}"
                )
        else:
            report.keys.add(False)

        if table is not None:
            extra = {c.header for c in table.columns} - set(expected["columns"])
            for header in sorted(extra):
                report.misses.append(
                    f"column EXTRA:  {expected['sheet']}.{header!r} was not expected"
                )

    # --- relationships ----------------------------------------------------
    expected_rels = {_rel_key(r) for r in truth.get("relationships", [])}
    hazards = {_rel_key(h): h.get("why", "") for h in truth.get("hazards", [])}
    candidates = find_candidates(tables)
    if use_llm and candidates:
        from inference.stages import judge_relationships

        judged, judge_warnings = judge_relationships(tables, candidates)
        for w in judge_warnings:
            report.misses.append(f"judge warning: {w}")
        candidates = [j.candidate for j in judged if j.accepted]

    proposed = {
        (c.from_table, c.from_column, c.to_table, c.to_column): c for c in candidates
    }

    for want in sorted(expected_rels):
        found = want in proposed
        report.rel_recall.add(found)
        if not found:
            report.misses.append(
                f"relationship MISSED: {want[0]}.{want[1]} -> {want[2]}.{want[3]}"
            )

    for key, candidate in sorted(proposed.items()):
        ok = key in expected_rels
        report.rel_precision.add(ok)
        if not ok:
            label = "KNOWN HAZARD" if key in hazards else "FALSE POSITIVE"
            why = f" -- {hazards[key]}" if key in hazards else ""
            report.misses.append(
                f"relationship {label}: {key[0]}.{key[1]} -> {key[2]}.{key[3]} "
                f"(match {candidate.match_rate:.2f}){why}"
            )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--verbose", "-v", action="store_true", help="list every miss")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="also run stage 4b (deep-agent relationship ranking). Costs API calls.",
    )
    args = parser.parse_args()

    if args.llm:
        import django

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gridless.settings")
        django.setup()

    expectations = sorted(args.fixtures.rglob("*.expected.json"))
    if not expectations:
        print(f"No .expected.json files under {args.fixtures}.")
        print("Generate the synthetic set with: uv run python scripts/make_fixtures.py")
        return 2

    reports: list[FixtureReport] = []
    for expected_path in expectations:
        xlsx = expected_path.with_name(expected_path.name.replace(".expected.json", ".xlsx"))
        if not xlsx.exists():
            print(f"skipping {expected_path.name}: no matching {xlsx.name}")
            continue
        reports.append(
            evaluate(xlsx, json.loads(expected_path.read_text()), use_llm=args.llm)
        )

    overall = FixtureReport(name="TOTAL")
    for r in reports:
        for attr in ("entities", "types", "keys", "rel_recall", "rel_precision"):
            src, dst = getattr(r, attr), getattr(overall, attr)
            dst.correct += src.correct
            dst.total += src.total

    width = max(len(r.name) for r in reports)
    print(f"\n{'fixture':<{width}}  {'entities':>16}  {'types':>16}  {'keys':>16}  "
          f"{'rel recall':>16}  {'rel prec':>16}")
    print("-" * (width + 92))
    for r in reports:
        print(f"{r.name:<{width}}  {str(r.entities):>16}  {str(r.types):>16}  "
              f"{str(r.keys):>16}  {str(r.rel_recall):>16}  {str(r.rel_precision):>16}")
    print("-" * (width + 92))
    print(f"{overall.name:<{width}}  {str(overall.entities):>16}  {str(overall.types):>16}  "
          f"{str(overall.keys):>16}  {str(overall.rel_recall):>16}  {str(overall.rel_precision):>16}")

    type_ok = overall.types.ratio > TYPE_TARGET
    recall_ok = overall.rel_recall.ratio > RECALL_TARGET
    print()
    print(f"column type accuracy   : {overall.types.ratio:.2f}   "
          f"target >{TYPE_TARGET:.2f}   {'PASS' if type_ok else 'FAIL'}")
    print(f"primary key accuracy   : {overall.keys.ratio:.2f}   "
          f"(no target; null is a valid expected answer)")
    print(f"relationship recall    : {overall.rel_recall.ratio:.2f}   "
          f"target >{RECALL_TARGET:.2f}   {'PASS' if recall_ok else 'FAIL'}")
    print(f"relationship precision : {overall.rel_precision.ratio:.2f}   "
          f"(no target; guards against buying recall with noise)")
    print(f"\nmode: {'deterministic + stage 4b (deep agent)' if args.llm else 'deterministic only (no API calls)'}")

    misses = [(r.name, m) for r in reports for m in r.misses]
    if misses:
        print(f"\n{len(misses)} finding(s):")
        shown = misses if args.verbose else misses[:15]
        for name, m in shown:
            print(f"  [{name}] {m}")
        if len(shown) < len(misses):
            print(f"  ... {len(misses) - len(shown)} more; re-run with --verbose")

    return 0 if (type_ok and recall_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
