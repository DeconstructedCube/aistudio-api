"""WAA (Web Authentication / Attestation) local IPC signing engine.

Communicates with the local SpiderMonkey 152 waa-signer binary over multiplexed
stdio NDJSON IPC without opening any local HTTP port or remote relay.
"""

from aistudio_api.infrastructure.waa.signer import (
    LocalIpcProofSigner,
    WaaSigner,
    extract_content_digest,
)

__all__ = [
    "LocalIpcProofSigner",
    "WaaSigner",
    "extract_content_digest",
]
