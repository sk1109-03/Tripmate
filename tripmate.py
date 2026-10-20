"""tripmate: 공연 입장 마감을 최악 추정치로 점검하고 유연 일정 대안 1~3순위를 제시하는 CLI.

사용 예:
  python tripmate.py --no-llm
  python tripmate.py --now 09:45 --rain 20 --wind 4
  python tripmate.py --delay 50 70 --concert-deadline 18:10
"""

import argparse
import json
import sys
from pathlib import Path

from planner import MODE_LABEL, fmt, hm, plan, tag, v

PARK_LABEL = {"keep": "한강공원 유지", "short": "한강공원 축소", "indoor": "한강공원→실내 카페 대체"}
OBS_LABEL = {"deck": "전망대 실내층+야외 데크", "indoor": "전망대 실내층만"}
SEG_LABEL = {"station_museum": "서울역→미술관", "museum_restaurant": "미술관→식당",
             "restaurant_park": "식당→한강공원", "park_observatory": "한강공원→전망대",
             "observatory_concert": "전망대→공연장"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="공연 입장 마감 기준 일정 점검")
    p.add_argument("--input", default=str(Path(__file__).resolve().parent / "input" / "default.json"))
    p.add_argument("--now", help="현재 시각 HH:MM")
    p.add_argument("--rain", type=int, help="강수확률(%%)")
    p.add_argument("--wind", type=float, help="평균 풍속(m/s)")
    p.add_argument("--delay", type=int, nargs=2, metavar=("MIN", "MAX"), help="한강공원→전망대 지하철 추가 지연(분)")
    p.add_argument("--concert-deadline", help="공연 입장 마감 HH:MM")
    p.add_argument("--buffer", type=int, help="공연 입장 전 안전 여유(분)")
    p.add_argument("--no-llm", action="store_true", help="LLM 요약 없이 규칙 결과만 출력")
    return p.parse_args(argv)


def override(node, value):
    node["value"] = value
    node["source"] = "CLI 입력"


def load(args):
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.now:
        override(data["now"], args.now)
    if args.rain is not None:
        override(data["weather"]["rain_prob"], args.rain)
    if args.wind is not None:
        override(data["weather"]["wind_ms"], args.wind)
    if args.delay:
        for d in data["delays"]:
            override(d["extra_min"], list(args.delay))
    if args.concert_deadline:
        override(data["reservations"]["concert"]["entry_deadline"], args.concert_deadline)
    if args.buffer is not None:
        override(data["assumptions"]["concert_buffer_min"], args.buffer)
    return data


def reasons(data, c):
    A, P, wx = data["assumptions"], data["preferences"], data["weather"]
    out = []
    dl = data["reservations"]["concert"]["entry_deadline"]
    out.append(f"최악 추정 기준 공연장 도착 {fmt(c['concert_arrive'])}, 입장 마감 {v(dl)}까지 여유 {c['slack']}분"
               f"(안전 여유 기준 {c['buffer']}분) {tag(dl)} {tag(A['concert_buffer_min'])}")
    rain, thr = v(wx["rain_prob"]), v(A["rain_threshold_pct"])
    if c["park"] == "keep":
        if c["rainy"]:
            out.append(f"강수확률 {rain}% 예보 시간대에 야외 연속 {c['cont_outdoor']}분 예상 - "
                       f"비 올 때 {v(P['max_outdoor_in_rain_min'])}분 이상 야외 회피 선호와 충돌할 수 있음 "
                       f"{tag(wx['rain_prob'])} {tag(P['max_outdoor_in_rain_min'])}")
        else:
            out.append(f"강수확률 {rain}%로 우천 판단 기준 {thr}% 미만 - 한강공원 {c['park_outdoor']}분 유지 "
                       f"(한강공원 희망 {v(P['park_wanted_by'])}명) {tag(wx['rain_prob'])} {tag(P['park_wanted_by'])}")
    elif c["park"] == "short":
        out.append(f"한강공원을 {c['park_outdoor']}분 이내로 줄여 비 예보 시간대 야외 노출 {c['rain_out']}분 - "
                   f"방문 희망은 일부 반영 {tag(wx['rain_prob'])} {tag(P['max_outdoor_in_rain_min'])}")
    else:
        out.append(f"야외 대신 인근 실내 카페로 대체(일행이 허용한 대체 방식) - 공원 야외 노출 0분 "
                   f"{tag(P['max_outdoor_in_rain_min'])}")
    wind, wthr = v(wx["wind_ms"]), v(A["deck_wind_threshold_ms"])
    if c["obs"] == "deck":
        if c["windy"]:
            out.append(f"풍속 {wind}m/s 예보(기준 {wthr}m/s 이상) - 야외 데크는 닫힐 가능성 있음 "
                       f"{tag(wx['wind_ms'])} {tag(data['facilities']['deck_status'])}")
        else:
            out.append(f"풍속 {wind}m/s 예보로 기준 {wthr}m/s 미만 - 야외 데크 포함 {tag(wx['wind_ms'])}")
    else:
        out.append(f"실내 전망층은 {v(data['facilities']['observatory_indoor'])} - 데크 운영 여부와 무관 "
                   f"{tag(data['facilities']['observatory_indoor'])}")
    leg = next(l for l in c["legs"] if l["seg"] == "park_observatory")
    delay_txt = f", 선로 점검 지연 최대 {leg['extra']}분 포함" if leg["extra"] else ""
    out.append(f"한강공원→전망대 {MODE_LABEL[leg['mode']]} 최악 {leg['dur']}분 예상{delay_txt} [추정 {leg['source']}"
               f"{', D06' if leg['extra'] else ''}] / 교통비 추정 {c['cost']:,}원(예산 {v(P['budget_won']):,}원) "
               f"{tag(P['budget_won'])} {tag(A['taxi_fare_per_ride'])}")
    return out


def timeline(c):
    parts = []
    for name, arrive, start, end in c["steps"]:
        span = f"{fmt(start)}~{fmt(end)}" if end else f"도착 {fmt(arrive)}"
        parts.append(f"{name} {span}")
    return " → ".join(parts)


def build_report(data, result):
    lines = []
    ranked = result["ranked"]
    R, A, wx, P = data["reservations"], data["assumptions"], data["weather"], data["preferences"]
    now = data["now"]
    lines.append(f"# tripmate 일정 점검 (현재 시각 {v(now)} {tag(now)})")
    lines.append("표기: [확정]=예약·입장 규칙, [추정]=예보·이동시간, [선호]=일행 의견, [가정]/[입력]=사용자 입력값")
    lines.append("")

    lines.append("## 1. 공연 입장 점검 (최악 추정 기준)")
    dl = R["concert"]["entry_deadline"]
    if ranked:
        best = ranked[0]
        lines.append(f"- 공연 입장 마감 {v(dl)} {tag(dl)}: 1순위 안의 최악 추정 도착 {fmt(best['concert_arrive'])}, "
                     f"여유 {best['slack']}분. 실제 이동시간에 따라 달라질 수 있음.")
    else:
        lines.append(f"- 공연 입장 마감 {v(dl)} {tag(dl)}: 검토한 유연 일정 조합 중 최악 추정치로 마감과 예약 규칙을 "
                     f"모두 지키는 안이 없음. 유연 일정을 생략하고 공연장 이동을 우선 검토해야 함.")
    lines.append(f"- 검토 조합 {len(result['all'])}개 중 조건 통과 {len(result['all']) - len(result['rejected'])}개, "
                 f"탈락 {len(result['rejected'])}개")
    lines.append("")

    lines.append("## 2. 현재 유지할 일정")
    lines.append(f"- 공연장 C: 절대 조건. 입장 마감 {v(dl)}, 환불 불가 {tag(dl)}")
    m = R["museum"]
    museum_note = (f"- 미술관 M: {v(m['entry_start'])}~{v(m['entry_end'])} 입장, 최대 {v(m['max_stay_min'])}분 "
                   f"{tag(m['entry_start'])}. {v(m['wanted_by'])}명이 꼭 가고 싶어 함 {tag(m['wanted_by'])}")
    if hm(v(now)) >= hm(v(m["refund_deadline"])):
        museum_note += f". 환불 마감 {v(m['refund_deadline'])}이 지나 취소해도 환불 불가 {tag(m['refund_deadline'])}"
    lines.append(museum_note)
    r = R["restaurant"]
    ra = ranked[0]["restaurant_arrive"] if ranked else None
    lines.append(f"- 식당 R: {v(r['time'])} 예약, 지각 한도 {v(r['late_limit_min'])}분 {tag(r['late_limit_min'])}"
                 + (f". 최악 추정 도착 {fmt(ra)}" if ra is not None else ""))
    if ranked:
        o = R["observatory"]
        lines.append(f"- 전망대 O 실내층: 입장 마감 {v(o['indoor_last_entry'])} {tag(o['indoor_last_entry'])}. "
                     f"실내층은 {v(data['facilities']['observatory_indoor'])} {tag(data['facilities']['observatory_indoor'])}. "
                     f"1~3순위 안 모두에 포함됨")
    lines.append("")

    lines.append("## 3. 변경 가능 일정 1~3순위 (한강공원·전망대 데크·이동수단)")
    if not ranked:
        lines.append("- 조건을 통과한 대안 없음. 아래 탈락 사유를 확인하세요.")
    for i, c in enumerate(ranked, 1):
        leg = next(l for l in c["legs"] if l["seg"] == "park_observatory")
        lines.append(f"### {i}순위: {PARK_LABEL[c['park']]} / {OBS_LABEL[c['obs']]} / "
                     f"한강공원→전망대 {MODE_LABEL[leg['mode']]} (점수 {c['score']})")
        lines.append(f"- 일정(최악 추정): {timeline(c)}")
        for reason in reasons(data, c):
            lines.append(f"- 이유: {reason}")
    lines.append("")

    lines.append("## 4. 탈락한 조합 (대표 사유)")
    seen = {}
    for c in result["rejected"]:
        for f in c["fails"]:
            key = f.split(" ")[0] + f.split(" ")[1]
            seen.setdefault(key, (f, PARK_LABEL[c["park"]], OBS_LABEL[c["obs"]], MODE_LABEL[c["mode"]]))
    if not seen:
        lines.append("- 없음")
    for f, p_, o_, m_ in seen.values():
        lines.append(f"- {p_}/{o_}/{m_}: {f}")
    lines.append("")

    lines.append("## 5. 불확실성 (예보·추정 정보)")
    prev = v(wx["previous"])
    lines.append(f"- 강수: {v(wx['issued'])} 예보 기준 {'~'.join(v(wx['rain_window']))} 강수확률 {v(wx['rain_prob'])}% "
                 f"(이전 {prev['issued']} 예보 {prev['rain_prob']}%). 확률 예보이며 시간대별 강수 위치·강도는 바뀔 수 있음 "
                 f"{tag(wx['rain_prob'])}")
    windy = v(wx["wind_ms"]) >= v(A["deck_wind_threshold_ms"])
    lines.append(f"- 바람: 평균 풍속 {v(wx['wind_ms'])}m/s 예보(이전 {prev['wind_ms']}m/s). 전망대 야외 데크 상태: "
                 f"{v(data['facilities']['deck_status'])}"
                 + (" - 기준 풍속 이상이라 데크가 닫힐 가능성을 고려함" if windy else "")
                 + f" {tag(wx['wind_ms'])} {tag(data['facilities']['deck_status'])}")
    for d in data["delays"]:
        lines.append(f"- 교통: {SEG_LABEL[d['segment']]} {MODE_LABEL[d['mode']]} {'~'.join(v(d['window']))} "
                     f"추가 {v(d['extra_min'])[0]}~{v(d['extra_min'])[1]}분 지연 가능(최대값으로 계산, 실제와 다를 수 있음) "
                     f"{tag(d['extra_min'])}")
    lines.append("- 이동시간: 모든 구간은 범위 추정치의 최대값으로 계산했으며 대기시간은 포함하지 않음 [추정 D03]")
    lines.append(f"- 한강공원 야외 프로그램: {v(data['facilities']['park_programs'])} {tag(data['facilities']['park_programs'])}")
    lines.append("")

    lines.append("## 6. 사용자가 확인할 항목")
    if hm(v(now)) < hm(v(m["refund_deadline"])):
        lines.append(f"- 미술관 취소 여부: 현재 {v(now)}, 환불 마감 {v(m['refund_deadline'])} 전이라 지금 취소하면 전액 환불 가능 "
                     f"{tag(m['refund_deadline'])}. 다만 {v(m['wanted_by'])}명이 꼭 가고 싶어 해서 유지를 권장함 "
                     f"{tag(m['wanted_by'])}")
    if ranked and any(c["obs"] == "deck" for c in ranked) or windy:
        lines.append("- 전망대 도착 전에 야외 데크 운영 여부를 현장 공지로 확인 [추정 D05]")
    lines.append("- 한강공원 출발 전에 지하철 선로 점검 실시간 지연과 버스·택시 소요시간을 확인 [추정 D06]")
    if ranked and any(c["taxi_uses"] for c in ranked):
        lines.append(f"- 택시 요금 추정치 {v(A['taxi_fare_per_ride']):,}원은 가정값이므로 실제 요금과 예산 "
                     f"{v(P['budget_won']):,}원을 확인 {tag(A['taxi_fare_per_ride'])}")
    lines.append(f"- 체류시간 가정(미술관 {v(A['museum_stay_min'])}분, 식당 {v(A['restaurant_stay_min'])}분, "
                 f"전망대 {v(A['observatory_stay_min'])}분)이 일행 계획과 맞는지 확인 {tag(A['museum_stay_min'])}")
    return "\n".join(lines)


def main(argv=None):
    args = parse_args(argv)
    data = load(args)
    result = plan(data)
    report = build_report(data, result)
    print(report)
    print()
    print("## 7. 요약")
    if args.no_llm:
        print("- LLM 미사용(--no-llm): 위 규칙 결과만 출력")
        return
    from llm_summary import summarize
    print(summarize(data, report))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
