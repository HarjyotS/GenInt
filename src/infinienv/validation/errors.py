"""Structured validation errors, not vague strings."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationIssue:
    code: str
    message: str
    object_id: str | None = None
    severity: str = "error"  # "error" | "warning"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "object_id": self.object_id,
            "severity": self.severity,
        }


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"valid": self.valid, "errors": [e.to_dict() for e in self.errors]}
