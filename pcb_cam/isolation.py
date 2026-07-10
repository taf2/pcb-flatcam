from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from shapely.geometry import LinearRing, MultiPolygon, Polygon


@dataclass(frozen=True)
class IsolationRoutingConfig:
    tool_diameter: float = 0.2155
    tool_type: str = "V"
    passes: int = 1
    overlap: float = 10.0
    milling_type: str = "cl"
    isolation_type: str = "full"
    combine: bool = True


COPPER_ISOLATION = IsolationRoutingConfig()


def apply_isolation_defaults(defaults: dict, config: IsolationRoutingConfig) -> None:
    """Store the recipe in the FlatCAM project preferences as well as the tool."""
    defaults.update(
        {
            "tools_iso_tooldia": config.tool_diameter,
            "tools_iso_tool_type": config.tool_type,
            "tools_iso_passes": config.passes,
            "tools_iso_overlap": config.overlap,
            "tools_iso_milling_type": config.milling_type,
            "tools_iso_isotype": config.isolation_type,
            "tools_iso_combine_passes": config.combine,
        }
    )


def _reverse_for_climb(geometry):
    """Match ToolIsolation.generate_envelope() direction handling."""
    if isinstance(geometry, MultiPolygon):
        return MultiPolygon(
            [Polygon(poly.exterior.coords[::-1], poly.interiors) for poly in geometry.geoms]
        )
    if isinstance(geometry, Polygon):
        return Polygon(geometry.exterior.coords[::-1], geometry.interiors)
    if isinstance(geometry, LinearRing):
        return Polygon(geometry.coords[::-1])
    return geometry


def _nonempty_parts(geometry) -> list:
    if hasattr(geometry, "geoms") and not isinstance(geometry, Polygon):
        return [part for part in geometry.geoms if not part.is_empty]
    return [] if geometry is None or geometry.is_empty else [geometry]


def _bounds(geometry: list) -> tuple[float, float, float, float]:
    bounds = [item.bounds for item in geometry]
    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def _geometry_options(defaults: dict, name: str, config: IsolationRoutingConfig) -> dict:
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
            "cutz": float(defaults["tools_iso_tool_cutz"]),
            "vtipdia": float(defaults["tools_iso_tool_vtipdia"]),
            "vtipangle": float(defaults["tools_iso_tool_vtipangle"]),
        }
    )
    return options


def _tool_data(defaults: dict, name: str, config: IsolationRoutingConfig) -> dict:
    geometry_keys = (
        "plot", "travelz", "feedrate", "feedrate_z", "feedrate_rapid",
        "multidepth", "ppname_g", "depthperpass", "extracut",
        "extracut_length", "toolchange", "toolchangez", "endz", "endxy",
        "dwell", "dwelltime", "spindlespeed", "spindledir",
        "optimization_type", "search_time", "toolchangexy", "startz",
        "area_exclusion", "area_shape", "area_strategy", "area_overz",
    )
    data = {
        key: deepcopy(defaults[f"geometry_{key}"])
        for key in geometry_keys
        if f"geometry_{key}" in defaults
    }
    data.update(
        {
            "name": name,
            "cutz": float(defaults["tools_iso_tool_cutz"]),
            "vtipdia": float(defaults["tools_iso_tool_vtipdia"]),
            "vtipangle": float(defaults["tools_iso_tool_vtipangle"]),
            "tools_iso_passes": config.passes,
            "tools_iso_overlap": config.overlap,
            "tools_iso_milling_type": config.milling_type,
            "tools_iso_follow": False,
            "tools_iso_isotype": config.isolation_type,
            "tools_iso_rest": False,
            "tools_iso_combine_passes": config.combine,
            "tools_iso_isoexcept": False,
            "tools_iso_selection": 0,
            "tools_iso_poly_ints": False,
            "tools_iso_force": defaults["tools_iso_force"],
            "tools_iso_area_shape": defaults["tools_iso_area_shape"],
        }
    )
    return data


def serialize_isolation_geometry(
    gerber,
    source_name: str,
    defaults: dict,
    config: IsolationRoutingConfig = COPPER_ISOLATION,
) -> dict:
    """Create a FlatCAM geometry object from a parsed Gerber, without the GUI."""
    if config.passes < 1:
        raise ValueError("Isolation routing requires at least one pass")
    if not config.combine:
        raise ValueError("The headless starter flow currently requires combined passes")

    iso_types = {"ext": 0, "int": 1, "full": 2}
    try:
        iso_type = iso_types[config.isolation_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported isolation type: {config.isolation_type}") from exc

    overlap = config.overlap / 100.0
    solid_geometry = []
    for pass_number in range(config.passes):
        offset = (
            config.tool_diameter * ((2 * pass_number + 1) / 2.0000001)
            - (pass_number * overlap * config.tool_diameter)
        )
        envelope = gerber.isolation_geometry(
            offset,
            geometry=gerber.solid_geometry,
            iso_type=iso_type,
            passes=pass_number,
        )
        if envelope == "fail":
            raise RuntimeError(f"Isolation geometry failed for {source_name}")
        if config.milling_type == "cl":
            envelope = _reverse_for_climb(envelope)
        solid_geometry.extend(_nonempty_parts(envelope))

    if not solid_geometry:
        raise RuntimeError(f"Isolation geometry is empty for {source_name}")

    name = f"{source_name}_iso_combined"
    options = _geometry_options(defaults, name, config)
    xmin, ymin, xmax, ymax = _bounds(solid_geometry)
    options.update({"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax})

    tool = {
        "tooldia": config.tool_diameter,
        "offset": "Path",
        "offset_value": 0.0,
        "type": "Iso",
        "tool_type": config.tool_type,
        "data": _tool_data(defaults, name, config),
        "solid_geometry": deepcopy(solid_geometry),
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
