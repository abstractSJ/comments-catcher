#!/usr/bin/env bash

set -Eeuo pipefail

SCOPE="user"
PROJECT_DIR="$(pwd -P)"
TARGET="auto"
TARGET_DIR=""
FORCE=0
SOURCE_DIR=""
INSTALL_ROOTS=()
TEMP_DIRS=()

print_usage() {
    cat <<'USAGE'
Usage: ./install.sh [options]

Options:
  --scope user|project   Install for the current user or a project.
  --project-dir PATH     Project root for --scope project.
  --target auto|codex|claude|both
                         Select native skill roots; auto is the default.
  --target-dir PATH      Install into this parent skill directory explicitly.
  --force                Replace an existing comments-catcher directory.
  -h, --help             Show this help.

Auto mode detects a shared .claude/skills junction or .agents/skills root.
When the two native roots are separate, it installs to both.
The script only copies the local skills/comments-catcher folder.
USAGE
}

cleanup() {
    local temp_dir
    for temp_dir in "${TEMP_DIRS[@]:-}"; do
        if [[ -n "$temp_dir" && -d "$temp_dir" ]]; then
            rm -rf -- "$temp_dir"
        fi
    done
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scope)
            [[ $# -ge 2 ]] || { printf 'Error: --scope needs a value.\n' >&2; exit 2; }
            SCOPE="$2"
            shift 2
            ;;
        --project-dir)
            [[ $# -ge 2 ]] || { printf 'Error: --project-dir needs a path.\n' >&2; exit 2; }
            PROJECT_DIR="$2"
            shift 2
            ;;
        --target)
            [[ $# -ge 2 ]] || { printf 'Error: --target needs a value.\n' >&2; exit 2; }
            TARGET="$2"
            shift 2
            ;;
        --target-dir)
            [[ $# -ge 2 ]] || { printf 'Error: --target-dir needs a path.\n' >&2; exit 2; }
            TARGET_DIR="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            printf 'Error: unknown option %s\n' "$1" >&2
            print_usage >&2
            exit 2
            ;;
    esac
done

[[ "$SCOPE" == "user" || "$SCOPE" == "project" ]] || {
    printf 'Error: --scope must be user or project.\n' >&2
    exit 2
}
[[ "$TARGET" == "auto" || "$TARGET" == "codex" || "$TARGET" == "claude" || "$TARGET" == "both" ]] || {
    printf 'Error: --target must be auto, codex, claude, or both.\n' >&2
    exit 2
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_DIR="$SCRIPT_DIR/skills/comments-catcher"

validate_skill_folder() {
    local skill_dir="$1"
    local relative_path
    local required_files=(
        "SKILL.md"
        "agents/openai.yaml"
        "scripts/comments_catcher.py"
        "references/setup.md"
        "references/cli-reference.md"
        "references/architecture.md"
        "references/safety-privacy.md"
        "references/troubleshooting.md"
        "references/output-schema-v1.json"
        "references/output-schema-v2.json"
    )

    [[ -d "$skill_dir" ]] || { printf 'Error: skill folder not found: %s\n' "$skill_dir" >&2; return 1; }
    for relative_path in "${required_files[@]}"; do
        [[ -f "$skill_dir/$relative_path" ]] || {
            printf 'Error: incomplete package, missing %s\n' "$skill_dir/$relative_path" >&2
            return 1
        }
    done
}

validate_skill_folder "$SOURCE_DIR"

remove_python_caches() {
    local root_path="$1"
    find "$root_path" -type d -name '__pycache__' -prune -exec rm -rf -- {} +
    find "$root_path" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
}

canonical_dir() {
    local path="$1"
    if [[ -d "$path" ]]; then
        (CDPATH= cd -- "$path" && pwd -P)
        return
    fi
    printf '%s\n' "$path"
}

add_root() {
    local candidate="$1"
    local canonical
    local existing
    canonical="$(canonical_dir "$candidate")"
    for existing in "${INSTALL_ROOTS[@]:-}"; do
        if [[ "$(canonical_dir "$existing")" == "$canonical" ]]; then
            return
        fi
    done
    INSTALL_ROOTS+=("$canonical")
}

if [[ -n "$TARGET_DIR" ]]; then
    add_root "$TARGET_DIR"
else
    if [[ "$SCOPE" == "user" ]]; then
        BASE_DIR="${USERPROFILE:-${HOME:?Unable to determine the user home directory}}"
        if [[ -n "${CODEX_HOME:-}" ]]; then
            CODEX_ROOT="$(canonical_dir "$CODEX_HOME/skills")"
        else
            CODEX_ROOT="$BASE_DIR/.agents/skills"
        fi
    else
        [[ -d "$PROJECT_DIR" ]] || { printf 'Error: project path not found: %s\n' "$PROJECT_DIR" >&2; exit 1; }
        BASE_DIR="$(CDPATH= cd -- "$PROJECT_DIR" && pwd -P)"
        CODEX_ROOT="$BASE_DIR/.agents/skills"
    fi
    CLAUDE_ROOT="$BASE_DIR/.claude/skills"

    case "$TARGET" in
        codex) add_root "$CODEX_ROOT" ;;
        claude) add_root "$CLAUDE_ROOT" ;;
        both)
            add_root "$CODEX_ROOT"
            add_root "$CLAUDE_ROOT"
            ;;
        auto)
            add_root "$CODEX_ROOT"
            add_root "$CLAUDE_ROOT"
            ;;
    esac
fi

DESTINATIONS=()
for root in "${INSTALL_ROOTS[@]}"; do
    DESTINATIONS+=("$root/comments-catcher")
done

for destination in "${DESTINATIONS[@]}"; do
    if [[ -e "$destination" && "$FORCE" -ne 1 ]]; then
        printf 'Error: target already exists: %s; use --force to replace it.\n' "$destination" >&2
        exit 1
    fi
done

for destination in "${DESTINATIONS[@]}"; do
    destination_parent="$(dirname -- "$destination")"
    mkdir -p -- "$destination_parent"
    temp_dir="$(mktemp -d "$destination_parent/.comments-catcher.install.XXXXXX")"
    TEMP_DIRS+=("$temp_dir")
    cp -R "$SOURCE_DIR/." "$temp_dir/"
    remove_python_caches "$temp_dir"
    validate_skill_folder "$temp_dir"
    if [[ -e "$destination" ]]; then
        rm -rf -- "$destination"
    fi
    mv -- "$temp_dir" "$destination"
    printf 'Installed comments-catcher to: %s\n' "$destination"
done

printf 'Installation completed. No remote content was downloaded or executed.\n'
