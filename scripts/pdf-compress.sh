#!/usr/bin/env bash
# pdf-compress — compress a PDF down to a target maximum size
#
# Iterates a Ghostscript quality ladder (least aggressive first) and stops at
# the first attempt that fits the target. Text stays vector/selectable; only
# raster images get downsampled and re-encoded. If no stage reaches the
# target, keeps the smallest result and warns (exit 3).
#
# Usage:
#   pdf-compress <input.pdf> [-m MB] [-o out.pdf] [-f] [--info]
#     -m, --max MB       target maximum size in MB (default: 20)
#     -o, --output FILE  output path (default: <input>-compressed.pdf)
#     -f, --force        overwrite the output file if it exists
#         --info         print pdfimages diagnostics only (what eats the size)
#     -h, --help
#
# Examples:
#   pdf-compress thesis.pdf -m 20
#   pdf-compress scan.pdf -m 5 -o scan-small.pdf -f
#   pdf-compress --info heavy.pdf
#
# Exit codes: 0 target met (or --info ran); 1 usage error; 2 ghostscript
#             missing; 3 target not reached (best result kept); 4 gs failed.

set -euo pipefail

# --- resolve binaries (brew lives outside PATH in non-interactive shells) ---
find_bin() {
  local name="$1" brew="/home/linuxbrew/.linuxbrew/bin/$1"
  command -v "$name" 2>/dev/null || { [ -x "$brew" ] && echo "$brew" || true; }
}
GS="$(find_bin gs || true)"
[ -n "$GS" ] || { echo "error: ghostscript not found (brew install ghostscript)" >&2; exit 2; }

MAX_MB=20; OUT=""; FORCE=0; INFO=0; INPUT=""

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    -m|--max)    MAX_MB="$2"; shift 2 ;;
    -o|--output) OUT="$2"; shift 2 ;;
    -f|--force)  FORCE=1; shift ;;
    --info)      INFO=1; shift ;;
    -h|--help)   usage ;;
    -*)          echo "error: unknown option $1" >&2; exit 1 ;;
    *)           INPUT="$1"; shift ;;
  esac
done

[ -n "$INPUT" ] || { usage >&2; exit 1; }
[ -f "$INPUT" ] || { echo "error: no such file: $INPUT" >&2; exit 1; }

ORIG_BYTES=$(stat -c%s "$INPUT")
ORIG_MB=$(awk -v b="$ORIG_BYTES" 'BEGIN{printf "%.2f", b/1048576}')

# --- diagnostics mode: show what the PDF is made of, then exit -----------
if [ "$INFO" -eq 1 ]; then
  PDFIMAGES="$(find_bin pdfimages || true)"
  echo "file: $INPUT (${ORIG_MB} MB)"
  if [ -n "$PDFIMAGES" ]; then
    echo "--- largest embedded images (bytes, dpi, type) ---"
    "$PDFIMAGES" -list "$INPUT" | {
      read -r header; echo "$header"
      awk 'NR>1' | sort -k14,14nr | head -15
    }
    total=$("$PDFIMAGES" -list "$INPUT" | tail -n +3 | wc -l)
    echo "--- total embedded images: $total ---"
  else
    echo "pdfimages not found (brew install poppler) for deeper diagnostics"
  fi
  exit 0
fi

# --- output path / safety --------------------------------------------------
OUT="${OUT:-${INPUT%.*}-compressed.pdf}"
[ "$OUT" != "$INPUT" ] || { echo "error: output would overwrite the input; use -o" >&2; exit 1; }
if [ -e "$OUT" ] && [ "$FORCE" -ne 1 ]; then
  echo "error: $OUT exists (use -f to overwrite)" >&2; exit 1
fi

TARGET_BYTES=$(awk -v m="$MAX_MB" 'BEGIN{printf "%d", m*1048576}')
fits() { awk -v b="$1" -v t="$TARGET_BYTES" 'BEGIN{exit !(b <= t)}'; }

# --- quality ladder: least aggressive first --------------------------------
# stages: printer(300dpi) -> ebook(150dpi) -> screen(72dpi) -> custom 60 -> custom 40
stage_args() {
  local dpi="$2" q="$3"
  case "$1" in
    printer) echo "-dPDFSETTINGS=/printer" ;;
    ebook)   echo "-dPDFSETTINGS=/ebook" ;;
    screen)  echo "-dPDFSETTINGS=/screen" ;;
    custom)  printf '%s\n' \
      "-dDownsampleColorImages=true" "-dColorImageResolution=$dpi" \
      "-dColorImageDownsampleType=/Average" \
      "-dDownsampleGrayImages=true" "-dGrayImageResolution=$dpi" \
      "-dDownsampleMonoImages=true" "-dMonoImageResolution=200" \
      "-dAutoFilterColorImages=false" "-dColorImageFilter=/DCTEncode" \
      "-dAutoFilterGrayImages=false" "-dGrayImageFilter=/DCTEncode" \
      "-dJPEGQ=$q" ;;
  esac
}

run_stage() {
  local out="$1"; shift
  # shellcheck disable=SC2086
  "$GS" -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 -dNOPAUSE -dQUIET -dBATCH \
        -dDetectDuplicateImages=true -dSubsetFonts=true \
        -sOutputFile="$out" "$@" "$INPUT"
}

TMP="$(mktemp -- "${OUT}.tmp.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

BEST_BYTES=$ORIG_BYTES; BEST_LABEL="original (untouched)"; MET=0
cp -- "$INPUT" "$TMP"   # best-so-far seed: the input itself

while IFS='|' read -r label dpi q; do
  stage_tmp="$(mktemp -- "${OUT}.try.XXXXXX")"
  if run_stage "$stage_tmp" $(stage_args "$label" "$dpi" "$q"); then
    bytes=$(stat -c%s "$stage_tmp")
    mb=$(awk -v b="$bytes" 'BEGIN{printf "%.2f", b/1048576}')
    pct=$(awk -v b="$bytes" -v o="$ORIG_BYTES" 'BEGIN{printf "%d", 100-(b*100/o)}')
    if fits "$bytes"; then
      printf 'OK  [%-8s] -> %s MB (-%s%%)  fits target %s MB\n' "$label" "$mb" "$pct" "$MAX_MB"
      mv -- "$stage_tmp" "$TMP"; BEST_BYTES=$bytes; BEST_LABEL="$label"; MET=1
      break
    elif [ "$bytes" -lt "$BEST_BYTES" ]; then
      printf '…   [%-8s] -> %s MB (-%s%%)  still over target\n' "$label" "$mb" "$pct"
      mv -- "$stage_tmp" "$TMP"; BEST_BYTES=$bytes; BEST_LABEL="$label"
    else
      printf '…   [%-8s] -> %s MB  no improvement, skipping\n' "$label" "$mb"
      rm -f "$stage_tmp"
    fi
  else
    echo "…   [$label] ghostscript failed, skipping" >&2
    rm -f "$stage_tmp"
  fi
done <<'STAGES'
printer|300|0
ebook|150|0
screen|72|0
custom|60|50
custom|40|30
STAGES

mv -- "$TMP" "$OUT"
trap - EXIT

BEST_MB=$(awk -v b="$BEST_BYTES" 'BEGIN{printf "%.2f", b/1048576}')
SAVED=$(awk -v b="$BEST_BYTES" -v o="$ORIG_BYTES" 'BEGIN{printf "%d", 100-(b*100/o)}')
echo "result: $OUT (${BEST_MB} MB, saved ${SAVED}%, stage: ${BEST_LABEL})"

if [ "$MET" -eq 1 ]; then
  exit 0
else
  echo "warning: target ${MAX_MB} MB not reached; kept the smallest result (${BEST_MB} MB)." >&2
  echo "         If it is mostly text/vector, the source is already tight; consider OCR-based tools (ocrmypdf) for scans." >&2
  exit 3
fi
