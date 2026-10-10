#!/bin/zsh
# Creates "Dolmi Local Signing", a self-signed code-signing certificate in your login Keychain (once).
#
# Why: macOS ties privacy permissions (System Audio Recording) to an app's signature. Ad-hoc signed
# builds get a new signature every build, so each rebuild of Dolmi.app loses the permission. Signed
# with this fixed certificate, every build keeps it. Local only: it isn't an Apple Developer ID, so
# Gatekeeper still asks once on other Macs (right-click → Open).
#
# Remove it any time: Keychain Access → login → My Certificates → "Dolmi Local Signing" → Delete.
set -euo pipefail
NAME="Dolmi Local Signing"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-certificate -c "$NAME" "$KEYCHAIN" >/dev/null 2>&1; then
  echo "\"$NAME\" already exists."
  exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/cert.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
CNF
/usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$TMP/cert.cnf" \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" 2>/dev/null
PASS=$(/usr/bin/openssl rand -hex 16)
/usr/bin/openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -name "$NAME" \
  -passout "pass:$PASS" -out "$TMP/identity.p12"
# -T: codesign may use the key without asking
security import "$TMP/identity.p12" -k "$KEYCHAIN" -P "$PASS" -T /usr/bin/codesign >/dev/null
echo "Created \"$NAME\" in your login Keychain."
