"""The review screen's chat: change the proposal by asking for it.

The review screen is the one place a non-technical user has to make decisions
about their own data, and the vocabulary there is theirs, not ours -- "the
joined date should be a date", "add a column for the renewal owner", "call it
Customer Name". Typing that is faster than hunting for the right dropdown, and
it is the only interaction on the page that does not assume the user knows what
a data type is.

Two properties this module exists to guarantee:

* **Every change is a real change.** The agent cannot answer "done" without
  having called a tool; the tools are the only way it can affect anything, and
  each one returns the change it made. What comes back to the browser is the
  list of applied changes plus a freshly-read proposal, so the UI shows the
  truth rather than the model's account of it.
* **A hallucinated column cannot land.** Tools resolve a table or column by
  name, slug or original spreadsheet header, and an unresolvable one returns the
  list of real options instead of creating anything. The agent then corrects
  itself, which is cheaper than validating a bad plan after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import InferredEntity, InferredField, Workbook
from core.review import (
    ReviewEditError,
    add_manual_field,
    add_section as add_section_row,
    check_type,
    remove_field,
    set_primary_key,
)

SYSTEM_PROMPT = """\
You help someone tidy up the columns of a spreadsheet that is being turned into \
a small business app. They are not technical: they know their business, not \
databases. Talk about "tables" and "columns", never about schemas, slugs, \
foreign keys or primary keys -- the identifying column is "the ID column".

You have tools that change the proposal. Use them. Rules:

1. Never say you changed something unless a tool call did it. If a tool returns \
an error, read it -- it lists the real tables and columns -- and try again with \
a name from that list.
2. Ambiguous request? Make the reasonable interpretation and say what you did. \
Only ask a question back when the request could destroy the wrong thing.
3. Removing a column throws away that data at commit time. Do it when asked, \
and say so plainly in your reply.
4. Types must be one of: text, integer, numeric, date, datetime, boolean, json. \
Money is numeric. A yes/no column is boolean. A count is integer.
5. Not everything in a spreadsheet is a column. Notes, remarks, a totals line, \
an instruction to whoever fills the sheet in -- those belong in a *section* \
under the table, and `add_section` puts one there. Reach for it when the person \
describes text that is about the table rather than a value in it. `edit_section` \
and `remove_section` change the ones already there.
6. If the request is not about the tables, columns and sections in front of you \
(for example a question about the actual row values, which are not loaded yet), \
say so in one sentence and suggest they commit first and use Ask.

Reply in at most three short sentences, in the user's own language, plainly \
describing what you changed. No lists of technical detail, no apologies."""

@dataclass
class AssistResult:
    reply: str
    changes: list[str] = field(default_factory=list)


def describe_proposal(workbook: Workbook) -> str:
    """What the agent is allowed to talk about, in the user's terms.

    Sample values are included because they are what makes a type request
    decidable: "Joined" is obviously a date once you have seen three of them.
    """
    lines: list[str] = []
    entities = (
        InferredEntity.objects.filter(workbook=workbook)
        .prefetch_related("fields")
        .order_by("id")
    )
    for entity in entities:
        lines.append(f'Table "{entity.name}"')
        for f in entity.fields.all():
            bits = [f'  - "{f.name}" ({f.data_type})']
            if f.is_primary_key:
                bits.append("[ID column]")
            if f.source_header and f.source_header != f.name:
                bits.append(f'[from spreadsheet header "{f.source_header}"]')
            if not f.source_header:
                bits.append("[added by hand, empty]")
            samples = [str(v) for v in (f.sample_values or [])[:3] if v is not None]
            if samples:
                bits.append(f"e.g. {', '.join(samples)}")
            lines.append(" ".join(bits))
        for section in entity.sections.all():
            preview = section.body[:120].replace("\n", " ")
            lines.append(
                f'  [section: {section.kind}] "{section.title}"'
                + (f" -- {preview}..." if preview else "")
            )
    if not lines:
        return "This workbook has no tables."
    return "\n".join(lines)


def _entities(workbook: Workbook) -> list[InferredEntity]:
    return list(
        InferredEntity.objects.filter(workbook=workbook)
        .prefetch_related("fields")
        .order_by("id")
    )


def _find_entity(workbook: Workbook, table: str) -> InferredEntity | str:
    """Resolve a table the way a person would name it, or explain the options."""
    wanted = (table or "").strip().lower()
    entities = _entities(workbook)
    for e in entities:
        if wanted in {e.name.lower(), e.table_slug.lower()}:
            return e
    # A singular/plural mismatch is the common miss ("Customers" vs "Customer"),
    # and it is not worth a round trip to the model.
    for e in entities:
        if wanted.rstrip("s") == e.name.lower().rstrip("s"):
            return e
    names = ", ".join(f'"{e.name}"' for e in entities) or "none"
    return f"ERROR: there is no table called {table!r}. The tables are: {names}."


def _find_field(entity: InferredEntity, column: str) -> InferredField | str:
    wanted = (column or "").strip().lower()
    fields = list(entity.fields.all())
    for f in fields:
        if wanted in {f.name.lower(), f.column_slug.lower(), f.source_header.lower()}:
            return f
    names = ", ".join(f'"{f.name}"' for f in fields) or "none"
    return (
        f"ERROR: table {entity.name!r} has no column called {column!r}. "
        f"Its columns are: {names}."
    )


def _resolve(workbook: Workbook, table: str, column: str):
    entity = _find_entity(workbook, table)
    if isinstance(entity, str):
        return entity
    found = _find_field(entity, column)
    if isinstance(found, str):
        return found
    return found


def build_tools(workbook: Workbook, changes: list[str]):
    """Tools bound to one workbook. Nothing outside it is reachable."""
    from langchain_core.tools import tool

    def record(message: str) -> str:
        changes.append(message)
        return f"OK: {message}"

    @tool
    def rename_table(table: str, new_name: str) -> str:
        """Rename one table.

        Args:
            table: The table's current name.
            new_name: The name to show instead, e.g. "Customer".
        """
        entity = _find_entity(workbook, table)
        if isinstance(entity, str):
            return entity
        name = (new_name or "").strip()
        if not name:
            return "ERROR: the new name cannot be empty."
        was, entity.name = entity.name, name
        entity.save(update_fields=["name"])
        return record(f'Renamed the table "{was}" to "{name}".')

    @tool
    def rename_column(table: str, column: str, new_name: str) -> str:
        """Rename one column.

        Args:
            table: The table the column is in.
            column: The column's current name.
            new_name: The name to show instead, e.g. "Customer Name".
        """
        found = _resolve(workbook, table, column)
        if isinstance(found, str):
            return found
        name = (new_name or "").strip()
        if not name:
            return "ERROR: the new name cannot be empty."
        was, found.name = found.name, name
        found.save(update_fields=["name"])
        return record(f'Renamed "{was}" to "{name}".')

    @tool
    def set_column_type(table: str, column: str, data_type: str) -> str:
        """Change what kind of value a column holds.

        Args:
            table: The table the column is in.
            column: The column's name.
            data_type: One of text, integer, numeric, date, datetime, boolean, json.
        """
        found = _resolve(workbook, table, column)
        if isinstance(found, str):
            return found
        try:
            wanted = check_type(data_type)
        except ReviewEditError as exc:
            return f"ERROR: {exc}"
        was, found.data_type = found.data_type, wanted
        found.user_confirmed = True
        found.save(update_fields=["data_type", "user_confirmed"])
        return record(f'Changed "{found.name}" from {was} to {wanted}.')

    @tool
    def set_id_column(table: str, column: str) -> str:
        """Make one column the table's ID column (the value that identifies a row).

        Args:
            table: The table to set the ID column on.
            column: The column that identifies each row uniquely.
        """
        found = _resolve(workbook, table, column)
        if isinstance(found, str):
            return found
        set_primary_key(found)
        return record(f'Made "{found.name}" the ID column of "{found.entity.name}".')

    @tool
    def add_column(table: str, name: str, data_type: str = "text") -> str:
        """Add a new, empty column that the spreadsheet did not have.

        Args:
            table: The table to add the column to.
            name: The new column's name, e.g. "Renewal Owner".
            data_type: One of text, integer, numeric, date, datetime, boolean, json.
        """
        entity = _find_entity(workbook, table)
        if isinstance(entity, str):
            return entity
        try:
            created = add_manual_field(entity, name, data_type)
        except ReviewEditError as exc:
            return f"ERROR: {exc}"
        return record(
            f'Added an empty {created.data_type} column "{created.name}" to '
            f'"{entity.name}". It will be blank until someone fills it in.'
        )

    @tool
    def remove_column(table: str, column: str) -> str:
        """Remove a column from the proposal, so its data is not imported.

        Args:
            table: The table the column is in.
            column: The column to remove.
        """
        found = _resolve(workbook, table, column)
        if isinstance(found, str):
            return found
        entity = found.entity
        try:
            label = remove_field(found)
        except ReviewEditError as exc:
            return f"ERROR: {exc}"
        return record(f'Removed "{label}" from "{entity.name}". Its data will not be imported.')

    def _find_section(entity: InferredEntity, title: str):
        wanted = (title or "").strip().lower()
        sections = list(entity.sections.all())
        for section in sections:
            if section.title.lower() == wanted:
                return section
        for section in sections:
            if wanted and wanted in section.title.lower():
                return section
        names = ", ".join(f'"{s.title}"' for s in sections) or "none"
        return (
            f"ERROR: {entity.name!r} has no section called {title!r}. "
            f"Its sections are: {names}."
        )

    @tool
    def add_section(table: str, title: str, text: str, kind: str = "note") -> str:
        """Add a section of text under a table, for what is not a column.

        Use for notes, remarks, instructions or a totals summary -- anything the
        sheet says *about* the table rather than a value in it.

        Args:
            table: The table the section belongs under.
            title: A short heading, e.g. "Notes" or "AÇIKLAMALAR".
            text: The body text. Newlines separate paragraphs.
            kind: "note" for remarks, "summary" for totals, "meta" for dates
                and other header information.
        """
        entity = _find_entity(workbook, table)
        if isinstance(entity, str):
            return entity
        try:
            created = add_section_row(entity, title, text, kind)
        except ReviewEditError as exc:
            return f"ERROR: {exc}"
        return record(
            f'Added a {created.kind} section "{created.title}" under "{entity.name}".'
        )

    @tool
    def edit_section(table: str, title: str, new_title: str = "", text: str = "") -> str:
        """Change a section's heading or text.

        Args:
            table: The table the section is under.
            title: The section's current heading.
            new_title: A new heading, or "" to keep it.
            text: New body text, or "" to keep it.
        """
        entity = _find_entity(workbook, table)
        if isinstance(entity, str):
            return entity
        section = _find_section(entity, title)
        if isinstance(section, str):
            return section
        changed = []
        if new_title.strip():
            section.title = new_title.strip()
            changed.append("title")
        if text.strip():
            section.body = text.strip()
            changed.append("body")
        if not changed:
            return "ERROR: give a new_title or some text to change."
        section.save(update_fields=changed)
        return record(f'Updated the section "{section.title}" under "{entity.name}".')

    @tool
    def remove_section(table: str, title: str) -> str:
        """Remove a section from under a table.

        Args:
            table: The table the section is under.
            title: The section's heading.
        """
        entity = _find_entity(workbook, table)
        if isinstance(entity, str):
            return entity
        section = _find_section(entity, title)
        if isinstance(section, str):
            return section
        label = section.title
        section.delete()
        return record(f'Removed the section "{label}" from "{entity.name}".')

    return [
        rename_table,
        rename_column,
        set_column_type,
        set_id_column,
        add_column,
        remove_column,
        add_section,
        edit_section,
        remove_section,
    ]


def run_assist(
    workbook: Workbook, message: str, history: list[dict] | None = None
) -> AssistResult:
    """One turn of the review chat. Raises MissingAPIKey when no key is set."""
    from inference.llm.agent import build_agent, final_text, structured_result
    from inference.llm.schemas import AssistAnswer

    changes: list[str] = []
    agent = build_agent(
        system_prompt=SYSTEM_PROMPT,
        response_schema=AssistAnswer,
        tools=build_tools(workbook, changes),
    )

    messages: list[dict] = []
    # Only the visible conversation is replayed -- previous tool traffic would
    # invite the agent to "re-apply" a change it already made.
    for turn in history or []:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append(
        {
            "role": "user",
            "content": (
                f"{message}\n\n"
                f"--- The tables and columns as they stand now ---\n"
                f"{describe_proposal(workbook)}"
            ),
        }
    )

    try:
        result = agent.invoke({"messages": messages})
        try:
            reply = structured_result(result, AssistAnswer).reply.strip()
        except Exception:
            # Prose instead of the structured-output tool call; the changes were
            # applied by the tools regardless. See agent.final_text.
            reply = final_text(result)
    except Exception as exc:
        if changes:
            # Tools already committed their changes, so reporting a bare failure
            # would leave the UI out of step with the database.
            return AssistResult(
                reply=(
                    "I made the changes below, but lost the thread after that "
                    f"({exc.__class__.__name__}). Have a look and tell me if "
                    "anything is still wrong."
                ),
                changes=changes,
            )
        raise

    if not reply:
        reply = (
            "Done." if changes else "I could not work out what to change. Try naming "
            "the table and column you mean."
        )
    return AssistResult(reply=reply, changes=changes)
