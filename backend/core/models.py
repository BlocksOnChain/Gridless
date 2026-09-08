"""Gridless data model.

Two layers, kept deliberately separate:

Layer A — the meta-schema. Ordinary Django models describing *what we inferred*
from a workbook. Normal migrations, normal ORM, visible in Django Admin.

Layer B — the committed data (Record, Edge). The user's schema is not known at
build time, so committed rows live in one JSONB table projected into typed
Postgres views. These two models exist so migrations own the table DDL and the
indexes; per the design, all reads and CRUD go through raw parameterised SQL
against the generated views, never through this ORM.
"""

from django.contrib.postgres.indexes import GinIndex
from django.db import models


class DataType(models.TextChoices):
    TEXT = "text", "Text"
    INTEGER = "integer", "Integer"
    NUMERIC = "numeric", "Numeric"
    DATE = "date", "Date"
    DATETIME = "datetime", "Datetime"
    BOOLEAN = "boolean", "Boolean"
    JSON = "json", "JSON"


class WorkbookStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    ANALYSING = "analysing", "Analysing"
    PROPOSED = "proposed", "Proposed"
    COMMITTED = "committed", "Committed"
    FAILED = "failed", "Failed"


class Cardinality(models.TextChoices):
    ONE_TO_MANY = "one_to_many", "One to many"
    MANY_TO_ONE = "many_to_one", "Many to one"
    ONE_TO_ONE = "one_to_one", "One to one"


# --------------------------------------------------------------------------
# Layer A — meta-schema
# --------------------------------------------------------------------------


class Organization(models.Model):
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name

    @property
    def schema_name(self) -> str:
        """Postgres schema holding this org's generated views."""
        return f"org_{self.pk}"


class Workbook(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="workbooks"
    )
    original_filename = models.CharField(max_length=500)
    file = models.FileField(upload_to="workbooks/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20, choices=WorkbookStatus.choices, default=WorkbookStatus.UPLOADED
    )
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.status})"


class Sheet(models.Model):
    """One detected *table*, not necessarily one worksheet.

    A worksheet holding two tables separated by a blank row is split into two
    Sheet rows by stage 1, each with its own detected_range.
    """

    workbook = models.ForeignKey(Workbook, on_delete=models.CASCADE, related_name="sheets")
    name = models.CharField(max_length=300)
    sheet_index = models.IntegerField()
    detected_header_row = models.IntegerField(null=True, blank=True)
    detected_range = models.CharField(max_length=50, blank=True, default="")
    raw_row_count = models.IntegerField(default=0)
    # Human-readable log of what each pipeline stage saw. This is what makes a
    # bad inference debuggable and what the review screen quotes to explain
    # itself, so every stage appends to it.
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["sheet_index", "id"]

    def __str__(self) -> str:
        return self.name

    def note(self, message: str) -> None:
        """Append one line to the stage log and persist it."""
        self.notes = f"{self.notes}{message}\n" if self.notes else f"{message}\n"
        self.save(update_fields=["notes"])


class InferredEntity(models.Model):
    workbook = models.ForeignKey(
        Workbook, on_delete=models.CASCADE, related_name="entities"
    )
    source_sheet = models.ForeignKey(
        Sheet, on_delete=models.CASCADE, related_name="entities"
    )
    name = models.CharField(max_length=200)
    table_slug = models.SlugField(max_length=63)
    confidence = models.FloatField(default=0.0)
    user_confirmed = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]
        constraints = [
            # Slug becomes a view name inside the org's Postgres schema, so it
            # has to be unique per org, not merely per workbook.
            models.UniqueConstraint(
                fields=["workbook", "table_slug"], name="uniq_entity_slug_per_workbook"
            )
        ]

    def __str__(self) -> str:
        return self.name


class InferredField(models.Model):
    entity = models.ForeignKey(
        InferredEntity, on_delete=models.CASCADE, related_name="fields"
    )
    name = models.CharField(max_length=300)
    # The header exactly as it appeared in the spreadsheet. `name` is the
    # proposed/edited display name and may be rewritten by stage 3 or the user;
    # this one never changes, so eval and the review screen can always trace a
    # field back to its source cell.
    source_header = models.CharField(max_length=300, blank=True, default="")
    column_slug = models.SlugField(max_length=63)
    source_column_letter = models.CharField(max_length=10, blank=True, default="")
    data_type = models.CharField(
        max_length=20, choices=DataType.choices, default=DataType.TEXT
    )
    nullable = models.BooleanField(default=True)
    is_primary_key = models.BooleanField(default=False)
    confidence = models.FloatField(default=0.0)
    user_confirmed = models.BooleanField(default=False)
    sample_values = models.JSONField(default=list)
    position = models.IntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "column_slug"], name="uniq_field_slug_per_entity"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name}: {self.data_type}"


class SectionKind(models.TextChoices):
    NOTE = "note", "Note"
    SUMMARY = "summary", "Summary"
    META = "meta", "Meta"


class EntitySection(models.Model):
    """A piece of a sheet that belongs with a table but is not rows of it.

    Real workbooks carry prose: an AÇIKLAMALAR block explaining why line 5
    stopped twice last night, a TOPLAM row, a report date above the header.
    Before this existed those rows were absorbed as data, which produced a
    column whose every value was the same paragraph of Turkish -- information
    destroyed by being stored in the wrong shape.

    Sections are rendered under their table in the generated app. They are also
    the first thing here that the assistant can *create*: the review chat adds,
    edits and removes them, so the page can grow a piece of UI that no one wrote
    a component for.
    """

    entity = models.ForeignKey(
        InferredEntity, on_delete=models.CASCADE, related_name="sections"
    )
    kind = models.CharField(
        max_length=20, choices=SectionKind.choices, default=SectionKind.NOTE
    )
    title = models.CharField(max_length=300, blank=True, default="")
    body = models.TextField(blank=True, default="")
    #: Where it came from, e.g. "A60:I80". Empty when a person or the assistant
    #: created it rather than the sheet.
    source_range = models.CharField(max_length=50, blank=True, default="")
    position = models.IntegerField(default=0)
    created_by_user = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return f"{self.entity.name}: {self.title or self.kind}"


class InferredRelationship(models.Model):
    workbook = models.ForeignKey(
        Workbook, on_delete=models.CASCADE, related_name="relationships"
    )
    from_entity = models.ForeignKey(
        InferredEntity, on_delete=models.CASCADE, related_name="outgoing_relationships"
    )
    from_field = models.ForeignKey(
        InferredField, on_delete=models.CASCADE, related_name="outgoing_relationships"
    )
    to_entity = models.ForeignKey(
        InferredEntity, on_delete=models.CASCADE, related_name="incoming_relationships"
    )
    to_field = models.ForeignKey(
        InferredField, on_delete=models.CASCADE, related_name="incoming_relationships"
    )
    name = models.CharField(max_length=200, blank=True, default="")
    cardinality = models.CharField(
        max_length=20, choices=Cardinality.choices, default=Cardinality.MANY_TO_ONE
    )
    # Fraction of distinct source values found in the target column. Measured,
    # never guessed by a model.
    match_rate = models.FloatField(default=0.0)
    confidence = models.FloatField(default=0.0)
    user_confirmed = models.BooleanField(default=False)
    rejected = models.BooleanField(default=False)
    # True when the ranking stage returned no verdict for this measured
    # candidate. Such a candidate is neither accepted nor rejected: dropping it
    # would cost recall silently, and showing it as accepted would let an
    # unjudged join through on a skim. The review screen renders it as its own
    # state and a human has to decide.
    needs_review = models.BooleanField(default=False)
    rationale = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-match_rate", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["from_field", "to_field"], name="uniq_relationship_field_pair"
            )
        ]

    def __str__(self) -> str:
        return f"{self.from_entity.name}.{self.from_field.name} -> {self.to_entity.name}.{self.to_field.name}"


# --------------------------------------------------------------------------
# Layer B — committed data (DDL owned here, accessed via raw SQL)
# --------------------------------------------------------------------------


class Record(models.Model):
    entity = models.ForeignKey(
        InferredEntity, on_delete=models.CASCADE, related_name="records"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="records"
    )
    data = models.JSONField()
    source_row_index = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "record"
        indexes = [
            GinIndex(fields=["data"], name="record_data_gin"),
            models.Index(fields=["entity"], name="record_entity_idx"),
            models.Index(fields=["organization"], name="record_org_idx"),
        ]


class Edge(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="edges"
    )
    from_record = models.ForeignKey(
        Record, on_delete=models.CASCADE, related_name="outgoing_edges"
    )
    to_record = models.ForeignKey(
        Record, on_delete=models.CASCADE, related_name="incoming_edges"
    )
    relationship = models.ForeignKey(
        InferredRelationship, on_delete=models.CASCADE, related_name="edges"
    )

    class Meta:
        db_table = "edge"
        indexes = [
            models.Index(fields=["from_record"], name="edge_from_idx"),
            models.Index(fields=["to_record"], name="edge_to_idx"),
            models.Index(fields=["organization"], name="edge_org_idx"),
        ]
