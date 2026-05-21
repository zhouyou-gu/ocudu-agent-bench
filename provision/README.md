# Provision Scaffolds

This directory holds placeholder provisioning assets for future live-runtime
work. These files are not part of the current scored simulated benchmark path
and do not establish live OCUDU, Open5GS, srsUE/ZMQ, or FlexRIC readiness.

Current runnable tasks use `E.runtime_adapter = simulated_ocudu`. A live adapter
must replace or complete these scaffolds, pass readiness checks, implement
dispatch/artifact/cleanup behavior, and add tests before the benchmark may claim
live OCUDU/FlexRIC/UE/core execution.
