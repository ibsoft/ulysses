from __future__ import annotations

import os
from functools import lru_cache
from importlib.resources import files
from pathlib import Path


@lru_cache(maxsize=1)
def get_project_root() -> Path:
    """Return the Sirina project root for source checkouts."""
    return Path(__file__).resolve().parents[2]


def get_default_model_root() -> Path:
    """Return the default writable model directory for Sirina."""
    model_dir = os.getenv("SIRINA_MODEL_DIR")
    if model_dir:
        return Path(model_dir).expanduser()
    project_models = get_project_root() / "models"
    if project_models.exists():
        return project_models
    return Path.home() / ".sirina" / "models"


def resource_path(relative_path: str) -> Path:
    """Resolve Sirina assets.

    Model lookup order:
    1. SIRINA_MODEL_DIR, when set
    2. Sirina project-local models/
    3. A sibling/parent Sirina checkout models/ directory.
    """
    relative = Path(relative_path)
    if relative.parts and relative.parts[0] == "models":
        model_dir = os.getenv("SIRINA_MODEL_DIR")
        if model_dir:
            return Path(model_dir).expanduser() / Path(*relative.parts[1:])

    project_candidate = get_project_root() / relative
    if project_candidate.exists():
        return project_candidate

    try:
        package_candidate = Path(str(files("sirina.assets") / relative))
        if package_candidate.exists():
            return package_candidate
    except Exception:
        pass

    sirina_candidate = get_project_root().parent / relative
    if sirina_candidate.exists():
        return sirina_candidate

    if relative.parts and relative.parts[0] == "models":
        return get_default_model_root() / Path(*relative.parts[1:])
    return project_candidate
