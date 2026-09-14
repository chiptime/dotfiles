#!/usr/bin/env bash
# wsl-shot — capture the Windows desktop from WSL via PowerShell interop.
# Node-side script for the OpenClaw "pc-eyes" skill. Born in ~/.dotfiles,
# mapped to ~/.local/bin/wsl-shot on this machine.
#
# Output: one JSON line on stdout: {"file","width","height","bytes"}
#         (JSON moves to stderr when --b64 is used; stdout carries base64 only)
# Options:
#   --b64        print base64 of the capture to stdout (for WS transport)
#   --width N    scale capture to N px wide (default 0 = full res)
#
# NOTE: env vars do NOT cross into Windows processes in this WSL setup
# (WSLENV /u proved unreliable) — values are inlined into the PS command.
set -euo pipefail

B64=0
MAXW="${WSL_SHOT_WIDTH:-0}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --b64) B64=1 ;;
    --width) MAXW="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ "$MAXW" =~ ^[0-9]+$ ]] || { echo '{"error":"invalid --width"}' >&2; exit 2; }

PS_BIN="$(command -v powershell.exe 2>/dev/null || true)"
if [[ -z "$PS_BIN" ]]; then
  PS_BIN="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
fi
if [[ ! -x "$PS_BIN" ]]; then
  echo '{"error":"powershell.exe not found (WSL interop missing?)"}' >&2
  exit 1
fi

STATE_DIR="${HOME}/.local/state/pc-eyes"
mkdir -p "$STATE_DIR"
OUT="${STATE_DIR}/shot-$(date +%Y%m%d-%H%M%S).jpg"
TMP_WIN='C:\Users\Public\pc-eyes-tmp.jpg'

read -r -d '' PS_CMD <<'PSEOF' || true
$ErrorActionPreference="Stop"
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$b=[System.Windows.Forms.SystemInformation]::VirtualScreen
$w=$b.Width; $h=$b.Height
$targetW=__MAXW__
if($targetW -le 0 -or $targetW -gt $w){$targetW=$w}
$targetH=[int]([double]$h*$targetW/$w)
$src=New-Object System.Drawing.Bitmap $w,$h
$g=[System.Drawing.Graphics]::FromImage($src)
$g.CopyFromScreen($b.Left,$b.Top,0,0,$src.Size)
$g.Dispose()
$bmp=$src
if($targetW -ne $w){
  $bmp=New-Object System.Drawing.Bitmap $targetW,$targetH
  $g2=[System.Drawing.Graphics]::FromImage($bmp)
  $g2.InterpolationMode="HighQualityBicubic"
  $g2.DrawImage($src,0,0,$targetW,$targetH)
  $g2.Dispose(); $src.Dispose()
}
$codec=[System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" }
$ep=New-Object System.Drawing.Imaging.EncoderParameters(1)
$ep.Param[0]=New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality,([long]85))
$bmp.Save("__OUT__",$codec,$ep)
$bmp.Dispose()
Write-Output "$targetW $targetH"
PSEOF
PS_CMD="${PS_CMD/__MAXW__/$MAXW}"
PS_CMD="${PS_CMD/__OUT__/$TMP_WIN}"

DIMS="$("$PS_BIN" -NoProfile -Command "$PS_CMD" | tail -1)"

cp "/mnt/c/Users/Public/pc-eyes-tmp.jpg" "$OUT"
rm -f "/mnt/c/Users/Public/pc-eyes-tmp.jpg"

# Deliver the capture to the gateway container (bind mount /downloads/pc-eyes)
# so the agent can read it as a FILE — never ship base64 through the model context.
GW_PATH=""
if scp -o BatchMode=yes -o ConnectTimeout=8 "$OUT" "contabo-vps:/var/lib/docker/volumes/aistack-all-o9aphm_downloads/_data/pc-eyes/$(basename "$OUT")" 2>/dev/null; then
  GW_PATH="/downloads/pc-eyes/$(basename "$OUT")"
fi

read -r W H BYTES <<< "$DIMS"
BYTES=$(stat -c %s "$OUT")

JSON="{\"file\":\"${OUT}\",\"gatewayPath\":\"${GW_PATH}\",\"width\":${W},\"height\":${H},\"bytes\":${BYTES}}"
if [[ "$B64" -eq 1 ]]; then
  echo "$JSON" >&2
  base64 -w0 "$OUT"
else
  echo "$JSON"
fi
