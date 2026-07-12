from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from shapely import affinity
from shapely.geometry import LineString, Point


@dataclass(frozen=True)
class AlignmentConfig:
    drill_diameter: float = 2.0
    # FlatCAM axis names describe the physical line of reflection.  Axis X
    # reflects Y coordinates, matching prepare.rb's mirror-axis=y output.
    axis: str = "X"
    clearance: float = 5.0


PCB_ALIGNMENT = AlignmentConfig()


def apply_alignment_defaults(defaults: dict, config: AlignmentConfig) -> None:
    defaults.update(
        {
            "tools_2sided_axis_loc": "box",
            "tools_2sided_drilldia": config.drill_diameter,
            "tools_2sided_allign_axis": config.axis,
            "tools_2sided_mirror_axis": config.axis,
        }
    )


def alignment_points(outline, config: AlignmentConfig = PCB_ALIGNMENT) -> list[Point]:
    """Return two seed holes and their FlatCAM-style mirrored partners."""
    if config.drill_diameter <= 0:
        raise ValueError("Alignment drill diameter must be positive")
    if config.clearance <= 0:
        raise ValueError("Alignment clearance must be positive")
    if config.axis != "X":
        raise ValueError("The current PCB alignment recipe requires physical flip axis X")

    xmin, ymin, xmax, ymax = outline.bounds()
    center_x = (xmin + xmax) / 2.0
    center_y = (ymin + ymax) / 2.0

    # Both seeds are below the X reflection axis. FlatCAM mirrors their Y
    # coordinates to create the two partners above the board.
    seed_points = (
        Point(xmin - config.clearance, ymin - config.clearance),
        Point(xmax + config.clearance, ymin - config.clearance),
    )
    points = []
    for seed in seed_points:
        mirrored = affinity.scale(seed, 1.0, -1.0, origin=(center_x, center_y))
        points.extend((seed, mirrored))

    # Keep a stable lower-row-then-upper-row drill order in generated NC files.
    return sorted(points, key=lambda point: (point.y, point.x))


def _object_options(defaults: dict, name: str) -> dict:
    options = {
        key.removeprefix("excellon_"): deepcopy(value)
        for key, value in defaults.items()
        if key.startswith("excellon_")
    }
    options.update({"name": name, "plot": True, "solid": True})
    return options


def serialize_alignment_drills(
    outline,
    Excellon,
    defaults: dict,
    config: AlignmentConfig = PCB_ALIGNMENT,
) -> dict:
    """Create the Excellon object produced by FlatCAM's 2-Sided Tool."""
    name = "Alignment Drills"
    points = alignment_points(outline, config)

    obj = Excellon(geo_steps_per_circle=int(defaults["geometry_circle_steps"]))
    obj.default_data = _object_options(defaults, name)
    obj.tools = {
        1: {
            "tooldia": config.drill_diameter,
            "drills": points,
            "solid_geometry": [],
        }
    }
    if obj.create_geometry() == "fail":
        raise RuntimeError("Could not create alignment drill geometry")

    for tool in obj.tools.values():
        tool.setdefault("slots", [])
        tool.setdefault("multicolor", None)

    data = obj.to_dict()
    options = _object_options(defaults, name)
    xmin, ymin, xmax, ymax = obj.bounds()
    options.update({"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax})

    fill_color = defaults.get("excellon_plot_fill", "#C40000bf")
    outline_color = defaults.get("excellon_plot_line", "#750000bf")
    data.update(
        {
            "kind": "excellon",
            "options": options,
            "fill_color": fill_color,
            "outline_color": outline_color,
            "alpha_level": fill_color[-2:] if len(fill_color) >= 2 else "bf",
        }
    )
    return data


def serialize_flip_axis_geometry(alignment_drills: dict, defaults: dict) -> dict:
    """Create a visual, non-machining line through the alignment-hole symmetry axis."""
    tools = alignment_drills.get("tools", {})
    drills = [drill for tool in tools.values() for drill in tool.get("drills", [])]
    if len(drills) < 4:
        raise ValueError("At least four alignment drills are required to show the flip axis")

    xs = [point.x for point in drills]
    ys = [point.y for point in drills]
    center_y = (min(ys) + max(ys)) / 2.0
    axis_line = LineString(((min(xs), center_y), (max(xs), center_y)))

    name = "PCB Flip Axis X (Alignment)"
    options = {
        key.removeprefix("geometry_"): deepcopy(value)
        for key, value in defaults.items()
        if key.startswith("geometry_")
    }
    xmin, ymin, xmax, ymax = axis_line.bounds
    options.update(
        {
            "name": name,
            "plot": True,
            "xmin": xmin,
            "ymin": ymin,
            "xmax": xmax,
            "ymax": ymax,
        }
    )

    color = "#00BFFF"
    return {
        "units": alignment_drills["units"],
        "solid_geometry": [axis_line],
        "follow_geometry": None,
        "tools": {},
        "kind": "geometry",
        "options": options,
        "multigeo": False,
        "fill_color": color,
        "outline_color": color,
        "alpha_level": "FF",
    }
