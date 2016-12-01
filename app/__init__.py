import flask
import pyRserve
import numpy as np
import json
import csv
import io
import docker
from collections import OrderedDict

import flask_admin
from flask.ext.sqlalchemy import SQLAlchemy
from flask_security import Security, SQLAlchemyUserDatastore, \
    UserMixin, RoleMixin, login_required, current_user
from flask.ext.migrate import Migrate

from app.database import db
from app.models import *
from app.views import *
from flask_admin import helpers as admin_helpers
from flask_socketio import SocketIO, emit, join_room, leave_room


app = flask.Flask(__name__)
app.config.from_object('config')

db.init_app(app)
migrate = Migrate(app, db)

# Setup Flask-Security
user_datastore = SQLAlchemyUserDatastore(db, User, Role)
security = Security(app, user_datastore)

socketio = SocketIO()
socketio.init_app(app)
docker_thread = None

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

    flask.request.files['model_image'].save('model_image.img')
    code = flask.request.form['code'] + '''

dir.create(file.path(".", "Rlib"), showWarnings = FALSE)
.libPaths("./Rlib")

# functions defined in 'code' must not be overriden by the saved image
.all_objects = ls()
.all_funcs <- .all_objects[lapply(.all_objects, function(name){
       class(globalenv()[[name]])
   }) == "function"]

.env <- new.env()
load('model_image.img', envir=.env)

for (n in ls(.env, all.names=TRUE)) {
  if (! n %in% .all_funcs) {
    assign(n, get(n, .env), .GlobalEnv)
  }
}
    '''

    #os.remove('code.R')
    with open('code.R', 'w') as file:
        file.write(code)

    model = PredictiveModel.query.filter_by(name = flask.request.form['modelname']).first()
    if not model:
        model = PredictiveModel(name=flask.request.form['modelname'])

    model.code = flask.request.form['code']

    db.session.add(model)
    db.session.commit()

    for p in json.loads(flask.request.form['packages'], object_pairs_hook=OrderedDict):
        dep = PredictiveModelDependency(**p)
        dep.model_id = model.id
        db.session.add(dep)

    db.session.commit()

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

@socketio.on('joined', namespace='/logs')
def joined(message):
    global docker_thread

    room = 'desperate_booth'
    dc_args = docker.utils.kwargs_from_env(assert_hostname=False)
    dc = docker.Client(**dc_args)
    print("Connected docker")

    @flask.copy_current_request_context
    def background_thread():
        while True:
            line = ''
            for s in dc.logs(container='desperate_booth',
                             stdout=True,
                             stderr=True,
                             timestamps=True,
                             stream=True,
                             tail=0):
                line += s
                if s == '\n':
                    emit('message', {'msg': line}, room=room, namespace='/logs')
                    line = ''

    join_room('desperate_booth')

    history = dc.logs(container='desperate_booth',
                 stdout=True,
                 stderr=True,
                 timestamps=True,
                 stream=False,
                 tail="all")

    emit('message', {'msg': history}, room=room, namespace='/logs')

    if docker_thread is None:
        docker_thread = socketio.start_background_task(background_thread)
