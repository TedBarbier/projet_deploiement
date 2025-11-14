FROM alpine:latest

# Installation des paquets
RUN apk add --no-cache openssh sudo python3 py3-pip py3-requests

# Configuration de SSH (pour root:password)
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config 
RUN echo 'root:password' | chpasswd
RUN /usr/bin/ssh-keygen -A
RUN ssh-keygen -t rsa -b 4096 -f  /etc/ssh/ssh_host_key

# Copier l'agent et le script de démarrage
COPY agent.py /usr/local/bin/agent.py
RUN chmod +x /usr/local/bin/agent.py

EXPOSE 22

# Lancer le script d'entrée
CMD ["sh", "-c", "python3 /usr/local/bin/agent.py & /usr/sbin/sshd -D"]

