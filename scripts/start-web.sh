#!/bin/sh
set -eu

python -m flask --app run:app db upgrade
python -m flask --app run:app setup-import-storage
exec gunicorn --config gunicorn.conf.py run:app
