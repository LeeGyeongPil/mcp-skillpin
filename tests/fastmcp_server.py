"""A real SDK server (FastMCP 4.2) that serves a skills folder; used by the interop test."""

import sys
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.skills.provider import SkillsDirectoryProvider

root = Path(sys.argv[1]).resolve()
mcp = FastMCP("interop-skills")
mcp.add_provider(SkillsDirectoryProvider(root, reload=True))

if __name__ == "__main__":
    transport = sys.argv[2] if len(sys.argv) > 2 else "stdio"
    if transport == "http":
        mcp.run(transport="http", host="127.0.0.1", port=int(sys.argv[3]), show_banner=False)
    else:
        mcp.run(show_banner=False)
