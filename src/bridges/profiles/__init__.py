"""Profile observation-candidate loop."""

from bridges.profiles.adapters import (
    InMemoryProfileRepository,
    ProfileError,
)
from bridges.profiles.ports import ProfileRepository
from bridges.profiles.service import ProfileService
from bridges.profiles.four_dimensions import (
    FourDimensionContractGate,
    FourDimensionMigrationGateError,
    FourDimensionProfileError,
    FourDimensionProfileRepository,
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
    SqliteFourDimensionProfileRepository,
)

__all__ = [
    "ProfileRepository",
    "InMemoryProfileRepository",
    "ProfileService",
    "ProfileError",
    "FourDimensionProfileError",
    "FourDimensionContractGate",
    "FourDimensionMigrationGateError",
    "FourDimensionProfileRepository",
    "FourDimensionProfileService",
    "InMemoryFourDimensionProfileRepository",
    "SqliteFourDimensionProfileRepository",
]
