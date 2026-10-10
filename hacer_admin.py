"""Give or remove the administrator role from the command line.

    python hacer_admin.py Desly30            -> Desly30 pasa a ser administrador
    python hacer_admin.py --quitar Desly30   -> deja de serlo
    python hacer_admin.py --lista            -> muestra los administradores
"""

import sys

from admin import admin_usernames, set_admin
from app import create_app


def main(args):
    app = create_app()
    with app.app_context():
        if args == ["--lista"]:
            names = admin_usernames()
            print("Administradores: " + (", ".join(names) if names else "ninguno"))
            return 0
        revoke = bool(args) and args[0] == "--quitar"
        names = args[1:] if revoke else args
        if len(names) != 1:
            print(__doc__)
            return 1
        user = set_admin(names[0], value=not revoke)
        if user is None:
            print(f"No existe el usuario «{names[0]}». Primero regístrese en la web.")
            return 1
        print(f"{user.username} {'ya no es' if revoke else 'ahora es'} administrador.")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
