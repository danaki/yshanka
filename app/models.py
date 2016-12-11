import datetime
from app.database import db
from flask_security import Security, \
    UserMixin, RoleMixin, login_required, current_user


roles_users = db.Table(
    'roles_users',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'))
)

class Role(db.Model, RoleMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True)
    description = db.Column(db.String(255))

    def __str__(self):
        return self.name

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    active = db.Column(db.Boolean)
    email = db.Column(db.String(255), unique=True)
    username = db.Column(db.String(30), nullable=False, unique=True)
    password = db.Column(db.String(128), nullable=False)
    apikey = db.Column(db.String(128), unique=True)

    roles = db.relationship('Role', secondary=roles_users, backref=db.backref('users', lazy='dynamic'))
    predictive_models = db.relationship("PredictiveModel", back_populates="user")

    def __str__(self):
        return self.username

class PredictiveModel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    model_name = db.Column(db.String(80), unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    active_build_id = db.Column(db.Integer, db.ForeignKey('build.id'))

    user = db.relationship("User", back_populates="predictive_models")
    builds = db.relationship("Build",
        back_populates="predictive_model",
        foreign_keys='Build.predictive_model_id',
        order_by="desc(Build.version)"
        )
    active_build = db.relationship("Build", foreign_keys=[active_build_id])

    def __str__(self):
        return self.name

class Build(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    container_name = db.Column(db.String(128))
    version = db.Column(db.Integer)
    code = db.Column(db.Text)
    created_date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    predictive_model_id = db.Column(db.Integer, db.ForeignKey('predictive_model.id'))

    dependencies = db.relationship("Dependency", back_populates="build")
    predictive_model = db.relationship("PredictiveModel", back_populates="builds", foreign_keys=[predictive_model_id])

    def __str__(self):
        return self.container_name

class Dependency(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80))
    importName = db.Column(db.String(80))
    src = db.Column(db.String(80))
    version = db.Column(db.String(80))
    install = db.Column(db.Boolean)
    build_id = db.Column(db.Integer, db.ForeignKey('build.id'))

    build = db.relationship("Build", back_populates="dependencies")

    def __str__(self):
        return self.name
