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
from bridges.profiles.automatic import (
    AUTOMATIC_EXTRACTOR_VERSION,
    AutomaticProfileError,
    AutomaticProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryAutomaticProfileRepository,
    RuleBasedAutomaticProfileExtractor,
    SqliteAutomaticProfileRepository,
)
from bridges.contracts.profile_extraction import ProfileExtractionOutput
from bridges.profiles.signals import (
    PROFILE_SIGNAL_CLASSIFIER_VERSION,
    ProfileSignalCategory,
    ProfileSignalClassification,
    ProfileSignalClassifier,
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
    "AUTOMATIC_EXTRACTOR_VERSION",
    "AutomaticProfileError",
    "AutomaticProfileService",
    "GatewayAutomaticProfileExtractor",
    "InMemoryAutomaticProfileRepository",
    "RuleBasedAutomaticProfileExtractor",
    "SqliteAutomaticProfileRepository",
    "ProfileExtractionOutput",
    "PROFILE_SIGNAL_CLASSIFIER_VERSION",
    "ProfileSignalCategory",
    "ProfileSignalClassification",
    "ProfileSignalClassifier",
]
