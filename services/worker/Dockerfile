FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/core /srv/packages/core
RUN pip install --no-cache-dir -e /srv/packages/core

COPY services/worker/app /srv/app

# Run unprivileged. The container has no reason to write anywhere.
RUN useradd --create-home --uid 10001 pokearb && chown -R pokearb /srv
USER pokearb

CMD ["python", "-m", "app.run"]
