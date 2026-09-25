import logging
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.tools.tigergraph_tools import DataLayer
from agent.orchestrator import FraudInvestigationAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="HHGOA Fraud Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize data layer and agent once globally
logger.info("Initializing DataLayer and Agent...")
dl = DataLayer()
agent = FraudInvestigationAgent(dl)
logger.info("Initialization complete.")

import os
import pyTigerGraph as tg
from dotenv import load_dotenv

load_dotenv()

# Initialize TigerGraph Connection for UI Endpoints
try:
    tg_conn = tg.TigerGraphConnection(
        host=os.getenv('TG_HOST'),
        graphname=os.getenv('TG_GRAPH_NAME', 'FraudGraph'),
        gsqlSecret=os.getenv('TG_SECRET')
    )
    tg_conn.apiToken = tg_conn.getToken(os.getenv('TG_SECRET'))[0]
    logger.info("Successfully connected to TigerGraph Cloud.")
except Exception as e:
    logger.error(f"Failed to connect to TigerGraph: {e}")
    tg_conn = None

class InvestigateRequest(BaseModel):
    transaction_id: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/transaction/{txn_id}")
def get_transaction(txn_id: str):
    if tg_conn:
        try:
            # HACKATHON REQUIREMENT: GSQL Interpreted Query
            query = f'''
            INTERPRET QUERY () FOR GRAPH {os.getenv('TG_GRAPH_NAME', 'FraudGraph')} {{
                Seed = {{Transaction.*}};
                res = SELECT s FROM Seed:s WHERE s.txn_id == "{txn_id}";
                PRINT res;
            }}
            '''
            import json
            raw_res = tg_conn.gsql(query)
            # Find the JSON part in the response
            start_idx = raw_res.find('[')
            if start_idx != -1:
                json_data = json.loads(raw_res[start_idx:])
                if json_data and len(json_data) > 0 and 'res' in json_data[0]:
                    if json_data[0]['res']:
                        return json_data[0]['res'][0].get('attributes', {})
            
            # Fallback if gsql fails to parse
            res = tg_conn.getVerticesById("Transaction", txn_id)
            if res:
                return res[0].get("attributes", {})
        except Exception as e:
            logger.error(f"TG get_transaction error: {e}")
    
    # Fallback to local DataLayer if TG fails
    result = dl.get_transaction(txn_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result

@app.get("/api/graph/{txn_id}")
def get_graph(txn_id: str):
    nodes = []
    edges = []
    
    def add_node(vid, vtype):
        if not any(n['v_id'] == vid for n in nodes):
            nodes.append({"v_id": vid, "v_type": vtype})
            
    def add_edge(from_id, to_id, etype):
        edges.append({"from_id": from_id, "to_id": to_id, "e_type": etype})

    if tg_conn:
        try:
            # Add center transaction
            add_node(txn_id, "Transaction")
            
            # Edges from/to Transaction
            # MADE_BY: Transaction -> Card (reverse of MADE: Card -> Transaction)
            made_by_edges = tg_conn.getEdges("Transaction", txn_id, "MADE_BY")
            for e in made_by_edges:
                card_id = e.get("to_id")
                add_node(card_id, "Card")
                add_edge(card_id, txn_id, "MADE")
                
                # Get Customer (OWNED_BY: Card -> Customer, reverse of OWNS)
                owned_by_edges = tg_conn.getEdges("Card", card_id, "OWNED_BY")
                for oe in owned_by_edges:
                    c_id = oe.get("to_id")
                    add_node(c_id, "Customer")
                    add_edge(c_id, card_id, "OWNS")
                    
            # FROM_DEVICE: Transaction -> DeviceProfile
            dev_edges = tg_conn.getEdges("Transaction", txn_id, "FROM_DEVICE")
            for e in dev_edges:
                d_id = e.get("to_id")
                add_node(d_id, "DeviceProfile")
                add_edge(txn_id, d_id, "FROM_DEVICE")
                    
            # BILLED_IN: Transaction -> BillingRegion
            bill_edges = tg_conn.getEdges("Transaction", txn_id, "BILLED_IN")
            for e in bill_edges:
                r_id = e.get("to_id")
                add_node(r_id, "BillingRegion")
                add_edge(txn_id, r_id, "BILLED_IN")

            return {"raw": {"nodes": nodes, "edges": edges}}
        except Exception as e:
            logger.error(f"TG get_graph error: {e}")
            
    # Fallback to local DataLayer
    result = dl.get_neighborhood(txn_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
        
    txn = result.get("transaction", {})
    t_id = str(txn.get("TransactionID", txn_id))
    add_node(t_id, "Transaction")
    
    c_id = str(txn.get("customer_id", ""))
    if c_id and c_id != "nan":
        add_node(c_id, "Customer")
        add_edge(c_id, t_id, "MADE")
    
    profile = result.get("customer_profile", {})
    if profile.get("cards"):
        for card in profile["cards"]:
            card_id = str(card.get("card_id"))
            add_node(card_id, "Card")
            add_edge(c_id, card_id, "OWNS")
    
    device = result.get("device_info", {})
    if device.get("has_identity"):
        d_id = str(device.get("DeviceInfo", "UnknownDevice"))
        add_node(d_id, "DeviceProfile")
        add_edge(t_id, d_id, "FROM_DEVICE")
        
        for n in result.get("device_neighbors", []):
            nt_id = str(n.get("TransactionID"))
            nc_id = str(n.get("customer_id"))
            if nt_id:
                add_node(nt_id, "Transaction")
                add_edge(nt_id, d_id, "FROM_DEVICE")
            if nc_id and nt_id:
                add_node(nc_id, "Customer")
                add_edge(nc_id, nt_id, "MADE")
            
    region = result.get("billing_region", {}).get("region")
    if region and region != "nan":
        add_node(str(region), "BillingRegion")
        add_edge(t_id, str(region), "BILLED_IN")
    
    return {"raw": {"nodes": nodes, "edges": edges}}

@app.post("/api/investigate")
def investigate(req: InvestigateRequest):
    txn_id = req.transaction_id
    
    # Check if transaction exists
    txn = dl.get_transaction(txn_id)
    if "error" in txn:
        raise HTTPException(status_code=404, detail=txn["error"])
        
    customer_id = str(txn.get("customer_id", "UNKNOWN"))
    
    # Create synthetic case row
    case_row = {
        "case_id": f"WEB-{txn_id}",
        "flagged_txn_id": txn_id,
        "customer_id": customer_id,
        "card_id": f"{customer_id}-K1", # Derive a card ID for tools
        "trigger_type": "analyst_request",
        "trigger_text": f"Analyst requested investigation via web UI for transaction {txn_id}"
    }
    
    # Run the investigation!
    try:
        case_state = agent.investigate_case(case_row)
        
        # Extract meaningful data from CaseState
        # Need to format for UI serialization
        return {
            "case_id": case_state.case_id,
            "verdict": case_state.verdict.value if case_state.verdict else "UNKNOWN",
            "status": case_state.status.value if case_state.status else "UNKNOWN",
            "fraud_probability": case_state.fraud_probability,
            "sufficient_evidence": getattr(case_state, "sufficient_evidence", True),
            "explanation": case_state.summary,
            "detected_patterns": [case_state.pattern.value],
            "evidence": [{"source": e.source, "claim": e.claim} for e in case_state.evidence],
            "decision_log": [{"node_name": d.state, "rationale": d.reasoning} for d in case_state.decisions_log]
        }
    except Exception as e:
        logger.exception("Investigation failed")
        raise HTTPException(status_code=500, detail=str(e))

import os
os.makedirs("public", exist_ok=True)
app.mount("/", StaticFiles(directory="public", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
