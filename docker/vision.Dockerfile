# vision-mcp-server needs system libs the generic mcp-server.Dockerfile doesn't install:
# opencv-python (even used headlessly) dlopens libGL/libglib/libSM/libXext/libXrender at
# import time, and insightface's ONNX models need libgomp for onnxruntime's CPU execution
# provider. Everything else follows the same pattern as docker/mcp-server.Dockerfile.
FROM python:3.11-slim AS libfreenect2-build

# Kinect v2 support (kinect2_client.py) needs libfreenect2 itself - a real C++ driver, not
# something `pip install` alone can provide; pylibfreenect2 (installed in the main stage below)
# is only a Cython wrapper around this. Built in its own stage so the final image doesn't carry
# cmake/build-essential/git - only the compiled .so and headers cross the COPY --from boundary.
#
# Pinned to a commit SHA, not a floating branch/tag reference - supply-chain hygiene for code
# that gets `make install`ed as root and links into a container that touches a physical USB
# device. Checked 2026-09-08 via `git ls-remote`: v0.2.1 (2018) IS the repo's current HEAD -
# OpenKinect/libfreenect2 has had no commits since that release at all. Worth naming plainly
# rather than glossing over: this is unmaintained upstream, a real factor to weigh, not just an
# old-but-fine pin. Re-check `git ls-remote` before ever assuming a newer commit exists.
ARG LIBFREENECT2_COMMIT=fd64c5d9b214df6f6a55b4419357e51083f15d93
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git pkg-config \
    libusb-1.0-0-dev libturbojpeg0-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/OpenKinect/libfreenect2.git /usr/src/libfreenect2 \
    && cd /usr/src/libfreenect2 \
    && git checkout "${LIBFREENECT2_COMMIT}" \
    # No OpenGL/OpenCL/CUDA dev headers installed above on purpose - this container has no GPU
    # (see kinect2_client.py's docstring), so cmake auto-detects their absence and builds only
    # the CPU pipeline instead of pulling a driver stack that would never be used.
    && cmake . -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local -DBUILD_EXAMPLES=OFF \
    && make -j"$(nproc)" \
    && make install

# pylibfreenect2 (the Cython wrapper - what kinect2_client.py actually imports) also gets built
# in THIS stage, not the final one: found by actually running the build, not assumed - its
# legacy setup.py (1) calls numpy.get_include() and cythonize() at module scope with neither
# declared as a build-time requirement, and (2) needs a real C compiler to turn the resulting
# .c files into a .so, none of which the slim final-stage image should have to carry just to
# satisfy one package's build step. Built here into a wheel instead, so the final stage only
# ever needs `pip install` on an already-compiled artifact - no compiler, no Cython, no git.
#
# Installed from git, not PyPI: PyPI's 0.1.4 sdist is missing
# pylibfreenect2/libfreenect2/libfreenect2.pxd (Cython fails trying to `cimport` it - a known
# packaging gap in that release), but the GitHub tree has the file. Pinned to a commit for the
# same reason libfreenect2 itself is pinned above - this is unmaintained upstream too, not a
# "just take latest" dependency; re-check `git ls-remote` before ever bumping it.
ARG PYLIBFREENECT2_COMMIT=40221e815c182ee31e8da33df717ccdef1bc615f
RUN pip install --no-cache-dir numpy cython wheel \
    && LIBFREENECT2_INSTALL_PREFIX=/usr/local pip wheel --no-cache-dir --no-build-isolation \
       --wheel-dir /wheels \
       "pylibfreenect2 @ git+https://github.com/r9y9/pylibfreenect2.git@${PYLIBFREENECT2_COMMIT}"

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libusb-1.0-0 \
    libturbojpeg0 \
    && rm -rf /var/lib/apt/lists/*

# Only the built libfreenect2 library/headers and the pylibfreenect2 wheel cross the COPY
# boundary - none of the toolchain (compiler/Cython/git) that built either of them.
COPY --from=libfreenect2-build /usr/local/lib/ /usr/local/lib/
COPY --from=libfreenect2-build /usr/local/include/libfreenect2/ /usr/local/include/libfreenect2/
COPY --from=libfreenect2-build /usr/local/lib/cmake/freenect2/ /usr/local/lib/cmake/freenect2/
COPY --from=libfreenect2-build /wheels/ /wheels/
RUN ldconfig

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir /wheels/pylibfreenect2*.whl \
    && rm -rf /wheels

ENV PYTHONUNBUFFERED=1 \
    FASTMCP_PORT=8000

# insightface's buffalo_l model (~150MB) downloads to /root/.insightface on first use -
# docker-compose.yml mounts a named volume there so it survives container restarts/rebuilds.

CMD ["python", "-m", "vision_mcp_server.server"]
