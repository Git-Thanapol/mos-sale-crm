"""Error types of the CRM V2 import rules.

Names and `code` values mirror the prototype's classes (ImportTransitionError,
BatchBusinessDateError, ConfirmError, ApplicationError) so the parity fixtures
can compare them directly and the UI can show the same codes.
"""

from __future__ import annotations


class ApplicationError(Exception):
    """A rule violation with a stable machine code (prototype ApplicationError)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


class ImportTransitionError(Exception):
    def __init__(self, from_status: str, to_status: str, code: str, message: str) -> None:
        super().__init__(f"{code}: {from_status} -> {to_status} ({message})")
        self.from_status = from_status
        self.to_status = to_status
        self.code = code


class BatchBusinessDateError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class ConfirmError(Exception):
    def __init__(self, blockers: list[dict]) -> None:
        super().__init__(f"CONFIRM_BLOCKED: {', '.join(b['code'] for b in blockers)}")
        self.blockers = blockers
