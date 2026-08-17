#!/usr/bin/env python3
"""Run Codex with the first ChatGPT account below the configured usage threshold."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import IO, Any

MAX_PERCENT = 100.0


class RateLimitError(RuntimeError):
    pass


def _send(stream: IO[str], message: Mapping[str, object]) -> None:
    stream.write(json.dumps(message, separators=(",", ":")) + "\n")
    stream.flush()


def _read_response(
    process: subprocess.Popen[str], request_id: int, timeout_seconds: float
) -> Mapping[str, Any]:
    if process.stdout is None:
        raise RateLimitError("Codex app-server stdout is unavailable")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RateLimitError(
                    f"Codex app-server timed out waiting for response {request_id}"
                )
            if not selector.select(remaining):
                continue
            line = process.stdout.readline()
            if not line:
                raise RateLimitError(f"Codex app-server exited before response {request_id}")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            error = message.get("error")
            if error is not None:
                raise RateLimitError(f"Codex app-server request {request_id} failed: {error}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise RateLimitError(f"Codex app-server response {request_id} has no object result")
            return result
    finally:
        selector.close()


def _maximum_used_percent(result: Mapping[str, Any]) -> float:
    buckets = result.get("rateLimitsByLimitId")
    if isinstance(buckets, dict) and buckets:
        rate_limits = list(buckets.values())
    else:
        rate_limits = [result.get("rateLimits")]

    percentages: list[float] = []
    for rate_limit in rate_limits:
        if not isinstance(rate_limit, dict):
            continue
        for window_name in ("primary", "secondary"):
            window = rate_limit.get(window_name)
            if not isinstance(window, dict):
                continue
            value = window.get("usedPercent")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                percentages.append(float(value))

    if not percentages:
        raise RateLimitError("Codex returned no usage percentage")
    return max(percentages)


def read_rate_limit(
    codex_home: Path, *, codex_command: str = "codex", timeout_seconds: float = 20.0
) -> float:
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    process = subprocess.Popen(  # noqa: S603
        [codex_command, "app-server", "--listen", "stdio://"],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    if process.stdin is None:
        process.kill()
        raise RateLimitError("Codex app-server stdin is unavailable")

    try:
        _send(
            process.stdin,
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "lovspor-ci",
                        "title": "Lovspor CI",
                        "version": "1.0.0",
                    }
                },
            },
        )
        _read_response(process, 0, timeout_seconds)
        _send(process.stdin, {"method": "initialized", "params": {}})
        _send(process.stdin, {"method": "account/rateLimits/read", "id": 1})
        result = _read_response(process, 1, timeout_seconds)
        return _maximum_used_percent(result)
    finally:
        with suppress(BrokenPipeError):
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def choose_home(
    primary_home: Path,
    secondary_home: Path,
    *,
    threshold: float,
    codex_command: str = "codex",
    timeout_seconds: float = 20.0,
) -> tuple[Path, float]:
    errors: list[str] = []
    for label, codex_home in (("primary", primary_home), ("secondary", secondary_home)):
        try:
            usage = read_rate_limit(
                codex_home,
                codex_command=codex_command,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, RateLimitError) as error:
            errors.append(f"{label}: {error}")
            continue
        print(f"Codex {label} account usage: {usage:g}%", file=sys.stderr)
        if usage < threshold:
            return codex_home, usage
        errors.append(f"{label}: usage {usage:g}% is at or above {threshold:g}%")

    raise RateLimitError("No Codex account is available (" + "; ".join(errors) + ")")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary-home", type=Path, required=True)
    parser.add_argument("--secondary-home", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=95.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("A command is required after --", file=sys.stderr)
        return 2
    if not 0 < args.threshold <= MAX_PERCENT:
        print("--threshold must be greater than 0 and at most 100", file=sys.stderr)
        return 2

    try:
        codex_home, _usage = choose_home(
            args.primary_home,
            args.secondary_home,
            threshold=args.threshold,
            codex_command=args.codex_command,
            timeout_seconds=args.timeout,
        )
    except RateLimitError as error:
        print(error, file=sys.stderr)
        return 1

    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    return subprocess.run(command, env=environment, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
