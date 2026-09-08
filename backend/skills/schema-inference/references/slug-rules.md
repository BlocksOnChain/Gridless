# Slug rules

Slugs are interpolated into Postgres DDL. `commitdata/ddl.py` re-validates every
slug at commit time and raises `UnsafeIdentifier` on anything that fails, so a
bad slug is caught rather than executed — but a rejected slug means the field
falls back to a machine-generated name, which is a worse outcome for the user.

## Pattern

    ^[a-z][a-z0-9_]{0,62}$

Lowercase ASCII letter first, then lowercase letters, digits and underscores.
Maximum 63 characters (Postgres `NAMEDATALEN - 1`). No leading digit, no
hyphens, no spaces, no accents, no double underscores.

## Reserved — never emit these as a slug

Postgres keywords:

    all analyse analyze and any array as asc authorization between binary both
    case cast check collate column constraint create cross current_date
    current_time current_timestamp current_user default deferrable desc distinct
    do else end except false for foreign freeze from full grant group having
    ilike in initially inner intersect into is isnull join leading left like
    limit localtime localtimestamp natural not notnull null offset on only or
    order outer overlaps placing primary references returning right select
    session_user similar some symmetric table then to trailing true union unique
    user using verbose when where window with

Gridless-reserved, because the generated view already projects them or the
physical table owns them:

    id record edge data entity_id organization_id

When the natural slug is reserved, prefer a meaningful qualifier
(`order` → `sales_order`, `user` → `account_user`) over a mechanical suffix.

## Transliteration

Turkish characters do not decompose under Unicode NFKD, so they are mapped
explicitly before normalisation:

| Character | Becomes |
|---|---|
| ı | i |
| ğ | g |
| ü | u |
| ş | s |
| ö | o |
| ç | c |

Everything else is NFKD-normalised and stripped of combining marks, so `é` → `e`
and `ñ` → `n`.

## Examples

| Header | Slug |
|---|---|
| `Customer Name` | `customer_name` |
| `Poliçe No` | `police_no` |
| `Ürün Adı` | `urun_adi` |
| `Premium (TRY)` | `premium` |
| `2024 Sales` | `field_2024_sales` |
| `id` | prefer `customer_id`; the generator falls back to `id_col` |
| `` (blank header) | `unnamed_1`, `unnamed_2`, … |
