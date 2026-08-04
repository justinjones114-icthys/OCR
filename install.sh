#!/usr/bin/env bash
# Install the ocr-pages skills into ~/.claude/skills/ so they load in any
# Claude Code session, not just one started inside this repository.
#
#   ./install.sh              install, refusing to clobber an existing copy
#   ./install.sh --force      overwrite an existing copy
#   ./install.sh --check      report dependency status and change nothing
#
# Both skills are installed together on purpose: ocr-pages-vlm reaches for
# ../ocr-pages/scripts/, so they have to stay siblings.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.claude/skills"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
SKILLS=(ocr-pages ocr-pages-vlm)

FORCE=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

green() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$1"; }
bad()   { printf '  \033[31m✗\033[0m %s\n' "$1"; }

# ---------------------------------------------------------------- dependencies
echo "dependencies"
MISSING=()

for mod in PIL numpy scipy surya; do
  if python3 -c "import $mod" >/dev/null 2>&1; then
    green "python: $mod"
  else
    bad "python: $mod"
    MISSING+=("$mod")
  fi
done

# Surya shells out to llama.cpp for inference; without it recognition cannot run.
if command -v "${LLAMA_CPP_BINARY:-llama-server}" >/dev/null 2>&1; then
  green "llama-server on PATH"
else
  bad "llama-server not found — Surya cannot run without it"
  MISSING+=(llama-server)
fi

# qa.py degrades gracefully without a word list, so this is advisory only.
if [ -r /usr/share/dict/words ]; then
  green "system word list (qa.py vocabulary check)"
else
  warn "no /usr/share/dict/words — qa.py will skip the vocabulary check"
fi

if [ ${#MISSING[@]} -gt 0 ]; then
  echo
  echo "to install what's missing:"
  pymissing=$(printf '%s\n' "${MISSING[@]}" | grep -v llama-server | tr '\n' ' ' || true)
  if [ -n "${pymissing// /}" ]; then
    echo "  pip install pillow numpy scipy surya-ocr"
  fi
  if printf '%s\n' "${MISSING[@]}" | grep -q llama-server; then
    echo "  brew install llama.cpp        # macOS / Linuxbrew"
    echo "  # or build the llama-server target from github.com/ggml-org/llama.cpp"
    echo "  # then either put it on PATH or set LLAMA_CPP_BINARY=/path/to/llama-server"
  fi
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  exit 0
fi

# ------------------------------------------------------------------- install
echo
echo "installing to $DEST"
mkdir -p "$DEST"

for skill in "${SKILLS[@]}"; do
  if [ ! -d "$SRC/$skill" ]; then
    bad "$skill missing from $SRC — run this from a checkout of the repo"
    exit 1
  fi
  if [ -e "$DEST/$skill" ] && [ "$FORCE" -eq 0 ]; then
    warn "$skill already installed — leaving it alone (use --force to overwrite)"
    continue
  fi
  rm -rf "${DEST:?}/$skill"
  cp -r "$SRC/$skill" "$DEST/$skill"
  find "$DEST/$skill" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  green "$skill"
done

# Cheap guard against a half-copy: the VLM skill is useless if it cannot see
# its sibling's scripts, and that failure would only surface at runtime.
if [ -d "$DEST/ocr-pages-vlm" ] && [ ! -d "$DEST/ocr-pages/scripts" ]; then
  bad "ocr-pages-vlm installed without ocr-pages/scripts — it will fail at runtime"
  exit 1
fi

echo
echo "done. Claude Code will pick these up in any new session."
echo "Try: \"OCR this\" with a photo of a printed page, or run the scripts directly:"
echo "  python $DEST/ocr-pages/scripts/split_spread.py PHOTO.jpg --out pageimgs/"
