"""Claude Code adapter.

Speaks the stream-json protocol over stdin/stdout.

v0.1.1
"""

import json
import os
import uuid

from .base import Adapter

CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")


class ClaudeAdapter(Adapter):

    backend_name = "claude"

    def __init__(self, system_prompt: str, model: str = None, effort: str = None,
                 provider: str = None):
        super().__init__(system_prompt, model, effort, provider=provider)
        self.session_id = str(uuid.uuid4())

    def build_command(self) -> list:
        cmd = [
            CLAUDE_BIN,
            "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--permission-mode", "bypassPermissions",
            "--verbose",
            "--system-prompt", self.system_prompt,
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.effort:
            cmd.extend(["--effort", self.effort])
        return cmd

    def build_env(self) -> dict:
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "16000"
        return env

    def send(self, text: str):
        if not self.alive:
            return
        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": text,
            },
            "parent_tool_use_id": None,
            "session_id": self.session_id,
        }
        line = json.dumps(msg) + "\n"
        try:
            self.proc.stdin.write(line.encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def parse_stdout(self):
        collected_text = []

        for raw_line in self.proc.stdout:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
                etype = event.get("type")

                if etype == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        kind = block.get("type")
                        if kind == "text":
                            text = block.get("text", "").strip()
                            if text:
                                collected_text.append(text)
                        elif kind == "tool_use":
                            name = block.get("name", "")
                            inp = block.get("input", {})
                            desc = (inp.get("description")
                                    or inp.get("command", "")[:80]
                                    or inp.get("file_path", "")
                                    or inp.get("pattern", "")
                                    or "")
                            if self.on_tool_use:
                                self.on_tool_use(name, desc)

                elif etype == "result":
                    cost = event.get("total_cost_usd", 0)
                    turns = event.get("num_turns", 0)

                    if collected_text:
                        response = "\n".join(collected_text)
                        if self.on_response:
                            self.on_response(response)
                        collected_text.clear()

                    if self.on_result:
                        self.on_result(cost, turns)

            except json.JSONDecodeError:
                pass
