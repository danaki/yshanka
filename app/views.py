from flask_security import current_user, utils
from flask import url_for, redirect, render_template, request, abort, Markup, flash
from flask_admin.contrib import sqla
from flask_admin.actions import action
from wtforms.fields import PasswordField, StringField
from wtforms.widgets import Input
from app.models import PredictiveModel
from app.docker_client import *

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
                # permission denied
                abort(403)
            else:
                # login
                return redirect(url_for('security.login', next=request.url))


class ApikeyField(StringField):

    def __call__(self, **kwargs):
        return super(self.__class__, self).__call__(**kwargs) + "<a href='#' onclick='javascript:$(this).prev(\"input\").val(Array.apply(0, Array(32)).map(function() { return Math.floor(Math.random() * 16).toString(16); }).join(\"\"));'>Regenerate</a>"


class UserAdminView(AdminView):
    form_overrides = dict(apikey=ApikeyField)

    # Don't display the password on the list of Users
    column_exclude_list = list = ('password',)

    # Don't include the standard password field when creating or editing a User (but see below)
    form_excluded_columns = ('password',)

    # On the form for creating or editing a User, don't display a field corresponding to the model's password field.
    # There are two reasons for this. First, we want to encrypt the password before storing in the database. Second,
    # we want to use a password field (with the input masked) rather than a regular text field.
    def scaffold_form(self):

        # Start with the standard form as provided by Flask-Admin. We've already told Flask-Admin to exclude the
        # password field from this form.
        form_class = super(self.__class__, self).scaffold_form()

        # Add a password field, naming it "password2" and labeling it "New Password".
        form_class.password2 = PasswordField('New Password')
        return form_class

    # This callback executes when the user saves changes to a newly-created or edited User -- before the changes are
    # committed to the database.
    def on_model_change(self, form, model, is_created):

        # If the password field isn't blank...
        if len(model.password2):

            # ... then encrypt the new password prior to storing it in the database. If the password field is blank,
            # the existing password in the database will be retained.
            model.password = utils.encrypt_password(model.password2)

class PredictiveModelView(AdminView):
    column_exclude_list = list = ('code',)
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True
    details_template = 'admin/predictive_model_details.html'
    list_template = 'admin/predictive_model_list.html'

    def _name_formatter(view, context, model, name):
        return Markup("""
        {model_name} <span class="label label-default" id="{model_name}-state"></span>
        <span class="label label-info" id="{model_name}-model_state"></span>
        <span id="{model_name}-stats">
            <span class="badge cpu"></span>
            <span class="badge mem"></span>
        </span>""".format(model_name=model.name))

    column_formatters = {
       'name': _name_formatter
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
