"""Shared mastering planner for the four production workflows (ADR-0052).

The planner decides (cuts, framing, roles, audio policy, export settings) and
saves its decisions as a versioned ``mastering_plan``; each workflow's existing
renderer executes them. Level ``off`` (the default) keeps every workflow as it was.
"""
