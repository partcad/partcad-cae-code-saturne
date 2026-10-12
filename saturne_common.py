#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Finding code_saturne, setting a case up, running it, and reading what it wrote.

Everything `cfd_code_saturne.py` needs that is about code_saturne rather than
about the flow.

**A case** is code_saturne's directory layout -- a study holding a mesh
directory and a case with `DATA/setup.xml` -- made by `code_saturne create` and
run by `code_saturne run`. The parameters are not written as XML by hand: the
setup file is built through code_saturne's own model classes, the ones its GUI
uses, run by code_saturne's own interpreter. They fill in every default the
solver expects, which a hand-written file would have to know.

**The mesh** comes from gmsh with each condition's faces a physical group.
code_saturne's preprocessor keeps those groups by **number**, not by name, so a
boundary zone selects the number the group was given -- read back from the mesh
file's `$PhysicalNames`.

**The answer** is written as EnSight Gold in its ASCII form, which is read here
with nothing but text parsing: the fluid domain's cells and their velocity and
pressure, and the boundary's faces with the zone each belongs to. A boundary
face's value is its owning cell's.

**Where it is.** The `code_saturne` launcher `PARTCAD_CODE_SATURNE` names, then
on `PATH`, then in the usual install prefixes.
"""

import glob
import math
import os
import platform
import re
import shutil
import subprocess

# The meta-wrapper's directory is on 'sys.path' when PartCAD runs a script, and
# 'cae_support' lives there: what every implementation does around its solver.
import cae_support as support  # noqa: E402

LAUNCHER_ENV = "PARTCAD_CODE_SATURNE"
DIRECTORIES = ("/opt/code_saturne/bin", "/usr/local/bin", "/usr/bin", os.path.expanduser("~/code_saturne/bin"))

ERROR = "error"
WARNING = "warning"
INFO = "info"
M2_PER_MM2 = 1e-6

# code_saturne solves in absolute pressure: a boundary held at "63.66" is held
# at 63.66 Pa absolute, against a reference of an atmosphere -- a near vacuum
# pulling the flow backwards. Every pressure here is relative to this.
REFERENCE_PRESSURE = 101325.0


def find_launcher():
    """The 'code_saturne' launcher to run cases with."""
    named = os.environ.get(LAUNCHER_ENV)
    if named:
        if os.path.isfile(named):
            return named
        raise support.ToolMissing("%s points at something that is not a file: %s" % (LAUNCHER_ENV, named))
    found = shutil.which("code_saturne")
    if found:
        return found
    for directory in DIRECTORIES:
        candidate = os.path.join(directory, "code_saturne")
        if os.path.isfile(candidate):
            return candidate
    raise support.ToolMissing(
        "code_saturne is not available on this %s-%s machine. Two ways to have it: run with a container runtime "
        "available, which gets the image named by 'dockerImage:' in partcad.yaml and carries code_saturne -- or "
        "build it here from https://github.com/code-saturne/code_saturne (the Dockerfile is a working recipe), "
        "or install EDF's salome_meca; then put the code_saturne launcher on PATH, or point %s at it. "
        "Searched: PATH, %s" % (platform.system(), platform.machine(), LAUNCHER_ENV, ", ".join(DIRECTORIES))
    )


def interpreter(launcher):
    """The Python that runs code_saturne, and the directory its package is in."""
    with open(launcher, errors="replace") as f:
        first = f.readline().strip()
    python = first[2:].strip().split()[0] if first.startswith("#!") else shutil.which("python3")
    if python and os.path.basename(python) == "env":
        python = shutil.which(first.split()[-1])
    prefix = os.path.dirname(os.path.dirname(os.path.realpath(launcher)))
    found = glob.glob(os.path.join(prefix, "lib", "python3*", "site-packages"))
    if not python or not found:
        raise support.ToolMissing("code_saturne's own Python could not be found beside %s" % launcher)
    return python, found[0]


def run(work, launcher, *arguments, log):
    """Run the launcher with 'arguments' in 'work'; keep what it printed as 'log'."""
    with open(os.path.join(work, log), "w") as f:
        completed = subprocess.run(
            [launcher, *arguments], cwd=work, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        )
    text = open(os.path.join(work, log), errors="replace").read()
    if completed.returncode != 0:
        tail = "\n".join(text.strip().splitlines()[-20:])
        raise support.ToolFailed("code_saturne %s failed (exit %d):\n%s" % (arguments[0], completed.returncode, tail))
    return text


def physical_groups(msh):
    """Physical group name -> number, from a gmsh mesh file's $PhysicalNames."""
    text = open(msh, errors="replace").read()
    block = re.search(r"\$PhysicalNames\s*\n\d+\n(.*?)\$EndPhysicalNames", text, re.S)
    groups = {}
    for line in (block.group(1) if block else "").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            groups[parts[2].strip().strip('"')] = int(parts[1])
    return groups


# The setup, built by code_saturne's own model classes in its own interpreter.
# Handed a JSON description on the command line: the mesh, the fluid, the
# iterations, and every boundary zone as (label, group number, nature, pressure).
BUILDER = r"""
import json, sys
site, path, description = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
sys.path.insert(0, site)
from code_saturne.base import cs_package
from code_saturne.model.Boundary import Boundary
from code_saturne.model.FluidCharacteristicsModel import FluidCharacteristicsModel
from code_saturne.model.LocalizationModel import LocalizationModel, Zone
from code_saturne.model.OutputControlModel import OutputControlModel
from code_saturne.model.SolutionDomainModel import SolutionDomainModel
from code_saturne.model.TimeStepModel import TimeStepModel
from code_saturne.model.TurbulenceModel import TurbulenceModel
from code_saturne.model.XMLengine import Case
from code_saturne.model.XMLinitialize import XMLinit

case = Case(package=cs_package.package(), file_name=path)
case["xmlfile"] = path
case.xmlCleanAllBlank(case.xmlRootNode())
XMLinit(case).initialize()
SolutionDomainModel(case).addMesh((description["mesh"], None))
TurbulenceModel(case).setTurbulenceModel("off")
fluid = FluidCharacteristicsModel(case)
fluid.setInitialValueDensity(description["density"])
fluid.setInitialValueViscosity(description["viscosity"])
steps = TimeStepModel(case)
steps.setTimePassing(-1)
steps.setStopCriterion("iterations", description["iterations"])
zones = LocalizationModel("BoundaryZone", case)
for label, number, nature, pressure in description["zones"]:
    zones.addZone(Zone("BoundaryZone", case=case, label=label, localization=str(number), nature=nature))
    boundary = Boundary(nature, label, case)
    if pressure is not None:
        boundary.setPressureValue(pressure)
    if nature == "free_inlet_outlet":
        # A reservoir feeding the boundary with no loss. code_saturne asks for
        # the formula whether or not there is a loss, and will not run without.
        boundary.setHeadLossesFormula("K = 0.;")
OutputControlModel(case).setWriterOptions("-1", "text")
case.xmlSaveDocument()
"""


def build_setup(launcher, setup, description):
    """Write 'setup' from a description, through code_saturne's own model classes."""
    import json

    python, site = interpreter(launcher)
    completed = subprocess.run(
        [python, "-c", BUILDER, site, setup, json.dumps(description)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise support.ToolFailed("code_saturne's setup could not be written: %s" % completed.stderr[-800:])


# --------------------------------------------------------------------------- #
# Reading EnSight Gold, ASCII                                                 #
# --------------------------------------------------------------------------- #

# How many points each EnSight element of a 3D fluid domain or its boundary has.
_SIZES = {"tria3": 3, "quad4": 4, "tetra4": 4, "pyramid5": 5, "penta6": 6, "hexa8": 8}

# The faces of each volume element by its points, the way EnSight numbers them.
_CELL_FACES = {
    "tetra4": ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)),
    "pyramid5": ((0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)),
    "penta6": ((0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)),
    "hexa8": ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)),
}


def read_geometry(path):
    """EnSight Gold ASCII geometry: part number -> (name, points, {element type: point lists})."""
    lines = open(path, errors="replace").read().split("\n")
    parts, k = {}, 0
    while k < len(lines):
        if lines[k].strip() != "part":
            k += 1
            continue
        number, name = int(lines[k + 1]), lines[k + 2].strip()
        k += 3
        count = int(lines[k + 1])
        k += 2
        axes = [[float(value) for value in lines[k + axis * count : k + (axis + 1) * count]] for axis in range(3)]
        k += 3 * count
        points, elements = list(zip(*axes)), {}
        while k < len(lines) and lines[k].strip() and lines[k].strip() != "part":
            kind, size = lines[k].strip(), int(lines[k + 1])
            k += 2
            if kind not in _SIZES:
                raise support.ToolFailed("code_saturne wrote '%s' elements, which this does not read" % kind)
            elements[kind] = [[int(value) - 1 for value in lines[k + row].split()] for row in range(size)]
            k += size
        parts[number] = (name, points, elements)
    return parts


def read_variable(path, components):
    """A per-element EnSight variable: part -> element type -> one tuple per element."""
    lines = open(path, errors="replace").read().split("\n")
    found, k = {}, 1
    while k < len(lines):
        if lines[k].strip() != "part":
            k += 1
            continue
        number, kind = int(lines[k + 1]), lines[k + 2].strip()
        k += 3
        values = []
        while k < len(lines) and lines[k].strip() and lines[k].strip() != "part" and lines[k].strip() not in _SIZES:
            values.append(float(lines[k]))
            k += 1
        count = len(values) // components
        found.setdefault(number, {})[kind] = [
            tuple(values[k2 + c * count] for c in range(components)) for k2 in range(count)
        ]
    return found


def latest(directory, name):
    """The newest file of a time series, 'results.velocity.00001' and the like."""
    found = sorted(glob.glob(os.path.join(directory, "%s.*" % name)))
    if not found:
        raise support.ToolFailed("code_saturne wrote no '%s'" % name)
    return found[-1]


def boundary_cells(parts):
    """For each boundary face, the fluid cell that owns it, by element type and index.

    The two parts number their points separately, so the boundary's points are
    found among the fluid's by position, and a face's owner is the cell that
    has a face with the same points.
    """
    fluid = next(number for number, part in parts.items() if "luid" in part[0])
    boundary = next(number for number, part in parts.items() if "oundary" in part[0])
    _name, fluid_points, fluid_elements = parts[fluid]
    _name, points, elements = parts[boundary]
    extent = max(
        max(point[axis] for point in fluid_points) - min(point[axis] for point in fluid_points) for axis in range(3)
    )
    at = support.match_points(points, fluid_points, (extent or 1.0) * 1e-6)
    owner = {}
    for kind, cells in fluid_elements.items():
        for index, cell in enumerate(cells):
            for face in _CELL_FACES.get(kind, ()):
                owner[tuple(sorted(cell[k] for k in face))] = (kind, index)
    owners = {}
    for kind, faces in elements.items():
        owners[kind] = [owner.get(tuple(sorted(at[k] for k in face))) for face in faces]
    return fluid, boundary, owners


def face_area(points, face):
    """A planar polygon's area vector, by fanning it into triangles."""
    total = [0.0, 0.0, 0.0]
    a = points[face[0]]
    for k in range(1, len(face) - 1):
        b, c = points[face[k]], points[face[k + 1]]
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        total[0] += 0.5 * (u[1] * v[2] - u[2] * v[1])
        total[1] += 0.5 * (u[2] * v[0] - u[0] * v[2])
        total[2] += 0.5 * (u[0] * v[1] - u[1] * v[0])
    return total


def norm(vector):
    return math.sqrt(sum(component * component for component in vector))


def nothing_covered(record):
    """The finding for a condition whose port covers no surface of the part."""
    return {
        "severity": WARNING,
        "message": (
            "The port covers no surface of the part, so the condition on it did nothing. Draw its interface "
            "with a sketch that lies on a face, or place the port on the face it means."
        ),
        "where": record.get("port") or record.get("interface_label") or "a port",
    }
