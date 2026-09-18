import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_load_user_for_update_locks_user_and_department_membership_current_reads():
    from app.domains.auth.repository import AuthRepository

    class Cursor:
        def __init__(self):
            self.statements = []
            self.current = None

        def execute(self, statement, parameters=()):
            normalized = " ".join(statement.split())
            self.statements.append(normalized)
            if "FROM app_user WHERE id=" in normalized:
                self.current = {
                    "id": 8,
                    "username": "u",
                    "display_name": "U",
                    "email": None,
                    "status": 1,
                    "last_login_at": None,
                    "password_changed_at": None,
                    "deleted_at": None,
                }
            elif "FROM user_department" in normalized:
                self.current = [{"department_id": 2, "is_primary": 1}]
            else:
                self.current = [{"id": 2, "code": "HR", "name": "人资", "is_primary": 1}]

        def fetchone(self):
            return self.current

        def fetchall(self):
            return self.current

    cursor = Cursor()
    user = AuthRepository(cursor).load_user(8, for_update=True)

    assert user["departments"][0]["code"] == "HR"
    assert len(cursor.statements) == 3
    assert cursor.statements[0].endswith("FOR UPDATE")
    assert "FROM user_department" in cursor.statements[1]
    assert "JOIN department" not in cursor.statements[1]
    assert cursor.statements[1].endswith("FOR UPDATE")
    assert "FROM department" in cursor.statements[2]
    assert not cursor.statements[2].endswith("FOR UPDATE")
