# Build from the repository root so the shared common/ package is in the context:
#   docker build -f appraisal_system/form_builder/Dockerfile -t hph-form-builder .
# Dockerfile.dockerignore next to this file limits the context to this service and common/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY appraisal_system/common/requirements.txt common/requirements.txt
COPY appraisal_system/form_builder/requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY appraisal_system/common/ common/
COPY appraisal_system/form_builder/ .

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

# Migrations are a separate release step (`flask --app main db upgrade`), never run on start-up.
CMD ["gunicorn", "--config", "gunicorn.conf.py", "main:app"]
