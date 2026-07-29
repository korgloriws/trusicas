"""
Lista admins e/ou redefine a senha de um administrador.

Uso na VPS (na pasta do projecto ou dentro do contentor):

  python reset_admin_password.py
  python reset_admin_password.py --list
  python reset_admin_password.py --username admin --password 'NovaSenhaForte'

Se --password for omitido, pede a nova senha de forma interactiva.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from store import connect, init_db
from users import get_user_by_username, update_user, validate_password


def list_admins() -> list[dict]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, display_name, role, created_at
            FROM users
            WHERE role = 'admin'
            ORDER BY id ASC
            """
        ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "username": str(r["username"]),
            "display_name": str(r["display_name"]),
            "created_at": str(r["created_at"]),
        }
        for r in rows
    ]


def main() -> int:
    p = argparse.ArgumentParser(description="Listar / repor senha de administrador Trusicas")
    p.add_argument("--list", action="store_true", help="Só listar admins (não altera senha)")
    p.add_argument("--username", type=str, default=None, help="Username do admin (default: primeiro admin)")
    p.add_argument("--password", type=str, default=None, help="Nova senha (se omitir, pergunta)")
    args = p.parse_args()

    admins = list_admins()
    if not admins:
        print("Nenhum administrador encontrado na base de dados.", file=sys.stderr)
        return 1

    print("Administradores:")
    for a in admins:
        print(f"  - id={a['id']}  username={a['username']!r}  nome={a['display_name']!r}")

    if args.list:
        print(
            "\nNota: a senha actual NÃO pode ser lida (está em hash). "
            "Use este script sem --list para definir uma senha nova."
        )
        return 0

    username = (args.username or "").strip() or admins[0]["username"]
    user = get_user_by_username(username)
    if user is None or user.get("role") != "admin":
        print(f"Utilizador {username!r} não é administrador (ou não existe).", file=sys.stderr)
        return 1

    password = args.password
    if password is None:
        password = getpass.getpass(f"Nova senha para «{username}»: ")
        confirm = getpass.getpass("Confirmar nova senha: ")
        if password != confirm:
            print("As senhas não coincidem.", file=sys.stderr)
            return 1

    err = validate_password(password)
    if err:
        print(err, file=sys.stderr)
        return 1

    update_user(int(user["id"]), password=password)
    print(f"OK — senha actualizada para o admin «{username}».")
    print(f"Entre com: utilizador = {username}  |  a senha que acabou de definir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
