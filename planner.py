"""규칙 기반 일정 판단. 모든 값은 입력 JSON에서 읽고, 추정치는 최악값(max)으로 계산한다."""

import itertools

PARK_OPTIONS = ("keep", "short", "indoor")
OBS_OPTIONS = ("deck", "indoor")
MODE_LABEL = {"subway": "지하철", "bus": "버스", "taxi": "택시"}
KIND_LABEL = {"fixed": "확정", "estimate": "추정", "preference": "선호",
              "assumption": "가정", "input": "입력"}


def v(node):
    return node["value"] if isinstance(node, dict) and "value" in node else node


def tag(node):
    return f"[{KIND_LABEL.get(node.get('kind'), node.get('kind'))} {node.get('source')}]"


def hm(s):
    h, m = map(int, s.split(":"))
    return h * 60 + m


def fmt(t):
    return f"{t // 60:02d}:{t % 60:02d}"


def overlap(a0, a1, b0, b1):
    return max(0, min(a1, b1) - max(a0, b0))


def worst_travel(data, seg, mode, depart):
    """최악 이동시간과 적용된 지연(분)을 돌려준다."""
    dur = v(data["travel"][seg][mode])[1]
    extra = 0
    for d in data.get("delays", []):
        if d["segment"] == seg and d["mode"] == mode:
            ws, we = map(hm, v(d["window"]))
            if overlap(depart, depart + dur, ws, we) > 0:
                extra += v(d["extra_min"])[1]
    return dur + extra, extra


def evaluate(data, park, obs, mode):
    A, R, P, W = data["assumptions"], data["reservations"], data["preferences"], data["weights"]
    wx = data["weather"]
    people = v(data["trip"]["people"])
    steps, fails, outdoor, legs = [], [], [], []
    t = hm(v(data["trip"]["arrive"]))
    cost, taxi_uses = 0, 0

    def go(seg, m="subway"):
        nonlocal t, cost, taxi_uses
        dur, extra = worst_travel(data, seg, m, t)
        legs.append({"seg": seg, "mode": m, "depart": t, "dur": dur, "extra": extra,
                     "source": data["travel"][seg][m]["source"]})
        t += dur
        if m == "taxi":
            cost += v(A["taxi_fare_per_ride"])
            taxi_uses += 1
        else:
            cost += people * v(A["transit_fare_per_person"])

    # 미술관 (예약 규칙: 입장 시간대, 최대 관람시간)
    go("station_museum")
    m = R["museum"]
    es, ee = hm(v(m["entry_start"])), hm(v(m["entry_end"]))
    if t > ee:
        fails.append(f"미술관 입장 마감 {fmt(ee)} 이후 도착({fmt(t)}) {tag(m['entry_end'])}")
    start = max(t, es)
    stay = v(A["museum_stay_min"])
    if stay > v(m["max_stay_min"]):
        fails.append(f"미술관 관람 {stay}분 > 최대 {v(m['max_stay_min'])}분 {tag(m['max_stay_min'])}")
    steps.append(("미술관 M", t, start, start + stay))
    t = start + stay

    # 식당 (예약 규칙: 지각 한도)
    go("museum_restaurant")
    r = R["restaurant"]
    rt, late = hm(v(r["time"])), v(r["late_limit_min"])
    if t > rt + late:
        fails.append(f"식당 지각 한도 {fmt(rt + late)} 초과 도착({fmt(t)}) {tag(r['late_limit_min'])}")
    start = max(t, rt)
    steps.append(("식당 R", t, start, start + v(A["restaurant_stay_min"])))
    restaurant_arrive = t
    t = start + v(A["restaurant_stay_min"])

    # 한강공원 (유연 일정)
    go("restaurant_park")
    if park == "keep":
        stay, name = v(A["park_stay_min"]), "한강공원 P(야외)"
        outdoor.append((t, t + stay))
    elif park == "short":
        stay, name = v(P["max_outdoor_in_rain_min"]), "한강공원 P(야외, 축소)"
        outdoor.append((t, t + stay))
    else:
        stay, name = v(A["indoor_cafe_stay_min"]), "한강공원 인근 실내 카페(대체)"
    steps.append((name, t, t, t + stay))
    park_outdoor = stay if park != "indoor" else 0
    t += stay

    # 전망대 (실내층 입장 마감은 확정, 데크는 유연)
    go("park_observatory", mode)
    o = R["observatory"]
    last = hm(v(o["indoor_last_entry"]))
    if t > last:
        fails.append(f"전망대 실내층 입장 마감 {fmt(last)} 이후 도착({fmt(t)}) {tag(o['indoor_last_entry'])}")
    stay = v(A["observatory_stay_min"])
    if obs == "deck":
        outdoor.append((t, t + v(A["deck_time_min"])))
    steps.append(("전망대 O(" + ("실내층+야외 데크" if obs == "deck" else "실내층만") + ")", t, t, t + stay))
    t += stay

    # 공연 (절대 조건)
    go("observatory_concert")
    c = R["concert"]
    deadline = hm(v(c["entry_deadline"]))
    slack = deadline - t
    buffer = v(A["concert_buffer_min"])
    if slack < buffer:
        fails.append(f"공연 입장 마감 {fmt(deadline)}까지 여유 {slack}분 < 안전 여유 {buffer}분 "
                     f"{tag(c['entry_deadline'])}")
    steps.append(("공연장 C", t, t, None))

    if taxi_uses > v(P["taxi_max_uses"]):
        fails.append(f"택시 {taxi_uses}회 > 허용 {v(P['taxi_max_uses'])}회 {tag(P['taxi_max_uses'])}")
    if cost > v(P["budget_won"]):
        fails.append(f"교통비 추정 {cost:,}원 > 예산 {v(P['budget_won']):,}원 {tag(P['budget_won'])}")

    # 날씨 노출
    rw0, rw1 = map(hm, v(wx["rain_window"]))
    rain_out = sum(overlap(a, b, rw0, rw1) for a, b in outdoor)
    cont = max([b - a for a, b in outdoor if overlap(a, b, rw0, rw1) > 0], default=0)
    rain = v(wx["rain_prob"])
    rainy = rain >= v(A["rain_threshold_pct"])
    windy = v(wx["wind_ms"]) >= v(A["deck_wind_threshold_ms"])

    score = W["slack_per_min"] * min(slack, W["slack_cap_min"])
    score -= W["rain_exposure_per_min"] * rain_out * rain / 100
    if rainy and cont > v(P["max_outdoor_in_rain_min"]):
        score -= W["long_outdoor_penalty"]
    score += W["park_per_min"] * park_outdoor
    if obs == "deck":
        score += -W["deck_risk_penalty"] if windy else W["deck_bonus"]
    score -= W["cost_per_10k"] * cost / 10000 + W["taxi_penalty"] * taxi_uses

    visits = ["museum", "restaurant", "park" if park != "indoor" else "cafe", "observatory_indoor"]
    if obs == "deck":
        visits.append("observatory_deck")
    visits.append("concert")

    return {
        "park": park, "obs": obs, "mode": mode, "steps": steps, "legs": legs, "visits": visits,
        "fails": fails, "feasible": not fails, "score": round(score, 1),
        "concert_arrive": t, "slack": slack, "buffer": buffer, "cost": cost,
        "taxi_uses": taxi_uses, "rain_out": rain_out, "cont_outdoor": cont,
        "park_outdoor": park_outdoor, "rainy": rainy, "windy": windy,
        "restaurant_arrive": restaurant_arrive,
    }


def plan(data):
    modes = list(data["travel"]["park_observatory"].keys())
    cands = [evaluate(data, p, o, m) for p, o, m in itertools.product(PARK_OPTIONS, OBS_OPTIONS, modes)]
    feasible = sorted((c for c in cands if c["feasible"]), key=lambda c: -c["score"])
    # 같은 (공원, 전망대) 조합은 가장 점수가 높은 이동수단만 남겨 서로 다른 대안 1~3순위를 만든다.
    ranked, seen = [], set()
    for c in feasible:
        if (c["park"], c["obs"]) not in seen:
            seen.add((c["park"], c["obs"]))
            ranked.append(c)
    rejected = [c for c in cands if not c["feasible"]]
    return {"ranked": ranked[:3], "rejected": rejected, "all": cands}
