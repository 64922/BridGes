"""Explicit sharing and minimized project copy domain module.

T036: This module implements the sharing service that coordinates share preview,
project copy creation, object grants, invite tokens and permission enforcement.
"""

from bridges.sharing.service import SharingService, SharingServiceError

__all__ = ["SharingService", "SharingServiceError"]
