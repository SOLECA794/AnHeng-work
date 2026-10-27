from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fastapi import HTTPException
from src.auth.d_principal import create_d_principal_dependency
from src.auth.service import User

USER = User("fixture-ops", "unused-hash", "SEC_OPS", id=1)


class DPrincipalTests(unittest.TestCase):
    def test_verified_user_and_server_grants_become_principal(self):
        seen = []
        def grants(identifier):
            seen.append(identifier)
            return frozenset({2, 3})
        principal = create_d_principal_dependency(grants)(USER)
        self.assertEqual((principal.user_id, principal.role, principal.scope_ids), (1, "SEC_OPS", frozenset({2, 3})))
        self.assertEqual(seen, [1])

    def test_no_resolver_produces_no_asset_grants(self):
        self.assertEqual(create_d_principal_dependency()(USER).scope_ids, frozenset())

    def test_each_call_refreshes_grants(self):
        grants = {1: frozenset({1})}
        dependency = create_d_principal_dependency(lambda identifier: grants[identifier])
        self.assertEqual(dependency(USER).scope_ids, frozenset({1}))
        grants[1] = frozenset()
        self.assertEqual(dependency(USER).scope_ids, frozenset())

    def test_invalid_user_is_rejected_before_reading_grants(self):
        def forbidden(_):
            self.fail("invalid user must not reach grant storage")
        for user in [None, replace(USER, id=None), replace(USER, id=True), replace(USER, id=0),
                     replace(USER, id=2**63), replace(USER, enabled=False)]:
            with self.subTest(user=user), self.assertRaises(HTTPException) as error:
                create_d_principal_dependency(forbidden)(user)
            self.assertEqual(error.exception.status_code, 401)

    def test_admin_and_unknown_roles_have_no_business_privilege(self):
        def forbidden(_):
            self.fail("unauthorized role must not reach grant storage")
        for role in ("ADMIN", "UNKNOWN"):
            with self.subTest(role=role), self.assertRaises(HTTPException) as error:
                create_d_principal_dependency(forbidden)(replace(USER, role=role))
            self.assertEqual(error.exception.status_code, 403)

    def test_rule_admin_can_receive_read_grants(self):
        principal = create_d_principal_dependency(lambda _: frozenset({1}))(replace(USER, role="RULE_ADMIN"))
        self.assertEqual(principal.role, "RULE_ADMIN")

    def test_invalid_grants_fail_safely(self):
        for value in [None, [], {1}, (1,), frozenset({True}), frozenset({0}),
                      frozenset({-1}), frozenset({"1"}), frozenset({2**63})]:
            with self.subTest(value=value), self.assertRaises(HTTPException) as error:
                create_d_principal_dependency(lambda _: value)(USER)
            self.assertEqual(error.exception.status_code, 500)

    def test_resolver_exception_text_is_not_exposed(self):
        def broken(_):
            raise RuntimeError("fixture-private-token")
        with self.assertRaises(HTTPException) as error:
            create_d_principal_dependency(broken)(USER)
        self.assertEqual(error.exception.status_code, 500)
        self.assertNotIn("fixture-private-token", str(error.exception))

    def test_async_or_noncallable_configuration_is_rejected(self):
        async def async_grants(_):
            return frozenset({1})
        for value in (async_grants, {}, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                create_d_principal_dependency(value)


if __name__ == "__main__":
    unittest.main()
