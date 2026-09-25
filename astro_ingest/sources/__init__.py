"""Where capture data comes from. Everything that touches the ASIAIR goes through a Source."""

from astro_ingest.sources.base import Source, SourceEntry

__all__ = ["Source", "SourceEntry"]
