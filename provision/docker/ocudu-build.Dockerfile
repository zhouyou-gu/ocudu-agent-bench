FROM ubuntu:22.04

LABEL org.opencontainers.image.title="OCUDUAgentBench OCUDU build placeholder"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
    cmake \
    git \
    && rm -rf /var/lib/apt/lists/*
