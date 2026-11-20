"""Use-case errors the delivery layer (HTTP, CLI) translates into its own vocabulary."""


class RunNotFound(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id} not found")
        self.run_id = run_id


class NoEvalReport(Exception):
    """No evaluation has been run yet."""
