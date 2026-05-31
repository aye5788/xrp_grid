"""magi.agents — native ADK agent definitions for the MAGI council.

Holds the locked cross-boundary vote schemas (schemas.py) and the per-agent
persona texts (personas/). council.py builds the three ADK LlmAgents from these
and translates their structured output back into the parsed-vote dict shapes
orchestrator.py consumes.
"""
