FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates ccache cmake gcc g++ git iproute2 iputils-ping libboost-program-options-dev \
    libfftw3-dev libgtest-dev libmbedtls-dev libsctp-dev libyaml-cpp-dev libzmq3-dev make ninja-build \
    pkg-config python3 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /work
