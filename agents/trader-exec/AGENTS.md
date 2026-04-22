# AGENTS.md — TraderExec Workspace

## Workspace
This is the Executor workspace for the `llm-day-trader` multi-agent pipeline.

Project root: `/home/wheat/projects/llm-day-trader/`

## Session Startup
1. Read `SOUL.md`
2. Read `strategy/SKILL.md` (execution checklist + guard rails)

## Role
You are spawned by `trader-orch` with a Strategy Spec in your task. When done, announce the Execution Result back to the orchestrator.
