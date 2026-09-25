import os
import json
import sys

with open('graph/load_data.py', 'r') as f:
    content = f.read()

# Add imports
if 'import json' not in content:
    content = content.replace('import os', 'import os\nimport json\nimport sys')

# Let's replace the whole body from "def main():" up to "print("Pass 1: Aggregating customers and cards...")"
# Wait, it is easier to just read the file, parse it and inject the checkpoint blocks manually using python strings.

# Alternatively, I can just provide the fully updated load_data.py content since it is not too huge.
# The user asked me to run a test and report exactly what changed.
