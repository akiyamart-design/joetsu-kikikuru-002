import os, json, requests, gspread
from datetime import datetime, timezone, timedelta
from google.oauth2.service_account import Credentials

JST = timezone(timedelta(hours=+9), 'JST')
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
TARGET_CITIES = {"1522200": "上越市", "1521700": "妙高市", "1521600": "糸魚川市"}

HOST = "https://" + "www." + "jma.go.jp"
WARNING_URLS = [HOST + "/bosai/warning/data/r8/150000.json", HOST + "/bosai/warning/data/warning/150000.json"]
FLOOD_URLS = [HOST + "/bosai/flood/data/r8/flood_xml.json", HOST + "/bosai/flood/data/flood_xml.json"]

KIKIKURU_LABEL = {
    5: "危険度5：災害切迫（黒）",
    4: "危険度4：危険（紫）",
    3: "危険度3：警戒（赤）",
    2: "危険度2：注意（黄）",
    1: "危険度1：今後の情報に留意",
}

WARNING_CODES = {
    "33": ("レベル５大雨特別警報", 5, "inundation"),
    "39": ("レベル５土砂災害特別警報", 5, "land"),
    "38": ("レベル５高潮特別警報", 5, "other"),
    "32": ("暴風雪特別警報", 5, "wind_wave"),
    "35": ("暴風特別警報", 5, "wind_wave"),
    "36": ("大雪特別警報", 5, "other"),
    "37": ("波浪特別警報", 5, "wind_wave"),
    "43": ("レベル４大雨危険警報", 4, "inundation"),
    "49": ("レベル４土砂災害危険警報", 4, "land"),
    "48": ("レベル４高潮危険警報", 4, "other"),
    "03": ("レベル３大雨警報", 3, "inundation"),
    "09": ("レベル３土砂災害警報", 3, "land"),
    "08": ("レベル３高潮警報", 3, "other"),
    "02": ("暴風雪警報", 3, "wind_wave"),
    "05": ("暴風警報", 3, "wind_wave"),
    "06": ("大雪警報", 3, "other"),
    "07": ("波浪警報", 3, "wind_wave"),
    "10": ("レベル２大雨注意報", 2, "inundation"),
    "29": ("レベル２土砂災害注意報", 2, "land"),
    "19": ("レベル２高潮注意報", 2, "other"),
    "12": ("大雪注意報", 2, "other"),
    "13": ("風雪注意報", 2, "wind_wave"),
    "14": ("雷注意報", 2, "other"),
    "15": ("強風注意報", 2, "wind_wave"),
    "16": ("波浪注意報", 2, "wind_wave"),
    "17": ("融雪注意報", 2, "other"),
    "20": ("濃霧注意報", 2, "other"),
    "21": ("乾燥注意報", 2, "other"),
    "22": ("なだれ注意報", 2, "other"),
    "23": ("低温注意報", 2, "other"),
    "24": ("霜注意報", 2, "other"),
    "25": ("着氷注意報", 2, "other"),
    "26": ("着雪注意報", 2, "other"),
}

def fmt_dt(dt):
    return dt.strftime(f"%Y-%m-%d %H:%M:%S ({WEEKDAYS[dt.weekday()]})")

def parse_iso(s):
    try:
        return fmt_dt(datetime.fromisoformat(s).astimezone(JST)) if s else ""
    except Exception:
        return s

def pick(seq, idx, default=""):
    for i, v in enumerate(seq):
        if i == idx:
            return v
    return default

def fetch_json(urls):
    for url in urls:
        u = url.replace("https://jma.go.jp", "https://jma.go.jp")
        try:
            r = requests.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, allow_redirects=False)
            if r.status_code == 200 and r.text.strip():
                return r.json()
        except Exception:
            pass
    return None

def get_data():
    w_data = fetch_json(WARNING_URLS)
    if w_data is None:
        raise RuntimeError("気象庁JSON取得失敗")
    f_data = fetch_json(FLOOD_URLS) or []
    w_reps = w_data if isinstance(w_data, list) else [w_data]
    f_reps = f_data if isinstance(f_data, list) else [f_data]

    res = {}
    for code, city in TARGET_CITIES.items():
        warns, max_lv, rep_t = [], 1, ""
        c_lv = {"land": 1, "inundation": 1, "flood": 1, "wind_wave": 1, "other": 1}

        for rep in w_reps:
            rt = rep.get("reportDatetime", "")
            items = rep.get("warning", {}).get("class20Items", [])
            if not items and "areaTypes" in rep:
                for at in rep.get("areaTypes", []):
                    for a in at.get("areas", []):
                        if a.get("code") == code:
                            items += [{"areaCode": code, "kinds": a.get("warnings", [])}]
            for it in items:
                if it.get("areaCode") == code:
                    rep_t = max(rep_t, rt)
                    for k in it.get("kinds", []):
                        st = k.get("status", "")
                        if st in ("発表", "継続"):
                            wn, lv, cat = WARNING_CODES.get(k.get("code", ""), ("不明", 2, "other"))
                            warns += [f"{wn}({st})"]
                            max_lv = max(max_lv, lv)
                            c_lv[cat] = max(c_lv[cat], lv)

        for fr in f_reps:
            for it in fr.get("warning", {}).get("class20Items", []):
                if it.get("areaCode") == code:
                    for k in it.get("kinds", []):
                        if k.get("status") in ("発表", "継続"):
                            fc = k.get("code", "")
                            f_map = {"30": ("レベル５氾濫特別警報", 5), "40": ("レベル４氾濫危険警報", 4), "01": ("レベル３氾濫警報", 3), "20": ("レベル２氾濫注意報", 2), "21": ("レベル２氾濫注意報", 2), "22": ("レベル２氾濫注意報", 2)}
                            if fc in f_map:
                                wn, lv = f_map[fc]
                                warns += [wn]
                                max_lv = max(max_lv, lv)
                                c_lv["flood"] = max(c_lv["flood"], lv)

        c_txt = {k: KIKIKURU_LABEL[v] for k, v in c_lv.items()}
        res[code] = {"max_lv": max_lv, "color": KIKIKURU_LABEL[max_lv], "rep_t": parse_iso(rep_t), "c": c_txt, "text": "、".join(warns) if warns else "発表なし"}
    return res

def rank_of(txt):
    if "危険度5" in txt or txt.startswith("黒"): return 5
    if "危険度4" in txt or txt.startswith("紫"): return 4
    if "危険度3" in txt or txt.startswith("赤"): return 3
    if "危険度2" in txt or txt.startswith("黄"): return 2
    return 1

def cell_style(val):
    if "危険度5" in val or val.startswith("黒"):
        return {"backgroundColor": {"red": 0.12, "green": 0.12, "blue": 0.15}, "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True}}
    if "危険度4" in val or val.startswith("紫"):
        return {"backgroundColor": {"red": 0.65, "green": 0.15, "blue": 0.65}, "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True}}
    if "危険度3" in val or val.startswith("赤"):
        return {"backgroundColor": {"red": 0.92, "green": 0.20, "blue": 0.18}, "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True}}
    if "危険度2" in val or val.startswith("黄"):
        return {"backgroundColor": {"red": 1.0, "green": 0.93, "blue": 0.25}, "textFormat": {"foregroundColor": {"red": 0.1, "green": 0.1, "blue": 0.1}, "bold": True}}
    if val.startswith("▲"):
        return {"backgroundColor": {"red": 1.0, "green": 0.85, "blue": 0.70}, "textFormat": {"foregroundColor": {"red": 0.7, "green": 0.1, "blue": 0.0}, "bold": True}}
    if val.startswith("▼"):
        return {"backgroundColor": {"red": 0.82, "green": 0.93, "blue": 1.0}, "textFormat": {"foregroundColor": {"red": 0.0, "green": 0.3, "blue": 0.6}, "bold": True}}
    return {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "textFormat": {"foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}, "bold": False}}

def main():
    data = get_data()
    g_host = "https://" + "www." + "googleapis.com"
    creds = Credentials.from_service_account_info(json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"]), scopes=[g_host + "/auth/spreadsheets", g_host + "/auth/drive"])
    ss = gspread.authorize(creds).open_by_key(os.environ["SPREADSHEET_ID"])
    now = datetime.now(JST)
    now_str, today_str = fmt_dt(now), now.strftime("%Y-%m-%d")

    for code, city in TARGET_CITIES.items():
        info = data[code]
        try:
            ws = ss.worksheet(city)
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=city, rows=1000, cols=15)
            ws.update(values=[[city] + [""]*10, ["取得日時(曜日)", "発表日時(曜日)", "前回危険度", "今回危険度", "変動", "土砂危険度", "浸水危険度", "洪水危険度", "風・波危険度", "その他危険度", "発表内容詳細"]], range_name="A1:K2")

        rows = ws.get_all_values()
        last = next((r for r in rows[2:] if pick(r, 0) and not pick(r, 0).startswith("━━")), None)
        if last and len(last) >= 4:
            prev_date, prev_col = pick(last, 0)[:10], pick(last, 3)
            diff = "▲ 警戒上昇" if info["max_lv"] > rank_of(prev_col) else ("▼ 警戒低下" if info["max_lv"] < rank_of(prev_col) else "変動なし")
        else:
            prev_date, prev_col, diff = "", "初回取得", "-"

        if prev_date and prev_date != today_str:
            d_obj = datetime.strptime(prev_date, "%Y-%m-%d")
            banner = d_obj.strftime(f"━━ %Y-%m-%d ({WEEKDAYS[d_obj.weekday()]}) ━━")
            ws.insert_row([banner] + [""]*10, index=3, value_input_option="USER_ENTERED")
            ws.format("A3:K3", {"backgroundColor": {"red": 0.90, "green": 0.92, "blue": 0.95}, "textFormat": {"foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}, "bold": True}})

        c = info["c"]
        new_row = [now_str, info["rep_t"], prev_col, info["color"], diff, c["land"], c["inundation"], c["flood"], c["wind_wave"], c["other"], info["text"]]
        ws.insert_row(new_row, index=3, value_input_option="USER_ENTERED")

        cols = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]
        fmt_reqs = [{"range": f"{col}3", "format": cell_style(val)} for col, val in zip(cols, new_row)]
        ws.batch_format(fmt_reqs)
        print(f"[{city}] 完了（キキクル危険度表記・色分け適用済）")

if __name__ == "__main__":
    main()
