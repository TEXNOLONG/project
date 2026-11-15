"""Ядро системы: заявки, подбор техника, ЧС, движение по графу, самолёты, GSE, журнал."""
import bisect
import math
import random
import time

from . import llm
from .graph import INF, Graph, Route

NORM_SEC = 900
QUALS = ("Планер", "Двигатель", "Авионика")

ST_WAIT, ST_ROUTE, ST_ARRIVED, ST_WORK, ST_DONE, ST_CANCEL = (
    "waiting", "enroute", "arrived", "working", "done", "cancelled")
ACTIVE = (ST_WAIT, ST_ROUTE, ST_ARRIVED, ST_WORK)
STATUS_RU = {
    ST_WAIT: "Ожидает", ST_ROUTE: "В пути", ST_ARRIVED: "Прибыл",
    ST_WORK: "В работе", ST_DONE: "Устранено", ST_CANCEL: "Отменена",
}

WORK_EST_SEC = 300.0

AIRCRAFT_MODELS = ["B737-800", "A320", "B777-300ER", "A330-300", "SSJ-100", "A350-900"]
TYPE_FAMILIES = ("A320", "A330", "A350", "B737", "B777", "B787", "SSJ-100", "MC-21")


def type_family(model):
    """B737-800 -> B737, A320 -> A320. Нужна для допусков на тип ВС."""
    m = (model or "").upper()
    for fam in TYPE_FAMILIES:
        if m.startswith(fam):
            return fam
    return m

AIRCRAFT_SPEED = 6.0
AIRCRAFT_SPAWN_INTERVAL = 180.0
AIRCRAFT_PARK_DURATION = 1800.0
AIRCRAFT_ARRIVAL_LEAD = 120.0

AS_INBOUND = "inbound"
AS_PARKED = "parked"
AS_DEPARTING = "departing"
AS_RU = {AS_INBOUND: "Прилёт", AS_PARKED: "На стоянке", AS_DEPARTING: "Вылет"}

GSE_TYPES = {
    "fuel":     {"label": "Заправка",  "short": "FUEL", "color": "#A9833E", "duration": 140, "lane":  28},
    "catering": {"label": "Питание",   "short": "CATE", "color": "#B36F4C", "duration": 110, "lane": -28},
    "baggage":  {"label": "Багаж",     "short": "BAGG", "color": "#7D689C", "duration": 150, "lane":  36},
    "cleaning": {"label": "Уборка",    "short": "CLEA", "color": "#5B826C", "duration": 90,  "lane": -36},
    "pushback": {"label": "Отправка",  "short": "PUSH", "color": "#B2503A", "duration": 60,  "lane":   0},
}
GSE_SPEED = 5.0
GSE_UNITS_PER_TYPE = 2

# Vehicles dedicated to engineer dispatch. Their paths are planned on the same
# taxiway/apron graph as technicians; the estimate includes vehicle pickup time.
TECH_VEHICLE_SPEED = 8.0
TECH_VEHICLE_COUNT = 3
TECH_VEHICLE_BOARD_SEC = 18.0
TECH_VEHICLE_TYPES = ("Электрокар", "Сервисный автомобиль", "Электрокар")

GS_IDLE = "idle"
GS_TO_STAND = "to_stand"
GS_WORKING = "working"
GS_TO_BASE = "to_base"
GS_RU = {GS_IDLE: "Свободна", GS_TO_STAND: "Едет к борту",
         GS_WORKING: "Работает", GS_TO_BASE: "Возвращается"}

TV_IDLE = "idle"
TV_TO_PICKUP = "to_pickup"
TV_BOARDING = "boarding"
TV_TO_STAND = "to_stand"
TV_RETURNING = "returning"
TV_RU = {TV_IDLE: "На базе", TV_TO_PICKUP: "За инженером", TV_BOARDING: "Посадка инженера",
         TV_TO_STAND: "К борту", TV_RETURNING: "Возвращается"}

KEYWORDS = {
    "Двигатель": ["двигател", "турбин", "масл", "обороты", "вибрац", "топлив", "возгоран", "помпаж",
                  "реверс", "engine", "egt", "выхлоп", "лопатк", "запуск"],
    "Авионика": ["авионик", "экран", "дисплей", "навигац", "радио", "связь", "gps", "fms", "транспондер",
                 "радар", "индикац", "приборн", "ils", "tcas", "автопилот", "электро", "датчик", "антенн"],
    "Планер": ["шасси", "крыл", "обшивк", "фюзеляж", "люк", "дверь", "двер", "закрылк", "элерон", "стекл",
               "колес", "колёс", "покрышк", "повреждени", "вмятин", "гидравл", "птиц", "трещин", "лючок"],
}


def detect_qual(text):
    t = (text or "").lower()
    scores = {q: sum(1 for k in kws if k in t) for q, kws in KEYWORDS.items()}
    best = max(scores.values())
    if best == 0:
        return None
    winners = [q for q, s in scores.items() if s == best]
    return winners[0] if len(winners) == 1 else None


def _random_flight():
    return f"SU {random.randint(100, 9999)}"


class Tech:
    def __init__(self, tid, name, qual, speed, base_id, node, xy, types=None):
        self.id, self.name, self.qual, self.speed = tid, name, qual, float(speed)
        self.base_id, self.node = base_id, node
        self.x, self.y = xy
        self.types = tuple(types or ())   # допуски на типы ВС; пусто = без ограничений
        self.status = "free"
        self.request_id = None
        self.route = None
        self.travelled = 0.0
        self.dest_node = None
        self.path_nodes = None
        self.vehicle_id = None

    def to_dict(self):
        left = self.route.remaining(self.travelled) if self.route else []
        return {
            "id": self.id, "name": self.name, "qual": self.qual, "speed": self.speed,
            "types": list(self.types),
            "status": self.status, "request_id": self.request_id, "base": self.base_id,
            "x": round(self.x, 1), "y": round(self.y, 1),
            "route": [[round(px, 1), round(py, 1)] for px, py in left],
            "vehicle_id": self.vehicle_id,
        }


class Aircraft:
    def __init__(self, aid, flight, model, entry_node, created_sim):
        self.id = aid
        self.flight = flight
        self.model = model
        self.entry_node = entry_node
        self.stand_id = None
        self.status = AS_INBOUND
        self.route = None
        self.travelled = 0.0
        self.x, self.y = 0.0, 0.0
        self.path_nodes = None
        self.path_edges = None
        self.last_edge_idx = 0
        self.dest_node = None
        self.parked_until = None
        self.created_sim = created_sim
        self.brief = None
        self.services = []
        self.service_idx = 0
        self.sched_arr_sim = created_sim + AIRCRAFT_ARRIVAL_LEAD
        self.actual_arr_sim = None
        self.sched_dep_sim = None
        self.actual_dep_sim = None

    def heading(self):
        if self.route is None or len(self.route.points) < 2:
            return 0.0
        cum = self.route.cum
        i = bisect.bisect_right(cum, self.travelled) - 1
        i = max(0, min(i, len(self.route.points) - 2))
        (x1, y1), (x2, y2) = self.route.points[i], self.route.points[i + 1]
        return math.degrees(math.atan2(y2 - y1, x2 - x1)) + 90.0

    def delay_arr_sec(self):
        if self.actual_arr_sim is None:
            return 0
        return self.actual_arr_sim - self.sched_arr_sim

    def services_progress(self):
        if not self.services:
            return 0
        done = sum(1 for s in self.services if s["status"] == "done")
        return round(100 * done / len(self.services))

    def to_dict(self, sim_time=0.0):
        left = self.route.remaining(self.travelled) if self.route else []
        eta_left = None
        if self.route is not None:
            eta_left = max(self.route.total - self.travelled, 0.0) / AIRCRAFT_SPEED
        parked_left = None
        if self.status == AS_PARKED and self.parked_until is not None:
            parked_left = max(self.parked_until - sim_time, 0.0)
        return {
            "id": self.id, "flight": self.flight, "model": self.model,
            "status": self.status, "status_ru": AS_RU[self.status],
            "stand": self.stand_id,
            "x": round(self.x, 1), "y": round(self.y, 1),
            "heading": round(self.heading(), 1),
            "route": [[round(px, 1), round(py, 1)] for px, py in left],
            "eta_left": round(eta_left, 1) if eta_left is not None else None,
            "parked_left": round(parked_left, 1) if parked_left is not None else None,
            "entry_node": self.entry_node,
            "dest_node": self.dest_node,
            "brief": self.brief,
            "services": self.services,
            "services_progress": self.services_progress(),
            "sched_arr_sim": self.sched_arr_sim,
            "actual_arr_sim": self.actual_arr_sim,
            "sched_dep_sim": self.sched_dep_sim,
            "actual_dep_sim": self.actual_dep_sim,
            "delay_arr_sec": self.delay_arr_sec() if self.actual_arr_sim else None,
            "sim_time": sim_time,
        }


class GSE:
    def __init__(self, gid, gtype, base_node, xy):
        self.id = gid
        self.gtype = gtype
        self.base_node = base_node
        self.x, self.y = xy
        self.status = GS_IDLE
        self.target_aircraft = None
        self.target_service = None
        self.route = None
        self.travelled = 0.0
        self.path_nodes = None
        self.dest_node = None
        self.work_left = 0.0

    def heading(self):
        if self.route is None or len(self.route.points) < 2:
            return 0.0
        cum = self.route.cum
        i = bisect.bisect_right(cum, self.travelled) - 1
        i = max(0, min(i, len(self.route.points) - 2))
        (x1, y1), (x2, y2) = self.route.points[i], self.route.points[i + 1]
        return math.degrees(math.atan2(y2 - y1, x2 - x1)) + 90.0

    def gtype_label(self):
        return GSE_TYPES[self.gtype]["label"]

    def to_dict(self):
        left = self.route.remaining(self.travelled) if self.route else []
        meta = GSE_TYPES[self.gtype]
        return {
            "id": self.id, "gtype": self.gtype,
            "label": meta["label"], "short": meta["short"], "color": meta["color"],
            "lane": meta.get("lane", 0),
            "status": self.status, "status_ru": GS_RU[self.status],
            "target": self.target_aircraft,
            "x": round(self.x, 1), "y": round(self.y, 1),
            "heading": round(self.heading(), 1),
            "work_left": round(self.work_left, 1),
            "route": [[round(px, 1), round(py, 1)] for px, py in left],
        }


class EngineerVehicle:
    """A shared apron vehicle reserved for one engineer dispatch at a time."""
    def __init__(self, vid, name, base_id, node, xy):
        self.id, self.name, self.base_id, self.base_node = vid, name, base_id, node
        self.x, self.y = xy
        self.status = TV_IDLE
        self.request_id = None
        self.tech_id = None
        self.route = None
        self.travelled = 0.0
        self.path_nodes = None
        self.dest_node = None
        self.delivery_points = []
        self.pickup_wait_left = 0.0

    def heading(self):
        if self.route is None or len(self.route.points) < 2:
            return 0.0
        i = bisect.bisect_right(self.route.cum, self.travelled) - 1
        i = max(0, min(i, len(self.route.points) - 2))
        (x1, y1), (x2, y2) = self.route.points[i], self.route.points[i + 1]
        return math.degrees(math.atan2(y2 - y1, x2 - x1)) + 90.0

    def to_dict(self):
        left = self.route.remaining(self.travelled) if self.route else []
        return {
            "id": self.id, "name": self.name, "base": self.base_id,
            "status": self.status, "status_ru": TV_RU[self.status],
            "request_id": self.request_id, "tech_id": self.tech_id,
            "x": round(self.x, 1), "y": round(self.y, 1),
            "heading": round(self.heading(), 1),
            "route": [[round(x, 1), round(y, 1)] for x, y in left],
        }


class Engine:
    def __init__(self, airport, techs_cfg, db, sim_speed=10.0):
        self.airport = airport
        self.graph = Graph(airport["nodes"], airport["edges"])
        self.stands = {s["id"]: s for s in airport["stands"]}
        self.bases = {b["id"]: b for b in airport["bases"]}
        for s in self.stands.values():
            if s["node"] not in self.graph.nodes:
                raise ValueError(f"Стоянка {s['id']}: нет узла {s['node']}")
        self.db = db
        self.sim_speed = float(sim_speed)
        self.sim_running = True
        self.sim_time = 0.0
        self.techs_cfg = techs_cfg
        self.techs = {}
        self.registered_techs = {}
        self.requests = {}
        self.aircraft = {}
        self.gse = {}
        self.engineer_vehicles = {}
        self.next_aircraft_id = 1
        self.next_gse_id = 1
        self.last_spawn_sim = 0.0
        self.events = []
        self.dirty = False
        self.edge_owner = {}
        self.entry_points = self._build_entry_points()
        self._build_techs()
        self._build_gse()
        self._build_engineer_vehicles()
        self._load()

    def _build_techs(self):
        self.techs = {}
        base_ids = list(self.bases)
        for i, c in enumerate(self.techs_cfg, 1):
            base_id = c.get("base") if c.get("base") in self.bases else base_ids[(i - 1) % len(base_ids)]
            node = self.bases[base_id]["node"]
            self.techs[i] = Tech(i, c["name"], c["qual"], c["speed"], base_id, node,
                                 self.graph.nodes[node], c.get("types"))

    def add_engineer_tech(self, user):
        """Добавляет в смену инженера с подтверждённым аккаунтом.

        ID техника берётся из аккаунта, поэтому он не меняется после перезапуска.
        Демо-техники 1..N уже созданы из technicians.json и повторно не добавляются.
        """
        try:
            tid = int(user.get("tech_id") or 0)
        except (TypeError, ValueError):
            raise ValueError("У аккаунта не назначен ID техника")
        if tid <= 0:
            raise ValueError("У аккаунта не назначен ID техника")
        if tid in self.techs:
            return self.techs[tid]
        base_id = user.get("base")
        if base_id not in self.bases:
            raise ValueError("Неизвестная база инженера")
        qual = user.get("qual")
        if qual not in QUALS:
            raise ValueError("Неизвестная квалификация инженера")
        node = self.bases[base_id]["node"]
        tech = Tech(
            tid,
            user.get("name") or f"Инженер {tid}",
            qual,
            float(user.get("speed") or 4.0),
            base_id,
            node,
            self.graph.nodes[node],
            user.get("types") or (),
        )
        self.techs[tid] = tech
        self.registered_techs[tid] = dict(user)
        self.dirty = True
        return tech

    def move_tech(self, tech_id, x, y):
        """Move a free engineer to the nearest simulated taxiway node."""
        tech = self.techs.get(int(tech_id))
        if tech is None:
            raise ValueError("Инженер не найден")
        if tech.status != "free":
            raise ValueError("Перемещать можно только свободного инженера")
        node = self.graph.nearest_node(float(x), float(y))
        tech.node = node
        tech.x, tech.y = self.graph.nodes[node]
        self.log(f"Положение {tech.name} изменено для симуляции: узел {node}", None, "info")
        self.dirty = True
        return tech

    def update_tech_profile(self, tech_id, qual, types):
        """Change a free engineer's simulated qualification and aircraft approvals."""
        tech = self.techs.get(int(tech_id))
        if tech is None:
            raise ValueError("Инженер не найден")
        if tech.status != "free":
            raise ValueError("Профиль занятого инженера нельзя менять до завершения заявки")
        if qual not in QUALS:
            raise ValueError("Неизвестная квалификация инженера")
        invalid = sorted(set(types) - set(TYPE_FAMILIES))
        if invalid:
            raise ValueError("Неизвестный допуск на тип ВС: " + ", ".join(invalid))
        if not types:
            raise ValueError("Выберите хотя бы один допуск на тип ВС")
        tech.qual = qual
        tech.types = tuple(sorted(set(types)))
        self.log(f"Профиль {tech.name} изменён для симуляции: {qual}, "
                 f"допуски {', '.join(tech.types)}", None, "info")
        self.dirty = True
        return tech

    def _build_gse(self):
        self.gse = {}
        base_list = list(self.bases.values())
        if not base_list:
            return
        gid = 1
        for gtype in GSE_TYPES:
            for k in range(GSE_UNITS_PER_TYPE):
                base = base_list[(gid - 1) % len(base_list)]
                node = base["node"]
                xy = self.graph.nodes[node]
                self.gse[gid] = GSE(gid, gtype, node, xy)
                gid += 1
        self.next_gse_id = gid

    def _build_engineer_vehicles(self):
        self.engineer_vehicles = {}
        bases = list(self.bases.values())
        for i in range(TECH_VEHICLE_COUNT):
            base = bases[i % len(bases)]
            node = base["node"]
            self.engineer_vehicles[i + 1] = EngineerVehicle(
                i + 1, f"{TECH_VEHICLE_TYPES[i]} {i + 1}", base["id"], node,
                self.graph.nodes[node],
            )

    def _build_entry_points(self):
        if not self.graph.nodes:
            return []
        items = sorted(self.graph.nodes.items(), key=lambda kv: kv[1][0])
        pts = [items[0][0]]
        if len(items) > 1:
            pts.append(items[-1][0])
        return pts

    def _load(self):
        reqs = self.db.load_requests()
        for r in reqs:
            if r["status"] in ACTIVE:
                r.update(status=ST_WAIT, tech_id=None, eta=None, created_sim=self.sim_time, candidates=[],
                         note="Восстановлена после перезапуска сервера")
            r.setdefault("aircraft_id", None)
            r.setdefault("flight", None)
            r.setdefault("vehicle_id", None)
            r.setdefault("travel_mode", "walk")
            self.requests[r["id"]] = r
        self.next_id = max([r["id"] for r in reqs], default=1000) + 1
        self.events = self.db.load_events(200)
        if any(r["status"] == ST_WAIT for r in self.requests.values()):
            self.dirty = True

    def reset(self):
        self.db.reset()
        self.requests.clear()
        self.events.clear()
        self.aircraft.clear()
        self.edge_owner.clear()
        self.next_id = 1001
        self.next_aircraft_id = 1
        self.sim_time = 0.0
        self.last_spawn_sim = 0.0
        for eid in list(self.graph.blocked_ids()):
            self.graph.set_blocked(eid, False)
        self._build_techs()
        for user in list(self.registered_techs.values()):
            self.add_engineer_tech(user)
        self._build_gse()
        self._build_engineer_vehicles()
        self.log("Демо-данные сброшены", None, "info")

    def log(self, text, request_id=None, kind="info"):
        ts = time.strftime("%H:%M:%S")
        eid = self.db.add_event(ts, text, request_id, kind)
        self.events.insert(0, {"id": eid, "ts": ts, "text": text, "request_id": request_id, "kind": kind})
        del self.events[200:]

    @staticmethod
    def fmt(sec):
        sec = int(round(sec))
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def create_request(self, stand, description, qual, extra="", priority="normal"):
        if stand not in self.stands:
            raise ValueError("Выберите борт из списка")
        description = (description or "").strip()
        if not description:
            raise ValueError("Опишите дефект")
        if priority not in ("normal", "emergency"):
            raise ValueError("Неизвестный приоритет")
        auto = False
        if qual in (None, "", "auto"):
            qual = detect_qual(description + " " + (extra or ""))
            auto = True
            if qual is None:
                raise ValueError("Не удалось определить квалификацию по тексту. Выберите её вручную")
        if qual not in QUALS:
            raise ValueError("Неизвестная квалификация")

        aircraft_id, flight = None, None
        for a in self.aircraft.values():
            if a.stand_id == stand and a.status == AS_PARKED:
                aircraft_id, flight = a.id, a.flight
                break

        rid = self.next_id
        self.next_id += 1
        req = {
            "id": rid, "stand": stand, "description": description[:500], "extra": (extra or "")[:300],
            "qual": qual, "qual_auto": auto, "priority": priority, "status": ST_WAIT, "tech_id": None,
            "eta": None, "created_sim": self.sim_time, "created_wall": time.strftime("%H:%M:%S"),
            "arrived_sim": None, "done_sim": None, "response_sec": None, "within_norm": None,
            "violation": False, "note": "", "candidates": [],
            "vehicle_id": None, "travel_mode": "walk",
            "aircraft_id": aircraft_id, "flight": flight,
        }
        self.requests[rid] = req
        em = priority == "emergency"
        f = f", борт {flight}" if flight else ""
        self.log(f"{'ЧС! ' if em else ''}Заявка #{rid}: {stand}{f}, {description[:60]} "
                 f"(квалификация: {qual}{', определена автоматически' if auto else ''})",
                 rid, "emergency" if em else "info")
        self._try_assign(req)
        self._save(req)
        return req

    def _save(self, req):
        self.db.save_request(req)

    def _get(self, rid, allowed, action):
        req = self.requests.get(rid)
        if not req:
            raise ValueError("Заявка не найдена")
        if req["status"] not in allowed:
            raise ValueError(f"Нельзя выполнить «{action}» для заявки в статусе «{STATUS_RU[req['status']]}»")
        return req

    def mark_arrived(self, rid, actor="диспетчер"):
        req = self._get(rid, (ST_ROUTE,), "Отметить прибытие")
        t = self.techs[req["tech_id"]]
        t.x, t.y = self.graph.nodes[self.stands[req["stand"]]["node"]]
        self._arrive(t, forced=True, actor=actor)
        return req

    def start_work(self, rid):
        req = self._get(rid, (ST_ARRIVED,), "Начать работу")
        req["status"] = ST_WORK
        t = self.techs[req["tech_id"]]
        self.log(f"Заявка #{rid}: {t.name} начал работу на {req['stand']}", rid, "work")
        self._save(req)
        return req

    def resolve(self, rid):
        req = self._get(rid, (ST_WORK,), "Дефект устранён")
        t = self.techs[req["tech_id"]]
        req["status"] = ST_DONE
        req["done_sim"] = self.sim_time
        self._release(t)
        self.log(f"Заявка #{rid}: дефект устранён, {t.name} свободен", rid, "done")
        self._save(req)
        return req

    def cancel(self, rid):
        req = self._get(rid, ACTIVE, "Отменить")
        if req["tech_id"]:
            self._release(self.techs[req["tech_id"]])
        req["status"] = ST_CANCEL
        req["tech_id"] = None
        self.log(f"Заявка #{rid} отменена диспетчером", rid, "warn")
        self._save(req)
        return req

    def _cancel_requests_for_aircraft(self, aid, reason):
        for req in self.requests.values():
            if req.get("aircraft_id") == aid and req["status"] in ACTIVE:
                if req["tech_id"]:
                    self._release(self.techs[req["tech_id"]])
                req["status"] = ST_CANCEL
                req["tech_id"] = None
                req["note"] = f"Отменена: {reason}"
                self.log(f"Заявка #{req['id']} отменена: {reason}", req["id"], "warn")
                self._save(req)
        self.dirty = True

    def _release(self, t):
        if t.vehicle_id:
            vehicle = self.engineer_vehicles.get(t.vehicle_id)
            if vehicle and vehicle.status != TV_IDLE:
                self._send_engineer_vehicle_home(vehicle)
        t.vehicle_id = None
        t.status, t.request_id, t.route, t.travelled = "free", None, None, 0.0
        t.path_nodes = None
        self.dirty = True

    def _origin(self, t):
        if t.route is None:
            node = t.node
        else:
            node = self.graph.nearest_node(t.x, t.y)
        nx, ny = self.graph.nodes[node]
        return node, math.hypot(nx - t.x, ny - t.y)

    def _network_route(self, start_xy, end_node):
        """Shortest open-graph route from an arbitrary position to a graph node."""
        start_node = self.graph.nearest_node(*start_xy)
        dist, prev = self.graph.tree(start_node)
        if end_node not in dist:
            return None
        path = Graph.path_from_source(prev, end_node, start_node)
        points = [tuple(start_xy)]
        if math.dist(points[-1], self.graph.nodes[start_node]) > 0.5:
            points.append(self.graph.nodes[start_node])
        points.extend(self.graph.nodes[n] for n in path[1:])
        return Route(points), path

    def _tech_free_delay(self, t):
        if t.status == "free":
            return 0.0
        req = self.requests.get(t.request_id)
        if not req:
            return 0.0
        travel_left = 0.0
        if req["status"] == ST_ROUTE and t.route:
            travel_left = max(t.route.total - t.travelled, 0.0) / t.speed
        elif req["status"] == ST_ROUTE and t.vehicle_id:
            vehicle = self.engineer_vehicles.get(t.vehicle_id)
            if vehicle and vehicle.route:
                travel_left = max(vehicle.route.total - vehicle.travelled, 0.0) / TECH_VEHICLE_SPEED
                if vehicle.status == TV_TO_PICKUP:
                    travel_left += TECH_VEHICLE_BOARD_SEC
                    if vehicle.delivery_points:
                        delivery = Route(vehicle.delivery_points)
                        travel_left += delivery.total / TECH_VEHICLE_SPEED
                elif vehicle.status == TV_BOARDING:
                    travel_left += vehicle.pickup_wait_left
                    if vehicle.delivery_points:
                        delivery = Route(vehicle.delivery_points)
                        travel_left += delivery.total / TECH_VEHICLE_SPEED
        work_left = WORK_EST_SEC if req["status"] in (ST_ROUTE, ST_ARRIVED, ST_WORK) else 0.0
        return travel_left + work_left

    def evaluate(self, req):
        stand_node = self.stands[req["stand"]]["node"]
        dist, prev = self.graph.tree(stand_node)
        # тип ВС: по привязанному рейсу или по борту, который стоит на стоянке
        ac = self.aircraft.get(req.get("aircraft_id")) if req.get("aircraft_id") else None
        if ac is None:
            ac = next((a for a in self.aircraft.values()
                       if a.stand_id == req["stand"] and a.status == AS_PARKED), None)
        fam = type_family(ac.model) if ac else None
        rows = []
        for t in self.techs.values():
            if t.qual != req["qual"]:
                continue
            node, off = self._origin(t)
            delay = self._tech_free_delay(t)

            row = {
                "tech_id": t.id, "name": t.name, "speed": t.speed, "qual": t.qual,
                "types": list(t.types),
                "dist": None, "eta": None, "delay": round(delay, 1),
                "mode": "walk", "vehicle_id": None, "vehicle_name": None,
                "eta_walk": None, "eta_vehicle": None,
                "busy": t.status == "busy", "busy_em": False, "working": False,
                "ok": False, "reason": "",
            }
            if fam and t.types and fam not in t.types:
                row["reason"] = f"нет допуска на {fam}"
                rows.append(row)
                continue
            if t.status == "busy":
                cur = self.requests.get(t.request_id)
                row["busy_em"] = bool(cur and cur["priority"] == "emergency")
                row["working"] = bool(cur and cur["status"] == ST_WORK)

            if node not in dist:
                row["reason"] = "нет маршрута (участки закрыты)"
                rows.append(row)
                continue

            travel = off + dist[node]
            walk_eta = travel / t.speed
            path = Graph.path_to_source(prev, node, stand_node)
            walk_points = [tuple((t.x, t.y))] + [self.graph.nodes[n] for n in path]
            choice = {"mode": "walk", "vehicle_id": None, "vehicle_name": None,
                      "dist": travel, "eta": walk_eta, "path": walk_points}
            if t.status == "free":
                for vehicle in self.engineer_vehicles.values():
                    if vehicle.status != TV_IDLE:
                        continue
                    approach = self._network_route((vehicle.x, vehicle.y), node)
                    if not approach:
                        continue
                    approach_route, _ = approach
                    vehicle_dist = approach_route.total + travel
                    vehicle_eta = vehicle_dist / TECH_VEHICLE_SPEED + TECH_VEHICLE_BOARD_SEC
                    if vehicle_eta < choice["eta"]:
                        combined = list(approach_route.points)
                        ride_points = [tuple((t.x, t.y))] + [self.graph.nodes[n] for n in path]
                        combined.extend(ride_points[1:] if combined and
                                        math.dist(combined[-1], ride_points[0]) < 0.5 else ride_points)
                        choice = {"mode": "vehicle", "vehicle_id": vehicle.id,
                                  "vehicle_name": vehicle.name, "dist": vehicle_dist,
                                  "eta": vehicle_eta, "path": combined}

            row.update(choice)
            row["eta_walk"] = round(walk_eta, 1)
            row["eta_vehicle"] = round(choice["eta"], 1) if choice["mode"] == "vehicle" else None
            row["dist"] = round(choice["dist"])
            row["eta"] = round(choice["eta"], 1)
            row["eta_if_free"] = round(delay + choice["eta"], 1)
            row["path"] = [[round(x, 1), round(y, 1)] for x, y in choice["path"]]

            if row["busy_em"]:
                row["reason"] = "занят ЧС — не перехватывается"
            elif row["busy"]:
                row["reason"] = (f"занят #{t.request_id}, освободится через "
                                 f"{self.fmt(delay)}, доедет за {self.fmt(row['eta'])}")
            elif row["eta"] > NORM_SEC:
                row["reason"] = f"ETA {self.fmt(row['eta'])} больше норматива"
            else:
                row["ok"] = True
                if row["mode"] == "vehicle":
                    row["reason"] = f"доступен {row['vehicle_name']} · ETA с подачей машины"
            rows.append(row)
        return rows, prev

    def _try_assign(self, req):
        rows, prev = self.evaluate(req)
        free_ok = [r for r in rows if r["ok"]]
        choice, preempt, violation = None, False, False
        if free_ok:
            # Выбираем минимальное время прибытия среди свободных техников,
            # которые укладываются в 15-минутный норматив.
            choice = min(free_ok, key=lambda r: (r["eta"], r["dist"]))
        elif req["priority"] == "emergency":
            # Для ЧС — важна скорость (ETA), там дистанция вторична
            pre = [r for r in rows if r["busy"] and not r["busy_em"] and r["eta"] is not None
                   and r["eta"] <= NORM_SEC]
            if pre:
                choice, preempt = min(pre, key=lambda r: (r["working"], r["eta"])), True
            else:
                anyr = [r for r in rows if r["eta"] is not None and not r["busy_em"]]
                if anyr:
                    choice = min(anyr, key=lambda r: r["eta"])
                    preempt, violation = choice["busy"], True
        for r in rows:
            if choice and r["tech_id"] == choice["tech_id"]:
                base = "выбран: минимальный ETA"
                r["reason"] = base + (" (перехват, ЧС)" if preempt else "") \
                    + (" — норматив недостижим" if violation else "")
        req["candidates"] = [{k: v for k, v in r.items() if k != "path_node"} for r in rows]
        if not choice:
            if req["status"] == ST_WAIT:
                req["note"] = "Нет подходящего свободного техника — заявка в очереди" if rows else \
                    "Нет техников такой квалификации"
            return False
        self._assign(req, choice, prev, preempt, violation)
        return True

    def _assign(self, req, choice, prev, preempt, violation):
        t = self.techs[choice["tech_id"]]
        stand_node = self.stands[req["stand"]]["node"]
        node, _ = self._origin(t)
        start = (t.x, t.y)
        if preempt:
            old = self.requests[t.request_id]
            old.update(status=ST_WAIT, tech_id=None, eta=None, candidates=[],
                       note=f"Приостановлена: {t.name} переназначен на ЧС #{req['id']}")
            self.log(f"Заявка #{old['id']} приостановлена: {t.name} переназначен на ЧС #{req['id']}",
                     old["id"], "warn")
            self._save(old)
            self.dirty = True
        path = Graph.path_to_source(prev, node, stand_node)
        t.dest_node = stand_node
        t.status, t.request_id = "busy", req["id"]
        req.update(status=ST_ROUTE, tech_id=t.id, eta=choice["eta"], violation=violation, note="",
                   travel_mode=choice.get("mode", "walk"), vehicle_id=choice.get("vehicle_id"))
        if choice.get("mode") == "vehicle" and choice.get("vehicle_id") in self.engineer_vehicles:
            vehicle = self.engineer_vehicles[choice["vehicle_id"]]
            approach = self._network_route((vehicle.x, vehicle.y), node)
            if approach:
                vehicle.route, vehicle.path_nodes = approach
                vehicle.travelled = 0.0
                vehicle.dest_node = node
                vehicle.delivery_points = [start] + [self.graph.nodes[n] for n in path]
                vehicle.status = TV_TO_PICKUP
                vehicle.request_id, vehicle.tech_id = req["id"], t.id
                t.route, t.path_nodes, t.travelled = None, None, 0.0
                t.vehicle_id = vehicle.id
                txt = (f"Заявка #{req['id']}: {t.name} едет на {vehicle.name}, "
                       f"подача {self.fmt(choice['eta'])}, путь {choice['dist']} м")
            else:
                # If topology changed after evaluation, safely fall back to walking.
                req.update(travel_mode="walk", vehicle_id=None)
                t.vehicle_id = None
                pts = [start] + [self.graph.nodes[n] for n in path]
                t.route, t.travelled, t.path_nodes = Route(pts), 0.0, path
                txt = (f"Заявка #{req['id']}: назначен {t.name} ({t.qual}), "
                       f"путь {choice['dist']} м, ETA {self.fmt(choice['eta'])}")
        else:
            t.vehicle_id = None
            pts = [start] + [self.graph.nodes[n] for n in path]
            t.route, t.travelled, t.path_nodes = Route(pts), 0.0, path
            txt = (f"Заявка #{req['id']}: назначен {t.name} ({t.qual}), "
                   f"путь {choice['dist']} м, ETA {self.fmt(choice['eta'])}")
        if violation:
            txt += " — НОРМАТИВ 15:00 НЕДОСТИЖИМ"
        self.log(txt, req["id"], "warn" if violation else "assign")
        if t.route is not None and t.route.total == 0:
            self._arrive(t)

    def _send_engineer_vehicle_home(self, vehicle):
        route_data = self._network_route((vehicle.x, vehicle.y), vehicle.base_node)
        vehicle.request_id = None
        vehicle.tech_id = None
        vehicle.delivery_points = []
        vehicle.pickup_wait_left = 0.0
        if route_data and route_data[0].total > 0.5:
            vehicle.route, vehicle.path_nodes = route_data
            vehicle.travelled = 0.0
            vehicle.dest_node = vehicle.base_node
            vehicle.status = TV_RETURNING
        else:
            vehicle.x, vehicle.y = self.graph.nodes[vehicle.base_node]
            vehicle.route, vehicle.path_nodes = None, None
            vehicle.travelled = 0.0
            vehicle.dest_node = vehicle.base_node
            vehicle.status = TV_IDLE

    def _start_engineer_vehicle_delivery(self, vehicle):
        tech = self.techs.get(vehicle.tech_id)
        if tech is None:
            self._send_engineer_vehicle_home(vehicle)
            return
        route_data = self._network_route((vehicle.x, vehicle.y), tech.dest_node)
        if not route_data:
            req = self.requests.get(vehicle.request_id)
            walk_route = self._network_route((tech.x, tech.y), tech.dest_node)
            tech.vehicle_id = None
            if walk_route:
                if req:
                    req["note"] = "Маршрут автомобиля закрыт; инженер продолжит пешком"
                    req["travel_mode"] = "walk"
                    req["vehicle_id"] = None
                tech.route, tech.path_nodes = walk_route
                tech.travelled = 0.0
            else:
                # Neither mode can currently reach the stand: release both resources
                # and let the normal dispatcher retry once the closure is lifted.
                if req:
                    req.update(status=ST_WAIT, tech_id=None, vehicle_id=None,
                               travel_mode="walk", note="Маршрут перекрыт; заявка ожидает открытия проезда")
                tech.status, tech.request_id = "free", None
                tech.route = tech.path_nodes = None
                tech.dest_node = None
                self.dirty = True
            self._send_engineer_vehicle_home(vehicle)
            return
        vehicle.route, vehicle.path_nodes = route_data
        vehicle.travelled = 0.0
        vehicle.dest_node = tech.dest_node
        vehicle.status = TV_TO_STAND

    def _engineer_vehicle_step(self, vehicle, dt):
        if vehicle.status == TV_BOARDING:
            vehicle.pickup_wait_left -= dt
            if vehicle.pickup_wait_left <= 0:
                self._start_engineer_vehicle_delivery(vehicle)
            return
        if vehicle.route is None:
            return
        vehicle.travelled += TECH_VEHICLE_SPEED * dt
        if vehicle.travelled < vehicle.route.total:
            vehicle.x, vehicle.y = vehicle.route.pos(vehicle.travelled)
            if vehicle.status == TV_TO_STAND:
                tech = self.techs.get(vehicle.tech_id)
                if tech:
                    tech.x, tech.y = vehicle.x, vehicle.y
            return
        vehicle.x, vehicle.y = self.graph.nodes[vehicle.dest_node]
        vehicle.route, vehicle.path_nodes, vehicle.travelled = None, None, 0.0
        if vehicle.status == TV_TO_PICKUP:
            vehicle.status = TV_BOARDING
            vehicle.pickup_wait_left = TECH_VEHICLE_BOARD_SEC
        elif vehicle.status == TV_TO_STAND:
            tech = self.techs.get(vehicle.tech_id)
            if tech:
                tech.x, tech.y = vehicle.x, vehicle.y
                self._arrive(tech)
            else:
                self._send_engineer_vehicle_home(vehicle)
        elif vehicle.status == TV_RETURNING:
            vehicle.status = TV_IDLE
            vehicle.request_id = vehicle.tech_id = None

    def _arrive(self, t, forced=False, actor="диспетчер"):
        req = self.requests[t.request_id]
        vehicle_id = t.vehicle_id
        t.x, t.y = self.graph.nodes[t.dest_node]
        t.node, t.route, t.travelled = t.dest_node, None, 0.0
        t.path_nodes = None
        t.vehicle_id = None
        if req["status"] != ST_ROUTE:
            return
        req["status"] = ST_ARRIVED
        req["arrived_sim"] = self.sim_time
        req["response_sec"] = self.sim_time - req["created_sim"]
        req["within_norm"] = req["response_sec"] <= NORM_SEC
        verdict = "в норматив" if req["within_norm"] else "НОРМАТИВ НАРУШЕН"
        self.log(f"Заявка #{req['id']}: {t.name} прибыл на {req['stand']} за {self.fmt(req['response_sec'])} "
                 f"({verdict}){' — отмечено ' + actor if forced else ''}", req["id"],
                 "arrive" if req["within_norm"] else "warn")
        if vehicle_id and vehicle_id in self.engineer_vehicles:
            self._send_engineer_vehicle_home(self.engineer_vehicles[vehicle_id])
        self._save(req)

    def dispatch_waiting(self):
        self.dirty = False
        waiting = sorted((r for r in self.requests.values() if r["status"] == ST_WAIT),
                         key=lambda r: (r["priority"] != "emergency", r["created_sim"], r["id"]))
        for req in waiting:
            if req["status"] == ST_WAIT and self._try_assign(req):
                self._save(req)

    def set_edge_blocked(self, edge_id, blocked):
        self.graph.set_blocked(edge_id, blocked)
        e = self.graph.edges[edge_id]
        self.log(f"Участок {e.get('name') or edge_id} {'закрыт' if blocked else 'открыт'}", None, "warn")
        if blocked:
            self._reroute_affected(edge_id)
        for vehicle in self.engineer_vehicles.values():
            if vehicle.status != TV_IDLE:
                self._reroute_engineer_vehicle(vehicle)
        self.dirty = True

    def _reroute_engineer_vehicle(self, vehicle):
        if not vehicle.route or not vehicle.path_nodes or vehicle.dest_node is None:
            return False
        route_data = self._network_route((vehicle.x, vehicle.y), vehicle.dest_node)
        if not route_data:
            return False
        vehicle.route, vehicle.path_nodes = route_data
        vehicle.travelled = 0.0
        return True

    def _reroute_tech(self, t):
        if t.route is None or not t.path_nodes:
            return False
        cum = t.route.cum
        i = bisect.bisect_right(cum, t.travelled) - 1
        i = max(0, min(i, len(t.route.points) - 2))
        if i + 1 >= len(t.path_nodes):
            return False
        next_node = t.path_nodes[i + 1]

        dist, prev = self.graph.tree(next_node)
        if t.dest_node not in dist:
            return False

        new_path = list(reversed(Graph.path_to_source(prev, t.dest_node, next_node)))
        current_pos = t.route.pos(t.travelled)

        pts = [current_pos]
        for n in new_path:
            nx, ny = self.graph.nodes[n]
            if abs(nx - pts[-1][0]) > 0.5 or abs(ny - pts[-1][1]) > 0.5:
                pts.append((nx, ny))
        if len(pts) < 2:
            return False

        t.route = Route(pts)
        t.travelled = 0.0
        t.path_nodes = [next_node] + new_path[1:] if new_path and new_path[0] == next_node else new_path
        return True

    def _reroute_affected(self, edge_id):
        e = self.graph.edges[edge_id]
        a, b = e["a"], e["b"]
        for t in self.techs.values():
            if t.route is None or not t.path_nodes:
                continue
            cum = t.route.cum
            i = bisect.bisect_right(cum, t.travelled) - 1
            i = max(0, min(i, len(t.route.points) - 2))
            remaining = t.path_nodes[i:]
            if not any({u, v} == {a, b} for u, v in zip(remaining, remaining[1:])):
                continue
            rid = t.request_id
            if self._reroute_tech(t):
                self.log(f"{t.name} перенаправлен: объезд закрытого участка "
                         f"{e.get('name') or edge_id}", rid, "warn")
            else:
                self.log(f"{t.name}: участок {e.get('name') or edge_id} отрезал маршрут, "
                         f"цель недостижима", rid, "warn")

    def set_speed(self, speed):
        if speed not in (1, 5, 10, 30, 60):
            raise ValueError("Допустимая скорость модели: 1, 5, 10, 30, 60")
        self.sim_speed = float(speed)

    def set_running(self, running):
        self.sim_running = bool(running)

    # ---------- резервирование рёбер самолётами ----------
    def _ac_can_reserve(self, edges, aid):
        return all(e not in self.edge_owner or self.edge_owner[e] == aid for e in edges)

    def _ac_reserve(self, edges, aid):
        for e in edges:
            self.edge_owner[e] = aid

    def _ac_release(self, edges, aid):
        for e in edges:
            if self.edge_owner.get(e) == aid:
                del self.edge_owner[e]

    def _path_edges(self, path):
        edges = []
        for a, b in zip(path, path[1:]):
            eid = self.graph.find_edge(a, b)
            if eid is None:
                return None
            edges.append(eid)
        return edges

    def _spawn_aircraft(self):
        if not self.entry_points:
            return None
        busy = {a.stand_id for a in self.aircraft.values() if a.stand_id}
        free = [sid for sid in self.stands if sid not in busy]
        if not free:
            return None
        stand_id = random.choice(free)
        entry_node = random.choice(self.entry_points)
        stand_node = self.stands[stand_id]["node"]

        dist, prev = self.graph.tree(entry_node)
        if stand_node not in dist:
            return None
        path = list(reversed(Graph.path_to_source(prev, stand_node, entry_node)))
        path_edges = self._path_edges(path)
        if not path_edges:
            return None

        aid = self.next_aircraft_id
        if not self._ac_can_reserve(path_edges, aid):
            return None

        self.next_aircraft_id += 1
        self._ac_reserve(path_edges, aid)

        ac = Aircraft(aid, _random_flight(), random.choice(AIRCRAFT_MODELS), entry_node, self.sim_time)
        ac.stand_id = stand_id
        ac.dest_node = stand_node
        pts = [self.graph.nodes[entry_node]] + [self.graph.nodes[n] for n in path[1:]]
        ac.route = Route(pts)
        ac.path_nodes = path
        ac.path_edges = path_edges
        ac.last_edge_idx = 0
        ac.x, ac.y = self.graph.nodes[entry_node]

        ac.brief = llm.template_now(ac.flight, ac.model)

        self.aircraft[aid] = ac
        self.log(f"Рейс {ac.flight} ({ac.model}) заходит на стоянку {stand_id}", None, "info")

        def on_brief(res, aid=aid, flight=ac.flight):
            a = self.aircraft.get(aid)
            if a is None:
                return
            a.brief = res
            if res.get("error"):
                self.log(f"LLM {flight}: {res['error']} — использован шаблон", None, "warn")
            else:
                self.log(f"LLM {flight}: карточка готова ({res.get('source')})", None, "info")
        llm.generate_async(ac.flight, ac.model, "прилёт", on_brief)
        return ac

    def _aircraft_depart(self, ac):
        if ac.route is not None:
            return False
        pending = [s for s in ac.services if s["status"] != "done" and s["gtype"] != "pushback"]
        if pending:
            return False
        stand_node = self.stands[ac.stand_id]["node"]
        exit_node = random.choice(self.entry_points)
        dist, prev = self.graph.tree(stand_node)
        if exit_node not in dist:
            return False
        path = list(reversed(Graph.path_to_source(prev, exit_node, stand_node)))
        path_edges = self._path_edges(path)
        if not path_edges:
            return False
        if not self._ac_can_reserve(path_edges, ac.id):
            return False

        self._ac_reserve(path_edges, ac.id)
        self._cancel_requests_for_aircraft(ac.id, f"борт {ac.flight} готовится к вылету")
        self._recall_gse_from(ac.id)

        old_stand = ac.stand_id
        ac.status = AS_DEPARTING
        ac.actual_dep_sim = self.sim_time
        ac.stand_id = None
        ac.dest_node = exit_node
        ac.parked_until = None
        ac.services = []
        ac.service_idx = 0
        pts = [self.graph.nodes[stand_node]] + [self.graph.nodes[n] for n in path[1:]]
        ac.route = Route(pts)
        ac.path_nodes = path
        ac.path_edges = path_edges
        ac.last_edge_idx = 0
        ac.travelled = 0.0
        self.log(f"Рейс {ac.flight} начал вылет со стоянки {old_stand}", None, "info")
        return True

    def _aircraft_reach_dest(self, ac):
        if ac.path_edges:
            self._ac_release(ac.path_edges[ac.last_edge_idx:], ac.id)
        nx, ny = self.graph.nodes[ac.dest_node]
        ac.x, ac.y = nx, ny
        ac.route = None
        ac.path_nodes = None
        ac.path_edges = None
        ac.last_edge_idx = 0
        ac.travelled = 0.0
        if ac.status == AS_INBOUND:
            ac.status = AS_PARKED
            ac.actual_arr_sim = self.sim_time
            ac.sched_dep_sim = self.sim_time + AIRCRAFT_PARK_DURATION
            ac.parked_until = ac.sched_dep_sim
            self._plan_services(ac)
            delay = ac.actual_arr_sim - ac.sched_arr_sim
            if delay > 30:
                self.log(f"Рейс {ac.flight} прибыл с задержкой "
                         f"{self.fmt(delay)} на стоянку {ac.stand_id}", None, "warn")
            else:
                self.log(f"Рейс {ac.flight} прибыл на стоянку {ac.stand_id}", None, "arrive")
        elif ac.status == AS_DEPARTING:
            self.log(f"Рейс {ac.flight} покинул перрон", None, "info")
            self.aircraft.pop(ac.id, None)

    def _ac_step(self, ac, dt):
        if ac.route is None:
            if ac.status == AS_PARKED and ac.parked_until is not None:
                if self.sim_time >= ac.parked_until:
                    if not self._aircraft_depart(ac):
                        ac.parked_until = self.sim_time + 10.0
            return

        old = ac.travelled
        new = min(old + AIRCRAFT_SPEED * dt, ac.route.total)
        cum = ac.route.cum

        while (ac.last_edge_idx < len(ac.path_edges) - 1
               and new >= cum[ac.last_edge_idx + 1]):
            eid = ac.path_edges[ac.last_edge_idx]
            if self.edge_owner.get(eid) == ac.id:
                del self.edge_owner[eid]
            ac.last_edge_idx += 1

        ac.travelled = new
        if ac.travelled >= ac.route.total:
            self._aircraft_reach_dest(ac)
        else:
            ac.x, ac.y = ac.route.pos(ac.travelled)

    def _aircraft_tick(self, dt):
        if self.sim_time - self.last_spawn_sim >= AIRCRAFT_SPAWN_INTERVAL:
            if self._spawn_aircraft() is not None:
                self.last_spawn_sim = self.sim_time
            else:
                self.last_spawn_sim = self.sim_time - AIRCRAFT_SPAWN_INTERVAL + 15.0
        for ac in list(self.aircraft.values()):
            self._ac_step(ac, dt)

    def spawn_aircraft_manual(self):
        return self._spawn_aircraft()

    def force_depart(self, aid):
        ac = self.aircraft.get(aid)
        if not ac:
            raise ValueError("Рейс не найден")
        if ac.status != AS_PARKED:
            raise ValueError("Борт ещё не на стоянке или уже вылетает")
        for s in ac.services:
            if s["status"] != "done":
                s["status"] = "done"
        if not self._aircraft_depart(ac):
            raise ValueError("Не удалось начать вылет: рулёжные дорожки заняты")
        return True

    # ---------- сервисные работы и GSE ----------
    def _plan_services(self, ac):
        pool = ["fuel", "catering", "baggage", "cleaning"]
        random.shuffle(pool)
        n = random.randint(2, len(pool))
        plan = pool[:n] + ["pushback"]
        ac.services = [{"gtype": g, "status": "pending", "gse_id": None} for g in plan]
        ac.service_idx = 0
        self._dispatch_next_service(ac)
        self.log(f"Рейс {ac.flight}: план обслуживания — "
                 f"{', '.join(GSE_TYPES[g]['label'] for g in plan)}", None, "info")

    def _nearest_idle_gse(self, gtype, dest_node):
        """Ближайшая свободная машина данного типа к dest_node (по графу).
        Возвращает (unit, distance, path_from_src_to_dest) или (None, None, None)."""
        dist, prev = self.graph.tree(dest_node)
        best_unit, best_d = None, None
        for g in self.gse.values():
            if g.gtype != gtype or g.status != GS_IDLE:
                continue
            src = g.base_node
            d = dist.get(src)
            if d is None:
                continue
            if best_d is None or d < best_d:
                best_unit, best_d = g, d
        if best_unit is None:
            return None, None, None
        # tree(dest_node) считает расстояния ОТ dest_node, а prev[v]=u значит
        # "в v пришли из u". Поэтому path_to_source(prev, base, dest) сразу даёт
        # путь [base, ..., dest] — уже в нужном направлении. reversed() НЕ нужен.
        path = Graph.path_to_source(prev, best_unit.base_node, dest_node)
        return best_unit, best_d, path

    def _dispatch_next_service(self, ac):
        while ac.service_idx < len(ac.services):
            svc = ac.services[ac.service_idx]
            if svc["status"] == "done":
                ac.service_idx += 1
                continue
            if svc["status"] == "active":
                return

            stand_node = self.stands[ac.stand_id]["node"]
            # ГЛАВНОЕ ИЗМЕНЕНИЕ: берём БЛИЖАЙШУЮ свободную машину нужного типа,
            # а не первую попавшуюся.
            unit, dist_to_stand, path = self._nearest_idle_gse(svc["gtype"], stand_node)
            if unit is None:
                # Либо все машины этого типа заняты, либо недостижимы — попробуем позже
                return

            pts = [self.graph.nodes[unit.base_node]] + [self.graph.nodes[n] for n in path[1:]]
            unit.route = Route(pts)
            unit.travelled = 0.0
            unit.path_nodes = path
            unit.dest_node = stand_node
            unit.status = GS_TO_STAND
            unit.target_aircraft = ac.id
            unit.target_service = svc
            svc["status"] = "active"
            svc["gse_id"] = unit.id
            self.log(f"{unit.gtype_label()} едет к борту {ac.flight} "
                     f"на стоянку {ac.stand_id} (путь {round(dist_to_stand)} м)",
                     None, "info")
            return

    def _recall_gse_from(self, aid):
        for g in self.gse.values():
            if g.target_aircraft == aid and g.status in (GS_TO_STAND, GS_WORKING):
                self._send_gse_home(g)

    def _send_gse_home(self, g):
        # Возвращаем на СВОЮ базу (не переезжаем на другую)
        if g.base_node not in self.graph.nodes:
            g.status, g.route, g.target_aircraft, g.target_service = GS_IDLE, None, None, None
            return
        node = self.graph.nearest_node(g.x, g.y)
        dist, prev = self.graph.tree(node)
        if g.base_node not in dist:
            g.status, g.route, g.target_aircraft, g.target_service = GS_IDLE, None, None, None
            return
        path = list(reversed(Graph.path_to_source(prev, g.base_node, node)))
        pts = [(g.x, g.y)] + [self.graph.nodes[n] for n in path[1:]]
        g.route = Route(pts)
        g.travelled = 0.0
        g.path_nodes = path
        g.dest_node = g.base_node
        g.status = GS_TO_BASE
        g.work_left = 0.0

    def _gse_step(self, g, dt):
        if g.route is None:
            if g.status == GS_WORKING:
                g.work_left -= dt
                if g.work_left <= 0:
                    ac = self.aircraft.get(g.target_aircraft)
                    if ac and g.target_service:
                        g.target_service["status"] = "done"
                        self.log(f"{g.gtype_label()} завершила работу у борта {ac.flight}",
                                 None, "done")
                    self._send_gse_home(g)
                    for a in self.aircraft.values():
                        if a.status == AS_PARKED:
                            self._dispatch_next_service(a)
            return

        g.travelled += GSE_SPEED * dt
        if g.travelled >= g.route.total:
            nx, ny = self.graph.nodes[g.dest_node]
            g.x, g.y = nx, ny
            g.route = None
            g.path_nodes = None
            g.travelled = 0.0
            if g.status == GS_TO_STAND:
                g.status = GS_WORKING
                g.work_left = float(GSE_TYPES[g.gtype]["duration"])
            elif g.status == GS_TO_BASE:
                g.status = GS_IDLE
                g.target_aircraft = None
                g.target_service = None
                for a in self.aircraft.values():
                    if a.status == AS_PARKED:
                        self._dispatch_next_service(a)
        else:
            g.x, g.y = g.route.pos(g.travelled)

    def _gse_tick(self, dt):
        for g in self.gse.values():
            self._gse_step(g, dt)

    def tick(self, dt_wall):
        dt = dt_wall * self.sim_speed if self.sim_running else 0.0
        self.sim_time += dt
        for t in self.techs.values():
            if t.route is not None:
                t.travelled += t.speed * dt
                if t.travelled >= t.route.total:
                    self._arrive(t)
                else:
                    t.x, t.y = t.route.pos(t.travelled)
        self._aircraft_tick(dt)
        for vehicle in self.engineer_vehicles.values():
            self._engineer_vehicle_step(vehicle, dt)
        self._gse_tick(dt)
        if self.dirty:
            self.dispatch_waiting()

    def _req_view(self, r):
        v = dict(r)
        v["status_ru"] = STATUS_RU[r["status"]]
        v["elapsed"] = round((self.sim_time if r["status"] in (ST_WAIT, ST_ROUTE) else
                              (r["arrived_sim"] or self.sim_time)) - r["created_sim"], 1)
        v["eta_left"] = None
        if r["status"] == ST_ROUTE and r["tech_id"]:
            t = self.techs[r["tech_id"]]
            if t.route:
                v["eta_left"] = round(max(t.route.total - t.travelled, 0) / t.speed, 1)
            elif t.vehicle_id:
                vehicle = self.engineer_vehicles.get(t.vehicle_id)
                if vehicle:
                    left = (max(vehicle.route.total - vehicle.travelled, 0) / TECH_VEHICLE_SPEED
                            if vehicle.route else 0.0)
                    if vehicle.status == TV_TO_PICKUP:
                        left += TECH_VEHICLE_BOARD_SEC
                        if vehicle.delivery_points:
                            left += Route(vehicle.delivery_points).total / TECH_VEHICLE_SPEED
                    elif vehicle.status == TV_BOARDING:
                        left += vehicle.pickup_wait_left
                        if vehicle.delivery_points:
                            left += Route(vehicle.delivery_points).total / TECH_VEHICLE_SPEED
                    v["eta_left"] = round(left, 1)
        v["tech_name"] = self.techs[r["tech_id"]].name if r["tech_id"] else None
        return v

    def stats(self):
        done = [r for r in self.requests.values() if r["status"] == ST_DONE]
        judged = [r for r in self.requests.values() if r["within_norm"] is not None]
        etas = [r["response_sec"] for r in judged if r["response_sec"] is not None]
        ac_total = len(self.aircraft)
        ac_parked = sum(1 for a in self.aircraft.values() if a.status == AS_PARKED)
        ac_inbound = sum(1 for a in self.aircraft.values() if a.status == AS_INBOUND)
        ac_dep = sum(1 for a in self.aircraft.values() if a.status == AS_DEPARTING)
        gse_busy = sum(1 for g in self.gse.values() if g.status != GS_IDLE)
        return {
            "techs_total": len(self.techs),
            "techs_free": sum(1 for t in self.techs.values() if t.status == "free"),
            "active": sum(1 for r in self.requests.values() if r["status"] in ACTIVE),
            "waiting": sum(1 for r in self.requests.values() if r["status"] == ST_WAIT),
            "done": len(done),
            "avg_response": round(sum(etas) / len(etas), 1) if etas else None,
            "norm_pct": round(100 * sum(1 for r in judged if r["within_norm"]) / len(judged)) if judged else None,
            "aircraft_total": ac_total,
            "aircraft_parked": ac_parked,
            "aircraft_inbound": ac_inbound,
            "aircraft_departing": ac_dep,
            "gse_total": len(self.gse),
            "gse_busy": gse_busy,
        }

    def tech_stats(self, tech_id):
        own = [r for r in self.requests.values() if r.get("tech_id") == tech_id]
        done = [r for r in own if r["status"] == ST_DONE]
        judged = [r for r in own if r["within_norm"] is not None]
        etas = [r["response_sec"] for r in judged if r["response_sec"] is not None]
        return {
            "active": sum(1 for r in own if r["status"] in ACTIVE),
            "done": len(done),
            "avg_response": round(sum(etas) / len(etas), 1) if etas else None,
            "norm_pct": round(100 * sum(1 for r in judged if r["within_norm"]) / len(judged)) if judged else None,
        }

    def engineer_snapshot(self, tech_id):
        """Состояние только для личного приложения инженера.

        Другие техники, чужие заявки и служебные кандидаты сюда не попадают.
        """
        tech = self.techs.get(tech_id)
        if tech is None:
            return {
                "sim_time": round(self.sim_time, 1), "sim_speed": self.sim_speed, "norm": NORM_SEC,
                "me": None, "techs": [], "vehicles": [], "active": [], "history": [], "events": [],
                "aircraft": [], "stats": {"active": 0, "done": 0, "avg_response": None, "norm_pct": None},
            }
        own = [r for r in self.requests.values() if r.get("tech_id") == tech_id]
        views = []
        for r in own:
            v = self._req_view(r)
            v.pop("candidates", None)
            views.append(v)
        active = sorted((v for v in views if v["status"] in ACTIVE),
                        key=lambda v: (v["priority"] != "emergency", v["id"]))
        history = sorted((v for v in views if v["status"] not in ACTIVE), key=lambda v: -v["id"])[:40]
        own_ids = {r["id"] for r in own}
        events = [e for e in self.events if e.get("request_id") in own_ids][:60]
        aircraft_ids = {r.get("aircraft_id") for r in own if r.get("aircraft_id")}
        return {
            "sim_time": round(self.sim_time, 1), "sim_speed": self.sim_speed,
            "sim_running": self.sim_running, "norm": NORM_SEC,
            "me": tech.to_dict(),
            "techs": [tech.to_dict()],
            "vehicles": [v.to_dict() for v in self.engineer_vehicles.values()
                         if v.tech_id == tech_id and v.status != TV_IDLE],
            "active": active,
            "history": history,
            "events": events,
            "aircraft": [a.to_dict(self.sim_time) for a in self.aircraft.values() if a.id in aircraft_ids],
            "stats": self.tech_stats(tech_id),
        }

    def snapshot(self):
        views = [self._req_view(r) for r in self.requests.values()]
        active = sorted((v for v in views if v["status"] in ACTIVE),
                        key=lambda v: (v["priority"] != "emergency", v["id"]))
        history = sorted((v for v in views if v["status"] not in ACTIVE), key=lambda v: -v["id"])[:100]
        return {
            "sim_time": round(self.sim_time, 1), "sim_speed": self.sim_speed,
            "sim_running": self.sim_running, "norm": NORM_SEC,
            "techs": [t.to_dict() for t in self.techs.values()],
            "aircraft": [a.to_dict(self.sim_time) for a in self.aircraft.values()],
            "gse": [g.to_dict() for g in self.gse.values()],
            "vehicles": [v.to_dict() for v in self.engineer_vehicles.values()],
            "vehicle_speed": TECH_VEHICLE_SPEED,
            "gse_types": {k: {"label": v["label"], "color": v["color"],
                              "short": v["short"], "lane": v.get("lane", 0)}
                          for k, v in GSE_TYPES.items()},
            "active": active, "history": history, "events": self.events[:100],
            "stats": self.stats(), "blocked": self.graph.blocked_ids(),
            "llm": llm.status_info(),
        }
