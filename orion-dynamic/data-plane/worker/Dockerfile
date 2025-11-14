FROM alpine:latest

# Installation des paquets
RUN apk add --no-cache openssh-server sudo python3 py3-pip py3-requests
RUN addgroup --system sudo || true

# Configuration de SSH (pour root:password)
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config
RUN echo 'root:password' | chpasswd
RUN ssh-keygen -A

# Copier l'agent et le script de démarrage
COPY agent.py /usr/local/bin/agent.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/agent.py

EXPOSE 22

# Lancer le script d'entrée
CMD ["/entrypoint.sh"]
