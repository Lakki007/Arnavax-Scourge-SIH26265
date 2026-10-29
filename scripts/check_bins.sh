#!/usr/bin/env bash
# Verifies the shipped binaries match bin/SHA256SUMS (built from rust/ at the TLSNotary commit in rust/TLSN_COMMIT.txt).
cd "$(dirname "$0")/../bin" && sha256sum -c SHA256SUMS
