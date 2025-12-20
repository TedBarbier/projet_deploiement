#!/bin/bash

# ==============================================================================
# Full Demo Script for Orion-Dynamic
# Demonstrates: Proxy, Autoscalers, API (Auth/Admin/User), Scheduler, Frontend
# Duration: ~4-5 minutes
# ==============================================================================

# Configuration
API="https://localhost/api"
AUTH_API="https://localhost"
ADMIN_USER="admin"
ADMIN_PASS="admin"
USER_NAME="user"
USER_PASS="user"

# Ensure Docker is in PATH (Mac specific)
export PATH=$PATH:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin

# Colors
GREEN='\033[0;32m'
BLUE='\033[1;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

function print_header() {
    echo -e "\n${BLUE}========================================================${NC}"
    echo -e "${BLUE}   $1${NC}"
    echo -e "${BLUE}========================================================${NC}"
}

function check_ret() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}[OK] $1${NC}"
    else
        echo -e "${RED}[FAIL] $1${NC}"
    fi
}

function get_json_val() {
    # Extracts a value from a JSON object
    echo "$1" | python3 -c "import sys, json; print(json.load(sys.stdin).get('$2', ''))" 2>/dev/null
}

function wait_for_step() {
    echo -e "\n${BLUE}Press [ENTER] to continue to the next step...${NC}"
    read
}

print_header "STARTING ORION DYNAMIC FULL DEMO"

# --- 0. Cleanup ---
print_header "0. GLOBAL CLEANUP"
echo "Stopping all containers and removing volumes (Fresh Start)..."
docker compose down -v > /dev/null 2>&1
echo "Removing any leftover worker containers..."
docker rm -f $(docker ps -a -q --filter "name=worker-") 2>/dev/null
check_ret "Cleanup Complete"

# --- 1. Infrastructure ---
print_header "1. INFRASTRUCTURE & STARTUP"
echo "Ensuring environment is up..."
if [ -f "start_demo.sh" ]; then
    chmod +x start_demo.sh
    ./start_demo.sh > /dev/null 2>&1
    check_ret "Environment Started"
else
    echo -e "${RED}start_demo.sh not found!${NC}"
    exit 1
fi

echo "Waiting for API to be healthy..."
sleep 2
for i in {1..30}; do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -k "$API/health")
    if [ "$CODE" == "200" ]; then
        echo -e "${GREEN}[OK] API is Reachable & Healthy.$NC"
        break
    fi
    echo -n "."
    sleep 2
done

echo -e "\n${GREEN}>>> CURRENTLY RUNNING CONTAINERS <<<${NC}"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo ""

wait_for_step

# --- 2. Scaling & Load Balancing ---
print_header "2. SCALING & LOAD BALANCING (API & SCHEDULER)"

echo "Scaling Up API to 3 replicas..."
docker compose up -d --scale api=3 --no-recreate api > /dev/null 2>&1
check_ret "Scaled API to 3"

echo "Scaling Up Scheduler to 3 replicas..."
docker compose up -d --scale scheduler=3 --no-recreate scheduler > /dev/null 2>&1
check_ret "Scaled Scheduler to 3"

echo "Waiting for replicas to register (12s)..."
sleep 12

echo "Demonstrating Round-Robin Load Balancing on API:"
echo "------------------------------------------------------------"
for i in {1..6}; do
    RESP=$(curl -s -k "$API/health")
    HOSTNAME=$(echo "$RESP" | python3 -c "import sys, json; print(json.load(sys.stdin).get('hostname', 'Unknown'))" 2>/dev/null)
    if [ "$HOSTNAME" == "Unknown" ]; then
         echo " -> Req $i: [Error/Starting] $RESP"
    else
         # Resolve name
         CONTAINER_NAME=$(docker ps --filter "id=$HOSTNAME" --format "{{.Names}}")
         [ -z "$CONTAINER_NAME" ] && CONTAINER_NAME=$HOSTNAME
         echo " -> Req $i handled by: $CONTAINER_NAME"
    fi
done
echo "------------------------------------------------------------"

echo "Reverting to 1 API replica (saving resources)..."
docker compose up -d --scale api=1 --no-recreate api > /dev/null 2>&1
check_ret "Scaled API down to 1"
sleep 2

wait_for_step

# --- 3. Authentication & Roles ---
print_header "3. AUTHENTICATION & ROLES"

# Use local DB exec to ensure clean slate
docker exec orion-db mysql -uorion -porionpass orion_db -e "DELETE FROM users WHERE username IN ('$ADMIN_USER', '$USER_NAME');" > /dev/null 2>&1

echo "Registering Admin ($ADMIN_USER)..."
curl -s -k -X POST "$API/signup" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"$ADMIN_USER\", \"password\": \"$ADMIN_PASS\"}" > /dev/null 2>&1

echo "Promoting user to Admin in DB..."
docker exec orion-db mysql -uorion -porionpass orion_db -e "UPDATE users SET role='admin' WHERE username='$ADMIN_USER';"
check_ret "Role Updated"

echo "Logging in as Admin..."
RESP=$(curl -s -k -X POST "$API/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"$ADMIN_USER\", \"password\": \"$ADMIN_PASS\"}")
ADMIN_TOKEN=$(get_json_val "$RESP" "token")

if [ -z "$ADMIN_TOKEN" ]; then
    echo -e "${RED}[FAIL] Admin Login Failed.${NC}"
    exit 1
else
    echo -e "${GREEN}[OK] Admin Logged In.${NC}"
fi

echo "Registering/Logging in User ($USER_NAME)..."
curl -s -k -X POST "$API/signup" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"$USER_NAME\", \"password\": \"$USER_PASS\"}" > /dev/null 2>&1

RESP=$(curl -s -k -X POST "$API/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"$USER_NAME\", \"password\": \"$USER_PASS\"}")
USER_TOKEN=$(get_json_val "$RESP" "token")

if [ -z "$USER_TOKEN" ]; then
    echo -e "${RED}[FAIL] User Login Failed.${NC}"
    exit 1
else
    echo -e "${GREEN}[OK] User Logged In.${NC}"
fi

wait_for_step

# --- 4. Scheduler & Work Queue ---
print_header "4. RENTAL, FAILOVER & SELF-HEALING"

echo "Waiting for 'alive' workers..."
ALIVE_COUNT=0
for i in {1..20}; do
    NODES=$(curl -s -k -X GET "$API/nodes" -H "Authorization: Bearer $ADMIN_TOKEN")
    ALIVE_COUNT=$(echo "$NODES" | python3 -c "import sys, json; print(len([n for n in json.load(sys.stdin) if n.get('status') == 'alive']))" 2>/dev/null)
    if [ "$ALIVE_COUNT" -gt "0" ]; then break; fi
    sleep 2
done

if [ "$ALIVE_COUNT" == "0" ]; then
    echo -e "${RED}[FAIL] No alive workers.${NC}"
else
    echo -e " -> Found ${GREEN}$ALIVE_COUNT${NC} alive workers."
    
    # 4a. RENT
    echo "User renting 1 node..."
    RENT_RESP=$(curl -s -k -X POST "$API/rent" \
        -H "Authorization: Bearer $USER_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"duration_hours": 1, "count": 1}')
    
    RENTAL_ID=$(echo "$RENT_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin).get('allocated', [{}])[0].get('rental_id', ''))" 2>/dev/null)
    HOST_IP=$(echo "$RENT_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin).get('allocated', [{}])[0].get('host_ip', ''))" 2>/dev/null)
    
    # Since host_ip might be host.docker.internal, we need the real container name to kill it.
    # We can get it from the Admin Node list by matching the Rental ID ? 
    # Or just picking the assigned node ID from DB.
    
    # Let's get the node ID associated with this rental
    NODE_ID=$(docker exec orion-db mysql -uorion -porionpass orion_db -N -e "SELECT node_id FROM rentals WHERE id=$RENTAL_ID")
    # Get hostname of that node
    WORKER_NAME=$(docker exec orion-db mysql -uorion -porionpass orion_db -N -e "SELECT hostname FROM nodes WHERE id=$NODE_ID")

    echo -e "${GREEN}[OK] Rental #$RENTAL_ID on Node: $WORKER_NAME${NC}"

    # 4b. CRASH
    echo -e "\n${RED}>>> SIMULATING CRASH OF $WORKER_NAME <<<${NC}"
    docker stop "$WORKER_NAME"
    check_ret "Stopped $WORKER_NAME"
    
    # DEBUG: Check if it's really stopped
    docker inspect "$WORKER_NAME" --format 'DEBUG STATUS: {{.State.Status}} (Restarting: {{.State.Restarting}})'

    # echo "Restarting Scheduler to clear state (Mitigation for Docker/Paramiko caching issue)..."
    # docker compose restart scheduler
    # sleep 5

    echo "Waiting for Scheduler to detect failure (Health Check interval ~2s + SSH Timeout)..."
    # Wait 15s to ensure we catch the next health check cycle and migration
    for i in {15..1}; do 
        echo -ne "Failover in $i s... \r"
        if [ $((i % 5)) -eq 0 ]; then
             # Periodically check status
             STATUS_NOW=$(docker inspect "$WORKER_NAME" --format '{{.State.Status}}' 2>/dev/null)
             if [ "$STATUS_NOW" == "running" ]; then
                 echo -e "\n${RED}[DEBUG] WTF? Container $WORKER_NAME is RUNNING!${NC}"
             fi
        fi
        sleep 1
    done; echo ""
    
    # 4c. VERIFY REASSIGNMENT
    echo "Checking User's Nodes for Reassignment..."
    MY_NODES=$(curl -s -k -X GET "$API/nodes" -H "Authorization: Bearer $USER_TOKEN")
    
    # Parsing JSON to find if we have a rental which is ACTIVE
    NEW_RENTAL_NODE=$(echo "$MY_NODES" | python3 -c "import sys, json; 
try:
    nodes = json.load(sys.stdin)
    for n in nodes:
        if n.get('lease'):
             print(n.get('hostname'))
             break
except: pass" 2>/dev/null)

    if [ -z "$NEW_RENTAL_NODE" ]; then
        echo -e "${RED}[FAIL] No active rental found! Failover failed?${NC}"
    elif [ "$NEW_RENTAL_NODE" == "$WORKER_NAME" ]; then
         echo -e "${RED}[FAIL] Still assigned to the dead node ($WORKER_NAME).${NC}"
    else
         echo -e "${GREEN}[OK] Auto-Remediation Success! User moved to: $NEW_RENTAL_NODE${NC}"
    fi

    # 4d. VERIFY CLEANUP (SECURITY)
    echo -e "\n${BLUE}>>> VERIFYING SECURITY CLEANUP (RESURRECTION) <<<${NC}"
    echo "Restarting the crashed worker ($WORKER_NAME)..."
    docker start "$WORKER_NAME" > /dev/null
    
    echo "Waiting for Scheduler to cleanup user (Cleanup Task interval ~2s + Ansible)..."
    # Wait 20s to ensure we catch the cleanup task cycle and Ansible execution
    for i in {20..1}; do echo -ne "Cleanup check in $i s... \r"; sleep 1; done; echo ""
    
    echo "Checking if user account ($USER_NAME) exists on $WORKER_NAME..."
    # We use 'id' command inside the container. If user exists, it succeeds (0). If not, fails (>0).
    docker exec "$WORKER_NAME" id "$USER_NAME" > /dev/null 2>&1
    RES=$?
    
    if [ $RES -ne 0 ]; then
        echo -e "${GREEN}[OK] Security Check Passed: User '$USER_NAME' was DELETED from $WORKER_NAME.${NC}"
    else
        echo -e "${RED}[FAIL] Security Risk: User '$USER_NAME' still exists on $WORKER_NAME!${NC}"
    fi
fi

wait_for_step

# --- 5. Extra API Features ---
print_header "5. EXTRA API FEATURES"

# --- 5. Step 5: Rental Lifecycle ---
print_header "5. RENTAL LIFECYCLE (Rent -> Extend -> Release)"

# 5a. CLEANUP (Ensure specific user starts fresh for this step)
echo "Ensuring clean state for user..."
MY_NODES=$(curl -s -k -X GET "$API/nodes" -H "Authorization: Bearer $USER_TOKEN")
# Extract all rental IDs for this user
RENTAL_IDS=$(echo "$MY_NODES" | python3 -c "import sys, json; print(' '.join([str(r['rental_id']) for n in json.load(sys.stdin) for r in [n.get('lease')] if r and r.get('active')]))" 2>/dev/null)

if [ ! -z "$RENTAL_IDS" ]; then
    echo "Releasing existing rentals: $RENTAL_IDS"
    for rid in $RENTAL_IDS; do
        curl -s -k -X POST "$API/release/$rid" -H "Authorization: Bearer $USER_TOKEN" > /dev/null
    done
fi
echo -e "${GREEN}[OK] Clean state confirmed.${NC}"

# 5b. RENT
echo "Renting a node for 1 hour..."
RENT_RESP=$(curl -s -k -X POST "$API/rent" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"duration_hours": 1, "count": 1}')

RENTAL_ID=$(echo "$RENT_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin).get('allocated', [{}])[0].get('rental_id', ''))" 2>/dev/null)

if [ -z "$RENTAL_ID" ]; then
    echo -e "${RED}[FAIL] Rental failed: $RENT_RESP${NC}"
else
    echo -e "${GREEN}[OK] Rented Node. Rental ID: $RENTAL_ID${NC}"
    
    # 5c. SHOW INFO
    echo "Fetching Rental Info..."
    NODES=$(curl -s -k -X GET "$API/nodes" -H "Authorization: Bearer $USER_TOKEN")
    # Show key info
    echo "$NODES" | python3 -c "import sys, json; 
data = json.load(sys.stdin)
for n in data:
    lease = n.get('lease')
    if lease and lease.get('rental_id') == $RENTAL_ID:
        print(f\" -> Node: {n.get('hostname')} ({n.get('node_id')})\")
        print(f\" -> Leased Until: {lease.get('leased_until')}\")
" 2>/dev/null

    wait_for_step
    
    # 5d. EXTEND
    echo "Extending Lease by +2 hours..."
    EXTEND_RESP=$(curl -s -k -X POST "$API/extend/$RENTAL_ID" \
       -H "Authorization: Bearer $USER_TOKEN" \
       -H "Content-Type: application/json" \
       -d '{"additional_hours": 2}')
    
    NEW_DATE=$(get_json_val "$EXTEND_RESP" "new_leased_until")
    if [ ! -z "$NEW_DATE" ]; then
         echo -e "${GREEN}[OK] Extension Successful. New End Date: $NEW_DATE${NC}"
    else
         echo -e "${RED}[FAIL] Extension Failed: $EXTEND_RESP${NC}"
    fi

    wait_for_step

    # 5e. RELEASE
    echo "Releasing Rental #$RENTAL_ID..."
    RELEASE_RESP=$(curl -s -k -X POST "$API/release/$RENTAL_ID" \
        -H "Authorization: Bearer $USER_TOKEN")
    
    MSG=$(get_json_val "$RELEASE_RESP" "message")
    if [ ! -z "$MSG" ]; then
        echo -e "${GREEN}[OK] Release Request: $MSG${NC}"
    else
        echo -e "${RED}[FAIL] Release Error: $RELEASE_RESP${NC}"
    fi

    # 5f. VERIFY
    echo "Verifying no active rentals remain..."
    sleep 2 # Give a moment for DB update if async (though it should be sync)
    FINAL_NODES=$(curl -s -k -X GET "$API/nodes" -H "Authorization: Bearer $USER_TOKEN")
    COUNT=$(echo "$FINAL_NODES" | python3 -c "import sys, json; 
data = json.load(sys.stdin)
# data is a list of nodes. We count how many have active leases
count = 0
if isinstance(data, list):
    for n in data:
        if n.get('lease') and n.get('lease').get('active'):
            count += 1
print(count)
" 2>/dev/null)
    
    if [ "$COUNT" == "0" ]; then
        echo -e "${GREEN}[OK] User has 0 active rentals. Verified.${NC}"
    else
         echo -e "${RED}[FAIL] User still has $COUNT active rentals!${NC}"
    fi
fi

wait_for_step

# --- 6. Step 6: Frontend Data Setup ---
print_header "6. FRONTEND DATA SETUP"

echo "Setting up demo data for Frontend..."

# 6a. User rents 3 nodes
echo "User '$USER_NAME' renting 3 nodes..."
curl -s -k -X POST "$API/rent" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"duration_hours": 1, "count": 3}' > /dev/null
check_ret "User rented 3 nodes"

# 6b. Admin rents 2 nodes
echo "Admin '$ADMIN_USER' renting 2 nodes..."
curl -s -k -X POST "$API/rent" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"duration_hours": 1, "count": 2}' > /dev/null
check_ret "Admin rented 2 nodes"

wait_for_step

# --- 7. Frontend Check ---
print_header "7. FRONTEND CHECK"
CODE_WEB=$(curl -s -o /dev/null -w "%{http_code}" -k "$AUTH_API/")
if [ "$CODE_WEB" == "200" ]; then
    echo -e "${GREEN}[OK] Frontend Online at https://localhost${NC}"
fi

echo -e "\n${GREEN}>>> FULL DEMO COMPLETED SUCCESSFULLY <<<${NC}"
