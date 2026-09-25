import pandas as pd

# 1. Read transactions and generate card IDs
txns = pd.read_csv('C:/Users/Khushi/Desktop/hhg/TASK 4/TASK 4/HHGOA_IEEE/transactions.csv', usecols=['customer_id', 'ts', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6'])
txns = txns[txns['customer_id'].notna()].copy()
txns['customer_id'] = txns['customer_id'].astype(str)
txns['ts'] = pd.to_datetime(txns['ts'], errors='coerce')
txns['card1'] = txns['card1'].fillna(-1).astype(int)
txns['card2'] = txns['card2'].fillna(-1.0)
txns['card3'] = txns['card3'].fillna(-1.0)
txns['card4'] = txns['card4'].fillna("UNKNOWN")
txns['card5'] = txns['card5'].fillna(-1.0)
txns['card6'] = txns['card6'].fillna("UNKNOWN")

agg = txns.groupby(['customer_id', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6']).agg(
    first_seen=('ts', 'min')
).reset_index()

agg = agg.sort_values(['customer_id', 'first_seen', 'card1', 'card2', 'card3', 'card4', 'card5', 'card6'])
agg['card_idx'] = agg.groupby('customer_id').cumcount() + 1
agg['generated_card_id'] = agg['customer_id'] + '-K' + agg['card_idx'].astype(str)

# 2. Read closed cases and extract referenced card IDs
cases = pd.read_csv('C:/Users/Khushi/Desktop/hhg/TASK 4/TASK 4/HHGOA_IEEE/closed_cases_history.csv')
case_cards = set(cases['card_id'].dropna().astype(str).unique())

for c_cards in cases['connected_card_ids'].dropna():
    for cc in str(c_cards).split('|'):
        if cc:
            case_cards.add(cc)

generated_cards = set(agg['generated_card_id'].unique())

print(f"Total generated cards: {len(generated_cards)}")
print(f"Total referenced cards in cases: {len(case_cards)}")
matching = generated_cards.intersection(case_cards)
print(f"Matching referenced cards: {len(matching)}")

orphans = case_cards - generated_cards
print(f"Orphaned case card IDs: {len(orphans)}")

if orphans:
    print("Sample orphans:", list(orphans)[:5])
