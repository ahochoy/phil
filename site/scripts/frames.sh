#!/usr/bin/env bash
# Turn a hero clip into the WebP frame sequence the hero player reads.
# Usage: frames.sh <clip> [count=120]
set -euo pipefail

clip=${1:?usage: frames.sh <clip> [count=120]}
count=${2:-120}
widths=(960 1600)
pad=4
warn_kb=8192

for tool in ffmpeg ffprobe cwebp; do
  command -v "$tool" >/dev/null || { echo "frames.sh: $tool not found" >&2; exit 1; }
done
[ -f "$clip" ] || { echo "frames.sh: no such clip: $clip" >&2; exit 1; }
[[ "$count" =~ ^[1-9][0-9]*$ ]] || { echo "frames.sh: count must be a positive integer" >&2; exit 1; }

site=$(cd "$(dirname "$0")/.." && pwd)
out="$site/public/hero"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Decode every frame once, then pick `count` of them evenly by index.
ffmpeg -v error -i "$clip" "$tmp/all-%06d.png"
total=$(find "$tmp" -name 'all-*.png' | wc -l | tr -d ' ')
[ "$total" -ge "$count" ] || { echo "frames.sh: clip has $total frames, fewer than $count" >&2; exit 1; }

IFS=x read -r w h < <(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0:s=x "$clip")
aspect=$(awk -v w="$w" -v h="$h" 'BEGIN { printf "%.6f", w / h }')

rm -rf "$out/frames"
for width in "${widths[@]}"; do mkdir -p "$out/frames/$width"; done

for ((k = 0; k < count; k++)); do
  if [ "$count" -eq 1 ]; then src_index=1; else src_index=$((1 + k * (total - 1) / (count - 1))); fi
  src=$(printf "%s/all-%06d.png" "$tmp" "$src_index")
  name=$(printf "%0${pad}d.webp" $((k + 1)))
  cwebp -quiet -q 70 -resize 960 0 "$src" -o "$out/frames/960/$name"
  cwebp -quiet -q 72 -resize 1600 0 "$src" -o "$out/frames/1600/$name"
done

cp "$out/frames/1600/$(printf "%0${pad}d" 1).webp" "$out/poster.webp"
printf '{"count": %d, "widths": [960, 1600], "aspect": %s, "pad": %d}\n' "$count" "$aspect" "$pad" > "$out/frames.json"

for width in "${widths[@]}"; do
  kb=$(du -sk "$out/frames/$width" | cut -f1)
  echo "frames.sh: $width px: $count frames, $((kb / 1024)).$(((kb % 1024) * 10 / 1024)) MB"
  if [ "$width" -eq 1600 ] && [ "$kb" -gt "$warn_kb" ]; then
    echo "frames.sh: warning: the 1600 set is over 8 MB; consider fewer frames or a lower quality" >&2
  fi
done
echo "frames.sh: wrote $out/frames.json"
