"""Blocks of a sheet that are not rows.

The case this exists for: a daily production report ends with

    AÇIKLAMALAR :
    *HAT 5 SABAH VARDİYASINDA BIÇAK ARIZASINDAN DOLAYI 2 KERE KAFAYA MAL SARDI...

merged across all nine columns and twenty rows. The old continuation rule asked
only "do these rows put something in the table's columns?", which a paragraph
merged across the sheet does trivially -- so twenty rows of prose were imported
as data and every column of them read as the same sentence. Information is not
preserved by being stored in the wrong shape.
"""

import pytest

from ingest.reader import RawSheet
from ingest.structure import classify_block, detect_tables

pytestmark = pytest.mark.django_db(transaction=True)


def sheet(grid) -> RawSheet:
    width = max(len(r) for r in grid)
    padded = [list(r) + [None] * (width - len(r)) for r in grid]
    return RawSheet(name="Sheet1", index=0, grid=padded)


TABLE = [
    ["Machine", "Product", "Total"],
    ["HAT-1", "3666-2", 6900],
    ["HAT-2", "349-50G", 7700],
    ["HAT-3", "249-50G", 5250],
]

# A merged paragraph arrives as the same string in every cell of the rectangle.
NOTE = "*HAT 5 SABAH VARDIYASINDA BICAK ARIZASINDAN DOLAYI 2 KERE KAFAYA MAL SARDI."


def test_a_merged_note_block_becomes_a_section_not_rows():
    grid = TABLE + [
        [None, None, None],
        ["AÇIKLAMALAR :", None, None],
        [NOTE, NOTE, NOTE],
        [NOTE, NOTE, NOTE],
    ]
    tables = detect_tables(sheet(grid))

    assert len(tables) == 1
    table = tables[0]
    assert table.last_data_row == 3, "the note rows must not be data"
    assert len(table.sections) == 1

    section = table.sections[0]
    assert section.kind == "note"
    assert section.title == "AÇIKLAMALAR"
    # The merge fill is collapsed back to the one paragraph that was written.
    assert section.body == NOTE
    assert section.source_range == "A6:C8"


def test_the_notes_do_not_leak_into_a_column():
    """The symptom that started this: a column of identical prose."""
    grid = TABLE + [[None] * 3, ["NOTLAR", None, None], [NOTE, NOTE, NOTE]]
    table = detect_tables(sheet(grid))[0]
    for column in table.columns:
        assert NOTE not in [str(v) for v in column.values]


def test_a_totals_block_becomes_a_summary_section():
    grid = TABLE + [
        [None, None, None],
        [None, None, "PVC"],
        [None, "TOPLAM (KG)", 17736],
        [None, "GENEL TOPLAM (KG)", 45326],
    ]
    table = detect_tables(sheet(grid))[0]
    kinds = {s.kind for s in table.sections}
    assert "summary" in kinds
    summary = next(s for s in table.sections if s.kind == "summary")
    assert "45326" in summary.body


def test_rows_of_values_are_never_turned_into_a_section():
    """The narrowing must not catch data.

    Rows separated from the table by a cosmetic blank row are values, whatever
    else the table logic then decides to do with them (absorb them, or read
    them as a second table -- both pre-date this and are tested elsewhere).
    What matters here is that they stay rows.
    """
    grid = TABLE + [
        [None, None, None],
        ["HAT-4", "258R2-50D", 4240],
        ["HAT-5", "504", 1290],
    ]
    tables = detect_tables(sheet(grid))
    assert all(t.sections == [] for t in tables)
    values = [str(v) for t in tables for c in t.columns for v in c.values]
    assert "HAT-5" in values, "the data must still be somewhere"


def test_an_unlabelled_paragraph_is_still_recognised():
    """Not every sheet writes AÇIKLAMALAR at the top of its notes."""
    grid = TABLE + [[None] * 3, [NOTE, NOTE, NOTE]]
    table = detect_tables(sheet(grid))[0]
    assert [s.kind for s in table.sections] == ["note"]
    assert table.sections[0].title == "Notes"


def test_a_short_value_row_is_not_mistaken_for_prose():
    grid = TABLE + [[None] * 3, ["HAT-9", "x", 1]]
    table = detect_tables(sheet(grid))[0]
    assert table.sections == []


def test_classify_leaves_ordinary_data_alone():
    grid = sheet(TABLE).grid
    assert classify_block(grid, 1, 3, range(3)) is None


def test_sections_are_stored_by_analysis_and_rebuilt_by_re_analysis(tmp_path):
    """End to end: detected, stored, and rebuilt from the file on re-analysis.

    Re-analysis discards a hand-written section, exactly as it discards a
    renamed field: `run_structure_and_types` deletes the workbook's entities
    and infers them again. Sections are not an exception to that, and the test
    says so rather than leaving it to be discovered.
    """
    from pathlib import Path

    from django.core.files import File
    from openpyxl import Workbook as XlsxWorkbook

    from core.models import Organization, Workbook
    from ingest.pipeline import run_structure_and_types

    book = XlsxWorkbook()
    ws = book.active
    ws.title = "Uretim"
    for row in TABLE:
        ws.append(row)
    ws.append([None, None, None])
    ws.append(["AÇIKLAMALAR :", None, None])
    ws.append([NOTE, NOTE, NOTE])
    path = tmp_path / "with_notes.xlsx"
    book.save(path)

    org = Organization.objects.create(name="Test Org")
    workbook = Workbook(organization=org, original_filename=path.name)
    with Path(path).open("rb") as fh:
        workbook.file.save(path.name, File(fh), save=True)

    run_structure_and_types(workbook, use_llm=False)
    entity = workbook.entities.get()
    assert [s.title for s in entity.sections.all()] == ["AÇIKLAMALAR"]

    from core.review import add_section

    hand = add_section(entity, "Hand written", "Keep me")
    assert hand.created_by_user, "provenance is what the review screen shows"

    run_structure_and_types(workbook, use_llm=False)
    entity = workbook.entities.get()
    titles = sorted(s.title for s in entity.sections.all())
    assert titles == ["AÇIKLAMALAR"], (
        "re-analysis rebuilds the proposal from the file, sections included"
    )
