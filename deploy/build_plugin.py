#!/usr/bin/env python3
"""Build the Koha plugin package: dist/koha-plugin-rfiddesk-<version>.kpz

A .kpz is a zip of the plugin's Koha/ tree. Upload it in the staff interface:
Administration > Manage plugins > Upload plugin (plugins must be enabled in
koha-conf.xml first; see docs/desk/desk-setup.md).

The .kpz also carries the desk agent, so desk PCs don't need this repo:
    RFIDDesk/agent/koha_rfid_middleware-<version>-py3-none-any.whl
    RFIDDesk/agent/install_agent.ps1     (knows the wheel's file name)
    RFIDDesk/agent/agent.example.ini
    RFIDDesk/staticapi.json              (Koha serves the three files above at
                                          /api/v1/contrib/rfiddesk/static/agent/...)

Building the wheel runs pip, which needs internet the first time (to fetch
setuptools into pip's cache).

    python deploy/build_plugin.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "koha-plugin")
MODULE = os.path.join(SRC, "Koha", "Plugin", "Org", "Middelburg", "RFIDDesk.pm")
BUNDLE = "Koha/Plugin/Org/Middelburg/RFIDDesk"      # path inside the .kpz
INSTALLER = os.path.join(ROOT, "deploy", "install_agent.ps1")
EXAMPLE_INI = os.path.join(ROOT, "deploy", "agent.example.ini")
WHEEL_LINE = re.compile(r"^\$BundledWheel = '[^']*'", re.M)


def _search(path: str, pattern: str) -> str:
    with open(path, encoding="utf-8") as fh:
        m = re.search(pattern, fh.read(), re.M)
    if not m:
        raise SystemExit(f"no version found in {path}")
    return m.group(1)


def plugin_version() -> str:
    return _search(MODULE, r"our \$VERSION\s*=\s*'([^']+)'")


def check_versions() -> str:
    """The plugin, the Python package and pyproject.toml must agree."""
    versions = {
        "RFIDDesk.pm $VERSION": plugin_version(),
        "koha_rfid/__init__.py __version__": _search(os.path.join(ROOT, "koha_rfid", "__init__.py"),
                                                     r"^__version__\s*=\s*\"([^\"]+)\""),
        "pyproject.toml version": _search(os.path.join(ROOT, "pyproject.toml"),
                                          r"^version\s*=\s*\"([^\"]+)\""),
    }
    if len(set(versions.values())) != 1:
        raise SystemExit("version mismatch:\n" + "\n".join(f"  {k}: {v}" for k, v in versions.items()))
    return plugin_version()


def build_wheel(out_dir: str) -> str:
    # setuptools reuses build/lib, so a module deleted or moved in the source
    # (e.g. koha_rfid/reader.py -> readers/) would still end up in the wheel.
    shutil.rmtree(os.path.join(ROOT, "build", "lib"), ignore_errors=True)
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", ROOT, "--no-deps", "--quiet",
                        "--disable-pip-version-check", "-w", out_dir],
                       capture_output=True, text=True)
    wheels = [f for f in os.listdir(out_dir) if f.endswith(".whl")]
    if r.returncode != 0 or len(wheels) != 1:
        raise RuntimeError("pip wheel failed:\n" + r.stdout + r.stderr)
    return os.path.join(out_dir, wheels[0])


def installer_for(wheel_name: str) -> bytes:
    with open(INSTALLER, encoding="utf-8-sig") as fh:
        text = fh.read()
    text, n = WHEEL_LINE.subn(f"$BundledWheel = '{wheel_name}'", text, count=1)
    if n != 1:
        raise SystemExit(f"no \"$BundledWheel = ''\" line in {INSTALLER}")
    # UTF-8 with BOM: Windows PowerShell 5.1 reads a BOM-less script as ANSI.
    return b"\xef\xbb\xbf" + text.encode("utf-8")


def static_spec(files) -> dict:
    spec = {}
    for i, name in enumerate(files):
        spec[f"/agent/{name}"] = {
            "get": {
                "x-mojo-to": "Static#get",
                "operationId": f"rfiddeskAgentFile{i}",
                "tags": ["rfiddesk"],
                "description": f"Desk agent download: {name} (public, no login).",
                "produces": ["application/octet-stream"],
                "responses": {"200": {"description": name, "schema": {"type": "file"}}},
            }
        }
    return spec


def build(out_dir: str = os.path.join(ROOT, "dist")) -> str:
    version = check_versions()
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"koha-plugin-rfiddesk-{version}.kpz")
    with tempfile.TemporaryDirectory() as tmp:
        wheel = build_wheel(tmp)
        wheel_name = os.path.basename(wheel)
        with open(EXAMPLE_INI, "rb") as fh:
            ini = fh.read()
        agent_files = [wheel_name, "install_agent.ps1", "agent.example.ini"]
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for folder, _, files in os.walk(os.path.join(SRC, "Koha")):
                for f in sorted(files):
                    path = os.path.join(folder, f)
                    z.write(path, os.path.relpath(path, SRC).replace(os.sep, "/"))
            z.write(wheel, f"{BUNDLE}/agent/{wheel_name}")
            z.writestr(f"{BUNDLE}/agent/install_agent.ps1", installer_for(wheel_name))
            z.writestr(f"{BUNDLE}/agent/agent.example.ini", ini)
            z.writestr(f"{BUNDLE}/staticapi.json", json.dumps(static_spec(agent_files), indent=2) + "\n")
    return out


if __name__ == "__main__":
    path = build(*sys.argv[1:2])
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    print(f"Built {path}")
    for n in names:
        print(f"  {n}")
