#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""What can be checked about this package, with and without code_saturne in the room.

Without it: the EnSight Gold readers, the owner of each boundary face, the mass
flow read from the log, the convergence test, and that a machine with no
code_saturne -- or a part code_saturne cannot pose -- is told so. With it, in
this package's image, the example pipe is solved and compared with
Hagen-Poiseuille.

Run with `pytest` from this directory, beside an installed `partcad` (for the
sandbox helpers the script imports; see `conftest.py`).
"""

import math

import pytest

support = pytest.importorskip("cae_support", reason="needs a partcad with wrappers/cae_support.py")

import cfd_code_saturne  # noqa: E402
import saturne_common as saturne  # noqa: E402

# Two tetrahedra sharing the face (1, 2, 3), and the boundary as its own part
# with its own numbering, the way code_saturne writes them.
GEOMETRY = """results
Output by code_saturne version 9.0.2
node id assign
element id assign
part
         1
Fluid domain
coordinates
         5
0
1
0
0
1
0
0
1
0
1
0
0
0
1
1
tetra4
         2
         1         2         3         4
         2         3         4         5
part
         2
Boundary
coordinates
         3
0
1
0
0
0
1
0
0
0
tria3
         1
         1         2         3
"""


def test_the_geometry_and_a_variable_are_read_part_by_part(tmp_path):
    (tmp_path / "results.geo").write_text(GEOMETRY)
    (tmp_path / "results.velocity.00001").write_text("Velocity\npart\n         1\ntetra4\n1\n2\n0\n0\n0\n0\n")
    parts = saturne.read_geometry(str(tmp_path / "results.geo"))
    assert parts[1][0] == "Fluid domain" and len(parts[1][1]) == 5
    assert parts[1][2]["tetra4"] == [[0, 1, 2, 3], [1, 2, 3, 4]]
    assert parts[2][2]["tria3"] == [[0, 1, 2]]
    velocity = saturne.read_variable(str(tmp_path / "results.velocity.00001"), 3)
    assert velocity[1]["tetra4"] == [(1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]


def test_each_boundary_face_finds_the_cell_that_owns_it(tmp_path):
    (tmp_path / "results.geo").write_text(GEOMETRY)
    parts = saturne.read_geometry(str(tmp_path / "results.geo"))
    fluid, boundary, owners = saturne.boundary_cells(parts)
    assert (fluid, boundary) == (1, 2)
    # The boundary face is (0,0,0), (1,0,0), (0,1,0): a face of the first cell.
    assert owners["tria3"] == [("tetra4", 0)]


def test_the_outflow_is_the_last_balance_code_saturne_printed():
    table = """
   ** BOUNDARY MASS FLOW INFORMATION
Boundary type          Code    Nb faces           Mass flow
Smooth wall               5       11602         0.000000000e+00
Free outlet               3         316         %s
Free inlet               14         316        -1.0e-03
"""
    log = table % "9.0e-04" + table % "1.414646058e-03"
    assert cfd_code_saturne._outflow(log) == pytest.approx(1.414646058e-3)
    with pytest.raises(support.ToolFailed):
        cfd_code_saturne._outflow("nothing printed")


def test_settled_means_the_residuals_fell_far_enough():
    assert cfd_code_saturne._settled([" iteration, velocity, pressure", "1, 8000, 7800", "500, 0.3, 0.06"], 1e-3)
    assert not cfd_code_saturne._settled([" iteration, velocity, pressure", "1, 8000, 7800", "300, 377, 70"], 1e-3)


def test_physical_groups_are_read_by_number(tmp_path):
    msh = tmp_path / "part.msh"
    msh.write_text(
        '$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$PhysicalNames\n2\n2 1 "inlet0"\n2 2 "walls"\n$EndPhysicalNames\n'
    )
    assert saturne.physical_groups(str(msh)) == {"inlet0": 1, "walls": 2}


def test_a_machine_with_no_code_saturne_is_told_what_to_do(monkeypatch):
    monkeypatch.delenv(saturne.LAUNCHER_ENV, raising=False)
    monkeypatch.setattr(saturne.shutil, "which", lambda _name: None)
    monkeypatch.setattr(saturne, "DIRECTORIES", ())
    with pytest.raises(support.ToolMissing) as raised:
        saturne.find_launcher()
    message = str(raised.value)
    assert "dockerImage:" in message and saturne.LAUNCHER_ENV in message


def test_inlets_driven_differently_are_refused_rather_than_solved_wrong():
    request = {
        "boundary": [
            {"load": 1.0, "area": 1.0, "faces": [{"index": 0}]},
            {"load": 2.0, "area": 1.0, "faces": [{"index": 1}]},
            {"outlet": True, "area": 1.0, "faces": [{"index": 2}]},
        ]
    }
    result = cfd_code_saturne.process("/nonexistent.glb", request)
    assert result["success"] is False and "one reservoir" in result["exception"]


def _here():
    try:
        saturne.find_launcher()
        return True
    except support.ToolMissing:
        return False


@pytest.mark.skipif(not _here(), reason="needs code_saturne")
def test_the_example_pipe_reads_hagen_poiseuille(tmp_path):
    build123d = pytest.importorskip("build123d")
    pytest.importorskip("gmsh")

    def disc(z):
        plane = build123d.Plane(origin=(0, 0, z), z_dir=(0, 0, 1))
        return [(plane * build123d.make_face(build123d.Circle(5))).wrapped]

    request = {
        "analysis": "cfd",
        "wrapped": build123d.Cylinder(5, 100).wrapped,
        "boundary": [
            {"port": "inlet", "location": [[0, 0, -50], [0, 0, 1], 0], "sketch": disc(-50), "load": 0.005},
            {"port": "outlet", "location": [[0, 0, 50], [0, 0, 1], 0], "sketch": disc(50), "outlet": True},
        ],
        "density": 900,
        "viscosity": 0.1,
        "mesh_size": 0.008,
    }
    assert support.prepare(request) == []
    result = cfd_code_saturne.process(str(tmp_path / "pipe.glb"), request)
    assert result["success"], result.get("exception")
    flow = float(result["warnings"][0].split("Flow rate ")[1].split(" ")[0])
    expected = 0.005 / (8 * math.pi * 0.1 * 0.1) * math.pi * 0.005**2
    assert flow == pytest.approx(expected, rel=0.02)
