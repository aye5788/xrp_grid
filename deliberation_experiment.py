import anthropic
import openai
from google import generativeai as genai
import json
import os
from datetime import datetime
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv('/root/xrp_grid/.env')

# Initialize API clients
claude_client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
openai_client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))  # FIXED: was GEMINI_API_KEY

class DeliberationSimulator:
    def __init__(self):
        self.results = []
        
    def run_control(self, case):
        """Control: original MAGI decision (no deliberation)"""
        return {
            'method': 'control',
            'decision': case['original_decision'],
            'rounds': 1,
            'reasoning': 'Original orchestrator decision: MAINTAIN'
        }
    
    def run_deliberation(self, case):
        """Experimental: structured 3-round deliberation"""
        print(f"\n{'='*60}")
        print(f"Case {case['id']} - {case['timestamp']}")
        print(f"Original: Melchior={case['melchior_action']}, Casper={case['casper_action']}")
        print(f"{'='*60}")
        
        # Round 1: Casper challenges Melchior's RECENTRE position
        print("\nRound 1: Casper challenges...")
        challenge = self._casper_challenge(case)
        if challenge['text'].startswith('ERROR:'):
            print(f"  SKIPPING: Casper returned agent error")
            return {'method': 'deliberation', 'skipped': True, 'reason': 'agent_error', 'agent': 'Casper', 'error': challenge['text']}
        time.sleep(2)  # Rate limiting

        # Round 2: Melchior defends
        print("Round 2: Melchior defends...")
        defense = self._melchior_defend(case, challenge)
        if defense['text'].startswith('ERROR:'):
            print(f"  SKIPPING: Melchior returned agent error")
            return {'method': 'deliberation', 'skipped': True, 'reason': 'agent_error', 'agent': 'Melchior', 'error': defense['text']}
        time.sleep(2)

        # Round 3: Balthasar adjudicates
        print("Round 3: Balthasar adjudicates...")
        final = self._balthasar_adjudicate(case, challenge, defense)
        if final['text'].startswith('ERROR:'):
            print(f"  SKIPPING: Balthasar returned agent error")
            return {'method': 'deliberation', 'skipped': True, 'reason': 'agent_error', 'agent': 'Balthasar', 'error': final['text']}
        time.sleep(2)
        
        return {
            'method': 'deliberation',
            'decision': final['decision'],
            'rounds': 3,
            'reasoning': {
                'challenge': challenge,
                'defense': defense,
                'adjudication': final
            }
        }
    
    def _casper_challenge(self, case):
        """Casper challenges Melchior's RECENTRE decision"""
        prompt = f"""You are Casper, the regime detection specialist in the MAGI trading council.

CONTEXT:
Melchior proposes: {case['melchior_action']}
Melchior's reasoning: {case['melchior_reasoning']}

Your assessment: {case['casper_action']} (TRENDING regime detected)
Your reasoning: {case['casper_reasoning']}

TASK:
Challenge Melchior's decision to RECENTRE the grid. What risks does recentring the grid pose when you detect a TRENDING regime?

Respond in this format:
CHALLENGE: [Your main objection in 2-3 sentences]
RISK: [Specific risk of recentring during a trend]
ALTERNATIVE: [What should happen instead]"""

        try:
            model = genai.GenerativeModel('gemini-2.5-flash')
            response = model.generate_content(prompt)
            
            return {
                'agent': 'Casper',
                'text': response.text,
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            print(f"  ERROR in Casper: {e}")
            return {
                'agent': 'Casper',
                'text': f"ERROR: {str(e)}",
                'timestamp': datetime.now().isoformat()
            }
    
    def _melchior_defend(self, case, challenge):
        """Melchior defends RECENTRE decision against Casper's challenge"""
        prompt = f"""You are Melchior, the grid architecture specialist in the MAGI trading council.

YOUR POSITION:
Decision: {case['melchior_action']}
Your reasoning: {case['melchior_reasoning']}
Your concerns: {case['melchior_concerns'] if case['melchior_concerns'] else 'None stated'}

CASPER'S CHALLENGE:
{challenge['text']}

TASK:
Respond to Casper's objection. Either:
1. Defend your RECENTRE decision with stronger evidence, OR
2. Revise your position if Casper's trend evidence is compelling

Respond in this format:
RESPONSE: [DEFEND or REVISE]
REASONING: [Why you maintain or change your position - 2-3 sentences]
FINAL_VOTE: [MAINTAIN or RECENTRE or TRENDING]"""

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            
            return {
                'agent': 'Melchior',
                'text': response.choices[0].message.content,
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            print(f"  ERROR in Melchior: {e}")
            return {
                'agent': 'Melchior',
                'text': f"ERROR: {str(e)}",
                'timestamp': datetime.now().isoformat()
            }
    
    def _balthasar_adjudicate(self, case, challenge, defense):
        """Balthasar makes final decision after hearing both arguments"""
        prompt = f"""You are Balthasar, the risk validator in the MAGI trading council.

CASPER'S CHALLENGE (TRENDING regime):
{challenge['text']}

MELCHIOR'S DEFENSE (RECENTRE grid):
{defense['text']}

BALTHASAR'S ORIGINAL ASSESSMENT:
Action: {case['balthasar_action']}
Reasoning: {case['balthasar_reasoning']}

TASK:
Make the final decision. Which position is more sound given the risk parameters and market state?

Respond in this format:
DECISION: [MAINTAIN or RECENTRE or TRENDING]
RATIONALE: [Which argument was more compelling and why - 2-3 sentences]
RISK_ASSESSMENT: [Any risk concerns with this decision]"""

        try:
            response = claude_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            text = response.content[0].text
            
            # Extract decision from response
            decision = 'MAINTAIN'  # default
            if 'DECISION:' in text:
                decision_line = [l for l in text.split('\n') if l.strip().startswith('DECISION:')]
                if decision_line:
                    decision_text = decision_line[0].split(':', 1)[1].strip()
                    if 'RECENTRE' in decision_text.upper():
                        decision = 'RECENTRE'
                    elif 'TRENDING' in decision_text.upper():
                        decision = 'TRENDING'
                    elif 'WIDEN' in decision_text.upper():
                        decision = 'WIDEN'
            
            return {
                'agent': 'Balthasar',
                'text': text,
                'decision': decision,
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            print(f"  ERROR in Balthasar: {e}")
            return {
                'agent': 'Balthasar',
                'text': f"ERROR: {str(e)}",
                'decision': 'MAINTAIN',
                'timestamp': datetime.now().isoformat()
            }


def main():
    print("="*60)
    print("MAGI DELIBERATION EXPERIMENT")
    print("="*60)
    print(f"Start time: {datetime.now()}")
    
    # Load test cases
    with open('/root/xrp_grid/test_cases.json', 'r') as f:
        test_cases = json.load(f)
    
    print(f"\nLoaded {len(test_cases)} test cases")
    print("This will take approximately {:.0f} minutes".format(len(test_cases) * 2))
    
    simulator = DeliberationSimulator()
    results = []
    
    for i, case in enumerate(test_cases, 1):
        print(f"\n\n{'#'*60}")
        print(f"TEST CASE {i}/{len(test_cases)}")
        print(f"{'#'*60}")
        
        try:
            # Run control (original decision)
            control = simulator.run_control(case)
            
            # Run deliberation
            deliberation = simulator.run_deliberation(case)

            # Skip if any agent returned an error
            if deliberation.get('skipped'):
                print(f"\nSKIPPED: {deliberation.get('agent', 'unknown')} returned agent error")
                results.append({
                    'case_id': case['id'],
                    'timestamp': case['timestamp'],
                    'skipped': True,
                    'reason': 'agent_error',
                    'error': deliberation.get('error')
                })
                with open('/root/xrp_grid/deliberation_results_partial.json', 'w') as f:
                    json.dump(results, f, indent=2)
                continue

            # Record results
            result = {
                'case_id': case['id'],
                'timestamp': case['timestamp'],
                'control': control,
                'deliberation': deliberation,
                'decisions_differ': control['decision'] != deliberation['decision']
            }
            
            results.append(result)
            
            # Print summary
            print(f"\nRESULT:")
            print(f"  Control: {control['decision']}")
            print(f"  Deliberation: {deliberation['decision']}")
            print(f"  Changed: {result['decisions_differ']}")
            
            # Save incremental progress
            with open('/root/xrp_grid/deliberation_results_partial.json', 'w') as f:
                json.dump(results, f, indent=2)
                
        except Exception as e:
            print(f"\nERROR processing case {case['id']}: {e}")
            results.append({
                'case_id': case['id'],
                'error': str(e)
            })
    
    # Final summary
    print("\n\n" + "="*60)
    print("EXPERIMENT COMPLETE")
    print("="*60)
    print(f"End time: {datetime.now()}")
    print(f"\nTotal cases: {len(results)}")
    changed = sum(1 for r in results if r.get('decisions_differ', False))
    print(f"Decisions changed by deliberation: {changed}/{len(results)}")
    
    # Save final results
    with open('/root/xrp_grid/deliberation_results_final.json', 'w') as f:
        json.dump({
            'summary': {
                'total_cases': len(results),
                'decisions_changed': changed,
                'completion_time': datetime.now().isoformat()
            },
            'results': results
        }, f, indent=2)
    
    print("\nResults saved to:")
    print("  - deliberation_results_final.json")
    print("  - deliberation_results_partial.json (incremental backup)")


if __name__ == "__main__":
    main()
