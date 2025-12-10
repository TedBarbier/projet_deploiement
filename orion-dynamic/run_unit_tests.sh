#!/bin/bash
set -e

echo ">>> Setting up Test Environment..."
python3 -m venv venv_test
source venv_test/bin/activate

echo ">>> Installing Dependencies..."
pip install --upgrade pip
pip install -r control-plane/api/requirements.txt
pip install -r control-plane/scheduler/requirements.txt
pip install -r control-plane/autoscaler/requirements.txt
pip install -r requirements-test.txt

echo ">>> Running Tests..."
pytest -v tests/

echo ">>> Cleaning up..."
deactivate
rm -rf venv_test
echo "✅ Tests Completed."
