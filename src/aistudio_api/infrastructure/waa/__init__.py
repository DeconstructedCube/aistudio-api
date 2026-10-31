"""WAA (Web Authentication / Attestation) signing engine."""

from aistudio_api.infrastructure.waa.challenge import (
    Challenge,
    compute_interpreter_hash,
    fetch_challenge,
    fetch_interpreter_code,
    parse_challenge_payload,
)
from aistudio_api.infrastructure.waa.signer import (
    RemoteProofSigner,
    WaaSigner,
    extract_content_digest,
)

__all__ = [
    "Challenge",
    "RemoteProofSigner",
    "WaaSigner",
    "compute_interpreter_hash",
    "extract_content_digest",
    "fetch_challenge",
    "fetch_interpreter_code",
    "parse_challenge_payload",
]
