#!/usr/bin/env bash
# One entry point from a clean Ubuntu host to the complete Kubernetes stack.
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_root"
environment_name=ubuntu
while (( $# )); do
    case "$1" in
        --env)
            if (( $# < 2 )); then
                echo '--env requires an environment name' >&2
                exit 2
            fi
            environment_name=$2
            shift 2
            ;;
        -h|--help)
            echo 'Usage: bash scripts/deploy.sh [--env NAME]'
            echo 'Installs controller tools, bootstraps Ubuntu/Kubernetes and deploys the complete stack.'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done
if [[ ! $environment_name =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo 'Environment name must contain only letters, digits, underscores or hyphens.' >&2
    exit 2
fi
if [[ ! -f config/environments/$environment_name.local.yaml ]]; then
    echo "Configure config/environments/$environment_name.local.yaml from ubuntu.example.yaml before deploying." >&2
    exit 2
fi
bash scripts/setup-tools.sh
exec .venv/bin/python scripts/deploy.py --env "$environment_name"
