FROM python:3.10-slim

# Installer les dépendances système pour Ansible
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    sshpass \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ./control-plane/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ./control-plane/api .

# Copier les playbooks Ansible dans l'image
COPY ./ansible /ansible

# --- CORRECTION AJOUTÉE ---
# "Nettoie" le fichier api.py en remplaçant les espaces insécables (U+00A0)
# par des espaces normaux (ASCII 32).
RUN sed -i 's/\xc2\xa0/ /g' api.py
# -------------------------

# Laisser des fichiers vides pour que Gunicorn démarre (n'est plus nécessaire)
# RUN touch api.py

# --- MODIFIER CETTE LIGNE ---
#CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8080", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "debug", "api:app"]
CMD ["python3", "api.py"]