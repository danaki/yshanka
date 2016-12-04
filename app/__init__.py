from __future__ import print_function

import flask
import pyRserve
import numpy as np
import json
import csv
import io
import os
from collections import OrderedDict

import flask_admin
from flask.ext.sqlalchemy import SQLAlchemy
from flask_security import Security, SQLAlchemyUserDatastore, \
    UserMixin, RoleMixin, login_required, current_user
from flask.ext.migrate import Migrate

from app.database import db
from app.models import *
from app.views import *
from app.docker_client import *
from flask_admin import helpers as admin_helpers
from flask_socketio import SocketIO, emit, join_room, leave_room

import eventlet
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

statuses = {}
def get_container_statuses():
    global statuses
    import time

    room = 'desperate_booth'

    while True:
        statuses = {}
        for container in docker_client.containers(all=True):
            print(container['Ports'])
            name = container['Names'][0].replace('/', '')
            try:
                stats = docker_client.stats(name, decode=False, stream=False)
            except ValueError:
                stats = {}

            statuses[name] = dict(
                state=container['State'],
                stats=stats
            )

        with app.app_context():
            emit('statuses', {'msg': statuses.get('desperate_booth', {})}, room=room, namespace='/logs')

        socketio.sleep(5)

status_thread = socketio.start_background_task(get_container_statuses)


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

    modelname = flask.request.form['modelname']

    #deps_file = '/tmp/{}_deps.R'.format(modelname)
    #model_file = '/tmp/{}.Rdata'.format(modelname)

    model_file = flask.request.files['model_image']

    #flask.request.files['model_image'].save(model_file)
    code = flask.request.form['code']

    model = PredictiveModel.query.filter_by(name = flask.request.form['modelname']).first()
    if not model:
        model = PredictiveModel(name=flask.request.form['modelname'])

    model.code = flask.request.form['code']

    db.session.add(model)
    db.session.commit()

    deps_file = ''
    for p in json.loads(flask.request.form['packages'], object_pairs_hook=OrderedDict):
        dep = PredictiveModelDependency(**p)
        dep.model_id = model.id
        db.session.add(dep)
        if p['install'] == 'true':
            deps += "install.packages('{}');\n".format(p['importName'])

    db.session.commit()

    import tarfile
    import tempfile

    template_dir = os.path.realpath(APP_ROOT + '/../worker/')
    f = tempfile.NamedTemporaryFile()

    t = tarfile.open(mode='w', fileobj=f)

    abs_path = os.path.abspath(template_dir)
    t.add(abs_path, arcname='.', recursive=True)
    t.addfile(tarfile.TarInfo("env.Rdata"), model_file)
    t.addfile(tarfile.TarInfo("deps.R"), io.BytesIO(deps_file.encode('utf-8')))

    t.close()
    f.seek(0)

    @flask.copy_current_request_context
    def background_thread(modelname, dockerfile):
        image = 'yshan:' + modelname
        containers = docker_client.containers(all=True, filters=dict(name=modelname))
        if len(containers) > 0:
            print("Removing and stoping previous " + modelname)
            docker_client.stop(modelname, timeout=0)
            docker_client.remove_container(modelname, force=True)
            docker_client.remove_image(image, force=True)

        for line in docker_client.build(fileobj=f, rm=True, tag=image, custom_context=True):
            out = flask.json.loads(line)
            if 'error' in out:
                print(out['errorDetail'], end="")
                return
            print(out['stream'], end="")

        container = docker_client.create_container(
            image,
            detach=True,
            name=modelname,
            host_config=docker_client.create_host_config(port_bindings={
                6311: ('0.0.0.0',)
            }))
        print(docker_client.start(container=container.get('Id')))

    socketio.start_background_task(background_thread, modelname, f)

    return flask.jsonify({"success": "true"})

@app.route('/<user>/models/<path:model_name>', methods=['POST'])
def model(user, model_name):
    conn = pyRserve.connect(host='localhost', port=9876)
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

@socketio.on('joined_statuses', namespace='/logs')
def joined_statuses(message):
    global statuses
    room = 'desperate_booth'
    join_room('desperate_booth')

    emit('statuses', {'msg': statuses.get('desperate_booth', {})}, room=room, namespace='/logs')

@socketio.on('joined_logs', namespace='/logs')
def joined_logs(message):
    global docker_thread

    room = 'desperate_booth'

    @flask.copy_current_request_context
    def background_thread():
        while True:
            line = ''
            for s in docker_client.logs(container='desperate_booth',
                             stdout=True,
                             stderr=True,
                             timestamps=True,
                             stream=True,
                             tail=0):
                line += s
                if s == '\n':
                    emit('logs', {'msg': line}, room=room, namespace='/logs')
                    line = ''

    join_room('desperate_booth')

    history = docker_client.logs(container='desperate_booth',
                 stdout=True,
                 stderr=True,
                 timestamps=True,
                 stream=False,
                 tail="all")

    emit('logs', {'msg': history}, room=room, namespace='/logs')

    if docker_thread is None:
        docker_thread = socketio.start_background_task(background_thread)
