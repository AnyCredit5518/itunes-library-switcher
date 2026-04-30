"""
iTunes Library Switcher
=======================
Patches com.apple.iTunes.plist to point at a specific library folder,
then launches iTunes (Windows Store / UWP edition).

Requires: Python 3 (standard library only — no pip dependencies)

Usage:
    python launch_itunes_library.py "D:\Music\iTunes Libraries\Alice Library"
    python launch_itunes_library.py "D:\Music\iTunes Libraries\Bob Library"

How it works:
    iTunes (Windows Store edition) stores the last-opened library in:

        %LOCALAPPDATA%\Packages\AppleInc.iTunes_nzyj5cx40ttqa\LocalCache\
            Roaming\Apple Computer\Preferences\com.apple.iTunes.plist

    Three keys hold the full library path:
        "DATA:1:iTunes Library Location"       — folder path (UTF-16LE bytes)
        "Database Location"                    — file:// URL to the .itl file
        "LXML:1:iTunes Library XML Location"   — .xml path (UTF-16LE bytes)

    This script rewrites those three keys atomically and launches iTunes.
    iTunes MUST be fully closed before running this script.
"""

import os
import sys
import plistlib
import subprocess
import urllib.parse


def build_plist_path() -> str:
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(
        local_appdata,
        "Packages", "AppleInc.iTunes_nzyj5cx40ttqa", "LocalCache",
        "Roaming", "Apple Computer", "Preferences",
        "com.apple.iTunes.plist",
    )


def patch_plist(plist_path: str, library_folder: str, itl_path: str, xml_path: str) -> None:
    with open(plist_path, "rb") as f:
        data = plistlib.load(f)

    # 1) Folder path — UTF-16LE bytes, no BOM, no null terminator
    data["DATA:1:iTunes Library Location"] = library_folder.encode("utf-16-le")

    # 2) file:// URL — spaces → %20, backslashes → forward slashes
    forward = itl_path.replace("\\", "/")
    encoded = urllib.parse.quote(forward, safe=":/")
    data["Database Location"] = f"file://localhost/{encoded}"

    # 3) XML path — UTF-16LE bytes
    data["LXML:1:iTunes Library XML Location"] = xml_path.encode("utf-16-le")

    with open(plist_path, "wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)

    return data["Database Location"]


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python launch_itunes_library.py <library-folder-path>")
        print('  e.g. python launch_itunes_library.py "D:\\Music\\iTunes Libraries\\Alice Library"')
        sys.exit(1)

    library_folder = sys.argv[1].rstrip("\\")

    # Validate
    itl_path = os.path.join(library_folder, "iTunes Library.itl")
    if not os.path.isfile(itl_path):
        print(f"ERROR: Cannot find '{itl_path}'")
        print("Make sure the library folder path is correct and contains 'iTunes Library.itl'.")
        sys.exit(1)

    xml_path = os.path.join(library_folder, "iTunes Library.xml")

    # Patch primary plist
    plist_path = build_plist_path()
    if not os.path.isfile(plist_path):
        print(f"ERROR: iTunes plist not found at:\n  {plist_path}")
        print("Is iTunes (Windows Store edition) installed and has it been run at least once?")
        sys.exit(1)

    db_location = patch_plist(plist_path, library_folder, itl_path, xml_path)
    print(f"Patched: {plist_path}")
    print(f"  Library folder  -> {library_folder}")
    print(f"  Database URL    -> {db_location}")

    # Patch the mirror copy in %APPDATA% if it exists
    appdata = os.environ.get("APPDATA", "")
    mirror_path = os.path.join(appdata, "Apple Computer", "Preferences", "com.apple.iTunes.plist")
    if os.path.isfile(mirror_path):
        patch_plist(mirror_path, library_folder, itl_path, xml_path)
        print(f"Patched mirror: {mirror_path}")

    # Launch iTunes via its Store AUMID
    print("Launching iTunes...")
    subprocess.Popen(
        ["explorer.exe", "shell:AppsFolder\\AppleInc.iTunes_nzyj5cx40ttqa!iTunes"]
    )
    print("Done.")


if __name__ == "__main__":
    main()
