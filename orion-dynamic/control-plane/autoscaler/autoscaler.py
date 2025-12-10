import docker
import time
import statistics
import logging
import os

# Configuration
SERVICE_NAME = os.getenv('SERVICE_NAME', 'api')
CHECK_INTERVAL = 5  # seconds
CPU_SCALE_UP_THRESHOLD = 50.0  # percent
CPU_SCALE_DOWN_THRESHOLD = 10.0  # percent
MIN_REPLICAS = 1
MAX_REPLICAS = 5
COOLDOWN_PERIOD = 30  # seconds

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

client = docker.from_env()

last_scale_time = 0

def get_cpu_usage(container):
    try:
        stats = container.stats(stream=False)
        
        cpu_stats = stats['cpu_stats']
        precpu_stats = stats['precpu_stats']
        
        # Check if we have valid data
        if not cpu_stats or not precpu_stats:
            return 0.0

        cpu_delta = cpu_stats['cpu_usage']['total_usage'] - precpu_stats['cpu_usage']['total_usage']
        system_cpu_delta = cpu_stats['system_cpu_usage'] - precpu_stats['system_cpu_usage']
        
        if system_cpu_delta > 0 and cpu_delta > 0:
            # CPU Usage calculation
            # usage = (cpu_delta / system_cpu_delta) * online_cpus * 100
            online_cpus = cpu_stats.get('online_cpus', 1) or len(cpu_stats['cpu_usage'].get('percpu_usage', [1]))
            return (cpu_delta / system_cpu_delta) * online_cpus * 100.0
        return 0.0
    except Exception as e:
        logger.error(f"Error getting stats for container {container.name}: {e}")
        return 0.0

def scale_service(replicas):
    global last_scale_time
    logger.info(f"Scaling service '{SERVICE_NAME}' to {replicas} replicas...")
    try:
        # We assume we are running in a docker compose project.
        # We can issue a command to docker compose.
        # Since we are inside a container, we mapped the docker socket.
        # The easiest way to scale a specific compose service programmatically via the SDK isn't direct.
        # However, we can use the 'docker compose' command if installed, OR just run more containers?
        # Running "docker compose up -d --scale api=N" is the standard way.
        # But we don't have 'docker compose' binary inside 'python:3.10-slim'.
        # We can install it or rely on the host?
        
        # Alternative: Use subprocess to call docker CLI if installed?
        # We only installed python docker sdk. 
        # Actually, let's just use os.system since we are mounting the socket, 
        # BUT we need the docker cli binary. 
        # Let's assume for this MVP we might need to install docker-cli in the generic Dockerfile
        # or... we can cheat. We can't easily scale a "compose service" just by starting containers with the SDK
        # simply, because Compose manages the network aliases and naming.
        
        # Let's update the Dockerfile to install docker-cli or docker-compose-plugin.
        pass 
    
    except Exception as e:
        logger.error(f"Failed to scale: {e}")

# RE-WRITING Scale Function for subprocess with installed docker cli
def scale_service_cmd(replicas):
    global last_scale_time
    logger.info(f"Scaling to {replicas} replicas.")
    project_name = os.getenv('PROJECT_NAME', 'orion-dynamic')
    # We execute in /project where we mount the source code
    cmd = f"cd /project && docker compose -p {project_name} up -d --scale {SERVICE_NAME}={replicas} --no-recreate {SERVICE_NAME}"
    # Note: --no-recreate prevents recreating existing containers, only adds/removes.
    
    # We need to run this command in the project directory. 
    # But inside the container, we don't have the project files unless mounted.
    # However, 'docker compose' needs the docker-compose.yml.
    # We should mount the docker-compose.yml into the autoscaler container too!
    
    ret = os.system(cmd)
    if ret == 0:
        logger.info("Scaling command executed successfully.")
        last_scale_time = time.time()
    else:
        logger.error("Scaling command failed.")

def main():
    logger.info(f"Starting Orion Autoscaler for service '{SERVICE_NAME}'...")
    
    # Needs to determine current project name to filter correctly? 
    # Usually 'com.docker.compose.project' label.
    
    while True:
        try:
            # list containers for the target service
            containers = client.containers.list(filters={"label": f"com.docker.compose.service={SERVICE_NAME}"})
            count = len(containers)
            
            if count == 0:
                logger.warning(f"No containers found matching label 'com.docker.compose.service={SERVICE_NAME}'. Waiting...")
                time.sleep(CHECK_INTERVAL)
                continue
            
            cpu_usages = []
            for c in containers:
                usage = get_cpu_usage(c)
                cpu_usages.append(usage)
            
            if not cpu_usages:
                 avg_cpu = 0
            else:
                avg_cpu = statistics.mean(cpu_usages)
                
            logger.info(f"Current Replicas: {count} | Avg CPU: {avg_cpu:.2f}% | Usages: {[round(u,2) for u in cpu_usages]}")
            
            now = time.time()
            if now - last_scale_time < COOLDOWN_PERIOD:
                time.sleep(CHECK_INTERVAL)
                continue
                
            new_count = count
            
            if avg_cpu > CPU_SCALE_UP_THRESHOLD:
                if count < MAX_REPLICAS:
                    new_count = count + 1
                    logger.info("Threshold exceeded (High Load). Scaling UP.")
            elif avg_cpu < CPU_SCALE_DOWN_THRESHOLD:
                if count > MIN_REPLICAS:
                    new_count = count - 1
                    logger.info("Threshold met (Low Load). Scaling DOWN.")
            
            if new_count != count:
                scale_service_cmd(new_count)
                
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
