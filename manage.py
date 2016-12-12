#!/usr/bin/env python
from app import app, db, socketio
from app.models import User, Role
from flask.ext.script import Manager, Server
from flask.ext.migrate import MigrateCommand
from flask_security import SQLAlchemyUserDatastore

manager = Manager(app)
#manager.add_command('runserver', Server())
manager.add_command('db', MigrateCommand)

@manager.option('-h', '--host', dest='host', default='127.0.0.1')
@manager.option('-p', '--port', dest='port', default=5000)
def run(host, port):
    # restarting with reloader spawns duplicate eventlet5000 threads, disable reloader here
    socketio.run(app, use_reloader=False, host=host, port=int(port))

@manager.command
def seed():
    user_datastore = SQLAlchemyUserDatastore(db, User, Role)
    user_datastore.create_role(name='superuser')

    user_datastore.create_user(
        username='admin',
        email='admin@example.com',
        password='admin',
        apikey='adminadminadmin',
        roles=['superuser'])

    db.session.commit()

if __name__ == '__main__':
    manager.run()
