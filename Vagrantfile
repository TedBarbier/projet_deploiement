Vagrant.configure("2") do |config|
  # Box compatible ARM64 (Apple Silicon) & AMD64 (Intel)
  config.vm.box = "hashicorp-education/ubuntu-24-04"

  # Network config: Forward ports
  # Caddy (HTTPS)
  config.vm.network "forwarded_port", guest: 443, host: 8443
  # Caddy (HTTP)
  config.vm.network "forwarded_port", guest: 80, host: 8081
  # API Direct (Debug)
  config.vm.network "forwarded_port", guest: 8080, host: 8082

  # Provider specific configuration
  config.vm.provider "vmware_desktop" do |v|
    v.gui = false
    v.memory = 4096
    v.cpus = 2
    v.allowlist_verified = true
  end

  config.vm.provider "virtualbox" do |v|
    v.gui = false
    v.memory = 4096
    v.cpus = 2
  end

  # Sycnhronize current folder to /project in VM
  config.vm.synced_folder "./orion-dynamic", "/project"

  # Provisioning Script (Inline Shell)
  config.vm.provision "shell", inline: <<-SHELL
    echo ">>> System Update & Docker Installation..."
    
    # Add Docker's official GPG key:
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    # Add the repository to Apt sources:
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    sudo apt-get update
    
    # Install Docker
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Add vagrant user to docker group
    sudo usermod -aG docker vagrant

    echo ">>> Starting Orion Project..."
    cd /project

    # Ensure .env exists (copy example if not present)
    if [ ! -f .env ]; then
      cp .env.example .env
      echo ">>> Created .env from example"
    fi

    echo ">>> Building & Starting Containers..."
    docker compose up -d --build

    echo ">>> DONE! Orion is running."
    echo "Access Web Interface at: https://localhost:8443"
  SHELL
end
