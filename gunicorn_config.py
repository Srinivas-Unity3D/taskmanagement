import multiprocessing
import os

# Server socket
bind = "0.0.0.0:" + os.getenv("PORT", "5000")
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = 'eventlet'
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = 'access.log'
errorlog = 'error.log'
loglevel = 'info'

# Process naming
proc_name = 'task_management'

# Server mechanics
daemon = False
pidfile = 'task_management.pid'
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL
keyfile = None
certfile = None

# Hook
def when_ready(server):
    server.log.info("Server is ready. Spawning workers") 