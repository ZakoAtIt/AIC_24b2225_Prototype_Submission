"""CauseTrace deterministic pipeline.

Execution order (each stage registers evidence and reports telemetry):
    load_and_validate -> semantic_resolver -> detection_engine ->
    decomposition_engine -> causal_engine -> confidence_engine ->
    action_engine -> llm_narrative -> persona_router

The LLM appears ONLY at the narrative step and is never a source of numbers.
"""
