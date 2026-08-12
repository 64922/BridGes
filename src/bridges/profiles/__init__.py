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
    PROFILE_CORRECTION_RULES_VERSION,
    PROFILE_REPLAY_QUEUE,
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
from bridges.profiles.replay import (
    DatabaseIdentity,
    ProfileReplayCoordinator,
    ProfileReplayError,
    ProfileReplayReport,
    VerifiedBackup,
    create_verified_backup,
    load_runtime_application_state,
    resolve_authoritative_database,
    upgrade_authoritative_schema,
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
    "PROFILE_REPLAY_QUEUE",
    "PROFILE_CORRECTION_RULES_VERSION",
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
    "DatabaseIdentity",
    "ProfileReplayCoordinator",
    "ProfileReplayError",
    "ProfileReplayReport",
    "VerifiedBackup",
    "create_verified_backup",
    "load_runtime_application_state",
    "resolve_authoritative_database",
    "upgrade_authoritative_schema",
]
