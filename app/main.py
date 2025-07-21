from flask import Flask, render_template, request, redirect, url_for
from app import app
from app import db
from app.models import Owner
from app.forms import AddForm
import docker
from flask_sock import Sock
sock = Sock(app)
from bs4 import BeautifulSoup

def conf():
    serverurl = Owner.query.get(1)
    client = docker.DockerClient(base_url='tcp://'+str(serverurl.name)+':'+str(serverurl.port))
    return client, serverurl

@app.route('/', methods=['GET', 'POST'])
def index():
    try:
        client, serverurl = conf()
        doc = client.containers.list(all)
        return render_template('index.html', doc=doc, serverurl=serverurl)
    except:
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
    id = request.args.get("id")
    while True:
        data = sock.receive()
        client, serverurl = conf()
        container = client.containers.get(id)
        result = container.exec_run(data)
        result =  BeautifulSoup(result.output, 'html.parser').get_text()
        sock.send(result)


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