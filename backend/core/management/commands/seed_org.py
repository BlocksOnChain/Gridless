"""Create the single Organization v1 operates against."""

from django.core.management.base import BaseCommand

from core.models import Organization


class Command(BaseCommand):
    help = "Create a default Organization if none exists and print its id."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Demo Org")

    def handle(self, *args, **options):
        org, created = Organization.objects.get_or_create(name=options["name"])
        verb = "Created" if created else "Found existing"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} organization id={org.pk} name={org.name!r}")
        )
        self.stdout.write(f"Use it with:  X-Org-Id: {org.pk}")
