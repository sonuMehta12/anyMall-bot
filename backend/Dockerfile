FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# NOTE: Do NOT run migrations here. Run them as a separate step before deploy:
#   docker run --rm <image> python -m alembic upgrade head
# Running migrations in CMD causes race conditions with multiple containers (W9).

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
