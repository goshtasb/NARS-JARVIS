#!/bin/sh
# One-time: create a persistent self-signed code-signing identity "JARVIS Self-Signed" (ADR-021).
#
# WHY: the menu-bar app must hold an Accessibility (TCC) grant to drive the GUI. macOS keys that grant
# to the app's *Designated Requirement*. An ad-hoc signature's DR is the cdhash, which changes on every
# rebuild — so each rebuild silently revoked the grant and JARVIS went "blind." Signing with a stable
# self-signed cert makes the DR identity-based (certificate leaf hash), constant across rebuilds, so you
# grant Accessibility ONCE and it persists forever.
#
# Idempotent. Imports key+cert as separate PEMs because macOS `security import` can't verify the MAC of
# a modern OpenSSL PKCS#12. The cert is untrusted as an x509 root (CSSMERR_TP_NOT_TRUSTED) — that's
# fine: `codesign` signs with it regardless, and TCC only cares that the DR matches.
set -e
IDENTITY="JARVIS Self-Signed"
if security find-identity -p codesigning 2>/dev/null | grep -q "$IDENTITY"; then
  echo "$IDENTITY already present — nothing to do."
  exit 0
fi
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/cert.cnf" <<'CNF'
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = JARVIS Self-Signed
[v3]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$tmp/key.pem" -out "$tmp/cert.pem" -days 3650 \
  -config "$tmp/cert.cnf" -extensions v3
kc="$HOME/Library/Keychains/login.keychain-db"
security import "$tmp/key.pem"  -k "$kc" -A -T /usr/bin/codesign
security import "$tmp/cert.pem" -k "$kc" -A -T /usr/bin/codesign
echo "Created '$IDENTITY'. Now: ui/build.sh, then grant Accessibility once — it persists from now on."
