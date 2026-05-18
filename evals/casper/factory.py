"""Suite entry point for Casper persona regression. Re-exports the shared
factory + extractor so letta-evals can import them from a single module."""
from common.extractors import r0_position  # noqa: F401  (registers extractor)
from common.factory_base import create_casper as create_eval_agent  # noqa: F401
