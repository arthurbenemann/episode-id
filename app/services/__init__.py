"""Service layer that orchestrates the core modules.

The HTTP API in `app/api` and the CLI in `app/cli.py` both call into here so
the scan/match/rename pipeline lives in exactly one place.
"""
