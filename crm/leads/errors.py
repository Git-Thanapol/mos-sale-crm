"""Domain errors raised by the lead write services.

Kept in their own module so crm.followcycles / crm.intake can raise and catch
them without importing crm.leads.services (which would be circular).
"""

from __future__ import annotations


class StaleLeadError(Exception):
    """The caller's form was rendered against an older Lead.version.

    Views map this to HTTP 409: the data on screen is out of date, so the write
    is refused rather than silently overwriting a concurrent change.
    """


class LeadStoppedError(ValueError):
    """The operation is not allowed on a STOPPED lead (reactivate first)."""


class InvalidCaretakerError(ValueError):
    """The chosen caretaker cannot hold this lead (inactive / wrong team / role)."""
