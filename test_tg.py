import os
import pyTigerGraph as tg
from dotenv import load_dotenv

load_dotenv()
conn = tg.TigerGraphConnection(
    host=os.getenv('TG_HOST'),
    graphname=os.getenv('TG_GRAPH_NAME', 'FraudGraph'),
    gsqlSecret=os.getenv('TG_SECRET')
)
try:
    conn.apiToken = conn.getToken(os.getenv('TG_SECRET'))[0]
    res = conn.getVerticesById('Transaction', '3000011')
    print('TIGERGRAPH_EXISTS:', len(res) > 0)
    print('VERTEX:', res)
except Exception as e:
    print('ERROR:', e)
