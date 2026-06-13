from __future__ import annotations

import argparse
import json
import lzma
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path


VERSION = 8.994

GERBER_FILES = [
    "Copper_Stock_100x70.GKO",
    "Gerber_BoardOutlineLayer.GKO",
    "Gerber_TopLayer.GTL",
    "Gerber_BottomLayer.GBL",
    "Gerber_TopSolderMaskLayer.GTS",
    "Gerber_BottomSolderMaskLayer.GBS",
    "Gerber_TopSilkscreenLayer.GTO",
    "Gerber_BottomSilkscreenLayer.GBO",
]

EXCELLON_FILES = [
    "Drill_PTH_Through.DRL",
    "Drill_PTH_Through_Via.DRL",
]


class _Signal:
    def emit(self, *_args, **_kwargs):
        return None


class _ProcContainer:
    new_text = ""

    def update_view_text(self, *_args, **_kwargs):
        return None


class _ShapeCollection:
    def add(self, *_args, **_kwargs):
        return None


class _PlotCanvas:
    def new_shape_collection(self, *_args, **_kwargs):
        return _ShapeCollection()


class _FakeApp:
    def __init__(self, defaults):
        self.defaults = defaults
        self.options = defaults
        self.decimals = int(defaults.get("decimals_metric", 4))
        self.is_legacy = False
        self.abort_flag = False
        self.inform = _Signal()
        self.proc_container = _ProcContainer()
        self.plotcanvas = _PlotCanvas()


def host_path(path: str | os.PathLike[str]) -> Path:
    """Accept Windows, Cygwin /cygdrive/c, and Cygwin /home paths."""
    value = str(path).replace("\\", "/")
    if value.startswith("/"):
        cygpath = Path("C:/cygwin64/bin/cygpath.exe")
        if cygpath.exists():
            result = subprocess.run(
                [str(cygpath), "-w", value],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return Path(result.stdout.strip())
    if value.startswith("/cygdrive/") and len(value) > 10:
        drive = value[10]
        rest = value[11:]
        return Path(f"{drive.upper()}:/{rest}")
    if value.startswith("/home/") and Path("C:/cygwin64").exists():
        return Path("C:/cygwin64") / value.lstrip("/")
    return Path(value)


def default_flatcam_source() -> Path:
    for candidate in (
        Path("C:/cygwin64/home/taf2/flatcam"),
        Path.home() / "flatcam",
    ):
        if (candidate / "appParsers" / "ParseGerber.py").exists():
            return candidate
    return Path("C:/cygwin64/home/taf2/flatcam")


def import_flatcam(flatcam_source: Path):
    source = host_path(flatcam_source).resolve()
    if not (source / "camlib.py").exists():
        raise SystemExit(f"FlatCAM source not found: {source}")

    sys.path.insert(0, str(source))

    import numpy as np

    # FlatCAM Beta uses np.Inf in a few places; NumPy 2 removed the alias.
    if not hasattr(np, "Inf"):
        np.Inf = np.inf

    import camlib
    from appParsers.ParseExcellon import Excellon
    from appParsers.ParseGerber import Gerber
    from defaults import FlatCAMDefaults

    defaults = deepcopy(FlatCAMDefaults.factory_defaults)
    app = _FakeApp(defaults)

    camlib.Geometry.app = app
    Gerber.app = app
    Excellon.app = app

    return camlib, Gerber, Excellon, defaults


def object_options(defaults: dict, prefix: str, name: str) -> dict:
    options = {
        key.removeprefix(prefix): deepcopy(value)
        for key, value in defaults.items()
        if key.startswith(prefix)
    }
    options["name"] = name
    options.setdefault("plot", True)
    options.setdefault("solid", True)
    return options


def color_pair(defaults: dict, index: int) -> tuple[str, str]:
    colors = defaults.get("gerber_color_list") or []
    if index < len(colors):
        line, fill = colors[index]
        return line, fill
    return defaults.get("gerber_plot_line", "#006E20bf"), defaults.get("gerber_plot_fill", "#BBF268bf")


def serialize_gerber(path: Path, index: int, camlib, Gerber, defaults: dict) -> dict:
    obj = Gerber(steps_per_circle=int(defaults["gerber_circle_steps"]))
    obj.parse_file(str(path))
    data = obj.to_dict()

    outline_color, fill_color = color_pair(defaults, index)
    data.update(
        {
            "kind": "gerber",
            "options": object_options(defaults, "gerber_", path.name),
            "fill_color": fill_color,
            "outline_color": outline_color,
            "alpha_level": fill_color[-2:] if len(fill_color) >= 2 else "bf",
        }
    )
    data["options"]["solid"] = True
    return data


def _geometry_count(geometries) -> int:
    if geometries is None:
        return 0
    if isinstance(geometries, list):
        return sum(_geometry_count(item) for item in geometries)
    try:
        return 0 if geometries.is_empty else 1
    except AttributeError:
        return 1


def serialize_excellon(path: Path, camlib, Excellon, defaults: dict) -> dict | None:
    obj = Excellon(geo_steps_per_circle=int(defaults["geometry_circle_steps"]))
    obj.default_data = object_options(defaults, "excellon_", path.name)
    if obj.parse_file(str(path)) == "fail":
        return None
    if obj.create_geometry() == "fail":
        return None
    if _geometry_count(obj.solid_geometry) == 0:
        return None

    for tool in obj.tools.values():
        tool.setdefault("data", deepcopy(obj.default_data))
        tool.setdefault("solid_geometry", [])
        tool.setdefault("drills", [])
        tool.setdefault("slots", [])
        tool.setdefault("multicolor", None)

    data = obj.to_dict()
    fill_color = defaults.get("excellon_plot_fill", "#C40000bf")
    outline_color = defaults.get("excellon_plot_line", "#750000bf")
    data.update(
        {
            "kind": "excellon",
            "options": object_options(defaults, "excellon_", path.name),
            "fill_color": fill_color,
            "outline_color": outline_color,
            "alpha_level": fill_color[-2:] if len(fill_color) >= 2 else "bf",
        }
    )
    data["options"]["solid"] = True
    return data


def build_project(project_dir: Path, flatcam_source: Path) -> dict:
    camlib, Gerber, Excellon, defaults = import_flatcam(flatcam_source)
    ready_dir = project_dir / "gerber" / "ready"
    if not ready_dir.exists():
        raise SystemExit(f"Missing gerber/ready directory: {ready_dir}")

    objects = []
    for name in GERBER_FILES:
        path = ready_dir / name
        if path.exists():
            print(f"gerber: {name}")
            objects.append(serialize_gerber(path, len(objects), camlib, Gerber, defaults))

    for name in EXCELLON_FILES:
        path = ready_dir / name
        if path.exists():
            print(f"excellon: {name}")
            data = serialize_excellon(path, camlib, Excellon, defaults)
            if data is None:
                print(f"  skipped empty/unparseable excellon: {name}")
            else:
                objects.append(data)

    return {
        "objs": objects,
        "options": defaults,
        "version": VERSION,
    }


def write_project(project: dict, output: Path, camlib, compression_level: int = 3) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(project, default=camlib.to_dict, indent=2, sort_keys=True).encode("utf-8")
    with lzma.open(output, "w", preset=compression_level) as file:
        file.write(payload)


def command_flatprj(args: argparse.Namespace) -> int:
    project_dir = host_path(args.project_dir).resolve()
    output = host_path(args.output).resolve() if args.output else project_dir / f"Project_{project_dir.name}_headless.FlatPrj"
    flatcam_source = host_path(args.flatcam_source)

    camlib, _Gerber, _Excellon, defaults = import_flatcam(flatcam_source)
    # Reuse already imported modules/defaults by building with a local helper.
    ready_dir = project_dir / "gerber" / "ready"
    if not ready_dir.exists():
        raise SystemExit(f"Missing gerber/ready directory: {ready_dir}")

    objects = []
    for name in GERBER_FILES:
        path = ready_dir / name
        if path.exists():
            print(f"gerber: {name}")
            objects.append(serialize_gerber(path, len(objects), camlib, _Gerber, defaults))

    for name in EXCELLON_FILES:
        path = ready_dir / name
        if path.exists():
            print(f"excellon: {name}")
            data = serialize_excellon(path, camlib, _Excellon, defaults)
            if data is None:
                print(f"  skipped empty/unparseable excellon: {name}")
            else:
                objects.append(data)

    project = {"objs": objects, "options": defaults, "version": VERSION}
    write_project(project, output, camlib, int(defaults.get("global_compression_level", 3)))
    print(f"wrote: {output}")
    print(f"objects: {len(objects)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-cam")
    sub = parser.add_subparsers(dest="command", required=True)

    flatprj = sub.add_parser("flatprj", help="write a FlatCAM .FlatPrj from gerber/ready")
    flatprj.add_argument("project_dir", help="PCB project directory containing gerber/ready")
    flatprj.add_argument("--output", help="Output .FlatPrj path")
    flatprj.add_argument("--flatcam-source", default=str(default_flatcam_source()), help="FlatCAM Beta source checkout")
    flatprj.set_defaults(func=command_flatprj)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
