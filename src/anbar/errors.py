"""Exception types with user-facing, actionable messages."""

from __future__ import annotations


class AnbarError(Exception):
    """An error that should be shown to the user without a traceback.

    ``hint`` is an optional suggestion on how to fix the problem, for example
    ``"try --mirror"``.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message} — {self.hint}"
        return self.message


class ConfigError(AnbarError):
    """Invalid or unreadable anbar.toml."""


class NetworkError(AnbarError):
    """An upstream registry or URL could not be reached."""


class IntegrityError(AnbarError):
    """A downloaded file does not match its expected checksum."""


class KitError(AnbarError):
    """The kit directory is missing, corrupt, or from an unsupported version."""
