from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from shapely.geometry import LinearRing, Polygon
from shapely.ops import unary_union


@dataclass(frozen=True)
class PaintConfig:
    tool_diameter: float
    tool_type: str
    overlap: float = 20.0
    method: int = 1  # FlatCAM: 0 = Standard, 1 = Seed, 2 = Lines
    connect: bool = True
    contour: bool = True
    offset: float = 0.0


SILKSCREEN_PAINT = PaintConfig(tool_diameter=0.1, tool_type="C1")
SOLDER_MASK_PAINT = PaintConfig(tool_diameter=0.2268, tool_type="V")


def apply_paint_defaults(defaults: dict, config: PaintConfig) -> None:
    """Store the most recently used paint recipe in project preferences."""
    defaults.update(
        {
            "tools_paint_tooldia": config.tool_diameter,
            "tools_paint_tool_type": config.tool_type,
            "tools_paint_overlap": config.overlap,
            "tools_paint_method": config.method,
            "tools_paint_connect": config.connect,
            "tools_paint_contour": config.contour,
            "tools_paint_offset": config.offset,
        }
    )


def _polygons(geometry):
    if geometry is None:
        return
    if isinstance(geometry, Polygon):
        if not geometry.is_empty and geometry.is_valid:
            yield geometry
        return
    if isinstance(geometry, LinearRing):
        polygon = Polygon(geometry)
        if not polygon.is_empty and polygon.is_valid:
            yield polygon
        return
    if hasattr(geometry, "geoms"):
        for item in geometry.geoms:
            yield from _polygons(item)
        return
    try:
        for item in geometry:
            yield from _polygons(item)
    except TypeError:
        return


def _geometry_options(defaults: dict, name: str, config: PaintConfig) -> dict:
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
            "cutz": float(defaults["tools_paint_cutz"]),
            "vtipdia": float(defaults["tools_paint_tipdia"]),
            "vtipangle": float(defaults["tools_paint_tipangle"]),
        }
    )
    return options


def _tool_data(defaults: dict, name: str, config: PaintConfig) -> dict:
    geometry_keys = (
        "plot", "travelz", "feedrate", "feedrate_z", "feedrate_rapid",
        "dwell", "dwelltime", "multidepth", "ppname_g", "depthperpass",
        "extracut", "extracut_length", "toolchange", "toolchangez", "endz",
        "endxy", "spindlespeed", "toolchangexy", "startz", "area_exclusion",
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
            "cutz": float(defaults["tools_paint_cutz"]),
            "vtipdia": float(defaults["tools_paint_tipdia"]),
            "vtipangle": float(defaults["tools_paint_tipangle"]),
            "tooldia": config.tool_diameter,
            "tools_paint_offset": config.offset,
            "tools_paint_method": config.method,
            "tools_paint_selectmethod": 0,
            "tools_paint_connect": config.connect,
            "tools_paint_contour": config.contour,
            "tools_paint_overlap": config.overlap,
            "tools_paint_rest": False,
        }
    )
    return data


def serialize_paint_geometry(
    gerber,
    source_name: str,
    defaults: dict,
    config: PaintConfig,
) -> dict:
    """Paint every polygon in a Gerber using FlatCAM's Seed algorithm."""
    if config.tool_diameter <= 0:
        raise ValueError("Paint tool diameter must be positive")
    if not 0 <= config.overlap < 100:
        raise ValueError("Paint overlap must be between 0 and 100 percent")
    if config.method != 1:
        raise ValueError("The headless paint flow currently supports the Seed method")

    toolpaths = []
    polygon_count = 0
    for polygon in _polygons(gerber.solid_geometry):
        polygon_count += 1
        painted_polygon = polygon.buffer(-config.offset)
        if painted_polygon.is_empty:
            continue
        storage = gerber.clear_polygon2(
            painted_polygon,
            tooldia=config.tool_diameter,
            steps_per_circle=int(defaults["geometry_circle_steps"]),
            overlap=config.overlap / 100.0,
            connect=config.connect,
            contour=config.contour,
            prog_plot=False,
        )
        if storage:
            toolpaths.extend(
                path for path in storage.get_objects()
                if path is not None and not path.is_empty
            )

    if not toolpaths:
        raise RuntimeError(
            f"Paint geometry is empty for {source_name}; processed {polygon_count} polygons"
        )

    name = f"{source_name}_mt_paint"
    solid_geometry = unary_union(toolpaths)
    options = _geometry_options(defaults, name, config)
    xmin, ymin, xmax, ymax = solid_geometry.bounds
    options.update({"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax})

    tool_kind = "Iso" if config.tool_type == "V" else "Rough"
    tool = {
        "tooldia": config.tool_diameter,
        "offset": "Path",
        "offset_value": 0.0,
        "type": tool_kind,
        "tool_type": config.tool_type,
        "data": _tool_data(defaults, name, config),
        "solid_geometry": deepcopy(toolpaths),
    }

    color = defaults.get("geometry_plot_line", "#FF0000")
    return {
        "units": gerber.units,
        "solid_geometry": solid_geometry,
        "follow_geometry": None,
        "tools": {1: tool},
        "kind": "geometry",
        "options": options,
        "multigeo": True,
        "fill_color": color,
        "outline_color": color,
        "alpha_level": "FF",
    }
