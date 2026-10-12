# The runtime the code_saturne flow analysis runs best in.
#
# It carries what pip cannot: code_saturne, which is a native solver, and a gmsh
# for 64-bit ARM Linux, for which pip has no wheel. Everything else the script
# imports is declared in `partcad.yaml` as `pythonRequirements` and installed by
# PartCAD into the environment it creates -- this image is a *runtime*, and the
# package has to work in a conda or venv sandbox on a host with code_saturne
# installed too.
#
# It does NOT carry this package's code. PartCAD mounts the package at run time.
ARG PYTHON_VERSION=3.11
ARG ARCH=amd64

# code_saturne is in neither Debian nor conda-forge, and publishes no release
# tarball, so it is built here from a pinned tag of its repository: bootstrapped
# with the autotools, configured without MPI, the GUI, SALOME or the
# documentation -- one process per analysis, driven by a setup file this
# package writes -- in a stage of its own, so the sources stay behind.
FROM ghcr.io/partcad/partcad-container-python:py${PYTHON_VERSION}-${ARCH} AS build
USER root
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
  && apt-get install --yes --no-install-recommends \
       git ca-certificates autoconf automake libtool make gcc g++ gfortran bison flex \
       zlib1g-dev libxml2-dev xz-utils \
  && rm -rf /var/lib/apt/lists/*
ARG SATURNE_RELEASE=v9.0.2
RUN git clone --depth 1 --branch "${SATURNE_RELEASE}" https://github.com/code-saturne/code_saturne.git /src/cs \
  && cd /src/cs && ./sbin/bootstrap \
  && mkdir -p /src/build && cd /src/build \
  && /src/cs/configure --prefix=/opt/code_saturne --disable-gui --without-mpi --without-salome --disable-doc \
       PYTHON=/usr/local/bin/python3 \
  && make -j"$(nproc)" \
  && make install \
  && rm -rf /src

FROM ghcr.io/partcad/partcad-container-python:py${PYTHON_VERSION}-${ARCH}

LABEL partcad.image="1"
LABEL org.opencontainers.image.source="https://github.com/partcad/partcad-cae-code-saturne"

USER root
ENV DEBIAN_FRONTEND=noninteractive

# The compilers stay: code_saturne turns every formula in a setup -- the head
# loss of an inlet, here -- into C and builds it at the start of each run, with
# the compiler it was configured with. And the mesher, from Debian, which builds
# `python3-gmsh` for amd64 and arm64 alike.
RUN apt-get update \
  && apt-get install --yes --no-install-recommends \
       make gcc g++ gfortran libxml2-dev zlib1g-dev xz-utils python3-gmsh \
  && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/code_saturne /opt/code_saturne

# Where `saturne_common.find_launcher()` looks even when PATH says nothing.
RUN ln -s /opt/code_saturne/bin/code_saturne /usr/local/bin/code_saturne

# gmsh's Python API is one ctypes module wrapping `libgmsh.so`, installed by
# Debian for Debian's interpreter; the one in this image is python.org's. A
# ctypes wrapper works from either, so the one file is copied where both can see
# it and named on PYTHONPATH -- PartCAD runs the script in a virtual environment
# it creates at run time, which sees neither site-packages.
RUN mkdir -p /opt/pc-site \
  && cp /usr/lib/python3/dist-packages/gmsh.py /opt/pc-site/gmsh.py
ENV PYTHONPATH=/opt/pc-site

ENV PC_CONTAINER_ALLOWED_COMMANDS='{"python": "/usr/local/bin/python3", "code_saturne": "/usr/local/bin/code_saturne"}'

# Two checks: the base image's own, and this package's, which meshes a box,
# writes a setup through code_saturne's own model classes and runs it -- the
# whole path the analysis takes, compile step included.
COPY verify_image.py /tmp/verify_image.py
RUN python3 /pc/verify.py \
  && python3 /tmp/verify_image.py \
  && rm /tmp/verify_image.py

USER pc
