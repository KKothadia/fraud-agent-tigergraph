import re

with open('graph/load_data.py', 'r') as f:
    content = f.read()

chunk_funcs = """
    batch_size_env = int(os.getenv("TG_UPSERT_BATCH_SIZE", "5000"))
    def chunked_upsert_vertex(connection, dataframe, vertex_type, v_id_col, attr_dict, batch_size=batch_size_env):
        if dataframe.empty: return
        for start_idx in range(0, len(dataframe), batch_size):
            connection.upsertVertexDataFrame(df=dataframe.iloc[start_idx:start_idx+batch_size], vertexType=vertex_type, v_id=v_id_col, attributes=attr_dict)

    def chunked_upsert_edge(connection, dataframe, source_type, edge_type, target_type, from_col, to_col, attr_dict, batch_size=batch_size_env):
        if dataframe.empty: return
        for start_idx in range(0, len(dataframe), batch_size):
            connection.upsertEdgeDataFrame(df=dataframe.iloc[start_idx:start_idx+batch_size], sourceVertexType=source_type, edgeType=edge_type, targetVertexType=target_type, from_id=from_col, to_id=to_col, attributes=attr_dict)
"""

content = re.sub(r'chunk_size = 50000', 'chunk_size = 50000\n' + chunk_funcs, content, count=1)

# Remove the old definitions
content = re.sub(r'    batch_size_env = int\(os.getenv\("TG_UPSERT_BATCH_SIZE", "5000"\)\)\s*def chunked_upsert_vertex.*?attributes=attr_dict\)', '', content, flags=re.DOTALL)

# Let's just do text replacements for the conn calls
content = re.sub(r"conn\.upsertVertexDataFrame\(\s*df=(.*?),\s*vertexType=(.*?),\s*v_id=(.*?),\s*attributes=(.*?)\s*\)", r"chunked_upsert_vertex(conn, \1, \2, \3, \4)", content, flags=re.DOTALL)
content = re.sub(r"conn\.upsertEdgeDataFrame\(\s*df=(.*?),\s*sourceVertexType=(.*?),\s*edgeType=(.*?),\s*targetVertexType=(.*?),\s*from_id=(.*?),\s*to_id=(.*?),\s*attributes=(.*?)\s*\)", r"chunked_upsert_edge(conn, \1, \2, \3, \4, \5, \6, \7)", content, flags=re.DOTALL)

with open('graph/load_data.py', 'w') as f:
    f.write(content)
