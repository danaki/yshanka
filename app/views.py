from flask_security import current_user, utils
from flask import url_for, redirect, render_template, request, abort, Markup, flash, Response
from flask.json import jsonify
from flask_admin.base import expose
from flask_admin.contrib import sqla
from flask_admin.actions import action
from flask_admin.model import typefmt
from flask_admin.model.template import macro
from marshmallow_sqlalchemy import ModelSchema
from wtforms.fields import PasswordField, StringField
from wtforms.widgets import Input
from app.database import db
from app.models import PredictiveModel, Build, Dependency
from app.docker_client import *
from datetime import date


MY_DEFAULT_FORMATTERS = dict(typefmt.BASE_FORMATTERS)
MY_DEFAULT_FORMATTERS.update({
        type(None): typefmt.null_formatter,
        date: lambda view, value: value.strftime("%Y-%m-%d %H:%M")
    })

class DependencySchema(ModelSchema):
    class Meta:
        model = Dependency

dependency_schema = DependencySchema(many=True)


class AdminView(sqla.ModelView):
    def is_accessible(self):
        if not current_user.is_active or not current_user.is_authenticated:
            return False

        if current_user.has_role('superuser'):
            return True

        return False

    def _handle_view(self, name, **kwargs):
        """
        Override builtin _handle_view in order to redirect users when a view is not accessible.
        """
        if not self.is_accessible():
            if current_user.is_authenticated:
                abort(403)
            else:
                return redirect(url_for('security.login', next=request.url))


class ApikeyField(StringField):
    def __call__(self, **kwargs):
        return super(self.__class__, self).__call__(**kwargs) + "<a href='#' onclick='javascript:$(this).prev(\"input\").val(Array.apply(0, Array(32)).map(function() { return Math.floor(Math.random() * 16).toString(16); }).join(\"\"));'>Regenerate</a>"


class UserAdminView(AdminView):
    form_overrides = dict(apikey=ApikeyField)
    column_exclude_list = list = ('password',)
    form_excluded_columns = ('password',)

    def scaffold_form(self):
        form_class = super(self.__class__, self).scaffold_form()
        form_class.password2 = PasswordField('New Password')
        return form_class

    def on_model_change(self, form, model, is_created):
        if len(model.password2):
            model.password = utils.encrypt_password(model.password2)


class PredictiveModelView(AdminView):
    column_list = ('user.username', 'model_name', 'active_build.version', 'active_build.created_date')
    column_labels = {
        'user.username': 'Owner',
        'model_name': 'Model',
        'active_build.version': 'Version',
        'active_build.created_date': 'Deployed'
        }
    column_type_formatters = MY_DEFAULT_FORMATTERS

    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True

    details_template = 'admin/predictive_model_details.html'
    list_template = 'admin/predictive_model_list.html'

    def model_name_formatter(view, context, model, name):
        return Markup("""
        {model_name} <span class="label label-default" id="{container_name}-state"></span>
        <span class="label label-info" id="{container_name}-yshanka_state"></span>
        <span id="{container_name}-stats">
            <span class="badge cpu"></span>
            <span class="badge mem"></span>
        </span>""".format(model_name=model.model_name, container_name=model.active_build.container_name))

    column_formatters = {
       'model_name': model_name_formatter
    }

    @action('restart', 'Restart', 'Are you sure you want to restart selected models?')
    def action_restart(self, ids):
        try:
            query = PredictiveModel.query.filter(PredictiveModel.id.in_(ids))

            count = 0
            for model in query.all():
                count += 1

            flash('{count}s models were successfully approved.'.format(count=count))

        except Exception as ex:
            if not self.handle_view_exception(ex):
                raise

            flash('Failed to restart models. {error}'.format(str(ex)), 'error')

    @expose('/build/<int:id>/code')
    def build_code(self, id):
        return Build.query.get(id).code

    @expose('/build/<int:id>/deps')
    def build_deps(self, id):
        return jsonify(dependency_schema.dump(Build.query.filter_by(id=id).first_or_404().dependencies).data)

    @expose('/build/<int:id>/activate', methods=["POST"])
    def activate_build(self, id):
        build = Build.query.filter_by(id=id).first_or_404()
        current_build = build.predictive_model.active_build
        if (current_build):
            docker_client.stop(build.predictive_model.active_build.container_name)
            
        build.predictive_model.active_build = build

        db.session.add(build.predictive_model)
        db.session.commit()

        docker_client.start(build.container_name)

        return redirect(url_for('.details_view', id=build.predictive_model_id))
