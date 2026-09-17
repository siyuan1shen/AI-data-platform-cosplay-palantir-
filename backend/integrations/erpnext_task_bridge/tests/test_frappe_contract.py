import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "enterprise_insight_task_bridge"


class FrappeContractTests(unittest.TestCase):
    def test_api_methods_have_exact_http_verb_contract(self):
        tree = ast.parse((PACKAGE / "api.py").read_text(encoding="utf-8"))
        decorators = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(
                        decorator.func, ast.Attribute
                    ):
                        if decorator.func.attr == "whitelist":
                            methods = next(
                                keyword.value
                                for keyword in decorator.keywords
                                if keyword.arg == "methods"
                            )
                            decorators[node.name] = ast.literal_eval(methods)
        self.assertEqual(decorators["execute_task_operation"], ["POST"])
        self.assertEqual(decorators["get_task_operation"], ["GET"])

    def test_api_contract_contains_both_action_aliases_and_digest_fields(self):
        tree = ast.parse((PACKAGE / "api.py").read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute_task_operation"
        )
        arguments = {argument.arg for argument in function.args.args}
        self.assertTrue(
            {
                "operation_id",
                "operation",
                "action",
                "payload",
                "payload_sha256",
                "expected_modified",
            }
            <= arguments
        )

    def test_task_operation_doctype_has_database_unique_key_and_result(self):
        doctype = json.loads(
            (
                PACKAGE
                / "enterprise_insight_task_bridge"
                / "doctype"
                / "task_operation"
                / "task_operation.json"
            ).read_text(encoding="utf-8")
        )
        fields = {field["fieldname"]: field for field in doctype["fields"]}
        self.assertEqual(doctype["autoname"], "field:operation_id")
        self.assertNotIn("unique", fields["operation_id"])
        self.assertEqual(fields["response_json"]["fieldtype"], "Long Text")
        self.assertIn("payload_sha256", fields)
        self.assertIn("status", fields)

    def test_app_metadata_is_installable_and_declares_erpnext_dependency(self):
        hooks = ast.parse((PACKAGE / "hooks.py").read_text(encoding="utf-8"))
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in hooks.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }
        self.assertEqual(assignments["app_name"], "enterprise_insight_task_bridge")
        self.assertEqual(assignments["required_apps"], ["frappe", "erpnext"])
        self.assertEqual(
            (PACKAGE / "modules.txt").read_text(encoding="utf-8").strip(),
            "Enterprise Insight Task Bridge",
        )

    def test_all_python_sources_parse_without_bytecode_or_site_dependencies(self):
        python_files = list(ROOT.rglob("*.py"))
        self.assertGreaterEqual(len(python_files), 8)
        for path in python_files:
            with self.subTest(path=path.relative_to(ROOT)):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
