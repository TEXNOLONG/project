"""Строит карту РЕАЛЬНОГО аэропорта из OpenStreetMap в формате проекта.

Примеры:
    python tools/osm_to_airport.py --icao UUEE --out data/airport_sheremetyevo.json
    python tools/osm_to_airport.py --bbox 37.32,55.96,37.46,55.99 --name "Шереметьево" \
        --out data/airport_sheremetyevo.json
    python tools/osm_to_airport.py --input raw_uuww.json --out data/airport_vnukovo.json
    python tools/osm_to_airport.py --input svo.osm --out data/airport_sheremetyevo.json  # XML

`--icao` использует Overpass API (иногда перегружен). `--bbox` качает данные
напрямую с api.openstreetmap.org и работает, когда Overpass недоступен.
Порядок bbox: minlon,minlat,maxlon,maxlat (как у OSM API).

Запуск системы с новой картой:  python run.py --airport data/airport_sheremetyevo.json
Данные © участники OpenStreetMap (лицензия ODbL) — укажите это в презентации.

Что берётся: aeroway = runway, taxiway, taxilane, apron, terminal, hangar,
parking_position / gate / aircraft_stand. Рулёжные дорожки становятся рёбрами графа
(в узлах пересечения OSM-линии должны делить общий узел — так обычно и есть).
"""
import argparse
import json
import math
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque

OVERPASS = "https://overpass-api.de/api/interpreter"
OSM_API = "https://api.openstreetmap.org/api/0.6/map?bbox={bbox}"
QUERY = """[out:json][timeout:90];
nwr["aeroway"="aerodrome"]["icao"="{icao}"];
map_to_area->.a;
(
  way(area.a)["aeroway"~"^(runway|taxiway|taxilane|apron|terminal|hangar)$"];
  nwr(area.a)["aeroway"~"^(parking_position|gate|aircraft_stand)$"];
);
out body;
>;
out skel qt;"""
STAND_TAGS = ("parking_position", "gate", "aircraft_stand")


def fetch(icao):
    req = urllib.request.Request(OVERPASS, data=("data=" + QUERY.format(icao=icao)).encode(),
                                 headers={"User-Agent": "dispatch-project/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def parse_osm_xml(data):
    """OSM XML -> структура, совместимая с ответом Overpass (node/way + tags)."""
    root = ET.fromstring(data) if isinstance(data, bytes) else ET.parse(data).getroot()
    elements = []
    for nd in root.findall("node"):
        e = {"type": "node", "id": int(nd.get("id")),
             "lat": float(nd.get("lat")), "lon": float(nd.get("lon"))}
        tags = {t.get("k"): t.get("v") for t in nd.findall("tag")}
        if tags:
            e["tags"] = tags
        elements.append(e)
    for w in root.findall("way"):
        e = {"type": "way", "id": int(w.get("id")),
             "nodes": [int(n.get("ref")) for n in w.findall("nd")]}
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        if tags:
            e["tags"] = tags
        elements.append(e)
    for rel in root.findall("relation"):
        e = {"type": "relation", "id": int(rel.get("id")),
             "members": [{"type": m.get("type"), "ref": int(m.get("ref")),
                          "role": m.get("role") or ""} for m in rel.findall("member")]}
        tags = {t.get("k"): t.get("v") for t in rel.findall("tag")}
        if tags:
            e["tags"] = tags
        elements.append(e)
    return {"elements": elements}


def fetch_bbox(bbox):
    """Скачать все объекты в bbox напрямую с OSM API (без Overpass)."""
    req = urllib.request.Request(OSM_API.format(bbox=bbox),
                                 headers={"User-Agent": "dispatch-project/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return parse_osm_xml(r.read())


def load_input(path):
    with open(path, "rb") as f:
        head = f.read(256).lstrip()
    if head.startswith(b"<"):
        with open(path, "rb") as f:
            return parse_osm_xml(f.read())
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def build(raw, name, max_stands):
    nodes = {e["id"]: e for e in raw["elements"] if e["type"] == "node"}
    ways = [e for e in raw["elements"] if e["type"] == "way" and e.get("tags")]
    taxi = [w for w in ways if w["tags"].get("aeroway") in ("taxiway", "taxilane")]
    if not taxi:
        raise SystemExit("В данных нет рулёжных дорожек (aeroway=taxiway/taxilane)")

    ref_pts = [(nodes[n]["lat"], nodes[n]["lon"]) for w in taxi for n in w["nodes"] if n in nodes]
    lat0, lon0 = centroid(ref_pts)
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110540

    def proj(n):
        return ((n["lon"] - lon0) * kx, -(n["lat"] - lat0) * ky)

    # --- граф рулёжных дорожек
    N, E = {}, []
    for w in taxi:
        ids = [n for n in w["nodes"] if n in nodes]
        for n in ids:
            N.setdefault(f"N{n}", proj(nodes[n]))
        for a, b in zip(ids, ids[1:]):
            if a != b:
                E.append({"a": f"N{a}", "b": f"N{b}", "kind": "taxiway", "name": w["tags"].get("ref", "")})
    adj = {}
    for e in E:
        adj.setdefault(e["a"], []).append(e["b"])
        adj.setdefault(e["b"], []).append(e["a"])
    # крупнейшая компонента связности
    seen, best = set(), []
    for s in adj:
        if s in seen:
            continue
        comp, q = [], deque([s])
        seen.add(s)
        while q:
            u = q.popleft()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        if len(comp) > len(best):
            best = comp
    keep = set(best)
    N = {k: v for k, v in N.items() if k in keep}
    E = [e for e in E if e["a"] in keep and e["b"] in keep]
    for i, e in enumerate(E, 1):
        e["id"] = f"E{i}"

    def nearest(pt):
        k = min(N, key=lambda n: math.hypot(N[n][0] - pt[0], N[n][1] - pt[1]))
        return k, math.hypot(N[k][0] - pt[0], N[k][1] - pt[1])

    # --- ВПП и площади
    runways, areas = [], []
    seen_area = set()

    def add_area(kind, name, pts):
        if len(pts) < 3:
            return
        cx, cy = centroid(pts)
        key = (kind, round(cx / 40), round(cy / 40))  # защита от дублей way + multipolygon
        if key in seen_area:
            return
        seen_area.add(key)
        areas.append({"kind": kind, "name": name or "",
                      "points": [[round(x), round(y)] for x, y in pts]})

    for w in ways:
        t = w["tags"]
        pts = [proj(nodes[n]) for n in w["nodes"] if n in nodes]
        if len(pts) < 2:
            continue
        if t.get("aeroway") == "runway":
            try:
                width = float(str(t.get("width", "45")).split()[0])
            except ValueError:
                width = 45
            runways.append({"name": t.get("ref", ""), "points": [[round(x), round(y)] for x, y in pts], "width": width})
        elif t.get("aeroway") in ("apron", "terminal", "hangar") and len(pts) >= 3:
            add_area(t["aeroway"], t.get("name", t.get("ref", "")), pts)
        elif t.get("building") in ("terminal", "hangar", "airport") and len(pts) >= 3:
            kind = "hangar" if t.get("building") == "hangar" else "terminal"
            add_area(kind, t.get("name", t.get("ref", "")), pts)

    # терминалы и ангары часто нарисованы мультиполигонами (relation) —
    # собираем внешние кольца из member-way, иначе у Домодедово не будет терминала
    way_by_id = {e["id"]: e for e in raw["elements"] if e["type"] == "way"}

    def relation_ring(rel):
        members = [way_by_id[m["ref"]] for m in rel.get("members", [])
                   if m.get("type") == "way" and m.get("role") != "inner" and m["ref"] in way_by_id]
        if not members:
            return []
        seq, used = list(members[0]["nodes"]), {0}
        changed = True
        while changed:
            changed = False
            for i, w in enumerate(members):
                if i in used or not w["nodes"]:
                    continue
                ns = w["nodes"]
                if ns[0] == seq[-1]:
                    seq += ns[1:]
                elif ns[-1] == seq[-1]:
                    seq += ns[::-1][1:]
                elif ns[-1] == seq[0]:
                    seq = ns[:-1] + seq
                elif ns[0] == seq[0]:
                    seq = ns[::-1][:-1] + seq
                else:
                    continue
                used.add(i)
                changed = True
                break
        return seq

    for e in raw["elements"]:
        if e["type"] != "relation":
            continue
        t = e.get("tags") or {}
        b = t.get("building")
        if t.get("aeroway") in ("terminal", "hangar"):
            kind = t["aeroway"]
        elif b in ("terminal", "airport"):
            kind = "terminal"
        elif b == "hangar":
            kind = "hangar"
        else:
            continue
        ids = relation_ring(e)
        pts = [proj(nodes[n]) for n in ids if n in nodes]
        add_area(kind, t.get("name", t.get("ref", "")), pts)

    # --- стоянки: все parking_position сохраняем как контуры для карты,
    #     для симуляции выбираем max_stands штук, равномерно по перрону.
    parking = []
    for e in raw["elements"]:
        t = e.get("tags") or {}
        if t.get("aeroway") not in STAND_TAGS:
            continue
        if e["type"] == "way":
            pts = [proj(nodes[n]) for n in e["nodes"] if n in nodes]
        elif e["type"] == "node":
            pts = [proj(e)]
        else:
            continue
        if len(pts) < 2:
            continue
        # контур стоянки (для way) или точка (для node)
        parking.append({"ref": t.get("ref") or t.get("name") or "",
                        "points": [[round(x), round(y)] for x, y in pts]})

    cands = []
    for e in raw["elements"]:
        t = e.get("tags") or {}
        if t.get("aeroway") not in STAND_TAGS:
            continue
        if e["type"] == "node":
            pt = proj(e)
        elif e["type"] == "way":
            pts = [proj(nodes[n]) for n in e["nodes"] if n in nodes]
            if not pts:
                continue
            pt = centroid(pts)
        else:
            continue
        k, d = nearest(pt)
        if d <= 250:
            cands.append((pt, t.get("ref") or t.get("name") or "", k))
    # выбираем плотный кластер стоянок (главный пассажирский перрон),
    # а не весь аэродром: иначе половина стоянок окажется в грузовой зоне
    if cands:
        center = max(cands, key=lambda c: sum(
            1 for o in cands if math.hypot(o[0][0] - c[0][0], o[0][1] - c[0][1]) < 500))[0]
        cluster = [c for c in cands if math.hypot(c[0][0] - center[0], c[0][1] - center[1]) < 500]
        if len(cluster) < max_stands:
            cluster = sorted(cands, key=lambda c: math.hypot(c[0][0] - center[0], c[0][1] - center[1]))
        cands = sorted(cluster[:max(max_stands * 2, max_stands)], key=lambda c: (c[0][0], c[0][1]))
    if len(cands) > max_stands:  # равномерная выборка вдоль перрона
        step = len(cands) / max_stands
        cands = [cands[int(i * step)] for i in range(max_stands)]
    stands, used = [], set()
    for i, (pt, ref, k) in enumerate(cands, 1):
        sid = f"ВС-{ref}" if ref else f"ВС-{i:02d}"
        if sid in used:
            sid = f"ВС-{i:02d}"
        used.add(sid)
        N[f"S{i}"] = pt
        E.append({"id": f"ES{i}", "a": k, "b": f"S{i}", "kind": "apron", "name": ""})
        stands.append({"id": sid, "node": f"S{i}", "heading": 0})

    # --- базы техников: посты линейного ТО у перрона, а не ангары в глубине
    #     (иначе на реальной карте пешком/на машине до стоянки несколько километров)
    bases = []
    post_pts = [N[f"S{i}"] for i in range(1, len(stands) + 1)]
    if post_pts:
        xs_s = [p[0] for p in post_pts]
        cx, cy = centroid(post_pts)
        spread = max(xs_s) - min(xs_s)
        anchors = [(min(xs_s) + 0.2 * spread, cy), (cx, cy), (max(xs_s) - 0.2 * spread, cy)]
        used_nodes = set()
        for i, (pt, nm) in enumerate(zip(
                anchors, ["Пост ТО · запад", "Пост ТО · центр", "Пост ТО · восток"]), 1):
            k = min((n for n in N if n.startswith("N") and n not in used_nodes),
                    key=lambda n: math.hypot(N[n][0] - pt[0], N[n][1] - pt[1]), default=None)
            if k is None:
                k = nearest(pt)[0]
            used_nodes.add(k)
            bases.append({"id": f"BASE{i}", "name": nm, "node": k})
    if not bases:
        hangars = [a for a in areas if a["kind"] == "hangar"][:3]
        for i, h in enumerate(hangars, 1):
            pt = centroid(h["points"])
            k, _ = nearest(pt)
            N[f"BASE{i}"] = pt
            E.append({"id": f"EBASE{i}", "a": k, "b": f"BASE{i}", "kind": "apron", "name": ""})
            bases.append({"id": f"BASE{i}", "name": h["name"] or f"Ангар {i}", "node": f"BASE{i}"})

    # --- сдвиг в положительные координаты
    xs = [p[0] for p in N.values()] + [q[0] for r in runways for q in r["points"]] + \
        [q[0] for a in areas for q in a["points"]] + [q[0] for p in parking for q in p["points"]]
    ys = [p[1] for p in N.values()] + [q[1] for r in runways for q in r["points"]] + \
        [q[1] for a in areas for q in a["points"]] + [q[1] for p in parking for q in p["points"]]
    ox, oy = min(xs) - 150, min(ys) - 150
    sh = lambda x, y: [round(x - ox), round(y - oy)]
    for r in runways:
        r["points"] = [sh(x, y) for x, y in r["points"]]
    for a in areas:
        a["points"] = [sh(x, y) for x, y in a["points"]]
    for p in parking:
        p["points"] = [sh(x, y) for x, y in p["points"]]
    return {
        "name": name, "schematic": False, "source": "© участники OpenStreetMap (ODbL)",
        "width": round(max(xs) - ox + 150), "height": round(max(ys) - oy + 150),
        "runways": runways, "areas": areas, "parking": parking,
        "nodes": [{"id": k, "x": sh(*v)[0], "y": sh(*v)[1]} for k, v in N.items()],
        "edges": E, "stands": stands, "bases": bases,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--icao", help="ICAO-код аэропорта: UUEE — Шереметьево, UUDD — Домодедово, UUWW — Внуково")
    src.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat — скачать напрямую с OSM API")
    src.add_argument("--input", help="готовый JSON-ответ Overpass или OSM XML")
    p.add_argument("--save-raw", help="сохранить сырой ответ Overpass в JSON")
    p.add_argument("--out", required=True)
    p.add_argument("--name", help="название на карте")
    p.add_argument("--max-stands", type=int, default=15)
    a = p.parse_args()
    if a.input:
        raw = load_input(a.input)
    elif a.bbox:
        raw = fetch_bbox(a.bbox)
    else:
        raw = fetch(a.icao)
    if a.save_raw:
        json.dump(raw, open(a.save_raw, "w", encoding="utf-8"), ensure_ascii=False)
    data = build(raw, a.name or a.icao or "Аэропорт", a.max_stands)
    json.dump(data, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"Готово: {a.out}: {len(data['nodes'])} узлов, {len(data['edges'])} рёбер, "
          f"{len(data['stands'])} стоянок, {len(data['bases'])} баз, {len(data['runways'])} ВПП, "
          f"{len(data['parking'])} мест стоянок")


if __name__ == "__main__":
    main()
