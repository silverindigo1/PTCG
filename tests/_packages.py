"""Load the two service packages under distinct names.

``services/api/app`` and ``services/worker/app`` are both called ``app``. Inside
their own containers that is correct and unambiguous. In one test process it is
not: whichever directory lands first on ``sys.path`` wins, and the other package
silently disappears.

Rather than reach for a rename that would ripple into both Dockerfiles, the
tests load each package explicitly from its own path under its own alias. The
production import names are untouched.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def load_package(alias: str, path: Path) -> ModuleType:
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(
        alias, path / "__init__.py", submodule_search_locations=[str(path)]
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {alias} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def worker(submodule: str) -> ModuleType:
    load_package("worker_app", ROOT / "services" / "worker" / "app")
    return importlib.import_module(f"worker_app.{submodule}")


def api(submodule: str) -> ModuleType:
    load_package("api_app", ROOT / "services" / "api" / "app")
    return importlib.import_module(f"api_app.{submodule}")
