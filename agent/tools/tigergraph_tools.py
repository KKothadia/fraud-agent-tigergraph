"""
TigerGraph tool wrappers — query the graph as agent tools.

Provides both a direct pyTigerGraph connection and fallback to a
pandas-based local data layer for development without TigerGraph.

The agent calls these tools by name; the MCP server (if used) exposes
the same interface.
"""

from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import numpy as np

from agent import config

logger = logging.getLogger(__name__)


class DataLayer:
    """
    Unified data access layer. Uses pandas DataFrames loaded from CSVs
    as the primary data source. When TigerGraph is available, queries
    are forwarded there; otherwise, equivalent pandas operations run.

    This design lets the agent work end-to-end on local data without
    requiring a TigerGraph instance, while being wire-compatible with
    TG MCP tools.
    """

    def __init__(self, dataset_dir: str | None = None):
        self.dataset_dir = dataset_dir or str(config.DATASET_DIR)
        self._transactions: Optional[pd.DataFrame] = None
        self._identity: Optional[pd.DataFrame] = None
        self._closed_cases: Optional[pd.DataFrame] = None
        self._case_pack: Optional[pd.DataFrame] = None
        self._tg_conn = None
        self.tool_call_count = 0

    # ── Lazy loading ───────────────────────────────────────────────────────

    @property
    def transactions(self) -> pd.DataFrame:
        if self._transactions is None:
            logger.info("Loading transactions.csv (~590k rows)...")
            path = os.path.join(self.dataset_dir, config.TRANSACTIONS_FILE)
            self._transactions = pd.read_csv(path, low_memory=False)
            self._transactions["ts"] = pd.to_datetime(self._transactions["ts"])
            self._transactions["TransactionID"] = self._transactions["TransactionID"].astype(str)
            self._transactions["customer_id"] = self._transactions["customer_id"].astype(str)
            logger.info(f"Loaded {len(self._transactions)} transactions")
        return self._transactions

    @property
    def identity(self) -> pd.DataFrame:
        if self._identity is None:
            logger.info("Loading identity.csv...")
            path = os.path.join(self.dataset_dir, config.IDENTITY_FILE)
            self._identity = pd.read_csv(path, low_memory=False)
            self._identity["TransactionID"] = self._identity["TransactionID"].astype(str)
            logger.info(f"Loaded {len(self._identity)} identity records")
        return self._identity

    @property
    def closed_cases(self) -> pd.DataFrame:
        if self._closed_cases is None:
            logger.info("Loading closed_cases_history.csv...")
            path = os.path.join(self.dataset_dir, config.CLOSED_CASES_FILE)
            self._closed_cases = pd.read_csv(path, low_memory=False)
            self._closed_cases["opened_at"] = pd.to_datetime(self._closed_cases["opened_at"])
            self._closed_cases["closed_at"] = pd.to_datetime(self._closed_cases["closed_at"])
            logger.info(f"Loaded {len(self._closed_cases)} closed cases")
        return self._closed_cases

    @property
    def case_pack(self) -> pd.DataFrame:
        if self._case_pack is None:
            path = os.path.join(self.dataset_dir, config.CASE_PACK_FILE)
            self._case_pack = pd.read_csv(path, low_memory=False)
            self._case_pack["flagged_txn_id"] = self._case_pack["flagged_txn_id"].astype(str)
            self._case_pack["customer_id"] = self._case_pack["customer_id"].astype(str)
        return self._case_pack

    # ── Tool functions (graph-equivalent queries) ──────────────────────────

    def get_transaction(self, txn_id: str) -> dict[str, Any]:
        """Get full details of a single transaction."""
        self.tool_call_count += 1
        txn_id = str(txn_id)
        row = self.transactions[self.transactions["TransactionID"] == txn_id]
        if row.empty:
            return {"error": f"Transaction {txn_id} not found"}
        result = row.iloc[0].to_dict()
        # Clean NaN values
        result = {k: (None if pd.isna(v) else v) for k, v in result.items()}
        return result

    def get_card_history(
        self, card_id: str, days: int = 90
    ) -> list[dict[str, Any]]:
        """
        Get transaction history for a card.
        card_id format: e.g. "C12382-K1" → customer_id="C12382"
        We filter by customer_id and card fields.
        """
        self.tool_call_count += 1
        customer_id = card_id.split("-")[0] if "-" in card_id else card_id
        card_suffix = card_id.split("-")[1] if "-" in card_id else None

        # Filter by customer
        mask = self.transactions["customer_id"] == customer_id
        card_txns = self.transactions[mask].copy()

        if card_txns.empty:
            return []

        # Sort by timestamp
        card_txns = card_txns.sort_values("ts")

        # Select key columns for history
        cols = [
            "TransactionID", "TransactionAmt", "ProductCD", "ts",
            "channel", "risk_score", "addr1", "addr2",
            "P_emaildomain", "R_emaildomain",
            "C1", "C2", "D1",
            "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9",
        ]
        available_cols = [c for c in cols if c in card_txns.columns]
        result = card_txns[available_cols].to_dict("records")

        # Clean NaN
        for r in result:
            for k, v in r.items():
                if isinstance(v, float) and np.isnan(v):
                    r[k] = None
                elif isinstance(v, pd.Timestamp):
                    r[k] = str(v)

        return result

    def get_customer_profile(self, customer_id: str) -> dict[str, Any]:
        """Build a customer profile from their transaction history."""
        self.tool_call_count += 1
        mask = self.transactions["customer_id"] == customer_id
        cust_txns = self.transactions[mask]

        if cust_txns.empty:
            return {"error": f"Customer {customer_id} not found"}

        profile = {
            "customer_id": customer_id,
            "total_transactions": len(cust_txns),
            "first_seen": str(cust_txns["ts"].min()),
            "last_seen": str(cust_txns["ts"].max()),
            "total_amount": float(cust_txns["TransactionAmt"].sum()),
            "avg_amount": float(cust_txns["TransactionAmt"].mean()),
            "max_amount": float(cust_txns["TransactionAmt"].max()),
            "min_amount": float(cust_txns["TransactionAmt"].min()),
            "channels": cust_txns["channel"].value_counts().to_dict(),
            "product_codes": cust_txns["ProductCD"].value_counts().to_dict(),
            "avg_risk_score": float(cust_txns["risk_score"].mean()),
            "unique_regions": cust_txns["addr1"].dropna().nunique(),
            "primary_region": (
                str(cust_txns["addr1"].mode().iloc[0])
                if not cust_txns["addr1"].mode().empty
                else None
            ),
            "unique_email_domains": cust_txns["P_emaildomain"].dropna().nunique(),
        }

        return profile

    def get_device_info(self, txn_id: str) -> dict[str, Any]:
        """Get device/identity info for a transaction (online only)."""
        self.tool_call_count += 1
        txn_id = str(txn_id)
        row = self.identity[self.identity["TransactionID"] == txn_id]
        if row.empty:
            return {"has_identity": False, "note": "No identity record (likely in-person transaction)"}

        result = row.iloc[0].to_dict()
        result["has_identity"] = True
        result = {k: (None if pd.isna(v) else v) for k, v in result.items()}

        # Build device profile string
        device_parts = []
        if result.get("DeviceInfo"):
            device_parts.append(str(result["DeviceInfo"]))
        if result.get("id_30"):
            device_parts.append(str(result["id_30"]))
        if result.get("id_31"):
            device_parts.append(str(result["id_31"]))
        if result.get("id_33"):
            device_parts.append(str(result["id_33"]))
        result["device_profile_string"] = " | ".join(device_parts) if device_parts else ""

        return result

    def find_device_neighbors(self, device_info: str) -> list[dict[str, Any]]:
        """Find all transactions sharing the same DeviceInfo."""
        self.tool_call_count += 1
        if not device_info:
            return []

        mask = self.identity["DeviceInfo"] == device_info
        matches = self.identity[mask]

        if matches.empty:
            return []

        # Get the transactions for these
        txn_ids = matches["TransactionID"].tolist()
        txn_mask = self.transactions["TransactionID"].isin(txn_ids)
        neighbor_txns = self.transactions[txn_mask]

        results = []
        for _, row in neighbor_txns.iterrows():
            results.append({
                "TransactionID": str(row["TransactionID"]),
                "customer_id": str(row["customer_id"]),
                "TransactionAmt": float(row["TransactionAmt"]),
                "ts": str(row["ts"]),
                "channel": str(row["channel"]),
                "risk_score": float(row["risk_score"]) if pd.notna(row["risk_score"]) else None,
            })

        return results

    def detect_card_testing(
        self, card_id: str, window_minutes: int = 60
    ) -> dict[str, Any]:
        """
        P1: Card testing detection.
        ≥3 small online txns within window, followed by larger purchase.
        """
        self.tool_call_count += 1
        customer_id = card_id.split("-")[0] if "-" in card_id else card_id
        mask = self.transactions["customer_id"] == customer_id
        card_txns = self.transactions[mask].sort_values("ts")

        if len(card_txns) < 4:
            return {"matched": False, "confidence": 0.0}

        # Look for sequences: 3+ small online txns in window, then larger
        online_txns = card_txns[card_txns["channel"] == "online"]
        small_threshold = config.CARD_TESTING_SMALL_TXN_LIMIT
        min_count = config.CARD_TESTING_MIN_COUNT

        for i in range(len(online_txns) - min_count):
            window_start = online_txns.iloc[i]["ts"]
            window_end = window_start + timedelta(minutes=window_minutes)

            window_txns = online_txns[
                (online_txns["ts"] >= window_start) &
                (online_txns["ts"] <= window_end)
            ]

            small_txns = window_txns[window_txns["TransactionAmt"] <= small_threshold]

            if len(small_txns) >= min_count:
                # Look for a larger purchase after the window
                after_window = card_txns[card_txns["ts"] > window_end]
                large_after = after_window[after_window["TransactionAmt"] > 20]

                if not large_after.empty:
                    all_txn_ids = (
                        small_txns["TransactionID"].tolist()
                        + [str(large_after.iloc[0]["TransactionID"])]
                    )
                    total_amount = (
                        small_txns["TransactionAmt"].sum()
                        + large_after.iloc[0]["TransactionAmt"]
                    )
                    return {
                        "matched": True,
                        "confidence": 0.80,
                        "txn_ids": all_txn_ids,
                        "small_txn_count": len(small_txns),
                        "large_purchase_amount": float(large_after.iloc[0]["TransactionAmt"]),
                        "total_amount": float(total_amount),
                        "window_minutes": window_minutes,
                    }

        return {"matched": False, "confidence": 0.0}

    def detect_cnp_fraud(
        self, card_id: str, txn_id: str
    ) -> dict[str, Any]:
        """
        P2: Card-not-present fraud.
        Online txns with amounts/products inconsistent with history.
        """
        self.tool_call_count += 1
        customer_id = card_id.split("-")[0] if "-" in card_id else card_id
        mask = self.transactions["customer_id"] == customer_id
        card_txns = self.transactions[mask].sort_values("ts")

        if card_txns.empty:
            return {"matched": False, "confidence": 0.0}

        # Get the flagged transaction
        flagged = card_txns[card_txns["TransactionID"] == str(txn_id)]
        if flagged.empty:
            return {"matched": False, "confidence": 0.0}

        flagged_row = flagged.iloc[0]
        flagged_ts = flagged_row["ts"]
        flagged_amt = flagged_row["TransactionAmt"]

        # Only for online transactions
        if flagged_row["channel"] != "online":
            return {"matched": False, "confidence": 0.0, "note": "in-person transaction"}

        # Calculate baseline
        history = card_txns[card_txns["ts"] < flagged_ts]
        if len(history) < 3:
            baseline_avg = flagged_amt  # not enough history
            baseline_std = flagged_amt * 0.5
        else:
            baseline_avg = history["TransactionAmt"].mean()
            baseline_std = history["TransactionAmt"].std()
            if baseline_std == 0:
                baseline_std = baseline_avg * 0.3

        # Amount deviation
        z_score = abs(flagged_amt - baseline_avg) / max(baseline_std, 0.01)

        # Look for burst: 2-4 txns within 48h
        burst_window = timedelta(hours=config.VELOCITY_BURST_WINDOW_HOURS)
        burst_txns = card_txns[
            (card_txns["ts"] >= flagged_ts - burst_window) &
            (card_txns["ts"] <= flagged_ts + burst_window) &
            (card_txns["channel"] == "online")
        ]

        # Product code anomaly
        common_products = history["ProductCD"].value_counts()
        product_is_new = (
            flagged_row["ProductCD"] not in common_products.index
            if not common_products.empty else False
        )

        confidence = 0.0
        indicators = []

        if z_score > 2.0:
            confidence += 0.25
            indicators.append(f"amount ${flagged_amt:.2f} is {z_score:.1f} std devs from baseline ${baseline_avg:.2f}")

        if len(burst_txns) >= config.VELOCITY_BURST_MIN_COUNT:
            confidence += 0.20
            indicators.append(f"{len(burst_txns)} online txns within {config.VELOCITY_BURST_WINDOW_HOURS}h window")

        if product_is_new:
            confidence += 0.15
            indicators.append(f"product code '{flagged_row['ProductCD']}' never used before")

        confidence = min(confidence, 0.95)

        burst_txn_ids = burst_txns["TransactionID"].tolist() if len(burst_txns) >= 2 else [str(txn_id)]

        return {
            "matched": confidence > 0.30,
            "confidence": round(confidence, 2),
            "txn_ids": burst_txn_ids,
            "indicators": indicators,
            "baseline_avg": round(float(baseline_avg), 2),
            "baseline_std": round(float(baseline_std), 2),
            "z_score": round(float(z_score), 2),
            "burst_count": len(burst_txns),
            "product_is_new": product_is_new,
        }

    def detect_cnp_new_device(
        self, card_id: str, txn_id: str
    ) -> dict[str, Any]:
        """
        P3: CNP fraud from new device.
        P2 conditions + device marked as New, possibly behind proxy.
        """
        self.tool_call_count += 1

        # First run P2
        cnp_result = self.detect_cnp_fraud(card_id, txn_id)
        self.tool_call_count -= 1  # Don't double count

        # Get device info
        device = self.get_device_info(txn_id)
        self.tool_call_count -= 1

        if not device.get("has_identity"):
            return {**cnp_result, "device_new": False, "proxy_flag": None}

        is_new = device.get("id_15") == "New"
        proxy = device.get("id_23", "")
        is_proxy = proxy in ("anonymous", "hidden")

        confidence = cnp_result.get("confidence", 0.0)
        indicators = list(cnp_result.get("indicators", []))

        if is_new:
            confidence += 0.20
            indicators.append("device marked 'New' for this account")

        if is_proxy:
            confidence += 0.15
            indicators.append(f"proxy detected: {proxy}")

        confidence = min(confidence, 0.95)

        return {
            "matched": confidence > 0.40,
            "confidence": round(confidence, 2),
            "txn_ids": cnp_result.get("txn_ids", [str(txn_id)]),
            "indicators": indicators,
            "device_new": is_new,
            "proxy_flag": proxy if proxy else None,
            "device_profile": device.get("device_profile_string", ""),
        }

    def detect_out_of_region(
        self, card_id: str, txn_id: str
    ) -> dict[str, Any]:
        """
        P4: Out-of-region use.
        Card-present txns in an unfamiliar billing region while normal
        activity continues at home.
        """
        self.tool_call_count += 1
        customer_id = card_id.split("-")[0] if "-" in card_id else card_id
        mask = self.transactions["customer_id"] == customer_id
        card_txns = self.transactions[mask].sort_values("ts")

        flagged = card_txns[card_txns["TransactionID"] == str(txn_id)]
        if flagged.empty:
            return {"matched": False, "confidence": 0.0}

        flagged_row = flagged.iloc[0]
        flagged_region = flagged_row.get("addr1")
        flagged_ts = flagged_row["ts"]

        if pd.isna(flagged_region):
            return {"matched": False, "confidence": 0.0, "note": "no billing region"}

        # Get history regions
        history = card_txns[card_txns["ts"] < flagged_ts]
        known_regions = set(history["addr1"].dropna().unique())

        if flagged_region in known_regions:
            return {"matched": False, "confidence": 0.0, "note": "region is familiar"}

        # Check if normal activity continues at home
        home_region = history["addr1"].mode()
        if home_region.empty:
            return {"matched": False, "confidence": 0.1, "note": "no established home region"}

        home_region = home_region.iloc[0]

        # Look for concurrent activity in home region
        window = timedelta(days=7)
        concurrent = card_txns[
            (card_txns["ts"] >= flagged_ts - window) &
            (card_txns["ts"] <= flagged_ts + window) &
            (card_txns["addr1"] == home_region)
        ]

        confidence = 0.30  # Base for unfamiliar region
        indicators = [f"transaction in unfamiliar billing region {flagged_region}"]

        if len(concurrent) > 0:
            confidence += 0.30
            indicators.append(f"concurrent activity in home region {home_region} suggests card cloned")

        # Card-present makes it stronger
        if flagged_row.get("channel") == "in_person":
            confidence += 0.15
            indicators.append("card-present purchase in foreign region")

        confidence = min(confidence, 0.95)

        return {
            "matched": confidence > 0.40,
            "confidence": round(confidence, 2),
            "txn_ids": [str(txn_id)],
            "indicators": indicators,
            "foreign_region": str(flagged_region),
            "home_region": str(home_region),
            "concurrent_home_txns": len(concurrent),
        }

    def detect_account_takeover(
        self, card_id: str, txn_id: str
    ) -> dict[str, Any]:
        """
        P5: Account takeover.
        Mixed-channel anomalies + device/match-flag deviations.
        """
        self.tool_call_count += 1
        customer_id = card_id.split("-")[0] if "-" in card_id else card_id
        mask = self.transactions["customer_id"] == customer_id
        card_txns = self.transactions[mask].sort_values("ts")

        flagged = card_txns[card_txns["TransactionID"] == str(txn_id)]
        if flagged.empty:
            return {"matched": False, "confidence": 0.0}

        flagged_row = flagged.iloc[0]
        flagged_ts = flagged_row["ts"]

        history = card_txns[card_txns["ts"] < flagged_ts]
        if len(history) < 5:
            return {"matched": False, "confidence": 0.1, "note": "insufficient history"}

        confidence = 0.0
        indicators = []

        # Check match flags (M1-M9) for mismatches
        m_cols = [f"M{i}" for i in range(1, 10)]
        for col in m_cols:
            if col in flagged_row.index and col in history.columns:
                flagged_val = flagged_row.get(col)
                if pd.notna(flagged_val):
                    hist_mode = history[col].mode()
                    if not hist_mode.empty and flagged_val != hist_mode.iloc[0]:
                        confidence += 0.05
                        indicators.append(f"{col} mismatch: {flagged_val} vs usual {hist_mode.iloc[0]}")

        # Channel change
        hist_channels = history["channel"].value_counts()
        if not hist_channels.empty:
            dominant = hist_channels.index[0]
            if flagged_row["channel"] != dominant and hist_channels.iloc[0] / len(history) > 0.8:
                confidence += 0.15
                indicators.append(f"channel switch from usually {dominant} to {flagged_row['channel']}")

        # Device info check
        device = self.get_device_info(txn_id)
        self.tool_call_count -= 1  # Don't double-count nested call

        if device.get("has_identity") and device.get("id_15") == "New":
            confidence += 0.15
            indicators.append("new device for this account")

        # Amount deviation
        if len(history) >= 3:
            avg = history["TransactionAmt"].mean()
            std = history["TransactionAmt"].std()
            if std > 0:
                z = abs(flagged_row["TransactionAmt"] - avg) / std
                if z > 3.0:
                    confidence += 0.15
                    indicators.append(f"amount ${flagged_row['TransactionAmt']:.2f} is {z:.1f}σ from average ${avg:.2f}")

        confidence = min(confidence, 0.95)

        return {
            "matched": confidence > 0.35,
            "confidence": round(confidence, 2),
            "txn_ids": [str(txn_id)],
            "indicators": indicators,
        }

    def find_similar_closed_cases(
        self,
        pattern: str = "",
        customer_id: str = "",
        card_id: str = "",
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Retrieve similar closed cases from history.
        Matches on pattern, customer, or card.
        """
        self.tool_call_count += 1
        cases = self.closed_cases.copy()

        # Score relevance
        scores = pd.Series(0.0, index=cases.index)

        if pattern and pattern != "none":
            scores += (cases["pattern"] == pattern).astype(float) * 3.0

        if customer_id:
            scores += (cases["customer_id"] == customer_id).astype(float) * 5.0

        if card_id:
            scores += (cases["card_id"] == card_id).astype(float) * 5.0
            # Also check connected cards
            connected_mask = cases["connected_card_ids"].fillna("").str.contains(card_id, na=False)
            scores += connected_mask.astype(float) * 3.0

        # Only return cases with some relevance
        relevant = cases[scores > 0].copy()
        relevant["relevance_score"] = scores[scores > 0]
        relevant = relevant.sort_values("relevance_score", ascending=False).head(k)

        results = []
        for _, row in relevant.iterrows():
            result = {
                "case_id": str(row["case_id"]),
                "customer_id": str(row["customer_id"]),
                "card_id": str(row["card_id"]),
                "outcome": str(row["outcome"]),
                "pattern": str(row["pattern"]),
                "exposure_usd": float(row["exposure_usd"]) if pd.notna(row["exposure_usd"]) else 0,
                "actions_taken": str(row["actions_taken"]) if pd.notna(row["actions_taken"]) else "",
                "analyst_notes": str(row["analyst_notes"]) if pd.notna(row["analyst_notes"]) else "",
                "relevance_score": float(row["relevance_score"]),
            }
            results.append(result)

        return results

    def get_neighborhood(
        self, txn_id: str, hops: int = 2
    ) -> dict[str, Any]:
        """
        2-hop neighborhood traversal from a transaction.
        txn → card → customer → other cards → their txns → devices → regions.
        """
        self.tool_call_count += 1
        txn_id = str(txn_id)

        # Get the transaction
        txn = self.get_transaction(txn_id)
        self.tool_call_count -= 1
        if "error" in txn:
            return txn

        customer_id = txn.get("customer_id", "")

        # Get customer profile
        profile = self.get_customer_profile(customer_id)
        self.tool_call_count -= 1

        # Get device info
        device = self.get_device_info(txn_id)
        self.tool_call_count -= 1

        # Find device neighbors if device info exists
        device_neighbors = []
        if device.get("has_identity") and device.get("DeviceInfo"):
            device_neighbors = self.find_device_neighbors(device["DeviceInfo"])
            self.tool_call_count -= 1
            # Filter to different customers only
            device_neighbors = [
                n for n in device_neighbors
                if n["customer_id"] != customer_id
            ]

        # Get billing region info
        region = str(txn.get("addr1", ""))
        region_info = {"region": region}

        return {
            "transaction": txn,
            "customer_profile": profile,
            "device_info": device,
            "device_neighbors": device_neighbors[:10],  # Cap at 10
            "billing_region": region_info,
            "connected_customers": list(set(
                n["customer_id"] for n in device_neighbors
            )),
        }

    def run_all_pattern_checks(
        self, card_id: str, txn_id: str
    ) -> list[dict[str, Any]]:
        """Run all 5 pattern detection queries and return results."""
        self.tool_call_count += 1
        results = []

        # P1: Card testing
        p1 = self.detect_card_testing(card_id)
        p1["pattern"] = "card_testing"
        results.append(p1)

        # P2: CNP fraud
        p2 = self.detect_cnp_fraud(card_id, txn_id)
        p2["pattern"] = "card_not_present_fraud"
        results.append(p2)

        # P3: CNP + new device
        p3 = self.detect_cnp_new_device(card_id, txn_id)
        p3["pattern"] = "card_not_present_new_device"
        results.append(p3)

        # P4: Out-of-region
        p4 = self.detect_out_of_region(card_id, txn_id)
        p4["pattern"] = "out_of_region_use"
        results.append(p4)

        # P5: Account takeover
        p5 = self.detect_account_takeover(card_id, txn_id)
        p5["pattern"] = "account_takeover"
        results.append(p5)

        return results
