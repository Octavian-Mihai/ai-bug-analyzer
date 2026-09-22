FROM python:3.12-slim

# git is required at runtime, not just build time: the sandbox validation engine
# shells out to `git apply` and pytest against a disposable clone of the target repo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /tmp/ai-bug-analyzer \
    && chown -R appuser:appuser /app /tmp/ai-bug-analyzer

ENV WORKDIR_ROOT=/tmp/ai-bug-analyzer

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
