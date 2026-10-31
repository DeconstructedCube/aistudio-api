"""WAA (Web Authentication / Attestation) signing engine.

Connects to aistudio-pg-bridge (https://github.com/Lianues/aistudio-pg-bridge)
or compatible WAA proof minting API endpoints.
"""

from aistudio_api.infrastructure.waa.signer import (
    RemoteProofSigner,
    WaaSigner,
    extract_content_digest,
)

__all__ = [
    "RemoteProofSigner",
    "WaaSigner",
    "extract_content_digest",
]
