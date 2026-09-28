from datetime import datetime, timedelta

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = "dev-only-change-this-secret"

users = {}

spots = [
    {"name": "雷門", "emoji": "⛩️", "tag": "景點", "category": "attraction", "desc": "充滿歷史感的紅色大燈籠。", "rating": 4.8},
    {"name": "明治神宮", "emoji": "⛩️", "tag": "景點", "category": "attraction", "desc": "東京市中心的森林神社。", "rating": 4.7},
    {"name": "一蘭拉麵", "emoji": "🍜", "tag": "美食", "category": "food", "desc": "豚骨拉麵的經典代表作。", "rating": 4.5},
    {"name": "築地市場", "emoji": "🍣", "tag": "美食", "category": "food", "desc": "新鮮海鮮與在地小吃集中地。", "rating": 4.7},
]


@app.context_processor
def inject_user():
    email = session.get("email")
    user = users.get(email)
    return {"user": user, "name": user["name"] if user else None}


def page(template):
    return render_template(template)


@app.get("/")
def index():
    return page("index.html")


@app.get("/planner")
def planner():
    return page("planner.html")


@app.get("/explore")
def explore():
    return page("explore.html")


@app.get("/recognize")
def recognize():
    return page("recognize.html")


@app.get("/favorites")
def favorites():
    return page("favorites.html")


@app.get("/about")
def about():
    return page("about.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        name = request.form.get("name", "").strip()
        if email in users:
            flash("這個 Email 已經註冊過了。", "danger")
        elif len(password) < 8:
            flash("密碼至少需要 8 碼。", "danger")
        else:
            users[email] = {
                "email": email,
                "password": generate_password_hash(password),
                "name": name,
                "created_at": datetime.now().isoformat(),
            }
            flash("註冊成功，請登入。", "success")
            return redirect(url_for("login"))
    return page("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = users.get(email)
        if user and check_password_hash(user["password"], password):
            session["email"] = email
            return redirect(url_for("index"))
        flash("Email 或密碼不正確。", "danger")
    return page("login.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.get("/api/explore_spots")
def explore_spots():
    category = request.args.get("category", "all")
    filtered = spots if category == "all" else [spot for spot in spots if spot["category"] == category]
    return jsonify({"spots": filtered})


@app.post("/api/generate_itinerary")
def generate_itinerary():
    data = request.get_json(silent=True) or {}
    city = str(data.get("city", "")).strip()
    start_date_text = str(data.get("startDate", "")).strip()
    try:
        days = max(1, min(30, int(data.get("days", 1))))
    except (TypeError, ValueError):
        return jsonify({"error": "旅行天數格式不正確。"}), 400
    if not city:
        return jsonify({"error": "請輸入目的地。"}), 400
    try:
        start_date = datetime.strptime(start_date_text, "%Y-%m-%d") if start_date_text else datetime.now()
    except ValueError:
        return jsonify({"error": "出發日期格式不正確。"}), 400

    itinerary = [
        {
            "day": day,
            "date": (start_date + timedelta(days=day - 1)).strftime("%Y-%m-%d"),
            "title": f"{city} 第 {day} 天",
            "activities": ["探索當地景點", "品嚐在地美食"],
        }
        for day in range(1, days + 1)
    ]
    return jsonify({"itinerary": itinerary})


@app.post("/api/trip_insights")
def trip_insights():
    data = request.get_json(silent=True) or {}
    city = str(data.get("city", "")).strip()
    start_date_text = str(data.get("startDate", "")).strip()
    try:
        days = max(1, min(30, int(data.get("days", 1))))
        budget = max(0, float(data.get("budget") or 0))
    except (TypeError, ValueError):
        return jsonify({"error": "天數或預算格式不正確。"}), 400
    if not city:
        return jsonify({"error": "請先輸入目的地。"}), 400
    try:
        start_date = datetime.strptime(start_date_text, "%Y-%m-%d") if start_date_text else datetime.now()
    except ValueError:
        return jsonify({"error": "出發日期格式不正確。"}), 400

    hotel = round(budget * 0.35) if budget else days * 2200
    transport = round(budget * 0.12) if budget else days * 700
    food = round(budget * 0.25) if budget else days * 1000
    tickets = round(budget * 0.13) if budget else days * 500
    reserve = round(budget * 0.15) if budget else days * 600
    total = hotel + transport + food + tickets + reserve
    return jsonify({
        "weather": [
            {
                "day": day,
                "date": (start_date + timedelta(days=day - 1)).strftime("%Y-%m-%d"),
                "condition": "晴時多雲" if day % 2 else "短暫雨",
                "temperature": "18-24°C",
                "rain": 20 if day % 2 else 60,
            }
            for day in range(1, days + 1)
        ],
        "transport": {
            "summary": "建議使用大眾運輸，景點間平均約 25 分鐘。",
            "estimatedCost": transport,
            "tip": "尖峰時段請預留 15 分鐘轉乘緩衝。",
        },
        "accommodation": {
            "area": f"{city} 車站周邊",
            "nightlyBudget": round(hotel / days),
            "reason": "靠近交通節點，適合首次造訪與每日往返。",
        },
        "budget": {
            "hotel": hotel,
            "transport": transport,
            "food": food,
            "tickets": tickets,
            "reserve": reserve,
            "total": total,
            "withinBudget": not budget or total <= budget,
            "message": "預算配置可行，已保留 15% 預備金。" if not budget or total <= budget else "目前配置可能超出預算，建議降低住宿或購物支出。",
        },
    })


@app.post("/api/replan")
def replan():
    data = request.get_json(silent=True) or {}
    city = str(data.get("city", "")).strip()
    reason = str(data.get("reason", "臨時變更")).strip() or "臨時變更"
    itinerary = data.get("itinerary")
    target_day = data.get("targetDay")
    try:
        target_day = int(target_day) if target_day is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "指定天數格式不正確。"}), 400
    if not city:
        return jsonify({"error": "請先輸入目的地。"}), 400

    fallback_activity = "室內景點與在地美食" if reason == "下雨" else "彈性景點與在地美食"
    if isinstance(itinerary, list) and target_day is not None:
        updated_itinerary = [dict(day_plan) for day_plan in itinerary]
        target_plan = next((day_plan for day_plan in updated_itinerary if day_plan.get("day") == target_day), None)
        if target_plan is None:
            return jsonify({"error": "找不到指定的行程日期。"}), 400
        target_plan["title"] = f"{city} 第 {target_day} 天（已依{reason}調整）"
        target_plan["activities"] = [fallback_activity, "保留交通緩衝時間"]
        return jsonify({
            "itinerary": updated_itinerary,
            "message": f"已只調整第 {target_day} 天，其餘行程保持不變。",
        })

    return jsonify({"error": "請選擇要調整的行程日期。"}), 400


@app.post("/api/recognize_image")
def recognize_image():
    if "image" not in request.files or not request.files["image"].filename:
        return jsonify({"error": "請選擇圖片。"}), 400
    return jsonify({
        "landmarkName": "東京街景",
        "description": "這是本地示範辨識結果。接入影像模型後即可替換成實際分析內容。",
        "similarSpots": [
            {"name": "雷門", "emoji": "⛩️", "reason": "同樣具有東京代表性的街景特色。"},
            {"name": "明治神宮", "emoji": "⛩️", "reason": "適合延伸探索東京文化景點。"},
        ],
    })


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
