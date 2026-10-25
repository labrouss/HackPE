#!/usr/bin/env bash
# Cross-compile 64-bit Windows iasl.exe + acpidump.exe from official ACPICA source with MinGW-w64.
# Why: Intel only publishes 32-bit Windows binaries and x64 WinPE has no WOW64 to run them.
# Usage: build/build-acpica-win64.sh [ACPICA_TAG] [OUT_DIR]      (needs: git make bison flex gcc-mingw-w64-x86-64)
set -euo pipefail
TAG="${1:-}"
OUT="$(realpath -m "${2:-acpica-win64}")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
if [ -n "$TAG" ]; then
  git clone -q --depth 1 --branch "$TAG" https://github.com/acpica/acpica "$WORK/acpica"
else
  git clone -q --depth 1 https://github.com/acpica/acpica "$WORK/acpica"
fi
cd "$WORK/acpica/generate/unix"
# Use the Windows OS service layer instead of the POSIX/Linux one
sed -i 's#\$(OBJDIR)/osunixxf.o#$(OBJDIR)/oswinxf.o#' iasl/Makefile acpidump/Makefile
sed -i 's#\$(OBJDIR)/osunixdir.o#$(OBJDIR)/oswindir.o#; \#\$(OBJDIR)/osunixmap.o#d; s#\$(OBJDIR)/oslinuxtbl.o#$(OBJDIR)/oswintbl.o#' acpidump/Makefile
# MinGW already provides the POSIX names: skip the MSVC remaps (#define stat _stat ...) for MinGW
for h in ../../source/include/platform/acwin64.h ../../source/include/platform/acwin.h; do
  perl -0pi -e 's/(#define open\s+_open.*?#define S_IFDIR\s+_S_IFDIR\n)/#ifndef __MINGW32__\n$1#endif\n/s' "$h"
done
# MinGW predefines WIN32 even on x64 -> make acenv.h choose the 64-bit header (acwin64.h) on _WIN64
perl -pi -e 's/^#elif defined\(WIN32\)\s*$/#elif defined(WIN32) \&\& !defined(_WIN64)\n/' ../../source/include/platform/acenv.h
grep -q 'defined(WIN32) && !defined(_WIN64)' ../../source/include/platform/acenv.h || { echo "acenv.h patch failed"; exit 1; }
EXTRA="-D_CRT_SECURE_NO_WARNINGS -Wno-error -Wno-format -Wno-unused-parameter"
make -j"$(nproc)" iasl acpidump \
  CC=x86_64-w64-mingw32-gcc ACPI_HOST=_WIN64 NOWERROR=TRUE \
  OPT_CFLAGS="-O2 $EXTRA" LDFLAGS="-static -s" >"$WORK/build.log" 2>&1 || { tail -60 "$WORK/build.log"; exit 1; }
mkdir -p "$OUT"
for t in iasl acpidump; do
  f=$(find "$WORK/acpica/generate/unix" -path "*/bin/$t*" -type f | head -1)
  cp "$f" "$OUT/$t.exe"
done
VER=$(git -C "$WORK/acpica" describe --tags --always 2>/dev/null || echo unknown)
echo "$VER" > "$OUT/VERSION.txt"
file "$OUT"/*.exe 2>/dev/null || ls -l "$OUT"
echo "ACPICA $VER -> $OUT"
