#!/usr/bin/env bash
# Make a stand-in hero clip from a still: a slow push-in with a gentle pan.
# Usage: placeholder-clip.sh <still> [out.mp4]
set -euo pipefail

still=${1:?usage: placeholder-clip.sh <still> [out.mp4]}
out=${2:-hero-placeholder.mp4}

command -v ffmpeg >/dev/null || { echo "placeholder-clip.sh: ffmpeg not found" >&2; exit 1; }
[ -f "$still" ] || { echo "placeholder-clip.sh: no such file: $still" >&2; exit 1; }

# 150 frames at 25 fps = 6 s. Upscaling first keeps zoompan from jittering.
ffmpeg -y -v error -i "$still" \
  -vf "scale=4800:-2,zoompan=z='1+0.18*on/150':x='(iw-iw/zoom)*(0.35+0.3*on/150)':y='(ih-ih/zoom)/2':d=150:s=1600x900:fps=25" \
  -frames:v 150 -c:v libx264 -pix_fmt yuv420p "$out"

echo "placeholder-clip.sh: wrote $out"
