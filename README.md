# Soheil OS Font Installer

A PyQt5 terminal-style font library manager for TrueType (`.ttf`) and
OpenType (`.otf`) fonts.

## Features

- Recursive font-library discovery with byte-identical duplicate removal.
- Duplicate groups and family/folder grouping, with a protected canonical copy.
- Per-user installation by default, with explicit system-wide installation.
- Atomic copies that refuse to overwrite a different same-name font.
- Hash-checked uninstall so unrelated system fonts are not deleted.
- Automatic backups for removals and a persistent `undo`/`history` operation log.
- A persistent size/mtime/hash index to make repeated library scans faster.
- Fast batched rendering for folder and font lists, with queued input during
  other animated terminal output.
- Installation status, system audit, validation, search, live preview,
  side-by-side comparison, custom sample text, themes, command history, and
  cancellation.

## Common commands

```text
list
catalog
duplicates
history
undo
sync
scope user
validate
audit
sys-audit
```

After `list`, enter a folder number. From the font list you can use:

```text
preview 1
info 1
install 1 3
compare 1 2
uninstall 1
```

`install` is intentionally available only in the font-selection view. Use
`install <n...> [--user|--system]` there; `sync` remains available globally.

`list` exposes fonts placed directly in `asset/` through a selectable virtual
`Loose Fonts` group, so they support the same preview, install, and uninstall
actions as family folders.

Folder counts show files on disk; identical duplicate copies are hidden when a
font list is opened. The catalog reports both total files and unique fonts.

`search <text>` opens the visual catalog with the query already applied, so
matching fonts can be previewed or installed immediately.

`catalog` opens a searchable visual grid. It supports family/filename search,
installed/missing/conflict filters, TTF/OTF filters, adjustable preview size,
multi-selection, user/system scope selection, bulk installation, bulk removal,
duplicate-only filtering, grouping by font family or source folder, duplicate
management, undo, and opening the full preview dialog. The full preview offers
editable English and Persian/Arabic samples, live 10-96pt sizing, a glyph and
symbol sheet, reset controls, saving custom samples as application defaults,
and clear arrowless scrollbars for long samples.
The comparison view uses the same live sizing and editable text for both fonts,
with English, Persian/Arabic, and alphabet/symbol presets. Catalog cards are
created incrementally. Font family names and installed-status checks are
resolved after the window appears; family metadata is cached using each file's
size and modification time. The catalog and duplicate manager adapt their
controls to smaller displays, and bulk operations show progress inside the
active dialog.

`duplicates` opens a tree of byte-identical asset copies. The first path in a
group is kept as the canonical copy; selecting extra copies backs them up before
removing them. `history` shows recent reversible operations and `undo` reverses
the newest operation that has not already been undone. Undo refuses to replace
files whose contents changed after the original operation.

Backups, operation history, and the font index are stored in the platform's
per-user application-data directory, outside the packaged executable.

The default scope is stored in `config.json`. System installation may require
administrator privileges; the application no longer elevates on every launch.

## Development

Install dependencies and run the standalone manager tests:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests
python installer.py
```

Build with the supplied spec file:

```powershell
pyinstaller installer.spec
```

The spec bundles the `asset/` directory. User settings are written to the
user configuration directory for packaged builds.
