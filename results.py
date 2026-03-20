import csv
import os
import re

CSV_COLUMNS = [
    'nickname',
    'mode',
    'uuid',
    'paipuUrl',
    'resultUrl',
    'modelTag',
    'rating',
    'aiConsistencyRate',
    'aiConsistencyNumerator',
    'aiConsistencyDenominator',
    'temperature',
    'gameLength',
    'playerId',
    'reviewDuration',
    'screenshotPath',
    'timestamp'
]

def parse_metadata(metadata: dict) -> dict:
    """
    Parse the metadata dict returned by browser.py into typed fields.
    """
    def get(keys: list) -> str:
        for k, v in metadata.items():
            for key in keys:
                if key in k:
                    return v
        return ''

    ai_consistency = get(['一致率', 'Match Rate'])
    numerator, denominator, rate = '', '', ''
    
    # regex matches: "195/271 = 71.956%"
    m = re.search(r'(\d+)\s*/\s*(\d+)\s*=\s*([\d.]+)%', ai_consistency)
    if m:
        numerator = m.group(1)
        denominator = m.group(2)
        rate = m.group(3) + '%'
        
    return {
        'modelTag': get(['model tag']),
        'rating': get(['rating']),
        'aiConsistencyRate': rate,
        'aiConsistencyNumerator': numerator,
        'aiConsistencyDenominator': denominator,
        'temperature': get(['temperature', 'τ']),
        'gameLength': get(['对局长度', 'length']),
        'playerId': get(['玩家 ID', 'player']),
        'reviewDuration': get(['检审用时', 'Duration'])
    }

import openpyxl

def append_row(filepath: str, row: dict, output_format: str = 'csv'):
    """
    Append one row to a CSV or XLSX file. Creates the file with a header row if new.
    """
    is_new = not os.path.exists(filepath)
    
    if is_new:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
    safe_row_list = [row.get(col, '') for col in CSV_COLUMNS]
    
    if output_format == 'csv':
        with open(filepath, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(CSV_COLUMNS)
            writer.writerow(safe_row_list)
    elif output_format == 'xlsx':
        if is_new:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(CSV_COLUMNS)
        else:
            wb = openpyxl.load_workbook(filepath)
            ws = wb.active
        ws.append(safe_row_list)
        wb.save(filepath)
