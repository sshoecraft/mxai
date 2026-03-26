"""Base adapter for AI CLI backends.

Each adapter knows how to spawn a specific AI CLI tool, send messages
to it via stdin, and parse responses from its stdout.

v0.1.2
"""

import subprocess
import threading
from abc import ABC, abstractmethod


class Adapter(ABC):
    """Base class for AI backend adapters."""

    def __init__(self, system_prompt: str, extra_args: list = None):
        self.system_prompt = system_prompt
        self.extra_args = extra_args or []
        self.proc = None
        self.on_response = None    # callback: fn(text: str)
        self.on_tool_use = None    # callback: fn(name: str, desc: str)
        self.on_result = None      # callback: fn(cost: float, turns: int)
        self.on_exit = None        # callback: fn(exit_code: int, stderr: str)
        self.stdout_thread = None
        self.stderr_thread = None
        self.stderr_output = ""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Short name for this backend (e.g. 'claude', 'gemini')."""
        ...

    @abstractmethod
    def build_command(self) -> list:
        """Return the argv list to spawn the subprocess."""
        ...

    @abstractmethod
    def build_env(self) -> dict:
        """Return the environment dict for the subprocess."""
        ...

    @abstractmethod
    def send(self, text: str):
        """Send a message to the subprocess via stdin."""
        ...

    @abstractmethod
    def parse_stdout(self):
        """Read stdout and call self.on_response/on_tool_use/on_result.

        This runs in a thread. Must loop until stdout is closed.
        """
        ...

    def spawn(self):
        """Start the subprocess."""
        argv = self.build_command()
        env = self.build_env()

        self.proc = subprocess.Popen(
            argv, env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.stdout_thread = threading.Thread(
            target=self._run_stdout_parser, daemon=True)
        self.stdout_thread.start()

        self.stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True)
        self.stderr_thread.start()

    def kill(self):
        """Terminate the subprocess."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _run_stdout_parser(self):
        """Wrapper that calls parse_stdout and fires on_exit when done."""
        try:
            self.parse_stdout()
        finally:
            exit_code = self.proc.wait() if self.proc else -1
            if self.stderr_thread:
                self.stderr_thread.join(timeout=5)
            if self.on_exit:
                self.on_exit(exit_code, self.stderr_output)

    def _drain_stderr(self):
        """Read stderr and capture output."""
        lines = []
        for line in self.proc.stderr:
            lines.append(line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line)
        self.stderr_output = "".join(lines).strip()
