"""Golden image management: CI guard, staging, promote, provenance.

Layout: <suite>/goldens/<section>/<name>.exr, staged copies in
<suite>/out/staging/<section>/<name>.exr (diff images next to them as
<name>.diff.exr). A golden is identified by the key "<section>/<name>".
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

from . import oiio

CI_VARS = ("CI", "GITHUB_ACTIONS", "JENKINS_URL")
PROVENANCE_NAME = "PROVENANCE.json"
STAGING_NAME = "STAGING.json"
RENDERERS = ("gpu", "sw")
_DIFF_SUFFIX = ".diff.exr"


def _refuse(message: str) -> SystemExit:
    """Print the message to stderr and return SystemExit(2) for the caller to raise."""
    print(f"error: {message}", file=sys.stderr)
    return SystemExit(2)


def refuse_in_ci(env: Mapping[str, str] = os.environ) -> None:
    """Exit 2 when any CI variable is present (even if empty)."""
    for var in CI_VARS:
        if var in env:
            raise _refuse(f"refusing to regenerate or promote goldens in CI "
                          f"({var} is set)")


def fresh_staging(suite_out: Path) -> Path:
    staging = Path(suite_out) / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    return staging


def read_provenance(goldens: Path) -> dict:
    path = Path(goldens) / PROVENANCE_NAME
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_provenance(goldens: Path, data: dict) -> None:
    path = Path(goldens) / PROVENANCE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_staging_info(staging: Path, data: dict) -> None:
    """Record what rendered the staged files (build, renderer, date, per-case numbers)."""
    path = Path(staging) / STAGING_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_staging_info(staging: Path) -> dict:
    """Read STAGING.json; SystemExit(2) when absent, unreadable or incomplete."""
    path = Path(staging) / STAGING_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _refuse(f"{path} not found; run --generate first") from None
    except (OSError, ValueError) as e:
        raise _refuse(f"cannot read {path}: {e}") from None
    if (not isinstance(data, dict) or not isinstance(data.get("build_line"), str)
            or not data["build_line"] or data.get("renderer") not in RENDERERS):
        raise _refuse(f"{path} lacks a build_line or renderer (gpu/sw); run --generate again")
    return data


def _staged_keys(staging: Path) -> list[str]:
    if not staging.is_dir():
        return []
    return sorted(p.relative_to(staging).with_suffix("").as_posix()
                  for p in staging.rglob("*.exr")
                  if not p.name.endswith(_DIFF_SUFFIX))


def _default_confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() == "y"
    except EOFError:
        return False


def promote(staging: Path, goldens: Path, names: Iterable[str] | None,
            build_line: str, yes: bool, oiiotool: Path, *,
            sources: Mapping[str, str] | None = None,
            renderer: str = "gpu",
            confirm: Callable[[str], bool] | None = None,
            env: Mapping[str, str] | None = None,
            diff_timeout: float = 60.0) -> list[str]:
    """Copy staged goldens into `goldens` after showing a comparison table.

    `names` are "<section>/<name>" keys; None means every staged file. Returns
    the promoted keys. Refusals raise SystemExit(2). Only the named staged files
    are copied and only their PROVENANCE.json entries are rewritten."""
    refuse_in_ci(os.environ if env is None else env)
    staging, goldens = Path(staging), Path(goldens)
    staged = _staged_keys(staging)
    if not staged:
        raise _refuse(f"nothing staged in {staging}; run --generate first")
    keys = staged if names is None else list(dict.fromkeys(names))
    unknown = [k for k in keys if k not in staged]
    if unknown:
        raise _refuse(f"not staged: {', '.join(unknown)}")
    if not keys:
        raise _refuse("no goldens selected to promote")

    for key in keys:
        new = staging / f"{key}.exr"
        old = goldens / f"{key}.exr"
        print(key)
        if old.exists():
            print("  old exists: yes")
            d = oiio.diff(oiiotool, old, new, timeout=diff_timeout)
            if d.timed_out:
                print("  oiiotool vs current golden: timed out")
            elif not (math.isfinite(d.max_abs) and math.isfinite(d.mean_abs)):
                print(f"  oiiotool vs current golden: oiiotool failed (rc={d.returncode})")
            else:
                print(f"  oiiotool vs current golden: max={d.max_abs:g} "
                      f"mean={d.mean_abs:g}" + (" (match)" if d.match else ""))
        else:
            print("  old exists: no (new golden)")
        diff_img = new.with_name(new.stem + _DIFF_SUFFIX)
        print(f"  diff image: {diff_img if diff_img.exists() else 'MISSING'}")

    if not yes:
        ok = (confirm or _default_confirm)(
            f"Promote {len(keys)} golden(s) to {goldens}? [y/N] ")
        if not ok:
            raise _refuse("promotion not confirmed; nothing copied")

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prov = read_provenance(goldens)
    for key in keys:
        dest = goldens / f"{key}.exr"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staging / f"{key}.exr", dest)
        prov[key] = {"build_line": build_line, "date": date, "renderer": renderer,
                     "source": (sources or {}).get(key, "")}
    write_provenance(goldens, prov)
    return keys
