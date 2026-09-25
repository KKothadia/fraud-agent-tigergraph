import os
import json
import sys
import time
import pandas as pd
import pyTigerGraph as tg
from dotenv import load_dotenv

def main():
    load_dotenv()
    
    # 2. Setup paths
    dataset_dir = os.getenv("DATASET_DIR", "../TASK 4/HHGOA_IEEE")
    txns_path = os.path.join(dataset_dir, "transactions.csv")
    identity_path = os.path.join(dataset_dir, "identity.csv")
    closed_cases_path = os.path.join(dataset_dir, "closed_cases_history.csv")
    
    checkpoint_file = os.path.join(dataset_dir, "load_checkpoint.json")

    if "--reset-checkpoint" in sys.argv:
        if os.path.exists(checkpoint_file):
            os.remove(checkpoint_file)
            print("Checkpoint reset.")
        else:
            print("No checkpoint found to reset.")
            
        for tmp_file in ["tmp_mapping_df.csv", "tmp_customers.csv", "tmp_cards.csv"]:
            p = os.path.join(dataset_dir, tmp_file)
            if os.path.exists(p):
                os.remove(p)
                print(f"Removed cache file {tmp_file}")
        return

    def load_checkpoint():
        if os.path.exists(checkpoint_file):
            with open(checkpoint_file, 'r') as f:
                return json.load(f)
        return {
            "customer_aggregation_complete": False,
            "customers_upsert_complete": False,
            "cards_upsert_complete": False,
            "identity_complete": False,
            "transaction_chunks_completed": [],
            "closed_cases_complete": False,
            "fraud_patterns_complete": False
        }

    def save_checkpoint(state):
        tmp = checkpoint_file + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, checkpoint_file)

    checkpoint = load_checkpoint()

    if "--status" in sys.argv:
        print(json.dumps(checkpoint, indent=2))
        return

    if os.path.exists(checkpoint_file):
        print("[RESUME] Loaded checkpoint")

    # 1. Connect to TigerGraph
    print("Connecting to TigerGraph...")
    tg_host = os.getenv("TG_HOST")
    tg_graph_name = os.getenv("TG_GRAPH_NAME", "FraudGraph")
    tg_secret = os.getenv("TG_SECRET")
    
    conn = tg.TigerGraphConnection(
        host=tg_host,
        graphname=tg_graph_name,
        gsqlSecret=tg_secret
    )
    
    try:
        token_res = conn.getToken(tg_secret)
        conn.apiToken = token_res[0] if isinstance(token_res, tuple) else token_res
    except Exception as e:
        print(f"Failed to authenticate with TigerGraph. Check if your TG_SECRET is correct.")
        print(f"Error type: {type(e).__name__}")
        return
    
    if not os.path.exists(txns_path):
        print(f"Error: Could not find transactions.csv at {txns_path}")
        return

    # Option for small validation run
    limit_chunks_env = os.getenv("TG_LIMIT_CHUNKS")
    limit_chunks = int(limit_chunks_env) if limit_chunks_env else None
    chunk_size = 50000

    batch_size_env = int(os.getenv("TG_UPSERT_BATCH_SIZE", "1000"))

    # Hard timeout (seconds) for a single upsert call. Large batches on slow links
    # can take a while, but we must never hang indefinitely on a stalled SSL socket.
    CALL_TIMEOUT_S = int(os.getenv("TG_CALL_TIMEOUT", "180"))  # 3 minutes per call

    import concurrent.futures

    def resilient_retry(operation_name, func, *args, **kwargs):
        """
        Call func(*args, **kwargs) with a hard per-call timeout.
        On transient failure, retry with exponential backoff.
        On permanent failure (schema/type error), raise immediately.
        All 5 attempts exhausted -> raise the last exception.

        Uses ThreadPoolExecutor with shutdown(wait=False) on timeout so that a
        stalled SSL/TCP socket in the background thread never blocks the retry loop.
        """
        max_retries = 5
        backoff_times = [2, 5, 10, 20, 40]

        last_exc = None
        for attempt in range(max_retries):
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(func, *args, **kwargs)
            try:
                result = future.result(timeout=CALL_TIMEOUT_S)
                executor.shutdown(wait=False)
                return result
            except concurrent.futures.TimeoutError:
                # Hard deadline exceeded — abandon the stuck thread and move on.
                executor.shutdown(wait=False)
                exc_name = "CallTimeout"
                exc_msg = f"Call did not complete within {CALL_TIMEOUT_S}s"
                last_exc = TimeoutError(exc_msg)
            except Exception as e:
                executor.shutdown(wait=False)
                exc_name = type(e).__name__
                exc_msg = str(e)
                last_exc = e

                # Do NOT retry permanent schema/type/data errors.
                err_lower = exc_msg.lower()
                if any(tok in err_lower for tok in ("rest-30200", "type conversion",
                                                     "invalid json", "no such vertex type",
                                                     "no such edge type")):
                    print(f"[FATAL] Permanent error in {operation_name}: {exc_name}: {exc_msg}")
                    raise e

            if attempt == max_retries - 1:
                print(f"[FATAL] All {max_retries} retries exhausted for {operation_name}. Last error: {exc_name}")
                raise last_exc

            sleep_time = backoff_times[attempt]
            print(f"[RETRY] TigerGraph request failed ({operation_name}); retry {attempt+1}/{max_retries} in {sleep_time}s: {exc_name}")
            time.sleep(sleep_time)

    def chunked_upsert_vertex(connection, dataframe, vertex_type, v_id_col, attr_dict, batch_size=batch_size_env):
        if dataframe.empty: return
        total = len(dataframe)
        for start_idx in range(0, total, batch_size):
            batch = dataframe.iloc[start_idx:start_idx+batch_size]
            batch_num = start_idx // batch_size + 1
            batch_total = (total + batch_size - 1) // batch_size
            op = f"Vertex {vertex_type} batch {batch_num}/{batch_total} ({len(batch)} rows)"
            resilient_retry(op, connection.upsertVertexDataFrame,
                            df=batch, vertexType=vertex_type, v_id=v_id_col, attributes=attr_dict)
            time.sleep(0.1)  # Rate limit

    def chunked_upsert_edge(connection, dataframe, source_type, edge_type, target_type, from_col, to_col, attr_dict, batch_size=batch_size_env):
        if dataframe.empty: return
        total = len(dataframe)
        for start_idx in range(0, total, batch_size):
            batch = dataframe.iloc[start_idx:start_idx+batch_size]
            batch_num = start_idx // batch_size + 1
            batch_total = (total + batch_size - 1) // batch_size
            op = f"Edge {source_type}->{edge_type}->{target_type} batch {batch_num}/{batch_total} ({len(batch)} rows)"
            resilient_retry(op, connection.upsertEdgeDataFrame,
                            df=batch, sourceVertexType=source_type, edgeType=edge_type,
                            targetVertexType=target_type, from_id=from_col, to_id=to_col, attributes=attr_dict)
            time.sleep(0.1)  # Rate limit


    # Paths to cache computed aggregation data
    mapping_df_path = os.path.join(dataset_dir, "tmp_mapping_df.csv")
    customers_path = os.path.join(dataset_dir, "tmp_customers.csv")
    cards_path = os.path.join(dataset_dir, "tmp_cards.csv")

    # ---------------------------------------------------------
    # PASS 1: AGGREGATE CUSTOMERS AND CARDS
    # ---------------------------------------------------------
    if not checkpoint["customer_aggregation_complete"]:
        print("Pass 1: Aggregating customers and cards...")
        card_dfs = []
        card_cols = ['customer_id', 'ts', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6']
        
        try:
            chunk_iter = pd.read_csv(txns_path, usecols=card_cols, low_memory=False, chunksize=chunk_size)
        except ValueError as e:
            print(f"Error reading CSV headers: {e}")
            return

        for i, chunk in enumerate(chunk_iter):
            if limit_chunks and i >= limit_chunks:
                break
                
            print(f"Aggregating chunk {i+1}...")
            chunk = chunk[chunk['customer_id'].notna()].copy()
            chunk['customer_id'] = chunk['customer_id'].astype(str)
            chunk['ts'] = pd.to_datetime(chunk['ts'], errors='coerce')
            
            chunk['card1'] = chunk['card1'].fillna(-1).astype(int)
            chunk['card2'] = chunk['card2'].fillna(-1.0)
            chunk['card3'] = chunk['card3'].fillna(-1.0)
            chunk['card4'] = chunk['card4'].fillna("UNKNOWN")
            chunk['card5'] = chunk['card5'].fillna(-1.0)
            chunk['card6'] = chunk['card6'].fillna("UNKNOWN")
            
            agg = chunk.groupby(['customer_id', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6']).agg(
                first_seen=('ts', 'min'),
                last_seen=('ts', 'max'),
                txn_count=('ts', 'count')
            ).reset_index()
            card_dfs.append(agg)

        if not card_dfs:
            print("No valid customer/card data found.")
            return

        print("Merging global customer and card statistics...")
        global_cards = pd.concat(card_dfs)
        global_cards = global_cards.groupby(['customer_id', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6']).agg(
            first_seen=('first_seen', 'min'),
            last_seen=('last_seen', 'max'),
            txn_count=('txn_count', 'sum')
        ).reset_index()

        global_cards = global_cards.sort_values(['customer_id', 'first_seen', 'card1', 'card2'])
        global_cards['card_idx'] = global_cards.groupby('customer_id').cumcount() + 1
        global_cards['card_id'] = global_cards['customer_id'] + '-K' + global_cards['card_idx'].astype(str)

        mapping_df = global_cards[['customer_id', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6', 'card_id']]
        mapping_df.to_csv(mapping_df_path, index=False)

        customers = global_cards.groupby('customer_id').agg(
            first_seen=('first_seen', 'min'),
            last_seen=('last_seen', 'max'),
            total_txn_count=('txn_count', 'sum'),
            card_count=('card_id', 'count')
        ).reset_index()
        customers['first_seen'] = customers['first_seen'].dt.strftime('%Y-%m-%d %H:%M:%S').fillna('1970-01-01 00:00:00')
        customers['last_seen'] = customers['last_seen'].dt.strftime('%Y-%m-%d %H:%M:%S').fillna('1970-01-01 00:00:00')
        customers['risk_tier'] = 'normal'
        customers.to_csv(customers_path, index=False)
        
        cards_upsert = global_cards.copy()
        cards_upsert['first_seen'] = cards_upsert['first_seen'].dt.strftime('%Y-%m-%d %H:%M:%S').fillna('1970-01-01 00:00:00')
        cards_upsert['last_seen'] = cards_upsert['last_seen'].dt.strftime('%Y-%m-%d %H:%M:%S').fillna('1970-01-01 00:00:00')
        cards_upsert.to_csv(cards_path, index=False)

        checkpoint["customer_aggregation_complete"] = True
        save_checkpoint(checkpoint)
        print("[CHECKPOINT] Customer aggregation complete")
    else:
        print("[SKIP] Customer aggregation already complete")
        mapping_df = pd.read_csv(mapping_df_path)
        mapping_df['customer_id'] = mapping_df['customer_id'].astype(str)
        mapping_df['card1'] = mapping_df['card1'].astype(int)
        mapping_df['card2'] = mapping_df['card2'].astype(float)
        mapping_df['card3'] = mapping_df['card3'].astype(float)
        mapping_df['card4'] = mapping_df['card4'].astype(str)
        mapping_df['card5'] = mapping_df['card5'].astype(float)
        mapping_df['card6'] = mapping_df['card6'].astype(str)
        customers = pd.read_csv(customers_path)
        cards_upsert = pd.read_csv(cards_path)

    # Convert mapping_df to index for join
    mapping_df = mapping_df.set_index(['customer_id', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6'])

    if not checkpoint["customers_upsert_complete"]:
        print(f"Upserting {len(customers)} Customers...")
        chunked_upsert_vertex(conn, customers, 'Customer', 'customer_id', {'first_seen':'first_seen', 'last_seen':'last_seen', 'total_txn_count':'total_txn_count', 'risk_tier':'risk_tier', 'card_count':'card_count'})
        checkpoint["customers_upsert_complete"] = True
        save_checkpoint(checkpoint)
        print("[CHECKPOINT] Customers upsert complete")
    else:
        print("[SKIP] Customers already uploaded")

    if not checkpoint["cards_upsert_complete"]:
        print(f"Upserting {len(cards_upsert)} Cards...")
        chunked_upsert_vertex(conn, cards_upsert, 'Card', 'card_id', {'card1':'card1', 'card2':'card2', 'card3':'card3', 'card4':'card4', 'card5':'card5', 'card6':'card6', 'first_seen':'first_seen', 'last_seen':'last_seen', 'txn_count':'txn_count'})
        chunked_upsert_edge(conn, cards_upsert, 'Customer', 'OWNS', 'Card', 'customer_id', 'card_id', {})
        checkpoint["cards_upsert_complete"] = True
        save_checkpoint(checkpoint)
        print("[CHECKPOINT] Cards upsert complete")
    else:
        print("[SKIP] Cards already uploaded")

    # ---------------------------------------------------------
    # PRE-LOAD IDENTITY
    # ---------------------------------------------------------
    device_profiles = pd.DataFrame()
    if os.path.exists(identity_path):
        if not checkpoint["identity_complete"]:
            print("Loading identity profiles...")
            identity_df = pd.read_csv(identity_path, usecols=['TransactionID', 'DeviceType', 'DeviceInfo', 'id_15', 'id_23', 'id_30', 'id_31', 'id_33'], low_memory=False)
            for col in ['DeviceType', 'DeviceInfo', 'id_15', 'id_23', 'id_30', 'id_31', 'id_33']:
                identity_df[col] = identity_df[col].fillna("UNKNOWN")
            
            identity_df['device_profile_id'] = identity_df['DeviceInfo'] + " | " + identity_df['id_30'] + " | " + identity_df['id_31'] + " | " + identity_df['id_33']
            identity_df['TransactionID'] = identity_df['TransactionID'].astype(str)
            
            device_profiles = identity_df.drop_duplicates(subset=['device_profile_id']).copy()
            device_profiles['first_seen'] = '1970-01-01 00:00:00'
            device_profiles['txn_count'] = 1
            
            chunked_upsert_vertex(conn, device_profiles, 'DeviceProfile', 'device_profile_id', {'DeviceType':'DeviceType', 'DeviceInfo':'DeviceInfo', 'os':'id_30', 'browser':'id_31', 'screen':'id_33', 'id_15':'id_15', 'id_23':'id_23', 'first_seen':'first_seen', 'txn_count':'txn_count'})
            checkpoint["identity_complete"] = True
            save_checkpoint(checkpoint)
            print("[CHECKPOINT] Identity complete")
        else:
            print("[SKIP] Identity already uploaded")
            identity_df = pd.read_csv(identity_path, usecols=['TransactionID', 'DeviceInfo', 'id_30', 'id_31', 'id_33'], low_memory=False)
            for col in ['DeviceInfo', 'id_30', 'id_31', 'id_33']:
                identity_df[col] = identity_df[col].fillna("UNKNOWN")
            identity_df['device_profile_id'] = identity_df['DeviceInfo'] + " | " + identity_df['id_30'] + " | " + identity_df['id_31'] + " | " + identity_df['id_33']
            identity_df['TransactionID'] = identity_df['TransactionID'].astype(str)
    else:
        identity_df = pd.DataFrame(columns=['TransactionID', 'device_profile_id'])

    # ---------------------------------------------------------
    # PASS 2: TRANSACTIONS & EDGES
    # ---------------------------------------------------------
    print("Pass 2: Loading transactions and edges...")
    
    core_cols = ['TransactionID', 'TransactionDT', 'TransactionAmt', 'ProductCD', 'addr1', 'addr2', 'dist1', 'dist2', 'P_emaildomain', 'R_emaildomain', 'customer_id', 'ts', 'channel', 'risk_score', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6']
    c_cols = [f'C{i}' for i in range(1, 15)]
    d_cols = [f'D{i}' for i in range(1, 16)]
    m_cols = [f'M{i}' for i in range(1, 10)]
    all_txn_cols = core_cols + c_cols + d_cols + m_cols
    
    last_txn_per_card = {}
    
    chunk_iter = pd.read_csv(txns_path, usecols=all_txn_cols, low_memory=False, chunksize=chunk_size)
    for i, chunk in enumerate(chunk_iter):
        chunk_num = i + 1
        if limit_chunks and chunk_num > limit_chunks:
            print(f"Reached chunk limit of {limit_chunks}. Stopping.")
            break
            
        if chunk_num in checkpoint["transaction_chunks_completed"]:
            print(f"[PASS 2] Chunk {chunk_num} already complete")
            # We still need to maintain last_txn_per_card so subsequent NEXT edges connect properly.
            txns = chunk.copy()
            txns['customer_id'] = txns['customer_id'].fillna("UNKNOWN_CUSTOMER").astype(str)
            txns['card1'] = txns['card1'].fillna(-1).astype(int)
            txns['card2'] = txns['card2'].fillna(-1.0)
            txns['card3'] = txns['card3'].fillna(-1.0)
            txns['card4'] = txns['card4'].fillna("UNKNOWN")
            txns['card5'] = txns['card5'].fillna(-1.0)
            txns['card6'] = txns['card6'].fillna("UNKNOWN")
            txns = txns.join(mapping_df, on=['customer_id', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6'])
            txns['card_id'] = txns['card_id'].fillna("UNKNOWN_CARD")
            txns['TransactionID'] = txns['TransactionID'].apply(lambda x: str(x) if pd.notnull(x) else "UNKNOWN_TXN")
            
            txns_sorted = txns.sort_values(['card_id', 'TransactionDT'])
            for card_id, group in txns_sorted.groupby('card_id'):
                last_txn_per_card[card_id] = {'txn_id': group.iloc[-1]['TransactionID'], 'time': group.iloc[-1]['TransactionDT']}
            continue

        print(f"[PASS 2] Processing chunk {chunk_num}...")
        txns = chunk.copy()
        
        txns['customer_id'] = txns['customer_id'].fillna("UNKNOWN_CUSTOMER").astype(str)
        txns['card1'] = txns['card1'].fillna(-1).astype(int)
        txns['card2'] = txns['card2'].fillna(-1.0)
        txns['card3'] = txns['card3'].fillna(-1.0)
        txns['card4'] = txns['card4'].fillna("UNKNOWN")
        txns['card5'] = txns['card5'].fillna(-1.0)
        txns['card6'] = txns['card6'].fillna("UNKNOWN")
        
        txns = txns.join(mapping_df, on=['customer_id', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6'])
        txns['card_id'] = txns['card_id'].fillna("UNKNOWN_CARD")
        txns['TransactionID'] = txns['TransactionID'].apply(lambda x: str(x) if pd.notnull(x) else "UNKNOWN_TXN")
        
        num_cols = ['TransactionDT', 'TransactionAmt', 'addr1', 'addr2', 'dist1', 'dist2', 'risk_score'] + c_cols + d_cols
        str_cols = ['ProductCD', 'P_emaildomain', 'R_emaildomain', 'channel'] + m_cols
        
        for col in num_cols:
            txns[col] = txns[col].fillna(-1.0)
        for col in str_cols:
            txns[col] = txns[col].fillna("")
            
        txns['ts'] = pd.to_datetime(txns['ts'], errors='coerce')
        txns['ts'] = txns['ts'].dt.strftime('%Y-%m-%d %H:%M:%S').fillna('1970-01-01 00:00:00')
        
        # TRANSACTION VERTEX
        attr_cols = [c for c in all_txn_cols if c not in ['TransactionID', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6']]
        chunked_upsert_vertex(
            conn, txns, 'Transaction', 'TransactionID',
            {k:k for k in attr_cols}, batch_size=2000
        )
        
        # MADE EDGE (Card -> Transaction)
        chunked_upsert_edge(
            conn, txns, 'Card', 'MADE', 'Transaction',
            'card_id', 'TransactionID', {}, batch_size=5000
        )
        
        # EMAIL DOMAINS
        emails_p = txns[['P_emaildomain']].rename(columns={'P_emaildomain': 'domain'})
        emails_r = txns[['R_emaildomain']].rename(columns={'R_emaildomain': 'domain'})
        emails = pd.concat([emails_p, emails_r])
        emails = emails[emails['domain'] != ""].drop_duplicates()
        if not emails.empty:
            emails['txn_count'] = 1
            chunked_upsert_vertex(conn, emails, 'EmailDomain', 'domain', {'txn_count': 'txn_count'})
            p_edges = txns[txns['P_emaildomain'] != ""]
            chunked_upsert_edge(conn, p_edges, 'Transaction', 'PURCHASER_EMAIL', 'EmailDomain', 'TransactionID', 'P_emaildomain', {})
            r_edges = txns[txns['R_emaildomain'] != ""]
            chunked_upsert_edge(conn, r_edges, 'Transaction', 'RECIPIENT_EMAIL', 'EmailDomain', 'TransactionID', 'R_emaildomain', {})

        # BILLING REGION
        regions = txns[txns['addr1'] != -1.0][['addr1']].drop_duplicates()
        if not regions.empty:
            regions['region_code'] = regions['addr1'].astype(str)
            regions['txn_count'] = 1
            chunked_upsert_vertex(conn, regions, 'BillingRegion', 'region_code', {'txn_count': 'txn_count'})
            b_edges = txns[txns['addr1'] != -1.0].copy()
            b_edges['region_code'] = b_edges['addr1'].astype(str)
            chunked_upsert_edge(conn, b_edges, 'Transaction', 'BILLED_IN', 'BillingRegion', 'TransactionID', 'region_code', {})

        # FROM_DEVICE
        if not identity_df.empty:
            dev_edges = pd.merge(txns[['TransactionID']], identity_df, on='TransactionID', how='inner')
            if not dev_edges.empty:
                chunked_upsert_edge(conn, dev_edges, 'Transaction', 'FROM_DEVICE', 'DeviceProfile', 'TransactionID', 'device_profile_id', {})
                
        # NEXT EDGES
        txns_sorted = txns.sort_values(['card_id', 'TransactionDT'])
        next_edges = []
        for card_id, group in txns_sorted.groupby('card_id'):
            prev_id = last_txn_per_card.get(card_id, {}).get('txn_id')
            prev_time = last_txn_per_card.get(card_id, {}).get('time')
            for _, row in group.iterrows():
                curr_id = row['TransactionID']
                curr_time = row['TransactionDT']
                if prev_id is not None:
                    next_edges.append({
                        'from_txn': prev_id,
                        'to_txn': curr_id,
                        'time_gap_seconds': curr_time - prev_time
                    })
                prev_id = curr_id
                prev_time = curr_time
            last_txn_per_card[card_id] = {'txn_id': prev_id, 'time': prev_time}
            
        if next_edges:
            next_df = pd.DataFrame(next_edges)
            chunked_upsert_edge(conn, next_df, 'Transaction', 'NEXT', 'Transaction', 'from_txn', 'to_txn', {'time_gap_seconds': 'time_gap_seconds'})

        checkpoint["transaction_chunks_completed"].append(chunk_num)
        save_checkpoint(checkpoint)
        print(f"[CHECKPOINT] Chunk {chunk_num} completed")

    # ---------------------------------------------------------
    # CLOSED CASES & PATTERNS
    # ---------------------------------------------------------
    if not checkpoint["closed_cases_complete"]:
        cases_df = pd.DataFrame()
        involves_edges = []
        connected_edges = []
        
        if os.path.exists(closed_cases_path):
            print("Loading closed cases...")
            cases_df = pd.read_csv(closed_cases_path)
            cases_df['opened_at'] = pd.to_datetime(cases_df['opened_at']).dt.strftime('%Y-%m-%d %H:%M:%S').fillna('1970-01-01 00:00:00')
            cases_df['closed_at'] = pd.to_datetime(cases_df['closed_at']).dt.strftime('%Y-%m-%d %H:%M:%S').fillna('1970-01-01 00:00:00')
            cases_df['exposure_usd'] = cases_df['exposure_usd'].fillna(0.0)
            cases_df['n_txns'] = cases_df['n_txns'].fillna(0).astype(int)
            
            for col in ['outcome', 'pattern', 'first_fraud_txn_id', 'actions_taken', 'report_filed', 'analyst_notes']:
                cases_df[col] = cases_df[col].fillna("").astype(str)
            
            cases_df['first_fraud_txn_id'] = cases_df['first_fraud_txn_id'].str.replace(r'\.0$', '', regex=True)
                
            chunked_upsert_vertex(conn, cases_df, 'ClosedCase', 'case_id', {'customer_id':'customer_id', 'card_id':'card_id', 'opened_at':'opened_at', 'closed_at':'closed_at', 'outcome':'outcome', 'pattern':'pattern', 'first_fraud_txn_id':'first_fraud_txn_id', 'n_txns':'n_txns', 'exposure_usd':'exposure_usd', 'actions_taken':'actions_taken', 'report_filed':'report_filed', 'analyst_notes':'analyst_notes'})
            
            for _, row in cases_df.iterrows():
                case_id = row['case_id']
                txn_ids = str(row['txn_ids']).split('|')
                for tid in txn_ids:
                    if tid and tid != 'nan':
                        involves_edges.append({'case_id': case_id, 'txn_id': tid})
            if involves_edges:
                chunked_upsert_edge(conn, pd.DataFrame(involves_edges), 'ClosedCase', 'INVOLVES', 'Transaction', 'case_id', 'txn_id', {})
                
            chunked_upsert_edge(conn, cases_df, 'ClosedCase', 'ON_CARD', 'Card', 'case_id', 'card_id', {})
            
            for _, row in cases_df.iterrows():
                case_id = row['case_id']
                c_cards = str(row['connected_card_ids']).split('|')
                for cc in c_cards:
                    if cc and cc != 'nan':
                        connected_edges.append({'case_id': case_id, 'card_id': cc})
            if connected_edges:
                chunked_upsert_edge(conn, pd.DataFrame(connected_edges), 'ClosedCase', 'CONNECTED_TO', 'Card', 'case_id', 'card_id', {})
        
        checkpoint["closed_cases_complete"] = True
        save_checkpoint(checkpoint)
        print("[CHECKPOINT] Closed cases complete")
    else:
        print("[SKIP] Closed cases already uploaded")

    if not checkpoint["fraud_patterns_complete"]:
        print("Loading static fraud patterns...")
        patterns = [
            {"pattern_id": "card_testing", "name": "Card Testing", "description": "Small initial transactions to test validity."},
            {"pattern_id": "card_not_present_fraud", "name": "CNP Fraud", "description": "Card not present unauthorized use."},
            {"pattern_id": "card_not_present_new_device", "name": "CNP New Device", "description": "CNP fraud from an unrecognized device."},
            {"pattern_id": "out_of_region_use", "name": "Out of Region", "description": "Transactions far from typical billing regions."},
            {"pattern_id": "account_takeover", "name": "Account Takeover", "description": "Compromised account via device/channel change."},
            {"pattern_id": "undocumented", "name": "Undocumented", "description": "Other undocumented fraud pattern."},
            {"pattern_id": "none", "name": "None", "description": "No fraud pattern."}
        ]
        chunked_upsert_vertex(conn, pd.DataFrame(patterns), 'FraudPattern', 'pattern_id', {'name':'name', 'description':'description'})
        checkpoint["fraud_patterns_complete"] = True
        save_checkpoint(checkpoint)
        print("[CHECKPOINT] Fraud patterns complete")
    else:
        print("[SKIP] Fraud patterns already uploaded")

    print("Data loading complete!")

if __name__ == "__main__":
    main()
