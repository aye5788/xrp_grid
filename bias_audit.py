import anthropic
import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv('/root/xrp_grid/.env')
client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

with open('/root/xrp_grid/deliberation_results_final.json', 'r') as f:
    data = json.load(f)

audit_results = []

for r in data['results']:
    if r.get('skipped'):
        continue

    case_id = r['case_id']
    challenge = r['deliberation']['reasoning']['challenge']['text']
    defense = r['deliberation']['reasoning']['defense']['text']
    adjudication = r['deliberation']['reasoning']['adjudication']['text']
    final_decision = r['deliberation']['decision']

    prompt = f"""You are an impartial auditor analyzing a multi-model deliberation for signs of bias, \
dismissiveness, or problematic interaction patterns.

Here is the full deliberation transcript for Case {case_id}:

ROUND 1 - CASPER'S CHALLENGE:
{challenge}

ROUND 2 - MELCHIOR'S DEFENSE:
{defense}

ROUND 3 - BALTHASAR'S ADJUDICATION:
{adjudication}

Audit this deliberation for the following specific patterns:

1. DISMISSIVENESS: Did any agent ignore or fail to engage with specific points raised?
2. CAPITULATION: Did Melchior revise its position? If so, was the revision earned by evidence or just social pressure?
3. AUTHORITY DEFERENCE: Did Balthasar show implicit preference for one agent over another based on framing rather than logic?
4. SYCOPHANTIC LANGUAGE: Are there phrases like "well-reasoned", "compelling", "notably" that signal social approval rather than logical evaluation?
5. LAST-SPEAKER BIAS: Did Balthasar disproportionately favor whichever agent spoke most recently?
6. COMPETITIVE FRAMING: Did any agent frame the deliberation as "winning" rather than truth-seeking?

For each pattern, cite specific language from the transcript as evidence.
Then give an overall bias score: LOW / MEDIUM / HIGH
And state whether the final decision appears to be driven by logic or by interaction dynamics."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception as e:
        print(f"API ERROR on case {case_id}: {e}")
        raise

    audit_text = response.content[0].text

    print(f"\n{'='*60}")
    print(f"BIAS AUDIT — CASE {case_id}")
    print(f"{'='*60}")
    print(audit_text)

    audit_results.append({
        'case_id': case_id,
        'timestamp': r['timestamp'],
        'final_decision': final_decision,
        'original_decision': r['control']['decision'],
        'decision_changed': r['decisions_differ'],
        'audit': audit_text,
        'audited_at': datetime.now().isoformat()
    })

output = {
    'source_file': 'deliberation_results_final.json',
    'experiment': 'RECENTRE vs TRENDING',
    'cases_audited': len(audit_results),
    'run_at': datetime.now().isoformat(),
    'results': audit_results
}

with open('/root/xrp_grid/bias_audit_results.json', 'w') as f:
    json.dump(output, f, indent=2)

print(f"\n{'='*60}")
print(f"Audit complete. {len(audit_results)} cases audited.")
print(f"Results saved to bias_audit_results.json")
