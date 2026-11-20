"""FastAPI dependencies: hand each route the service it needs from the app's container."""

from typing import Annotated

from fastapi import Depends, Request

from ap_agent.services import ApprovalService, EvalService, RunService, ServiceContainer


def get_container(request: Request) -> ServiceContainer:
    container: ServiceContainer = request.app.state.container
    return container


Container = Annotated[ServiceContainer, Depends(get_container)]


def get_run_service(container: Container) -> RunService:
    return container.runs


def get_approval_service(container: Container) -> ApprovalService:
    return container.approvals


def get_eval_service(container: Container) -> EvalService:
    return container.evals


Runs = Annotated[RunService, Depends(get_run_service)]
Approvals = Annotated[ApprovalService, Depends(get_approval_service)]
Evals = Annotated[EvalService, Depends(get_eval_service)]
