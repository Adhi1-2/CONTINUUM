"""Round-trip tests for the optional event-chain attestation primitives."""

from __future__ import annotations

import pytest

from continuum.security.attestation import (
    ATTESTATION_ALGORITHM,
    Attestation,
    generate_keypair,
    sign_chain,
    verify_attestation,
)
from continuum.security.workload_identity import WorkloadIdentity


def test_sign_and_verify_round_trip() -> None:
    priv_pem, pub_pem = generate_keypair()
    attest = sign_chain(
        priv_pem,
        run_id="run_abc",
        trusted_through_seq=42,
        chain_hash="abc123",
        signer="ci-bot",
    )
    assert isinstance(attest, Attestation)
    assert attest.algorithm == ATTESTATION_ALGORITHM
    assert attest.public_key == pub_pem
    assert verify_attestation(attest) is True
    assert verify_attestation(attest, expected_chain_hash="abc123") is True


def test_verify_rejects_wrong_chain_hash() -> None:
    priv_pem, _pub_pem = generate_keypair()
    attest = sign_chain(priv_pem, "run_abc", 42, "abc123")
    assert verify_attestation(attest, expected_chain_hash="tampered") is False


def test_verify_rejects_tampered_payload() -> None:
    priv_pem, _pub_pem = generate_keypair()
    attest = sign_chain(priv_pem, "run_abc", 42, "abc123")
    tampered = Attestation(**{**attest.to_dict(), "trusted_through_seq": 43})
    assert verify_attestation(tampered) is False


def test_verify_rejects_substituted_public_key() -> None:
    priv_pem, _pub_pem = generate_keypair()
    _other_priv, other_pub = generate_keypair()
    attest = sign_chain(priv_pem, "run_abc", 42, "abc123")
    # An attacker swaps in a different public key but leaves the signature.
    swapped = Attestation(**{**attest.to_dict(), "public_key": other_pub})
    assert verify_attestation(swapped) is False


def test_sign_chain_rejects_a_non_ed25519_private_key() -> None:
    """An RSA private key PEM is refused, not silently signed (#2011)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    with pytest.raises(ValueError, match="is not an Ed25519 key"):
        sign_chain(rsa_pem, "run_abc", 42, "abc123")


def test_verify_rejects_an_altered_algorithm_field() -> None:
    """Anything other than the expected algorithm is rejected before signature work."""
    priv_pem, _pub_pem = generate_keypair()
    attest = sign_chain(priv_pem, "run_abc", 42, "abc123")
    swapped = Attestation(**{**attest.to_dict(), "algorithm": "rsa+sha256"})
    assert verify_attestation(swapped) is False


def test_verify_rejects_a_non_ed25519_public_key() -> None:
    """A well-formed RSA public key PEM fails the isinstance guard, not decoding."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv_pem, _pub_pem = generate_keypair()
    attest = sign_chain(priv_pem, "run_abc", 42, "abc123")
    rsa_pub_pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    swapped = Attestation(**{**attest.to_dict(), "public_key": rsa_pub_pem})
    assert verify_attestation(swapped) is False


def test_verify_require_workload_identity_rejects_a_missing_binding() -> None:
    """An attestation with no bound identity must not satisfy the requirement."""
    priv_pem, _pub_pem = generate_keypair()
    attest = sign_chain(priv_pem, "run_abc", 42, "abc123")
    assert verify_attestation(attest) is True
    assert verify_attestation(attest, require_workload_identity=True) is False


def test_verify_falls_back_to_no_identity_when_the_binding_is_invalid() -> None:
    """An unparseable workload mapping degrades to no binding instead of dying.

    ``sign_chain`` signs the raw mapping exactly as given, so the attestation
    itself verifies; the invalid claim simply carries no weight.
    """
    priv_pem, _pub_pem = generate_keypair()
    attest = sign_chain(priv_pem, "run_abc", 42, "abc123", workload_identity={"unrelated": True})
    assert verify_attestation(attest) is True
    assert verify_attestation(attest, require_workload_identity=True) is False


def test_verify_expected_workload_identity_matches_by_spiffe_id() -> None:
    """The bound identity must match ``expected_workload_identity``, string or object."""
    priv_pem, _pub_pem = generate_keypair()
    wid = WorkloadIdentity(spiffe_id="spiffe://example.org/ns/prod/sa/agent")
    attest = sign_chain(priv_pem, "run_wid", 7, "cafe123", workload_identity=wid)

    assert verify_attestation(attest, expected_workload_identity=wid) is True
    assert (
        verify_attestation(
            attest, expected_workload_identity="spiffe://example.org/ns/prod/sa/agent"
        )
        is True
    )
    assert (
        verify_attestation(attest, expected_workload_identity="spiffe://other.test/sa/agent")
        is False
    )
    unbound = Attestation(**{**attest.to_dict(), "workload_identity": None})
    assert verify_attestation(unbound, expected_workload_identity=wid) is False


class _RecordingProvider:
    """Stand-in for a WorkloadIdentityProvider with a fixed verdict."""

    def __init__(self, verdict: bool) -> None:
        self.verdict = verdict
        self.seen: WorkloadIdentity | None = None

    def verify_identity(self, identity: WorkloadIdentity) -> bool:
        self.seen = identity
        return self.verdict


def test_verify_identity_provider_accepts_and_rejects() -> None:
    """A provider validates the parsed binding: its verdict decides the outcome."""
    priv_pem, _pub_pem = generate_keypair()
    wid = WorkloadIdentity(spiffe_id="spiffe://example.org/ns/prod/sa/agent")
    attest = sign_chain(priv_pem, "run_wid", 7, "cafe123", workload_identity=wid)

    accepting = _RecordingProvider(verdict=True)
    assert verify_attestation(attest, identity_provider=accepting) is True
    assert isinstance(accepting.seen, WorkloadIdentity)
    assert accepting.seen.spiffe_id == wid.spiffe_id

    rejecting = _RecordingProvider(verdict=False)
    assert verify_attestation(attest, identity_provider=rejecting) is False

    # A provider with no bound identity to check refuses rather than downgrades.
    unbound = sign_chain(priv_pem, "run_plain", 8, "beef456")
    assert verify_attestation(unbound, identity_provider=_RecordingProvider(verdict=True)) is False
