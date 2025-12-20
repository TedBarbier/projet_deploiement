#!/bin/bash

# Configuration
VENV_DIR="venv_test"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}>>> Setting up Test Environment...${NC}"

# 1. Create venv if missing
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "Virtualenv created."
fi

# 2. Activate
source "$VENV_DIR/bin/activate"

# 3. Install deps
echo "Installing dependencies..."
pip install -U pip > /dev/null
pip install -r requirements-test.txt > /dev/null

# 4. Run Tests
echo -e "\n${GREEN}>>> Running Tests...${NC}"
# Add control-plane paths to PYTHONPATH so tests can import api.py
export PYTHONPATH=$PYTHONPATH:$(pwd)/control-plane/api:$(pwd)/control-plane/scheduler:$(pwd)/control-plane/autoscaler

pytest tests -v --cov=control-plane/api --cov=control-plane/scheduler --cov=control-plane/autoscaler --cov-report term-missing --cov-report=xml

RET=$?
if [ $RET -eq 0 ]; then
    echo -e "\n${GREEN}[SUCCESS] All tests passed!${NC}"
else
    echo -e "\n${RED}[FAIL] Some tests failed.${NC}"
fi

exit $RET
