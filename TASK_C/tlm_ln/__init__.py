"""Combining swissTLM3D with cantonal agricultural land use data.

Produces a detailed, yearly land use dataset for Switzerland by taking the
irregularly updated topographic model as a base and overlaying the yearly
cantonal Landwirtschaftliche Nutzungsflaechen.

Typical use, from a notebook or a script:

    from tlm_ln import config, pipeline

    tlm_cfg, _ = config.load("config.toml")
    result = pipeline.prepare_tlm(tlm_cfg)          # once per TLM release

    year_cfg = config.load_year("config.toml", 2025)
    result = pipeline.run_year(year_cfg)            # once per year

Every step logs what it did. Call `setup_logging()` first to see it.
"""

from __future__ import annotations

import logging

__version__ = "2.0.0"

__all__ = ["config", "io", "classify", "geometry", "validate", "pipeline", "setup_logging"]


def setup_logging(level: int = logging.INFO) -> None:
    """Configure readable logging once, without stamping on an existing setup."""
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
