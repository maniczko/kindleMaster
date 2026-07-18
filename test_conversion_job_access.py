import unittest
from unittest.mock import patch

from conversion_job_access import (
    GUEST_OWNER_FIELD,
    USER_OWNER_FIELD,
    InvalidAuthenticatedIdentity,
    InvalidGuestIdentity,
    MissingGuestIdentity,
    apply_job_owner,
    guest_owner_id,
    is_local_request_host,
    job_owned_by,
    normalize_guest_id,
    owner_scope,
    resolve_job_owner,
)


class ConversionJobAccessTests(unittest.TestCase):
    def test_authenticated_owner_requires_user_id(self) -> None:
        with self.assertRaises(InvalidAuthenticatedIdentity):
            resolve_job_owner(authenticated=True, user_id="", request_host="api.example.com")

    def test_authenticated_owner_is_exact_and_does_not_claim_legacy_jobs(self) -> None:
        owner = resolve_job_owner(authenticated=True, user_id="user-a", request_host="api.example.com")

        self.assertTrue(job_owned_by({USER_OWNER_FIELD: "user-a"}, owner))
        self.assertFalse(job_owned_by({USER_OWNER_FIELD: "user-b"}, owner))
        self.assertFalse(job_owned_by({}, owner))
        self.assertEqual(owner_scope(owner), "account")

    def test_guest_identity_is_hashed_before_it_is_stored(self) -> None:
        raw_guest_id = "guest-session-0123456789abcdef"
        owner = resolve_job_owner(
            authenticated=False,
            guest_id=raw_guest_id,
            request_host="api.example.com",
        )
        job: dict[str, object] = {}

        apply_job_owner(job, owner)

        self.assertEqual(job[GUEST_OWNER_FIELD], guest_owner_id(raw_guest_id))
        self.assertNotEqual(job[GUEST_OWNER_FIELD], raw_guest_id)
        self.assertNotIn(USER_OWNER_FIELD, job)
        self.assertEqual(owner_scope(owner), "guest")

    def test_guest_cannot_claim_another_guest_or_legacy_job(self) -> None:
        owner_a = resolve_job_owner(
            authenticated=False,
            guest_id="guest-session-aaaaaaaaaaaaaaaa",
            request_host="api.example.com",
        )
        owner_b = resolve_job_owner(
            authenticated=False,
            guest_id="guest-session-bbbbbbbbbbbbbbbb",
            request_host="api.example.com",
        )
        job: dict[str, object] = {}
        apply_job_owner(job, owner_a)

        self.assertTrue(job_owned_by(job, owner_a))
        self.assertFalse(job_owned_by(job, owner_b))
        self.assertFalse(job_owned_by({}, owner_a))

    def test_public_guest_without_identity_is_rejected(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(MissingGuestIdentity):
                resolve_job_owner(authenticated=False, request_host="api.example.com")

    def test_localhost_preserves_explicit_single_user_legacy_mode(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            owner = resolve_job_owner(authenticated=False, request_host="localhost:5001")

        self.assertTrue(job_owned_by({}, owner))
        self.assertFalse(job_owned_by({USER_OWNER_FIELD: "user-a"}, owner))
        self.assertFalse(job_owned_by({GUEST_OWNER_FIELD: "guest:abc"}, owner))
        self.assertEqual(owner_scope(owner), "local")

    def test_legacy_local_mode_can_be_disabled_explicitly(self) -> None:
        with patch.dict("os.environ", {"KINDLEMASTER_ALLOW_LEGACY_LOCAL_GUEST": "0"}, clear=True):
            with self.assertRaises(MissingGuestIdentity):
                resolve_job_owner(authenticated=False, request_host="localhost:5001")

    def test_invalid_guest_identity_is_rejected(self) -> None:
        with self.assertRaises(InvalidGuestIdentity):
            normalize_guest_id("short")
        with self.assertRaises(InvalidGuestIdentity):
            normalize_guest_id("guest identity with spaces and unsafe data")

    def test_local_host_detection_handles_ports_and_ipv6(self) -> None:
        self.assertTrue(is_local_request_host("127.0.0.1:5001"))
        self.assertTrue(is_local_request_host("[::1]:5001"))
        self.assertTrue(is_local_request_host("kindlemaster.localhost:5001"))
        self.assertFalse(is_local_request_host("kindlemaster-production.up.railway.app"))


if __name__ == "__main__":
    unittest.main()
