# Architecture

A FastAPI service: given a repo + ref, it diagnoses a failure with Claude, proposes a patch and regression test, and validates them in a disposable clone before returning.

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI (app/main.py)
    participant O as orchestrator.analyze
    participant GH as GitHub (github_client)
    participant E as error_capture / context
    participant L as LLMClient (Claude)
    participant S as sandbox (temp clone)

    C->>API: POST /analyze {repo, ref, error?}
    API->>O: analyze(request)
    O->>GH: resolve ref (branch / SHA / PR), clone
    GH-->>O: workspace
    O->>E: parse traceback or discover failing test
    E-->>O: error + code context
    O->>L: traceback + source context
    L-->>O: diagnosis, diff, regression test
    O->>S: apply patch in disposable clone, run test
    S-->>O: validation result
    O-->>API: AnalyzeResponse
    API-->>C: diagnosis + patch + test + validated?
```

| Module | Role |
|---|---|
| `config.py` | Settings (model, tokens) |
| `github_client.py` | Parse URLs/PRs, clone |
| `error_capture.py` / `context.py` | Find failing test, extract surrounding code |
| `llm.py` | Anthropic SDK, structured output validated with Pydantic |
| `sandbox.py` / `subprocess_utils.py` | Isolated patch + test execution |
