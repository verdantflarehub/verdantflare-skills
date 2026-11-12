"""Grok ACP and headless targeting with explicit session resumption."""
from ..worker import NotAccepted
from ..store import Conflict
from .task_mcp import TaskMCPAdapter, TERMINAL, prompt_for


class GrokAdapter(TaskMCPAdapter):
    prefix = "grok_"
    agent = "grok"
    native_key = "session_id"

    async def prepare(self, message, endpoint, handoff):
        if endpoint["mode"] not in {"acp", "headless"}:
            raise NotAccepted("UNSUPPORTED_CAPABILITY")
        binding = endpoint.get("binding")
        if message["body"]["kind"] == "cancel":
            if not binding:
                raise NotAccepted("UNSUPPORTED_CAPABILITY")
            status = await self.call("task_status", {"task_id": binding["task_id"], "limit": 1})
            self.check_target(status, endpoint, require_turn=True)
            if status["turn_id"] != binding["turn_id"]:
                raise Conflict("STALE_BINDING")
            return {"tool": "grok_task_cancel", "arguments": {"task_id": binding["task_id"], "expected_turn_id": binding["turn_id"]}}
        prompt = prompt_for(message, endpoint, handoff)
        if binding:
            status = await self.call("task_status", {"task_id": binding["task_id"], "limit": 1})
            self.check_target(status, endpoint, require_turn=True)
            if status["turn_id"] != binding["turn_id"]:
                raise Conflict("STALE_BINDING")
            if status.get("state") not in TERMINAL:
                raise NotAccepted("SESSION_BUSY")
            return {"tool": "grok_task_continue", "arguments": {"request_id": self.request_id(message),
                    "task_id": binding["task_id"], "expected_turn_id": binding["turn_id"], "prompt": prompt}}
        return {"tool": "grok_task_start", "arguments": {"request_id": self.request_id(message), "mode": endpoint["mode"],
                "cwd": endpoint["workspace"], "session_id": endpoint["native_session_id"], "prompt": prompt}}


def create(argv, store):
    return GrokAdapter(argv, store)
