# Fraud Policy Matrix

Extracted from the README Fraud Policy section. This is the authoritative reference for the policy engine code in `agent/policy/rules.py`.

## Actions (Policy §1)

| Action | Approval | Customer Impact | Notes |
|---|---|---|---|
| `ALLOW_TRANSACTION` | auto | None | Let the flagged transaction stand |
| `DECLINE_TRANSACTION` | L1 | Low | Decline flagged authorization only; card stays active |
| `MONITOR_CARD` | auto | None | Raise monitoring sensitivity for 72 hours |
| `MONITOR_CONNECTED_CARDS` | auto | None | Monitor cards sharing device/region/ring |
| `WARN_CUSTOMER` | auto | None | Informational message |
| `VERIFY_WITH_CUSTOMER` | auto | Low | Ask if they made the transaction |
| `STEP_UP_AUTH` | auto | Low | Require OTP before further activity |
| `BLOCK_CARD` | L1 (≤$2,500) / L2 (>$2,500) | High | Block and reissue |
| `BLOCK_ALL_CARDS` | L2 always | Very High | Block every card the customer holds |
| `GENERATE_REPORT` | auto | None | Internal record without opening a case |
| `CREATE_CASE` | auto | None | Open an internal fraud case |
| `FILE_REPORT` | L2 always | None | Regulatory SAR filing |
| `ESCALATE_TO_ANALYST` | auto | None | Hand to human analyst |
| `CLOSE_NO_FRAUD` | auto | None | Close alert as legitimate |

## Rules (Policy §3)

| Rule | Condition | Actions | Key Threshold |
|---|---|---|---|
| **R1** | Single signal, probability < 0.70 | `VERIFY_WITH_CUSTOMER` or `STEP_UP_AUTH` | prob < 0.70, evidence_count ≤ 1 |
| **R2** | Customer denies transaction | `BLOCK_CARD` + `CREATE_CASE` + maybe `FILE_REPORT` | exposure > $1,000 or shared device → FILE_REPORT |
| **R3** | Customer confirms transaction | `CLOSE_NO_FRAUD` | — |
| **R4** | No reply within 24h | `MONITOR_CARD` + `DECLINE_TRANSACTION` + maybe escalate | exposure > $500 → escalate |
| **R5** | Card testing (3+ small txns + large) | `DECLINE_TRANSACTION` + `STEP_UP_AUTH` + maybe `BLOCK_CARD` | purchase > $100 cleared → BLOCK_CARD |
| **R6** | Shared device/region/email | `CREATE_CASE` + `FILE_REPORT` + `MONITOR_CONNECTED_CARDS` | — |
| **R7** | Disputed but recurring pattern | `CREATE_CASE` + `VERIFY_WITH_CUSTOMER` + `WARN_CUSTOMER` | Do NOT block |
| **R8** | Uncertain + exposed | `ESCALATE_TO_ANALYST` | exposure > $500 or evidence conflicts |
| **R9** | Undocumented pattern | `CREATE_CASE` + `FILE_REPORT` + `ESCALATE_TO_ANALYST` | — |
| **R10** | BLOCK_ALL_CARDS guard | Only if ≥2 cards confirmed fraud or credentials compromised | — |

## Case vs Report (§3a)

| Deliverable | When to create | Threshold |
|---|---|---|
| **Case** (`CREATE_CASE`) | fraud_probability ≥ 0.30, or evidence requested, or customer disputes | prob ≥ 0.30 |
| **Report** (`FILE_REPORT`) | Confirmed/strongly suspected AND (exposure > $1,000 OR shared device/region OR connected fraud OR undocumented pattern) | Multiple conditions |

## Stop Conditions (§6)

| Condition | Criteria |
|---|---|
| High/low confidence | prob ≥ 0.85 or ≤ 0.15, with ≥2 independent evidence pieces |
| Verification settles | Customer response resolves the question |
| No further value | Further steps unlikely to change the decision |
