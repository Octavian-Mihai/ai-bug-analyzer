from __future__ import annotations

import threading

from fastapi import Depends, FastAPI, HTTPException

from app.config import Settings, get_settings
from app.llm import LLMClient
from app.models import AnalyzeRequest, AnalyzeResponse
from app.orchestrator import analyze

app = FastAPI(title="AI Bug Investigation Assistant")

_semaphore: threading.Semaphore | None = None
_semaphore_lock = threading.Lock()


def _get_semaphore(limit: int) -> threading.Semaphore:
    global _semaphore
    with _semaphore_lock:
        if _semaphore is None:
            _semaphore = threading.Semaphore(limit)
        return _semaphore


def get_llm_client(settings: Settings = Depends(get_settings)) -> LLMClient:
    return LLMClient(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        max_retries=settings.llm_max_retries,
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze_endpoint(
    request: AnalyzeRequest,
    settings: Settings = Depends(get_settings),
    llm_client: LLMClient = Depends(get_llm_client),
) -> AnalyzeResponse:
    semaphore = _get_semaphore(settings.max_concurrent_analyses)
    if not semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=429, detail="Too many concurrent analyses in progress, try again shortly."
        )
    try:
        return analyze(request, settings, llm_client)
    finally:
        semaphore.release()
