"""Personal vault boundary and persistence ports."""

from bridges.vault.adapters import (
    DevicePairingService,
    FernetVaultEncryptionAdapter,
    InMemoryCloudControlProjectionStore,
    InMemoryDeviceKeychain,
    InMemoryDevicePairingRepository,
    InMemoryVaultRepository,
    MemoryDeviceVaultPort,
    UnavailableDeviceVaultPort,
    VaultError,
)
from bridges.vault.ports import (
    CloudControlProjectionStore,
    DeviceKeychainPort,
    DevicePairingRepository,
    DeviceVaultPort,
    VaultEncryptionPort,
    VaultRepository,
)
from bridges.vault.service import VaultService

__all__ = [
    "CloudControlProjectionStore",
    "DeviceKeychainPort",
    "DevicePairingRepository",
    "DeviceVaultPort",
    "VaultEncryptionPort",
    "VaultRepository",
    "DevicePairingService",
    "FernetVaultEncryptionAdapter",
    "InMemoryVaultRepository",
    "InMemoryCloudControlProjectionStore",
    "InMemoryDeviceKeychain",
    "InMemoryDevicePairingRepository",
    "MemoryDeviceVaultPort",
    "UnavailableDeviceVaultPort",
    "VaultService",
    "VaultError",
]
