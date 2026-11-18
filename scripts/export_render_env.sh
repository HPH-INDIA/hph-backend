#!/usr/bin/env bash
set -euo pipefail

project_id="${GCP_PROJECT_ID:-hph-inhouse}"
output_path="${1:-.env.render}"

database_url="$(gcloud secrets versions access latest \
  --secret=hph-supabase-database-url \
  --project="${project_id}")"
secret_key="$(gcloud secrets versions access latest \
  --secret=hph-secret-key \
  --project="${project_id}")"
encryption_master_key="$(gcloud secrets versions access latest \
  --secret=hph-encryption-master-key \
  --project="${project_id}")"

if [[ -z "${database_url}" || -z "${secret_key}" || -z "${encryption_master_key}" ]]; then
  echo "One or more required production secrets are empty." >&2
  exit 1
fi

umask 077
temporary_path="$(mktemp "${output_path}.tmp.XXXXXX")"
trap 'rm -f "${temporary_path}"' EXIT

{
  printf 'DATABASE_URL=%s\n' "${database_url}"
  printf 'SECRET_KEY=%s\n' "${secret_key}"
  printf 'ENCRYPTION_MASTER_KEY=%s\n' "${encryption_master_key}"
  printf 'KAIRON_IDENTITY_KEY=%s\n' "${secret_key}"
  printf 'SESSION_COOKIE_SECURE=true\n'
  printf 'TEMP_PASSWORD_EXPIRY_HOURS=72\n'
  printf 'CELERY_BROKER_URL=redis://localhost:6379/0\n'
  printf 'CELERY_RESULT_BACKEND=redis://localhost:6379/0\n'
  printf 'CELERY_TASK_ALWAYS_EAGER=true\n'
  printf 'EMAIL_HOST=localhost\n'
  printf 'EMAIL_PORT=1025\n'
  printf 'EMAIL_USE_TLS=false\n'
  printf 'EMAIL_USE_SSL=false\n'
  printf 'EMAIL_HOST_USER=\n'
  printf 'EMAIL_HOST_PASSWORD=\n'
  printf 'DEFAULT_FROM_EMAIL=system@humanpoweredhealth.com\n'
  printf 'EMAIL_SUPPRESS_SEND=true\n'
  printf 'EMAIL_VALIDATE_CONFIG=false\n'
  printf 'FRONTEND_LOGIN_URL=https://hph.onrender.com/login\n'
  printf 'LOG_DECRYPTED_PAYLOADS=false\n'
  printf 'CORS_ALLOWED_ORIGINS=https://hph.onrender.com\n'
  printf 'WEB_CONCURRENCY=2\n'
  printf 'GUNICORN_THREADS=4\n'
  printf 'GUNICORN_TIMEOUT=120\n'
} > "${temporary_path}"

mv "${temporary_path}" "${output_path}"
trap - EXIT
echo "Wrote ${output_path} with permissions 600."
