"""Brainalot: readable notes, durable capture, explicit commands.

The only place the program version is written. ``pyproject.toml`` reads it, ``/api/health``
reports it, and ``scripts/build_release.py`` refuses to build when the extension manifest differs.
"""

__version__ = "0.1.38"
# Bumped when the HTTP API changes incompatibly; the panel compares it with its own expectation.
API_VERSION = 1
