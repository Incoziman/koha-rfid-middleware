#!/usr/bin/env python3
"""Build the Koha plugin package: dist/koha-plugin-rfiddesk-<version>.kpz

A .kpz is a zip of the plugin's Koha/ tree. Upload it in the staff interface:
Administration > Manage plugins > Upload plugin (plugins must be enabled in
koha-conf.xml first; see docs/desk/desk-setup.md).

    python deploy/build_plugin.py
"""
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "koha-plugin")
MODULE = os.path.join(SRC, "Koha", "Plugin", "Org", "Middelburg", "RFIDDesk.pm")


def plugin_version() -> str:
    with open(MODULE, encoding="utf-8") as fh:
        m = re.search(r"our \$VERSION\s*=\s*'([^']+)'", fh.read())
    if not m:
        raise SystemExit(f"no $VERSION in {MODULE}")
    return m.group(1)


def build(out_dir: str = os.path.join(ROOT, "dist")) -> str:
    version = plugin_version()
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"koha-plugin-rfiddesk-{version}.kpz")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, _, files in os.walk(os.path.join(SRC, "Koha")):
            for f in sorted(files):
                path = os.path.join(folder, f)
                z.write(path, os.path.relpath(path, SRC).replace(os.sep, "/"))
    return out


if __name__ == "__main__":
    path = build(*sys.argv[1:2])
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    print(f"Built {path}")
    for n in names:
        print(f"  {n}")
