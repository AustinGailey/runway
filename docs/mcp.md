# Using Runway from a Claude Code or Codex session


`python3 ~/.ai-planner/runway.py mcp` is a stdio MCP server, so an interactive session can pull work from Runway instead of only being launched by it. Register it once (`runway.py mcp-install` prints these lines and changes nothing):

```
claude mcp add --scope user runway -- python3 ~/.ai-planner/runway.py mcp
```

For Codex, add the block `mcp-install` prints to `~/.codex/config.toml` (`[mcp_servers.runway]` with the helper's absolute path).

Tools: `list_ready_tasks` (id, project, title, kind, estimated tokens, repo, and how old the board is), `claim_task` (creates the task's worktree and branch as a Runway agent run would, runs the project's `setup`, and returns the worktree path and the full brief), `task_status`, `finish_task` and `release_task`. Work in the returned worktree and commit there. `finish_task` commits nothing and refuses while files are uncommitted; it runs the project's `.runway.json` checks, and green (or no checks) marks the task done, so Runway's Merge button takes it through the normal merge gate. Red returns the failing step and log and leaves the task claimed, so fix, commit and call it again. `release_task` gives the task back and removes the worktree only if it has no commits. The server never merges, pushes or touches the integration branch.

The list comes from `~/.ai-planner/board.json`, which the Runway page rewrites (through `runway.py board-sync`) whenever its ready tasks change and on load. If the board is more than a day old, open the Runway page to refresh it. Claims are files in `~/.ai-planner/claims/`; the page applies them to its own database, so the card shows the session as it works. Server logs go to `~/.ai-planner/helper.log`.
