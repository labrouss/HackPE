#!/usr/bin/env bash
# Boot a HackPE USB image in QEMU (UEFI/OVMF, image attached as a USB stick) and take screenshots:
# OpenCore picker -> HackPE (WinPE) desktop. Uses KVM when the runner exposes /dev/kvm.
#   build/boot_test.sh HackPE-usb.img OUTDIR [seconds...]
set -euo pipefail
img=$1; out=$2; shift 2
[ $# -gt 0 ] && shots=("$@") || shots=(20 90 180 300)
mkdir -p "$out"
code=/usr/share/OVMF/OVMF_CODE_4M.fd; vars_src=/usr/share/OVMF/OVMF_VARS_4M.fd
[ -f "$code" ] || { code=/usr/share/OVMF/OVMF_CODE.fd; vars_src=/usr/share/OVMF/OVMF_VARS.fd; }
vars=$(mktemp); cp "$vars_src" "$vars"
mon=$(mktemp -u)

if [ -w /dev/kvm ]; then accel=(-accel kvm -cpu host); echo "KVM available"; else accel=(-accel tcg -cpu max); echo "no KVM - emulation (slow)"; fi

qemu-system-x86_64 -machine q35 -m 4096 -smp 2 "${accel[@]}" \
  -drive if=pflash,format=raw,readonly=on,file="$code" -drive if=pflash,format=raw,file="$vars" \
  -drive if=none,id=stick,format=raw,snapshot=on,file="$img" -device qemu-xhci -device usb-storage,drive=stick,bootindex=0 \
  -device usb-tablet -vga std -display none -monitor unix:"$mon",server,nowait -serial file:"$out/serial.log" &
qpid=$!
trap 'kill $qpid 2>/dev/null || true; rm -f "$vars"' EXIT

mon_cmd() { python3 - "$mon" "$1" <<'EOF'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(0.3); s.recv(65536)
s.send(sys.argv[2].encode() + b"\n"); time.sleep(1.5)
EOF
}

t=0
for at in "${shots[@]}"; do
  sleep $((at - t)); t=$at
  kill -0 $qpid 2>/dev/null || { echo "QEMU exited early"; exit 1; }
  mon_cmd "screendump $out/boot-${at}s.png -f png" && echo "screenshot at ${at}s"
done
ls -la "$out"
