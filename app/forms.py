#!/usr/bin/env python3
# forms.py
from flask_wtf import FlaskForm
from wtforms import Form, StringField, SelectField, validators, PasswordField, BooleanField, SubmitField, TextAreaField
from wtforms.validators import ValidationError, DataRequired, Email, EqualTo
from wtforms.widgets.core import TextArea
from app.models import Owner



class AddForm(FlaskForm):
    name = StringField('Docker Server URL(FQDN)')
    port = StringField('Docker Server Port(PORT)')
    submit = SubmitField('Save')
    
