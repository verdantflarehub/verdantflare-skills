class BridgeError(Exception):
    def __init__(self, code, message, *, retryable=False, task_id=None, reason=None, details=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.task_id = task_id
        self.reason = reason
        self.details = details

    def as_dict(self):
        return dict(code=self.code, message=str(self), retryable=self.retryable,
                    task_id=self.task_id, **({"reason": self.reason} if self.reason else {}),
                    **({'details': self.details} if self.details is not None else {}))
