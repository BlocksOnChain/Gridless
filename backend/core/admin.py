"""Django Admin is our internal inspection tool for the meta-schema.

Per the brief: use this instead of building admin screens.
"""

from django.contrib import admin

from core import models


class InferredFieldInline(admin.TabularInline):
    model = models.InferredField
    extra = 0
    fields = (
        "position",
        "name",
        "column_slug",
        "source_column_letter",
        "data_type",
        "is_primary_key",
        "nullable",
        "confidence",
        "user_confirmed",
    )


class SheetInline(admin.TabularInline):
    model = models.Sheet
    extra = 0
    fields = ("sheet_index", "name", "detected_header_row", "detected_range", "raw_row_count")
    show_change_link = True


@admin.register(models.Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "schema_name", "created_at")


@admin.register(models.Workbook)
class WorkbookAdmin(admin.ModelAdmin):
    list_display = ("id", "original_filename", "organization", "status", "uploaded_at")
    list_filter = ("status", "organization")
    inlines = [SheetInline]


@admin.register(models.Sheet)
class SheetAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "workbook",
        "sheet_index",
        "detected_header_row",
        "detected_range",
        "raw_row_count",
    )
    list_filter = ("workbook",)
    readonly_fields = ("notes",)


@admin.register(models.InferredEntity)
class InferredEntityAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "table_slug", "workbook", "confidence", "user_confirmed")
    list_filter = ("workbook", "user_confirmed")
    inlines = [InferredFieldInline]


@admin.register(models.InferredField)
class InferredFieldAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "column_slug", "entity", "data_type", "is_primary_key", "confidence")
    list_filter = ("data_type", "is_primary_key", "user_confirmed")


@admin.register(models.InferredRelationship)
class InferredRelationshipAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__", "cardinality", "match_rate", "confidence", "user_confirmed", "rejected")
    list_filter = ("cardinality", "user_confirmed", "rejected")


@admin.register(models.Record)
class RecordAdmin(admin.ModelAdmin):
    list_display = ("id", "entity", "organization", "source_row_index", "created_at")
    list_filter = ("entity",)


@admin.register(models.Edge)
class EdgeAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "from_record", "to_record", "relationship")
