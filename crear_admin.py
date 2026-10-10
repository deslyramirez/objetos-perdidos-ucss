"""Create the administrator account (usuario «admin», correo admin@gmail.com).

    python crear_admin.py              -> crea la cuenta y muestra una contraseña nueva
    python crear_admin.py MiClave123   -> usa esa contraseña (mínimo 6 caracteres)

Running it again resets the password. The password is shown once and never saved in the code.
"""

import sys

from admin import create_admin_account
from app import create_app


def main(args):
    if len(args) > 1 or (args and len(args[0]) < 6):
        print(__doc__)
        return 1
    app = create_app()
    with app.app_context():
        user, password = create_admin_account(args[0] if args else None)
        username, email = user.username, user.email
    print("Cuenta de administrador lista.")
    print(f"  Usuario:    {username}")
    print(f"  Correo:     {email}")
    print(f"  Contraseña: {password}")
    print("Guarde la contraseña; puede cambiarla luego en «Cambiar contraseña».")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
