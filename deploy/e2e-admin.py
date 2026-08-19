import os
import sys

sys.path.insert(0, "/app")

from app.database import connect
from app.security import hash_password


def main() -> None:
    action = sys.argv[1]
    username = os.environ["E2E_ADMIN_USERNAME"]
    with connect() as connection, connection.cursor() as cursor:
        if action == "create":
            password = os.environ["E2E_ADMIN_PASSWORD"]
            cursor.execute("SELECT id FROM department WHERE code='PLATFORM_ADMIN' AND status=1")
            department = cursor.fetchone()
            if not department:
                raise RuntimeError("PLATFORM_ADMIN department is missing")
            cursor.execute(
                "INSERT INTO app_user (external_id,username,display_name,password_hash,password_changed_at,status) "
                "VALUES (%s,%s,%s,%s,NOW(3),1)",
                (f"e2e:{username}", username, "E2E Platform Administrator", hash_password(password)),
            )
            user_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO user_department (user_id,department_id,is_primary) VALUES (%s,%s,1)",
                (user_id, department["id"]),
            )
        elif action == "cleanup":
            cursor.execute(
                "UPDATE app_user SET status=0,deleted_at=COALESCE(deleted_at,NOW(3)) "
                "WHERE username=%s AND external_id=%s",
                (username, f"e2e:{username}"),
            )
        else:
            raise RuntimeError(f"Unsupported action: {action}")
        connection.commit()


if __name__ == "__main__":
    main()
