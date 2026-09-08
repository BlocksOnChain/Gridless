"""Workbooks that openpyxl refuses for reasons unrelated to their data.

The case that prompted this: a real daily production report whose print-title
definition read `#N/A`. Excel opens the file; openpyxl raises ValueError while
parsing reserved defined names and takes the whole workbook with it, so upload
answered "this file is not a readable .xlsx workbook" about a file that was
perfectly readable. Rejecting it would be rejecting the premise of the product.

The damage is reproduced here from a known-good fixture rather than by
committing the customer's file, so the test is self-contained and the assertion
is about the mechanism, not about one workbook.
"""

import io
import shutil
import zipfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from ingest.reader import read_workbook
from ingest.repair import Unrepairable, strip_broken_defined_names

FIXTURE = Path("fixtures/synthetic/relational.xlsx")

# `localSheetId` matters: openpyxl assigns global names straight onto the
# workbook without parsing them, and only parses reserved *sheet-local* names --
# which is the code path that raises.
BROKEN_NAMES = (
    "<definedNames>"
    '<definedName name="_xlnm.Print_Titles" localSheetId="0">#N/A</definedName>'
    '<definedName name="_xlnm.Print_Titles">#N/A</definedName>'
    '<definedName name="leftover_range">#REF!</definedName>'
    "</definedNames>"
)


def damaged(tmp_path: Path) -> Path:
    """A copy of the fixture with broken defined names spliced in."""
    if not FIXTURE.exists():
        pytest.skip("run scripts/make_fixtures.py first")

    source = zipfile.ZipFile(FIXTURE)
    parts = {n: source.read(n) for n in source.namelist()}
    xml = parts["xl/workbook.xml"].decode("utf-8")
    # The fixture already carries an empty `<definedNames />`. Appending a
    # second element would be ignored by the parser -- and a test that damages
    # nothing proves nothing -- so replace the one that is there.
    if "<definedNames />" in xml:
        xml = xml.replace("<definedNames />", BROKEN_NAMES, 1)
    else:
        assert "</sheets>" in xml
        xml = xml.replace("</sheets>", f"</sheets>{BROKEN_NAMES}", 1)
    parts["xl/workbook.xml"] = xml.encode("utf-8")

    out = tmp_path / "damaged.xlsx"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return out


def test_the_damage_really_does_break_openpyxl(tmp_path):
    """Guards the test itself: if openpyxl ever fixes this, the repair is moot."""
    with pytest.raises(ValueError):
        load_workbook(damaged(tmp_path), data_only=True)


def test_a_workbook_with_broken_print_titles_is_read_anyway(tmp_path):
    path = damaged(tmp_path)
    sheets = read_workbook(path)

    assert sheets, "the workbook has sheets and they must come through"
    assert any(s.grid for s in sheets), "cell data must survive the repair"
    assert any(
        "broken defined name" in note for s in sheets for note in s.notes
    ), "the repair has to be recorded, not silent"


def test_the_uploaded_file_is_never_modified(tmp_path):
    """The repair happens on an in-memory copy: bytes on disk stay as uploaded."""
    path = damaged(tmp_path)
    before = path.read_bytes()
    read_workbook(path)
    assert path.read_bytes() == before


def test_a_clean_workbook_is_reported_as_needing_no_repair(tmp_path):
    if not FIXTURE.exists():
        pytest.skip("run scripts/make_fixtures.py first")
    clean = tmp_path / "clean.xlsx"
    shutil.copy(FIXTURE, clean)

    sheets = read_workbook(clean)
    assert not any(
        "broken defined name" in note for s in sheets for note in s.notes
    )


def test_repair_refuses_a_file_with_nothing_of_this_kind_wrong(tmp_path):
    """So the caller re-raises the real error instead of blaming defined names."""
    if not FIXTURE.exists():
        pytest.skip("run scripts/make_fixtures.py first")
    with pytest.raises(Unrepairable):
        strip_broken_defined_names(FIXTURE.read_bytes())


def test_repair_refuses_something_that_is_not_a_workbook():
    with pytest.raises(Unrepairable):
        strip_broken_defined_names(b"definitely not a workbook")


def test_repair_keeps_every_other_part_byte_for_byte(tmp_path):
    """Only xl/workbook.xml may differ; a rescue that corrupts a sheet is worse
    than a refusal."""
    path = damaged(tmp_path)
    original = path.read_bytes()
    repaired, removed = strip_broken_defined_names(original)
    assert removed == 3

    with zipfile.ZipFile(io.BytesIO(original)) as a, zipfile.ZipFile(io.BytesIO(repaired)) as b:
        assert a.namelist() == b.namelist()
        for name in a.namelist():
            if name == "xl/workbook.xml":
                continue
            assert a.read(name) == b.read(name), name
