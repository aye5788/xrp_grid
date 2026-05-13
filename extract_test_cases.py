import sqlite3
import json

conn = sqlite3.connect('/root/xrp_grid/observer.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT 
        id,
        timestamp,
        melchior_action,
        melchior_reasoning,
        melchior_concerns,
        casper_action,
        casper_reasoning,
        balthasar_action,
        balthasar_reasoning,
        consensus_grid_action,
        consensus_regime,
        notes
    FROM magi_decisions
    WHERE melchior_action = 'RECENTRE'
      AND casper_action = 'TRENDING'
      AND consensus_grid_action = 'MAINTAIN'
    ORDER BY timestamp DESC
""")

cases = []
for row in cursor.fetchall():
    cases.append({
        'id': row[0],
        'timestamp': row[1],
        'melchior_action': row[2],
        'melchior_reasoning': row[3],
        'melchior_concerns': row[4],
        'casper_action': row[5],
        'casper_reasoning': row[6],
        'balthasar_action': row[7],
        'balthasar_reasoning': row[8],
        'original_decision': row[9],
        'regime': row[10],
        'notes': row[11]
    })

conn.close()

with open('test_cases.json', 'w') as f:
    json.dump(cases, f, indent=2)

print(f"Extracted {len(cases)} test cases")
