# Siyakhokha Bridge

<p align="center">
  <img src="https://raw.githubusercontent.com/Defria/siyakhokha-bridge/main/logo_square.png" alt="Siyakhokha Bridge logo" width="180" />
</p>

<p align="center">
  Home Assistant custom integration for Siyakhokha bills, payment/debit history, batch payments, and PDF access.
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=Defria&repository=siyakhokha-bridge&category=integration">
    <img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open in HACS" />
  </a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=siyakhokha_bridge">
    <img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Open integration in Home Assistant" />
  </a>
</p>

## Dashboard Preview

Visual preview from the included dashboard setup:

<table>
  <tr>
    <td><img src="https://raw.githubusercontent.com/Defria/siyakhokha-bridge/main/examples/screenshots/siyakhokha_dashboard1.png" alt="Siyakhokha dashboard preview 1" /></td>
    <td><img src="https://raw.githubusercontent.com/Defria/siyakhokha-bridge/main/examples/screenshots/siyakhokha_dashboard2.png" alt="Siyakhokha dashboard preview 2" /></td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/Defria/siyakhokha-bridge/main/examples/screenshots/siyakhokha_dashboard3.png" alt="Siyakhokha dashboard preview 3" /></td>
    <td><img src="https://raw.githubusercontent.com/Defria/siyakhokha-bridge/main/examples/screenshots/siyakhokha_dashboard4.png" alt="Siyakhokha dashboard preview 4" /></td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/Defria/siyakhokha-bridge/main/examples/screenshots/siyakhokha_dashboard5.png" alt="Siyakhokha dashboard preview 5" /></td>
    <td><img src="https://raw.githubusercontent.com/Defria/siyakhokha-bridge/main/examples/screenshots/prepaid_meter_dashboard1.png" alt="Prepaid meter dashboard preview" /></td>
  </tr>
</table>

For the full screenshot set and replication guide, see `examples/README.md`.

## What It Does

- Fetches Siyakhokha municipal bills and historical bill rows.
- Fetches **live account balance** (current portal balance, due date, next debit-run date).
- Fetches **customer profile** (holder, name, email, phone, physical address) as diagnostic sensors.
- **Auto-discovers** linked municipal accounts at setup — single account picked automatically, multi-account setups get a dropdown.
- Fetches payment history, debit orders, and batch orders.
- Exposes local-first and portal PDF URLs for downloadable bills.
- Supports once-off batch payment submission and single debit-order submission via Home Assistant services.
- Exposes A.1.2 Block residential tariff (Excl/Incl VAT) sourced from the Ekurhuleni Schedule 2 PDF (manual refresh).
- Exposes debug attributes for submit response and API row counts.
- Supports configurable polling intervals from 30 minutes up to 31 days.

## Install

### HACS

1. Open HACS.
2. Go to `Custom repositories`.
3. Add `https://github.com/Defria/siyakhokha-bridge` as type `Integration`.
4. Install `Siyakhokha Bridge`.
5. Restart Home Assistant.
6. Go to `Settings -> Devices & Services -> Add Integration`.
7. Search for `Siyakhokha Bridge` and complete setup.

### Manual

1. Copy `custom_components/siyakhokha_bridge` to `/config/custom_components/siyakhokha_bridge`.
2. Restart Home Assistant.
3. Add from `Settings -> Devices & Services -> Add Integration`.

## Polling Interval

- Configure in integration setup, or later via `Settings -> Devices & Services -> Siyakhokha Bridge -> Configure`.
- Range: `30` to `44640` minutes.
- Recommended values:
  - Hourly: `60`
  - Every 6 hours: `360`
  - Daily: `1440`
  - Weekly: `10080`
  - Monthly: `43200` (30 days) or `44640` (31 days)

## Account Auto-Discovery

After you enter your Siyakhokha username and password, the integration calls `/Profile/LoadAccounts`
and auto-discovers all municipal accounts linked to your login.

- **One account linked**: it's selected automatically. No extra prompt.
- **Multiple accounts linked**: you'll see a dropdown listing each account with its description and holder name.

You can change which account is bound to a config entry later via the **Configure** button (Options flow),
which also lets you update credentials, base URL, polling interval, and tariff refresh settings without
removing and re-adding the integration.

## Main Entities

### Billing
- `sensor.latest_bill_amount` — most recent statement amount (negative = credit)
- `sensor.latest_bill_date`
- `sensor.latest_bill_pdf_url`
- `sensor.last_batch_submit_status`

### Live Balance (new in 0.2.0)
- `sensor.current_balance` — live portal balance (negative = credit)
- `sensor.balance_due_date`
- `sensor.next_debit_run_date`

### Customer Profile (diagnostic, new in 0.2.0)
- `sensor.account_description`
- `sensor.account_holder`
- `sensor.customer_name`
- `sensor.customer_email`
- `sensor.customer_phone`
- `sensor.customer_address`

### Tariff (manual refresh)
- `sensor.tariff_status`
- `sensor.tariff_last_refresh`
- `sensor.tariff_source_document`
- `sensor.tariff_a2_block_0_50_excl`
- `sensor.tariff_a2_block_0_50_incl`

### Buttons
- `button.refresh_bills`
- `button.open_latest_downloadable_bill`

`sensor.latest_bill_amount` attributes include `bills`, `payment_history`, `debit_orders`, `batch_orders`,
`accounts`, `account_info`, `current_balance`, `balance_due_date`, `balance_next_run_date`, and latest PDF URLs.

> **Sign convention:** balance/bill amounts preserve the upstream sign — negative means in credit, positive means owing, zero means settled. Dashboards colour-code accordingly (green / cyan / red).

> **PDF availability.** The Siyakhokha portal now generates printable statement PDFs for credit accounts as well — the integration picks these up automatically. On rare occasions a row may still return `DownloadLink: "UNPAYABLE"`, in which case the dashboard shows `—` in the PDF columns; the row data (date, amount, identifier) remains available in `sensor.latest_bill_amount` attributes.

## Services

- `siyakhokha_bridge.refresh`
- `siyakhokha_bridge.submit_batch_payment`

`submit_batch_payment` requires `entry_id`, `account_numbers`, `amounts`, and `confirm: true`.

## Dashboard Example

Ready-to-import sample:

- `examples/siyakhokha-dashboard.yaml`
- Full replication guide and assets: `examples/README.md`

It includes:

- bill summary + bill history table
- payment history and debit orders row popups
- batch-payment controls + submit card
- latest batch orders table for submit verification
- debug view for submit/API diagnostics

### Dashboard Dependencies

- [Mushroom Cards](https://github.com/piitaya/lovelace-mushroom)
- [Bubble Card](https://github.com/Clooos/Bubble-Card)
- [card-mod](https://github.com/thomasloven/lovelace-card-mod)

Install with HACS Frontend and refresh browser cache.

## Detailed Docs

- `custom_components/siyakhokha_bridge/README.md`
- `examples/README.md`

## Credits

Special thanks to **Heinz Meulke** ([tomatensaus](https://github.com/tomatensaus)):

- [DeyeSolarDesktop](https://github.com/tomatensaus/DeyeSolarDesktop)
- [Prepaid_electricity_meter.md](https://github.com/tomatensaus/DeyeSolarDesktop/blob/main/Prepaid_electricity_meter.md)

The prepaid meter/tracker approach in this project was inspired by the above work.

## Disclaimer

This is a hobby project built for personal use, learning, and Home Assistant experimentation.

For official account actions and authoritative data, use the official City of Ekurhuleni portal:

- https://siyakhokha.ekurhuleni.gov.za/

Use this repository and all example dashboards/automations at your own discretion and risk.
Always validate values before running payment-related actions.
