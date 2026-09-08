"""Django URLs.

Bolt serves the API from api.py modules; Django only serves the admin, which is
our internal inspection tool for the meta-schema. Django-Bolt auto-detects this
prefix and mounts Django's ASGI app there.
"""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
