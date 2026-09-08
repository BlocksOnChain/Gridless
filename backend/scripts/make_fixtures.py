#!/usr/bin/env python
"""Generate messy .xlsx fixtures with hand-known ground truth.

Each fixture isolates one real-world failure mode; `nightmare.xlsx` combines
them. Every workbook is written alongside a `<name>.expected.json` describing
the schema a competent human would infer from it.

Real workbooks drop into the same directory with a hand-written .expected.json
in this identical format -- no code change required. That is the whole point of
keeping the expectation file separate from the generator.

Ground-truth format
-------------------
{
  "entities": [
    {
      "sheet": "<Sheet name, or 'Sheet (2)' for the 2nd table on a sheet>",
      "columns": {"<exact header text>": "<data_type>", ...},
      "primary_key": "<header text>" | null
    }
  ],
  "relationships": [
    {"from_sheet": "...", "from_column": "...",
     "to_sheet": "...",   "to_column": "..."}
  ],
  "hazards": [            # optional: detectable by overlap, but WRONG.
    {"from_sheet": "...", "from_column": "...",
     "to_sheet": "...",   "to_column": "...", "why": "..."}
  ]
}
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic"

FIRST = ["Ada", "Bora", "Cem", "Deniz", "Ece", "Firat", "Gul", "Hakan", "Irem", "Jale",
         "Kaan", "Lale", "Mert", "Nil", "Onur", "Pelin", "Rana", "Sinan", "Tuna", "Umut"]
LAST = ["Aydin", "Baris", "Celik", "Demir", "Erdem", "Fidan", "Gunes", "Hoca",
        "Ilhan", "Kaya", "Kurt", "Ozturk", "Sahin", "Tekin", "Yildiz", "Yilmaz"]
CITIES = ["Istanbul", "Ankara", "Izmir", "Bursa", "Antalya", "Adana"]
STATUSES = ["active", "lapsed", "pending"]
PRODUCTS = ["Motor", "Home", "Travel", "Health"]


def rng() -> random.Random:
    # Fixed seed: fixtures must be byte-stable so eval numbers are comparable
    # between runs and between machines.
    return random.Random(20260905)


def write_rows(ws: Worksheet, rows: list[list], start_row: int = 1, start_col: int = 1) -> None:
    for r, row in enumerate(rows, start=start_row):
        for c, value in enumerate(row, start=start_col):
            if value is not None:
                ws.cell(row=r, column=c, value=value)


def customers(n: int, r: random.Random) -> tuple[list[str], list[list]]:
    header = ["Customer ID", "Full Name", "City", "Joined", "Active", "Lifetime Value"]
    rows = []
    for i in range(1, n + 1):
        rows.append([
            1000 + i,
            f"{r.choice(FIRST)} {r.choice(LAST)}",
            r.choice(CITIES),
            date(2019, 1, 1) + timedelta(days=r.randint(0, 2200)),
            r.choice(["Yes", "No"]),
            round(r.uniform(500, 90000), 2),
        ])
    return header, rows


def customers_truth(sheet: str) -> dict:
    return {
        "sheet": sheet,
        "columns": {
            "Customer ID": "integer",
            "Full Name": "text",
            "City": "text",
            "Joined": "date",
            "Active": "boolean",
            "Lifetime Value": "numeric",
        },
        "primary_key": "Customer ID",
    }


def policies(n: int, customer_ids: list[int], r: random.Random) -> tuple[list[str], list[list]]:
    header = ["Policy No", "Customer ID", "Product", "Premium", "Start Date", "Expires", "Status"]
    rows = []
    for i in range(1, n + 1):
        start = date(2023, 1, 1) + timedelta(days=r.randint(0, 700))
        rows.append([
            f"POL-{5000 + i}",
            r.choice(customer_ids),
            r.choice(PRODUCTS),
            round(r.uniform(120, 8400), 2),
            start,
            start + timedelta(days=365),
            r.choice(STATUSES),
        ])
    return header, rows


def policies_truth(sheet: str) -> dict:
    return {
        "sheet": sheet,
        "columns": {
            "Policy No": "text",
            "Customer ID": "integer",
            "Product": "text",
            "Premium": "numeric",
            "Start Date": "date",
            "Expires": "date",
            "Status": "text",
        },
        "primary_key": "Policy No",
    }


def claims(n: int, policy_nos: list[str], r: random.Random) -> tuple[list[str], list[list]]:
    header = ["Claim Ref", "Policy No", "Reported At", "Amount", "Settled"]
    rows = []
    for i in range(1, n + 1):
        rows.append([
            f"CLM-{9000 + i}",
            r.choice(policy_nos),
            datetime(2024, 1, 1, 9, 0) + timedelta(hours=r.randint(0, 9000)),
            round(r.uniform(50, 30000), 2),
            r.choice(["Yes", "No"]),
        ])
    return header, rows


def claims_truth(sheet: str) -> dict:
    return {
        "sheet": sheet,
        "columns": {
            "Claim Ref": "text",
            "Policy No": "text",
            "Reported At": "datetime",
            "Amount": "numeric",
            "Settled": "boolean",
        },
        "primary_key": "Claim Ref",
    }


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def fx_clean(r):
    """Baseline: header on row 1, nothing hostile."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Customers"
    header, rows = customers(60, r)
    write_rows(ws, [header] + rows)
    return wb, {"entities": [customers_truth("Customers")], "relationships": []}


def fx_offset_header(r):
    """Title block above the real header -- the single most common real defect."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Customers"
    header, rows = customers(60, r)
    preamble = [
        ["ACME Insurance — Customer Master"],
        ["Exported 4 September 2026 by M. Yilmaz"],
        ["CONFIDENTIAL — internal use only"],
        [],
    ]
    write_rows(ws, preamble + [header] + rows)
    return wb, {"entities": [customers_truth("Customers")], "relationships": []}


def fx_merged_cells(r):
    """Region label merged down a block of rows: reads as nulls without a fill."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Portfolio"
    header = ["Region", "Customer ID", "Full Name", "Premium"]
    body = []
    regions = [("West", 12), ("Central", 14), ("East", 10)]
    cid = 1000
    for region, count in regions:
        for _ in range(count):
            cid += 1
            body.append([region, cid, f"{r.choice(FIRST)} {r.choice(LAST)}",
                         round(r.uniform(100, 5000), 2)])
    write_rows(ws, [header] + body)

    row = 2
    for _, count in regions:
        ws.merge_cells(start_row=row, start_column=1, end_row=row + count - 1, end_column=1)
        row += count

    return wb, {
        "entities": [{
            "sheet": "Portfolio",
            "columns": {
                "Region": "text",
                "Customer ID": "integer",
                "Full Name": "text",
                "Premium": "numeric",
            },
            "primary_key": "Customer ID",
        }],
        "relationships": [],
    }


def fx_two_tables(r):
    """Two unrelated tables stacked on one sheet, separated by a blank row."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Reference"
    products = [["Product Code", "Product Name", "Base Rate"]] + [
        [f"P{i:03d}", name, round(r.uniform(0.5, 4.5), 3)]
        for i, name in enumerate(PRODUCTS, start=1)
    ]
    branches = [["Branch Code", "Branch City", "Head Count", "Opened"]] + [
        [f"B{i:02d}", city, r.randint(3, 40), date(2015 + i, 3, 1)]
        for i, city in enumerate(CITIES, start=1)
    ]
    write_rows(ws, products + [[], []] + branches)
    return wb, {
        "entities": [
            {"sheet": "Reference",
             "columns": {"Product Code": "text", "Product Name": "text", "Base Rate": "numeric"},
             "primary_key": "Product Code"},
            {"sheet": "Reference (2)",
             "columns": {"Branch Code": "text", "Branch City": "text",
                         "Head Count": "integer", "Opened": "date"},
             "primary_key": "Branch Code"},
        ],
        "relationships": [],
    }


def fx_junk_columns(r):
    """Empty spacer columns, a free-text notes column, and a totals row."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Premiums"
    header = ["Policy No", None, "Customer ID", "Premium", "Notes", None]
    rows = []
    total = 0.0
    for i in range(1, 41):
        premium = round(r.uniform(120, 8400), 2)
        total += premium
        rows.append([
            f"POL-{5000 + i}", None, 1000 + r.randint(1, 40), premium,
            r.choice(["", "chased by phone", "renewal pending", "see email 12/03"]), None,
        ])
    rows.append(["TOTAL", None, None, round(total, 2), None, None])
    write_rows(ws, [header] + rows)
    return wb, {
        "entities": [{
            "sheet": "Premiums",
            "columns": {
                "Policy No": "text",
                "Customer ID": "integer",
                "Premium": "numeric",
                "Notes": "text",
            },
            "primary_key": "Policy No",
        }],
        "relationships": [],
    }


def fx_relational(r):
    """Three related sheets, plus a decoy: Status overlaps across sheets but is
    NOT a foreign key. Proposing it would be a precision failure."""
    wb = Workbook()
    cust_header, cust_rows = customers(50, r)
    customer_ids = [row[0] for row in cust_rows]
    pol_header, pol_rows = policies(90, customer_ids, r)
    policy_nos = [row[0] for row in pol_rows]
    clm_header, clm_rows = claims(70, policy_nos, r)

    ws = wb.active
    ws.title = "Customers"
    write_rows(ws, [cust_header] + cust_rows)

    ws2 = wb.create_sheet("Policies")
    write_rows(ws2, [pol_header] + pol_rows)

    ws3 = wb.create_sheet("Claims")
    write_rows(ws3, [clm_header] + clm_rows)

    # The decoy: a second sheet whose Status column shares every value with
    # Policies.Status. High overlap, but the target is not unique, so the
    # uniqueness requirement must reject it.
    ws4 = wb.create_sheet("Status Codes")
    write_rows(ws4, [["Status", "Meaning"]] + [[s, s.title()] for s in STATUSES])

    return wb, {
        "entities": [
            customers_truth("Customers"),
            policies_truth("Policies"),
            claims_truth("Claims"),
            {"sheet": "Status Codes",
             "columns": {"Status": "text", "Meaning": "text"},
             "primary_key": "Status"},
        ],
        "relationships": [
            {"from_sheet": "Policies", "from_column": "Customer ID",
             "to_sheet": "Customers", "to_column": "Customer ID"},
            {"from_sheet": "Claims", "from_column": "Policy No",
             "to_sheet": "Policies", "to_column": "Policy No"},
            {"from_sheet": "Policies", "from_column": "Status",
             "to_sheet": "Status Codes", "to_column": "Status"},
        ],
        "hazards": [
            {"from_sheet": "Policies", "from_column": "Status",
             "to_sheet": "Status Codes", "to_column": "Meaning",
             "why": "Meaning is unique and case-insensitively equal to Status, so "
                    "value overlap alone cannot separate it from the real key."},
        ],
    }


def fx_nightmare(r):
    """Everything at once: offset headers, merges, two tables on a sheet, junk
    columns, a totals row, and cross-sheet foreign keys."""
    wb = Workbook()
    cust_header, cust_rows = customers(55, r)
    customer_ids = [row[0] for row in cust_rows]
    pol_header, pol_rows = policies(80, customer_ids, r)

    ws = wb.active
    ws.title = "Customers"
    write_rows(ws, [
        ["ACME Insurance"], ["Customer extract"], [],
        cust_header, *cust_rows,
    ])

    ws2 = wb.create_sheet("Policies")
    junk_header = [None, "Policy No", "Customer ID", "Product", "Premium", "Start Date",
                   "Expires", "Status", "Internal Notes"]
    junk_rows = []
    total = 0.0
    for row in pol_rows:
        total += row[3]
        junk_rows.append([None, *row, r.choice(["", "flagged", "ok"])])
    junk_rows.append([None, "Grand Total", None, None, round(total, 2), None, None, None, None])
    write_rows(ws2, [["Policy register — do not edit"], [], junk_header, *junk_rows])

    ws3 = wb.create_sheet("Reference")
    products = [["Product Code", "Product Name", "Base Rate"]] + [
        [f"P{i:03d}", name, round(r.uniform(0.5, 4.5), 3)]
        for i, name in enumerate(PRODUCTS, start=1)
    ]
    branches = [["Branch Code", "Branch City", "Head Count"]] + [
        [f"B{i:02d}", city, r.randint(3, 40)] for i, city in enumerate(CITIES, start=1)
    ]
    write_rows(ws3, products + [[], []] + branches)

    ws4 = wb.create_sheet("By Region")
    header = ["Region", "Customer ID", "Premium"]
    body = []
    for region, count in (("West", 18), ("Central", 20), ("East", 17)):
        for _ in range(count):
            # Repeats on purpose: this is the many side of a many-to-one.
            body.append([region, r.choice(customer_ids),
                         round(r.uniform(100, 5000), 2)])
    write_rows(ws4, [header] + body)
    row = 2
    for _, count in (("West", 18), ("Central", 20), ("East", 17)):
        ws4.merge_cells(start_row=row, start_column=1, end_row=row + count - 1, end_column=1)
        row += count

    return wb, {
        "entities": [
            customers_truth("Customers"),
            policies_truth("Policies") | {"columns": policies_truth("Policies")["columns"] |
                                          {"Internal Notes": "text"}},
            {"sheet": "Reference",
             "columns": {"Product Code": "text", "Product Name": "text", "Base Rate": "numeric"},
             "primary_key": "Product Code"},
            {"sheet": "Reference (2)",
             "columns": {"Branch Code": "text", "Branch City": "text", "Head Count": "integer"},
             "primary_key": "Branch Code"},
            {"sheet": "By Region",
             "columns": {"Region": "text", "Customer ID": "integer", "Premium": "numeric"},
             "primary_key": None},
        ],
        "relationships": [
            {"from_sheet": "Policies", "from_column": "Customer ID",
             "to_sheet": "Customers", "to_column": "Customer ID"},
            {"from_sheet": "By Region", "from_column": "Customer ID",
             "to_sheet": "Customers", "to_column": "Customer ID"},
            {"from_sheet": "Policies", "from_column": "Product",
             "to_sheet": "Reference", "to_column": "Product Name"},
        ],
        "hazards": [
            {"from_sheet": "Customers", "from_column": "City",
             "to_sheet": "Reference (2)", "to_column": "Branch City",
             "why": "Every customer city is also a branch city, but a customer's "
                    "city is not a reference to a branch. Only naming can tell "
                    "these apart -- this is what the LLM ranking pass is for."},
        ],
    }


FIXTURES = {
    "clean": fx_clean,
    "offset_header": fx_offset_header,
    "merged_cells": fx_merged_cells,
    "two_tables": fx_two_tables,
    "junk_columns": fx_junk_columns,
    "relational": fx_relational,
    "nightmare": fx_nightmare,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, builder in FIXTURES.items():
        wb, truth = builder(rng())
        xlsx = OUT / f"{name}.xlsx"
        wb.save(xlsx)
        (OUT / f"{name}.expected.json").write_text(json.dumps(truth, indent=2) + "\n")
        n_cols = sum(len(e["columns"]) for e in truth["entities"])
        print(f"{name:16} {len(truth['entities'])} entities  {n_cols:3d} columns  "
              f"{len(truth['relationships'])} relationships")
    print(f"\nWrote {len(FIXTURES)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
