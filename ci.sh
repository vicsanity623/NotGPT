#!/bin/bash

set -ex -o pipefail

# Log some general info about the environment
echo "::group::Environment"
uname -a
env | sort
PROJECT='axiom_server'
ON_GITHUB_CI=true
echo "::endgroup::"

# If not running on Github's CI, discard the summaries
if [ -z "${GITHUB_STEP_SUMMARY+x}" ]; then
    GITHUB_STEP_SUMMARY=/dev/null
    ON_GITHUB_CI=false
fi

################################################################
# We have a Python environment!
################################################################

echo "::group::Versions"
python -c "import sys, struct; print('python:', sys.version); print('version_info:', sys.version_info); print('bits:', struct.calcsize('P') * 8)"
echo "::endgroup::"

echo "::group::Install dependencies"
python -m pip install -U pip tomli
python -m pip --version
UV_VERSION=$(python -c 'import tomli; from pathlib import Path; print({p["name"]:p for p in tomli.loads(Path("uv.lock").read_text())["package"]}["uv"]["version"])')
SPACY_VERSION=$(python -c 'import tomli; from pathlib import Path; print({p["name"]:p for p in tomli.loads(Path("uv.lock").read_text())["package"]}["spacy"]["version"])')
python -m pip install uv==$UV_VERSION
python -m uv --version

UV_VENV_SEED="pip"
python -m uv venv --seed --allow-existing

# Determine the platform and activate the virtual environment accordingly
case "$OSTYPE" in
  linux-gnu*|linux-musl*|darwin*)
    source .venv/bin/activate
    ;;
  cygwin*|msys*)
    source .venv/Scripts/activate
    ;;
  *)
    echo "::error:: Unknown OS. Please add an activation method for '$OSTYPE'."
    exit 1
    ;;
esac

# Install uv in virtual environment
python -m pip install uv==$UV_VERSION

# Prepare Linux System Dependencies
if [[ "${RUNNER_OS:-}" == "Linux" ]]; then
    if $ON_GITHUB_CI; then
        sudo apt-get update -q
        sudo apt-get install -y -q libxml2-dev libxslt1-dev
    fi
fi

if [ "$CHECK_FORMATTING" = "1" ]; then
    python -m uv sync --locked --extra tests --extra tools
else
    # Actual tests sync
    python -m uv sync --locked --extra tests
fi

# NOW: Install spacy from specific binaries if on Linux
# This happens AFTER uv sync so it doesn't get overwritten
if [[ "${RUNNER_OS:-}" == "Linux" ]]; then
    echo "::group::Overwriting spacy with Linux Optimized Binary"
    PYTHON_VERSION=$(python -c 'import sys; print("".join(map(str, sys.version_info[:2])))')
    
    # Use 'uv pip install' instead of 'uv add' to bypass the universal solver
    python -m uv pip install --force-reinstall "spacy @ https://github.com/explosion/spaCy/releases/download/release-v${SPACY_VERSION}/spacy-${SPACY_VERSION}-cp${PYTHON_VERSION}-cp${PYTHON_VERSION}-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
    
    # Make sure installation was successful
    SPACY_RUN_VERSION=$(python -c "import importlib.metadata; print(importlib.metadata.version('spacy'))")
    if [[ "${SPACY_RUN_VERSION}" != "${SPACY_VERSION}" ]]; then
        echo "::error:: spacy linux installation failed, version does not match expected."
        exit 1
    fi
    echo "::endgroup::"
fi

# Final setup and running
if [ "$CHECK_FORMATTING" = "1" ]; then
    source check.sh
else
    echo "::group::Setup for tests"
    mkdir empty || true
    cd empty
    python -m spacy download en_core_web_lg
    echo "::endgroup::"

    echo "::group:: Run Tests"
    if coverage run --rcfile=../pyproject.toml -m pytest -ra --junitxml=../test-results.xml ../tests --verbose --durations=10; then
        PASSED=true
    else
        PASSED=false
    fi
    echo "::endgroup::"

    echo "::group::Coverage"
    coverage combine --rcfile ../pyproject.toml
    coverage report -m --rcfile ../pyproject.toml
    coverage xml --rcfile ../pyproject.toml
    echo "::endgroup::"
    $PASSED
fi