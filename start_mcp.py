import os
import uvicorn
import tigergraph_mcp
from dotenv import load_dotenv
load_dotenv()

print('Starting TigerGraph MCP server...')
os.system('python -m tigergraph_mcp')

