FROM python:3.10-slim

# Dépendances système pour Ansible
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    sshpass \
    openssh-client \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*



WORKDIR /app

# Installer les dépendances Python de l’API
COPY ./control-plane/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code API
COPY ./control-plane/api .

# Copier les playbooks Ansible
COPY ./ansible /ansible

# Nettoyer les espaces insécables
RUN sed -i 's/\xc2\xa0/ /g' api.py

# Lancer Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8080", "api:app"]

