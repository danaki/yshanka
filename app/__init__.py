from __future__ import print_function

import flask
import pyRserve
import numpy as np
import json
import csv
import io
import re
import os
import time
import tarfile
import tempfile
import flask_admin
import eventlet
import docker
import requests

from flask_admin import helpers as admin_helpers
from flask_socketio import SocketIO, emit, join_room, leave_room

from collections import OrderedDict
from flask.ext.sqlalchemy import SQLAlchemy
from flask_security import Security, SQLAlchemyUserDatastore, \
    UserMixin, RoleMixin, login_required, current_user
from flask.ext.migrate import Migrate

from app.database import db
from app.models import *
from app.views import *
from app.docker_client import *


eventlet.monkey_patch()

APP_ROOT = os.path.dirname(os.path.abspath(__file__))

app = flask.Flask(__name__)
app.config.from_object('config')

db.init_app(app)
migrate = Migrate(app, db)

# Setup Flask-Security
user_datastore = SQLAlchemyUserDatastore(db, User, Role)
security = Security(app, user_datastore)

socketio = SocketIO(app, async_mode='eventlet')
docker_thread = None

model_containers = {}

def send_history(container):
    print("send_history " + container)
    history = docker_client.logs(
        container=container,
        stdout=True,
        stderr=True,
        timestamps=True,
        stream=False,
        tail="all")

    with app.app_context():
        emit('logs', dict(logs=history, clear=True), room='logs_' + container, namespace='/logs')

def start_background_task(f, *args, **kwargs):
    return eventlet.spawn(f, *args, **kwargs)

def stream_logs(container):
    while True:
        send_history(container)
        # since = time.time()
        try:
            print("Stream logs " + container)
            line = ''
            for s in docker_client.logs(container=container,
                             stdout=True,
                             stderr=True,
                             timestamps=True,
                             stream=True,
                             tail=0):
                line += s
                if line.endswith('\n'):
                    with app.app_context():
                        #print("sent line " + len(line))
                        emit('logs', dict(logs=line), room='logs_' + container, namespace='/logs')
                    line = ''
                    since = time.time()

        except (requests.exceptions.ConnectionError, docker.errors.NotFound) as e:
            print("Logs ConnectionError: " + container + ' ' + str(e))

def stream_stats(container):
    while True:
        try:
            print("Stream stats " + container)
            for s in docker_client.stats(container, decode=True, stream=True):
                with app.app_context():
                    emit('stats', {'model_name': container, 'stats': s}, room='stats', namespace='/logs')
        except (requests.exceptions.ConnectionError, docker.errors.NotFound) as e:
            print("Stats ConnectionError" + container + ' ' + str(e))

def stream_states():
    print("stream_states")
    while True:
        # TODO: stop monitoring if container not in the list or stoped
        for c in docker_client.containers(all=True):
            rserve_ports = [p['PublicPort'] for p in c['Ports'] if p['PrivatePort'] == 6311]
            if len(rserve_ports) == 0:
                break

            model_name = c['Names'][0].replace('/', '')

            if not model_name in model_containers:
                print("Starting "+ model_name)
                model_containers[model_name] = dict(
                    logs_thread=start_background_task(stream_logs, model_name),
                    stats_thread=start_background_task(stream_stats, model_name),
                    port=rserve_ports[0])

            with app.app_context():
                emit('states', {'model_name': model_name, 'state': c['State']}, room='states', namespace='/logs')

        socketio.sleep(1)

status_thread = start_background_task(stream_states)


@app.errorhandler(404)
def not_found(error):
    err = {'message': "Resource doesn't exist."}
    return flask.jsonify(**err)

# Flask views
@app.route('/')
def index():
    return flask.render_template('index.html')

# Create admin
admin = flask_admin.Admin(
    app,
    'Yshanka',
    base_template='my_master.html',
    template_mode='bootstrap3',
)

# Add model views
admin.add_view(AdminView(Role, db.session, name='Roles'))
admin.add_view(UserAdminView(User, db.session, name='Users'))
admin.add_view(PredictiveModelView(PredictiveModel, db.session, name='Models'))

# define a context processor for merging flask-admin's template context into the
# flask-security views.
@security.context_processor
def security_context_processor():
    return dict(
        admin_base_template=admin.base_template,
        admin_view=admin.index_view,
        h=admin_helpers,
        get_url=flask.url_for
    )


@app.route('/verify', methods=['POST'])
def verify():
    return flask.jsonify({"success": "true"})

@app.route('/deployer/model', methods=['POST'])
def deploy_model():
    from pprint import pprint
    pprint(flask.request.form)

    model_name = flask.request.form['modelname']
    model_file = flask.request.files['model_image']

    #flask.request.files['model_image'].save(model_file)
    code = flask.request.form['code']

    model = PredictiveModel.query.filter_by(name = flask.request.form['modelname']).first()
    if not model:
        model = PredictiveModel(name=flask.request.form['modelname'])

    model.code = flask.request.form['code']

    db.session.add(model)
    db.session.commit()

    deps = ''
    for p in json.loads(flask.request.form['packages'], object_pairs_hook=OrderedDict):
        dep = PredictiveModelDependency(**p)
        dep.model_id = model.id
        db.session.add(dep)
        if p['install']:
            deps += "install.packages('{}');\n".format(p['importName'])

    db.session.commit()
    template_dir = os.path.realpath(APP_ROOT + '/../worker/')
    f = tempfile.NamedTemporaryFile()

    t = tarfile.open(mode='w', fileobj=f)

    abs_path = os.path.abspath(template_dir)

    t.add(abs_path, arcname='.', recursive=True)

    info = tarfile.TarInfo("env.RData")
    model_file.seek(0, os.SEEK_END)
    info.size = model_file.tell()
    model_file.seek(0)
    t.addfile(info, model_file)

    info = tarfile.TarInfo("deps.R")
    info.size = len(deps)
    t.addfile(info, io.BytesIO(deps.encode('utf-8')))

    t.close()
    f.seek(0)

    @flask.copy_current_request_context
    def releaser_thread(model_name, dockerfile):
        image = 'yshan:' + model_name
        containers = docker_client.containers(all=True, filters=dict(name=model_name))
        if len(containers) > 0:
            print("Removing and stoping previous " + model_name)
            docker_client.stop(model_name, timeout=0)
            docker_client.remove_container(model_name, force=True)
            docker_client.remove_image(image, force=True)

        for line in docker_client.build(fileobj=f, rm=True, tag=image, custom_context=True):
            out = flask.json.loads(line)
            if not 'stream' in out:
                print(out)
            print(out['stream'], end="")

        container = docker_client.create_container(
            image,
            detach=True,
            name=model_name,
            host_config=docker_client.create_host_config(port_bindings={
                6311: ('0.0.0.0',)
            }))
        docker_client.start(container=container.get('Id'))

        if model_name in model_containers:
            eventlet.greenthread.kill(model_containers[model_name]['logs_thread'])
            eventlet.greenthread.kill(model_containers[model_name]['stats_thread'])
            print("Kill sent " + model_name)
            del model_containers[model_name]

    start_background_task(releaser_thread, model_name, f)

    return flask.jsonify({"success": "true"})

@app.route('/<user>/models/<path:model_name>/', methods=['POST'])
def call_model(user, model_name):
    host = re.search('(?:http.*://)?(?P<host>[^:/ ]+).?(?P<port>[0-9]*).*', docker_client.base_url).group('host')
    port = model_containers[model_name]['port']
    print(dict(host=host, port=port))

    conn = pyRserve.connect(host=host, port=port)
    model_predict = getattr(conn.r, 'model.predict')

    od = json.loads(flask.request.data.decode('utf-8'), object_pairs_hook=OrderedDict)
    delimited = io.StringIO()
    writer = csv.writer(delimited, delimiter=',')
    writer.writerow(od.keys())
    writer.writerows(zip(*[od[key] for key in od.keys()]))
    nparr = np.genfromtxt(io.BytesIO(delimited.getvalue().encode('utf-8')), dtype=None, delimiter=',', names=True, deletechars='')

    kv = []
    for name in od.keys():
        column = nparr[name]

        # make pyRserve happy
        if column.dtype.type.__name__ == 'bytes_':
            column = list([str(x, 'utf-8') for x in column])

        kv.append((np.str(str(name)), column,))

    res = model_predict(pyRserve.TaggedList(kv))
    conn.close()

    # make jsonify happy
    od = OrderedDict(res.astuples())
    fl = {k: v.tolist() for k, v in od.items()}

    return flask.jsonify({'result': fl})

@socketio.on('joined_states', namespace='/logs')
def joined_statuses(message):
    join_room('states')

@socketio.on('joined_stats', namespace='/logs')
def joined_statuses(message):
    join_room('stats')

@socketio.on('joined_logs', namespace='/logs')
def joined_logs(message):
    room = 'logs_' + message['model_name']
    join_room(room)

    send_history(message['model_name'])
