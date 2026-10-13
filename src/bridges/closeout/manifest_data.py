"""按分类组合生产能力清单。"""

from bridges.closeout.manifest_contracts import CapabilityManifestEntry
from bridges.closeout.manifest_local_entries import LOCAL_AND_RETIRED_CAPABILITIES
from bridges.closeout.manifest_model_entries import MODEL_AND_EXTERNAL_CAPABILITIES

PRODUCTION_CAPABILITY_MANIFEST: tuple[CapabilityManifestEntry, ...] = (
    *MODEL_AND_EXTERNAL_CAPABILITIES,
    *LOCAL_AND_RETIRED_CAPABILITIES,
)
