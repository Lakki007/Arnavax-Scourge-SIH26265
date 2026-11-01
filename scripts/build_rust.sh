#!/usr/bin/env bash
# Builds scourge_prove, scourge_verifierd and scourge_mockbank inside a pinned TLSNotary checkout.
# Requires: git, Rust (the repo pins 1.95.0 via rust-toolchain.toml), ~4 GB RAM. First build takes several minutes.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${TLSN_DIR:-$HERE/.tlsn}"
if [ ! -d "$WORK/.git" ]; then git clone https://github.com/tlsnotary/tlsn "$WORK"; fi
cd "$WORK"
git fetch -q origin "$(cat "$HERE/rust/TLSN_COMMIT.txt")" || true
git checkout -q "$(cat "$HERE/rust/TLSN_COMMIT.txt")"
git apply --check "$HERE/rust/examples_Cargo.toml.patch" 2>/dev/null && git apply "$HERE/rust/examples_Cargo.toml.patch" || echo "Cargo.toml patch already applied or differs; continuing"
mkdir -p crates/examples/scourge crates/examples/bsp crates/examples/fdr
cp "$HERE"/rust/*.rs crates/examples/scourge/
cp "$HERE"/rust/realbank/real_bank_probe.rs crates/examples/fdr/real_bank_probe.rs
# the patch also registers older spike examples; provide them as stubs if absent so the manifest resolves
for f in bsp/prove bsp/verifierd bsp/challenge bsp/mockbank fdr/fdr_proof fdr/real_bank_probe; do [ -f crates/examples/$f.rs ] || echo 'fn main() {}' > crates/examples/$f.rs; done
cp "$HERE/rust/Cargo.lock" Cargo.lock
cargo build --release --example scourge_prove --example scourge_verifierd --example scourge_mockbank --example real_bank_probe
cargo test --release --example scourge_verifierd   # JSON span-parser unit tests
# Linux x86-64 builds go to bin/ (what the repo ships); every other platform gets its own folder, e.g. bin/darwin-arm64,
# so the Linux binaries are never overwritten. The app picks the right folder automatically (app/settings.py).
PLAT="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
if [ "$PLAT" = "linux-x86_64" ]; then DEST="$HERE/bin"; else DEST="$HERE/bin/$PLAT"; fi
mkdir -p "$DEST"
cp target/release/examples/scourge_{prove,verifierd,mockbank} target/release/examples/real_bank_probe "$DEST/"
( cd "$DEST" && shasum -a 256 scourge_prove scourge_verifierd scourge_mockbank real_bank_probe > SHA256SUMS 2>/dev/null || sha256sum scourge_prove scourge_verifierd scourge_mockbank real_bank_probe > SHA256SUMS )
echo "binaries copied to $DEST"
