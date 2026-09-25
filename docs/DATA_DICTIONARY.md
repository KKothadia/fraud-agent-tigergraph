# Data Dictionary

Mapping of the IEEE-CIS dataset columns to the TigerGraph graph schema.

## Source Files

| File | Rows | Columns | Size |
|---|---|---|---|
| `transactions.csv` | 590,742 | 397 (393 Vesta + 4 added) | ~708 MB |
| `identity.csv` | 144,432 | 41 | ~27 MB |
| `closed_cases_history.csv` | 5,565 | 15 | ~2.7 MB |
| `case_pack.csv` | 20 | 8 | ~3.5 KB |

## transactions.csv → Graph Mapping

### Core columns → Transaction vertex
| CSV Column | Type | Graph Attribute | Notes |
|---|---|---|---|
| `TransactionID` | int → str | Transaction.txn_id (PK) | Disguised IDs |
| `TransactionDT` | float | Transaction.TransactionDT | Seconds from dataset start |
| `TransactionAmt` | float | Transaction.TransactionAmt | USD |
| `ProductCD` | str | Transaction.ProductCD | W, C, H, R, S. W = no identity record = in_person |
| `customer_id` | str | → Customer.customer_id edge | Added column. Format: C01234 |
| `ts` | datetime | Transaction.ts | YYYY-MM-DD HH:MM:SS, Jul 2 – Dec 31, 2016 |
| `channel` | str | Transaction.channel | `in_person` or `online` |
| `risk_score` | float | Transaction.risk_score | 0-1. Input, not answer |

### Card columns → Card vertex
| CSV Column | Type | Graph Attribute | Notes |
|---|---|---|---|
| `card1` | int | Card.card1 | Issuer code |
| `card2` | float | Card.card2 | Issuer code |
| `card3` | float | Card.card3 | Issuer code |
| `card4` | str | Card.card4 | Network: visa, mastercard, amex, discover |
| `card5` | float | Card.card5 | Issuer code |
| `card6` | str | Card.card6 | Type: credit, debit |

Card IDs in case_pack are formatted as `C01234-K1` (customer_id + suffix).

### Address/Region columns → BillingRegion vertex
| CSV Column | Type | Graph Attribute | Notes |
|---|---|---|---|
| `addr1` | float | BillingRegion.region_code | Anonymized billing region code |
| `addr2` | float | Transaction.addr2 | Billing country. 87 = home country |

### Email columns → EmailDomain vertex
| CSV Column | Type | Graph Attribute | Notes |
|---|---|---|---|
| `P_emaildomain` | str | EmailDomain.domain | Purchaser email domain |
| `R_emaildomain` | str | EmailDomain.domain | Recipient email domain |

### Count columns (C1-C14)
Stored as Transaction attributes. Counts of addresses, phones, etc. associated with the card. Unnamed individually.

### Time delta columns (D1-D15)
Stored as Transaction attributes. Days since previous transaction, etc. Unnamed individually.

### Match flag columns (M1-M9)
Stored as Transaction attributes. Match flags (e.g., name vs address match). Key for account takeover detection (P5).

### Vesta features (V1-V339)
NOT stored in TigerGraph due to count (339 columns). Available in raw CSV for feature analysis. Unnamed engineered features.

### Distance columns (dist1, dist2)
Stored as Transaction attributes. Distances between two unnamed points.

## identity.csv → DeviceProfile vertex

Joined to transactions on `TransactionID`. Online transactions only.

| CSV Column | Type | Graph Attribute | Notes |
|---|---|---|---|
| `DeviceType` | str | DeviceProfile.DeviceType | "mobile" or "desktop" |
| `DeviceInfo` | str | DeviceProfile.DeviceInfo | e.g. "SAMSUNG SM-G892A Build/NRD90M" |
| `id_15` | str | DeviceProfile.id_15 | "New" or "Found" (device status for this account) |
| `id_23` | str | DeviceProfile.id_23 | Proxy: "transparent", "anonymous", "hidden" |
| `id_30` | str | DeviceProfile.os | OS, e.g. "Android 7.0" |
| `id_31` | str | DeviceProfile.browser | Browser, e.g. "samsung browser 6.2" |
| `id_33` | str | DeviceProfile.screen | Screen resolution, e.g. "2220x1080" |
| `id_01` to `id_11` | float | Not stored | Encoded ratings (device, IP-domain, proxy, login counts) |
| `id_12` to `id_38` | mixed | Not stored | Other categorical identity fields |

**Device profile string format:** `DeviceInfo | OS | browser | screen`

## closed_cases_history.csv → ClosedCase vertex

| CSV Column | Type | Graph Attribute | Notes |
|---|---|---|---|
| `case_id` | str | ClosedCase.case_id (PK) | Format: CC-0001 |
| `customer_id` | str | ClosedCase.customer_id | → Customer vertex |
| `card_id` | str | ClosedCase.card_id | → Card vertex (ON_CARD edge) |
| `opened_at` | datetime | ClosedCase.opened_at | |
| `closed_at` | datetime | ClosedCase.closed_at | |
| `outcome` | str | ClosedCase.outcome | `confirmed_fraud` or `cleared` |
| `pattern` | str | ClosedCase.pattern | One of 5 patterns, `undocumented`, or `none` |
| `first_fraud_txn_id` | str | ClosedCase.first_fraud_txn_id | |
| `txn_ids` | pipe-separated | → INVOLVES edges | TransactionIDs involved |
| `n_txns` | int | ClosedCase.n_txns | |
| `exposure_usd` | float | ClosedCase.exposure_usd | |
| `connected_card_ids` | str | → CONNECTED_TO edges | Other linked cards |
| `actions_taken` | pipe-separated | ClosedCase.actions_taken | e.g. "CREATE_CASE\|BLOCK_CARD" |
| `report_filed` | str | ClosedCase.report_filed | "Yes" or "No" |
| `analyst_notes` | str | ClosedCase.analyst_notes | Free text |

## case_pack.csv → Case triggers

| CSV Column | Type | Notes |
|---|---|---|
| `case_id` | str | Format: HHG-001 to HHG-020 |
| `opened_at` | datetime | Nov-Dec 2016 |
| `trigger_type` | str | `risk_score`, `customer_report`, or `analyst_request` |
| `trigger_text` | str | Human-readable trigger description |
| `flagged_txn_id` | str | Starting point for investigation |
| `card_id` | str | Format: C01234-K1 |
| `customer_id` | str | Format: C01234 |
| `risk_score` | float | Only for risk_score triggers |
