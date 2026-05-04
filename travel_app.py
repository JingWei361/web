import os
import time
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import datetime
from werkzeug.utils import secure_filename
from supabase import create_client, Client
import json


app = Flask(__name__, template_folder='templates')
app.secret_key = 'ai_traveler_secret_key_2026'

# ==========================================
# Supabase 設定
# ==========================================

SUPABASE_URL = "https://enwmhjywfwrnkknbfajt.supabase.co"
SUPABASE_KEY = "sb_publishable_GsRvKbwlfUfObiTvVti1iQ_ix8J2lHz"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# 路由：首頁
# ==========================================
@app.route("/")
def home():
    # 從 session 中獲取當前用戶的資訊
    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    # 渲染首頁模板，並傳遞用戶資訊
    return render_template("index.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)

# ==========================================
# 路由：其他頁面
# ==========================================
@app.route("/planner")
def planner():

    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("planner.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)

@app.route("/explore")
def explore():

    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("explore.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)

@app.route("/favorites")
def favorites():

    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("favorites.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender,
                           favorites=[])

@app.route("/about")
def about():
    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("about.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)

@app.route("/recognize")
def recognize():
    current_user = session.get('user')
    current_name = session.get('name')
    current_gender = session.get('gender')
    return render_template("recognize.html",
                           user=current_user,
                           name=current_name,
                           gender=current_gender)

# ==========================================
# API：生成行程
# ==========================================
@app.route("/api/generate_itinerary", methods=['POST'])
def generate_itinerary():
    # 從請求中獲取 JSON 資料
    data = request.get_json()
    city = data.get('city')
    days = int(data.get('days', 1))

    # 檢查是否提供了城市名稱
    if not city:
        return jsonify({'error': '請輸入目的地'}), 400

    # 模擬AI生成行程（實際應用中可集成OpenAI等）
    itinerary = []
    for day in range(days):
        # 為每一天生成景點列表
        day_spots = [
            {'name': f'{city} 熱門景點 {chr(65 + day*2)}', 'emoji': '🏛️', 'time': '09:00 - 12:00'},
            {'name': f'{city} 隱藏美食 {chr(66 + day*2)}', 'emoji': '🍛', 'time': '12:30 - 14:00'},
            {'name': f'{city} 文化體驗 {chr(67 + day*2)}', 'emoji': '🎭', 'time': '15:00 - 17:00'}
        ]
        itinerary.append(day_spots)

    # 返回生成的行程資料
    return jsonify({'itinerary': itinerary})

# ==========================================
# API：獲取探索景點
# ==========================================
@app.route("/api/explore_spots")
def get_explore_spots():
    # 定義一個靜態的景點列表，包含名稱、表情符號、標籤和描述
    spots = [
        {'name': '淺草寺', 'emoji': '🏮', 'tag': '歷史地標', 'desc': '東京最古老的寺廟。'},
        {'name': '澀谷橫丁', 'emoji': '🍜', 'tag': '美食推薦', 'desc': '匯聚全日本美食。'},
        {'name': '晴空塔', 'emoji': '🗼', 'tag': '必去景點', 'desc': '俯瞰東京全景。'},
        {'name': '築地市場', 'emoji': '🍣', 'tag': '美食推薦', 'desc': '品嚐最新鮮的海鮮與玉子燒。'},
        {'name': '景福宮', 'emoji': '🏯', 'tag': '歷史地標', 'desc': '首爾最具代表性的朝鮮時代宮殿。'},
        {'name': '明洞購物街', 'emoji': '🛍️', 'tag': '購物天堂', 'desc': '首爾最熱鬧的商業區。'}
    ]
    # 返回景點列表的 JSON 響應
    return jsonify({'spots': spots})

# ==========================================
# 註冊功能 (Register) - 支援 Profile 資料
# ==========================================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # 1. 接收所有表單資料
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        gender = request.form.get('gender')
        phone = request.form.get('phone')
        birthday = request.form.get('birthday')

        try:
            # 2. 呼叫 Supabase 的註冊 API
            # 將資料打包進 options["data"] 中
            res = supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "name": name,
                        "gender": gender,
                        "phone": phone,
                        "birthday": birthday,
                    }
                }
            })

            # 顯示成功訊息並重定向到登入頁面
            flash('註冊信已發送！請前往您的信箱點擊驗證連結。', 'info')
            return redirect(url_for('login'))

        except Exception as e:
            # 處理註冊失敗的情況
            flash(f'註冊失敗: {e}', 'danger')
            return redirect(url_for('register'))

    # 對於 GET 請求，渲染註冊頁面
    return render_template('register.html')

# ==========================================
# 登入功能 (Login)
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # 獲取表單中的信箱和密碼
        email = request.form['email']
        password = request.form['password']

        try:
            # 使用 Supabase 進行密碼登入
            res = supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })
            # 將用戶信箱存入 session
            session['user'] = res.user.email

            # [新增] 登入成功時，把 user_metadata 裡面的姓名與性別存入 Session
            user_meta = res.user.user_metadata or {}
            session['name'] = user_meta.get('name', res.user.email) # 若無姓名則顯示 email 作為備用
            session['gender'] = user_meta.get('gender')

            # 顯示歡迎訊息並重定向到首頁
            flash(f'歡迎回來, {session["name"]}!', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            # 處理登入失敗的情況
            error_msg = str(e)
            if "Email not confirmed" in error_msg:
                flash('登入失敗：您的帳號尚未開通！請先前往信箱點擊驗證連結。', 'warning')
            else:
                flash('登入失敗：請檢查信箱或密碼是否正確。', 'danger')

            return redirect(url_for('login'))

    # 對於 GET 請求，渲染登入頁面
    return render_template('login.html')

@app.route('/logout')
def logout():
    try:
        # 嘗試從 Supabase 登出
        supabase.auth.sign_out()
    except Exception as e:
        # 記錄登出錯誤，但不影響用戶體驗
        print(f"Supabase 登出錯誤: {e}")

    # 清除 session 中的用戶資訊
    session.pop('user', None)
    session.pop('name', None)    # [新增] 清除姓名
    session.pop('gender', None)  # [新增] 清除性別
    # 顯示登出訊息並重定向到首頁
    flash('您已成功登出', 'info')
    return redirect(url_for('home'))


# ==========================================
# 啟動區塊
# ==========================================
if __name__ == "__main__":
    # Render 會提供 PORT 環境變數，若無則預設 5002
    port = int(os.environ.get("PORT", 5002))
    # 生產環境中 debug 應設為 False，但在 Render 介面可透過環境變數控制
    app.run(host='0.0.0.0', port=port)