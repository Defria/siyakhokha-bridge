# Siyakhokha Bridge

<p align="center">
  <img src="./logo_square.png" alt="Siyakhokha Bridge logo" width="180" />
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

![Siyakhokha dashboard preview 1](./examples/screenshots/siyakhokha_dashboard1.png)
![Siyakhokha dashboard preview 2](./examples/screenshots/siyakhokha_dashboard3.png)
![Siyakhokha dashboard preview 3](./examples/screenshots/siyakhokha_dashboard5.png)

For the full screenshot set and replication guide, see `examples/README.md`.

## What It Does

- Fetches Siyakhokha municipal bills and historical bill rows.
- Fetches payment history, debit orders, and batch orders.
- Exposes local-first and portal PDF URLs for downloadable bills.
- Supports once-off batch payment submission via Home Assistant service.
- Exposes debug attributes for submit response and API row counts.
- Supports configurable polling intervals from 1 day up to 31 days.

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
- Range: `1440` to `44640` minutes.
- Recommended values:
  - Daily: `1440`
  - Weekly: `10080`
  - Monthly: `43200` (30 days) or `44640` (31 days)

## Main Entities

- `sensor.latest_bill_amount`
- `sensor.latest_bill_date`
- `sensor.latest_bill_pdf_url`
- `sensor.last_batch_submit_status`
- `button.refresh_bills`
- `button.open_latest_downloadable_bill`

`sensor.latest_bill_amount` attributes include `bills`, `payment_history`, `debit_orders`, `batch_orders`, and latest PDF URLs.

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
