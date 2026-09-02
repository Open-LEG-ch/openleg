# OpenLEG agent instructions

## GLM through OpenCode

Use `openrouter/z-ai/glm-5.3-flash` when the user asks for GLM or OpenCode work.

- Drive one behavior per call. Attach only the files needed for that slice.
- Prefer `opencode run --pure --auto --format json`. `--pure` removes plugins; `--auto` permits noninteractive built-in tools under the configured policy.
- Give GLM a narrow edit boundary, one test command, and explicit limits on commits, pushes, and GitHub writes.
- Monitor JSON events for step starts, tool calls, snapshots, and finishes. Check process elapsed time, CPU time, state, and the working-tree diff between quiet intervals.
- Treat quiet output as reasoning while CPU time or events advance. Call it stalled only after several checks show no new event, file change, or process activity.
- Let slow active calls finish. Interrupt only a confirmed stall or scope violation.
- Review every GLM edit locally. Run the repository's real test environment when the host Python lacks dependencies.
- Keep loops tight: one red test, one minimal green change, then the next slice.
