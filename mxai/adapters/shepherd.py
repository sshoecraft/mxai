"""Shepherd adapter.

Speaks the JSON-lines protocol over stdin/stdout (--json frontend).

v0.2.0
"""

import json
import os

from .base import Adapter

SHEPHERD_BIN = "/usr/local/bin/shepherd"


class ShepherdAdapter(Adapter):

    backend_name = "shepherd"

    def build_command(self) -> list:
        cmd = [
            SHEPHERD_BIN,
            "--json",
            "--system-prompt", self.system_prompt,
        ]
        if self.provider:
            cmd.extend(["--provider", self.provider])
        if self.model:
            cmd.extend(["--model", self.model])
        if self.effort:
            cmd.extend(["--reasoning", self.effort])
        return cmd

    def build_env(self) -> dict:
        return dict(os.environ)

    def send(self, text: str):
        if not self.alive:
            return
        msg = json.dumps({"type": "user", "content": text}) + "\n"
        try:
            self.proc.stdin.write(msg.encode())
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

                if etype == "text":
                    content = event.get("content", "")
                    if content:
                        collected_text.append(content)

                elif etype == "tool_use":
                    name = event.get("name", "")
                    params = event.get("params", {})
                    desc = (params.get("command", "")[:80]
                            or params.get("file_path", "")
                            or params.get("pattern", "")
                            or params.get("query", "")
                            or "")
                    if self.on_tool_use:
                        self.on_tool_use(name, desc)

                elif etype == "end_turn":
                    turns = event.get("turns", 0)
                    total_tokens = event.get("total_tokens", 0)

                    if collected_text:
                        response = "".join(collected_text).strip()
                        if response and self.on_response:
                            self.on_response(response)
                        collected_text.clear()

                    if self.on_result:
                        self.on_result(0.0, turns)

                elif etype == "error":
                    msg = event.get("message", "unknown error")
                    if collected_text:
                        collected_text.append(f"\n[error: {msg}]")

            except json.JSONDecodeError:
                pass
