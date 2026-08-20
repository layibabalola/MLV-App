"""Pure semantic checks for the zero-authority autonomous-golden contract.

This module does not promote files, verify broker signatures, manage trusted
keys, or confer authority.  A separately reviewed installed broker must call
equivalent checks with its pinned signer registry and one-use ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import AbstractSet, Any, Mapping


MAX_SIGNER_RECEIPT_LIFETIME_SECONDS = 2 * 60 * 60
EXPECTED_SIGNER_COUNT = {
    "A_REPRESENTATION_ONLY": 3,
    "B_MECHANICALLY_PREDICTED": 3,
    "C_AESTHETIC_DEFAULT_OR_AMBIGUOUS": 5,
}
LEGAL_PROMOTION_EDGES = {
    (None, "PREPARED"),
    ("PREPARED", "SHADOW_PASSED"),
    ("SHADOW_PASSED", "COMMITTED"),
    ("PREPARED", "QUARANTINED"),
    ("SHADOW_PASSED", "QUARANTINED"),
    ("COMMITTED", "ROLLED_BACK"),
    ("ROLLED_BACK", "QUARANTINED"),
}


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return parsed.replace(tzinfo=timezone.utc)


def signer_commitment(receipt: Mapping[str, Any]) -> str:
    """Return the normative commit/reveal digest for a signer receipt."""

    fields = (
        receipt["proposalDigest"],
        receipt["policyDigest"],
        receipt["evidenceDigest"],
        receipt["signerId"],
        receipt["verdict"],
        receipt["revealNonce"],
    )
    return _sha256_text("\n".join(fields))


def validate_signer_receipt_semantics(
    receipt: Mapping[str, Any],
    *,
    consumed_receipt_ids: AbstractSet[str] = frozenset(),
) -> None:
    """Fail closed on semantic relationships JSON Schema cannot express."""

    signer_id = receipt["signerId"]
    if signer_id in {receipt["proposerId"], receipt["brokerId"]}:
        raise ValueError("signer must differ from proposer and broker")
    if receipt["proposerId"] == receipt["brokerId"]:
        raise ValueError("proposer and broker must be distinct")
    if receipt["receiptId"] in consumed_receipt_ids:
        raise ValueError("signer receipt replayed")
    issued = _parse_utc(receipt["issuedAtUtc"])
    expires = _parse_utc(receipt["expiresAtUtc"])
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0 or lifetime > MAX_SIGNER_RECEIPT_LIFETIME_SECONDS:
        raise ValueError("signer receipt lifetime invalid")
    if receipt["commitNonceSha256"] != _sha256_text(receipt["revealNonce"]):
        raise ValueError("commit nonce does not bind reveal nonce")
    if receipt["committedVerdictSha256"] != signer_commitment(receipt):
        raise ValueError("verdict commitment mismatch")


def validate_promotion_receipt_semantics(receipt: Mapping[str, Any]) -> None:
    """Validate legal transition and decision-class quorum cardinality."""

    edge = (receipt["previousState"], receipt["state"])
    if edge not in LEGAL_PROMOTION_EDGES:
        raise ValueError(f"illegal promotion edge: {edge!r}")
    expected = EXPECTED_SIGNER_COUNT[receipt["decisionClass"]]
    if len(receipt["signerReceiptDigests"]) != expected:
        raise ValueError("signer receipt cardinality does not match decision class")
