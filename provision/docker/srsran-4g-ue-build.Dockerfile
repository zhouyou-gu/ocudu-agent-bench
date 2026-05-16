FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates ccache cmake gcc g++ git iproute2 iputils-ping libboost-program-options-dev \
    libconfig++-dev libfftw3-dev libmbedtls-dev libsctp-dev libzmq3-dev make ninja-build pkg-config \
    python3 rapidjson-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /work
