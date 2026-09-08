"""Recover workbooks openpyxl refuses to open for reasons unrelated to the data.

A real production report -- `Merit Plastik günlük üretim raporu 26.08.2026.xlsx`
-- was rejected at upload with "this file is not a readable .xlsx workbook".
It was a perfectly valid OOXML file. What it also had, after years of
copy-paste, was sixty-odd dead defined names pointing at `#REF!` and, fatally,
two print-title definitions reading `#N/A`:

    <definedName name="_xlnm.Print_Titles" localSheetId="0">#N/A</definedName>

openpyxl parses reserved names eagerly while opening the workbook. `Print_Area`
is wrapped in a try/except and merely warns; `Print_Titles` is not, so a broken
print setting -- a *page layout* leftover, nothing to do with the cells --
raises ValueError and takes the whole file with it.

Refusing that file would be refusing the premise of this product. Every workbook
we exist to ingest has this kind of scar tissue on it, and "convert it in Excel
first" is precisely the instruction the people we are building for cannot act
on.

So: leave the uploaded bytes untouched on disk, and when a read fails, strip the
unusable definitions from an in-memory copy and read that. The repair is
attempted only after a genuine failure, so a healthy workbook pays nothing for
it, and it is deliberately narrow -- one element type, only when its value is an
Excel error literal. Anything else still fails loudly, because a file we cannot
read for a reason we do not understand must not be quietly half-imported.
"""

from __future__ import annotations

import io
import re
import zipfile

WORKBOOK_PART = "xl/workbook.xml"

#: The literals Excel writes into a definition whose target is gone. A name
#: pointing at one of these carries no information by construction: there is
#: nothing to preserve by keeping it.
ERROR_LITERALS = ("#N/A", "#REF!", "#VALUE!", "#NAME?", "#DIV/0!", "#NULL!", "#NUM!")

# Matched textually rather than by parsing and re-serialising the XML: a
# round-trip through ElementTree rewrites namespace prefixes across the whole
# part (mc, x14, xr...), which risks breaking a file we were trying to rescue.
# Cutting out whole elements leaves every other byte exactly as Excel wrote it.
_DEFINED_NAME_RE = re.compile(
    r"<definedName\b[^>]*>\s*(?:"
    + "|".join(re.escape(literal) for literal in ERROR_LITERALS)
    + r")\s*</definedName>",
    re.IGNORECASE,
)

_EMPTY_CONTAINER_RE = re.compile(r"<definedNames>\s*</definedNames>")


class Unrepairable(Exception):
    """The repair pass found nothing it knows how to fix."""


def strip_broken_defined_names(data: bytes) -> tuple[bytes, int]:
    """Return (repaired workbook bytes, how many definitions were dropped).

    Raises `Unrepairable` when there is nothing of this kind to remove, so the
    caller re-raises the original error rather than reporting a failed repair of
    a problem that was never there.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if WORKBOOK_PART not in archive.namelist():
                raise Unrepairable(f"No {WORKBOOK_PART} in the archive.")
            parts = {name: archive.read(name) for name in archive.namelist()}
            infos = {info.filename: info for info in archive.infolist()}
    except zipfile.BadZipFile as exc:
        raise Unrepairable(f"Not a zip archive: {exc}")

    xml = parts[WORKBOOK_PART].decode("utf-8", errors="replace")
    cleaned, removed = _DEFINED_NAME_RE.subn("", xml)
    if not removed:
        raise Unrepairable("No defined names point at an Excel error value.")
    # An empty <definedNames/> container is legal but pointless; Excel omits it.
    cleaned = _EMPTY_CONTAINER_RE.sub("", cleaned)
    parts[WORKBOOK_PART] = cleaned.encode("utf-8")

    out = io.BytesIO()
    # Rebuilt in the original order, and every other part copied verbatim.
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            info = infos[name]
            archive.writestr(
                zipfile.ZipInfo(filename=name, date_time=info.date_time), payload
            )
    return out.getvalue(), removed
