"""Main routes and WebSocket handlers for LightDockerWebUI."""
import os
from weakref import WeakKeyDictionary

import docker
from bs4 import BeautifulSoup
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_sock import Sock

from app import db
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
        client, serverurl = conf()
        doc = client.containers.list(all=True)
        return render_template('index.html', doc=doc, serverurl=serverurl)
    except ValueError as err:
        flash(str(err), 'warning')
        return redirect(url_for('main.addcon'))


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
        client, _ = conf()
        container = client.containers.get(container_id)
        # Get last 1000 lines to avoid memory issues with large logs
        log_output = container.logs(tail=1000, timestamps=True)
        return render_template('logs.html', logs=log_output)
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
    """Handle built-in shell commands (cd, pwd, clear).

    Returns:
        True if command was handled, False otherwise.
    """
    if data == 'clear':
        sock.send('__CLEAR__')
        return True

    if data == 'pwd':
        sock.send(session_workdir.get(sock, '/'))
        return True

    if data.startswith('cd '):
        target = data[3:].strip()
        current = session_workdir.get(sock, '/')
        new_dir = os.path.normpath(os.path.join(current, target))
        session_workdir[sock] = new_dir
        sock.send(f'Changed directory to {new_dir}')
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