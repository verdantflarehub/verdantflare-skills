"""Codex plugin targeting and managed-task continuation without mode fallback."""
from ..worker import NotAccepted
from ..store import Conflict
from .task_mcp import TaskMCPAdapter, TERMINAL, prompt_for


class CodexAdapter(TaskMCPAdapter):
    prefix = "codex_"
    agent = "codex"
    native_key = "thread_id"

    async def prepare(self, message, endpoint, handoff):
        mode = endpoint["mode"]
        if mode not in {"plugin", "managed"}:
            raise NotAccepted("UNSUPPORTED_CAPABILITY")
        binding = endpoint.get("binding")
        if message["body"]["kind"] == "cancel":
            if not binding:
                raise NotAccepted("UNSUPPORTED_CAPABILITY")
            status = await self.call("task_status", {"task_id": binding["task_id"], "limit": 1})
            self.check_target(status, endpoint, require_turn=True)
            if status["turn_id"] != binding["turn_id"]:
                raise Conflict("STALE_BINDING")
            return {"tool": "codex_task_cancel", "arguments": {"task_id": binding["task_id"], "expected_turn_id": binding["turn_id"]}}
        prompt = prompt_for(message, endpoint, handoff)
        if mode == "plugin":
            return {"tool": "codex_task_start", "arguments": {"request_id": self.request_id(message),
                    "mode": mode, "cwd": endpoint["workspace"], "thread_id": endpoint["native_session_id"], "prompt": prompt}}
        if not binding:
            raise NotAccepted("UNSUPPORTED_CAPABILITY")
        status = await self.call("task_status", {"task_id": binding["task_id"], "limit": 1})
        self.check_target(status, endpoint, require_turn=True)
        if status["turn_id"] != binding["turn_id"]:
            raise Conflict("STALE_BINDING")
        if status.get("state") not in TERMINAL:
            raise NotAccepted("SESSION_BUSY")
        return {"tool": "codex_task_continue", "arguments": {"request_id": self.request_id(message),
                "task_id": binding["task_id"], "expected_turn_id": binding["turn_id"], "prompt": prompt}}


def create(argv, store):
    return CodexAdapter(argv, store)
