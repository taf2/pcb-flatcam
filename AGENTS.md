# AGENTS.md

## Project Intent

`pcb-cam` is a headless PCB CAM workflow project for moving from EasyEDA Gerber
exports into a FlatCAM-style manufacturing workflow for a Makera Carvera.

The current milestone is not full toolpath generation. It is a reliable starter
flow that:

1. Takes an EasyEDA Gerber zip export.
2. Uses the existing legacy PCB project template/prep flow.
3. Normalizes the output into a project directory with `gerber/ready`.
4. Imports FlatCAM Beta source parsers directly, without launching the FlatCAM
   GUI.
5. Writes FlatCAM's compressed JSON `.FlatPrj` format as a starter project.

Longer term, keep the design open to additional EDA exporters beyond EasyEDA.
Avoid baking EasyEDA-only assumptions deeper than necessary; prefer separating
export-specific normalization from FlatCAM project writing.

## Environment Context

This project is used from Cygwin on Windows.

Important path assumptions:

- Repository root is typically `C:\cygwin64\home\taf2\pcb-cam`.
- FlatCAM Beta source is expected at `C:\cygwin64\home\taf2\flatcam` unless
  overridden.
- Python venv is expected at `.venv/Scripts/python.exe` unless
  `PCB_CAM_PYTHON` is set.
- The legacy project starter is discovered from common Windows/Cygwin locations
  or can be set with `PCB_CAM_LEGACY_NEW_PROJECT`.

Be careful with path handling. The Python code intentionally accepts Windows
paths, Cygwin `/cygdrive/c/...` paths, and Cygwin `/home/...` paths through
`host_path()`.

## Main Commands

Create or refresh a PCB project from an EasyEDA Gerber zip:

```bash
ruby scripts/new-project.rb ~/Desktop/light/switch-board ~/Downloads/Gerber_light-night_switchboard_2026-06-12.zip
```

This writes a starter FlatCAM project:

```text
Project_<gerber-zip-name>_start.FlatPrj
```

Debug only, write a FlatCAM project from an existing project directory with
`gerber/ready`:

```bash
ruby scripts/new-project.rb flatprj ~/Desktop/light/power-board
```

Equivalent direct Python entry point:

```bash
.venv/Scripts/python.exe -m pcb_cam flatprj <PROJECT_DIR>
```

## Key Files

- `scripts/new-project.rb` is the high-level workflow command. It validates the
  Gerber zip, creates or refreshes the project directory, then invokes the
  Python FlatCAM writer.
- `pcb_cam/flatcam_project.py` contains the FlatCAM parser imports, Gerber and
  Excellon serialization, path conversion, and `.FlatPrj` writer.
- `pcb_cam/__main__.py` exposes the Python CLI.
- `requirements.txt` mirrors the dependencies needed for importing FlatCAM Beta
  code headlessly.
- `README.md` has the user-facing quick start.

## FlatCAM Details

The Python writer imports FlatCAM Beta source directly from a source checkout.
It expects files like `camlib.py`, `appParsers/ParseGerber.py`, and
`appParsers/ParseExcellon.py` to exist.

The code creates a small fake FlatCAM app object because the parser classes
expect application globals/signals even when used headlessly.

The output `.FlatPrj` is LZMA-compressed JSON. Serialization uses
`camlib.to_dict` for geometry objects.

Current expected ready-layer names are defined in `GERBER_FILES` and
`EXCELLON_FILES` in `pcb_cam/flatcam_project.py`. Update those lists carefully
if the upstream preparation flow changes exported filenames.

## Development Guidance

- Preserve Windows and Cygwin compatibility. Do not replace `host_path()` with
  plain POSIX-only path logic.
- Keep the FlatCAM GUI out of the normal flow. This project is meant to make
  repeatable headless setup easier.
- Prefer small, testable functions around exporter normalization, FlatCAM object
  creation, and project writing.
- Keep EasyEDA-specific filename assumptions close to the ingestion/prep layer
  so future exporters can be added cleanly.
- If adding support for another EDA tool, normalize its output into the same
  `gerber/ready` contract first, then reuse the FlatCAM writer.
- Treat Carvera manufacturing as the end goal: changes should make it easier to
  reach accurate, repeatable NC output through FlatCAM.

## Verification

There is no formal test suite yet. Useful smoke checks:

```bash
.venv/Scripts/python.exe -m pcb_cam --help
.venv/Scripts/python.exe -m pcb_cam flatprj <PROJECT_DIR_WITH_GERBER_READY>
```

For the full workflow, use:

```bash
ruby scripts/new-project.rb <PROJECT_DIR> <EASYEDA_GERBER_ZIP>
```

After writing a `.FlatPrj`, open it in FlatCAM Beta when possible and confirm
the Gerber and drill objects load with the expected layer names, geometry, and
board outline before proceeding toward NC generation for the Carvera.
