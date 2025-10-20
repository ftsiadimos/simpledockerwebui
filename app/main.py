from flask import Flask, render_template, request, redirect, url_for, flash
from app import app
from app import db
from app.models import Owner
from app.forms import AddForm
import docker
from flask_sock import Sock
sock = Sock(app)
from bs4 import BeautifulSoup
import os

# ------------------------------------------------------------------
# Keep a per‑socket working directory.  The key is the Sock instance
# itself (which is hashable) and the value is the absolute path.
# ------------------------------------------------------------------
session_workdir = {}

def conf():
    """
    Return a Docker client and the Owner record.
    If the Owner record does not specify a host/port, connect to the
    local Docker daemon via the Unix socket.
    Raises ValueError if the Docker endpoint cannot be reached.
    """
    serverurl = Owner.query.get(1)

    # Determine the base URL
    if not serverurl or not serverurl.name or not serverurl.port:
        base_url = 'unix://var/run/docker.sock'
    else:
        base_url = f"tcp://{serverurl.name}:{serverurl.port}"

    try:
        client = docker.DockerClient(base_url=base_url)
        # Quick sanity check – ping the daemon
        client.ping()
    except docker.errors.DockerException as exc:
        # Connection failed – raise a user‑friendly error
        raise ValueError(f"Cannot connect to Docker at {base_url}: {exc}") from exc

    return client, serverurl

@app.route('/', methods=['GET', 'POST'])
def index():
    try:
        client, serverurl = conf()
        doc = client.containers.list(all)
        return render_template('index.html', doc=doc, serverurl=serverurl)
    except ValueError as err:
        # Show a warning to the user and ask them to correct the URL
        flash(str(err), 'warning')
        return redirect(url_for('addcon'))


@app.route('/about', methods=['GET'])
def about():
    return render_template('about.html')


@app.route('/addcon', methods=['GET', 'POST'])
def addcon():
    form = AddForm()
    user = Owner.query.get(1)
    if form.validate_on_submit():
        user = Owner.query.get(1)
        user.name = form.name.data
        user.port = form.port.data
        db.session.commit()
        return redirect(url_for('index'))
    return render_template('addcon.html', form=form, user=user)



@app.route("/logs", methods=["POST"])
def logs():
    id = request.form.get("logs")
    client, serverurl = conf()
    container = client.containers.get(id)
    logs = container.logs()
    return render_template('logs.html', logs=logs)


@app.route("/comma", methods=["POST"])
def comma():
    id = request.form.get("comma")
    serverurl = Owner.query.get(1)
    client, serverurl = conf()
    container = client.containers.get(id)
    return render_template('soc.html', id=container.id)



@sock.route('/echo')
def echo(sock):
    """
    WebSocket echo that now supports `cd` and `pwd`.
    Each client gets its own working directory stored in
    `session_workdir`.  All other commands are executed with
    that directory as the container's working directory.
    """
    id = request.args.get("id")
    # Initialise working directory for this socket if not present
    if sock not in session_workdir:
        session_workdir[sock] = '/'  # default to root

    while True:
        data = sock.receive()          # <-- may return None on close
        if data is None:               # socket closed
            break                      # exit loop → thread ends
        data = data.strip()
        if not data:
            continue

        # Handle built‑in commands
        if data.startswith('cd '):
            # Extract target directory, support relative paths
            target = data[3:].strip()
            # Resolve against current working directory
            new_dir = os.path.normpath(os.path.join(session_workdir[sock], target))
            # Docker exec_run does not validate the path, so we just
            # store it; the next command will be executed there.
            session_workdir[sock] = new_dir
            sock.send(f'Changed directory to {new_dir}')
            continue

        if data == 'pwd':
            sock.send(session_workdir[sock])
            continue

        # Normal command – run inside the container with the stored cwd
        client, serverurl = conf()
        container = client.containers.get(id)
        try:
            result = container.exec_run(
                data,
                workdir=session_workdir[sock],   # <‑‑ use the stored directory
                stdout=True,
                stderr=True,
                demux=False
            )
            output = BeautifulSoup(result.output, 'html.parser').get_text()
            sock.send(output)
        except Exception as e:
            sock.send(f'Error: {e}')
        # NEW: handle the "clean" command to reset the terminal
        if data == 'clear':
            # Send a special marker that the client will interpret as a clear request
            sock.send('__CLEAR__')
            continue

# ------------------------------------------------------------------
# Clean‑up when the socket thread ends
# ------------------------------------------------------------------


@app.route("/submitadmin", methods=["POST"])
def submit_remove():
    action = request.form.get("submit_button")
    client, serverurl = conf()
    if action == "Delete":
        user_interests = request.form.getlist("interests")
        for interest in user_interests:
            container = client.containers.get(interest)
            container.stop()
            container.remove()
        return redirect(url_for('index'))
    elif action == "Start":
        user_interests = request.form.getlist("interests")
        for interest in user_interests:
            container = client.containers.get(interest)
            container.start()
        return redirect(url_for('index'))

    elif action == "Restart":
        user_interests = request.form.getlist("interests")
        for interest in user_interests:
            container = client.containers.get(interest)
            container.restart()
        return redirect(url_for('index'))

    elif action == "Stop":
        user_interests = request.form.getlist("interests")
        for interest in user_interests:
                container = client.containers.get(interest)
                container.stop()
        return redirect(url_for('index'))
    else:
        return "Invalid form submission"
