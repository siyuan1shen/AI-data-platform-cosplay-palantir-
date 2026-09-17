import frappe
from frappe.model.document import Document

TERMINAL_STATUSES = {"COMMITTED", "NOT_FOUND", "CONFLICT", "REJECTED", "FAILED"}
IMMUTABLE_FIELDS = (
    "operation_id",
    "action",
    "payload_sha256",
    "expected_modified",
    "requested_by",
    "requested_on",
)


class TaskOperation(Document):
    def validate(self):
        if self.status not in TERMINAL_STATUSES | {"PROCESSING"}:
            frappe.throw("Unsupported Task Operation status")

        previous = self.get_doc_before_save()
        if not previous:
            if self.status != "PROCESSING":
                frappe.throw("A Task Operation must be registered as PROCESSING first")
            return

        for fieldname in IMMUTABLE_FIELDS:
            if self.get(fieldname) != previous.get(fieldname):
                frappe.throw(
                    "Task Operation identity fields are immutable", exc=frappe.PermissionError
                )

        if previous.status != "PROCESSING":
            frappe.throw("A finalized Task Operation is immutable", exc=frappe.PermissionError)

        if self.status == "PROCESSING":
            frappe.throw(
                "A Task Operation can only transition from PROCESSING to a terminal status"
            )
