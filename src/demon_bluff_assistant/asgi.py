"""ASGI entry point for development and browser tests (without global hotkey)."""

from demon_bluff_assistant.main import build_runtime

app = build_runtime().app

