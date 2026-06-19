#!/usr/bin/env python3
"""Build and verify the release artifacts.

This script is intentionally stricter than the normal test job. It validates
the artifacts an external reviewer actually receives:

- the wheel contains the installable package, schemas, and typing marker;
- the source distribution contains docs, examples, benchmarks, scripts, tests,
  and runtime/integration modules;
- README local links resolve inside the source distribution;
- no ignored local/generated files leak into either artifact;
- the wheel is built through the generated source distribution;
- optional smoke gates can test the unpacked sdist and run examples against an
  installed distribution without source-tree shadowing.

Run locally from the repository root:

    python scripts/check_release_artifacts.py --run-sdist-tests --run-examples
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIST_DIR = REPO_ROOT / "dist"

SDIST_REQUIRED_PATHS = (
    "README.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "MANIFEST.in",
    "docs/architecture.md",
    "docs/STABILITY.md",
    "docs/RELEASE-v1.2.0.md",
    "docs/RELEASE-v1.3.0.md",
    "docs/specs/sprint_28_audit_and_runtime.md",
    "examples/quickstart.py",
    "examples/reference_reducer.py",
    "examples/poisoning_demo/run.py",
    "benchmarks/run_all.py",
    "scripts/audit_public_api.py",
    "tests/fixtures.py",
    "tests/runtime_seed.py",
    "tests/invariants/test_no_silent_writes.py",
    "src/agent_memory_contracts/runtime/store.py",
    "src/agent_memory_contracts/runtime/gate.py",
    "src/agent_memory_contracts/integrations/mcp.py",
    "src/agent_memory_contracts/integrations/langchain.py",
    "src/agent_memory_contracts/schemas/source_record.schema.json",
)

WHEEL_REQUIRED_PATHS = (
    "agent_memory_contracts/__init__.py",
    "agent_memory_contracts/__main__.py",
    "agent_memory_contracts/py.typed",
    "agent_memory_contracts/runtime/store.py",
    "agent_memory_contracts/runtime/gate.py",
    "agent_memory_contracts/integrations/mcp.py",
    "agent_memory_contracts/integrations/langchain.py",
    "agent_memory_contracts/schemas/source_record.schema.json",
)

FORBIDDEN_EXACT_PATHS = {
    "docs/GPT_PRO_FEEDBACK_WORKPLAN.md",
}

FORBIDDEN_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".mavis",
    ".venv",
    ".eggs",
}

FORBIDDEN_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".DS_Store",
)

README_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> None:
    print(">>>", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _run_capture(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> str:
    print(">>>", " ".join(cmd), flush=True)
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, file=sys.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    return proc.stdout.strip()


def _build(dist_dir: Path) -> None:
    dist_dir.mkdir(parents=True, exist_ok=True)
    for artifact in (*dist_dir.glob("*.tar.gz"), *dist_dir.glob("*.whl")):
        artifact.unlink()
    try:
        _run(
            [
                sys.executable,
                "-m",
                "build",
                "--outdir",
                str(dist_dir),
            ],
            cwd=REPO_ROOT,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            "release artifact build failed; install dev deps with "
            "`python -m pip install -e '.[dev]'`"
        ) from exc


def _artifact(dist_dir: Path, pattern: str, label: str) -> Path:
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        names = ", ".join(p.name for p in matches) or "none"
        raise SystemExit(f"expected exactly one {label}, found {names}")
    return matches[0]


def _sdist_members(sdist: Path) -> tuple[str, ...]:
    with tarfile.open(sdist, "r:gz") as archive:
        names = [m.name for m in archive.getmembers() if m.name]

    roots = {name.split("/", 1)[0] for name in names}
    if len(roots) != 1:
        raise SystemExit(f"sdist should have one top-level directory: {roots}")
    root = next(iter(roots))

    rel_names = []
    for name in names:
        if name == root:
            continue
        prefix = root + "/"
        if not name.startswith(prefix):
            raise SystemExit(f"unexpected sdist member outside root: {name}")
        rel_names.append(name[len(prefix):])
    return tuple(sorted(rel_names))


def _wheel_members(wheel: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(wheel) as archive:
        return tuple(sorted(archive.namelist()))


def _has_path(members: tuple[str, ...], path: str) -> bool:
    clean = path.rstrip("/")
    return clean in members or any(m.startswith(clean + "/") for m in members)


def _assert_required(members: tuple[str, ...], required: tuple[str, ...], label: str) -> None:
    missing = [path for path in required if not _has_path(members, path)]
    if missing:
        joined = "\n  - ".join(missing)
        raise SystemExit(f"{label} is missing required paths:\n  - {joined}")


def _forbidden_reason(member: str) -> str | None:
    if member in FORBIDDEN_EXACT_PATHS:
        return "explicitly forbidden path"
    parts = PurePosixPath(member).parts
    if any(part in FORBIDDEN_PARTS or part.startswith(".venv-") for part in parts):
        return "local/cache/virtualenv path"
    if (
        len(parts) >= 3
        and parts[0] == "examples"
        and parts[2] == "out"
    ):
        return "generated example output"
    if member.endswith(FORBIDDEN_SUFFIXES):
        return "generated file suffix"
    return None


def _assert_no_forbidden(members: tuple[str, ...], label: str) -> None:
    bad: list[str] = []
    for member in members:
        if _forbidden_reason(member) is not None:
            bad.append(member)
    if bad:
        sample = "\n  - ".join(bad[:40])
        more = "" if len(bad) <= 40 else f"\n  ... and {len(bad) - 40} more"
        raise SystemExit(f"{label} contains forbidden files:\n  - {sample}{more}")


def _source_package_members() -> tuple[str, ...]:
    package_root = REPO_ROOT / "src" / "agent_memory_contracts"
    members: list[str] = []
    for path in package_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT / "src").as_posix()
        if _forbidden_reason(rel) is None:
            members.append(rel)
    return tuple(sorted(members))


def _source_schema_members() -> tuple[str, ...]:
    schema_root = REPO_ROOT / "src" / "agent_memory_contracts" / "schemas"
    return tuple(
        sorted(
            path.relative_to(REPO_ROOT / "src").as_posix()
            for path in schema_root.glob("*.schema.json")
        )
    )


def _assert_wheel_surface(wheel_members: tuple[str, ...]) -> None:
    top_levels = {
        PurePosixPath(member).parts[0]
        for member in wheel_members
        if PurePosixPath(member).parts
    }
    dist_info_roots = {
        root
        for root in top_levels
        if re.fullmatch(r"agent_memory_contracts-[^/]+\.dist-info", root)
    }
    if len(dist_info_roots) != 1:
        raise SystemExit(
            "wheel should contain exactly one agent_memory_contracts "
            f"dist-info directory, found {sorted(dist_info_roots)}"
        )

    extra_roots = top_levels - {"agent_memory_contracts"} - dist_info_roots
    if extra_roots:
        raise SystemExit(
            "wheel contains unexpected top-level paths: "
            + ", ".join(sorted(extra_roots))
        )

    expected_package = set(_source_package_members())
    actual_package = {
        member
        for member in wheel_members
        if member.startswith("agent_memory_contracts/")
        and not member.endswith("/")
    }
    missing = sorted(expected_package - actual_package)
    extra = sorted(actual_package - expected_package)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing package files:\n  - " + "\n  - ".join(missing[:40]))
            if len(missing) > 40:
                details.append(f"  ... and {len(missing) - 40} more")
        if extra:
            details.append("unexpected package files:\n  - " + "\n  - ".join(extra[:40]))
            if len(extra) > 40:
                details.append(f"  ... and {len(extra) - 40} more")
        raise SystemExit("wheel package surface mismatch:\n" + "\n".join(details))


def _assert_schema_set(wheel_members: tuple[str, ...]) -> None:
    expected = set(_source_schema_members())
    actual = {
        name
        for name in wheel_members
        if name.startswith("agent_memory_contracts/schemas/")
        and name.endswith(".schema.json")
    }
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing schemas:\n  - " + "\n  - ".join(missing))
        if extra:
            details.append("unexpected schemas:\n  - " + "\n  - ".join(extra))
        raise SystemExit("wheel schema set mismatch:\n" + "\n".join(details))


def _readme_local_links() -> tuple[str, ...]:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    links: set[str] = set()
    for match in README_LINK_RE.finditer(text):
        raw = unquote(match.group(1)).strip()
        if not raw:
            continue
        if raw.startswith(("#", "http://", "https://", "mailto:", "//")):
            continue
        path = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        if not path:
            continue
        links.add(path)
    return tuple(sorted(links))


def _assert_readme_links_in_sdist(sdist_members: tuple[str, ...]) -> None:
    missing = [
        link
        for link in _readme_local_links()
        if not _has_path(sdist_members, link)
    ]
    if missing:
        joined = "\n  - ".join(missing)
        raise SystemExit(f"README local links missing from sdist:\n  - {joined}")


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _create_venv(path: Path) -> Path:
    venv.EnvBuilder(with_pip=True).create(path)
    python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=REPO_ROOT,
        env=_clean_env(),
    )
    return python


def _venv_env(venv_dir: Path) -> dict[str, str]:
    env = _clean_env()
    bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


def _venv_script(venv_dir: Path, name: str) -> Path:
    script = venv_dir / ("Scripts" if os.name == "nt" else "bin") / name
    if os.name == "nt":
        script = script.with_suffix(".exe")
    return script


def _smoke_wheel_install(wheel: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="amc-wheel-smoke-") as tmp:
        tmp_root = Path(tmp)
        venv_dir = Path(tmp) / "venv"
        python = _create_venv(venv_dir)
        env = _venv_env(venv_dir)
        _run([str(python), "-m", "pip", "install", str(wheel)], cwd=tmp_root, env=env)
        version = _run_capture(
            [
                str(python),
                "-c",
                (
                    "from importlib import metadata; "
                    "import agent_memory_contracts as amc; "
                    "from agent_memory_contracts.runtime import MemoryStore; "
                    "dist_version = metadata.version('agent-memory-contracts'); "
                    "assert amc.__version__ == dist_version, "
                    "(amc.__version__, dist_version); "
                    "assert MemoryStore; "
                    "print(dist_version)"
                ),
            ],
            cwd=tmp_root,
            env=env,
        )
        expected = f"agent-memory-contracts {version}"
        module_version = _run_capture(
            [str(python), "-m", "agent_memory_contracts", "--version"],
            cwd=tmp_root,
            env=env,
        )
        if module_version != expected:
            raise SystemExit(
                f"module CLI version mismatch: expected {expected!r}, "
                f"got {module_version!r}"
            )
        console_script = _venv_script(venv_dir, "agent-memory-contracts")
        console_version = _run_capture(
            [str(console_script), "--version"],
            cwd=tmp_root,
            env=env,
        )
        if console_version != expected:
            raise SystemExit(
                f"console CLI version mismatch: expected {expected!r}, "
                f"got {console_version!r}"
            )


def _extract_sdist(sdist: Path, tmp_root: Path) -> Path:
    with tarfile.open(sdist, "r:gz") as archive:
        base = tmp_root.resolve()
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise SystemExit(f"unsafe sdist link member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise SystemExit(f"unsafe sdist member type: {member.name}")
            target = (tmp_root / member.name).resolve()
            if target != base and base not in target.parents:
                raise SystemExit(f"unsafe sdist member path: {member.name}")
        archive.extractall(tmp_root)
    roots = [p for p in tmp_root.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise SystemExit(f"expected one extracted sdist root, found {roots}")
    return roots[0]


def _run_sdist_tests(sdist: Path, *, run_examples: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="amc-sdist-smoke-") as tmp:
        tmp_root = Path(tmp)
        source_root = _extract_sdist(sdist, tmp_root)
        venv_dir = tmp_root / "venv"
        python = _create_venv(venv_dir)
        install_env = _venv_env(venv_dir)
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                ".[dev,jsonschema,langchain,mcp]",
            ],
            cwd=source_root,
            env=install_env,
        )
        test_cwd = tmp_root / "test-run"
        test_cwd.mkdir()
        _run(
            [str(python), "-m", "pytest", "-q", str(source_root / "tests")],
            cwd=test_cwd,
            env=install_env,
        )
        if run_examples:
            example_paths = sorted((source_root / "examples").glob("*.py"))
            example_paths += sorted((source_root / "examples").glob("*/run.py"))
            for path in example_paths:
                _run([str(python), str(path)], cwd=test_cwd, env=install_env)


def _twine_check(sdist: Path, wheel: Path) -> None:
    try:
        _run([sys.executable, "-m", "twine", "check", str(sdist), str(wheel)], cwd=REPO_ROOT)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            "twine check failed; install dev deps with "
            "`python -m pip install -e '.[dev]'`"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST_DIR)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-twine-check", action="store_true")
    parser.add_argument("--skip-wheel-install", action="store_true")
    parser.add_argument("--run-sdist-tests", action="store_true")
    parser.add_argument("--run-examples", action="store_true")
    args = parser.parse_args(argv)

    dist_dir = args.dist_dir.resolve()
    if not args.skip_build:
        _build(dist_dir)

    sdist = _artifact(dist_dir, "*.tar.gz", "sdist")
    wheel = _artifact(dist_dir, "*.whl", "wheel")

    sdist_members = _sdist_members(sdist)
    wheel_members = _wheel_members(wheel)

    _assert_required(sdist_members, SDIST_REQUIRED_PATHS, "sdist")
    _assert_required(wheel_members, WHEEL_REQUIRED_PATHS, "wheel")
    _assert_no_forbidden(sdist_members, "sdist")
    _assert_no_forbidden(wheel_members, "wheel")
    _assert_wheel_surface(wheel_members)
    _assert_schema_set(wheel_members)
    _assert_readme_links_in_sdist(sdist_members)

    if not args.skip_twine_check:
        _twine_check(sdist, wheel)
    if not args.skip_wheel_install:
        _smoke_wheel_install(wheel)
    if args.run_sdist_tests:
        _run_sdist_tests(sdist, run_examples=args.run_examples)

    print("OK: release artifacts verified")
    print(f"  sdist: {sdist.name} ({len(sdist_members)} members)")
    print(f"  wheel: {wheel.name} ({len(wheel_members)} members)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
