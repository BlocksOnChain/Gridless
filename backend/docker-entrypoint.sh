#!/bin/sh
# Bring the database up to date, then serve.
#
# Both steps are safe to repeat: `migrate` is a no-op once applied and
# `seed_org` is a get_or_create, so restarting the container -- or running
# `docker compose up` twice -- changes nothing. That is what lets the API own
# its own schema instead of asking the reader to run two commands first.
set -e

python manage.py migrate --noinput
python manage.py seed_org

exec python manage.py runbolt --host 0.0.0.0 --port "${PORT:-8010}" "$@"
