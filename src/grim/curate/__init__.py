"""curate — human-only library ergonomics (browse/curate/shell integration).

Never part of the agent's closed verb set: nothing here is wired into
adapter/tools.py::GRIM_TOOLS, so the agent cannot call it. Dispatched only
from src/grim/cli.py, like init/config/doctor. See curate/CLAUDE.md.
"""
