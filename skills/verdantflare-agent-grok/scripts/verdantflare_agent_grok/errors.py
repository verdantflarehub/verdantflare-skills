"""Structured error model for the Grok + AFK Pilot MCP service."""


class BridgeError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, task_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.task_id = task_id

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": str(self), "retryable": self.retryable}
        if self.task_id:
            out["task_id"] = self.task_id
        return out
