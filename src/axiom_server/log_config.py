"""log_config - Centralized, professional logging configuration for Axiom Engine.

Call ``configure_logging()`` once at process startup (in ``cli_run``).
Every other module should do nothing but ``logging.getLogger(__name__)``
and let propagation carry records up to the root handler defined here.
"""

from __future__ import annotations

# Copyright (C) 2025 The Axiom Contributors
# This program is licensed under the Peer Production License (PPL).
# See the LICENSE file for full details.
import logging
import sys

# ---------------------------------------------------------------------------
# ANSI colour palette
# ---------------------------------------------------------------------------
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

# Foreground colours
_FG_WHITE = "\033[97m"
_FG_CYAN = "\033[96m"
_FG_GREEN = "\033[92m"
_FG_YELLOW = "\033[93m"
_FG_RED = "\033[91m"
_FG_MAGENTA = "\033[95m"
_FG_BLUE = "\033[94m"
_FG_GREY = "\033[90m"

# Level → (colour prefix, short label)
_LEVEL_STYLES: dict[int, tuple[str, str]] = {
    logging.DEBUG: (_FG_GREY, "DEBUG"),
    logging.INFO: (_FG_GREEN, " INFO"),
    logging.WARNING: (_FG_YELLOW + _BOLD, " WARN"),
    logging.ERROR: (_FG_RED + _BOLD, "ERROR"),
    logging.CRITICAL: (_FG_MAGENTA + _BOLD, "CRIT"),
}

# Logger-name → accent colour  (longest-prefix match wins)
_NAME_COLOURS: dict[str, str] = {
    "axiom-node.background-thread": _FG_BLUE,
    "axiom-node": _FG_CYAN,
    "crucible": _FG_MAGENTA,
    "ledger": _FG_YELLOW,
    "axiom_server.discovery_rss": _FG_GREEN,
    "axiom-node.hasher": _FG_CYAN,
}


def _name_colour(name: str) -> str:
    """Return the best accent colour for a logger name."""
    # longest matching prefix wins
    best = ""
    colour = _FG_WHITE
    for prefix, col in _NAME_COLOURS.items():
        if name.startswith(prefix) and len(prefix) > len(best):
            best = prefix
            colour = col
    return colour


class _AxiomFormatter(logging.Formatter):
    """Colourised, single-line formatter.

    Format (one line)::

        2026-05-12 01:18:43  INFO  [axiom-node          ]  node.py:337  Starting P2P network …
        ^^^^^^^^^^^^^^^^^^^^^^^^  ^^^^  ^^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^  ^^^^^^^^^^^^^^^^^^^^^^^^
              timestamp           lvl       logger name        location         message
    """

    # The timestamp is printed in dim white so it doesn't dominate.
    _TS_FMT = "%Y-%m-%d %H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        level_colour, level_label = _LEVEL_STYLES.get(
            record.levelno,
            (_FG_WHITE, f"{record.levelno:5d}"),
        )
        name_col = _name_colour(record.name)

        # Timestamp
        ts = self.formatTime(record, self._TS_FMT)
        ts_str = f"{_DIM}{_FG_WHITE}{ts}{_RESET}"

        # Level badge  e.g.  " INFO"
        lvl_str = f"{level_colour}{level_label}{_RESET}"

        # Logger name - padded so columns stay aligned
        name_display = record.name[:24]
        name_str = f"{name_col}{name_display:<24}{_RESET}"

        # Source location - filename:lineno, kept short
        loc = f"{record.filename}:{record.lineno}"
        loc_str = f"{_DIM}{loc:<22}{_RESET}"

        # Message
        msg = record.getMessage()
        msg_str = f"{level_colour}{msg}{_RESET}"

        line = f"{ts_str}  {lvl_str}  [{name_str}]  {loc_str}  {msg_str}"

        # Attach exception traceback if present
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line = f"{line}\n{_FG_RED}{record.exc_text}{_RESET}"
        return line


class _PlainFormatter(logging.Formatter):
    """Non-colourised formatter for file handlers / non-TTY outputs."""

    _FMT = "%(asctime)s  %(levelname)-5s  [%(name)-24s]  %(filename)s:%(lineno)-4d  %(message)s"
    _TS = "%Y-%m-%d %H:%M:%S"

    def __init__(self) -> None:
        super().__init__(fmt=self._FMT, datefmt=self._TS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure_logging(
    level: int = logging.INFO,
    *,
    force_plain: bool = False,
) -> None:
    """Configure the root logger once for the entire Axiom process.

    All Axiom sub-loggers propagate to the root, so calling this once is
    sufficient.  Subsequent calls are idempotent (guarded by the handler
    count on the root logger).

    Args:
        level: Minimum log level (default: INFO).
        force_plain: If True, use the plain (non-ANSI) formatter even on a
                     TTY (useful when piping to a file from code).

    """
    root = logging.getLogger()

    # Idempotency guard - don't add handlers twice (e.g. during testing)
    if root.handlers:
        return

    root.setLevel(level)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(level)

    use_colour = (not force_plain) and sys.stdout.isatty()
    handler.setFormatter(
        _AxiomFormatter() if use_colour else _PlainFormatter(),
    )

    root.addHandler(handler)

    # Silence extremely chatty third-party loggers that would otherwise flood
    # the output with low-value noise.
    silence_loggers: list[tuple[str, int]] = [
        ("urllib3", logging.WARNING),
        ("requests", logging.WARNING),
        ("feedparser", logging.WARNING),
        ("werkzeug", logging.WARNING),  # Flask dev server
        ("transformers", logging.WARNING),
        ("torch", logging.WARNING),
        ("filelock", logging.WARNING),
        ("huggingface_hub", logging.WARNING),
        ("spacy", logging.WARNING),
        ("crucible", logging.WARNING),
        ("axiom-node.hasher", logging.WARNING),
        ("axiom_server.discovery_rss", logging.WARNING),
    ]
    for name, lvl in silence_loggers:
        logging.getLogger(name).setLevel(lvl)


def interactive_log_level() -> None:
    """Launch a simple REPL to view and change the root log level."""
    import shlex

    level_map: dict[str, int] = {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
        "NOTSET": logging.NOTSET,
    }

    root = logging.getLogger()
    prompt: str = "(logctl) "

    while True:
        try:
            raw: str = input(prompt)
        except (KeyboardInterrupt, EOFError):
            print()
            break

        try:
            args: list[str] = shlex.split(raw, posix=True)
        except ValueError as exc:
            print(f"Error parsing input: {exc}")
            continue

        if not args:
            continue

        cmd: str = args[0].lower()
        if cmd in {"quit", "exit"}:
            break
        if cmd == "show":
            lvl: int = root.level
            name: str = logging.getLevelName(lvl)
            print(f"Current root level: {name} ({lvl})")
        elif cmd == "list":
            print("Available levels:")
            for name, val in level_map.items():
                print(f"  {name:8} â {val}")
        elif cmd == "set" and len(args) == 2:
            level_name: str = args[1].upper()
            if level_name in level_map:
                root.setLevel(level_map[level_name])
                print(f"Root level set to {level_name}")
            else:
                print(f"Unknown level: {level_name}")
        else:
            print(f"Unknown command: {cmd}")
