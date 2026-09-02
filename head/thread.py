import logging
import os
import socket
import subprocess
import sys
import threading
import time

from gevent import pywsgi

from head import config
from head.backup import backup
from head.server import app


logger = logging.getLogger(__name__)


# 等待指定端口启动
def wait_for_port(host, port, timeout=90):
    logger.info(f"Waiting for {host}:{port} ...")

    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=2):
                logger.info(f"{host}:{port} is ready.")
                return True
        except (ConnectionRefusedError, TimeoutError, OSError):
            time.sleep(1)

    logger.error(
        f"{host}:{port} did not become available within {timeout} seconds."
    )
    return False


# 创建 Reader 线程
def reader_thread():
    try:
        logger.info("Starting Reader Java service...")
        logger.info(f"Java path: {config.java_path}")

        process = subprocess.Popen(
            [config.java_path, "-jar", "reader-pro.jar"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        for line in process.stdout:
            logger.info(f"[Reader] {line.rstrip()}")

        return_code = process.wait()

        logger.error(
            f"Reader Java service exited with code: {return_code}"
        )

        sys.exit(return_code)

    except Exception as err:
        logger.exception(
            f"Unable to load Reader service: {err}"
        )
        sys.exit(1)


# 创建 Flask 线程
def flask_thread():
    app.run(
        host='0.0.0.0',
        debug=True,
        port=config.port
    )


# 创建 WSGI 线程
def wsgi_thread():
    port = config.port if config.port is not None else 5000

    logger.info(f"Starting WSGI server on 0.0.0.0:{port}")

    server = pywsgi.WSGIServer(
        ('0.0.0.0', port),
        app
    )

    server.serve_forever()


# 创建 Nginx 线程
def nginx_thread():
    try:
        nginx_config = "/reader/nginx/conf/nginx.conf"

        logger.info(
            f"Starting Nginx with config: {nginx_config}"
        )

        if not os.path.exists(nginx_config):
            raise FileNotFoundError(
                f"Nginx config not found: {nginx_config}"
            )

        process = subprocess.Popen(
            [
                "nginx",
                "-c",
                nginx_config,
                "-g",
                "daemon off;"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        for line in process.stdout:
            logger.info(f"[Nginx] {line.rstrip()}")

        return_code = process.wait()

        logger.error(
            f"Nginx exited with code: {return_code}"
        )

    except Exception as err:
        logger.exception(
            f"Unable to start Nginx: {err}"
        )


# 创建备份线程
def backup_thread():
    if config.AUTO_BACKUP:
        backup()
    else:
        logger.info("Doesn't OPEN AUTO_BACKUP.")


# 线程创建
t_flask = threading.Thread(
    name='flask',
    target=flask_thread,
    daemon=True
)

t_reader = threading.Thread(
    name='reader',
    target=reader_thread,
    daemon=True
)

t_nginx = threading.Thread(
    name='nginx',
    target=nginx_thread,
    daemon=True
)

t_wsgi = threading.Thread(
    name='wsgi',
    target=wsgi_thread,
    daemon=True
)

t_backup = threading.Thread(
    name='backup',
    target=backup_thread,
    daemon=True
)


# 线程启动
def thread_starter():

    # 1. 启动 Reader
    t_reader.start()

    # 2. 等待 Reader 8080
    if not wait_for_port("127.0.0.1", 8080, timeout=90):
        logger.error("Reader service failed to start.")
        return

    # 3. 启动备份
    t_backup.start()

    # 4. 启动 Flask / WSGI
    if config.DEBUG:
        t_flask.start()
    else:
        t_wsgi.start()

    # 5. 启动 Nginx
    t_nginx.start()

    # 6. 等待 Nginx 80
    if wait_for_port("127.0.0.1", 80, timeout=30):
        logger.info("Nginx is ready on port 80.")
    else:
        logger.error("Nginx failed to listen on port 80.")
