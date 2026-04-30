# Reverse-Engineering the iTunes Library Switcher on Windows

## Problem Statement

When you have multiple iTunes libraries on one Windows PC (e.g., one per family member), the only Apple-supported way to switch between them is:

1. Fully close iTunes
2. Hold **Shift** while launching iTunes
3. Choose a specific `.itl` library file from a dialog

The Shift key interaction is not hard, but it is hidden and easy to forget over time. It can also be awkward for children, shared family PCs, or anyone who just wants a simpler point-and-click way to open the right library.

The first obvious idea was to create shortcuts directly to each `.itl` file. That does launch iTunes, but it does **not** make iTunes open that specific library. Instead, iTunes simply reopens whichever library was used last.

That strongly suggested iTunes was storing its "last-opened library" state somewhere else. If that state could be found, it should be possible to override it and build reliable one-click launchers.

**Goal:** Find the mechanism that stores the "last-opened library" state, determine if it can be safely overridden, and build one-click per-library launchers.

---

## Environment

| Component | Detail |
|---|---|
| OS | Windows 10/11 |
| iTunes | Windows Store (UWP) edition, version 12.x (`AppleInc.iTunes_nzyj5cx40ttqa`) |
| Libraries | `D:\Music\iTunes Libraries\Alice Library\` and `D:\Music\iTunes Libraries\Bob Library\` |
| Tools | ProcMon (Sysinternals), PowerShell 5.1, Python 3.12 |

---

## Phase 1: Capturing Evidence with ProcMon

### Strategy

Run two separate ProcMon traces, one per library switch, then compare them to find what changed.

### ProcMon Filter Configuration

| Filter | Condition |
|---|---|
| Process Name | is `iTunes.exe` |
| Operation | is one of: `WriteFile`, `SetRenameInformationFile`, `RegSetValue`, `CreateFile`, `CloseFile` |

These operations capture all meaningful state changes: file writes, atomic renames, registry modifications, and file creation.

### Capture Procedure

1. **Trace A:** Shift-launch iTunes, select "Alice Library," let it fully load, close cleanly, then export the ProcMon log to `Logfile-A.CSV`
2. **Trace B:** Shift-launch iTunes, select "Bob Library," let it fully load, close cleanly, then export the ProcMon log to `Logfile-B.CSV`

### Raw Data Summary

```powershell
$a = Import-Csv "C:\tools\Logfile-A.CSV"
$b = Import-Csv "C:\tools\Logfile-B.CSV"
"A rows: $($a.Count)  B rows: $($b.Count)"
```

| Trace | Total Rows | CreateFile | CloseFile | WriteFile | SetRenameInformationFile | RegSetValue |
|---|---|---|---|---|---|---|
| A (Alice) | 4,584 | 2,865 | 1,432 | 247 | 33 | **7** |
| B (Bob) | 5,836 | 3,655 | 1,906 | 225 | 41 | **9** |

**Key observation:** Only 7–9 registry writes total. That's a tiny haystack to search for the needle.

---

## Phase 2: Eliminate the Registry Hypothesis

### Command

```powershell
$a | Where-Object { $_.Operation -eq 'RegSetValue' } |
    Format-Table Path, Detail -AutoSize -Wrap
```

### Results (Both Traces)

| Registry Path | Value |
|---|---|
| `...\iTunes\Preferences\DontAutomaticallySyncIPods` | `REG_DWORD: 0` (identical in both) |
| `HKLM\...\Services\bam\State\UserSettings\...\AppleInc.iTunes_nzyj5cx40ttqa` | `REG_BINARY` (timestamps only, Windows Background Activity Moderator tracking when the app ran) |

### Conclusion

**The registry does NOT store the library path.** Both traces wrote the exact same registry keys with no library-specific differentiation. The BAM entries are just execution timestamps maintained by Windows itself.

---

## Phase 3: Compare Unique File/Registry Paths

### Command

```powershell
$pathsA = $a | Select-Object -ExpandProperty Path -Unique
$pathsB = $b | Select-Object -ExpandProperty Path -Unique

$onlyA = $pathsA | Where-Object { $_ -notin $pathsB }
$onlyB = $pathsB | Where-Object { $_ -notin $pathsA }
```

### Results

- **A unique paths:** 472 total, **42 only in A**
- **B unique paths:** 504 total, **74 only in B**

### Key Patterns in A-Only Paths

```
D:\Music\iTunes Libraries\Alice Library\iTunes Library.itl
D:\Music\iTunes Libraries\Alice Library\Album Artwork\...
D:\Music\iTunes Libraries\Alice Library\iTunes Library Play Data.plist
...Apple Computer\Preferences\com.apple.iTunes.plist.Xa23032    ← temp file
...Apple Computer\Preferences\com.apple.iTunes.plist.Xa24360    ← temp file
...Apple Computer\Preferences\com.apple.iTunes.plist.Xa11872    ← temp file
```

### Key Patterns in B-Only Paths

```
D:\Music\iTunes Libraries\Bob Library\iTunes Library.itl
D:\Music\iTunes Libraries\Bob Library\Album Artwork\...
D:\Music\iTunes Libraries\Bob Library\iTunes Library Play Data.plist
...Apple Computer\Preferences\com.apple.iTunes.plist.Xa24744    ← temp file
...Apple Computer\Preferences\com.apple.iTunes.plist.Xa15256    ← temp file
```

### Interpretation

1. Library-specific content files (`.itl`, `Album Artwork/`, `.itdb`) naturally only appear in the active library's folder. These are the library **data**, not the **selector**.
2. The `.Xa#####` suffixed files are **Apple's atomic write pattern**: write to a temp file, then rename over the real file. The PID-based suffix changes between runs, so these appear as "unique" even though they target the same final file.
3. The final target is always: **`com.apple.iTunes.plist`**

---

## Phase 4: Trace the Atomic Rename Pattern

### Command

```powershell
$a | Where-Object { $_.Operation -eq 'SetRenameInformationFile' } |
    Select-Object Path, Detail | Format-List
```

### Results (Representative Entries from Each Trace)

**Trace A: Plist atomic writes**
```
Path:   ...Preferences\com.apple.iTunes.plist.Xa23032
Detail: ReplaceIfExists: True, FileName: ...Preferences\com.apple.iTunes.plist
```

**Trace A: Library file atomic writes**
```
Path:   D:\Music\iTunes Libraries\Alice Library\Temp File.tmp
Detail: ReplaceIfExists: False, FileName: ...Alice Library\iT.tmp

Path:   D:\Music\iTunes Libraries\Alice Library\iTunes Library.itl
Detail: ReplaceIfExists: False, FileName: ...Alice Library\Temp File.tmp

Path:   D:\Music\iTunes Libraries\Alice Library\iT.tmp
Detail: ReplaceIfExists: False, FileName: ...Alice Library\iTunes Library.itl
```

**Trace B: Same patterns but with Bob Library paths.**

### Interpretation

iTunes uses a **three-step atomic rename** for `.itl` files:
1. `Temp File.tmp` becomes `iT.tmp` (stage the new content)
2. `iTunes Library.itl` becomes `Temp File.tmp` (back up the old)
3. `iT.tmp` becomes `iTunes Library.itl` (commit the new)

More importantly, `com.apple.iTunes.plist` is atomically rewritten **many times per session** (roughly 10–15 times across startup, runtime, and shutdown). This is exactly what you'd expect for a preferences file that stores operational state including the active library.

---

## Phase 5: Parse the Plist and Find the Smoking Gun

### Plist File Location

For the Windows Store edition of iTunes, the primary plist is at:

```
%LOCALAPPDATA%\Packages\AppleInc.iTunes_nzyj5cx40ttqa\LocalCache\
    Roaming\Apple Computer\Preferences\com.apple.iTunes.plist
```

A mirror copy also exists at:

```
%APPDATA%\Apple Computer\Preferences\com.apple.iTunes.plist
```

### Parsing (Requires Python 3; `plistlib` is in the standard library)

```python
import plistlib

# Construct the path using environment variables in production;
# shown here with a placeholder username for illustration.
path = r'C:\Users\<youruser>\AppData\Local\Packages\AppleInc.iTunes_nzyj5cx40ttqa' \
       r'\LocalCache\Roaming\Apple Computer\Preferences\com.apple.iTunes.plist'

with open(path, 'rb') as f:
    data = plistlib.load(f)

# Search for library-related keys
terms = ['library', 'database', 'location', 'path', 'folder', 'dir', 'itl']
for k, v in sorted(data.items()):
    if any(term in k.lower() for term in terms):
        print(f"{k} = {repr(v)[:200]}")
```

### Result: The Three Keys

The plist contains **18 total keys**. Three of them store the active library:

| Key | Type | Value (after Bob Library session) |
|---|---|---|
| `DATA:1:iTunes Library Location` | `bytes` (UTF-16LE) | `D:\Music\iTunes Libraries\Bob Library` |
| `Database Location` | `string` | `file://localhost/D:/Music/iTunes%20Libraries/Bob%20Library/iTunes%20Library.itl` |
| `LXML:1:iTunes Library XML Location` | `bytes` (UTF-16LE) | `D:\Music\iTunes Libraries\Bob Library\iTunes Library.xml` |

### All 18 Keys in the Plist (for reference)

| Key | Purpose |
|---|---|
| `AppleLanguages` | UI language (`['en']`) |
| `AppleLocale` | Locale (`en_US`) |
| **`DATA:1:iTunes Library Location`** | **Library folder path (UTF-16LE bytes)** |
| **`Database Location`** | **file:// URL to the .itl file** |
| **`LXML:1:iTunes Library XML Location`** | **Path to the .xml export (UTF-16LE bytes)** |
| `RDoc:132:Documents` | Binary blob (documents state) |
| `bwui` | Browse window UI state (sidebar, view modes) |
| `debugAssertCategoriesEnabled` | Debug flag (`0`) |
| `debugAssertCategoriesVersion` | Debug version (`3`) |
| `gnot:1:Gracenote Match Registered User ID` | Gracenote (CD lookup) registration |
| `gnot:2:Gracenote CDDB Lookup Registered User ID` | Gracenote CDDB registration |
| `license-agreements` | EULA acceptance state |
| `log-push` | Logging flag (`0`) |
| `pref:130:Preferences` | Binary blob (general prefs) |
| `pref:400:Touch Remote Preferences` | Binary blob (Remote app prefs) |
| `rprf:0000000000000000` | Binary blob (unknown prefs) |
| `rspl:1:AirTunes Speaker List` | AirPlay speaker config (embedded XML) |
| `storefront` | iTunes Store region (`143441-1,32` = US) |

### Other Plists Checked

| File | Contents | Relevant? |
|---|---|---|
| `ByHost\com.apple.iTunes.{machine-GUID}.plist` | 1 key: `pref:200:Machine Preferences` (opaque binary blob) | **No** - no library path |
| `com.apple.iTunes.eq.plist` | 1 key: `eqps:129:EQPresets` (equalizer presets blob) | **No** |

---

## Phase 6: Validate the Patch Approach

### Round-Trip Test

```python
import plistlib, tempfile, os

# Read the live plist
with open(plist_path, 'rb') as f:
    data = plistlib.load(f)

# Patch to Alice Library
test_folder = r"D:\Music\iTunes Libraries\Alice Library"
data["DATA:1:iTunes Library Location"] = test_folder.encode("utf-16-le")
data["Database Location"] = "file://localhost/D:/Music/iTunes%20Libraries/Alice%20Library/iTunes%20Library.itl"
data["LXML:1:iTunes Library XML Location"] = (test_folder + r"\iTunes Library.xml").encode("utf-16-le")

# Write to temp file, read back, verify
with tempfile.NamedTemporaryFile(delete=False, suffix=".plist") as tmp:
    plistlib.dump(data, tmp, fmt=plistlib.FMT_BINARY)

with open(tmp.name, 'rb') as f:
    verify = plistlib.load(f)

assert "Alice Library" in verify["DATA:1:iTunes Library Location"].decode("utf-16-le")
assert "Alice" in verify["Database Location"]
```

### Result

```
=== Current plist state ===
  Library Location : D:\Music\iTunes Libraries\Bob Library
  Database Location: file://localhost/D:/Music/iTunes%20Libraries/Bob%20Library/iTunes%20Library.itl
  XML Location     : D:\Music\iTunes Libraries\Bob Library\iTunes Library.xml

=== After patching to Alice Library (temp file) ===
  Library Location : D:\Music\iTunes Libraries\Alice Library
  Database Location: file://localhost/D:/Music/iTunes%20Libraries/Alice%20Library/iTunes%20Library.itl
  XML Location     : D:\Music\iTunes Libraries\Alice Library\iTunes Library.xml

All round-trip checks PASSED.
```

---

## Phase 7: Build the Launcher

### Core Script: `launch_itunes_library.py`

The script:
1. Accepts a library folder path as a command-line argument
2. Validates that the folder contains `iTunes Library.itl`
3. Reads the binary plist
4. Patches all three library-location keys
5. Writes the plist back in binary format
6. Also patches the mirror copy at `%APPDATA%` if it exists
7. Launches iTunes via `explorer.exe shell:AppsFolder\AppleInc.iTunes_nzyj5cx40ttqa!iTunes`

### One-Click `.cmd` Wrappers

```cmd
@echo off
python "%~dp0launch_itunes_library.py" "D:\Music\iTunes Libraries\Alice Library"
```

Create one `.cmd` per family member. These can be pinned to the Start menu, taskbar, or placed on the desktop.

### Key Implementation Details

- **UTF-16LE encoding** for `DATA:1:iTunes Library Location` and `LXML:1:iTunes Library XML Location` - these are raw bytes, not strings, in the plist. No BOM, no null terminator.
- **URL encoding** for `Database Location` - spaces become `%20`, backslashes become forward slashes, prefixed with `file://localhost/`.
- **Binary plist format** (`plistlib.FMT_BINARY`) - iTunes writes binary plists, not XML. The script preserves this format.
- **Mirror patching** - the Store edition maintains a shadow copy in `%APPDATA%`. Both must be updated.

---

## Summary of Findings

### The Answer

iTunes on Windows stores the last-opened library path in **`com.apple.iTunes.plist`**, a binary Apple property list file. Three keys hold the library identity:

1. `DATA:1:iTunes Library Location` - folder path as UTF-16LE bytes
2. `Database Location` - `file://localhost/` URL to the `.itl` file
3. `LXML:1:iTunes Library XML Location` - `.xml` file path as UTF-16LE bytes

**The registry plays no role** in library selection for the Store edition of iTunes.

### Safety Assessment

| Factor | Assessment |
|---|---|
| Format stability | Apple binary plist - well-documented, standard format |
| Round-trip fidelity | Python `plistlib` reads and writes identically |
| Atomic write compatibility | iTunes uses temp+rename; we write directly while iTunes is closed |
| Risk of data loss | None - we only change 3 keys out of 18; library data files are untouched |
| Prerequisite | iTunes **must** be fully closed before patching |

### Investigation Method Summary

| Step | Technique | What It Eliminated or Revealed |
|---|---|---|
| 1 | ProcMon differential capture | Narrowed from "all possible state" to ~5K rows per trace |
| 2 | Operation breakdown | Showed only 7-9 registry writes - tiny search space |
| 3 | Registry analysis | **Eliminated registry** - no library paths stored |
| 4 | Unique path comparison | Identified `com.apple.iTunes.plist` as the shared preference file |
| 5 | Atomic rename tracing | Confirmed plist is actively rewritten many times per session |
| 6 | Binary plist parsing | **Found the exact 3 keys** storing the library path |
| 7 | Round-trip validation | Confirmed safe read/patch/write cycle |

---

## Files Produced

| File | Purpose |
|---|---|
| `launch_itunes_library.py` | Core library switcher - patches plist and launches iTunes |
| `iTunes - Alice.cmd` | One-click launcher for Alice's library |
| `iTunes - Bob.cmd` | One-click launcher for Bob's library |
| `parse_plist.py` | Utility - search plist for library-related keys |
| `dump_plist.py` | Utility - dump all keys from any plist file |
| `test_plist_patch.py` | Validation - round-trip patch test (non-destructive) |

---

## Appendix: Adding More Libraries

To add a new library launcher:

1. Create the library once using the Shift-launch method
2. Create a new `.cmd` file:

```cmd
@echo off
python "%~dp0launch_itunes_library.py" "D:\Music\iTunes Libraries\Frank Library"
```

3. (Optional) Right-click the `.cmd`, choose Send to > Desktop (create shortcut), then change the icon to the iTunes icon from `C:\Program Files\WindowsApps\AppleInc.iTunes_...\iTunes.exe`.
