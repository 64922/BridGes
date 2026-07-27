"""Learning mission and evidence-backed diagnosis domain."""

from science_companion.learning.adapters import InMemoryLearningRepository, LearningError
from science_companion.learning.ports import LearningRepository
from science_companion.learning.service import LearningService

__all__ = [
    "LearningRepository",
    "InMemoryLearningRepository",
    "LearningService",
    "LearningError",
]
