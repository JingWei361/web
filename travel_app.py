import os
import json
from datetime import datetime, timedelta
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from supabase import create_client, Client


app = Flask(__name__, template_folder='templates')
app.secret_key = 'ai_traveler_secret_key_2026'

spots = [
    {'name': '雷門', 'emoji': '⛩️', 'tag': '景點', 'category': 'attraction', 'desc': '充滿歷史感的紅色大燈籠。', 'rating': 4.8},
    {'name': '明治神宮', 'emoji': '⛩️', 'tag': '景點', 'category': 'attraction', 'desc': '東京市中心的森林神社。', 'rating': 4.7},
    {'name': '一蘭拉麵', 'emoji': '🍜', 'tag': '美食', 'category': 'food', 'desc': '豚骨拉麵的經典代表作。', 'rating': 4.5},
    {'name': '築地市場', 'emoji': '🍣', 'tag': '美食', 'category': 'food', 'desc': '新鮮海鮮與在地小吃集中地。', 'rating': 4.7},
]

# ==========================================
# Supabase 設定
# ==========================================

SUPABASE_URL = "https://keiyqvrdiogknmmghzgq.supabase.co"
SUPABASE_KEY = "sb_publishable_tTq7TudmepTnN1oqQxEv1g__2pjRfFN"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_authenticated_client() -> tuple[Any, Any]:
    user_id = session.get('user_id')
    access_token = session.get('access_token')
    refresh_token = session.get('refresh_token')
    if not user_id or not access_token or not refresh_token:
        return None, None

    try:
        user_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        user_client.auth.set_session(access_token, refresh_token)
        return user_client, user_id
    except Exception:
        return None, None


def authentication_required() -> tuple[Any, Any, Any]:
    user_client, user_id = get_authenticated_client()
    if not user_client:
        return None, None, (jsonify({'error': '請先登入才能使用此功能。'}), 401)
    return user_client, user_id, None

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
    data = request.get_json(silent=True) or {}
    city = str(data.get('city', '')).strip()
    start_date_text = str(data.get('startDate', '')).strip()
    try:
        days = max(1, min(30, int(data.get('days', 1))))
    except (TypeError, ValueError):
        return jsonify({'error': '旅行天數格式不正確。'}), 400
    if not city:
        return jsonify({'error': '請輸入目的地。'}), 400
    try:
        start_date = datetime.strptime(start_date_text, '%Y-%m-%d') if start_date_text else datetime.now()
    except ValueError:
        return jsonify({'error': '出發日期格式不正確。'}), 400

    itinerary = [
        {
            'day': day,
            'date': (start_date + timedelta(days=day - 1)).strftime('%Y-%m-%d'),
            'title': f'{city} 第 {day} 天',
            'activities': ['探索當地景點', '品嚐在地美食'],
        }
        for day in range(1, days + 1)
    ]
    return jsonify({'itinerary': itinerary})

# ==========================================
# API：獲取探索景點
# ==========================================
@app.route("/api/explore_spots")
def get_explore_spots():
    category = request.args.get('category', 'all')
    filtered = spots if category == 'all' else [spot for spot in spots if spot['category'] == category]
    return jsonify({'spots': filtered})


@app.get('/api/favorites')
def get_favorites():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        favorite_rows = user_client.table('ai_recommendation_favorites').select(
            'id, recommendation_id, created_at'
        ).eq('user_id', user_id).order('created_at', desc=True).execute().data or []
        recommendation_ids = [row['recommendation_id'] for row in favorite_rows]
        if not recommendation_ids:
            return jsonify({'favorites': []})

        recommendations = user_client.table('ai_recommendations').select('*').in_(
            'id', recommendation_ids
        ).eq('user_id', user_id).execute().data or []
        recommendations_by_id = {item['id']: item for item in recommendations}
        favorites = []
        for favorite in favorite_rows:
            recommendation = recommendations_by_id.get(favorite['recommendation_id'])
            if recommendation:
                item = recommendation.get('recommendation_data') or {}
                item = dict(item) if isinstance(item, dict) else {}
                item.setdefault('name', recommendation.get('recommendation_text', '未命名推薦'))
                item['recommendationId'] = recommendation['id']
                item['favoriteId'] = favorite['id']
                favorites.append(item)
        return jsonify({'favorites': favorites})
    except Exception as error:
        return jsonify({'error': f'讀取收藏失敗：{error}'}), 500


@app.post('/api/favorites')
def create_favorite():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    name = str(data.get('name', '')).strip()
    if not name:
        return jsonify({'error': '缺少收藏內容。'}), 400

    try:
        recommendation = user_client.table('ai_recommendations').insert({
            'user_id': user_id,
            'recommendation_text': str(data.get('desc') or name),
            'recommendation_data': data,
            'source': 'explore',
        }).execute().data[0]
        favorite = user_client.table('ai_recommendation_favorites').insert({
            'user_id': user_id,
            'recommendation_id': recommendation['id'],
        }).execute().data[0]
        return jsonify({'favorite': {**data, 'recommendationId': recommendation['id'], 'favoriteId': favorite['id']}}), 201
    except Exception as error:
        return jsonify({'error': f'新增收藏失敗：{error}'}), 500


@app.delete('/api/favorites/<recommendation_id>')
def delete_favorite(recommendation_id):
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        user_client.table('ai_recommendation_favorites').delete().eq(
            'user_id', user_id
        ).eq('recommendation_id', recommendation_id).execute()
        return jsonify({'message': '已取消收藏。'})
    except Exception as error:
        return jsonify({'error': f'取消收藏失敗：{error}'}), 500


@app.post('/api/generations')
def create_generation_record():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    itinerary = data.get('itinerary')
    city = str(data.get('city', '')).strip()
    if not city or not isinstance(itinerary, list) or not itinerary:
        return jsonify({'error': '缺少有效的行程資料。'}), 400

    try:
        record = user_client.table('ai_generation_records').insert({
            'user_id': user_id,
            'record_type': 'itinerary',
            'title': f'{city} 行程',
            'prompt': str(data.get('prompt') or f'規劃 {city} 的旅行行程'),
            'generated_content': json.dumps(itinerary, ensure_ascii=False),
            'context_data': {
                'city': city,
                'days': data.get('days'),
                'startDate': data.get('startDate'),
                'budget': data.get('budget'),
                'style': data.get('style'),
            },
            'model_name': 'demo-itinerary-generator',
            'request_payload': data,
            'response_payload': {'itinerary': itinerary},
        }).execute().data[0]
        return jsonify({'record': record}), 201
    except Exception as error:
        return jsonify({'error': f'儲存行程紀錄失敗：{error}'}), 500


@app.get('/api/generation-favorites')
def get_generation_favorites():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        rows = user_client.table('ai_generation_favorites').select(
            'id, generation_record_id, created_at'
        ).eq('user_id', user_id).order('created_at', desc=True).execute().data or []
        record_ids = [row['generation_record_id'] for row in rows]
        records = []
        if record_ids:
            records = user_client.table('ai_generation_records').select('*').in_(
                'id', record_ids
            ).eq('user_id', user_id).execute().data or []
        records_by_id = {record['id']: record for record in records}
        return jsonify({
            'favorites': [
                {**row, 'record': records_by_id[row['generation_record_id']]}
                for row in rows if row['generation_record_id'] in records_by_id
            ]
        })
    except Exception as error:
        return jsonify({'error': f'讀取行程收藏失敗：{error}'}), 500


@app.post('/api/generation-favorites')
def create_generation_favorite():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    generation_record_id = str(data.get('generationRecordId', '')).strip()
    if not generation_record_id:
        return jsonify({'error': '缺少生成紀錄 ID。'}), 400
    try:
        favorite = user_client.table('ai_generation_favorites').insert({
            'user_id': user_id,
            'generation_record_id': generation_record_id,
        }).execute().data[0]
        return jsonify({'favorite': favorite}), 201
    except Exception as error:
        return jsonify({'error': f'收藏行程失敗：{error}'}), 500


@app.delete('/api/generation-favorites/<generation_record_id>')
def delete_generation_favorite(generation_record_id):
    user_client, user_id, error = authentication_required()
    if error:
        return error
    try:
        user_client.table('ai_generation_favorites').delete().eq(
            'user_id', user_id
        ).eq('generation_record_id', generation_record_id).execute()
        return jsonify({'message': '已取消行程收藏。'})
    except Exception as error:
        return jsonify({'error': f'取消行程收藏失敗：{error}'}), 500


@app.post('/api/search_history')
def create_search_history():
    user_client, user_id, error = authentication_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    search_query = str(data.get('searchQuery', '')).strip()
    if not search_query:
        return jsonify({'error': '搜尋內容不可為空。'}), 400
    try:
        record = user_client.table('search_history').insert({
            'user_id': user_id,
            'search_query': search_query,
            'search_filters': data.get('filters') or {},
        }).execute().data[0]
        return jsonify({'record': record}), 201
    except Exception as error:
        return jsonify({'error': f'儲存搜尋紀錄失敗：{error}'}), 500


@app.post('/api/trip_insights')
def trip_insights():
    data = request.get_json(silent=True) or {}
    city = str(data.get('city', '')).strip()
    start_date_text = str(data.get('startDate', '')).strip()
    try:
        days = max(1, min(30, int(data.get('days', 1))))
        budget = max(0, float(data.get('budget') or 0))
    except (TypeError, ValueError):
        return jsonify({'error': '天數或預算格式不正確。'}), 400
    if not city:
        return jsonify({'error': '請先輸入目的地。'}), 400
    try:
        start_date = datetime.strptime(start_date_text, '%Y-%m-%d') if start_date_text else datetime.now()
    except ValueError:
        return jsonify({'error': '出發日期格式不正確。'}), 400

    hotel = round(budget * 0.35) if budget else days * 2200
    transport = round(budget * 0.12) if budget else days * 700
    food = round(budget * 0.25) if budget else days * 1000
    tickets = round(budget * 0.13) if budget else days * 500
    reserve = round(budget * 0.15) if budget else days * 600
    total = hotel + transport + food + tickets + reserve
    return jsonify({
        'weather': [
            {
                'day': day,
                'date': (start_date + timedelta(days=day - 1)).strftime('%Y-%m-%d'),
                'condition': '晴時多雲' if day % 2 else '短暫雨',
                'temperature': '18-24°C',
                'rain': 20 if day % 2 else 60,
            }
            for day in range(1, days + 1)
        ],
        'transport': {'summary': '建議使用大眾運輸，景點間平均約 25 分鐘。', 'estimatedCost': transport, 'tip': '尖峰時段請預留 15 分鐘轉乘緩衝。'},
        'accommodation': {'area': f'{city} 車站周邊', 'nightlyBudget': round(hotel / days), 'reason': '靠近交通節點，適合首次造訪與每日往返。'},
        'budget': {
            'hotel': hotel, 'transport': transport, 'food': food, 'tickets': tickets, 'reserve': reserve, 'total': total,
            'withinBudget': not budget or total <= budget,
            'message': '預算配置可行，已保留 15% 預備金。' if not budget or total <= budget else '目前配置可能超出預算，建議降低住宿或購物支出。',
        },
    })


@app.post('/api/replan')
def replan():
    data = request.get_json(silent=True) or {}
    city = str(data.get('city', '')).strip()
    reason = str(data.get('reason', '臨時變更')).strip() or '臨時變更'
    itinerary = data.get('itinerary')
    target_day = data.get('targetDay')
    try:
        target_day = int(target_day) if target_day is not None else None
    except (TypeError, ValueError):
        return jsonify({'error': '指定天數格式不正確。'}), 400
    if not city:
        return jsonify({'error': '請先輸入目的地。'}), 400
    if not isinstance(itinerary, list) or target_day is None:
        return jsonify({'error': '請選擇要調整的行程日期。'}), 400

    updated_itinerary = [dict(day_plan) for day_plan in itinerary]
    target_plan = next((day_plan for day_plan in updated_itinerary if day_plan.get('day') == target_day), None)
    if target_plan is None:
        return jsonify({'error': '找不到指定的行程日期。'}), 400
    fallback_activity = '室內景點與在地美食' if reason == '下雨' else '彈性景點與在地美食'
    target_plan['title'] = f'{city} 第 {target_day} 天（已依{reason}調整）'
    target_plan['activities'] = [fallback_activity, '保留交通緩衝時間']
    return jsonify({'itinerary': updated_itinerary, 'message': f'已只調整第 {target_day} 天，其餘行程保持不變。'})


@app.post('/api/recognize_image')
def recognize_image():
    if 'image' not in request.files or not request.files['image'].filename:
        return jsonify({'error': '請選擇圖片。'}), 400
    return jsonify({
        'landmarkName': '東京街景',
        'description': '這是本地示範辨識結果。接入影像模型後即可替換成實際分析內容。',
        'similarSpots': [
            {'name': '雷門', 'emoji': '⛩️', 'reason': '同樣具有東京代表性的街景特色。'},
            {'name': '明治神宮', 'emoji': '⛩️', 'reason': '適合延伸探索東京文化景點。'},
        ],
    })

# ==========================================
# 註冊功能 (Register) - 支援 Profile 資料
# ==========================================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # 1. 接收所有表單資料
        email = request.form.get('email')
        password = request.form.get('password')
        full_name = (request.form.get('name') or '').strip()
        gender = (request.form.get('gender') or '').strip()
        phone = (request.form.get('phone') or '').strip()

        if not email or not password or not full_name or not phone or gender not in {'male', 'female', 'other'}:
            flash('請完整填寫註冊資料，並選擇有效的性別。', 'warning')
            return redirect(url_for('register'))

        try:
            # 2. 呼叫 Supabase 的註冊 API
            # 將資料打包進 options["data"] 中
            res = supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "full_name": full_name,
                        "gender": gender,
                        "phone": phone,
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
            # 將 Supabase 使用者與 session token 存入 Flask session，供 RLS API 使用
            session['user'] = res.user.email
            session['user_id'] = res.user.id
            session['access_token'] = res.session.access_token
            session['refresh_token'] = res.session.refresh_token

            # [新增] 登入成功時，把 user_metadata 裡面的姓名與性別存入 Session
            user_meta = res.user.user_metadata or {}
            session['name'] = user_meta.get('full_name', res.user.email) # 若無姓名則顯示 email 作為備用
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
    session.pop('user_id', None)
    session.pop('access_token', None)
    session.pop('refresh_token', None)
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