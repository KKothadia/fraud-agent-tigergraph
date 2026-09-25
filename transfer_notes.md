# HHGoa Fraud Agent - Project Transfer Notes

These notes summarize everything we have built, configured, and attempted so far. Use this as your guide when setting up the project on the new PC.

## 1. What We Built & Accomplished
* **Architecture & Scaffolding**: We set up the core directories (`agent/`, `benchmark/`, `docs/`, `graph/`) and the initial architecture for the LangGraph-powered state machine paired with TigerGraph.
* **Graph Schema (`schema.gsql`)**: We designed the complete graph schema, which models `Customers`, `Cards`, `Transactions`, `FraudCases`, and the relationships between them.
* **Pandas Simulator Success**: We successfully ran the initial AI agent against a local Pandas CSV simulator (`benchmark/run_all.py`). It scored 73.4/100 with a perfect 20/20 on schema validation!
* **TigerGraph Scripts**: 
  * Created `graph/load_data.py`: A Python script that uses `pyTigerGraph` and Pandas chunks to ingest the massive 700MB `transactions.csv` into the graph database without crashing.
  * Created `setup_cloud_schema.py`: A quick utility to push the `schema.gsql` file directly to a TigerGraph Cloud instance.

## 2. Changes to Environment Variables (`.env`)
We updated the `.env` file with the following real configurations:
* **LLM**: Added the OpenRouter API Key (`sk-or-v1-...`) and set the model to `openrouter/free`.
* **TigerGraph Secret**: We generated a Database Secret on TigerGraph Cloud and stored it as `TG_SECRET=b9hve0ru8k453p4h9g5qktfc9dc5sr19`.

## 3. The Docker Issue & The Move to Cloud
We originally tried to spin up a local TigerGraph instance using Docker. However, the newest TigerGraph Community Edition container requires a license key to exit the "Warmup" phase. Because we didn't have one on hand, we elected to pivot to **TigerGraph Savanna (Cloud)** as recommended in the hackathon rules. 

We stopped and completely removed the local Docker containers to free up system resources.

## 4. Next Steps on the New PC
When you transfer the files to the new PC, here is exactly what you need to do to resume:

1. **Install Dependencies**: Open a terminal in this folder and run `pip install -r requirements.txt`.
2. **Find the Cloud Endpoint**: Log into your TigerGraph Savanna account, find your **Dedicated Workgroup**, and locate its connection URL (e.g., `https://your-workspace.i.tgcloud.io`).
3. **Update `.env`**: Open the `.env` file and set `TG_HOST` to that new URL.
4. **Push the Schema**: Run `python setup_cloud_schema.py` to create all the vertices and edges in the cloud database.
5. **Load the Data**: Run `python graph/load_data.py` to upload the IEEE-CIS CSVs into your graph.
6. **Final Refactor**: Once the data is in the graph, we will refactor the `DataLayer` class in `agent/tools/tigergraph_tools.py` so the AI queries the real graph instead of local CSVs!
