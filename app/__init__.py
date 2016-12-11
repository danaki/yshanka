from __future__ import print_function

import werkzeug
import pyRserve
import numpy as np
import json
import csv
import io
import re
import os
import tarfile
import tempfile
import flask_admin
import eventlet
import docker
import requests

from flask import Flask, Response, request, jsonify, url_for, render_template
from flask_admin import helpers as admin_helpers
from flask_socketio import SocketIO, emit, join_room, leave_room

from collections import OrderedDict, Iterable
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
YSHANKA_TAG = '*** YSHANKA:'

app = Flask(__name__)
app.config.from_object('config')

db.init_app(app)
migrate = Migrate(app, db)

# Setup Flask-Security
user_datastore = SQLAlchemyUserDatastore(db, User, Role)
security = Security(app, user_datastore)

socketio = SocketIO(app, async_mode='eventlet')
docker_thread = None

model_containers = {}

admin = flask_admin.Admin(
    app,
    'Yshanka',
    base_template='my_master.html',
    template_mode='bootstrap3',
)

admin.add_view(AdminView(Role, db.session, name='Roles'))
admin.add_view(UserAdminView(User, db.session, name='Users'))
admin.add_view(PredictiveModelView(PredictiveModel, db.session, name='Models'))


class PyRserveEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, pyRserve.TaggedList):
            return OrderedDict(obj.astuples())
        elif issubclass(obj.__class__, np.ndarray):
            return obj.tolist()
        else:
            return json.JSONEncoder.default(self, obj)

def dockerize_name(name):
    # for regex see https://github.com/docker/docker/pull/3253
    return re.sub(r"[^a-zA-Z0-9_.-]", '_', name)

def model_to_image_name(model_name, version):
    # see http://stackoverflow.com/questions/36741678/docker-invalid-tag-value
    return 'yshanka/{}:{}'.format(dockerize_name(model_name).lower(), version)

def model_to_container_name(model_name, version):
    return 'yshanka_{}_{}'.format(dockerize_name(model_name), version)

def parse_yshanka_state(lines):
    state = None
    for line in lines.split('\n'):
        m = re.search('"{} (.*)"'.format(re.escape(YSHANKA_TAG)), line)
        if m:
            state = m.group(1)

    return state

def send_history(container_name):
    history = docker_client.logs(
        container=container_name,
        stdout=True,
        stderr=True,
        timestamps=True,
        stream=False,
        tail="all")

    model_containers[container_name]['yshanka_state'] = parse_yshanka_state(history)

    with app.app_context():
        emit('logs', dict(logs=history, clear=True), room='logs_' + container_name, namespace='/logs')

def start_background_task(f, *args, **kwargs):
    return eventlet.spawn(f, *args, **kwargs)

def stream_logs(container_name):
    while True:
        with app.app_context():
            send_history(container_name)

            try:
                app.logger.debug("Stream logs " + container_name)
                line = ''
                for s in docker_client.logs(container=container_name,
                                 stdout=True,
                                 stderr=True,
                                 timestamps=True,
                                 stream=True,
                                 tail=0):
                    line += s
                    if line.endswith('\n'):
                        yshanka_state = parse_yshanka_state(line)
                        if not yshanka_state is None:
                            model_containers[container_name]['yshanka_state'] = yshanka_state
                        emit('logs', dict(logs=line), room='logs_' + container_name, namespace='/logs')
                        line = ''
                else:
                    app.logger.debug("Container stopped?: " + container_name)
                    stop_watching(container_name)
                    break

            except requests.exceptions.ConnectionError:
                pass
            except docker.errors.NotFound as e:
                app.logger.debug("Logs ConnectionError: " + container_name + ' ' + str(e))
                eventlet.sleep(1)

def stream_stats(container_name):
    while True:
        with app.app_context():
            try:
                app.logger.debug("Stream stats " + container_name)
                for s in docker_client.stats(container_name, decode=True, stream=True):
                    with app.app_context():
                        emit('stats', dict(container_name=container_name, stats=s), room='stats', namespace='/logs')
            except requests.exceptions.ConnectionError:
                pass
            except docker.errors.NotFound:
                app.logger.debug("Stats ConnectionError" + container_name + ' ' + str(e))
                eventlet.sleep(1)

def watch_containers():
    while True:
        with app.app_context():
            for c in docker_client.containers(all=True, filters=dict(name='yshanka')):
                container_name = c['Names'][0].replace('/', '')

                if not container_name in model_containers:
                    app.logger.debug("Found new container: "+ container_name + ', state=' + c['State'])
                    model_containers[container_name] = dict(
                        logs_thread=None,
                        stats_thread=None,
                        yshanka_state='parsing logs...')

                if c['State'] == 'running' and model_containers[container_name]['logs_thread'] is None:
                    model_containers[container_name]['logs_thread'] = start_background_task(stream_logs, container_name)

                if c['State'] == 'running' and model_containers[container_name]['stats_thread'] is None:
                    model_containers[container_name]['stats_thread'] = start_background_task(stream_stats, container_name)

                emit('states', dict(
                        container_name=container_name,
                        state=c['State'],
                        yshanka_state=model_containers[container_name]['yshanka_state']),
                    room='states', namespace='/logs')

        eventlet.sleep(1)

def stop_watching(container_name):
    with app.app_context():
        app.logger.debug("Stopped watching " + container_name)
        eventlet.kill(model_containers[container_name]['logs_thread'])
        eventlet.kill(model_containers[container_name]['stats_thread'])
        del model_containers[container_name]

def deployer_thread(image_name, container_name, model_file, deps):
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

    for line in docker_client.build(fileobj=f, rm=True, tag=image_name, custom_context=True):
        out = json.loads(line)
        if not 'stream' in out:
            app.logger.error(out)
        app.logger.error(out['stream'].rstrip('\n'))

    container = docker_client.create_container(
        image_name,
        detach=True,
        name=container_name,
        host_config=docker_client.create_host_config(port_bindings={
            6311: ('0.0.0.0',)
        }))
    #docker_client.start(container=container.get('Id'))

    if container_name in model_containers:
        # will restart in watch_containers
        stop_watching(container_name)

status_thread = start_background_task(watch_containers)


@app.errorhandler(404)
def not_found(error):
    return jsonify(dict(message="Resource doesn't exist."))

@app.errorhandler(500)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response

@app.route('/')
def index():
    return render_template('index.html')

@security.context_processor
def security_context_processor():
    return dict(
        admin_base_template=admin.base_template,
        admin_view=admin.index_view,
        h=admin_helpers,
        get_url=url_for
    )


@app.route('/verify', methods=['POST'])
def verify():
    u = User.query.filter_by(
        username=request.args['username'],
        apikey=request.args['apikey']).first_or_404()

    return jsonify(dict(success=str(True).lower()))

@app.route('/deployer/model', methods=['POST'])
def deploy_model():
    user = User.query.filter_by(
        username=request.args['username'],
        apikey=request.args['apikey'],
        ).first_or_404()

    model_name = request.form['modelname']
    code       = request.form['code']
    packages   = request.form['packages']

    model_file = request.files['model_image']

    model = PredictiveModel.query.filter_by(model_name=model_name).first()
    if not model:
        version = 1
    elif model.user.id != user.id:
        raise werkzeug.exceptions.Forbidden('Not an owner')
    else:
        version = int(db.session.query(db.func.max(Build.version)).filter_by(predictive_model=model).scalar()) + 1

    container_name = model_to_container_name(model_name, version)
    try:
        docker_client.inspect_container(container_name)
    except docker.errors.NotFound:
        pass
    else:
        raise werkzeug.exceptions.NotImplemented('A container with name {} already exists. Dunno what to do.'.format(container_name))

    if version == 1:
        model = PredictiveModel(
            model_name=model_name,
            user=user)

    build = Build(
        container_name=container_name,
        version=version,
        code=code,
        predictive_model=model
    )

    db.session.add(model)
    db.session.add(build)
    db.session.commit()

    deps = """
    sink(stderr());
    print("{yshanka_tag} initialization");
    """.format(yshanka_tag=YSHANKA_TAG)

    libraries = json.loads(packages, object_pairs_hook=OrderedDict)
    for i, p in enumerate(libraries):
        dep = Dependency(**p)
        dep.build = build
        db.session.add(dep)
        if p['install']:
            deps += """
            print("{yshanka_tag} installing ({i}/{len}): {library}");
            install.packages('{library}');
            """.format(
                yshanka_tag=YSHANKA_TAG,
                library=p['importName'],
                i=i+1,
                len=len(libraries))

    deps += 'print("{yshanka_tag} ready");\n'.format(yshanka_tag=YSHANKA_TAG)

    image_name = model_to_image_name(model_name, version)
    deployer_thread(image_name, container_name, model_file, deps)

    if model.active_build is None:
        model.active_build = build
        docker_client.start(container_name)

    db.session.add(model)
    db.session.commit()

    return jsonify(dict(success=str(True).lower()))

@app.route('/<user>/models/<path:model_name>/', methods=['POST'])
def call_model(user, model_name):
    host = re.search('(?:http.*://)?(?P<host>[^:/ ]+).?(?P<port>[0-9]*).*', docker_client.base_url).group('host')

    model = PredictiveModel.query.filter_by(model_name=model_name).first()
    if not model:
        raise werkzeug.exceptions.NotFound('Model {} not found'.format(model_name))

    containers = docker_client.containers(filters=dict(status='running', name=model.active_build.container_name))
    if len(containers) != 1:
        raise werkzeug.exceptions.ServiceUnavailable('Model {} container either not running or multiple found'.format(model_name))

    c = containers[0]
    container_name = c['Names'][0].replace('/', '')
    rserve_ports = [p['PublicPort'] for p in c['Ports'] if p['PrivatePort'] == 6311]
    if len(rserve_ports) == 0:
        raise werkzeug.exceptions.ServiceUnavailable('Container {} missconfigured'.format(container_name))

    port = rserve_ports[0]

    conn = pyRserve.connect(host=host, port=port)
    model_predict = getattr(conn.r, 'model.predict')

    od = json.loads(request.data.decode('utf-8'), object_pairs_hook=OrderedDict)
    delimited = io.BytesIO()
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

    # emulate yhatr brehavior to coerce to dataframe on client
    if isinstance(res, pyRserve.TaggedList):
        res = dict(result=res)

    return Response(
        response=json.dumps(res, cls=PyRserveEncoder),
        status=200,
        mimetype="application/json")

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
