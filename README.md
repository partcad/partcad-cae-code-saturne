# partcad-cae-code-saturne

Steady, laminar, incompressible flow analysis for [PartCAD](https://github.com/partcad/partcad), by
[code_saturne](https://www.code-saturne.org/), EDF's finite volume CFD solver. It implements `pc cae cfd`.

```shell
pc cae cfd -i //pub/feature/cae/code_saturne:cfd :pipe
pc test -f cfd
```

or name it for a part with `implementation:` in the part's `cfd:` section, or for everything with the
`caeCfdImplementation` user configuration option.

## What it computes

The part is read as the **fluid volume**. Its `cfd:` section says where the flow goes in and out, on ports
whose sketches are the surfaces meant -- PartCAD imprints each port's sketch on the part before this package
runs and hands it the faces each covers:

```yaml
parts:
  pipe:
    type: build123d
    implements:
      pipe-inlet: [[0, 0, -50], [0, 0, 1], 0]
      pipe-outlet: [[0, 0, 50], [0, 0, 1], 0]
    cfd:
      load:
        pipe-inlet: 0.005 N  # what drives the flow in
      outlet:
        - pipe-outlet        # where it leaves
```

* **Inlets** are the ports under `load:`. The flow is drawn in from a reservoir at rest whose pressure is the
  force over the area the port covers: 5 mN over a 10 mm bore is 63.66 Pa.
* **Outlets** are the ports under `outlet:`, held at the reference pressure.
* **Every other surface is a wall**, no-slip.

### How that is posed, and why

An inflow through an imposed-pressure *outlet* -- the obvious reading of "hold the inlet at 63.66 Pa" -- is
ill-posed in code_saturne: on the example pipe it settles on a solution whose inflow and outflow disagree
three to one. What code_saturne offers for an inflow at a pressure is its **free inlet**, a reservoir at the
reference pressure feeding the boundary (here with no loss), so each inlet is that and each outlet is held the
driving pressure *below* the reference. Two things follow:

* the reservoir fixes the inlet's *total* pressure where a static-pressure inlet fixes its *static* one; for a
  laminar flow the difference is the dynamic pressure, a fraction of a percent of the drive (0.18 Pa of
  63.66 Pa on the example pipe);
* there is one reservoir, so **every inlet is driven at the same pressure**. A part whose inlets carry
  different forces over their areas is told so and not solved -- use another implementation for it.

code_saturne also solves in absolute pressure, with a reference of an atmosphere; every pressure here is
written relative to it.

### The pipeline

1. `code_saturne create` makes a study.
2. gmsh meshes the part into tetrahedra, each condition's faces a physical group and the rest `walls`
   (PartCAD's `cae_support.mesh()`). code_saturne keeps groups by *number*, so each boundary zone selects the
   number its group was given.
3. The setup is written through code_saturne's own model classes -- the ones its GUI uses -- run by its own
   interpreter, so every default the solver expects is filled in: laminar, the steady SIMPLE algorithm for
   `iterations` steps, the fluid, the zones.
4. `code_saturne run` runs it. The flow rate is code_saturne's own mass flux through the outlets, from its
   log; the fields come back as EnSight Gold in its ASCII form, read here with plain text parsing.

What comes back is `<part>.cfd.glb`, the boundary coloured by the pressure in the cell behind each face; a
measurement line in the log -- flow rate, mean outlet speed, peak speed, Reynolds number, the driving
pressure, element count -- and **findings**, which fail `pc test`: a flow whose residuals did not fall by
`residual_drop` within `iterations` steps, a Reynolds number past 2300 (where this laminar analysis stops
applying), a speed past `max_velocity`, a flow rate short of `min_flow_rate` when that is set, and any
condition whose port covers no surface.

### Measured

`examples/feature_cae` in the PartCAD repository has a pipe whose answer is a formula:

| | Hagen-Poiseuille | this package |
| --- | --- | --- |
| Flow rate | 1.5625 mL/s | 1.572 mL/s (+0.6 %) |
| Centreline (peak) speed | 0.03979 m/s | 0.0399 m/s |

72,087 tetrahedra, 500 steps. The small excess over the static-pressure solvers (OpenFOAM, Elmer, MOOSE read
0.4-1.2 % low) is the reservoir inlet above.

## What it needs

**A container runtime, or code_saturne installed.**

With a container runtime PartCAD runs the analysis in the image `partcad.yaml` names: PartCAD's Python base
image with code_saturne built from a pinned release (there is no Debian or conda-forge package of it) and
Debian's gmsh, for amd64 and arm64. The image keeps a C compiler, because code_saturne compiles every formula in
a setup -- the inlet's head loss here -- at the start of each run.

Without one, PartCAD runs it in a conda or venv sandbox on the host, and code_saturne has to be there. The
`code_saturne` launcher is found where `PARTCAD_CODE_SATURNE` points, on `PATH`, or in `/opt/code_saturne/bin`,
`/usr/local/bin`, `/usr/bin` or `~/code_saturne/bin`. Its own Python, which writes the setup, is the one its
launcher names. The Dockerfile is a working recipe for building it; EDF's salome_meca carries it too.

The daemon is what runs the analysis, so a variable set in your shell reaches it only when the daemon is
started from that shell: `pc daemon stop` first.

### The pin

`dockerImage:` names the image by a hash of what it is built from -- the Dockerfile, `verify_image.py` and the
Python version. Run `./image-tag.sh` after changing any of them and update the pin; the `Image` workflow refuses
to build if the two disagree, and publishes `<tag>-amd64` and `<tag>-arm64` on a push to `main`. The arm64
build compiles code_saturne under emulation and takes a while.

## Tests

```shell
pytest
```

beside an installed `partcad`. The EnSight readers, the face owners, the mass flow read from the log, the
convergence test and the refusals are checked everywhere; the example pipe is solved and compared with
Hagen-Poiseuille where code_saturne, gmsh and build123d are all present -- in practice, in this package's image.

To keep a study for inspection, set `PARTCAD_CODE_SATURNE_STUDY` to an empty directory before the daemon
starts.

## License

Apache License 2.0. code_saturne is GPL-2.0-or-later and is not part of this package: the image builds it from
EDF's published sources at the release the Dockerfile names.
