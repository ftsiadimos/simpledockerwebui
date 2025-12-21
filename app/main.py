"""Main routes and WebSocket handlers for LightDockerWebUI."""
import os
from weakref import WeakKeyDictionary

import docker
from bs4 import BeautifulSoup
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_sock import Sock
import requests
import logging

log = logging.getLogger(__name__)

from app import db  # Only import db, not app, to avoid circular import
from app.models import DockerServer
from app.forms import AddServerForm, SelectServerForm

main_bp = Blueprint('main', __name__)
sock = Sock()


def init_sock(app):
    """Initialize the WebSocket extension with the app."""
    sock.init_app(app)

# ------------------------------------------------------------------
# Per-socket working directory using WeakKeyDictionary for automatic
# cleanup when socket connections are garbage collected.
# ------------------------------------------------------------------
session_workdir = WeakKeyDictionary()

# Cache for Docker client to avoid reconnecting on every request
_docker_client_cache = {}

# In-memory cache for reachable container URLs: container.id -> (url_or_None, timestamp)
_REACHABLE_CACHE = {}

# Defaults for reachability checks
REACHABLE_TTL = 30.0  # seconds
CHECK_TIMEOUT_DEFAULT = 0.45
MAX_WORKERS_DEFAULT = 10

# Thread pool for background reachability checks
from concurrent.futures import ThreadPoolExecutor
_REACH_CHECKER = ThreadPoolExecutor(max_workers=MAX_WORKERS_DEFAULT)

import time


def check_http(host, port, timeout=CHECK_TIMEOUT_DEFAULT):
    url = f"http://{host}:{port}/"
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code < 400:
            return url
        r = requests.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code < 400:
            return url
    except requests.RequestException:
        return None
    return None


def check_http_and_cache(cid, host, port, timeout=CHECK_TIMEOUT_DEFAULT):
    try:
        url = check_http(host, port, timeout=timeout)
        _REACHABLE_CACHE[cid] = (url, time.time())
        return url
    except Exception:
        log.exception("check_http_and_cache failed for %s:%s", host, port)
        # Ensure we still cache the negative result to avoid repeated failures
        _REACHABLE_CACHE[cid] = (None, time.time())
        return None


def get_docker_base_url():
    """Get the Docker base URL from the active server configuration."""
    server = DockerServer.get_active()
    if not server or not server.is_configured:
        return 'unix://var/run/docker.sock', server
    return f"tcp://{server.host}:{server.port}", server


def conf(use_cache=True):
    """
    Return a Docker client and the Owner record.

    If the Owner record does not specify a host/port, connect to the
    local Docker daemon via the Unix socket.

    Args:
        use_cache: If True, reuse cached client for the same base_url.

    Raises:
        ValueError: If the Docker endpoint cannot be reached.
    """
    base_url, serverurl = get_docker_base_url()

    # Return cached client if available and valid
    if use_cache and base_url in _docker_client_cache:
        try:
            client = _docker_client_cache[base_url]
            client.ping()
            return client, serverurl
        except docker.errors.DockerException:
            # Client is stale, remove from cache
            _docker_client_cache.pop(base_url, None)

    try:
        client = docker.DockerClient(base_url=base_url, timeout=10)
        client.ping()
        if use_cache:
            _docker_client_cache[base_url] = client
    except docker.errors.DockerException as exc:
        raise ValueError(f"Cannot connect to Docker at {base_url}: {exc}") from exc

    return client, serverurl

@main_bp.route('/', methods=['GET'])
def index():
    """Display the main dashboard with all containers."""
    try:
        t0 = time.time()
        client, serverurl = conf()
        t1 = time.time()
        raw_containers = client.containers.list(all=True)
        t2 = time.time()

        # Map container.id -> reachable http url if any
        reachable = {}
        host_fallback = getattr(serverurl, 'host', None) or 'localhost'

        # Build lightweight container summaries
        containers = []
        for c in raw_containers:
            try:
                image_name = c.attrs.get('Config', {}).get('Image', '') if getattr(c, 'attrs', None) else ''
            except Exception:
                image_name = ''
            containers.append({
                'id': c.id,
                'name': getattr(c, 'name', '') or '',
                'status': getattr(c, 'status', '') or '',
                'ports': getattr(c, 'ports', {}) or {},
                'image': image_name,
            })
        t3 = time.time()

        # Fill reachable from cache only (fast)
        now = time.time()
        cached = 0
        for c in containers:
            entry = _REACHABLE_CACHE.get(c['id'])
            if entry:
                url, ts = entry
                if now - ts <= REACHABLE_TTL and url:
                    reachable[c['id']] = url
                    cached += 1
        t4 = time.time()

        # Schedule background reachability checks for containers missing a fresh cache
        prefer_ports = [80, 443, 8080, 8000, 3000]
        def _bg_check(cid, host, candidates):
            for p in candidates[:6]:
                url = check_http_and_cache(cid, host, p)
                if url:
                    break

        tasks_submitted = 0
        for c in containers:
            if c['id'] in reachable:
                continue
            ports_map = c.get('ports') or {}
            candidates = []
            for internal, hostports in ports_map.items():
                if not hostports:
                    continue
                for info in hostports:
                    hp = info.get('HostPort')
                    if not hp:
                        continue
                    try:
                        candidates.append(int(hp))
                    except Exception:
                        continue
            if not candidates:
                continue
            candidates = sorted(candidates, key=lambda x: (0 if x in prefer_ports else 1, prefer_ports.index(x) if x in prefer_ports else x))
            try:
                _REACH_CHECKER.submit(_bg_check, c['id'], host_fallback, candidates)
                tasks_submitted += 1
            except Exception:
                pass
        t5 = time.time()

        log.info("index timings: conf=%.3fs list=%.3fs build=%.3fs cache=%.3fs bgtasks=%.3fs (submitted=%d)",
                 t1-t0, t2-t1, t3-t2, t4-t3, t5-t4, tasks_submitted)

        return render_template('index.html', containers=containers, serverurl=serverurl, reachable=reachable)
    except ValueError as err:
        flash(str(err), 'warning')
        return redirect(url_for('main.addcon'))


@main_bp.route('/api/reachable/<container_id>', methods=['GET'])
def api_reachable(container_id):
    """Return cached reachable URL for a container, and schedule a background
    discovery+check if not present. This endpoint is intentionally non-blocking
    and will not perform Docker network calls on the request path."""
    entry = _REACHABLE_CACHE.get(container_id)
    now = time.time()
    if entry and now - entry[1] <= REACHABLE_TTL and entry[0]:
        return jsonify({'reachable': entry[0], 'cached': True})

    # Schedule background discovery and checks
    def _bg_discover_and_check(cid):
        try:
            client, serverurl = conf()
            c = client.containers.get(cid)
            ports_map = getattr(c, 'ports', {}) or {}
            candidates = []
            for internal, hostports in ports_map.items():
                if not hostports:
                    continue
                for info in hostports:
                    hp = info.get('HostPort')
                    if not hp:
                        continue
                    try:
                        candidates.append(int(hp))
                    except Exception:
                        continue
            if candidates:
                prefer_ports = [80, 443, 8080, 8000, 3000]
                candidates = sorted(candidates, key=lambda x: (0 if x in prefer_ports else 1, prefer_ports.index(x) if x in prefer_ports else x))
                for p in candidates[:6]:
                    url = check_http_and_cache(cid, serverurl.host or 'localhost', p)
                    if url:
                        break
        except Exception:
            log.exception('bg discover failed for %s', cid)

    try:
        _REACH_CHECKER.submit(_bg_discover_and_check, container_id)
    except Exception:
        pass

    return jsonify({'reachable': None, 'cached': False, 'scheduled': True})


@main_bp.route('/api/reachable/probe', methods=['POST'])
def api_reachable_probe():
    """Synchronously probe a host and a list of ports. This is intended for
    client-initiated progressive checks and uses a short timeout by default."""
    data = request.get_json(force=True, silent=True) or {}
    host = data.get('host')
    ports = data.get('ports') or []
    try:
        timeout = float(data.get('timeout', CHECK_TIMEOUT_DEFAULT))
    except Exception:
        timeout = CHECK_TIMEOUT_DEFAULT

    if not host or not ports:
        return jsonify({'reachable': None}), 400

    prefer_ports = [80, 443, 8080, 8000, 3000]
    candidates = sorted([int(p) for p in ports], key=lambda x: (0 if x in prefer_ports else 1, prefer_ports.index(x) if x in prefer_ports else x))
    for p in candidates[:6]:
        url = check_http(host, p, timeout=timeout)
        if url:
            return jsonify({'reachable': url})

    return jsonify({'reachable': None}), 404


@main_bp.route('/about', methods=['GET'])
def about():
    return render_template('about.html')


@main_bp.route('/addcon', methods=['GET', 'POST'])
def addcon():
    """Configure Docker server connections."""
    add_form = AddServerForm()
    select_form = SelectServerForm()
    
    # Get all servers and active server
    servers = DockerServer.query.all()
    active_server = DockerServer.get_active()
    
    # Populate server dropdown with display name and URL
    def get_server_label(s):
        if s.host and s.port:
            return f"{s.display_name} (tcp://{s.host}:{s.port})"
        elif s.host:
            return f"{s.display_name} ({s.host})"
        return f"{s.display_name} (local)"
    
    select_form.server.choices = [(s.id, get_server_label(s)) for s in servers]
    
    # Handle form submissions
    if request.method == 'POST':
        # Add new server
        if 'submit' in request.form and add_form.validate():
            new_server = DockerServer(
                display_name=add_form.display_name.data,
                host=add_form.host.data or None,
                port=add_form.port.data or None,
                is_active=len(servers) == 0  # First server is active by default
            )
            db.session.add(new_server)
            db.session.commit()
            _docker_client_cache.clear()
            flash(f'Server "{new_server.display_name}" added successfully.', 'success')
            return redirect(url_for('main.addcon'))
        
        # Select active server
        if 'submit_select' in request.form and select_form.validate():
            server = DockerServer.set_active(select_form.server.data)
            _docker_client_cache.clear()
            if server:
                flash(f'Connected to "{server.display_name}".', 'success')
            return redirect(url_for('main.index'))
        
        # Delete server
        if 'delete_server' in request.form:
            server_id = request.form.get('delete_server')
            server = db.session.get(DockerServer, server_id)
            if server:
                name = server.display_name
                was_active = server.is_active
                db.session.delete(server)
                db.session.commit()
                # If deleted server was active, activate another one
                if was_active:
                    remaining = DockerServer.query.first()
                    if remaining:
                        DockerServer.set_active(remaining.id)
                _docker_client_cache.clear()
                flash(f'Server "{name}" deleted.', 'success')
            return redirect(url_for('main.addcon'))
    
    # Set default selection to active server (only for GET requests)
    if active_server:
        select_form.server.data = active_server.id

    return render_template('addcon.html', 
                          add_form=add_form, 
                          select_form=select_form,
                          servers=servers,
                          active_server=active_server)



@main_bp.route("/logs", methods=["POST"])
def logs():
    """Display logs for a specific container."""
    container_id = request.form.get("logs")
    if not container_id:
        flash('No container specified.', 'warning')
        return redirect(url_for('main.index'))

    try:
        t0 = time.time()
        client, _ = conf()
        t1 = time.time()
        container = client.containers.get(container_id)
        t2 = time.time()
        # Get last 1000 lines to avoid memory issues with large logs
        log_output = container.logs(tail=1000, timestamps=True)
        t3 = time.time()

        # Ensure we work with a decoded string in templates
        try:
            if isinstance(log_output, (bytes, bytearray)):
                logs_text = log_output.decode('utf-8', errors='replace')
            else:
                logs_text = str(log_output)
        except Exception as e:
            logs_text = ''
            log.exception('Failed to decode logs for %s', container_id)
        t4 = time.time()

        # Log timings and size
        lines = len(logs_text.splitlines())
        log.info("logs: container=%s lines=%d timings: conf=%.3fs get=%.3fs fetch=%.3fs decode=%.3fs",
                 container_id, lines, t1-t0, t2-t1, t3-t2, t4-t3)

        return render_template('logs.html', logs_text=logs_text)

    except docker.errors.NotFound:
        flash(f'Container {container_id} not found.', 'danger')
        return redirect(url_for('main.index'))
    except docker.errors.APIError as e:
        flash(f'Error fetching logs: {e}', 'danger')
        return redirect(url_for('main.index'))


@main_bp.route("/comma", methods=["POST"])
def comma():
    """Open a terminal session for a container."""
    container_id = request.form.get("comma")
    if not container_id:
        flash('No container specified.', 'warning')
        return redirect(url_for('main.index'))

    try:
        client, _ = conf()
        container = client.containers.get(container_id)
        return render_template('soc.html', id=container.id)
    except docker.errors.NotFound:
        flash(f'Container {container_id} not found.', 'danger')
        return redirect(url_for('main.index'))
    except docker.errors.APIError as e:
        flash(f'Error accessing container: {e}', 'danger')
        return redirect(url_for('main.index'))



def _handle_builtin_command(sock, data):
    """Handle built-in shell commands (cd, pwd, clear, ls, cat, echo, help, exit).

    Returns:
        True if command was handled, False otherwise.
    """
    # Help command
    if data in ('help', '?'):
        sock.send(
            """
Available commands:
  clear         - Clear the terminal
  pwd           - Print working directory
  cd            - Change directory
  ls            - List directory contents
  cat           - Show file contents
  echo          - Print text
  exit          - Close terminal session
  help, ?       - Show this help message
All other commands are executed inside the container.
            """
        )
        return True

    # Exit command
    if data == 'exit':
        sock.send('Session closed. Bye!')
        try:
            sock.close()
        except Exception:
            pass
        return True

    # Clear command
    if data == 'clear':
        sock.send('__CLEAR__')
        return True

    # Print working directory
    if data == 'pwd':
        sock.send(session_workdir.get(sock, '/'))
        return True

    # Change directory
    if data.startswith('cd '):
        target = data[3:].strip()
        current = session_workdir.get(sock, '/')
        new_dir = os.path.normpath(os.path.join(current, target))
        session_workdir[sock] = new_dir
        sock.send(f'Changed directory to {new_dir}')
        return True

    # Echo command
    if data.startswith('echo '):
        sock.send(data[5:].strip())
        return True

    # ls command (list directory)
    if data.startswith('ls'):
        # Accept 'ls' or 'ls <dir>'
        parts = data.split(maxsplit=1)
        target_dir = parts[1].strip() if len(parts) > 1 else session_workdir.get(sock, '/')
        # Use Docker exec to run ls
        try:
            client, _ = conf()
            container_id = request.args.get("id")
            container = client.containers.get(container_id)
            container.reload()
            result = container.exec_run(
                f'ls -al {target_dir}',
                workdir=session_workdir.get(sock, '/'),
                stdout=True,
                stderr=True,
                demux=False
            )
            output = _decode_output(result.output)
            sock.send(output if output else '(empty)')
        except Exception as e:
            sock.send(f'ls error: {e}')
        return True

    # cat command (show file contents)
    if data.startswith('cat '):
        file_path = data[4:].strip()
        try:
            client, _ = conf()
            container_id = request.args.get("id")
            container = client.containers.get(container_id)
            container.reload()
            result = container.exec_run(
                f'cat {file_path}',
                workdir=session_workdir.get(sock, '/'),
                stdout=True,
                stderr=True,
                demux=False
            )
            output = _decode_output(result.output)
            sock.send(output if output else '(empty)')
        except Exception as e:
            sock.send(f'cat error: {e}')
        return True

    return False


def _decode_output(output):
    """Safely decode container output to string."""
    if isinstance(output, bytes):
        try:
            return output.decode('utf-8', errors='replace')
        except Exception:
            return BeautifulSoup(output, 'html.parser').get_text()
    return str(output)


@sock.route('/echo')
def echo(sock):
    """
    WebSocket handler for container terminal sessions.

    Supports built-in commands: cd, pwd, clear.
    All other commands are executed inside the container.
    """
    container_id = request.args.get("id")
    if not container_id:
        sock.send('Error: No container ID provided')
        return

    # Initialize working directory for this socket
    session_workdir[sock] = '/'

    # Get client once for this session
    try:
        client, _ = conf()
        container = client.containers.get(container_id)
    except (ValueError, docker.errors.NotFound) as e:
        sock.send(f'Error: {e}')
        return

    while True:
        try:
            data = sock.receive()
        except Exception:
            break  # Connection closed or error

        if data is None:
            break

        data = data.strip()
        if not data:
            continue

        # Handle built-in commands
        if _handle_builtin_command(sock, data):
            continue

        # Execute command in container
        try:
            # Refresh container reference in case it was restarted
            container.reload()
            result = container.exec_run(
                data,
                workdir=session_workdir.get(sock, '/'),
                stdout=True,
                stderr=True,
                demux=False
            )
            output = _decode_output(result.output)
            sock.send(output if output else '(no output)')
        except docker.errors.APIError as e:
            sock.send(f'Docker API Error: {e}')
        except Exception as e:
            sock.send(f'Error: {e}')


@main_bp.route("/submitadmin", methods=["POST"])
def submit_remove():
    """Handle container management actions (start, stop, restart, delete)."""
    action = request.form.get("submit_button")
    container_ids = request.form.getlist("interests")

    if not container_ids:
        flash('No containers selected.', 'warning')
        return redirect(url_for('main.index'))

    action_map = {
        "Delete": (lambda c: c.remove(force=True), "deleted"),
        "Start": (lambda c: c.start(), "started"),
        "Restart": (lambda c: c.restart(), "restarted"),
        "Stop": (lambda c: c.stop(), "stopped"),
    }

    if action not in action_map:
        flash('Invalid action specified.', 'danger')
        return redirect(url_for('main.index'))

    try:
        client, _ = conf()
        handler, past_tense = action_map[action]
        success_count = 0
        errors = []

        for cid in container_ids:
            try:
                container = client.containers.get(cid)
                handler(container)
                success_count += 1
            except docker.errors.NotFound:
                errors.append(f"Container {cid[:12]} not found")
            except docker.errors.APIError as e:
                errors.append(f"Container {cid[:12]}: {e}")

        if success_count:
            flash(f'{success_count} container(s) {past_tense}.', 'success')
        for error in errors:
            flash(error, 'danger')

    except ValueError as e:
        flash(str(e), 'danger')

    return redirect(url_for('main.index'))