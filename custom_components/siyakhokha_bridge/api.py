"""API-first Siyakhokha client for the Home Assistant integration.

Normal polling uses only the official Basic-auth mobile JSON API documented at
``/swagger/docs/v1``.  The ASP.NET portal session is created lazily and is used
only for features that the current API does not provide reliably: statement PDF
downloads and on-demand debit-order submissions.

Flow:
  1. ``login()``      GET /api/mobile/customer          (Basic) -> customer
                      GET /api/mobile/latestaccounts    (Basic) -> balances
                      GET /api/mobile/accounts          (Basic) -> linked account ids
  2. bills            GET /api/mobile/billlist          (Basic) -> statement rows
  3. history          GET /api/mobile/getpaymenthistory (Basic) -> all payment/debit data
  4. PDFs/writes      POST /Account/Login (lazy) and use the current portal forms

The legacy ``/mobilepayment/paymenthistory`` HTML token bootstrap and the
``Load*?q=`` history routes are deliberately not used.
"""

from __future__ import annotations

import base64
import json
import re
from io import BytesIO
from http.cookiejar import CookieJar
from datetime import datetime
from typing import Any
from urllib.parse import quote, unquote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from pypdf import PdfReader


class SiyakhokhaApiError(Exception):
    """Raised when API communication fails."""


class SiyakhokhaApi:
    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Stateless client used for the mobile JSON API (Basic auth header only).
        self._mobile_opener = build_opener()
        # Cookie jar used for the lazy ASP.NET portal session (PDFs + submissions).
        self._cookie_jar = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar))
        self._username: str | None = None
        self._password: str | None = None
        self._accounts: list[dict[str, Any]] | None = None
        self._linked_accounts: list[dict[str, Any]] | None = None
        self._customer: dict[str, Any] | None = None
        self._payment_history_payload: dict[str, Any] | None = None
        self._portal_logged_in = False

    # ------------------------------------------------------------------ #
    # low-level HTTP helpers
    # ------------------------------------------------------------------ #

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path}"

    def _basic_auth(self) -> str:
        if not self._username or not self._password:
            raise SiyakhokhaApiError("API username or password is not set.")
        raw = f"{self._username}:{self._password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _mobile_request_bytes(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        req = Request(self._url(path), data=data, method=method)
        req.add_header("Authorization", self._basic_auth())
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self._mobile_opener.open(req, timeout=self.timeout) as resp:
                return resp.read()
        except Exception as exc:
            raise SiyakhokhaApiError(f"Request failed for {path}: {exc}") from exc

    def _mobile_request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        return self._mobile_request_bytes(
            method, path, data=data, headers=headers
        ).decode("utf-8", errors="replace")

    def _mobile_json(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        text = self._mobile_request(method, path, data=data, headers=headers)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SiyakhokhaApiError(
                f"Unexpected response for {path}: {text[:240]}"
            ) from exc

    def _portal_request_bytes(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        req = Request(self._url(path), data=data, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.read()
        except Exception as exc:
            raise SiyakhokhaApiError(f"Portal request failed for {path}: {exc}") from exc

    def _portal_request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        return self._portal_request_bytes(
            method, path, data=data, headers=headers
        ).decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ #
    # auth + priming
    # ------------------------------------------------------------------ #

    def login(self, username: str, password: str) -> None:
        """Validate Basic auth and refresh the API-side customer/account caches.

        No portal request is made here.  The portal session remains lazy so a
        normal Home Assistant refresh never parses HTML or creates cookies.
        """
        if self._username != username or self._password != password:
            self._portal_logged_in = False
            self._cookie_jar = CookieJar()
            self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar))

        self._username = username
        self._password = password
        self._payment_history_payload = None

        customer = self._mobile_json("GET", "/api/mobile/customer")
        if not isinstance(customer, dict) or customer.get("systemUserId") is None:
            raise SiyakhokhaApiError(
                "Mobile login failed: invalid /api/mobile/customer response"
            )
        self._customer = customer
        self._refresh_accounts()

    def _refresh_accounts(self) -> None:
        latest = self._mobile_json("GET", "/api/mobile/latestaccounts")
        self._accounts = latest if isinstance(latest, list) else []

        try:
            linked = self._mobile_json("GET", "/api/mobile/accounts")
        except SiyakhokhaApiError:
            # ``latestaccounts`` is sufficient for setup/balance reads.  Keep a
            # graceful fallback for profiles where the richer route is disabled.
            linked = []
        self._linked_accounts = linked if isinstance(linked, list) else []

    def _ensure_portal_session(self) -> None:
        """Log into the ASP.NET portal (lazily, once per credentials)."""
        if self._portal_logged_in:
            return
        if not self._username or not self._password:
            raise SiyakhokhaApiError("Missing credentials for portal login")

        login_page = self._portal_request("GET", "/Account/Login")
        m = re.search(
            r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', login_page, re.S
        )
        if not m:
            raise SiyakhokhaApiError("Unable to extract portal login token.")

        payload = urlencode(
            {
                "__RequestVerificationToken": m.group(1),
                "UserName": self._username,
                "Password": self._password,
                "RememberMe": "false",
            }
        ).encode("utf-8")

        response = self._portal_request(
            "POST",
            "/Account/Login",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if re.search(r'action=["\']/Account/Login["\']', response, re.IGNORECASE):
            raise SiyakhokhaApiError("Portal login failed: credentials were rejected.")
        self._portal_logged_in = True

    # ------------------------------------------------------------------ #
    # accounts / balance / bills (mobile JSON)
    # ------------------------------------------------------------------ #

    def get_account_list(self) -> list[dict[str, Any]]:
        """Return linked municipal accounts from the current mobile APIs.

        ``latestaccounts`` supplies current descriptions/holders while
        ``accounts`` supplies stable internal ids needed for debit-order forms.
        The large upstream customer/account graphs are intentionally reduced
        before being exposed as Home Assistant state attributes.
        """
        if self._accounts is None or self._linked_accounts is None:
            self._refresh_accounts()

        customer = self._customer or {}
        cust = {
            "first_name": customer.get("firstName"),
            "last_name": customer.get("lastName"),
            "email": customer.get("emailAddress"),
            "cell_phone": customer.get("cellPhoneNumber"),
            "physical_address": [
                customer.get(f"physicalAddress{i}")
                for i in range(1, 6)
                if customer.get(f"physicalAddress{i}")
            ],
            "postal_address": [
                customer.get(f"postalAddress{i}")
                for i in range(1, 6)
                if customer.get(f"postalAddress{i}")
            ],
        }

        linked_by_number: dict[str, dict[str, Any]] = {}
        for linked in self._linked_accounts or []:
            if not isinstance(linked, dict):
                continue
            account = linked.get("account")
            if not isinstance(account, dict):
                continue
            number = str(account.get("accountNumber") or "").strip()
            if number:
                linked_by_number[number] = linked

        latest_by_number = {
            str(entry.get("accountNumber") or "").strip(): entry
            for entry in (self._accounts or [])
            if isinstance(entry, dict) and entry.get("accountNumber")
        }
        numbers = list(latest_by_number)
        numbers.extend(number for number in linked_by_number if number not in latest_by_number)

        rows: list[dict[str, Any]] = []
        for number in numbers:
            latest = latest_by_number.get(number, {})
            linked = linked_by_number.get(number, {})
            account = linked.get("account") if isinstance(linked, dict) else {}
            account = account if isinstance(account, dict) else {}
            rows.append(
                {
                    "account_id": linked.get("accountId") or account.get("id"),
                    "account_number": number,
                    "description": latest.get("description") or account.get("description"),
                    "account_holder": latest.get("accountHolder") or account.get("accountHolder"),
                    "account_type": account.get("accountType"),
                    "is_active": account.get("isActive", True),
                    "is_blacklisted": account.get("isBlacklisted"),
                    "customer": cust,
                }
            )
        return rows

    def get_account_balance(self) -> list[dict[str, Any]]:
        """Return current balance per account from /api/mobile/latestaccounts.

        ``next_run_date`` is not exposed by the mobile endpoint and is None.
        """
        if self._accounts is None:
            self._refresh_accounts()
        rows: list[dict[str, Any]] = []
        for entry in self._accounts or []:
            if not isinstance(entry, dict):
                continue
            try:
                payable = (
                    float(entry.get("latestAmountDue"))
                    if entry.get("latestAmountDue") is not None
                    else None
                )
            except (TypeError, ValueError):
                payable = None
            rows.append(
                {
                    "account_number": str(entry.get("accountNumber") or "").strip(),
                    "payable": payable,
                    "due_date": entry.get("dueDate"),
                    "next_run_date": None,
                }
            )
        return rows

    def get_bills(
        self,
        page_size: int = 10,
        page_number: int = 1,
        search_text: str = "",
        sort_order: str = "asc",
    ) -> dict[str, Any]:
        """Return bill history from /api/mobile/billlist for every linked account.

        The mobile endpoint returns all bills in one response, so paging is a
        no-op: page 1 carries everything, later pages are empty.
        """
        if self._accounts is None:
            self._refresh_accounts()
        account_numbers = [
            str(a.get("accountNumber") or "").strip()
            for a in (self._accounts or [])
        ]
        account_numbers = [a for a in account_numbers if a]

        all_rows: list[dict[str, Any]] = []
        for acc in account_numbers:
            try:
                payload = self._mobile_json(
                    "GET", f"/api/mobile/billlist?AccountNo={quote(acc, safe='')}"
                )
            except SiyakhokhaApiError:
                continue
            data = payload.get("data") if isinstance(payload, dict) else payload
            rows = (data.get("rows") or []) if isinstance(data, dict) else (data or [])
            for r in rows:
                if not isinstance(r, dict):
                    continue
                all_rows.append(
                    {
                        "AccountNumber": r.get("accountNumber"),
                        "IdentificationNumber": r.get("identificationNumber"),
                        "BillDate": r.get("billDate"),
                        "BillAmount": r.get("billAmount"),
                        "DownloadLink": r.get("downloadLink"),
                        "IsAgent": r.get("isAgent"),
                    }
                )

        all_rows.sort(key=lambda x: str(x.get("BillDate") or ""), reverse=True)
        if page_number > 1:
            return {"rows": [], "total": len(all_rows)}
        return {"rows": all_rows, "total": len(all_rows)}

    # ------------------------------------------------------------------ #
    # history (current mobile JSON API)
    # ------------------------------------------------------------------ #

    def _get_payment_history_payload(self) -> dict[str, Any]:
        """Fetch and cache the combined mobile payment-history response."""
        if self._payment_history_payload is None:
            payload = self._mobile_json("GET", "/api/mobile/getpaymenthistory")
            if not isinstance(payload, dict):
                raise SiyakhokhaApiError(
                    "Invalid response from /api/mobile/getpaymenthistory"
                )
            self._payment_history_payload = payload
        return self._payment_history_payload

    @staticmethod
    def _status_summary(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {
            "Id": value.get("id"),
            "Name": value.get("name"),
            "Description": value.get("description"),
            "Key": value.get("key"),
        }

    @staticmethod
    def _account_summary(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {
            "Id": value.get("id"),
            "AccountNumber": value.get("accountNumber"),
            "AccountHolder": value.get("accountHolder"),
            "Description": value.get("description"),
            "AccountType": value.get("accountType"),
        }

    @classmethod
    def _bank_account_summary(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        bank = value.get("bank")
        bank_summary = None
        if isinstance(bank, dict):
            bank_summary = {
                "Id": bank.get("id"),
                "Name": bank.get("name"),
                "Description": bank.get("description"),
                "UniversalBranchCode": bank.get("uniBranchCode"),
            }
        # Never expose the upstream full bankAccountNumber in HA attributes.
        return {
            "Id": value.get("id"),
            "HiddenBankAccountNumber": value.get("hiddenBankAccountNumber"),
            "ShowBankAccountNumber": value.get("showBankAccountNumber"),
            "AccountStatus": value.get("accountStatus"),
            "BranchCode": value.get("branchCode"),
            "Bank": bank_summary,
        }

    @classmethod
    def _debit_order_summary(cls, value: Any) -> dict[str, Any]:
        row = value if isinstance(value, dict) else {}
        return {
            "Id": row.get("id"),
            "AccountId": row.get("accountId"),
            "Amount": row.get("amount"),
            "BankAccountId": row.get("bankAccountId"),
            "CustomerId": row.get("customerId"),
            "IsActive": row.get("isActive"),
            "IsBatch": row.get("isBatch"),
            "IsRecurring": row.get("isRecurring"),
            "StartDateTime": row.get("startDateTime"),
            "StrikeDay": row.get("strikeDay"),
            "Status": cls._status_summary(row.get("status")),
            "Account": cls._account_summary(row.get("account")),
            "BankAccount": cls._bank_account_summary(row.get("bankAccount")),
        }

    @staticmethod
    def _payment_summary(value: Any) -> dict[str, Any]:
        row = value if isinstance(value, dict) else {}
        amount = (
            row.get("amountPaid") if "amountPaid" in row else row.get("amount")
        )
        return {
            "Id": row.get("id"),
            "MetroAccountNumber": row.get("metroAccountNumber")
            or row.get("accountNumber"),
            "AmountPaid": amount,
            "PaymentDateTime": row.get("paymentDateTime")
            or row.get("createdDateTime"),
            "ReconDateTime": row.get("reconDateTime"),
            "PaymentType": row.get("paymentType") or row.get("type"),
        }

    @classmethod
    def _batch_order_summary(cls, value: Any) -> dict[str, Any]:
        row = value if isinstance(value, dict) else {}
        return {
            "Id": row.get("id"),
            "BatchNumber": row.get("batchNumber"),
            "BatchReference": row.get("batchReference"),
            "CreatedDateTime": row.get("createdDateTime"),
            "Status": cls._status_summary(row.get("status")),
            "DebitOrder": cls._debit_order_summary(row.get("debitOrder")),
        }

    def get_payment_history(self) -> dict[str, Any]:
        payload = self._get_payment_history_payload()
        rows = [
            self._payment_summary(row)
            for row in payload.get("paymentHistories") or []
            if isinstance(row, dict)
        ]
        eft_rows = [
            self._payment_summary(row)
            for row in payload.get("eftPaymentHistories") or []
            if isinstance(row, dict)
        ]
        masterpass_rows = [
            self._payment_summary(row)
            for row in payload.get("mpPaymentHistories") or []
            if isinstance(row, dict)
        ]
        return {
            "data": rows,
            "eft_data": eft_rows,
            "masterpass_data": masterpass_rows,
            "total": len(rows),
            "source": "/api/mobile/getpaymenthistory",
        }

    def get_debit_orders(self) -> dict[str, Any]:
        payload = self._get_payment_history_payload()
        rows = [
            self._debit_order_summary(row)
            for row in payload.get("debitOrderPaymentHistories") or []
            if isinstance(row, dict)
        ]
        return {
            "data": rows,
            "total": len(rows),
            "source": "/api/mobile/getpaymenthistory",
        }

    def get_batch_orders(self) -> dict[str, Any]:
        payload = self._get_payment_history_payload()
        rows = [
            self._batch_order_summary(row)
            for row in payload.get("batchDebitOrderPaymentHistories") or []
            if isinstance(row, dict)
        ]
        return {
            "data": rows,
            "total": len(rows),
            "source": "/api/mobile/getpaymenthistory",
        }

    def get_debit_order_context(self) -> dict[str, Any]:
        """Build polling-safe debit-order choices from JSON API data only."""
        payload = self._get_payment_history_payload()
        debit_rows = [
            row
            for row in payload.get("debitOrderPaymentHistories") or []
            if isinstance(row, dict)
        ]

        bank_accounts_by_id: dict[int, dict[str, Any]] = {}
        for row in debit_rows:
            bank = row.get("bankAccount")
            if not isinstance(bank, dict):
                continue
            try:
                bank_id = int(bank.get("id"))
            except (TypeError, ValueError):
                continue
            summary = self._bank_account_summary(bank) or {}
            bank_name = summary.get("Bank") or {}
            bank_accounts_by_id[bank_id] = {
                "id": bank_id,
                "value": str(bank_id),
                "label": " - ".join(
                    str(part)
                    for part in (
                        bank_name.get("Name") if isinstance(bank_name, dict) else None,
                        summary.get("HiddenBankAccountNumber"),
                    )
                    if part
                )
                or str(bank_id),
                "selected": False,
            }

        municipal_accounts = []
        for account in self.get_account_list():
            try:
                account_id = int(account.get("account_id"))
            except (TypeError, ValueError):
                continue
            municipal_accounts.append(
                {
                    "id": account_id,
                    "value": str(account_id),
                    "label": str(
                        account.get("description")
                        or account.get("account_number")
                        or account_id
                    ),
                    "selected": False,
                }
            )

        latest = debit_rows[0] if debit_rows else {}
        resolved_bank_id = latest.get("bankAccountId")
        resolved_account_id = latest.get("accountId")
        if resolved_bank_id is None and bank_accounts_by_id:
            resolved_bank_id = next(iter(bank_accounts_by_id))
        if resolved_account_id is None and municipal_accounts:
            resolved_account_id = municipal_accounts[0]["id"]

        for row in bank_accounts_by_id.values():
            row["selected"] = row["id"] == resolved_bank_id
        for row in municipal_accounts:
            row["selected"] = row["id"] == resolved_account_id

        return {
            "source": "/api/mobile/getpaymenthistory + /api/mobile/accounts",
            "portal_required_for_submission": True,
            "bank_accounts": list(bank_accounts_by_id.values()),
            "municipal_accounts": municipal_accounts,
            "resolved": {
                "bank_account_id": resolved_bank_id,
                "account_id": resolved_account_id,
                "strike_day": latest.get("strikeDay"),
                "start_date": latest.get("startDateTime"),
            },
        }

    # ------------------------------------------------------------------ #
    # PDF download (portal session required)
    # ------------------------------------------------------------------ #

    def download_bill(self, download_token: str) -> bytes:
        token = str(download_token).strip()
        if not token or token.upper() == "UNPAYABLE":
            raise SiyakhokhaApiError("Bill does not have a downloadable PDF token.")

        encoded = quote(unquote(token), safe="")
        for _attempt in range(2):
            self._ensure_portal_session()
            data = self._portal_request_bytes(
                "GET", f"/Report/GenerateBill?q={encoded}"
            )
            if data.lstrip().startswith(b"%PDF"):
                return data
            # Portal session expired -> re-login and retry once.
            self._portal_logged_in = False
        raise SiyakhokhaApiError(
            "PDF download returned a non-PDF response (portal session expired?)."
        )

    # ------------------------------------------------------------------ #
    # submissions (portal session required, on-demand only)
    # ------------------------------------------------------------------ #

    def get_single_debit_order_context(self) -> dict[str, Any]:
        self._ensure_portal_session()
        page = self._portal_request("GET", "/DebitOrder")

        def _extract(name: str) -> str | None:
            m = re.search(
                rf'name="{re.escape(name)}"[^>]*value="([^"]*)"',
                page,
                re.IGNORECASE,
            )
            return m.group(1) if m else None

        token = _extract("__RequestVerificationToken")
        bank_account_id = _extract("DebitOrder.BankAccountId")
        account_id = _extract("DebitOrder.AccountId")
        strike_day = _extract("DebitOrder.StrikeDay")
        start_date = _extract("DebitOrder.StartDateTime")

        def _extract_select_options(name: str) -> list[dict[str, Any]]:
            select_match = re.search(
                rf'<select[^>]*name="{re.escape(name)}"[^>]*>(.*?)</select>',
                page,
                re.IGNORECASE | re.DOTALL,
            )
            if not select_match:
                return []

            options_html = select_match.group(1)
            option_matches = re.finditer(
                r'<option[^>]*value="([^"]*)"([^>]*)>(.*?)</option>',
                options_html,
                re.IGNORECASE | re.DOTALL,
            )
            rows: list[dict[str, Any]] = []
            for match in option_matches:
                raw_value = (match.group(1) or "").strip()
                raw_attrs = match.group(2) or ""
                raw_label = re.sub(r"<[^>]+>", "", match.group(3) or "")
                label = " ".join(raw_label.split())
                selected = bool(re.search(r"\bselected\b", raw_attrs, re.IGNORECASE))

                try:
                    parsed_id: int | None = int(raw_value)
                except (TypeError, ValueError):
                    parsed_id = None

                rows.append(
                    {
                        "id": parsed_id,
                        "value": raw_value,
                        "label": label,
                        "selected": selected,
                    }
                )

            return rows

        bank_accounts = _extract_select_options("DebitOrder.BankAccountId")
        municipal_accounts = _extract_select_options("DebitOrder.AccountId")

        def _first_selected_id(options: list[dict[str, Any]]) -> int | None:
            for option in options:
                if option.get("selected") and option.get("id") is not None:
                    return int(option["id"])
            for option in options:
                if option.get("id") is not None:
                    return int(option["id"])
            return None

        def _as_int(value: Any) -> int | None:
            try:
                if value is None or str(value).strip() == "":
                    return None
                return int(str(value).strip())
            except (TypeError, ValueError):
                return None

        resolved_bank_account_id = _as_int(bank_account_id)
        if resolved_bank_account_id is None:
            resolved_bank_account_id = _first_selected_id(bank_accounts)

        resolved_account_id = _as_int(account_id)
        if resolved_account_id is None:
            resolved_account_id = _first_selected_id(municipal_accounts)

        if not token:
            raise SiyakhokhaApiError(
                "Could not extract __RequestVerificationToken from /DebitOrder"
            )

        return {
            "__RequestVerificationToken": token,
            "DebitOrder.BankAccountId": bank_account_id,
            "DebitOrder.AccountId": account_id,
            "DebitOrder.StrikeDay": strike_day,
            "DebitOrder.StartDateTime": start_date,
            "bank_accounts": bank_accounts,
            "municipal_accounts": municipal_accounts,
            "resolved": {
                "bank_account_id": resolved_bank_account_id,
                "account_id": resolved_account_id,
                "strike_day": _as_int(strike_day),
                "start_date": start_date,
            },
        }

    def submit_single_debit_order(
        self,
        *,
        bank_account_id: int,
        account_id: int,
        amount: float,
        strike_day: int,
        start_date: str,
        is_recurring: bool = False,
        debit_order_id: str = "",
        request_verification_token: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        token = request_verification_token
        if not token:
            context = self.get_single_debit_order_context()
            token = str(context.get("__RequestVerificationToken") or "").strip()

        if not token:
            raise SiyakhokhaApiError("Missing request verification token")

        amount_value = f"{float(amount):.2f}".replace(".", ",")

        pairs = [
            ("__RequestVerificationToken", token),
            ("DebitOrder.Id", str(debit_order_id)),
            ("DebitOrder.BankAccountId", str(bank_account_id)),
            ("DebitOrder.AccountId", str(account_id)),
            ("DebitOrder.Amount", amount_value),
            ("DebitOrder.IsRecurring", "true" if is_recurring else "false"),
            ("DebitOrder.StrikeDay", str(strike_day)),
            ("DebitOrder.StartDateTime", str(start_date)),
        ]

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "path": "/DebitOrder",
                "payload": {k: v for k, v in pairs},
            }

        self._ensure_portal_session()
        body = urlencode(pairs).encode("utf-8")
        text = self._portal_request(
            "POST",
            "/DebitOrder",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            return {"ok": True, "json": json.loads(text)}
        except json.JSONDecodeError:
            return {"ok": True, "raw": text}

    def get_bulk_payment_context(self) -> dict[str, str]:
        self._ensure_portal_session()
        page = self._portal_request("GET", "/DebitOrder/IndexBatchPayment")

        def _extract(name: str) -> str | None:
            m = re.search(
                rf'name="{re.escape(name)}"[^>]*value="([^"]+)"', page, re.IGNORECASE
            )
            if m:
                return m.group(1)
            m2 = re.search(
                rf'\b{re.escape(name)}\b\s*[:=]\s*["\']([^"\']+)["\']',
                page,
                re.IGNORECASE,
            )
            if m2:
                return m2.group(1)
            return None

        cus_id = _extract("CusId")
        bank_match = re.search(
            r'<select[^>]*id="BankAccountDdl"[^>]*>.*?<option[^>]*value="([^"]+)"[^>]*>',
            page,
            re.IGNORECASE | re.DOTALL,
        )
        bid = bank_match.group(1) if bank_match else _extract("BId")

        def _extract_js_int(name: str) -> int | None:
            values = re.findall(
                rf"\b{re.escape(name)}\s*=\s*([0-9]+)\s*;",
                page,
                re.IGNORECASE,
            )
            return int(values[-1]) if values else None

        strike_day = _extract_js_int("BatchstrikeDate")
        year = _extract_js_int("BatchYear")
        month = _extract_js_int("BatchMonth")
        day = _extract_js_int("BatchDay")
        hour = _extract_js_int("BatchHour")
        minute = _extract_js_int("BatchMinute")
        second = _extract_js_int("BatchSec")
        millisecond = _extract_js_int("BatchSSec")
        batch_bool = _extract_js_int("BatchBool")

        date_parts = (year, month, day, hour, minute, second, millisecond)
        start_date = None
        batch_reference = None
        if all(value is not None for value in date_parts) and batch_bool is not None:
            start_date = f"{year:04d}-{month:02d}-{day:02d}"
            batch_reference = (
                f".{year:04d}{month:02d}{day:02d}"
                f"{hour:02d}{minute:02d}{second:02d}{millisecond:03d}"
                f"{batch_bool}{strike_day}"
            )

        context = {
            "CusId": cus_id,
            "BId": bid,
            "dStrike": str(strike_day) if strike_day is not None else None,
            "startDate": start_date,
            "bat": batch_reference,
        }

        missing = [k for k, v in context.items() if not v]
        if missing:
            raise SiyakhokhaApiError(
                f"Could not extract bulk payment context fields: {', '.join(missing)}"
            )

        return {k: str(v) for k, v in context.items()}

    def submit_bulk_payment(
        self,
        account_numbers: list[str],
        amounts: list[float],
        context: dict[str, str],
    ) -> dict[str, Any]:
        if not account_numbers or not amounts:
            raise SiyakhokhaApiError("No payment items provided.")
        if len(account_numbers) != len(amounts):
            raise SiyakhokhaApiError(
                "account_numbers and amounts must have the same length."
            )

        pairs: list[tuple[str, str]] = []
        for amount in amounts:
            pairs.append(("ControllerDebitAmount[]", f"{float(amount):.2f}"))

        for acct in account_numbers:
            pairs.append(("MAccounts[]", str(acct)))

        pairs.extend(
            [
                ("CusId", context["CusId"]),
                ("BId", context["BId"]),
                ("bat", context["bat"]),
                ("startDate", context["startDate"]),
            ]
        )

        self._ensure_portal_session()
        body = urlencode(pairs).encode("utf-8")
        text = self._portal_request(
            "POST",
            "/DebitOrder/BulkPayment",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            return {"ok": True, "json": json.loads(text)}
        except json.JSONDecodeError:
            return {"ok": True, "raw": text}

    # ------------------------------------------------------------------ #
    # tariff data (public ekurhuleni.gov.za documents) — unchanged
    # ------------------------------------------------------------------ #

    def fetch_latest_public_tariff_data(self) -> dict[str, Any]:
        schedule = self._fetch_latest_tariff_document(
            search_term="schedule 2 electricity tariffs"
        )
        policy = self._fetch_latest_tariff_document(
            search_term="annexure e16 electricity tariff policy"
        )

        if not schedule or not schedule.get("download_url"):
            raise SiyakhokhaApiError(
                "Could not locate latest Schedule 2 tariff document"
            )

        pdf_bytes = self._download_public_file(schedule["download_url"])
        parsed_rows = self._parse_schedule_pdf_to_rows(
            pdf_bytes,
            schedule_title=str(schedule.get("title") or ""),
            schedule_filename=str(schedule.get("filename") or ""),
        )
        if not parsed_rows:
            raise SiyakhokhaApiError(
                "Schedule 2 PDF parsed, but no tariff rows were extracted"
            )

        now_iso = datetime.now().isoformat()
        return {
            "status": "ok",
            "last_refresh": now_iso,
            "source": {
                "schedule": {
                    "id": schedule.get("id"),
                    "title": schedule.get("title"),
                    "date": schedule.get("date"),
                    "link": schedule.get("link"),
                    "download_url": schedule.get("download_url"),
                    "filename": schedule.get("filename"),
                },
                "policy": {
                    "id": policy.get("id") if policy else None,
                    "title": policy.get("title") if policy else None,
                    "date": policy.get("date") if policy else None,
                    "link": policy.get("link") if policy else None,
                    "download_url": policy.get("download_url") if policy else None,
                    "filename": policy.get("filename") if policy else None,
                },
            },
            "rows": parsed_rows,
        }

    def _fetch_latest_tariff_document(self, search_term: str) -> dict[str, Any] | None:
        fields = "id,date,title,link,download_url,filename"
        query = urlencode(
            {
                "search": search_term,
                "per_page": 20,
                "orderby": "date",
                "order": "desc",
                "_fields": fields,
            }
        )
        url = f"https://www.ekurhuleni.gov.za/wp-json/wp/v2/dlp_document?{query}"
        try:
            with urlopen(url, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            raise SiyakhokhaApiError(
                f"Failed discovering public tariff document: {exc}"
            ) from exc

        if not isinstance(payload, list) or not payload:
            return None

        ranked: list[tuple[int, dict[str, Any]]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            title_obj = item.get("title") or {}
            title = (
                title_obj.get("rendered", "")
                if isinstance(title_obj, dict)
                else str(title_obj)
            )
            title_l = str(title).lower()
            score = 0
            if "amended" in title_l:
                score += 40
            if "final" in title_l:
                score += 30
            if "draft" in title_l:
                score -= 20
            ranked.append((score, item))

        ranked.sort(key=lambda x: (x[0], x[1].get("date", "")), reverse=True)
        chosen = ranked[0][1]
        chosen_title = chosen.get("title")
        title_rendered = (
            chosen_title.get("rendered", "")
            if isinstance(chosen_title, dict)
            else str(chosen_title)
        )
        return {
            "id": chosen.get("id"),
            "date": chosen.get("date"),
            "title": title_rendered,
            "link": chosen.get("link"),
            "download_url": chosen.get("download_url"),
            "filename": chosen.get("filename"),
        }

    def _download_public_file(self, url: str) -> bytes:
        try:
            with urlopen(url, timeout=self.timeout) as resp:
                return resp.read()
        except Exception as exc:
            raise SiyakhokhaApiError(f"Failed downloading tariff file: {exc}") from exc

    def _parse_schedule_pdf_to_rows(
        self,
        pdf_bytes: bytes,
        schedule_title: str = "",
        schedule_filename: str = "",
    ) -> list[dict[str, Any]]:
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception as exc:
            raise SiyakhokhaApiError(f"Failed parsing schedule PDF: {exc}") from exc

        compact = re.sub(r"\s+", " ", text)

        def _parse_amount(raw: str | None) -> float | None:
            if not raw:
                return None
            value = str(raw).strip().replace("R", "").replace(" ", "")
            if re.match(r"^\d+\.\d{2},\d{2}$", value):
                left, right = value.split(",", 1)
                int_part, frac2 = left.split(".", 1)
                return float(f"{int_part}.{frac2}{right}")
            if "," in value and "." not in value:
                return float(value.replace(",", "."))
            if "," in value and "." in value:
                return float(value.replace(",", ""))
            return float(value)

        amount_pattern = r"R?\s*([0-9][0-9 .]*[.,][0-9]{2}(?:,[0-9]{2})?)"

        def _extract_near(anchor_pattern: str, window_chars: int = 420) -> float | None:
            anchor = re.search(anchor_pattern, compact, re.IGNORECASE)
            if not anchor:
                return None
            segment = compact[anchor.end() : anchor.end() + window_chars]
            m = re.search(amount_pattern, segment, re.IGNORECASE)
            if not m:
                return None
            return _parse_amount(m.group(1))

        def _extract_first(pattern: str) -> float | None:
            m = re.search(pattern, compact, re.IGNORECASE)
            if not m:
                return None
            return _parse_amount(m.group(1))

        rows_seed: list[tuple[str, str, str, str, str, str]] = [
            (
                "Residential A2 Non-Indigent",
                "A2",
                "A.1.2 Block",
                "0 to 50 kWh",
                "R/kWh",
                r"A\.1\.2\b.*?\(\s*0\s*to\s*50\s*kWh\s*\)",
            ),
            (
                "Residential A2 Non-Indigent",
                "A2",
                "A.2.2 Block",
                ">50 to 600 kWh",
                "R/kWh",
                r"A\.2\.2\b.*?>\s*50\s*to\s*<=\s*600",
            ),
            (
                "Residential A2 Non-Indigent",
                "A2",
                "A.3.2 Block",
                ">600 to 700 kWh",
                "R/kWh",
                r"A\.3\.2\b.*?>\s*600\s*to\s*<=\s*700",
            ),
            (
                "Residential A2 Non-Indigent",
                "A2",
                "A.4.2 Block",
                ">700 kWh",
                "R/kWh",
                r"A\.4\.2\b.*?>\s*700\s*kWh",
            ),
            (
                "Residential B",
                "B",
                "Energy charge high season (Jun-Aug)",
                "All usage",
                "R/kWh",
                r"R\.3\.\s*High\s*Demand\s*Season",
            ),
            (
                "Residential B",
                "B",
                "Basic charge 1-phase",
                "",
                "R/month",
                r"R1\.1\b.*?Basic\s*charge\s*:\s*1\s*Phase",
            ),
            (
                "Residential B",
                "B",
                "Basic charge 3-phase",
                "",
                "R/month",
                r"R1\.2\b.*?Basic\s*charge\s*:\s*3\s*Phase",
            ),
            (
                "Tariff C Energy",
                "C",
                "Energy charge high season 230/400V",
                "",
                "R/kWh",
                r"C\.3\.1\.1\.\s*230/400\s*V",
            ),
            (
                "Tariff C Energy",
                "C",
                "Energy charge low season 230/400V",
                "",
                "R/kWh",
                r"C\.3\.2\.1\.\s*230/400\s*V",
            ),
        ]

        rows: list[dict[str, Any]] = []
        for section, tariff, charge, usage_range, unit, label_pattern in rows_seed:
            excl = _extract_near(label_pattern)
            if excl is None:
                continue
            rows.append(
                {
                    "section": section,
                    "tariff": tariff,
                    "charge_or_block": charge,
                    "usage_range": usage_range,
                    "unit": unit,
                    "excl_vat": round(float(excl), 4),
                    "incl_vat": round(float(excl) * 1.15, 4),
                }
            )

        a_business_energy = _extract_first(
            r"A\.5\.\s*Energy\s*Charge\s*\(\s*R/kWh\s*\)\s*" + amount_pattern
        )
        if a_business_energy is not None:
            rows.append(
                {
                    "section": "Business A Small Business",
                    "tariff": "A Business",
                    "charge_or_block": "Energy charge all seasons",
                    "usage_range": "",
                    "unit": "R/kWh",
                    "excl_vat": round(float(a_business_energy), 4),
                    "incl_vat": round(float(a_business_energy) * 1.15, 4),
                }
            )

        b_energy_low = _extract_first(
            r"R\.4\.\s*Low\s*Demand\s*Season\s*" + amount_pattern
        )
        if b_energy_low is not None:
            rows.append(
                {
                    "section": "Residential B",
                    "tariff": "B",
                    "charge_or_block": "Energy charge low season (Sep-May)",
                    "usage_range": "All usage",
                    "unit": "R/kWh",
                    "excl_vat": round(float(b_energy_low), 4),
                    "incl_vat": round(float(b_energy_low) * 1.15, 4),
                }
            )

        unique: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                str(row.get("section", "")),
                str(row.get("tariff", "")),
                str(row.get("charge_or_block", "")),
                str(row.get("usage_range", "")),
                str(row.get("unit", "")),
            )
            unique[key] = row

        deduped = list(unique.values())

        if len(deduped) < 20:
            fallback_rows = self._fallback_rows_from_known_schedule(
                schedule_text=compact,
                schedule_title=schedule_title,
                schedule_filename=schedule_filename,
            )
            for row in fallback_rows:
                key = (
                    str(row.get("section", "")),
                    str(row.get("tariff", "")),
                    str(row.get("charge_or_block", "")),
                    str(row.get("usage_range", "")),
                    str(row.get("unit", "")),
                )
                unique[key] = row
            deduped = list(unique.values())

        deduped.sort(
            key=lambda item: (
                str(item.get("tariff", "")),
                str(item.get("charge_or_block", "")),
            )
        )
        return deduped

    def _fallback_rows_from_known_schedule(
        self,
        schedule_text: str,
        schedule_title: str = "",
        schedule_filename: str = "",
    ) -> list[dict[str, Any]]:
        # Conservative fallback for known 2025-26 amended structure.
        title_match = re.search(
            r"Schedule\s*2\s*Electricity\s*Tariffs\s*2025-?26",
            schedule_text,
            re.IGNORECASE,
        )
        meta = f"{schedule_title} {schedule_filename}"
        meta_match = re.search(r"2025\s*-?\s*26", meta, re.IGNORECASE)
        if not title_match and not meta_match:
            return []

        return [
            {
                "section": "Business A Small Business",
                "tariff": "A Business",
                "charge_or_block": "Basic charge 1-phase",
                "usage_range": "",
                "unit": "R/month",
                "excl_vat": 138.2200,
                "incl_vat": 158.9530,
            },
            {
                "section": "Business A Small Business",
                "tariff": "A Business",
                "charge_or_block": "Basic charge 3-phase",
                "usage_range": "",
                "unit": "R/month",
                "excl_vat": 269.7500,
                "incl_vat": 310.2125,
            },
            {
                "section": "Business A Small Business",
                "tariff": "A Business",
                "charge_or_block": "Embedded generation credit",
                "usage_range": "",
                "unit": "R/kWh",
                "excl_vat": 1.0538,
                "incl_vat": 1.2119,
            },
            {
                "section": "Business A Small Business",
                "tariff": "A Business",
                "charge_or_block": "Energy charge all seasons",
                "usage_range": "",
                "unit": "R/kWh",
                "excl_vat": 3.7705,
                "incl_vat": 4.3361,
            },
            {
                "section": "Residential A1 Indigent",
                "tariff": "A1",
                "charge_or_block": "A0.1 Block",
                "usage_range": "0 to 50 kWh",
                "unit": "R/kWh",
                "excl_vat": 0.0000,
                "incl_vat": 0.0000,
            },
            {
                "section": "Residential A1 Indigent",
                "tariff": "A1",
                "charge_or_block": "A1.1 Block",
                "usage_range": ">50 to 600 kWh",
                "unit": "R/kWh",
                "excl_vat": 2.5861,
                "incl_vat": 2.9740,
            },
            {
                "section": "Residential A1 Indigent",
                "tariff": "A1",
                "charge_or_block": "A2.1 Block",
                "usage_range": ">600 to 700 kWh",
                "unit": "R/kWh",
                "excl_vat": 4.3956,
                "incl_vat": 5.0549,
            },
            {
                "section": "Residential A1 Indigent",
                "tariff": "A1",
                "charge_or_block": "A3.1 Block",
                "usage_range": ">700 kWh",
                "unit": "R/kWh",
                "excl_vat": 12.3889,
                "incl_vat": 14.2472,
            },
            {
                "section": "Residential A2 Non-Indigent",
                "tariff": "A2",
                "charge_or_block": "A.1.2 Block",
                "usage_range": "0 to 50 kWh",
                "unit": "R/kWh",
                "excl_vat": 2.5865,
                "incl_vat": 2.9745,
            },
            {
                "section": "Residential A2 Non-Indigent",
                "tariff": "A2",
                "charge_or_block": "A.2.2 Block",
                "usage_range": ">50 to 600 kWh",
                "unit": "R/kWh",
                "excl_vat": 2.5865,
                "incl_vat": 2.9745,
            },
            {
                "section": "Residential A2 Non-Indigent",
                "tariff": "A2",
                "charge_or_block": "A.3.2 Block",
                "usage_range": ">600 to 700 kWh",
                "unit": "R/kWh",
                "excl_vat": 4.0341,
                "incl_vat": 4.6392,
            },
            {
                "section": "Residential A2 Non-Indigent",
                "tariff": "A2",
                "charge_or_block": "A.4.2 Block",
                "usage_range": ">700 kWh",
                "unit": "R/kWh",
                "excl_vat": 10.4287,
                "incl_vat": 11.9930,
            },
            {
                "section": "Residential A2 Threshold Totals",
                "tariff": "A2",
                "charge_or_block": "Total up to threshold",
                "usage_range": "50 kWh",
                "unit": "R",
                "excl_vat": 129.33,
                "incl_vat": 148.72,
            },
            {
                "section": "Residential A2 Threshold Totals",
                "tariff": "A2",
                "charge_or_block": "Total up to threshold",
                "usage_range": "600 kWh",
                "unit": "R",
                "excl_vat": 1551.90,
                "incl_vat": 1784.68,
            },
            {
                "section": "Residential A2 Threshold Totals",
                "tariff": "A2",
                "charge_or_block": "Total up to threshold",
                "usage_range": "700 kWh",
                "unit": "R",
                "excl_vat": 1955.31,
                "incl_vat": 2248.61,
            },
            {
                "section": "Residential B",
                "tariff": "B",
                "charge_or_block": "Basic charge 1-phase",
                "usage_range": "",
                "unit": "R/month",
                "excl_vat": 109.7800,
                "incl_vat": 126.2470,
            },
            {
                "section": "Residential B",
                "tariff": "B",
                "charge_or_block": "Basic charge 3-phase",
                "usage_range": "",
                "unit": "R/month",
                "excl_vat": 203.8900,
                "incl_vat": 234.4735,
            },
            {
                "section": "Residential B",
                "tariff": "B",
                "charge_or_block": "Energy charge high season (Jun-Aug)",
                "usage_range": "All usage",
                "unit": "R/kWh",
                "excl_vat": 3.3722,
                "incl_vat": 3.8780,
            },
            {
                "section": "Residential B",
                "tariff": "B",
                "charge_or_block": "Energy charge low season (Sep-May)",
                "usage_range": "All usage",
                "unit": "R/kWh",
                "excl_vat": 3.3722,
                "incl_vat": 3.8780,
            },
            {
                "section": "Residential B",
                "tariff": "B",
                "charge_or_block": "Internet display optional",
                "usage_range": "",
                "unit": "R/month",
                "excl_vat": 313.8000,
                "incl_vat": 360.8700,
            },
            {
                "section": "Residential B Embedded Generation",
                "tariff": "B",
                "charge_or_block": "Energy credit all seasons",
                "usage_range": "Exported units",
                "unit": "R/kWh",
                "excl_vat": 1.0538,
                "incl_vat": 1.2119,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Basic charge 230/400V",
                "usage_range": "",
                "unit": "R/month",
                "excl_vat": 3389.7300,
                "incl_vat": 3898.1895,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Basic charge 230/400V direct from substation",
                "usage_range": "",
                "unit": "R/month",
                "excl_vat": 3389.7300,
                "incl_vat": 3898.1895,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Basic charge >230/400V and <=11kV",
                "usage_range": "",
                "unit": "R/month",
                "excl_vat": 5179.2100,
                "incl_vat": 5956.0915,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Demand charge 230/400V direct high season",
                "usage_range": "",
                "unit": "R/kVA-month",
                "excl_vat": 236.5400,
                "incl_vat": 272.0210,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Demand charge 230/400V direct low season",
                "usage_range": "",
                "unit": "R/kVA-month",
                "excl_vat": 236.5400,
                "incl_vat": 272.0210,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Demand charge 230/400V high season",
                "usage_range": "",
                "unit": "R/kVA-month",
                "excl_vat": 242.1900,
                "incl_vat": 278.5185,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Demand charge 230/400V low season",
                "usage_range": "",
                "unit": "R/kVA-month",
                "excl_vat": 242.1900,
                "incl_vat": 278.5185,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Demand charge >230/400V<=11kV high season",
                "usage_range": "",
                "unit": "R/kVA-month",
                "excl_vat": 227.1600,
                "incl_vat": 261.2340,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Demand charge >230/400V<=11kV low season",
                "usage_range": "",
                "unit": "R/kVA-month",
                "excl_vat": 227.1600,
                "incl_vat": 261.2340,
            },
            {
                "section": "Tariff C Energy",
                "tariff": "C",
                "charge_or_block": "Energy charge high season 230/400V",
                "usage_range": "",
                "unit": "R/kWh",
                "excl_vat": 4.6143,
                "incl_vat": 5.3064,
            },
            {
                "section": "Tariff C Energy",
                "tariff": "C",
                "charge_or_block": "Energy charge high season 230/400V direct from substation",
                "usage_range": "",
                "unit": "R/kWh",
                "excl_vat": 4.5266,
                "incl_vat": 5.2056,
            },
            {
                "section": "Tariff C Energy",
                "tariff": "C",
                "charge_or_block": "Energy charge high season >230/400V<=11kV",
                "usage_range": "",
                "unit": "R/kWh",
                "excl_vat": 4.4522,
                "incl_vat": 5.1200,
            },
            {
                "section": "Tariff C Energy",
                "tariff": "C",
                "charge_or_block": "Energy charge low season 230/400V",
                "usage_range": "",
                "unit": "R/kWh",
                "excl_vat": 2.2641,
                "incl_vat": 2.6037,
            },
            {
                "section": "Tariff C Energy",
                "tariff": "C",
                "charge_or_block": "Energy charge low season 230/400V direct from substation",
                "usage_range": "",
                "unit": "R/kWh",
                "excl_vat": 2.2017,
                "incl_vat": 2.5320,
            },
            {
                "section": "Tariff C Energy",
                "tariff": "C",
                "charge_or_block": "Energy charge low season >230/400V<=11kV",
                "usage_range": "",
                "unit": "R/kWh",
                "excl_vat": 2.1520,
                "incl_vat": 2.4748,
            },
            {
                "section": "Tariff C Embedded Generation",
                "tariff": "C",
                "charge_or_block": "Energy credit high season",
                "usage_range": "Exported units",
                "unit": "R/kWh",
                "excl_vat": 1.4054,
                "incl_vat": 1.6162,
            },
            {
                "section": "Tariff C Embedded Generation",
                "tariff": "C",
                "charge_or_block": "Energy credit low season",
                "usage_range": "Exported units",
                "unit": "R/kWh",
                "excl_vat": 0.9611,
                "incl_vat": 1.1053,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Network access charge 230/400V",
                "usage_range": "",
                "unit": "R/kVA-month",
                "excl_vat": 99.1500,
                "incl_vat": 114.0225,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Network access charge 230/400V direct from substation",
                "usage_range": "",
                "unit": "R/kVA-month",
                "excl_vat": 96.2300,
                "incl_vat": 110.6645,
            },
            {
                "section": "Tariff C Existing Customers",
                "tariff": "C",
                "charge_or_block": "Network access charge >230/400V<=11kV",
                "usage_range": "",
                "unit": "R/kVA-month",
                "excl_vat": 90.0900,
                "incl_vat": 103.6035,
            },
        ]
