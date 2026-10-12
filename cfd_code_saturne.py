#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""`pc cae cfd`, by code_saturne: steady, laminar, incompressible flow through one part.

The part is read as the **fluid volume**. Its `cfd:` keys, applied to the faces
each port covers:

* `load:` names an **inlet**: the flow is drawn in from a reservoir at rest, at
  the pressure that is the inlet's force over the area its port covers;
* `outlet:` names where it **leaves**, held at the reference pressure;
* every other surface is a **wall**, no-slip.

How that is posed in code_saturne matters, and was measured rather than chosen.
An inflow through an imposed-pressure *outlet* -- the obvious reading -- is
ill-posed in code_saturne: on the example pipe it settles on a solution whose
inflow and outflow disagree three to one. What code_saturne offers for an
inflow at a pressure is its free inlet, a reservoir at the reference pressure
feeding the boundary with no loss; so the inlet is that, and the outlet is held
the driving pressure *below* the reference. The difference that leaves is that
the reservoir fixes the inlet's total pressure where the others fix its static
pressure -- for a laminar flow, a dynamic pressure of a fraction of a percent of
the drive.

That reservoir is one, so every inlet is driven at the same pressure. A part
whose inlets carry different forces over their areas is told so rather than
solved wrong.

The steady SIMPLE algorithm, laminar, for `iterations` steps. The flow rate is
code_saturne's own mass flux through the outlets; the model is the boundary
coloured by the pressure in the cell behind each face.
"""

import os
import re
import sys
import tempfile

# `runpy.run_path()` leaves the script's directory off `sys.path`, so the
# sibling module below is not importable without saying where it is.
sys.path.append(os.path.dirname(__file__))
import saturne_common as saturne  # noqa: E402
from saturne_common import WARNING, support  # noqa: E402

TURBULENT_REYNOLDS = 2300.0


def process(path, request):
    try:
        return _analyse(path, request)
    except (support.ToolMissing, support.ToolFailed) as e:
        return support.failed(e)


def _analyse(path, request):
    findings = []
    density = float(request.get("density", 1.204))
    viscosity = float(request.get("viscosity", 1.82e-5))
    iterations = int(request.get("iterations", 500))
    inlets, inlet_records, inlet_empty = support.condition_groups(request, "load", "inlet")
    outlets, outlet_records, outlet_empty = support.condition_groups(request, "outlet", "outlet")
    findings.extend(saturne.nothing_covered(record) for record in inlet_empty + outlet_empty)
    if not inlet_records:
        raise support.ToolFailed("'cfd:' drives nothing: name an inlet under 'load:', on a port that covers a surface")
    if not outlet_records:
        raise support.ToolFailed(
            "'cfd:' names no outlet, so the flow has nowhere to go and the problem has no downstream pressure "
            "reference: name where it leaves under 'outlet:', on a port that covers a surface"
        )
    pressures = [float(r["load"]) / (float(r["area"]) * saturne.M2_PER_MM2) for r in inlet_records]
    drive = pressures[0]
    if any(abs(value - drive) > 1e-6 * max(abs(drive), 1e-12) for value in pressures):
        raise support.ToolFailed(
            "code_saturne draws every inlet from one reservoir, and these inlets are driven at different "
            "pressures (%s): use another implementation, or drive them alike"
            % ", ".join("%.4g Pa" % value for value in pressures)
        )

    launcher = saturne.find_launcher()
    with tempfile.TemporaryDirectory() as work:
        # A directory to keep the study in, for whoever is debugging one by hand.
        work = os.environ.get("PARTCAD_CODE_SATURNE_STUDY") or work
        saturne.run(work, launcher, "create", "-s", "study", "-c", "case", log="log.create")
        case = os.path.join(work, "study", "case")
        msh = os.path.join(work, "study", "MESH", "part.msh")
        mesh = support.mesh(
            request,
            {**inlets, **outlets},
            size=float(request.get("mesh_size", 0.05)),
            order=1,
            rest="walls",
            files=[msh],
        )
        numbers = saturne.physical_groups(msh)
        zones = [[name, numbers[name], "free_inlet_outlet", None] for name in inlets]
        reference = saturne.REFERENCE_PRESSURE
        zones += [[name, numbers[name], "imposed_p_outlet", reference - drive] for name in outlets]
        zones += [["walls", numbers["walls"], "wall", None]] if "walls" in numbers else []
        saturne.build_setup(
            launcher,
            os.path.join(case, "DATA", "setup.xml"),
            {
                "mesh": "part.msh",
                "density": density,
                "viscosity": viscosity,
                "iterations": iterations,
                "zones": zones,
            },
        )
        saturne.run(os.path.join(case, "DATA"), launcher, "run", log="log.run")
        results = sorted(os.listdir(os.path.join(case, "RESU")))[-1]
        directory = os.path.join(case, "RESU", results)
        log = open(os.path.join(directory, "run_solver.log"), errors="replace").read()
        residuals = open(os.path.join(directory, "residuals.csv")).read().strip().splitlines()
        post = os.path.join(directory, "postprocessing")
        parts = saturne.read_geometry(os.path.join(post, "results.geo"))
        velocity = saturne.read_variable(saturne.latest(post, "results.velocity"), 3)
        pressure = saturne.read_variable(saturne.latest(post, "results.pressure"), 1)

    flow = _outflow(log) / density
    fluid, boundary, owners = saturne.boundary_cells(parts)
    speeds = [saturne.norm(value) for values in velocity[fluid].values() for value in values]
    _name, points, elements = parts[boundary]
    vertices, triangles, values = [], [], []
    for kind, faces in elements.items():
        for face, owner in zip(faces, owners[kind]):
            value = pressure[fluid][owner[0]][owner[1]][0] if owner else 0.0
            base = len(vertices)
            vertices.extend(points[k] for k in face)
            values.extend([value] * len(face))
            triangles.extend((base, base + k, base + k + 1) for k in range(1, len(face) - 1))
    support.write_glb(path, vertices, triangles, values)

    outlet_area = sum(float(record["area"]) for record in outlet_records) * saturne.M2_PER_MM2
    mean_speed = flow / outlet_area if outlet_area else 0.0
    peak_speed = max(speeds, default=0.0)
    reynolds = density * abs(mean_speed) * (4.0 * outlet_area / 3.141592653589793) ** 0.5 / viscosity

    settled = _settled(residuals, float(request.get("residual_drop", 1e-3)))
    if not settled:
        findings.append(
            {
                "severity": WARNING,
                "message": "The flow did not settle within %d iterations, so these numbers are not a steady state. "
                "Raise 'iterations'." % iterations,
            }
        )
    if reynolds > TURBULENT_REYNOLDS:
        findings.append(
            {
                "severity": WARNING,
                "message": "The flow is likely turbulent (Reynolds number about %.0f, past %.0f), and this analysis "
                "is laminar." % (reynolds, TURBULENT_REYNOLDS),
            }
        )
    limit = request.get("max_velocity")
    if limit is not None and peak_speed > float(limit):
        findings.append(
            {
                "severity": WARNING,
                "message": "The flow reaches %.3g m/s, past the %.3g m/s it is allowed." % (peak_speed, float(limit)),
                "where": "peak speed",
            }
        )
    minimum = request.get("min_flow_rate")
    if minimum is not None and flow < float(minimum):
        findings.append(
            {
                "severity": WARNING,
                "message": "The part passes %.4g m^3/s, short of the %.4g m^3/s it has to." % (flow, float(minimum)),
                "where": "flow rate",
            }
        )
    return {
        "success": True,
        "findings": findings,
        "warnings": [
            "Flow rate %.4g m^3/s (%.4g L/min), mean outlet speed %.4g m/s, peak speed %.4g m/s, Reynolds number "
            "%.3g, driven by %.4g Pa, over %d elements"
            % (flow, flow * 60000.0, mean_speed, peak_speed, reynolds, drive, mesh.element_count)
        ],
    }


def _outflow(log):
    """The mass flow leaving through the outlets, kg/s, from the last balance code_saturne printed.

    Its table is by boundary type, and an imposed-pressure outlet is a 'Free
    outlet'; the inlets, being free inlets, are counted on a line of their own.
    """
    tables = log.split("BOUNDARY MASS FLOW INFORMATION")
    if len(tables) < 2:
        raise support.ToolFailed("code_saturne printed no mass flow: the run did not finish")
    match = re.search(r"Free outlet\s+\d+\s+\d+\s+([-+0-9.eE]+)", tables[-1])
    if not match:
        raise support.ToolFailed("code_saturne's last mass flow table names no outlet")
    return float(match.group(1))


def _settled(residuals, drop):
    """Whether the residuals fell by 'drop' from the first iteration to the last."""
    rows = [line.split(",") for line in residuals[1:] if line.strip()]
    if len(rows) < 2:
        return False
    first = [float(value) for value in rows[0][1:]]
    last = [float(value) for value in rows[-1][1:]]
    return all(b <= a * drop for a, b in zip(first, last) if a > 0)


# Importable as a module by its tests, and executed by PartCAD's meta-wrapper
# under the run name below -- so nothing here runs on import.
if __name__ == "__partcad_export__":
    pass
