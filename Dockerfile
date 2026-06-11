# Social Spark Studio — Cloud Run image
FROM python:3.12-slim

WORKDIR /app

# ffmpeg — extracts the audio track from uploaded founder MP4s
# (Speech-to-Text batch cannot decode MP4 containers directly)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first so Docker layer caching skips
# reinstalling them on every code change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ holds generated outputs (JSON, images, CSVs).
# NOTE: on Cloud Run this directory is ephemeral — files are lost on
# restart/scale. Fine for a demo; use GCS (GCS_BUCKET_NAME) for persistence.
RUN mkdir -p data/generated_images

ENV PYTHONUNBUFFERED=1

# Cloud Run injects $PORT (default 8080). No --reload in production.
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}
