from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from shapely.geometry import MultiPolygon, box
from shapely.ops import linemerge, unary_union


@dataclass(frozen=True)
class CutoutConfig:
    tool_diameter: float = 1.1
    cut_z: float = -1.8
    multidepth: bool = True
    depth_per_pass: float = 0.6
    margin: float = 0.1
    gap_size: float = 2.0
    gaps: str = "TB"
    gap_type: str = "bt"  # FlatCAM: Thin
    thin_gap_depth: float = -1.0


BOARD_CUTOUT = CutoutConfig()


def apply_cutout_defaults(defaults: dict, config: CutoutConfig) -> None:
    defaults.update(
        {
            "tools_cutout_tooldia": config.tool_diameter,
            "tools_cutout_kind": "single",
            "tools_cutout_margin": config.margin,
            "tools_cutout_z": config.cut_z,
            "tools_cutout_depthperpass": config.depth_per_pass,
            "tools_cutout_mdepth": config.multidepth,
            "tools_cutout_gapsize": config.gap_size,
            "tools_cutout_gaps_ff": config.gaps,
            "tools_cutout_gap_type": config.gap_type,
            "tools_cutout_gap_depth": config.thin_gap_depth,
        }
    )


def _geometry_options(defaults: dict, name: str, config: CutoutConfig) -> dict:
    options = {
        key.removeprefix("geometry_"): deepcopy(value)
        for key, value in defaults.items()
        if key.startswith("geometry_")
    }
    options.update(
        {
            "name": name,
            "plot": True,
            "cnctooldia": config.tool_diameter,
            "cutz": config.cut_z,
            "multidepth": config.multidepth,
            "depthperpass": config.depth_per_pass,
        }
    )
    return options


def _tool_data(defaults: dict, name: str, config: CutoutConfig, cutz: float) -> dict:
    geometry_keys = (
        "plot", "travelz", "feedrate", "feedrate_z", "feedrate_rapid",
        "dwell", "dwelltime", "ppname_g", "extracut", "extracut_length",
        "toolchange", "toolchangez", "endz", "endxy", "spindlespeed",
        "spindledir", "toolchangexy", "startz", "area_exclusion",
        "area_shape", "area_strategy", "area_overz", "optimization_type",
    )
    data = {
        key: deepcopy(defaults[f"geometry_{key}"])
        for key in geometry_keys
        if f"geometry_{key}" in defaults
    }
    data.update(
        {
            "name": name,
            "cutz": cutz,
            "multidepth": config.multidepth,
            "depthperpass": config.depth_per_pass,
            "vtipdia": float(defaults["geometry_vtipdia"]),
            "vtipangle": float(defaults["geometry_vtipangle"]),
            "tools_cutout_tooldia": config.tool_diameter,
            "tools_cutout_kind": "single",
            "tools_cutout_margin": config.margin,
            "tools_cutout_z": config.cut_z,
            "tools_cutout_depthperpass": config.depth_per_pass,
            "tools_cutout_mdepth": config.multidepth,
            "tools_cutout_gapsize": config.gap_size,
            "tools_cutout_gaps_ff": config.gaps,
            "tools_cutout_gap_type": config.gap_type,
            "tools_cutout_gap_depth": config.thin_gap_depth,
            "tools_cutout_convexshape": defaults["tools_cutout_convexshape"],
            "tools_cutout_big_cursor": defaults["tools_cutout_big_cursor"],
            "tools_cutout_mb_dia": defaults["tools_cutout_mb_dia"],
            "tools_cutout_mb_spacing": defaults["tools_cutout_mb_spacing"],
        }
    )
    return data


def serialize_cutout_geometry(outline, defaults: dict, config: CutoutConfig = BOARD_CUTOUT) -> dict:
    """Create FlatCAM's single-outline Thin/TB cutout geometry headlessly."""
    if config.tool_diameter <= 0 or config.depth_per_pass <= 0:
        raise ValueError("Cutout tool diameter and depth per pass must be positive")
    if config.gaps != "TB" or config.gap_type != "bt":
        raise ValueError("The current cutout recipe requires Thin gaps in TB orientation")

    object_geometry = unary_union(outline.solid_geometry)
    if isinstance(object_geometry, MultiPolygon):
        object_geometry = box(*object_geometry.bounds)

    offset = config.margin + abs(config.tool_diameter / 2.0)
    cut_path = object_geometry.buffer(offset).exterior
    xmin, ymin, xmax, ymax = cut_path.bounds
    center_x = (xmin + xmax) / 2.0

    # FlatCAM expands half the requested material gap by the cutter radius.
    half_gap = config.gap_size / 2.0 + config.tool_diameter / 2.0
    gap_box = box(center_x - half_gap, ymin - half_gap, center_x + half_gap, ymax + half_gap)
    main_geometry = linemerge(cut_path.difference(gap_box))
    thin_geometry = cut_path.intersection(gap_box)
    if main_geometry.is_empty or thin_geometry.is_empty:
        raise RuntimeError("Cutout TB gaps did not intersect the board outline")

    name = "Gerber_BoardOutlineLayer.GKO_cutout"
    main_data = _tool_data(defaults, name, config, config.cut_z)
    thin_data = _tool_data(defaults, name, config, config.thin_gap_depth)
    thin_data["override_color"] = "#29a3a3fa"

    tools = {
        1: {
            "tooldia": config.tool_diameter,
            "offset": "Path",
            "offset_value": 0.0,
            "type": "Rough",
            "tool_type": "C1",
            "data": main_data,
            "solid_geometry": main_geometry,
        },
        9999: {
            "tooldia": config.tool_diameter,
            "offset": "Path",
            "offset_value": 0.0,
            "type": "Rough",
            "tool_type": "C1",
            "data": thin_data,
            "solid_geometry": thin_geometry,
        },
    }

    options = _geometry_options(defaults, name, config)
    options.update({"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax})
    color = defaults.get("geometry_plot_line", "#FF0000")
    return {
        "units": outline.units,
        "solid_geometry": main_geometry,
        "follow_geometry": None,
        "tools": tools,
        "kind": "geometry",
        "options": options,
        "multigeo": True,
        "fill_color": color,
        "outline_color": color,
        "alpha_level": "FF",
    }
