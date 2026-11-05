# CineVision as an AI-agent tool (MCP)

`backend/cinevision/mcp_server.py` is a [Model Context Protocol](https://modelcontextprotocol.io) server. Any MCP
client — Claude Desktop, Claude Code, other assistants — can then use CineVision as a tool: review a video like a
director, change a concept in plain language, or assemble a movie from many clips.

It uses only the Python standard library and talks over stdio, the same way colibri's `coli mcp` does.

## What it can do

| Tool | What it does |
|---|---|
| `review_video` | Uploads a video file from this computer and returns the Director's review (verdict, 8 departments with what to do and why, ways to edit, alternative concepts) in the chosen language |
| `get_review` | The review of an existing project, in any of the 16 languages |
| `list_projects` | Your projects and movie assemblies |
| `list_concepts` | The alternative cinematography concepts of a project, shot by shot |
| `refine_concept` | "Make the lighting more dramatic", "shot 2 tighter"… — creates a new revision |
| `assemble_movie` | Uploads many clips + an optional story; returns the story order with reasons, trims, transitions and the clips that don't fit |

## Privacy

* It signs in to **your** CineVision (default `http://127.0.0.1:8000`) with your email and password, so every
  privacy rule of the app applies.
* Video files are read only from the paths you give, and uploaded only to your CineVision.
* The assistant receives **text** (the review, the order, concept descriptions) — never frames or footage. If the
  assistant itself is a cloud AI, that text goes to its provider; footage does not.

## Set it up (Windows)

1. Start CineVision as usual (`scripts\run_windows.cmd`).
2. In your MCP client's configuration add (use your own paths and account):

```json
{
  "mcpServers": {
    "cinevision": {
      "command": "py",
      "args": ["-3", "C:\\Users\\you\\cinevision-studio\\backend\\cinevision\\mcp_server.py"],
      "env": {
        "CINEVISION_EMAIL": "you@example.com",
        "CINEVISION_PASSWORD": "your CineVision password"
      }
    }
  }
}
```

For Claude Desktop the file is `%APPDATA%\Claude\claude_desktop_config.json`; restart the app afterwards.
If two-factor sign-in is on for the account, also set `CINEVISION_TOTP_SECRET` (the secret shown when you enabled it).
If CineVision runs elsewhere, set `CINEVISION_URL`.

3. Ask the assistant, for example: *"Review C:\\Videos\\scene12.mp4 with CineVision in Telugu and tell me how to
   shoot it better"*, or *"Assemble the clips in C:\\Shoot\\day1 into a movie following this story: …"*.

## Test it without a client

```bash
cd backend
CINEVISION_EMAIL=you@example.com CINEVISION_PASSWORD=... python cinevision/mcp_server.py
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
```

Automated test: `backend/tests/test_mcp.py`.
