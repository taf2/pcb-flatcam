from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import LineString, LinearRing, Polygon


@dataclass(frozen=True)
class GeometryStep:
    number: int
    source_name: str
    filename: str
    cutz: float


GEOMETRY_STEPS = (
    GeometryStep(1, "Gerber_TopLayer.GTL_iso_combined", "step1-top-iso.nc", -0.1),
    GeometryStep(2, "Gerber_TopSilkscreenLayer.GTO_mt_paint", "step2-top-silk.nc", -0.05),
    GeometryStep(3, "Gerber_TopSolderMaskLayer.GTS_mt_paint", "step3-top-pads.nc", 0.0),
    GeometryStep(5, "Gerber_BottomLayer.GBL_iso_combined", "step5-bot-iso.nc", -0.1),
    GeometryStep(6, "Gerber_BottomSilkscreenLayer.GBO_mt_paint", "step6-bot-silk.nc", -0.05),
    GeometryStep(7, "Gerber_BottomSolderMaskLayer.GBS_mt_paint", "step7-bot-pads.nc", 0.0),
)

ALIGNMENT_STEP = 4
ALIGNMENT_FILENAME = "step4-top-align.nc"
FINAL_STEP = 8
ALIGNMENT_CUT_Z = -5.8
BOARD_DRILL_CUT_Z = -1.85
DRILL_DEPTH_PER_PASS = 0.7
MANAGED_START = "# BEGIN pcb-cam dynamic final jobs"
MANAGED_END = "# END pcb-cam dynamic final jobs"
DEFAULT_DRILL_TOOLS = {
    1: "T6",  # approximately 0.95 mm
    2: "T1",  # 1.1 mm
    3: "T4",  # 3.0 mm
}


def _paths(geometry):
    if geometry is None:
        return
    if isinstance(geometry, Polygon):
        yield LineString(geometry.exterior.coords)
        for interior in geometry.interiors:
            yield LineString(interior.coords)
        return
    if isinstance(geometry, (LineString, LinearRing)):
        if not geometry.is_empty:
            yield LineString(geometry.coords)
        return
    if hasattr(geometry, "geoms"):
        for item in geometry.geoms:
            yield from _paths(item)
        return
    if isinstance(geometry, dict):
        for item in geometry.values():
            yield from _paths(item)
        return
    try:
        for item in geometry:
            yield from _paths(item)
    except TypeError:
        return


def _job_options(defaults: dict, name: str, job_type: str, tooldia: float) -> dict:
    options = {
        key.removeprefix("cncjob_"): deepcopy(value)
        for key, value in defaults.items()
        if key.startswith("cncjob_")
    }
    options.update(
        {
            "name": name,
            "plot": True,
            "tooldia": tooldia,
            "append": "",
            "prepend": "",
            "dwell": False,
            "dwelltime": 1,
            "type": job_type,
        }
    )
    return options


def _header(name: str, source_name: str, job_type: str) -> str:
    return (
        "(G-CODE GENERATED HEADLESSLY BY PCB-CAM)\n"
        f"(Name: {name})\n"
        f"(Source: {source_name})\n"
        f"(Type: {job_type})\n"
        "(Units: MM)\n\n"
    )


def _geometry_gcode(paths: list[LineString], data: dict) -> tuple[str, list[dict]]:
    travelz = float(data["travelz"])
    cutz = float(data["cutz"])
    feedrate = float(data["feedrate"])
    feedrate_z = float(data["feedrate_z"])
    lines = ["G21", "G90", "G94", "M03", f"G01 F{feedrate:.2f}"]
    parsed = []
    previous = (0.0, 0.0)

    for path in paths:
        coordinates = list(path.coords)
        if len(coordinates) < 2:
            continue
        start = coordinates[0]
        lines.extend(
            (
                f"G00 Z{travelz:.4f}",
                f"G00 X{start[0]:.4f} Y{start[1]:.4f}",
                f"G01 Z{cutz:.4f} F{feedrate_z:.2f}",
                f"G01 F{feedrate:.2f}",
            )
        )
        for x, y in coordinates[1:]:
            lines.append(f"G01 X{x:.4f} Y{y:.4f}")
        lines.append(f"G00 Z{travelz:.4f}")

        parsed.append({"geom": LineString((previous, start)), "kind": ["T", "F"]})
        parsed.append({"geom": path, "kind": ["C", "S"]})
        previous = coordinates[-1]

    lines.extend(("M05", "M30"))
    return "\n".join(lines) + "\n", parsed


def _drill_points(excellon: dict) -> list:
    points = []
    for tool in excellon["tools"].values():
        points.extend(deepcopy(tool.get("drills", [])))
        # EasyEDA sometimes emits essentially zero-length G85 slots for round holes.
        # The drilling operation treats each such slot as one drill at its midpoint.
        for start, stop in tool.get("slots", []):
            points.append(
                type(start)(((start.x + stop.x) / 2.0, (start.y + stop.y) / 2.0))
            )
    return points


def _drill_depths(cutz: float, depth_per_pass: float) -> list[float]:
    depths = []
    target = abs(cutz)
    current = depth_per_pass
    while current < target - 1e-9:
        depths.append(-current)
        current += depth_per_pass
    depths.append(cutz)
    return depths


def _drill_gcode(points: list, data: dict) -> tuple[str, list[dict]]:
    travelz = float(data["tools_drill_travelz"])
    cutz = float(data["tools_drill_cutz"])
    feedrate_z = float(data["tools_drill_feedrate_z"])
    multidepth = bool(data["tools_drill_multidepth"])
    depth_per_pass = float(data["tools_drill_depthperpass"])
    depths = _drill_depths(cutz, depth_per_pass) if multidepth else [cutz]
    lines = ["G21", "G90", "G94", "M03", f"G01 F{feedrate_z:.2f}"]
    parsed = []
    previous = (0.0, 0.0)
    for point in points:
        current = (point.x, point.y)
        lines.extend((f"G00 Z{travelz:.4f}", f"G00 X{point.x:.4f} Y{point.y:.4f}"))
        for depth in depths:
            lines.extend(
                (
                    f"G01 Z{depth:.4f} F{feedrate_z:.2f}",
                    "G01 Z0.0000",
                    f"G00 Z{travelz:.4f}",
                )
            )
        parsed.append({"geom": LineString((previous, current)), "kind": ["T", "F"]})
        previous = current
    lines.extend(("M05", "M30"))
    return "\n".join(lines) + "\n", parsed


def _base_job(
    name: str,
    source_name: str,
    job_type: str,
    tooldia: float,
    gcode: str,
    parsed: list,
    solid_geometry,
    bounds: tuple,
    defaults: dict,
    z_cut: float,
    z_move: float,
    feedrate: float,
    feedrate_z: float,
    feedrate_rapid: float,
    depth_per_cut: float | None = None,
) -> dict:
    header = _header(name, source_name, job_type)
    options = _job_options(defaults, name, job_type, tooldia)
    options.update(dict(zip(("xmin", "ymin", "xmax", "ymax"), bounds)))
    return {
        "units": "MM",
        "solid_geometry": solid_geometry,
        "follow_geometry": None,
        "tools": {},
        "kind": "cncjob",
        "z_cut": z_cut,
        "z_move": z_move,
        "z_toolchange": float(defaults["geometry_toolchangez"]),
        "feedrate": feedrate,
        "z_feedrate": feedrate_z,
        "feedrate_rapid": feedrate_rapid,
        "tooldia": tooldia,
        "gcode": gcode,
        "input_geometry_bounds": bounds,
        "gcode_parsed": parsed,
        "steps_per_circle": int(defaults["cncjob_steps_per_circle"]),
        "z_depthpercut": abs(depth_per_cut if depth_per_cut is not None else z_cut),
        "spindlespeed": 0,
        "dwell": False,
        "dwelltime": 1,
        "options": options,
        "origin_kind": None if job_type == "Geometry" else "excellon",
        "cnc_tools": {},
        "exc_cnc_tools": {},
        "multitool": True,
        "append_snippet": "",
        "prepend_snippet": "",
        "gc_header": header,
    }


def geometry_cnc_job(source: dict, step: GeometryStep, defaults: dict) -> tuple[dict, str]:
    tool = next(iter(source["tools"].values()))
    data = deepcopy(tool["data"])
    data["cutz"] = step.cutz
    paths = list(_paths(tool["solid_geometry"]))
    gcode, parsed = _geometry_gcode(paths, data)
    bounds = tuple(source["options"][key] for key in ("xmin", "ymin", "xmax", "ymax"))
    name = f"step{step.number}"
    job = _base_job(
        name, source["options"]["name"], "Geometry", float(tool["tooldia"]),
        gcode, parsed, paths, bounds, defaults,
        float(data["cutz"]), float(data["travelz"]), float(data["feedrate"]),
        float(data["feedrate_z"]), float(data["feedrate_rapid"]),
    )
    job["cnc_tools"] = {
        1: {
            "tooldia": float(tool["tooldia"]),
            "offset": tool["offset"],
            "offset_value": tool["offset_value"],
            "type": tool["type"],
            "tool_type": tool["tool_type"],
            "data": data,
            "gcode": gcode,
            "gcode_parsed": parsed,
            "solid_geometry": paths,
        }
    }
    return job, job["gc_header"] + gcode


def cutout_cnc_job(
    source: dict,
    step_number: int,
    filename: str,
    defaults: dict,
) -> tuple[dict, str]:
    """Generate the full-depth outline plus partially cut Thin support gaps."""
    ordered_tools = sorted(source["tools"].items(), key=lambda item: int(item[0]))
    first_tool = ordered_tools[0][1]
    travelz = float(first_tool["data"]["travelz"])
    feedrate = float(first_tool["data"]["feedrate"])
    feedrate_z = float(first_tool["data"]["feedrate_z"])
    feedrate_rapid = float(first_tool["data"]["feedrate_rapid"])
    lines = ["G21", "G90", "G94", "M03", f"G01 F{feedrate:.2f}"]
    all_parsed = []
    cnc_tools = {}
    previous = (0.0, 0.0)

    for uid, tool in ordered_tools:
        data = deepcopy(tool["data"])
        paths = list(_paths(tool["solid_geometry"]))
        depths = _drill_depths(float(data["cutz"]), float(data["depthperpass"]))
        tool_lines = []
        tool_parsed = []
        for path in paths:
            coordinates = list(path.coords)
            if len(coordinates) < 2:
                continue
            start = coordinates[0]
            for depth in depths:
                path_lines = [
                    f"G00 Z{travelz:.4f}",
                    f"G00 X{start[0]:.4f} Y{start[1]:.4f}",
                    f"G01 Z{depth:.4f} F{feedrate_z:.2f}",
                    f"G01 F{feedrate:.2f}",
                ]
                path_lines.extend(f"G01 X{x:.4f} Y{y:.4f}" for x, y in coordinates[1:])
                path_lines.append(f"G00 Z{travelz:.4f}")
                lines.extend(path_lines)
                tool_lines.extend(path_lines)
            travel = {"geom": LineString((previous, start)), "kind": ["T", "F"]}
            cut = {"geom": path, "kind": ["C", "S"]}
            all_parsed.extend((travel, cut))
            tool_parsed.extend((travel, cut))
            previous = coordinates[-1]

        cnc_tools[uid] = {
            "tooldia": float(tool["tooldia"]),
            "offset": tool["offset"],
            "offset_value": tool["offset_value"],
            "type": tool["type"],
            "tool_type": tool["tool_type"],
            "data": data,
            "gcode": "\n".join(tool_lines) + "\n",
            "gcode_parsed": tool_parsed,
            "solid_geometry": deepcopy(tool["solid_geometry"]),
        }

    lines.extend(("M05", "M30"))
    gcode = "\n".join(lines) + "\n"
    bounds = tuple(source["options"][key] for key in ("xmin", "ymin", "xmax", "ymax"))
    name = f"step{step_number}"
    job = _base_job(
        name, source["options"]["name"], "Geometry", float(first_tool["tooldia"]),
        gcode, all_parsed, source["solid_geometry"], bounds, defaults,
        float(first_tool["data"]["cutz"]), travelz, feedrate, feedrate_z, feedrate_rapid,
        depth_per_cut=float(first_tool["data"]["depthperpass"]),
    )
    job["cnc_tools"] = cnc_tools
    return job, job["gc_header"] + gcode


def drill_cnc_job(
    source: dict,
    step_number: int,
    filename: str,
    defaults: dict,
    cutz: float | None = None,
) -> tuple[dict, str]:
    source_tool = next(iter(source["tools"].values()))
    diameter = float(source_tool["tooldia"])
    points = _drill_points(source)
    data = deepcopy(source_tool.get("data") or source["options"])
    data.update(
        {
            key: deepcopy(value)
            for key, value in defaults.items()
            if key.startswith("tools_drill_")
        }
    )
    data["name"] = source["options"]["name"]
    data["tools_drill_cutz"] = float(cutz if cutz is not None else defaults["tools_drill_cutz"])
    data["tools_drill_multidepth"] = True
    data["tools_drill_depthperpass"] = DRILL_DEPTH_PER_PASS
    gcode, parsed = _drill_gcode(points, data)
    bounds = tuple(source["options"][key] for key in ("xmin", "ymin", "xmax", "ymax"))
    name = f"step{step_number}"
    job = _base_job(
        name, source["options"]["name"], "Excellon", diameter,
        gcode, parsed, source["solid_geometry"], bounds, defaults,
        float(data["tools_drill_cutz"]), float(data["tools_drill_travelz"]),
        float(data["tools_drill_feedrate_z"]), float(data["tools_drill_feedrate_z"]),
        float(data["tools_drill_feedrate_rapid"]),
        depth_per_cut=float(data["tools_drill_depthperpass"]),
    )
    job["options"]["ppname_e"] = data["tools_drill_ppname_e"]
    job["exc_cnc_tools"] = {
        diameter: {
            "tool": 1,
            "nr_drills": len(points),
            "nr_slots": 0,
            "offset": 0,
            "data": data,
            "gcode": gcode,
            "gcode_parsed": parsed,
            "solid_geometry": deepcopy(source["solid_geometry"]),
        }
    }
    return job, job["gc_header"] + gcode


def _existing_drill_mappings(script: str) -> dict[int, str]:
    mappings = {}
    pattern = re.compile(r'^DRILL(\d+)_TOOL="\$\{DRILL\d+_TOOL:-([^}]+)\}"', re.MULTILINE)
    for match in pattern.finditer(script):
        mappings[int(match.group(1))] = match.group(2)
    return mappings


def _existing_powershell_drill_mappings(script: str) -> dict[int, str]:
    mappings = {}
    pattern = re.compile(
        r'^\$Drill(\d+)Tool\s*=.*?else\s*\{\s*"([^"]+)"\s*\}',
        re.MULTILINE,
    )
    for match in pattern.finditer(script):
        mappings[int(match.group(1))] = match.group(2)
    return mappings


def _default_script_prefix() -> str:
    return '''#!/usr/bin/env bash
set -euo pipefail

RB="${RB:-ruby}"
LASER_X_OFFSET="${LASER_X_OFFSET:--0.5}"
LASER_Y_OFFSET="${LASER_Y_OFFSET:-0.3}"
TOP_PAD_DEPTH_RULE="${TOP_PAD_DEPTH_RULE:-0.1366=0.012}"
BOTTOM_PAD_DEPTH_RULE="${BOTTOM_PAD_DEPTH_RULE:-0.1366=0.012}"

mkdir -p cut

# T1: 1.0 mm endmill, large holes and final cutout
# T2: 0.1 mm / 60 deg V-bit, isolation
# T3: 2.0 mm endmill, alignment peg holes
# T5: 0.3 mm / 30 deg V-bit or spring bit, solder mask pad clearing
# T6: 0.5 mm endmill, vias/small holes

"$RB" scripts/post-makera.rb flatcam/step1-top-iso.nc T2
[[ -f flatcam/step2-top-silk.nc ]] && "$RB" scripts/post-laser-makera.rb flatcam/step2-top-silk.nc 10 "$LASER_X_OFFSET" "$LASER_Y_OFFSET" 150
[[ -f flatcam/step3-top-pads.nc ]] && "$RB" scripts/post-makera.rb flatcam/step3-top-pads.nc T5 "$TOP_PAD_DEPTH_RULE"
"$RB" scripts/post-makera.rb flatcam/step4-top-align.nc T3

# Flip the board across the X alignment axis before step 5.
"$RB" scripts/post-makera.rb flatcam/step5-bot-iso.nc T2
[[ -f flatcam/step6-bot-silk.nc ]] && "$RB" scripts/post-laser-makera.rb flatcam/step6-bot-silk.nc 10 "$LASER_X_OFFSET" "$LASER_Y_OFFSET" 150
[[ -f flatcam/step7-bot-pads.nc ]] && "$RB" scripts/post-makera.rb flatcam/step7-bot-pads.nc T5 "$BOTTOM_PAD_DEPTH_RULE"

'''


def write_gen_all_script(
    project_dir: Path,
    drill_jobs: list[dict],
    cutout_diameter: float,
) -> Path:
    script_path = project_dir / "scripts" / "gen.all.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    existing = script_path.read_text(encoding="utf-8") if script_path.exists() else ""
    mappings = _existing_drill_mappings(existing)

    if MANAGED_START in existing:
        prefix = existing.split(MANAGED_START, 1)[0]
    elif "CUTOUT_FILE=" in existing:
        prefix = existing.split("CUTOUT_FILE=", 1)[0]
    else:
        prefix = existing.rstrip() + "\n\n" if existing.strip() else _default_script_prefix()
    prefix = prefix.replace(
        "# T1: 1.0 mm endmill, large holes and final cutout",
        "# T1: 1.1 mm drill/cutter, large holes and final cutout",
    )
    prefix = prefix.replace(
        "# T1: 1.0 mm endmill, large holes",
        "# T1: 1.1 mm drill/cutter, large holes and final cutout",
    )
    prefix = prefix.replace(
        "# T6: 0.5 mm endmill, vias/small holes",
        "# T6: approximately 0.95 mm drill, vias/small holes",
    )
    prefix = prefix.replace(
        "# Flip the board across the Y alignment axis before step 5.",
        "# Flip the board across the X alignment axis before step 5.",
    )
    if "# T4: 3.0 mm drill, large holes" not in prefix:
        prefix = prefix.replace(
            "# T3: 2.0 mm endmill, alignment peg holes\n",
            "# T3: 2.0 mm endmill, alignment peg holes\n# T4: 3.0 mm drill, large holes\n",
        )

    matching_cutout_drill = next(
        (
            drill
            for drill in drill_jobs
            if abs(drill["diameter"] - cutout_diameter) < 0.0005
        ),
        None,
    )
    cutout_tool_default = (
        f"$DRILL{matching_cutout_drill['index']}_TOOL"
        if matching_cutout_drill is not None
        else "UNMAPPED"
    )

    lines = [
        MANAGED_START,
        "# Review these Carvera tool-slot mappings before running the final combine.",
        "# Defaults: Drill1=T6 (~0.95 mm), Drill2=T1 (1.1 mm), Drill3=T4 (3.0 mm).",
        "# Additional drill groups remain UNMAPPED until a tool slot is assigned.",
    ]
    for drill in drill_jobs:
        index = drill["index"]
        existing_mapping = mappings.get(index)
        mapping = (
            DEFAULT_DRILL_TOOLS.get(index, "UNMAPPED")
            if existing_mapping in (None, "UNMAPPED")
            else existing_mapping
        )
        lines.append(
            f'DRILL{index}_TOOL="${{DRILL{index}_TOOL:-{mapping}}}"  # {drill["diameter"]:.3f} mm'
        )

    lines.extend(("", "DRILL_JOBS=("))
    for drill in drill_jobs:
        lines.append(
            f'  "flatcam/{drill["filename"]}:${{DRILL{drill["index"]}_TOOL}}"'
        )
    lines.extend(
        (
            ")",
            "",
            'for spec in "${DRILL_JOBS[@]}"; do',
            '  if [[ "$spec" == *:UNMAPPED ]]; then',
            '    echo "ERROR: Set every DRILLn_TOOL mapping in scripts/gen.all.sh before combining." >&2',
            "    exit 1",
            "  fi",
            "done",
            "",
            'FINAL_JOBS=("${DRILL_JOBS[@]}")',
            f'CUTOUT_FILE="flatcam/step{FINAL_STEP + len(drill_jobs)}-cutout.nc"',
            'if [[ ! -f "$CUTOUT_FILE" && -f flatcam/cutout.nc ]]; then',
            '  CUTOUT_FILE="flatcam/cutout.nc"',
            "fi",
            'if [[ -f "$CUTOUT_FILE" ]]; then',
            f'  CUTOUT_TOOL="${{CUTOUT_TOOL:-{cutout_tool_default}}}"  # {cutout_diameter:.3f} mm',
            '  if [[ "$CUTOUT_TOOL" == "UNMAPPED" ]]; then',
            '    echo "ERROR: Set CUTOUT_TOOL for the cutout cutter before combining." >&2',
            "    exit 1",
            "  fi",
            '  FINAL_JOBS+=("${CUTOUT_FILE}:${CUTOUT_TOOL}")',
            "fi",
            "",
            'if (( ${#FINAL_JOBS[@]} )); then',
            f'  "$RB" scripts/combine.rb cut/step{FINAL_STEP}-final.nc "${{FINAL_JOBS[@]}}"',
            "fi",
            MANAGED_END,
            "",
        )
    )
    script_path.write_text(prefix + "\n".join(lines), encoding="utf-8", newline="\n")
    return script_path


def _default_powershell_prefix() -> str:
    return '''param(
  [string]$Ruby = "ruby",
  [string]$LaserXOffset = "-0.5",
  [string]$LaserYOffset = "0.3",
  [string]$TopPadDepthRule = "0.1366=0.012",
  [string]$BottomPadDepthRule = "0.1366=0.012"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path "cut" | Out-Null

# T1: 1.1 mm drill/cutter, large holes and final cutout
# T2: 0.1 mm / 60 deg V-bit, isolation
# T3: 2.0 mm endmill, alignment peg holes
# T4: 3.0 mm drill, large holes
# T5: 0.3 mm / 30 deg V-bit or spring bit, solder mask pad clearing
# T6: approximately 0.95 mm drill, vias/small holes

& $Ruby scripts/post-makera.rb flatcam/step1-top-iso.nc T2
if (Test-Path flatcam/step2-top-pads.nc) {
  & $Ruby scripts/post-laser-makera.rb flatcam/step2-top-pads.nc 70 $LaserXOffset $LaserYOffset 900
}
if (Test-Path flatcam/step2-top-silk.nc) {
  & $Ruby scripts/post-laser-makera.rb flatcam/step2-top-silk.nc 10 $LaserXOffset $LaserYOffset 150
}
if (Test-Path flatcam/step3-top-pads.nc) {
  & $Ruby scripts/post-makera.rb flatcam/step3-top-pads.nc T5 $TopPadDepthRule
}
& $Ruby scripts/post-makera.rb flatcam/step4-top-align.nc T3

# Flip the board across the X alignment axis before step 5.
& $Ruby scripts/post-makera.rb flatcam/step5-bot-iso.nc T2
if (Test-Path flatcam/step6-bot-pads.nc) {
  & $Ruby scripts/post-laser-makera.rb flatcam/step6-bot-pads.nc 70 $LaserXOffset $LaserYOffset 900
}
if (Test-Path flatcam/step6-bot-silk.nc) {
  & $Ruby scripts/post-laser-makera.rb flatcam/step6-bot-silk.nc 10 $LaserXOffset $LaserYOffset 150
}
if (Test-Path flatcam/step7-bot-pads.nc) {
  & $Ruby scripts/post-makera.rb flatcam/step7-bot-pads.nc T5 $BottomPadDepthRule
}

'''


def write_gen_all_powershell_script(
    project_dir: Path,
    drill_jobs: list[dict],
    cutout_diameter: float,
    mappings: dict[int, str],
) -> Path:
    """Write the native Windows equivalent of the generated Bash finalizer."""
    script_path = project_dir / "scripts" / "gen.all.ps1"
    existing = script_path.read_text(encoding="utf-8-sig") if script_path.exists() else ""
    if MANAGED_START in existing:
        prefix = existing.split(MANAGED_START, 1)[0]
    elif re.search(r"(?m)^# Final combined operation\.", existing):
        prefix = re.split(r"(?m)^# Final combined operation\.", existing, maxsplit=1)[0]
    else:
        prefix = existing.rstrip() + "\n\n" if existing.strip() else _default_powershell_prefix()

    prefix = prefix.replace(
        "# Flip the board across the Y alignment axis before step 5.",
        "# Flip the board across the X alignment axis before step 5.",
    )

    prefix = prefix.replace(
        "# T1: 1.0 mm endmill, large holes and final cutout",
        "# T1: 1.1 mm drill/cutter, large holes and final cutout",
    ).replace(
        "# T6: 0.5 mm endmill, vias/small holes",
        "# T6: approximately 0.95 mm drill, vias/small holes",
    )
    if "# T4: 3.0 mm drill, large holes" not in prefix:
        prefix = prefix.replace(
            "# T3: 2.0 mm endmill, alignment peg holes\n",
            "# T3: 2.0 mm endmill, alignment peg holes\n# T4: 3.0 mm drill, large holes\n",
        )

    # PowerShell 5 does not make a non-zero native-process exit fatal under
    # ErrorActionPreference. Route every Ruby call through an explicit check.
    # Normalize helpers written by older runs before replacing Ruby command
    # lines; otherwise refreshing a project makes Invoke-Ruby call itself.
    prefix = prefix.replace(
        "function Invoke-Ruby {\n  Invoke-Ruby @Args",
        "function Invoke-Ruby {\n  & ($Ruby) @Args",
    ).replace(
        "function Invoke-Ruby {\n  & $Ruby @Args",
        "function Invoke-Ruby {\n  & ($Ruby) @Args",
    )
    prefix = prefix.replace("& $Ruby ", "Invoke-Ruby ")
    if "function Invoke-Ruby" not in prefix:
        helper = '''$ErrorActionPreference = "Stop"

function Invoke-Ruby {
  & ($Ruby) @Args
  if ($LASTEXITCODE -ne 0) {
    throw "Ruby command failed with exit code $LASTEXITCODE"
  }
}'''
        prefix = prefix.replace('$ErrorActionPreference = "Stop"', helper, 1)

    matching_cutout_drill = next(
        (drill for drill in drill_jobs if abs(drill["diameter"] - cutout_diameter) < 0.0005),
        None,
    )
    cutout_tool_default = (
        f"$Drill{matching_cutout_drill['index']}Tool"
        if matching_cutout_drill is not None
        else '"UNMAPPED"'
    )

    lines = [
        MANAGED_START,
        "# Review these Carvera tool-slot mappings before running the final combine.",
        "# Environment overrides such as DRILL1_TOOL and CUTOUT_TOOL are supported.",
    ]
    for drill in drill_jobs:
        index = drill["index"]
        mapping = mappings.get(index, DEFAULT_DRILL_TOOLS.get(index, "UNMAPPED"))
        lines.append(
            f'$Drill{index}Tool = if ($env:DRILL{index}_TOOL) {{ $env:DRILL{index}_TOOL }} else {{ "{mapping}" }}  # {drill["diameter"]:.3f} mm'
        )

    lines.extend(("", "$drillJobs = @("))
    for drill in drill_jobs:
        lines.append(f'  "flatcam/{drill["filename"]}:${{Drill{drill["index"]}Tool}}"')
    lines.extend(
        (
            ")",
            "",
            "foreach ($spec in $drillJobs) {",
            '  if ($spec.EndsWith(":UNMAPPED", [System.StringComparison]::OrdinalIgnoreCase)) {',
            '    throw "Set every DRILLn_TOOL mapping in scripts/gen.all.ps1 before combining."',
            "  }",
            "}",
            "",
            "$finalJobs = @($drillJobs)",
            f'$cutoutFile = "flatcam/step{FINAL_STEP + len(drill_jobs)}-cutout.nc"',
            'if (-not (Test-Path $cutoutFile) -and (Test-Path "flatcam/cutout.nc")) {',
            '  $cutoutFile = "flatcam/cutout.nc"',
            "}",
            "if (Test-Path $cutoutFile) {",
            f'  $cutoutTool = if ($env:CUTOUT_TOOL) {{ $env:CUTOUT_TOOL }} else {{ {cutout_tool_default} }}  # {cutout_diameter:.3f} mm',
            '  if ($cutoutTool -eq "UNMAPPED") {',
            '    throw "Set CUTOUT_TOOL for the cutout cutter before combining."',
            "  }",
            '  $finalJobs += "${cutoutFile}:${cutoutTool}"',
            "}",
            "",
            "if ($finalJobs.Count -gt 0) {",
            f'  Invoke-Ruby scripts/combine.rb cut/step{FINAL_STEP}-final.nc @finalJobs',
            "}",
            MANAGED_END,
            "",
        )
    )
    script_path.write_text(prefix + "\n".join(lines), encoding="utf-8", newline="\n")
    return script_path


def write_gen_all_scripts(
    project_dir: Path,
    drill_jobs: list[dict],
    cutout_diameter: float,
) -> tuple[Path, Path]:
    powershell_path = project_dir / "scripts" / "gen.all.ps1"
    existing_powershell = (
        powershell_path.read_text(encoding="utf-8-sig")
        if powershell_path.exists()
        else ""
    )
    powershell_mappings = _existing_powershell_drill_mappings(existing_powershell)
    shell_path = write_gen_all_script(project_dir, drill_jobs, cutout_diameter)
    mappings = _existing_drill_mappings(shell_path.read_text(encoding="utf-8"))
    mappings.update(powershell_mappings)
    powershell_path = write_gen_all_powershell_script(
        project_dir, drill_jobs, cutout_diameter, mappings
    )
    return shell_path, powershell_path


def generate_cnc_jobs(objects: list[dict], project_dir: Path, defaults: dict) -> list[dict]:
    """Generate CNCJob objects, raw NC files, and Windows/Unix finalizers."""
    by_name = {obj.get("options", {}).get("name"): obj for obj in objects}
    output_dir = project_dir / "flatcam"
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_drill_job in output_dir.glob("step*-drill*.nc"):
        stale_drill_job.unlink()
    for stale_cutout_job in output_dir.glob("step*-cutout.nc"):
        stale_cutout_job.unlink()
    jobs = []

    for step in GEOMETRY_STEPS:
        source = by_name.get(step.source_name)
        if source is None:
            raise RuntimeError(f"Missing geometry for CNC step {step.number}: {step.source_name}")
        job, nc = geometry_cnc_job(source, step, defaults)
        (output_dir / step.filename).write_text(nc, encoding="ascii", newline="\n")
        jobs.append(job)
        print(f"cnc: step{step.number} {step.filename} source={step.source_name}")

    alignment = by_name.get("Alignment Drills")
    if alignment is None:
        raise RuntimeError("Missing Alignment Drills for CNC step 4")
    alignment_job, alignment_nc = drill_cnc_job(
        alignment, ALIGNMENT_STEP, ALIGNMENT_FILENAME, defaults, cutz=ALIGNMENT_CUT_Z
    )
    (output_dir / ALIGNMENT_FILENAME).write_text(alignment_nc, encoding="ascii", newline="\n")
    jobs.append(alignment_job)
    print(f"cnc: step{ALIGNMENT_STEP} {ALIGNMENT_FILENAME} source=Alignment Drills")

    # Store CNCJob objects in numerical order even though step 4 is generated after geometry setup.
    jobs.sort(key=lambda job: int(job["options"]["name"].removeprefix("step")))

    drill_sources = []
    pattern = re.compile(r"^Drill(\d+)\.DRL$")
    for name, source in by_name.items():
        match = pattern.match(name or "")
        if match:
            drill_sources.append((int(match.group(1)), source))
    drill_sources.sort()

    drill_jobs = []
    for offset, (index, source) in enumerate(drill_sources):
        step_number = FINAL_STEP + offset
        filename = f"step{step_number}-drill{index}.nc"
        job, nc = drill_cnc_job(source, step_number, filename, defaults, cutz=BOARD_DRILL_CUT_Z)
        (output_dir / filename).write_text(nc, encoding="ascii", newline="\n")
        jobs.append(job)
        diameter = float(next(iter(source["tools"].values()))["tooldia"])
        drill_jobs.append({"index": index, "filename": filename, "diameter": diameter})
        print(f"cnc: step{step_number} {filename} source=Drill{index}.DRL dia={diameter:.3f}")

    cutout = by_name.get("Gerber_BoardOutlineLayer.GKO_cutout")
    if cutout is None:
        raise RuntimeError("Missing board cutout geometry")
    cutout_step = FINAL_STEP + len(drill_sources)
    cutout_filename = f"step{cutout_step}-cutout.nc"
    cutout_job, cutout_nc = cutout_cnc_job(cutout, cutout_step, cutout_filename, defaults)
    (output_dir / cutout_filename).write_text(cutout_nc, encoding="ascii", newline="\n")
    jobs.append(cutout_job)
    print(
        f"cnc: step{cutout_step} {cutout_filename} "
        "source=Gerber_BoardOutlineLayer.GKO_cutout"
    )

    cutout_diameter = float(next(iter(cutout["tools"].values()))["tooldia"])
    shell_path, powershell_path = write_gen_all_scripts(project_dir, drill_jobs, cutout_diameter)
    print(f"script: {shell_path}")
    print(f"script: {powershell_path}")
    return jobs
