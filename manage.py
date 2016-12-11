#!/usr/bin/env python
from app import app, db, socketio
from app.models import User, Role
from flask.ext.script import Manager, Server
from flask.ext.migrate import MigrateCommand
from flask_security import SQLAlchemyUserDatastore

manager = Manager(app)
#manager.add_command('runserver', Server())
manager.add_command('db', MigrateCommand)

@manager.command
def run():
    # restarting with reloader spawns duplicate eventlet threads, disable it here
    socketio.run(app, use_reloader=False)

@manager.command
def seed():
    user_datastore = SQLAlchemyUserDatastore(db, User, Role)
    user_datastore.create_role(name='superuser')

    user_datastore.create_user(
        username='admin',
        email='admin@example.com',
        password='admin',
        roles=['superuser'])

    db.session.commit()

if __name__ == '__main__':
    manager.run()
