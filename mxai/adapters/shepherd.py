"""Shepherd adapter.

Speaks the JSON-lines protocol over stdin/stdout (--json frontend).

v0.2.0
"""

import json
import os
import tempfile

from .base import Adapter

SHEPHERD_BIN = "/usr/local/bin/shepherd"


class ShepherdAdapter(Adapter):

    backend_name = "shepherd"

    def __init__(self, system_prompt: str, extra_args: list = None,
                 debug: bool = False):
        super().__init__(system_prompt, extra_args=extra_args, debug=debug)
        self.prompt_file = None

    def build_command(self) -> list:
        self.prompt_file = tempfile.NamedTemporaryFile(
            mode="w", prefix="mxai_", suffix=".txt",
            dir="/tmp", delete=False)
        self.prompt_file.write(self.system_prompt)
        self.prompt_file.close()

        cmd = [
            SHEPHERD_BIN,
            "--json",
            "--system-prompt-file", self.prompt_file.name,
        ]
        cmd.extend(self.extra_args)
        return cmd

    def cleanup(self):
        if self.prompt_file and os.path.exists(self.prompt_file.name):
            os.unlink(self.prompt_file.name)
            self.prompt_file = None

    def build_env(self) -> dict:
        return dict(os.environ)

    def send(self, text: str):
        if not self.alive:
            if self.debug:
                print(f"  [DEBUG shepherd] send called but proc not alive", flush=True)
            return
        msg = json.dumps({"type": "user", "content": text}) + "\n"
        if self.debug:
            print(f"  [DEBUG shepherd] stdin write: {msg[:200].strip()}", flush=True)
        try:
            self.proc.stdin.write(msg.encode())
            self.proc.stdin.flush()
            if self.debug:
                print(f"  [DEBUG shepherd] stdin flushed ok", flush=True)
        except (BrokenPipeError, OSError) as e:
            if self.debug:
                print(f"  [DEBUG shepherd] stdin write FAILED: {e}", flush=True)

    def parse_stdout(self):
        collected_text = []

        if self.debug:
            print(f"  [DEBUG shepherd] parse_stdout started, reading lines...", flush=True)

        for raw_line in self.proc.stdout:
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            if self.debug:
                print(f"  [DEBUG shepherd] stdout: {raw_line[:300]}", flush=True)

            try:
                event = json.loads(raw_line)
                etype = event.get("type")

                if etype == "text":
                    content = event.get("content", "")
                    if content:
                        collected_text.append(content)
                        if self.debug:
                            print(f"  [DEBUG shepherd] collected text chunk, total_chunks={len(collected_text)} len={len(content)}", flush=True)

                elif etype == "tool_use":
                    name = event.get("name", "")
                    params = event.get("params", {})
                    desc = (params.get("command", "")[:80]
                            or params.get("file_path", "")
                            or params.get("pattern", "")
                            or params.get("query", "")
                            or "")
                    if self.debug:
                        print(f"  [DEBUG shepherd] tool_use: {name} — {desc[:80]}", flush=True)
                    if self.on_tool_use:
                        self.on_tool_use(name, desc)

                elif etype == "end_turn":
                    turns = event.get("turns", 0)
                    total_tokens = event.get("total_tokens", 0)

                    if self.debug:
                        print(f"  [DEBUG shepherd] end_turn: turns={turns} tokens={total_tokens} collected_text_chunks={len(collected_text)}", flush=True)

                    if collected_text:
                        response = "".join(collected_text).strip()
                        if self.debug:
                            print(f"  [DEBUG shepherd] firing on_response, len={len(response)}", flush=True)
                        if response and self.on_response:
                            self.on_response(response)
                        collected_text.clear()
                    elif self.debug:
                        print(f"  [DEBUG shepherd] end_turn with no collected text", flush=True)

                    if self.debug:
                        print(f"  [DEBUG shepherd] firing on_result", flush=True)
                    if self.on_result:
                        self.on_result(0.0, turns)

                elif etype == "error":
                    msg = event.get("message", "unknown error")
                    print(f"  [DEBUG shepherd] error event: {msg}", flush=True)
                    if collected_text:
                        collected_text.append(f"\n[error: {msg}]")

                elif self.debug:
                    print(f"  [DEBUG shepherd] unknown event type: {etype}", flush=True)

            except json.JSONDecodeError:
                if self.debug:
                    print(f"  [DEBUG shepherd] JSON parse error: {raw_line[:200]}", flush=True)

        if self.debug:
            print(f"  [DEBUG shepherd] stdout loop ended (proc exited?)", flush=True)
