"""Тесты аккаунтов, регистрации и личного состояния инженера."""
import json
import pathlib
import tempfile

from backend.auth import AuthError, AuthStore
from backend.db import DB
from backend.engine import Engine

ROOT = pathlib.Path(__file__).resolve().parent.parent
TECHS = json.loads((ROOT / "data" / "technicians.json").read_text(encoding="utf-8"))


def make_auth():
    tmp = tempfile.TemporaryDirectory()
    auth = AuthStore(str(pathlib.Path(tmp.name) / "auth.db"), tech_seed_count=len(TECHS))
    auth.seed_demo(TECHS)
    return auth, tmp


def test_seed_and_login():
    auth, tmp = make_auth()
    dispatcher = auth.authenticate("dispatcher@oto.ru", "Dispatcher2026!")
    engineer = auth.authenticate("tech1@oto.ru", "Engineer2026!")
    assert dispatcher["role"] == "dispatcher"
    assert engineer["role"] == "engineer" and engineer["tech_id"] == 1
    try:
        auth.authenticate("dispatch@oto.ru", "bad")
        assert False
    except AuthError as e:
        assert e.code == "invalid_credentials"
    tmp.cleanup()


def test_registration_pending_and_approval():
    auth, tmp = make_auth()
    pending = auth.register_engineer(
        "Тестов Иван", "test@oto.ru", "TestPass2026!", "Планер", ["A320", "B737"], "BASE2"
    )
    assert pending["status"] == "pending" and pending["tech_id"] is None
    try:
        auth.authenticate("test@oto.ru", "TestPass2026!")
        assert False
    except AuthError as e:
        assert e.code == "pending"

    approved = auth.approve_engineer(pending["id"])
    assert approved["status"] == "active"
    assert approved["tech_id"] >= len(TECHS) + 1
    active = auth.authenticate("test@oto.ru", "TestPass2026!")
    assert active["tech_id"] == approved["tech_id"]

    pending2 = auth.register_engineer(
        "Второй Инженер", "second@oto.ru", "TestPass2026!", "Авионика", ["SSJ-100"], "BASE1"
    )
    rejected = auth.reject_engineer(pending2["id"])
    assert rejected["status"] == "rejected"
    tmp.cleanup()


def test_duplicate_email_rejected():
    auth, tmp = make_auth()
    auth.register_engineer("Один", "same@oto.ru", "TestPass2026!", "Планер", ["A320"], "BASE1")
    try:
        auth.register_engineer("Два", "SAME@oto.ru", "TestPass2026!", "Планер", ["A320"], "BASE1")
        assert False
    except AuthError as e:
        assert e.code == "email_exists"
    tmp.cleanup()


def test_engineer_tech_and_private_snapshot():
    auth, tmp = make_auth()
    pending = auth.register_engineer(
        "Новый Инженер", "new@oto.ru", "TestPass2026!", "Планер", ["A320"], "BASE1"
    )
    user = auth.approve_engineer(pending["id"])
    airport = json.loads((ROOT / "data" / "airport_demo.json").read_text(encoding="utf-8"))
    engine = Engine(airport, TECHS, DB(":memory:"))
    engine.add_engineer_tech(user)
    assert user["tech_id"] in engine.techs

    req = engine.create_request("ВС-01", "Трещина обшивки", "Планер")
    assigned = req["tech_id"]
    own = engine.engineer_snapshot(assigned)
    other = engine.engineer_snapshot(user["tech_id"] if user["tech_id"] != assigned else 1)
    assert all(r["tech_id"] == assigned for r in own["active"])
    assert len(own["techs"]) == 1 and own["me"]["id"] == assigned
    assert other["active"] == []
    assert all("candidates" not in r for r in own["active"])

    engine.reset()
    assert user["tech_id"] in engine.techs
    tmp.cleanup()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("OK  ", fn.__name__)
    print(f"\nВсе {len(fns)} тестов пройдены")
