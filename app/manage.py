"""Управление пользователями из консоли.

    python -m app.manage --config config.yaml create-user admin --role admin
    python -m app.manage --config config.yaml set-password admin
"""
import argparse
import getpass
import sys

from app.config import load_config
from app.db import Database
from app.users import ROLE_NAMES, ROLES, UserError, UserRepo


def main(argv=None, ask_password=None) -> int:
    ask_password = ask_password or (lambda: getpass.getpass("Пароль: "))
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create-user", help="создать пользователя")
    create.add_argument("login")
    create.add_argument("--role", choices=ROLES, default="viewer")
    passwd = sub.add_parser("set-password", help="сменить пароль")
    passwd.add_argument("login")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    users = UserRepo(Database(cfg.db_path))
    try:
        if args.cmd == "create-user":
            user = users.create(args.login, ask_password(), args.role)
            print(f"Создан пользователь {user.login} ({ROLE_NAMES[user.role]})")
        else:
            user = users.get_by_login(args.login)
            if user is None:
                print(f"Нет пользователя {args.login}")
                return 1
            users.set_password(user.id, ask_password())
            print("Пароль изменён")
    except UserError as e:
        print(f"Ошибка: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
