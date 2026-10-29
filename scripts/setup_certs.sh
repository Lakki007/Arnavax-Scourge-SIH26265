#!/usr/bin/env bash
# Generates SYNTHETIC test certificates (never use in production):
#  test_ca            - the only CA the demo verifier trusts
#  bank_ok            - netbanking.mockbank.test signed by test_ca      (genuine mock bank)
#  bank_fake          - netbanking.mockbank.test signed by attacker_ca  (prover's fake bank)
#  bank_unlisted      - netbanking.unlisted-bank.test signed by test_ca (valid cert, host not approved)
set -euo pipefail
cd "$(dirname "$0")/../certs"
mkca(){ openssl genrsa -out $1.key.pem 2048 2>/dev/null
  openssl req -x509 -new -key $1.key.pem -days 3650 -subj "/CN=$2" -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" -out $1.pem 2>/dev/null
  openssl x509 -in $1.pem -outform DER -out $1.der; }
mkleaf(){ openssl genrsa -out $1.key.pem 2048 2>/dev/null
  openssl pkcs8 -topk8 -nocrypt -in $1.key.pem -outform DER -out $1.key.der
  openssl req -new -key $1.key.pem -subj "/CN=$2" -out $1.csr 2>/dev/null
  printf "subjectAltName=DNS:$2\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n" > $1.ext
  openssl x509 -req -in $1.csr -CA $3.pem -CAkey $3.key.pem -CAcreateserial -days 825 -extfile $1.ext -out $1.pem 2>/dev/null
  openssl x509 -in $1.pem -outform DER -out $1.der; rm -f $1.csr $1.ext; }
mkca test_ca "Scourge SYNTHETIC test CA"
mkca attacker_ca "Attacker CA (synthetic)"
mkleaf bank_ok netbanking.mockbank.test test_ca
mkleaf bank_fake netbanking.mockbank.test attacker_ca
mkleaf bank_unlisted netbanking.unlisted-bank.test test_ca
rm -f *.srl
echo "certificates written to $(pwd)"
