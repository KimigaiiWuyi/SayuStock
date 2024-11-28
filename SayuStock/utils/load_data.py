import json
from pathlib import Path

output_data = Path(__file__).parent / 'output.json'

with output_data.open('r', encoding='utf-8') as f:
    mdata = json.load(f)
