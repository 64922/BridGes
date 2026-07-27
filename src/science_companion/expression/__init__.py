"""Scientific expression pipeline (T025)."""

from science_companion.expression.service import (
    DraftGeneratorPort,
    ExpressionService,
    ExpressionServiceError,
)

__all__ = [
    "DraftGeneratorPort",
    "ExpressionService",
    "ExpressionServiceError",
]
