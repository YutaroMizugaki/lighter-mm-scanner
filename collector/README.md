# Collector

The production collector package lives in [`src/lighter_mm/`](../src/lighter_mm/).

- Entrypoint: `lighter-mm collect` (see `Dockerfile`)
- Cloud deploy: Cloud Run **Worker Pool** via `cloudbuild.yaml`
- Durable storage: GCS through `StorageBackend` (local fallback under `data/remote/`)

This directory exists so the repo layout matches the cloud architecture diagram; do not duplicate business logic here.
