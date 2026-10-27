#!/usr/bin/env bash
# Read-only diagnostics; missing optional programs do not stop the report.
set -u

cat /etc/os-release
uname -r
python3 --version
free -h
df -h .
id -nG
ls -l /dev/kfd /dev/dri/render* 2>/dev/null

if command -v rocminfo >/dev/null 2>&1; then
    rocminfo | grep -E 'Marketing Name:|Name:.*gfx'
elif [[ -x /opt/rocm/bin/rocminfo ]]; then
    /opt/rocm/bin/rocminfo | grep -E 'Marketing Name:|Name:.*gfx'
else
    echo "rocminfo not found"
fi

if command -v dpkg-query >/dev/null 2>&1; then
    dpkg-query -W -f='${Package}\t${Version}\n' \
        'rocm*' 'hip*' 'libhsa*' 'libamdhip*' 'librocblas*' 2>/dev/null || true
fi

for version_file in /opt/rocm/.info/version*; do
    [[ -f "$version_file" ]] && cat "$version_file"
done
exit 0
