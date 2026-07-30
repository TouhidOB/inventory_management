"""Gunicorn production config for StockWhisk.

Run:  gunicorn -c deploy/gunicorn.conf.py config.wsgi:application
Tune workers with the WEB_CONCURRENCY env var (default: 2*CPU + 1).
"""
import multiprocessing
import os

# Bind to a unix socket (nginx proxies to it). Use 0.0.0.0:8000 for TCP instead.
bind = os.getenv("GUNICORN_BIND", "unix:/run/stockwhisk/gunicorn.sock")

workers = int(os.getenv("WEB_CONCURRENCY", multiprocessing.cpu_count() * 2 + 1))
worker_class = "sync"          # switch to "gthread" + threads=N for I/O-bound loads
threads = int(os.getenv("GUNICORN_THREADS", "1"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5

# Recycle workers to bound memory growth under sustained load.
max_requests = 1000
max_requests_jitter = 100

# Log to stdout/stderr so journald/systemd captures everything.
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")
# Real client IP from nginx (X-Forwarded-For).
forwarded_allow_ips = "*"
proc_name = "stockwhisk"
