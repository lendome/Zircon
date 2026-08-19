# `tools/` — Tool Implementations

Tools are the atomic actions the LLM can call. Each tool inherits from `BaseTool` in `registry.py`.

| Tool | Module | Purpose |
|------|--------|---------|
| `ReadFileTool` | `file_ops.py` | Read file contents |
| `CreateFileTool` | `file_ops.py` | Create new files |
| `DeleteFileTool` | `file_ops.py` | Delete files |
| `GlobFilesTool` | `file_ops.py` | Find files by pattern |
| `ListDirTool` | `file_ops.py` | List directory contents |
| `EditFileTool` | `edit_ops.py` | SEARCH/REPLACE targeted edits |
| `EditLinesTool` | `edit_ops.py` | Line-range replacement |
| `AiderEditTool` | `edit_ops.py` | Aider-format multi-file edit blocks |
| `GrepCodeTool` | `search_ops.py` | Regex search across files |
| `FindSymbolsTool` | `search_ops.py` | Find symbol definitions |
| `GetStructureTool` | `search_ops.py` | Get file symbol structure |
| `GetSymbolDefinitionTool` | `nav_ops.py` | Locate a symbol's definition (file, exact lines, kind) |
| `GetFunctionBodyTool` | `nav_ops.py` | Read a function/method body by name, with line numbers |
| `FindReferencesTool` | `nav_ops.py` | Find all usages of a symbol (word-boundary, grouped by file) |
| `RunCommandTool` | `shell_ops.py` | Run short-lived shell commands |
| `ShellStartTool` | `shell_ops.py` | Start long-running processes |
| `ShellPollTool` | `shell_ops.py` | Read output from background jobs |
| `ShellStopTool` | `shell_ops.py` | Stop background jobs |
| `RunTaskTool` | `dev_ops.py` | Structured command capture (separate stdout/stderr, save-to-file) |
| `VerifyDeterminismTool` | `dev_ops.py` | Run N times, report output stability with first-diff context |
| `RunProfilerTool` | `dev_ops.py` | Native profiler wrapper (cProfile / node --cpu-prof / go pprof), top-N hotspots |
| `FetchUrlTool` | `web_ops.py` | HTTP GET requests |