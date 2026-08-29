"""Offline regression tests for the Siyakhokha API client."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any


API_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "siyakhokha_bridge"
    / "api.py"
)
SPEC = importlib.util.spec_from_file_location("siyakhokha_test_api", API_PATH)
assert SPEC and SPEC.loader
API_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = API_MODULE
SPEC.loader.exec_module(API_MODULE)
SiyakhokhaApi = API_MODULE.SiyakhokhaApi
SiyakhokhaApiError = API_MODULE.SiyakhokhaApiError


class ScriptedApi(SiyakhokhaApi):
    """Client whose mobile responses are supplied by each test."""

    def __init__(self, responses: dict[str, Any]) -> None:
        super().__init__("https://example.invalid")
        self.responses = responses
        self.mobile_calls: list[tuple[str, str]] = []
        self.portal_calls: list[tuple[str, str]] = []

    def _mobile_json(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        self.mobile_calls.append((method, path))
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response

    def _portal_request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        self.portal_calls.append((method, path))
        raise AssertionError("The portal must not be used by this test")


class LoginAndAccountTests(unittest.TestCase):
    def test_login_uses_only_customer_and_account_json_routes(self) -> None:
        api = ScriptedApi(
            {
                "/api/mobile/customer": {"systemUserId": 7},
                "/api/mobile/latestaccounts": [{"accountNumber": "100"}],
                "/api/mobile/accounts": [
                    {"accountId": 42, "account": {"accountNumber": "100"}}
                ],
            }
        )

        api.login("user", "password")

        self.assertEqual(
            api.mobile_calls,
            [
                ("GET", "/api/mobile/customer"),
                ("GET", "/api/mobile/latestaccounts"),
                ("GET", "/api/mobile/accounts"),
            ],
        )
        self.assertEqual(api.portal_calls, [])
        self.assertEqual(api.get_account_list()[0]["account_id"], 42)

    def test_login_rejects_invalid_customer_payload(self) -> None:
        api = ScriptedApi({"/api/mobile/customer": {"firstName": "No ID"}})

        with self.assertRaisesRegex(SiyakhokhaApiError, "invalid.*customer"):
            api.login("user", "password")

    def test_account_merge_and_richer_route_fallback(self) -> None:
        api = ScriptedApi(
            {
                "/api/mobile/customer": {
                    "systemUserId": 7,
                    "firstName": "Test",
                    "physicalAddress1": "Example street",
                },
                "/api/mobile/latestaccounts": [
                    {
                        "accountNumber": "100",
                        "description": "Current description",
                        "accountHolder": "Current holder",
                    }
                ],
                "/api/mobile/accounts": [
                    {
                        "accountId": 42,
                        "account": {
                            "id": 41,
                            "accountNumber": "100",
                            "description": "Old description",
                        },
                    }
                ],
            }
        )
        api.login("user", "password")

        account = api.get_account_list()[0]
        self.assertEqual(account["account_id"], 42)
        self.assertEqual(account["description"], "Current description")
        self.assertEqual(account["account_holder"], "Current holder")
        self.assertEqual(account["customer"]["first_name"], "Test")

        fallback = ScriptedApi(
            {
                "/api/mobile/customer": {"systemUserId": 7},
                "/api/mobile/latestaccounts": [
                    {"accountNumber": "200", "description": "Fallback"}
                ],
                "/api/mobile/accounts": SiyakhokhaApiError("disabled"),
            }
        )
        fallback.login("user", "password")
        self.assertEqual(fallback.get_account_list()[0]["account_number"], "200")


    def test_account_balance_preserves_zero_without_raw_upstream_data(self) -> None:
        api = ScriptedApi({})
        api._accounts = [
            {
                "accountNumber": "100",
                "latestAmountDue": 0.0,
                "dueDate": "2026-09-01",
                "unexpectedSensitiveField": "must-not-leak",
            }
        ]

        balance = api.get_account_balance()[0]

        self.assertEqual(balance["account_number"], "100")
        self.assertEqual(balance["payable"], 0.0)
        self.assertEqual(balance["due_date"], "2026-09-01")
        self.assertNotIn("raw", balance)
        self.assertNotIn("unexpectedSensitiveField", str(balance))


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history = {
            "paymentHistories": [
                {
                    "id": 1,
                    "amountPaid": 0.0,
                    "amount": 99.0,
                    "paymentDateTime": "2026-01-02",
                    "paymentType": "Card",
                }
            ],
            "eftPaymentHistories": [],
            "mpPaymentHistories": [],
            "debitOrderPaymentHistories": [
                {
                    "id": 2,
                    "accountId": 22,
                    "amount": 123.45,
                    "bankAccountId": 11,
                    "isBatch": False,
                    "startDateTime": "2026-02-01",
                    "strikeDay": 7,
                    "status": {"id": 3, "name": "Active"},
                    "bankAccount": {
                        "id": 11,
                        "bankAccountNumber": "full-number-must-not-leak",
                        "hiddenBankAccountNumber": "****1234",
                        "bank": {"id": 5, "name": "Example Bank"},
                    },
                },
                {
                    "id": 3,
                    "accountId": 22,
                    "bankAccountId": 11,
                    "strikeDay": 7,
                    "bankAccount": {
                        "id": 11,
                        "bankAccountNumber": "also-secret",
                        "hiddenBankAccountNumber": "****1234",
                        "bank": {"id": 5, "name": "Example Bank"},
                    },
                },
            ],
            "batchDebitOrderPaymentHistories": [
                {
                    "id": 4,
                    "batchNumber": "B-1",
                    "createdDateTime": "2026-03-01",
                    "status": {"name": "Submitted"},
                    "debitOrder": {
                        "amount": 50.0,
                        "startDateTime": "2026-03-02",
                        "strikeDay": 2,
                    },
                }
            ],
        }
        self.api = ScriptedApi(
            {"/api/mobile/getpaymenthistory": self.history}
        )
        self.api._customer = {"systemUserId": 7}
        self.api._accounts = [
            {"accountNumber": "100", "description": "Municipal account"}
        ]
        self.api._linked_accounts = [
            {"accountId": 22, "account": {"accountNumber": "100"}}
        ]

    def test_one_fetch_serves_all_history_views_and_preserves_shapes(self) -> None:
        payments = self.api.get_payment_history()
        debits = self.api.get_debit_orders()
        batches = self.api.get_batch_orders()

        self.assertEqual(
            self.api.mobile_calls,
            [("GET", "/api/mobile/getpaymenthistory")],
        )
        self.assertEqual(payments["data"][0]["AmountPaid"], 0.0)
        self.assertEqual(payments["data"][0]["PaymentType"], "Card")
        self.assertEqual(debits["data"][0]["Status"]["Name"], "Active")
        bank = debits["data"][0]["BankAccount"]
        self.assertEqual(bank["HiddenBankAccountNumber"], "****1234")
        self.assertNotIn("BankAccountNumber", bank)
        self.assertEqual(batches["data"][0]["BatchNumber"], "B-1")
        self.assertEqual(batches["data"][0]["DebitOrder"]["Amount"], 50.0)

    def test_api_debit_context_deduplicates_and_selects_options(self) -> None:
        context = self.api.get_debit_order_context()

        self.assertEqual(len(context["bank_accounts"]), 1)
        self.assertEqual(context["bank_accounts"][0]["id"], 11)
        self.assertTrue(context["bank_accounts"][0]["selected"])
        self.assertEqual(context["municipal_accounts"][0]["id"], 22)
        self.assertTrue(context["municipal_accounts"][0]["selected"])
        self.assertEqual(context["resolved"]["strike_day"], 7)
        self.assertTrue(context["portal_required_for_submission"])
        self.assertEqual(self.api.portal_calls, [])


class PortalAndPdfTests(unittest.TestCase):
    def test_pdf_token_is_normalized_once_and_non_pdf_retries_once(self) -> None:
        class PdfApi(SiyakhokhaApi):
            def __init__(self) -> None:
                super().__init__("https://example.invalid")
                self.paths: list[str] = []
                self.session_checks = 0

            def _ensure_portal_session(self) -> None:
                self.session_checks += 1
                self._portal_logged_in = True

            def _portal_request_bytes(
                self,
                method: str,
                path: str,
                data: bytes | None = None,
                headers: dict[str, str] | None = None,
            ) -> bytes:
                self.paths.append(path)
                return b"login page" if len(self.paths) == 1 else b"%PDF-test"

        api = PdfApi()
        result = api.download_bill("abc%2Fdef+ghi")

        self.assertEqual(result, b"%PDF-test")
        self.assertEqual(api.session_checks, 2)
        self.assertEqual(
            api.paths,
            [
                "/Report/GenerateBill?q=abc%2Fdef%2Bghi",
                "/Report/GenerateBill?q=abc%2Fdef%2Bghi",
            ],
        )

    def test_portal_login_accepts_profile_and_rejects_returned_login_form(self) -> None:
        class PortalApi(SiyakhokhaApi):
            def __init__(self, post_response: str) -> None:
                super().__init__("https://example.invalid")
                self._username = "user"
                self._password = "password"
                self.post_response = post_response

            def _portal_request(
                self,
                method: str,
                path: str,
                data: bytes | None = None,
                headers: dict[str, str] | None = None,
            ) -> str:
                if method == "GET":
                    return '<input name="__RequestVerificationToken" value="csrf">'
                return self.post_response

        valid = PortalApi("<html><h1>Profile</h1></html>")
        valid._ensure_portal_session()
        self.assertTrue(valid._portal_logged_in)

        invalid = PortalApi('<form action="/Account/Login" method="post"></form>')
        with self.assertRaisesRegex(SiyakhokhaApiError, "credentials were rejected"):
            invalid._ensure_portal_session()
        self.assertFalse(invalid._portal_logged_in)


    def test_bulk_context_and_payload_match_current_portal_javascript(self) -> None:
        from urllib.parse import parse_qs

        page = """
        <input name="CusId" value="123">
        <select id="BankAccountDdl"><option value="456">Bank</option></select>
        <script>
        var BatchYear = 2026;
        var BatchMonth = 8;
        BatchMonth = "0" + BatchMonth;
        var BatchDay = 29;
        BatchDay = "0" + BatchDay;
        var BatchHour = 22;
        var BatchMinute = 18;
        var BatchSec = 20;
        var BatchSSec = 728;
        var BatchBool = 1;
        var BatchstrikeDate = 29;
        var BatchDateFormat = "." + BatchYear + BatchMonth + BatchDay
            + BatchHour + BatchMinute + BatchSec + BatchSSec
            + BatchBool + BatchstrikeDate;
        document.getElementById("startD").value =
            BatchYear + "-" + BatchMonth + "-" + BatchDay;
        </script>
        """

        class BulkApi(SiyakhokhaApi):
            def __init__(self) -> None:
                super().__init__("https://example.invalid")
                self.post_data: bytes | None = None

            def _ensure_portal_session(self) -> None:
                self._portal_logged_in = True

            def _portal_request(
                self,
                method: str,
                path: str,
                data: bytes | None = None,
                headers: dict[str, str] | None = None,
            ) -> str:
                if method == "GET":
                    return page
                self.post_data = data
                return '{"accepted": true}'

        api = BulkApi()
        context = api.get_bulk_payment_context()
        self.assertEqual(context["dStrike"], "29")
        self.assertEqual(context["startDate"], "2026-08-29")
        self.assertEqual(context["bat"], ".20260829221820728129")

        result = api.submit_bulk_payment(
            account_numbers=["100", "200"],
            amounts=[50.0, 75.25],
            context=context,
        )
        self.assertTrue(result["ok"])
        self.assertIsNotNone(api.post_data)
        payload = parse_qs(api.post_data.decode("utf-8"), keep_blank_values=True)
        self.assertEqual(payload["ControllerDebitAmount[]"], ["50.00", "75.25"])
        self.assertEqual(payload["MAccounts[]"], ["100", "200"])
        self.assertEqual(payload["CusId"], ["123"])
        self.assertEqual(payload["BId"], ["456"])
        self.assertEqual(payload["startDate"], ["2026-08-29"])
        self.assertEqual(payload["bat"], [".20260829221820728129"])
        self.assertNotIn("dStrike", payload)


if __name__ == "__main__":
    unittest.main()
