import os
from dotenv import load_dotenv
import pyTigerGraph as tg

def main():
    load_dotenv()

    host = os.getenv("TG_HOST")
    secret = os.getenv("TG_SECRET")
    graph_name = os.getenv("TG_GRAPH_NAME", "FraudGraph")

    print(f"Connecting to TigerGraph Cloud at {host}...")
    print(f"Using graph: {graph_name}")

    try:
        conn = tg.TigerGraphConnection(
            host=host,
            graphname=graph_name,
            gsqlSecret=secret
        )

        conn.apiToken = conn.getToken(secret)[0]

        print("Connected successfully!")
        print("Pushing GSQL Schema (this may take a minute)...")

        with open("graph/schema.gsql", "r") as f:
            schema_content = f.read()

        result = conn.gsql(schema_content)

        print("Schema Execution Result:")
        print(result)

    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    main()