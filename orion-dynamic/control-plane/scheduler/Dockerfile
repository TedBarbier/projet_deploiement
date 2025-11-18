FROM python:3.10-slim

# Installer les dépendances système pour Paramiko et Ansible
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    sshpass \
    openssh-client \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ./control-plane/scheduler/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ./control-plane/scheduler .

# Copier les playbooks Ansible dans l'image
COPY ./ansible /ansible

# Laisser un fichier vide pour que le build passe
RUN touch scheduler.py

CMD ["python", "scheduler.py"]