# GSQL Fraud Pattern Specifications

The 5 known fraud patterns from the README, with their GSQL-detectable signatures and implementation details.

## P1: Card Testing (`card_testing`)

**Description:** A stolen card number is checked before use: 3+ tiny online authorizations (under $5), then a larger purchase.

**Graph signature:**
- Per-card sliding window: ≥3 transactions with amount ≤ $5.00 within 60 minutes
- All small transactions are online (`channel = "online"`)
- Followed by a larger purchase (> $20)

**GSQL approach:** Temporal aggregation on `MADE` edges, group by card, sliding window with amount filter.

**Evidence output:**
```json
{
    "matched": true,
    "confidence": 0.80,
    "txn_ids": ["T001", "T002", "T003", "T004"],
    "small_txn_count": 3,
    "large_purchase_amount": 259.98,
    "total_amount": 264.43,
    "window_minutes": 60
}
```

**Policy rule:** R5

---

## P2: Card-Not-Present Fraud (`card_not_present_fraud`)

**Description:** Card number used online without the card. Amounts and products inconsistent with cardholder history, often in a burst of 2-4 within 48 hours.

**Graph signature:**
- Online transaction (`channel = "online"`)
- Amount deviates significantly from baseline (z-score > 2.0)
- Product code not in cardholder's history
- Burst of 2+ online txns within 48h

**GSQL approach:** Per-card baseline computation (avg, std of amounts), product code frequency analysis, temporal burst detection.

**Evidence output:**
```json
{
    "matched": true,
    "confidence": 0.60,
    "txn_ids": ["T001", "T002"],
    "indicators": ["amount $292.36 is 3.2 std devs from baseline $45.10"],
    "baseline_avg": 45.10,
    "z_score": 3.2,
    "burst_count": 2,
    "product_is_new": true
}
```

**Policy rule:** R1-R4

---

## P3: Card-Not-Present from New Device (`card_not_present_new_device`)

**Description:** Same as P2, with identity record marking device as `New`, sometimes behind a proxy.

**Graph signature:**
- All P2 conditions
- Identity record: `id_15 = "New"` (device new to this account)
- Optional: `id_23 = "anonymous"` or `"hidden"` (proxy detected)

**GSQL approach:** P2 query + join with identity table on `FROM_DEVICE` edge.

**Evidence output:**
```json
{
    "matched": true,
    "confidence": 0.75,
    "txn_ids": ["T001"],
    "indicators": ["device marked 'New' for this account", "proxy detected: anonymous"],
    "device_new": true,
    "proxy_flag": "anonymous",
    "device_profile": "SAMSUNG SM-G892A | Android 7.0 | samsung browser 6.2 | 2220x1080"
}
```

**Policy rule:** R1-R4, R6 (if shared device)

---

## P4: Out-of-Region Use (`out_of_region_use`)

**Description:** Card-present purchases in a billing region the cardholder has no history in, while normal activity continues at home.

**Graph signature:**
- Transaction in billing region (`addr1`) not in cardholder's historical regions
- Concurrent activity in home region within ±7 days
- Card-present transaction (`channel = "in_person"`)

**GSQL approach:** Per-card region history query, concurrent home-region activity check.

**Evidence output:**
```json
{
    "matched": true,
    "confidence": 0.75,
    "txn_ids": ["T001"],
    "indicators": ["transaction in unfamiliar region 494", "concurrent activity in home region 325"],
    "foreign_region": "494",
    "home_region": "325",
    "concurrent_home_txns": 3
}
```

**Policy rule:** R2, R3

---

## P5: Account Takeover (`account_takeover`)

**Description:** Mixed-channel activity inconsistent with the cardholder, with device and match-flag anomalies, pointing to stolen credentials.

**Graph signature:**
- Channel switch from dominant historical channel
- Match flag (M1-M9) mismatches vs. history
- New device (`id_15 = "New"`)
- Amount deviation from baseline (z-score > 3.0)

**GSQL approach:** Per-customer behavioral baseline, match-flag comparison, device history analysis.

**Evidence output:**
```json
{
    "matched": true,
    "confidence": 0.55,
    "txn_ids": ["T001"],
    "indicators": ["M4 mismatch: F vs usual T", "channel switch from in_person to online", "new device"]
}
```

**Policy rule:** R1-R4, R6, R8
