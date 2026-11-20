"""FastAPI app (architecture §10). Run with: uvicorn ap_agent.api.app:app --workers 1

Layers: routes (router + controller: HTTP in, HTTP out) → services (use cases) →
orchestration. The API never touches the Orchestrator, storage or settings directly.

| Endpoint                          | Brief §2 operation        |
|-----------------------------------|---------------------------|
| POST /runs                        | Start run                 |
| GET  /runs/{run_id}[?timeline=1]  | Get run (+ timeline, §10) |
| POST /runs/{run_id}/approval      | Approve / reject          |
| POST /evals, GET /evals/latest    | List evaluation results   |

One worker by design: SQLite allows one writer and the idempotency guarantees rely on it.
submit_finance_decision is internal and never exposed here.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ap_agent import __version__
from ap_agent.api.routes import approval, evals, runs
from ap_agent.services import ServiceContainer, build_container


def create_app(
    container: ServiceContainer | None = None,
    container_factory: Callable[[], ServiceContainer] = build_container,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = getattr(app.state, "container", None) is None
        if owned:
            app.state.container = container_factory()  # also runs the O7 startup hook
        yield
        if owned:
            app.state.container.close()

    app = FastAPI(
        title="ap-rag-agent",
        version=__version__,
        description="Accounts-payable RAG agent with an approval gate. Tools are SIMULATED "
        "except document retrieval; the ledger is a sandbox.",
        lifespan=lifespan,
    )
    if container is not None:
        app.state.container = container
    app.include_router(runs.router)
    app.include_router(approval.router)
    app.include_router(evals.router)
    return app


app = create_app()
