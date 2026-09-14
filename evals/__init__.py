"""Prompt Evals Harness (PRD FR-27): runs each Action Template version against its Golden Set.

Lives outside the shipping package but shares its template loader and Provider protocol, so a
template that passes here is the same file the app loads. Not a CI gate.
"""
