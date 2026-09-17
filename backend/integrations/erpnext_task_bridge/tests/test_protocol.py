import hashlib
import unittest

from enterprise_insight_task_bridge.protocol import (
    ProtocolError,
    compute_payload_sha256,
    normalize_action,
    parse_payload,
    status_transition_allowed,
    validate_digest,
    validate_operation_id,
    validate_payload,
)


class ProtocolTests(unittest.TestCase):
    def test_canonical_digest_ignores_object_key_order_and_spacing(self):
        first = parse_payload('{"subject":"需求评审","priority":"High"}')
        second = parse_payload('{ "priority" : "High", "subject": "需求评审" }')
        self.assertEqual(compute_payload_sha256(first), compute_payload_sha256(second))
        self.assertEqual(
            validate_digest(first, compute_payload_sha256(second)),
            compute_payload_sha256(first),
        )

    def test_digest_mismatch_is_rejected(self):
        payload = {"subject": "Plan"}
        with self.assertRaisesRegex(ProtocolError, "does not match"):
            validate_digest(payload, hashlib.sha256(b"different").hexdigest())

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(ProtocolError) as raised:
            parse_payload('{"subject":"one","subject":"two"}')
        self.assertEqual(raised.exception.code, "DUPLICATE_JSON_KEY")

    def test_non_finite_numbers_are_rejected(self):
        with self.assertRaises(ProtocolError):
            parse_payload('{"progress":NaN}')

    def test_action_aliases_are_consistent(self):
        self.assertEqual(normalize_action("update", " UPDATE "), "UPDATE")
        with self.assertRaises(ProtocolError):
            normalize_action("CREATE", "CANCEL")

    def test_operation_id_is_bounded_and_safe(self):
        self.assertEqual(validate_operation_id("bridge:op-12.a"), "bridge:op-12.a")
        for value in ("", "../x", "contains space", "x" * 129, None):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                validate_operation_id(value)

    def test_create_requires_subject_and_disallows_caller_selected_status(self):
        with self.assertRaises(ProtocolError):
            validate_payload("CREATE", {"project": "PROJ-1"}, None)
        with self.assertRaisesRegex(ProtocolError, "not allowed"):
            validate_payload("CREATE", {"subject": "Task", "status": "Completed"}, None)
        validate_payload("CREATE", {"subject": "Task", "priority": "High"}, None)

    def test_update_requires_name_field_allowlist_and_expected_modified(self):
        with self.assertRaises(ProtocolError):
            validate_payload("UPDATE", {"name": "TASK-1", "priority": "High"}, None)
        with self.assertRaises(ProtocolError):
            validate_payload(
                "UPDATE",
                {"name": "TASK-1", "sql": "delete from tabTask"},
                "2026-09-13 10:00:00",
            )
        validate_payload(
            "UPDATE",
            {"name": "TASK-1", "priority": "High"},
            "2026-09-13 10:00:00.000000",
        )

    def test_read_and_cancel_have_exact_target_shape(self):
        validate_payload("READ", {"name": "TASK-1"}, None)
        validate_payload("CANCEL", {"name": "TASK-1"}, "2026-09-13 10:00:00")
        with self.assertRaises(ProtocolError):
            validate_payload("READ", {"name": "TASK-1", "fields": ["*"]}, None)

    def test_field_types_ranges_and_native_values_are_checked(self):
        invalid_updates = (
            {"name": "TASK-1", "progress": 101},
            {"name": "TASK-1", "expected_time": float("inf")},
            {"name": "TASK-1", "priority": "Critical"},
            {"name": "TASK-1", "exp_start_date": "13/09/2026"},
            {"name": "TASK-1", "is_milestone": 1},
        )
        for payload in invalid_updates:
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                validate_payload("UPDATE", payload, "2026-09-13 10:00:00")

    def test_status_transitions_are_explicit_and_terminal(self):
        self.assertTrue(status_transition_allowed("Open", "Working"))
        self.assertTrue(status_transition_allowed("Overdue", "Completed"))
        self.assertFalse(status_transition_allowed("Open", "Completed"))
        self.assertFalse(status_transition_allowed("Completed", "Cancelled"))
        self.assertTrue(status_transition_allowed("Cancelled", "Cancelled"))


if __name__ == "__main__":
    unittest.main()
