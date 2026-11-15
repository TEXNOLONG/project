"""Контрольные сценарии алгоритма. Запуск: python -m tests.test_engine  (или pytest)"""
import json
import pathlib
import time

from backend.db import DB
from backend.engine import Engine, NORM_SEC, detect_qual

ROOT = pathlib.Path(__file__).resolve().parent.parent


def make(techs=None):
    airport = json.loads((ROOT / "data" / "airport_demo.json").read_text(encoding="utf-8"))
    cfg = techs or json.loads((ROOT / "data" / "technicians.json").read_text(encoding="utf-8"))
    return Engine(airport, cfg, DB(":memory:"))


def run(e, seconds, step=0.5):
    """Прокрутить модель на `seconds` модельных секунд."""
    n = int(seconds / (step * e.sim_speed)) + 1
    for _ in range(n):
        e.tick(step)


def test_1_basic_assignment():
    e = make()
    r = e.create_request("ВС-01", "Повреждение обшивки", "Планер")
    assert r["status"] == "enroute" and r["eta"] <= NORM_SEC


def test_2_different_quals():
    e = make()
    a = e.create_request("ВС-01", "x", "Планер")
    b = e.create_request("ВС-02", "x", "Авионика")
    c = e.create_request("ВС-03", "x", "Двигатель")
    assert len({a["tech_id"], b["tech_id"], c["tech_id"]}) == 3
    assert [e.techs[i]["qual"] if False else e.techs[i].qual for i in (a["tech_id"], b["tech_id"], c["tech_id"])] \
        == ["Планер", "Авионика", "Двигатель"]


def test_3_fastest_eta_preferred():
    """Система не выбирает путь с более долгим ETA, если есть быстрее."""
    cfg = [{"name": "А", "qual": "Планер", "speed": 2.0, "base": "BASE3"},
           {"name": "Б", "qual": "Планер", "speed": 5.2, "base": "BASE1"}]
    e = make(cfg)
    # Isolate pedestrian ETA comparison; dedicated transport is covered below.
    for vehicle in e.engineer_vehicles.values():
        vehicle.status = "returning"
    r = e.create_request("ВС-02", "x", "Планер")
    cand = {c["name"]: c for c in r["candidates"]}
    assert cand["А"]["dist"] < cand["Б"]["dist"]          # А ближе
    assert cand["А"]["eta_walk"] > cand["Б"]["eta_walk"]  # но Б быстрее
    # Выбирается более быстрый техник, как требует критерий задания.
    assert e.techs[r["tech_id"]].name == "Б"
    assert r["eta"] == cand["Б"]["eta"]
    assert r["eta"] == min(c["eta"] for c in cand.values() if c["ok"])


def test_engineer_vehicle_is_selected_when_it_reduces_eta():
    e = make()
    r = e.create_request("ВС-03", "Вибрация двигателя", "Двигатель")
    selected = next(c for c in r["candidates"] if c["tech_id"] == r["tech_id"])
    assert selected["mode"] == "vehicle"
    assert r["travel_mode"] == "vehicle"
    assert r["vehicle_id"] == selected["vehicle_id"]
    assert selected["eta"] < selected["eta_walk"]
    vehicle = e.engineer_vehicles[r["vehicle_id"]]
    assert vehicle.status == "to_pickup"
    assert vehicle.route is not None and len(vehicle.route.points) >= 1


def test_engineer_vehicle_pickup_arrival_and_return():
    e = make()
    r = e.create_request("ВС-04", "Повреждение обшивки", "Планер")
    assert r["travel_mode"] == "vehicle"
    vehicle = e.engineer_vehicles[r["vehicle_id"]]
    run(e, 500)
    assert r["status"] == "arrived"
    assert e.techs[r["tech_id"]].vehicle_id is None
    assert vehicle.status in ("returning", "idle")
    run(e, 500)
    assert vehicle.status == "idle"


def test_vehicle_eta_updates_during_pickup_and_boarding():
    e = make()
    r = e.create_request("ВС-05", "Перегрев двигателя", "Двигатель")
    assert r["travel_mode"] == "vehicle"
    initial = e._req_view(r)["eta_left"]
    run(e, 10)
    assert e._req_view(r)["eta_left"] < initial

def test_4_norm_filter():
    cfg = [{"name": "Медленный", "qual": "Планер", "speed": 0.5, "base": "BASE1"}]
    e = make(cfg)
    for vehicle in e.engineer_vehicles.values():
        vehicle.status = "returning"
    r = e.create_request("ВС-05", "x", "Планер")
    assert r["status"] == "waiting" and r["tech_id"] is None


def test_5_busy_tech_not_reassigned():
    e = make([{"name": "Один", "qual": "Планер", "speed": 5.0, "base": "BASE1"}])
    a = e.create_request("ВС-01", "x", "Планер")
    b = e.create_request("ВС-02", "x", "Планер")
    assert a["status"] == "enroute" and b["status"] == "waiting"
    e.mark_arrived(a["id"]); e.start_work(a["id"]); e.resolve(a["id"])
    e.tick(0.1)  # dirty -> dispatch_waiting
    assert b["status"] == "enroute"


def test_6_several_requests():
    e = make()
    a = e.create_request("ВС-01", "x", "Планер")
    b = e.create_request("ВС-02", "x", "Планер")
    assert a["tech_id"] != b["tech_id"]


def test_movement_and_auto_arrival():
    e = make()
    r = e.create_request("ВС-03", "x", "Планер")
    t = e.techs[r["tech_id"]]
    run(e, r["eta"] + 5)
    assert r["status"] == "arrived" and t.route is None
    assert r["within_norm"] is True and r["response_sec"] <= NORM_SEC


def test_full_cycle_and_history():
    e = make()
    r = e.create_request("ВС-01", "x", "Авионика")
    e.mark_arrived(r["id"]); e.start_work(r["id"]); e.resolve(r["id"])
    snap = e.snapshot()
    assert snap["active"] == [] and snap["history"][0]["id"] == r["id"]
    assert snap["stats"]["done"] == 1 and snap["stats"]["techs_free"] == 9


def test_invalid_transitions():
    e = make()
    r = e.create_request("ВС-01", "x", "Планер")
    for fn in (e.start_work, e.resolve):
        try:
            fn(r["id"]); assert False
        except ValueError:
            pass


def test_blocked_edge_reroutes_or_blocks():
    e = make([{"name": "Т", "qual": "Планер", "speed": 5.0, "base": "BASE1"}])
    open_eta = e.evaluate({"stand": "ВС-05", "qual": "Планер"})[0][0]["eta"]
    e.set_edge_blocked("EB2", True)  # участок B между B2 и B3
    rows, _ = e.evaluate({"stand": "ВС-05", "qual": "Планер"})
    assert rows[0]["eta"] > open_eta  # объезд по дорожке A длиннее
    for eid in ("EB1", "EL1"):
        e.set_edge_blocked(eid, True)  # база отрезана полностью
    rows, _ = e.evaluate({"stand": "ВС-05", "qual": "Планер"})
    assert rows[0]["eta"] is None


def test_emergency_preempts_busy_tech():
    e = make([{"name": "Единственный", "qual": "Двигатель", "speed": 5.0, "base": "BASE1"}])
    a = e.create_request("ВС-01", "x", "Двигатель")
    em = e.create_request("ВС-04", "пожар двигателя", "Двигатель", priority="emergency")
    assert em["status"] == "enroute" and a["status"] == "waiting"
    assert "ЧС" in a["note"]
    e.mark_arrived(em["id"]); e.start_work(em["id"]); e.resolve(em["id"])
    e.tick(0.1)
    assert a["status"] == "enroute"  # прерванная заявка получила техника


def test_emergency_when_norm_unreachable_still_assigns():
    e = make([{"name": "Медленный", "qual": "Планер", "speed": 0.5, "base": "BASE1"}])
    for vehicle in e.engineer_vehicles.values():
        vehicle.status = "returning"
    em = e.create_request("ВС-05", "x", "Планер", priority="emergency")
    assert em["status"] == "enroute" and em["violation"] is True


def test_waiting_queue_emergency_first():
    e = make([{"name": "Один", "qual": "Планер", "speed": 5.0, "base": "BASE1"}])
    a = e.create_request("ВС-01", "x", "Планер")
    n = e.create_request("ВС-02", "x", "Планер")
    em = e.create_request("ВС-03", "x", "Планер", priority="emergency")
    assert em["status"] == "enroute"          # перехватил у a
    assert a["status"] == "waiting" and n["status"] == "waiting"
    e.mark_arrived(em["id"]); e.start_work(em["id"]); e.resolve(em["id"])
    e.tick(0.1)
    assert a["status"] == "enroute" and n["status"] == "waiting"  # раньше созданная идёт первой


def test_detect_qual():
    assert detect_qual("Вибрация правого двигателя") == "Двигатель"
    assert detect_qual("Не работает навигационный дисплей") == "Авионика"
    assert detect_qual("Трещина обшивки у шасси") == "Планер"
    assert detect_qual("что-то странное") is None


def test_auto_qual_error_message():
    e = make()
    try:
        e.create_request("ВС-01", "что-то странное", "auto"); assert False
    except ValueError as ex:
        assert "вручную" in str(ex)


def test_simulation_pause_and_resume():
    e = make()
    r = e.create_request("ВС-03", "x", "Планер")
    tech = e.techs[r["tech_id"]]
    vehicle = e.engineer_vehicles.get(tech.vehicle_id)
    e.set_running(False)
    start = (e.sim_time, tech.x, tech.y, tech.travelled)
    vehicle_start = (vehicle.x, vehicle.y) if vehicle else None
    run(e, 12)
    assert (e.sim_time, tech.x, tech.y, tech.travelled) == start
    e.set_running(True)
    run(e, 40)
    moved = tech.travelled > start[3] or (vehicle and (vehicle.x, vehicle.y) != vehicle_start)
    assert e.sim_time > start[0] and moved


def test_simulator_can_move_and_reclassify_free_technicians():
    e = make()
    tech = e.techs[1]
    node = next(node for node in e.graph.nodes if node != tech.node)
    x, y = e.graph.nodes[node]
    e.move_tech(tech.id, x, y)
    assert tech.node == node and (tech.x, tech.y) == (x, y)
    e.update_tech_profile(tech.id, "Авионика", ["A320", "B737"])
    assert tech.qual == "Авионика" and tech.types == ("A320", "B737")


def test_recommendation_computes_within_ten_seconds():
    e = make()
    start = time.perf_counter()
    r = e.create_request("ВС-04", "Вибрация двигателя", "Двигатель")
    elapsed = time.perf_counter() - start
    assert r["status"] == "enroute"
    assert elapsed < 10, f"Подбор занял {elapsed:.3f} секунд"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("OK  ", fn.__name__)
    print(f"\nВсе {len(fns)} тестов пройдены")
