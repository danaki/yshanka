from app.database import db
from flask_security import Security, \
    UserMixin, RoleMixin, login_required, current_user

roles_users = db.Table(
    'roles_users',
    db.Column('user_id', db.Integer(), db.ForeignKey('user.id')),
    db.Column('role_id', db.Integer(), db.ForeignKey('role.id'))
)

class Role(db.Model, RoleMixin):
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80), unique=True)
    description = db.Column(db.String(255))

    def __str__(self):
        return self.name

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    active = db.Column(db.Boolean())     
    email = db.Column(db.String(255), unique=True)
    username = db.Column(db.String(30), nullable=False, unique=True)
    password = db.Column(db.String(128), nullable=False)
    apikey = db.Column(db.String(128), unique=True)  
    roles = db.relationship('Role', secondary=roles_users,
                            backref=db.backref('users', lazy='dynamic'))    
    models = db.relationship("PredictiveModel")

    def __str__(self):
        return self.username

class PredictiveModel(db.Model):
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80), unique=True)
    code = db.Column(db.Text())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    dependencies = db.relationship("PredictiveModelDependency")

    def __str__(self):
        return self.name
        
    def image(self):
        return self.name + '.rda'
        
class PredictiveModelDependency(db.Model):
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80))
    importName = db.Column(db.String(80))
    src = db.Column(db.String(80))
    version = db.Column(db.String(80))
    install = db.Column(db.Boolean())
    model_id = db.Column(db.Integer, db.ForeignKey('predictive_model.id'))

    def __str__(self):
        return self.name        