from datetime import datetime

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    DateField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    InputRequired,
    Length,
    Optional,
    ValidationError,
)
from domain import CATEGORY_LABELS, CONVERSATION_KINDS, DEFAULT_STATUS, FLAG_REASONS, STATUS_LABELS
from models import User
from utils import today_local

AVATAR_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp"]


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def _username_field():
    return StringField(
        "Usuario",
        filters=[_strip],
        validators=[
            DataRequired(message="Ingrese un nombre de usuario."),
            Length(
                min=3,
                max=64,
                message="El nombre de usuario debe tener entre 3 y 64 caracteres.",
            ),
        ],
    )


def _email_field():
    return StringField(
        "Correo electrónico",
        filters=[_strip],
        validators=[
            DataRequired(message="Ingrese su correo electrónico."),
            Email(message="Ingrese un correo electrónico válido."),
            Length(
                max=120,
                message="El correo electrónico no puede superar los 120 caracteres.",
            ),
        ],
    )


# ---------------------------
# AUTH: LOGIN FORM
# ---------------------------
class LoginForm(FlaskForm):
    username = StringField(
        "Usuario",
        filters=[_strip],
        validators=[DataRequired(message="Ingrese su usuario.")],
    )
    password = PasswordField(
        "Contraseña",
        validators=[DataRequired(message="Ingrese su contraseña.")],
    )
    submit = SubmitField("Iniciar sesión")


# ---------------------------
# AUTH: REGISTER FORM
# ---------------------------
class RegisterForm(FlaskForm):
    username = _username_field()
    email = _email_field()
    password = PasswordField(
        "Contraseña",
        validators=[
            DataRequired(message="Ingrese una contraseña."),
            Length(min=6, message="La contraseña debe tener al menos 6 caracteres."),
        ],
    )
    confirm_password = PasswordField(
        "Confirmar contraseña",
        validators=[
            DataRequired(message="Confirme su contraseña."),
            EqualTo("password", message="Las contraseñas no coinciden."),
        ],
    )
    submit = SubmitField("Crear cuenta")


# ---------------------------
# PROFILE: EDIT PROFILE FORM
# ---------------------------
class EditProfileForm(FlaskForm):
    username = _username_field()
    email = _email_field()
    bio = TextAreaField(
        "Biografía",
        filters=[_strip],
        validators=[
            Optional(),
            Length(max=300, message="La biografía no puede superar los 300 caracteres."),
        ],
    )
    submit = SubmitField("Guardar cambios")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self._user = user

    def _taken_by_another_user(self, column, value):
        other = User.query.filter(column == value).first()
        return other is not None and other.id != self._user.id

    def validate_username(self, field):
        if self._taken_by_another_user(User.username, field.data):
            raise ValidationError("El nombre de usuario ya está en uso.")

    def validate_email(self, field):
        if self._taken_by_another_user(User.email, field.data):
            raise ValidationError("El correo electrónico ya está en uso.")


# ---------------------------
# PROFILE: CHANGE PASSWORD FORM
# ---------------------------
class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Contraseña actual",
        validators=[DataRequired(message="Ingrese su contraseña actual.")],
    )
    new_password = PasswordField(
        "Nueva contraseña",
        validators=[
            DataRequired(message="Ingrese la nueva contraseña."),
            Length(min=6, message="La nueva contraseña debe tener al menos 6 caracteres."),
        ],
    )
    confirm_password = PasswordField(
        "Confirmar nueva contraseña",
        validators=[
            DataRequired(message="Confirme la nueva contraseña."),
            EqualTo("new_password", message="Las contraseñas no coinciden."),
        ],
    )
    submit = SubmitField("Cambiar contraseña")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self._user = user

    def validate_current_password(self, field):
        if not self._user.check_password(field.data):
            raise ValidationError("La contraseña actual es incorrecta.")


# ---------------------------
# PROFILE: AVATAR UPLOAD FORM
# ---------------------------
class AvatarUploadForm(FlaskForm):
    avatar = FileField(
        "Foto de perfil",
        validators=[
            FileRequired(message="Seleccione una imagen."),
            FileAllowed(
                AVATAR_EXTENSIONS,
                "Formato no permitido. Use PNG, JPG, GIF o WEBP.",
            ),
        ],
    )
    submit = SubmitField("Actualizar foto")


# ---------------------------
# REPORTS: LOST-AND-FOUND ITEM FORMS
# ---------------------------
ITEM_IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp"]
ITEM_NAME_MAX = 120
ITEM_DESCRIPTION_MAX = 500
ITEM_LOCATION_MAX = 120
ITEM_CONTACT_MAX = 120

ITEM_CATEGORY_CHOICES = [("", "Seleccione una categoría")] + list(CATEGORY_LABELS.items())
ITEM_STATUS_CHOICES = list(STATUS_LABELS.items())

FUTURE_DATE_MESSAGE = "La fecha no puede ser posterior a hoy."


class _EventDateField(DateField):
    """Date field (<input type="date">, ISO format) with Spanish parse errors.

    An empty value is left to InputRequired so the user sees "Ingrese la fecha del suceso."
    instead of a parse error.
    """

    def process_formdata(self, valuelist):
        if not valuelist:
            return
        raw = str(valuelist[0]).strip()
        if not raw:
            self.data = None
            return
        try:
            self.data = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            self.data = None
            raise ValueError("Ingrese una fecha válida.") from None


class AddItemForm(FlaskForm):
    name = StringField(
        "Objeto",
        filters=[_strip],
        validators=[
            DataRequired(message="Ingrese el nombre del objeto."),
            Length(
                max=ITEM_NAME_MAX,
                message=f"El nombre no puede superar los {ITEM_NAME_MAX} caracteres.",
            ),
        ],
    )
    description = TextAreaField(
        "Descripción",
        filters=[_strip],
        validators=[
            DataRequired(message="Ingrese una descripción del objeto."),
            Length(
                max=ITEM_DESCRIPTION_MAX,
                message=f"La descripción no puede superar los {ITEM_DESCRIPTION_MAX} caracteres.",
            ),
        ],
    )
    # Keys stay in English (data contract); only the labels are Spanish. Choices are validated
    # in validate_category instead of by the field so the edit route can extend them with a
    # legacy category that is no longer in the list.
    category = SelectField(
        "Categoría",
        choices=ITEM_CATEGORY_CHOICES,
        default="",
        validate_choice=False,
        validators=[DataRequired(message="Seleccione una categoría.")],
    )
    location = StringField(
        "Lugar",
        filters=[_strip],
        validators=[
            DataRequired(message="Ingrese el lugar."),
            Length(
                max=ITEM_LOCATION_MAX,
                message=f"El lugar no puede superar los {ITEM_LOCATION_MAX} caracteres.",
            ),
        ],
    )
    event_date = _EventDateField(
        "Fecha del suceso",
        default=today_local,
        validators=[InputRequired(message="Ingrese la fecha del suceso.")],
    )
    status = SelectField(
        "Estado",
        choices=ITEM_STATUS_CHOICES,
        default=DEFAULT_STATUS,
        validate_choice=False,
        validators=[DataRequired(message="Seleccione un estado.")],
    )
    contact = StringField(
        "Contacto",
        filters=[_strip],
        validators=[
            DataRequired(message="Ingrese un correo o teléfono de contacto."),
            Length(
                max=ITEM_CONTACT_MAX,
                message=f"El contacto no puede superar los {ITEM_CONTACT_MAX} caracteres.",
            ),
        ],
    )
    image = FileField(
        "Fotografía",
        validators=[
            FileRequired(message="Suba una fotografía del objeto."),
            FileAllowed(ITEM_IMAGE_EXTENSIONS, "Formato no permitido. Use PNG, JPG, GIF o WEBP."),
        ],
    )
    submit = SubmitField("Guardar reporte")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The browser date picker stops at today; validate_event_date enforces it server-side.
        self.event_date.render_kw = {"max": today_local().isoformat()}

    def validate_category(self, field):
        allowed = {value for value, _label in field.choices}
        if field.data and field.data not in allowed:
            raise ValidationError("Seleccione una categoría válida.")

    def validate_status(self, field):
        if field.data not in STATUS_LABELS:
            raise ValidationError("Seleccione un estado válido.")

    def validate_event_date(self, field):
        if field.data is not None and field.data > today_local():
            raise ValidationError(FUTURE_DATE_MESSAGE)


class EditItemForm(AddItemForm):
    # Editing keeps the current photo when no new file is chosen.
    image = FileField(
        "Cambiar fotografía (opcional)",
        validators=[
            FileAllowed(ITEM_IMAGE_EXTENSIONS, "Formato no permitido. Use PNG, JPG, GIF o WEBP."),
        ],
    )
    submit = SubmitField("Guardar cambios")


# -----------------------------
# MESSAGES ABOUT A REPORT
# -----------------------------
MESSAGE_MIN = 5
MESSAGE_MAX = 1000


def _message_field(label):
    return TextAreaField(
        label,
        filters=[_strip],
        validators=[
            DataRequired(message="Escriba un mensaje."),
            Length(
                min=MESSAGE_MIN,
                max=MESSAGE_MAX,
                message=f"El mensaje debe tener entre {MESSAGE_MIN} y {MESSAGE_MAX} caracteres.",
            ),
        ],
    )


class ContactForm(FlaskForm):
    """First message to the person who published a report ("Es mío", "Lo encontré", ...)."""

    kind = SelectField(
        "Motivo",
        choices=list(CONVERSATION_KINDS.items()),
        validate_choice=False,
        validators=[DataRequired(message="Seleccione un motivo.")],
    )
    body = _message_field("Mensaje")
    submit = SubmitField("Enviar mensaje")

    def validate_kind(self, field):
        if field.data not in CONVERSATION_KINDS:
            raise ValidationError("Seleccione un motivo válido.")


class ReplyForm(FlaskForm):
    body = _message_field("Respuesta")
    submit = SubmitField("Enviar")


class EditMessageForm(FlaskForm):
    body = _message_field("Editar mensaje")
    submit = SubmitField("Guardar")


class ConversationKindForm(FlaskForm):
    kind = SelectField(
        "Motivo",
        choices=list(CONVERSATION_KINDS.items()),
        validate_choice=False,
        validators=[DataRequired(message="Seleccione un motivo.")],
    )
    submit = SubmitField("Cambiar")

    def validate_kind(self, field):
        if field.data not in CONVERSATION_KINDS:
            raise ValidationError("Seleccione un motivo válido.")


FLAG_COMMENT_MAX = 300


class FlagForm(FlaskForm):
    reason = SelectField(
        "Motivo",
        choices=[("", "Seleccione un motivo")] + list(FLAG_REASONS.items()),
        default="",
        validate_choice=False,
        validators=[DataRequired(message="Seleccione un motivo.")],
    )
    comment = TextAreaField(
        "Comentario (opcional)",
        filters=[_strip],
        validators=[
            Optional(),
            Length(
                max=FLAG_COMMENT_MAX,
                message=f"El comentario no puede superar los {FLAG_COMMENT_MAX} caracteres.",
            ),
        ],
    )
    submit = SubmitField("Enviar reporte")

    def validate_reason(self, field):
        if field.data not in FLAG_REASONS:
            raise ValidationError("Seleccione un motivo válido.")
