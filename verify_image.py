#!/usr/bin/env python3
#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Whether this image can actually run the code_saturne flow analysis.

Run at build time, so that an image which cannot is never published. The base
image's own `verify.py` checks the contract PartCAD talks to a container
through; this checks what this image adds, the way the analysis uses it: gmsh
meshes a box from *this* interpreter, code_saturne's own model classes write a
setup with a free inlet -- whose head loss is a formula, and so is compiled at
the start of the run -- and `code_saturne run` runs a few steps of it to the
end.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

SETUP = r"""
import sys
sys.path.insert(0, sys.argv[1])
from code_saturne.base import cs_package
from code_saturne.model.Boundary import Boundary
from code_saturne.model.FluidCharacteristicsModel import FluidCharacteristicsModel
from code_saturne.model.LocalizationModel import LocalizationModel, Zone
from code_saturne.model.SolutionDomainModel import SolutionDomainModel
from code_saturne.model.TimeStepModel import TimeStepModel
from code_saturne.model.TurbulenceModel import TurbulenceModel
from code_saturne.model.XMLengine import Case
from code_saturne.model.XMLinitialize import XMLinit
case = Case(package=cs_package.package(), file_name=sys.argv[2])
case["xmlfile"] = sys.argv[2]
case.xmlCleanAllBlank(case.xmlRootNode())
XMLinit(case).initialize()
SolutionDomainModel(case).addMesh(("box.msh", None))
TurbulenceModel(case).setTurbulenceModel("off")
# A viscous oil, so that a pascal drives a slow, laminar flow: air through this
# box at the same drive is fast enough to run away, which is not what is tested.
fluid = FluidCharacteristicsModel(case)
fluid.setInitialValueDensity(900.0)
fluid.setInitialValueViscosity(0.1)
steps = TimeStepModel(case)
steps.setTimePassing(-1)
steps.setStopCriterion("iterations", 3)
zones = LocalizationModel("BoundaryZone", case)
ZONES = (("in", "1", "free_inlet_outlet"), ("out", "2", "imposed_p_outlet"), ("walls", "3", "wall"))
for label, number, nature in ZONES:
    zones.addZone(Zone("BoundaryZone", case=case, label=label, localization=number, nature=nature))
Boundary("free_inlet_outlet", "in", case).setHeadLossesFormula("K = 0.;")
Boundary("imposed_p_outlet", "out", case).setPressureValue(101324.0)
Boundary("wall", "walls", case)
case.xmlSaveDocument()
"""


def main() -> int:
    failures = []
    launcher = shutil.which("code_saturne")
    if not launcher:
        failures.append("code_saturne is not on PATH")
    with tempfile.TemporaryDirectory() as work:
        if not failures:
            subprocess.run([launcher, "create", "-s", "study", "-c", "case"], cwd=work, capture_output=True)
            msh = os.path.join(work, "study", "MESH", "box.msh")
            try:
                import gmsh

                gmsh.initialize()
                try:
                    gmsh.option.setNumber("General.Terminal", 0)
                    gmsh.model.occ.addBox(0, 0, 0, 1, 0.2, 0.2)
                    gmsh.model.occ.synchronize()
                    ends, sides = [], []
                    for _dim, tag in gmsh.model.getEntities(2):
                        x = gmsh.model.occ.getCenterOfMass(2, tag)[0]
                        (ends if abs(x) < 1e-9 or abs(x - 1) < 1e-9 else sides).append((x, tag))
                    ends.sort()
                    gmsh.model.addPhysicalGroup(2, [ends[0][1]], 1, name="in")
                    gmsh.model.addPhysicalGroup(2, [ends[1][1]], 2, name="out")
                    gmsh.model.addPhysicalGroup(2, [tag for _x, tag in sides], 3, name="walls")
                    gmsh.model.addPhysicalGroup(3, [1], 4, name="body")
                    gmsh.option.setNumber("Mesh.MeshSizeMax", 0.1)
                    gmsh.model.mesh.generate(3)
                    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
                    gmsh.write(msh)
                finally:
                    gmsh.finalize()
                print("gmsh meshed a box")
            except Exception as e:
                failures.append("gmsh could not mesh anything from this interpreter (%s)" % e)
        if not failures:
            site = glob.glob("/opt/code_saturne/lib/python3*/site-packages")[0]
            setup = os.path.join(work, "study", "case", "DATA", "setup.xml")
            built = subprocess.run([sys.executable, "-c", SETUP, site, setup], capture_output=True, text=True)
            if built.returncode != 0:
                failures.append("code_saturne's model classes could not write a setup: %s" % built.stderr[-800:])
        if not failures:
            ran = subprocess.run(
                [launcher, "run"], cwd=os.path.join(work, "study", "case", "DATA"), capture_output=True, text=True
            )
            logs = glob.glob(os.path.join(work, "study", "case", "RESU", "*", "run_solver.log"))
            if ran.returncode != 0 or not logs:
                failures.append("code_saturne could not run a case: %s" % (ran.stdout + ran.stderr)[-1200:])
            else:
                print("code_saturne ran a box with a free inlet, its formula compiled")

    if failures:
        print("\nThis image cannot run the code_saturne flow analysis:")
        for failure in failures:
            print("  * %s" % failure)
        return 1
    print("\ncode_saturne and the mesher are both usable from this interpreter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
