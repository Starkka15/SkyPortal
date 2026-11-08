"""Packages the SkyPortal Decky plugin as out/SkyPortal.zip (one top-level SkyPortal/ folder).
Forward-slash paths and LF line endings on every text file. Run `pnpm build` first."""

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["main.py", "plugin.json", "package.json", "dist/index.js", "py_modules/skyportal.py"]
TEXT = (".py", ".json", ".js", ".md", ".sh")


def main():
    js = open(os.path.join(ROOT, "dist", "index.js"), encoding="utf-8").read()
    pkg = open(os.path.join(ROOT, "package.json"), encoding="utf-8").read()
    plugin = open(os.path.join(ROOT, "plugin.json"), encoding="utf-8").read()
    problems = []
    if '"type": "module"' not in pkg:
        problems.append('package.json lacks "type": "module"')
    if '"api_version": 1' not in plugin:
        problems.append('plugin.json lacks "api_version": 1')
    if "__PLUGIN_VERSION__" in js:
        problems.append("dist/index.js still has __PLUGIN_VERSION__")
    if problems:
        sys.exit("refusing to package: " + "; ".join(problems))
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    out = os.path.join(ROOT, "out", "SkyPortal.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in FILES + (["LICENSE"] if os.path.exists(os.path.join(ROOT, "LICENSE")) else []):
            data = open(os.path.join(ROOT, rel), "rb").read()
            if rel.endswith(TEXT):
                data = data.replace(b"\r\n", b"\n")
            z.writestr("SkyPortal/" + rel.replace("\\", "/"), data)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert all("\\" not in n for n in names)
        assert all(n.startswith("SkyPortal/") for n in names)
    print(out, len(names), "files")


if __name__ == "__main__":
    main()
