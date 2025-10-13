from flask import Flask, request, render_template, url_for, redirect, jsonify, session, g, Response, make_response
from bleach.css_sanitizer import CSSSanitizer
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
from functools import wraps
from flask import jsonify
from PIL import Image
import datetime
import requests
import hashlib
import secrets
import sqlite3
import bleach
import socket
import uuid
import json
import math
import html
import os

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)

DATABASE = 'data.db'
LOG_DATABASE = 'log.db'

ACADEMIC_CLUBS = ["WIN", "TNT", "PLUTONIUM", "LOGIC", "LOTTOL", "RAIBIT", "QUASAR"]
HOBBY_CLUBS = ["책톡", "픽쳐스", "메카", "퓨전", "차랑", "스포츠문화부", "체력단련부", "I-FLOW", "아마빌레"]
CAREER_CLUBS = ["TIP", "필로캠", "천수동", "씽크빅", "WIZARD", "METEOR", "엔진"]

# DB connect (first line of all route)
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

# Log DB connect
def get_log_db():
    db = getattr(g, '_log_database', None)
    if db is None:
        db = g._log_database = sqlite3.connect(LOG_DATABASE)
    return db

# Close DB connecting
@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# Close Log DB connecting
@app.teardown_appcontext
def close_log_connection(exception):
    db = getattr(g, '_log_database', None)
    if db is not None:
        db.close()

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE login_id = ?", (user_id,))
        g.user = cursor.fetchone()

@app.before_request
def check_ban_status():
    """
    모든 요청 전에 사용자의 제재 상태를 확인하고,
    제재 기간이 만료되었다면 자동으로 상태를 'active'로 변경합니다.
    """
    if g.user and g.user['status'] == 'banned' and g.user['banned_until']:
        try:
            banned_until_date = datetime.strptime(g.user['banned_until'], '%Y-%m-%d %H:%M:%S')
            if datetime.now() > banned_until_date:
                # 제재 기간 만료, 상태를 active로 변경
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET status = 'active', banned_until = NULL WHERE login_id = ?", (g.user['login_id'],))
                conn.commit()
                # g.user 객체도 실시간으로 갱신
                g.user = conn.execute("SELECT * FROM users WHERE login_id = ?", (g.user['login_id'],)).fetchone()
        except (ValueError, TypeError):
            # 날짜 형식이 잘못되었거나 NULL인 경우
            pass

# --- 👇 [추가] 제재된 사용자의 활동을 제한하는 데코레이터 ---
def check_banned(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user and g.user['status'] == 'banned':
            # 제재 메시지 생성
            message = "활동이 정지된 계정입니다."
            if g.user['banned_until']:
                try:
                    expiry_date = datetime.strptime(g.user['banned_until'], '%Y-%m-%d %H:%M:%S').strftime('%Y년 %m월 %d일 %H:%M')
                    message += f" (만료일: {expiry_date})"
                except ValueError:
                    pass # 날짜 형식이 잘못된 경우 그냥 기본 메시지만 표시

            # 요청 경로를 확인하여 JSON을 반환할지 결정
            if request.path.startswith('/react/'):
                # '/react/' 경로로 시작하는 AJAX 요청에는 JSON으로 응답
                return jsonify({'status': 'error', 'message': message}), 403 # 403 Forbidden 상태 코드
            else:
                # 그 외의 모든 요청에는 기존 방식대로 스크립트 응답
                return Response(f'<script> alert("{message}"); history.back(); </script>')
                
        return f(*args, **kwargs)
    return decorated_function


class NotificationChannel:
    def __init__(self):
        self.clients = {} # { 'user_id': Queue(), ... }

    def subscribe(self, user_id):
        # 사용자가 접속하면, 해당 사용자를 위한 큐(채널)를 생성
        self.clients[user_id] = Queue()
        return self.clients[user_id]

    def unsubscribe(self, user_id):
        # 사용자가 접속을 끊으면 채널 삭제
        self.clients.pop(user_id, None)

    def publish(self, user_id, message):
        # 특정 사용자에게 메시지(알림)를 보냄
        if user_id in self.clients:
            self.clients[user_id].put_nowait(message)

# 전역 변수로 알림 채널 객체 생성
notification_channel = NotificationChannel()

def create_notification(recipient_id, actor_id, action, target_type, target_id, post_id):
    """알림을 생성하고 DB에 저장하는 함수"""
    # 자기 자신에게는 알림을 보내지 않음
    if recipient_id == actor_id:
        return

    conn = get_db()
    cursor = conn.cursor()
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute("""
        INSERT INTO notifications 
        (recipient_id, actor_id, action, target_type, target_id, post_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (recipient_id, actor_id, action, target_type, target_id, post_id, created_at))
    conn.commit()

    cursor.execute("SELECT u.nickname FROM users u WHERE u.login_id = ?", (actor_id,))
    actor = cursor.fetchone()
    actor_nickname = actor['nickname'] if actor else '알 수 없음'

    message = {
        'action': action,
        'actor_nickname': actor_nickname,
        'post_id': post_id,
        'is_read': 0, # 새 알림이므로 is_read는 0
        'id': cursor.lastrowid # 방금 생성된 알림의 ID
    }

    # 3. 알림 채널을 통해 해당 사용자에게 메시지 발행(publish)
    notification_channel.publish(recipient_id, message)

# Add Log to log.db
def add_log(action, user_id, details):
    """
    활동 로그를 log.db에 기록합니다.
    action: 'CREATE_USER', 'DELETE_USER', 'CREATE_POST', 'DELETE_POST' 등
    user_id: 활동을 수행한 사용자의 login_id
    details: 로그에 기록할 추가 정보 (예: 게시글 ID)
    """
    try:
        conn = get_log_db()
        cursor = conn.cursor()
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ip_address = request.remote_addr

        cursor.execute(
            "INSERT INTO activity_logs (timestamp, action, user_id, ip_address, details) VALUES (?, ?, ?, ?, ?)",
            (timestamp, action, user_id, ip_address, details)
        )
        conn.commit()
    except Exception as e:
        # 로그 기록에 실패하더라도 메인 기능에 영향을 주지 않도록 처리
        print(f"Error writing to log database: {e}")
        print(f"Timestam: {timestamp}, Action: {action}, User ID: {user_id}, ip: {ip_address}, Details: {details}")

# Initialize log.db
def init_log_db():
    with app.app_context():
        conn = get_log_db()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                user_id TEXT,
                ip_address TEXT,
                details TEXT
            )
        ''')
        conn.commit()

# Check Auto Login
@app.before_request
def check_auto_login():
    if 'user_id' not in session and 'remember_token' in request.cookies:
        token = request.cookies.get('remember_token')
        hashed_token = hashlib.sha256(token.encode()).hexdigest()

        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('SELECT login_id FROM users WHERE autologin_token = ?', (hashed_token,))
        user = cursor.fetchone()

        if user:
            session.pop('hakbun', None)
            session.pop('name', None)
            session.pop('gen', None)
            session.pop('agree', None)

            session['user_id'] = user[0]
            session.permanent = True

# Bob (School Meal Information)
def get_bob():
        date = (datetime.datetime.now()).strftime('%Y%m%d')

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT breakfast, lunch, dinner FROM meals WHERE date = ?', (date,))
        meal_data = cursor.fetchone()

        if meal_data:
            return [meal_data[0], meal_data[1], meal_data[2]]
        else:
            url = (
                "https://open.neis.go.kr/hub/mealServiceDietInfo"
                "?KEY=75f40bb14ddd41d1b5ecda3389258cb1"
                "&TYPE=JSON"
                "&ATPT_OFCDC_SC_CODE=E10"
                "&SD_SCHUL_CODE=7310058"
                f"&MLSV_YMD={date}"
                f"&"
            )

            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()

                result = {'1': '급식 정보가 없습니다.', '2': '급식 정보가 없습니다.', '3': '급식 정보가 없습니다.'}  # 1: 아침, 2: 점심, 3: 저녁

                try:
                    meals = data['mealServiceDietInfo'][1]['row']

                    for meal in meals:
                        meal_code = meal.get('MMEAL_SC_CODE')
                        dish_nm = meal.get('DDISH_NM')
                        if meal_code and dish_nm:
                            menu = html.escape(meal['DDISH_NM']).replace('&lt;br/&gt;', '<br>')
                            result[str(meal_code)] = menu

                except (KeyError, IndexError):
                    pass

                content = result
                cursor.execute('INSERT INTO meals (date, breakfast, lunch, dinner) VALUES (?, ?, ?, ?)',(date, content['1'], content['2'], content['3']))
                conn.commit()
            
                cursor.execute('SELECT breakfast, lunch, dinner FROM meals WHERE date = ?', (date,))
                meal_data = cursor.fetchone()

                return [meal_data[0], meal_data[1], meal_data[2]]
            else:
                content = ["API 호출 실패","API 호출 실패","API 호출 실패"]

            return content

# Update User EXP and Level
def update_exp_level(user_id, exp_change):
    conn = get_db()
    cursor = conn.cursor()

    # 현재 사용자 정보 조회
    cursor.execute("SELECT level, exp FROM users WHERE login_id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        return

    current_level, current_exp = user
    new_exp = current_exp + exp_change

    # 레벨업/레벨다운 계산
    # 실제 서비스에서는 레벨별 필요 경험치를 다르게 설정하는 것이 좋습니다.
    exp_per_level = 1000
    level_change = new_exp // exp_per_level
    final_level = current_level + level_change
    final_exp = new_exp % exp_per_level

    # 레벨은 최소 1로 유지
    if final_level < 1:
        final_level = 1
        final_exp = 0

    # DB 업데이트
    cursor.execute(
        "UPDATE users SET level = ?, exp = ? WHERE login_id = ?",
        (final_level, final_exp, user_id)
    )
    conn.commit()

# Jinja2 Filter for Datetime Formatting
def format_datetime(value):
    # DB에서 가져온 날짜/시간 문자열을 datetime 객체로 변환
    post_time = datetime.datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    now = datetime.datetime.now()
    
    # 시간 차이 계산
    delta = now - post_time
    
    seconds = delta.total_seconds()
    
    if seconds < 60:
        return '방금 전'
    elif seconds < 3600:
        return f'{int(seconds // 60)}분 전'
    elif seconds < 86400:
        return f'{int(seconds // 3600)}시간 전'
    elif seconds < 2592000:
        return f'{delta.days}일 전'
    else:
        # 한 달이 넘으면 'YYYY-MM-DD' 형식으로 반환
        return post_time.strftime('%Y-%m-%d')

# 위에서 만든 함수를 템플릿에서 'datetime'이라는 이름의 필터로 사용할 수 있도록 등록
app.jinja_env.filters['datetime'] = format_datetime

# Get Recent Posts from board id
def get_recent_posts(board_id):
    """
    특정 게시판 ID를 받아 해당 게시판의 게시글을 최신순으로 5개 가져옵니다.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # board_id에 해당하는 게시글을 updated_at 기준으로 내림차순(최신순) 정렬하여 상위 5개를 선택합니다.
        # users 테이블과 JOIN하여 작성자 닉네임도 함께 가져옵니다.
        query = """
            SELECT p.id, p.title, u.nickname, p.updated_at
            FROM posts p
            JOIN users u ON p.author = u.login_id
            WHERE p.board_id = ?
            ORDER BY p.updated_at DESC
            LIMIT 5
        """
        cursor.execute(query, (board_id,))
        posts = cursor.fetchall()
        return posts
    except Exception as e:
        add_log('ERROR', 'SYSTEM', f"Error fetching recent posts for board_id {board_id}: {e}")
        return []

def get_hot_posts():
    """최근 7일간 추천 수가 10개 이상인 게시글을 상위 5개까지 가져옵니다."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 7일 전 날짜 계산
    seven_days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    
    query = """
        SELECT p.id, p.title, COUNT(r.id) as like_count
        FROM posts p
        JOIN reactions r ON p.id = r.target_id
        WHERE r.target_type = 'post'
          AND r.reaction_type = 'like'
          AND p.created_at >= ?
        GROUP BY p.id
        HAVING like_count >= 10
        ORDER BY like_count DESC
        LIMIT 5
    """
    cursor.execute(query, (seven_days_ago,))
    return cursor.fetchall()

def get_trending_posts():
    """최근 24시간 동안 조회수가 10 이상인 게시글 중 가장 높은 글을 상위 5개까지 가져옵니다."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 24시간 전 날짜 계산
    one_day_ago = (datetime.datetime.now() - datetime.timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    
    # 수정: WHERE 절에 view_count >= 10 조건 추가
    query = """
        SELECT id, title, view_count
        FROM posts
        WHERE created_at >= ? AND view_count >= 10
        ORDER BY view_count DESC
        LIMIT 5
    """
    cursor.execute(query, (one_day_ago,))
    return cursor.fetchall()

# Main Page
@app.route('/')
def main_page():
    if 'user_id' in session:
        conn = get_db()
        conn.row_factory = sqlite3.Row  # 컬럼 이름으로 접근 가능하도록 설정
        cursor = conn.cursor()

        free_board_posts = get_recent_posts(1)
        info_board_posts = get_recent_posts(2)
        hot_posts = get_hot_posts()
        trending_posts = get_trending_posts()
        
        cursor.execute("SELECT nickname, hakbun, login_id FROM users WHERE login_id = ?", (session['user_id'],))
        user_data = cursor.fetchone()

        bob_data = get_bob()

        if user_data:
            return render_template('main_logined.html', 
                                   user=user_data, 
                                   bob=bob_data, 
                                   free_posts=free_board_posts, 
                                   info_posts=info_board_posts,
                                   hot_posts=hot_posts,
                                   trending_posts=trending_posts)
        else:
            # 혹시 모를 예외 처리 (세션은 있는데 DB에 유저가 없는 경우)
            session.clear()
            return redirect('/')
    else:
        # 비로그인 시
        bob_data = get_bob()
        return render_template('main_notlogined.html', bob=bob_data)

# Googlebot Verification Logic
def is_googlebot():
    """요청이 실제 구글 봇으로부터 왔는지 DNS 조회를 통해 확인합니다."""
    # 로컬 환경 테스트 등을 위해 User-Agent를 먼저 확인 (선택 사항)
    user_agent = request.user_agent.string
    if "Googlebot" not in user_agent:
        return False

    # 1. 요청 IP 확인
    ip = request.remote_addr
    # 로컬호스트에서 테스트하는 경우 예외 처리
    if ip == '127.0.0.1':
        return False # 혹은 테스트 목적에 맞게 True로 설정

    try:
        # 2. IP 주소로 역방향 DNS 조회 (IP -> Hostname)
        hostname, _, _ = socket.gethostbyaddr(ip)

        # 3. Hostname이 구글 소유인지 확인
        if not (hostname.endswith('.googlebot.com') or hostname.endswith('.google.com')):
            return False

        # 4. Hostname으로 순방향 DNS 조회 (Hostname -> IP)
        resolved_ip = socket.gethostbyname(hostname)

        # 5. 원래 IP와 조회된 IP가 일치하는지 확인
        if ip == resolved_ip:
            return True

    except socket.herror:
        # DNS 조회 실패 시
        return False
    except Exception as e:
        add_log('ERROR', 'SYSTEM', f"Error during Googlebot verification: {e}")
        return False

    return False

# For Login Required Page
# @login_required under @app.route
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session and not is_googlebot():
            return Response('<script> alert("로그인 사용자만 접근할 수 있습니다."); history.back(); </script>')
        return f(*args, **kwargs)
    return decorated_function

@app.route('/stream')
@login_required
def stream():
    def event_stream():
        # 현재 로그인한 사용자를 위한 알림 채널을 구독
        user_id = g.user['login_id']
        messages = notification_channel.subscribe(user_id)
        try:
            while True:
                # 큐에 새로운 메시지가 들어올 때까지 대기
                message = messages.get()
                # SSE 형식에 맞춰 "data: {json_string}\n\n" 형태로 전송
                yield f"data: {json.dumps(message)}\n\n"
        except GeneratorExit:
            # 클라이언트 연결이 끊어지면 구독 해제
            notification_channel.unsubscribe(user_id)

    # text/event-stream MIME 타입으로 응답
    return Response(event_stream(), mimetype='text/event-stream')

# Riro Auth
@app.route('/riro-auth', methods=['GET', 'POST'])
def riro_auth():
    if 'user_id' in session:
        return redirect("/")

    conn = get_db()
    if request.method == 'POST': # POST : return Form
        id = request.form['user_id']
        pw = request.form['user_pw']
            
        base_url = "http://localhost:3000"
        endpoint = "/api/riro_login"

        payload = {
            'id': id,
            'password': pw
        }

        try:
            response = requests.post(f"{base_url}{endpoint}", json=payload)

            response.raise_for_status()

            api_result = response.json()

            if api_result['status'] != 'success':
                return Response(f'''
        <script>
            alert("{api_result['message']}")
            history.back();
        </script>
    ''')

            cursor = conn.cursor()

            cursor.execute('SELECT COUNT(*) FROM users WHERE name = ? AND status = "active"', (api_result['name'],))
            count_name = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM users WHERE hakbun = ? AND status = "active"', (api_result['student_number'],))
            count_hakbun = cursor.fetchone()[0]

            if count_name > 0 and count_hakbun > 0:
                return Response(f'''
        <script>
            alert("이미 가입된 계정이 있습니다.");
            history.back();
        </script>
    ''')

            session['hakbun'] = api_result['student_number']
            session['name'] = api_result['name']
            session['gen'] = api_result['generation']

            return redirect('yakgwan')

        except requests.exceptions.HTTPError as http_err:
            add_log('ERROR', 'SYSTEM', f"HTTP error during Riro Auth: {http_err}, Response: {response.text}")
            return Response(f'''
    <script>
        alert("HTTP 오류 발생")
        history.back();
    </script>
''')
        except requests.exceptions.RequestException as req_err:
            add_log('ERROR', 'SYSTEM', f"Request error during Riro Auth: {req_err}")
            return Response(f'''
    <script>
        alert("요청 중 오류가 발생했습니다.")
        history.back();
    </script>
''')

    return render_template('riro_auth_form.html') # GET

# Check duplicate
@app.route('/check-register/', methods=['POST'])
def check_register():
    conn = get_db()
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    id = data.get('id')
    nick = data.get('nick')

    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) FROM users WHERE login_id = ?', (id,))
    count_id = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM users WHERE nickname = ?', (nick,))
    count_nickname = cursor.fetchone()[0]

    if count_id > 0:
        id_tf = 'True'
    else:
        id_tf = 'False'

    if count_nickname > 0:
        nickname_tf = 'True'
    else:
        nickname_tf = 'False'

    return {'login_id': id_tf, 'nickname': nickname_tf}

# YakGwan
@app.route('/yakgwan', methods=['GET', 'POST'])
def yakgwan():
    if 'user_id' in session:
        return redirect("/")
    
    if 'hakbun' not in session or 'name' not in session or 'gen' not in session:
        return redirect("riro-auth")

    if request.method == 'POST': # POST
        agree_terms = request.form.get('agree-terms')
        agree_privacy = request.form.get('agree-privacy')

        if agree_terms == 'on' and agree_privacy == 'on':
            session['agree'] = True
            return redirect('register')
        else:
            return Response('''
        <script>
            alert("약관에 동의하셔야 회원가입이 가능합니다.");
            history.back();
        </script>
    ''')
        
    return render_template('yakgwan.html') # GET

# Register
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect("/")
    
    if 'hakbun' in session and 'name' in session and 'gen' in session:
        hakbun = session['hakbun']
        name = session['name']
        gen = session['gen']
    else:
        return redirect('riro-auth')    
    
    if 'agree' not in session or not session['agree']:
        return redirect('yakgwan')
    
    conn = get_db()

    if request.method == 'POST': # POST : return Form
        pw = request.form['password']
        pw_check = request.form['password_confirm']
        id = request.form['login_id']
        nick = request.form['nickname']
        birth = str(request.form['birth'])
        print(birth)

        if not isinstance(birth, str):
            birth = str(birth)

        # 1. 입력값 길이 확인
        if len(birth) != 8:
            return Response('<script> alert("생년월일은 8자리로 입력해야 합니다."); history.back(); </script>')

        year = int(birth[0:4])
        month = int(birth[4:6])
        day = int(birth[6:8])

        print(year, month, day)

        try:
            datetime.datetime.date(int(year), int(month), int(day))
        except:
            return Response('<script> alert("생년월일 형식을 다시 확인하세요. 1"); history.back(); </script>')

        if len(birth) != 8:
            return Response('<script> alert("생년월일 형식을 다시 확인하세요. 2"); history.back(); </script>')

        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM users WHERE login_id = ?', (id,))
        count_id = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM users WHERE nickname = ?', (nick,))
        count_nickname = cursor.fetchone()[0]

        if count_id > 0:
            return Response('<script> alert("이미 존재하는 아이디입니다."); history.back(); </script>')

        if count_nickname > 0:
            return Response('<script> alert("이미 존재하는 닉네임입니다."); history.back(); </script>')

        if len(pw) < 6:
            return Response('<script> alert("비밀번호는 최소 6자 이상이어야 합니다."); history.back(); </script>')
        
        if pw_check != pw:
            return Response('<script> alert("비밀번호가 일치하지 않습니다."); history.back(); </script>')
        
        hashed_pw = bcrypt.generate_password_hash(pw).decode('utf-8')
        join_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        default_profile = 'images/profiles/defualt_images.jpeg'
        
        # DATA INSERT to DB
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (hakbun, gen, name, pw, login_id, nickname, birth, profile_image, join_date, role, is_autologin, autologin_token, level, exp, post_count, comment_count, point) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'student', 0, '', 1, 0, 0, 0, 0)", (hakbun, gen, name, hashed_pw, id, nick, birth, default_profile, join_date))
        conn.commit()

        session.pop('hakbun', None)
        session.pop('name', None)
        session.pop('gen', None)
        session.pop('agree', None)

        add_log('CREATE_USER', id, f"'{nick}'({id})님이 가입했습니다.({hakbun}, {name})")

        return Response('<script> alert("회원가입이 완료되었습니다."); window.location.href = "/"; </script>') # After Register
    
    return render_template('register_form.html', hakbun=hakbun, name=name, gen=gen) # GET

# login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect("/")

    if request.method == 'POST':
        login_id = request.form['login_id']
        password = request.form['password']
        remember = request.form.get('remember')

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT login_id, pw FROM users WHERE login_id = ?', (login_id,))
        user = cursor.fetchone() # (id, pw_hash) or None

        if user and bcrypt.check_password_hash(user[1], password):
            session.pop('hakbun', None)
            session.pop('name', None)
            session.pop('gen', None)
            session.pop('agree', None)
            
            session['user_id'] = user[0]

            if remember:
                token = secrets.token_hex(32)
                hashed_token = hashlib.sha256(token.encode()).hexdigest()

                cursor.execute('UPDATE users SET autologin_token = ? WHERE login_id = ?', (hashed_token, user[0]))
                conn.commit()

                resp = make_response(redirect("/"))
                resp.set_cookie('remember_token', token, max_age=timedelta(days=90), httponly=True)
                return resp

            return redirect("/")
        else:
            return Response('<script> alert("아이디 또는 비밀번호가 올바르지 않습니다."); history.back(); </script>')

    return render_template('login_form.html') # GET

# logout
@app.route('/logout')
def logout():
    session.clear()

    resp = make_response(redirect("/"))
    resp.set_cookie('remember_token', '', max_age=0)
    
    return resp

# My Page
@app.route('/mypage')
@login_required
def mypage():
    # g.user 객체를 통해 사용자 정보를 가져오므로, 추가적인 DB 조회가 불필요합니다.
    user_data = g.user 

    if not user_data:
        # 세션은 있지만 DB에 유저가 없는 예외적인 경우
        session.clear()
        return redirect('/login')

    conn = get_db()
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()

    # --- 1. 사용자 게시글 목록 조회 (N+1 문제 해결) ---
    # JOIN을 사용하여 한 번의 쿼리로 게시글 정보와 게시판 이름을 함께 가져옵니다.
    posts_query = """
        SELECT 
            p.id, p.title, p.comment_count, p.updated_at, b.board_name
        FROM posts p
        JOIN board b ON p.board_id = b.board_id
        WHERE p.author = ?
        ORDER BY p.updated_at DESC
    """
    cursor.execute(posts_query, (session['user_id'],))
    user_posts = cursor.fetchall()

    # --- 2. 사용자 댓글 목록 조회 (N+1 문제 해결) ---
    # JOIN을 사용하여 한 번의 쿼리로 댓글 정보와 원본 게시글 제목을 함께 가져옵니다.
    comments_query = """
        SELECT 
            c.content, c.post_id, c.updated_at, p.title AS post_title
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE c.author = ?
        ORDER BY c.updated_at DESC
    """
    cursor.execute(comments_query, (session['user_id'],))
    user_comments = cursor.fetchall()
    
    # 날짜 형식 변환
    birth = user_data['birth']
    birth_year = birth[0:4]
    birth_month = birth[4:6]
    birth_day = birth[6:8]
    formatted_birth = f'{birth_year}.{birth_month}.{birth_day}'

    join_date = user_data['join_date']
    datetime_obj = datetime.datetime.strptime(join_date, '%Y-%m-%d %H:%M:%S')
    formatted_join_date = datetime_obj.strftime('%Y.%m.%d')

    return render_template('my_page.html', 
                           user=user_data, # g.user 객체를 템플릿에 전달
                           hakbun=user_data['hakbun'], 
                           name=user_data['name'], 
                           gen=user_data['gen'], 
                           nickname=user_data['nickname'], 
                           birth=formatted_birth, 
                           profile_image=user_data['profile_image'],
                           join_date=formatted_join_date, 
                           level=user_data['level'], 
                           exp=user_data['exp'], 
                           post_count=user_data['post_count'], 
                           comment_count=user_data['comment_count'], 
                           point=user_data['point'], 
                           user_posts=user_posts, 
                           user_comments=user_comments,
                           academic_clubs=ACADEMIC_CLUBS,
                           hobby_clubs=HOBBY_CLUBS,
                           career_clubs=CAREER_CLUBS
                           )

# Post Write
@app.route('/post-write', methods=['GET', 'POST'])
@login_required
@check_banned
def post_write():
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        # 1. 폼 데이터 수신
        title = request.form.get('title')
        content = request.form.get('content')
        board_id = request.form.get('board_id') # board_id 수신
        author_id = session['user_id']

        is_notice = 0
        if g.user and g.user['role'] == 'admin':
            is_notice = 1 if request.form.get('is_notice') == 'on' else 0

        # 2. 서버 사이드 유효성 검사
        if not title or not content or not board_id:
            return Response('<script>alert("게시판, 제목, 내용을 모두 입력해주세요."); history.back();</script>')

        plain_text_content = bleach.clean(content, tags=[], strip=True)
        if len(plain_text_content) > 5000:
            return Response('<script>alert("글자 수는 5,000자를 초과할 수 없습니다."); history.back();</script>')
        if len(plain_text_content) == 0:
            return Response('<script>alert("내용을 입력해주세요."); history.back();</script>')

        # 3. XSS 공격 방어를 위한 HTML 정제 (Sanitization)
        # Summernote의 Base64 이미지 저장을 위해 'img' 태그의 'src' 속성에 data URI 스킴을 허용합니다.
        allowed_tags = [
            'p', 'br', 'b', 'strong', 'i', 'em', 'u', 'h1', 'h2', 'h3',
            'img', 'a', 'video', 'source', 'iframe',
            'table', 'thead', 'tbody', 'tr', 'td', 'th', 'caption',
            'ol', 'ul', 'li', 'blockquote', 'span', 'font'
        ]
        allowed_attrs = {
            '*': ['class', 'style'],
            'a': ['href', 'target'],
            'img': ['src', 'alt', 'width', 'height'], # src 속성을 허용
            'video': ['src', 'width', 'height', 'controls'],
            'source': ['src', 'type'],
            'iframe': ['src', 'width', 'height', 'frameborder', 'allow', 'allowfullscreen'],
            'font': ['color', 'face']
        }
        allowed_css_properties = [
        'color', 'background-color', 'font-family', 'font-size', 
        'font-weight', 'text-align', 'text-decoration'
        ]
        css_sanitizer = CSSSanitizer(allowed_css_properties=allowed_css_properties)
        
        # data URI를 허용하도록 protocols에 'data' 추가
        sanitized_content = bleach.clean(content, tags=allowed_tags, attributes=allowed_attrs, protocols=['http', 'https', 'data'], css_sanitizer=css_sanitizer)

        # 4. 데이터베이스에 저장
        try:
            created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            query = """
                INSERT INTO posts
                (board_id, title, content, author, created_at, updated_at, view_count, comment_count, is_notice)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
            """
            cursor.execute(query, (board_id, title, sanitized_content, author_id, created_at, created_at, is_notice))

            cursor.execute("UPDATE users SET post_count = post_count + 1 WHERE login_id = ?", (author_id,))

            update_exp_level(author_id, 50)

            conn.commit()

            cursor.execute("SELECT last_insert_rowid()")
            post_id = cursor.fetchone()[0]
            add_log('CREATE_POST', author_id, f"'{title}' 글 작성(id : {post_id}). 내용 : {sanitized_content}")

            return redirect(url_for('post_list', board_id=board_id))
        except Exception as e:
            print(f"Database error: {e}")
            add_log('ERROR', author_id, f"Error saving post: {e}")
            return Response('<script>alert("게시글 저장 중 오류가 발생했습니다."); history.back();</script>')

    # GET 요청 시: DB에서 게시판 목록을 가져와 템플릿으로 전달
    cursor.execute("SELECT board_id, board_name FROM board ORDER BY board_id")
    boards = cursor.fetchall() # (board_id, board_name) 튜플의 리스트
    return render_template('post_write.html', boards=boards)

# Post List with Pagination
@app.route('/board/<int:board_id>', defaults={'page': 1})
@app.route('/board/<int:board_id>/<int:page>')
@login_required
def post_list(board_id, page):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    posts_per_page = 20

    user_data = g.user

    if not user_data:
        # 혹시 모를 예외 처리 (세션은 있는데 DB에 유저가 없는 경우)
        session.clear()
        return redirect('/login')

    try:
        cursor.execute("SELECT board_name FROM board WHERE board_id = ?", (board_id,))
        board = cursor.fetchone()

        if not board:
            return Response('<script>alert("존재하지 않는 게시판입니다."); history.back();</script>')

        # 2. 공지사항 목록 조회 (is_notice = 1) - 쿼리 수정
        notice_query = """
            SELECT
                p.id, p.title, u.nickname, p.updated_at, p.view_count,
                SUM(CASE WHEN r.reaction_type = 'like' THEN 1 WHEN r.reaction_type = 'dislike' THEN -1 ELSE 0 END) as net_reactions
            FROM posts p
            JOIN users u ON p.author = u.login_id
            LEFT JOIN reactions r ON r.target_id = p.id AND r.target_type = 'post'
            WHERE p.board_id = ? AND p.is_notice = 1
            GROUP BY p.id
            ORDER BY p.updated_at DESC
        """
        cursor.execute(notice_query, (board_id,))
        notices = cursor.fetchall()

        # 3. 일반 게시글 총 개수 조회
        cursor.execute("SELECT COUNT(*) FROM posts WHERE board_id = ? AND is_notice = 0", (board_id,))
        total_posts = cursor.fetchone()[0]
        total_pages = math.ceil(total_posts / posts_per_page) if total_posts > 0 else 1

        # 4. 현재 페이지에 해당하는 일반 게시글 목록 조회 (is_notice = 0) - 쿼리 수정
        offset = (page - 1) * posts_per_page
        posts_query = """
            SELECT
                p.id, p.title, p.comment_count, p.updated_at, p.view_count, u.nickname,
                SUM(CASE WHEN r.reaction_type = 'like' THEN 1 WHEN r.reaction_type = 'dislike' THEN -1 ELSE 0 END) as net_reactions
            FROM posts p
            JOIN users u ON p.author = u.login_id
            LEFT JOIN reactions r ON r.target_id = p.id AND r.target_type = 'post'
            WHERE p.board_id = ? AND p.is_notice = 0
            GROUP BY p.id
            ORDER BY p.updated_at DESC
            LIMIT ? OFFSET ?
        """
        cursor.execute(posts_query, (board_id, posts_per_page, offset))
        posts = cursor.fetchall()

    except Exception as e:
        print(f"Error fetching post list: {e}")
        add_log('ERROR', user_data['login_id'], f"Error fetching post list for board_id {board_id}, page {page}: {e}")
        return Response('<script>alert("게시글을 불러오는 중 오류가 발생했습니다."); history.back();</script>')

    return render_template('post_list.html', user=user_data,
                           board=board,
                           notices=notices,
                           posts=posts,
                           total_pages=total_pages,
                           current_page=page,
                           board_id=board_id)

# Post Detail
@app.route('/post/<int:post_id>')
@login_required
def post_detail(post_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    user_data = g.user
    if not user_data:
        session.clear()
        return redirect('/login')

    try:
        # --- 게시글 정보 조회 (기존과 동일) ---
        query = """
            SELECT p.*, u.nickname, u.profile_image, b.board_name
            FROM posts p
            JOIN users u ON p.author = u.login_id
            JOIN board b ON p.board_id = b.board_id
            WHERE p.id = ?
        """
        cursor.execute(query, (post_id,))
        post_data = cursor.fetchone()

        if not post_data:
            return Response('<script>alert("존재하지 않거나 삭제된 게시글입니다."); history.back();</script>')
    
        post = dict(post_data)
        post['created_at_datetime'] = datetime.datetime.strptime(post['created_at'], '%Y-%m-%d %H:%M:%S')
        post['updated_at_datetime'] = datetime.datetime.strptime(post['updated_at'], '%Y-%m-%d %H:%M:%S')

        cursor.execute("SELECT reaction_type, COUNT(*) as count FROM reactions WHERE target_type = 'post' AND target_id = ? GROUP BY reaction_type", (post_id,))
        reactions = {r['reaction_type']: r['count'] for r in cursor.fetchall()}
        post['likes'] = reactions.get('like', 0)
        post['dislikes'] = reactions.get('dislike', 0)

        post['user_reaction'] = None
        if g.user:
            cursor.execute("SELECT reaction_type FROM reactions WHERE user_id = ? AND target_type = 'post' AND target_id = ?", (g.user['login_id'], post_id,))
            user_reaction_row = cursor.fetchone()
            if user_reaction_row:
                post['user_reaction'] = user_reaction_row['reaction_type']

        cursor.execute("UPDATE posts SET view_count = view_count + 1 WHERE id = ?", (post_id,))
        conn.commit()

        # --- 👇 댓글 로직 수정 시작 ---
        comment_query = """
            SELECT c.*, u.nickname, u.profile_image
            FROM comments c
            JOIN users u ON c.author = u.login_id
            WHERE c.post_id = ?
            ORDER BY c.created_at DESC
        """
        cursor.execute(comment_query, (post_id,))
        all_comments = cursor.fetchall()
        
        comments_dict = {}
        # 1. 모든 댓글을 딕셔너리로 변환하고, 'replies' 리스트와 reaction 정보를 초기화합니다.
        for comment_row in all_comments:
            comment = dict(comment_row)
            comment['replies'] = []

            cursor.execute("SELECT reaction_type, COUNT(*) as count FROM reactions WHERE target_type = 'comment' AND target_id = ? GROUP BY reaction_type", (comment['id'],))
            comment_reactions = {r['reaction_type']: r['count'] for r in cursor.fetchall()}
            comment['likes'] = comment_reactions.get('like', 0)
            comment['dislikes'] = comment_reactions.get('dislike', 0)
            
            comment['user_reaction'] = None
            if g.user:
                cursor.execute("SELECT reaction_type FROM reactions WHERE user_id = ? AND target_type = 'comment' AND target_id = ?", (g.user['login_id'], comment['id']))
                user_reaction_row = cursor.fetchone()
                if user_reaction_row:
                    comment['user_reaction'] = user_reaction_row['reaction_type']
            
            comments_dict[comment['id']] = comment

        # 2. 댓글들을 부모-자식 관계로 연결하여 트리 구조를 만듭니다.
        comments_tree = []
        for comment_id, comment in comments_dict.items():
            parent_id = comment.get('parent_comment_id')
            if parent_id:
                if parent_id in comments_dict:
                    comments_dict[parent_id]['replies'].append(comment)
            else:
                comments_tree.append(comment)
        # --- 👆 댓글 로직 수정 끝 ---

    except Exception as e:
        print(f"Error fetching post detail: {e}")
        return Response('<script>alert("게시글을 불러오는 중 오류가 발생했습니다."); history.back();</script>')

    return render_template('post_detail.html', user=user_data, post=post, comments=comments_tree)

# Post Edit
@app.route('/post-edit/<int:post_id>', methods=['GET', 'POST'])
@login_required
@check_banned
def post_edit(post_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
    post = cursor.fetchone()

    if not post:
        return Response('<script>alert("존재하지 않거나 삭제된 게시글입니다."); history.back();</script>')

    if post['author'] != session['user_id']:
        return Response('<script>alert("수정 권한이 없습니다."); history.back();</script>')

    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        board_id = request.form.get('board_id')

        if not title or not content or not board_id:
            return Response('<script>alert("게시판, 제목, 내용을 모두 입력해주세요."); history.back();</script>')
        
        is_notice = 0
        if g.user and g.user['role'] == 'admin':
            is_notice = 1 if request.form.get('is_notice') == 'on' else 0
        
        plain_text_content = bleach.clean(content, tags=[], strip=True)
        if len(plain_text_content) > 5000:
            return Response('<script>alert("글자 수는 5,000자를 초과할 수 없습니다."); history.back();</script>')
        if len(plain_text_content) == 0:
            return Response('<script>alert("내용을 입력해주세요."); history.back();</script>')

        allowed_tags = [
            'p', 'br', 'b', 'strong', 'i', 'em', 'u', 'h1', 'h2', 'h3',
            'img', 'a', 'video', 'source', 'iframe',
            'table', 'thead', 'tbody', 'tr', 'td', 'th', 'caption',
            'ol', 'ul', 'li', 'blockquote', 'span', 'font'
        ]
        allowed_attrs = {
            '*': ['class', 'style'],
            'a': ['href', 'target'],
            'img': ['src', 'alt', 'width', 'height'],
            'video': ['src', 'width', 'height', 'controls'],
            'source': ['src', 'type'],
            'iframe': ['src', 'width', 'height', 'frameborder', 'allow', 'allowfullscreen'],
            'font': ['color', 'face']
        }
        sanitized_content = bleach.clean(content, tags=allowed_tags, attributes=allowed_attrs, protocols=['http', 'https', 'data'])

        updated_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        query = "UPDATE posts SET board_id = ?, title = ?, content = ?, updated_at = ?, is_notice = ? WHERE id = ?"
        cursor.execute(query, (board_id, title, sanitized_content, updated_at, is_notice, post_id))

        add_log('EDIT_POST', session['user_id'], f"게시글 (id : {post_id})를 수정했습니다. 제목 : {title} 내용 : {sanitized_content}")

        conn.commit()

        return redirect(url_for('post_detail', post_id=post_id))
    else: # GET 요청
        cursor.execute("SELECT board_id, board_name FROM board ORDER BY board_id")
        boards = cursor.fetchall()
        return render_template('post_edit.html', post=post, boards=boards)

# Post Delete
@app.route('/post-delete/<int:post_id>', methods=['POST'])
@login_required
@check_banned
def post_delete(post_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT author, board_id FROM posts WHERE id = ?", (post_id,))
    post = cursor.fetchone()

    if not post:
        return Response('<script>alert("존재하지 않거나 삭제된 게시글입니다."); history.back();</script>')

    board_id = post[1]

    # 관리자는 다른 사람의 글도 삭제할 수 있도록 수정 (선택 사항)
    if post[0] != session['user_id'] and (not g.user or g.user['role'] != 'admin'):
        return Response('<script>alert("삭제 권한이 없습니다."); history.back();</script>')

    try:
        # --- 👇 로직 수정 시작 ---

        # 1. 삭제될 댓글들의 ID와 작성자 정보를 미리 조회합니다.
        cursor.execute("SELECT id, author FROM comments WHERE post_id = ?", (post_id,))
        comments = cursor.fetchall()
        
        if comments:
            comment_ids = [c['id'] for c in comments]
            
            # 2. 댓글들의 reaction을 먼저 삭제합니다.
            placeholders = ', '.join('?' for _ in comment_ids)
            cursor.execute(f"DELETE FROM reactions WHERE target_type = 'comment' AND target_id IN ({placeholders})", comment_ids)

            # 3. 각 댓글 작성자별로 댓글 수를 차감합니다.
            comment_authors_counts = {}
            for c in comments:
                author = c['author']
                comment_authors_counts[author] = comment_authors_counts.get(author, 0) + 1
            
            for author, count in comment_authors_counts.items():
                cursor.execute("UPDATE users SET comment_count = comment_count - ? WHERE login_id = ?", (count, author))

        # 4. 게시글 자체의 reaction을 삭제합니다.
        cursor.execute("DELETE FROM reactions WHERE target_type = 'post' AND target_id = ?", (post_id,))
        
        # 5. 해당 게시글의 댓글들을 모두 삭제합니다.
        cursor.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))

        # 6. 게시글을 삭제합니다.
        cursor.execute("DELETE FROM posts WHERE id = ?", (post_id,))

        # 7. 게시글 작성자의 post_count를 1 감소시킵니다.
        cursor.execute("UPDATE users SET post_count = post_count - 1 WHERE login_id = ?", (post['author'],))
        
        # 8. 경험치를 차감합니다.
        update_exp_level(post['author'], -50)

        # --- 👆 로직 수정 끝 ---

        add_log('DELETE_POST', session['user_id'], f"게시글 (id : {post_id})를 삭제했습니다. 제목 : {post['title']}")
        
        conn.commit()

    except Exception as e:
        print(f"Error during post deletion: {e}")
        add_log('ERROR', session['user_id'], f"Error deleting post id {post_id}: {e}")
        conn.rollback()
        return Response('<script>alert("게시글 삭제 중 오류가 발생했습니다."); history.back();</script>')

    return redirect(url_for('post_list', board_id=board_id))

# Comment Add
@app.route('/comment/add/<int:post_id>', methods=['POST'])
@login_required
@check_banned
def add_comment(post_id):
    content = request.form.get('comment_content')
    parent_comment_id = request.form.get('parent_comment_id', None)

    if not content or not content.strip():
        return Response('<script>alert("댓글 내용을 입력해주세요."); history.back();</script>')

    conn = get_db()
    cursor = conn.cursor()

    try:
        author_id = session['user_id']
        created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sanitized_content = bleach.clean(content)
        
        if parent_comment_id:
            # --- 👇 추가된 검증 로직 시작 ---
            # 부모 댓글이 최상위 댓글인지(parent_comment_id가 NULL인지) 확인
            cursor.execute("SELECT parent_comment_id FROM comments WHERE id = ?", (parent_comment_id,))
            parent_comment = cursor.fetchone()

            create_notification(
                recipient_id=parent_comment['author'],
                actor_id=author_id,
                action='reply',
                target_type='comment',
                target_id=parent_comment['id'],
                post_id=post_id
            )
            
            if not parent_comment:
                return Response('<script>alert("답글을 작성할 원본 댓글이 존재하지 않습니다."); history.back();</script>')
            
            if parent_comment[0] is not None:
                # 부모 댓글의 parent_comment_id가 NULL이 아니라면, 그것은 이미 대댓글임.
                return Response('<script>alert("대댓글에는 답글을 작성할 수 없습니다."); history.back();</script>')
            # --- 👆 추가된 검증 로직 끝 ---

            query = """
                INSERT INTO comments 
                (post_id, author, content, created_at, updated_at, parent_comment_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """
            cursor.execute(query, (post_id, author_id, sanitized_content, created_at, created_at, parent_comment_id))
        else:
            cursor.execute("SELECT author FROM posts WHERE id = ?", (post_id,))
            post = cursor.fetchone()
            if post:
                create_notification(
                    recipient_id=post['author'],
                    actor_id=author_id,
                    action='comment',
                    target_type='post',
                    target_id=post_id,
                    post_id=post_id
                )

            query = """
                INSERT INTO comments 
                (post_id, author, content, created_at, updated_at, parent_comment_id)
                VALUES (?, ?, ?, ?, ?, NULL)
            """
            cursor.execute(query, (post_id, author_id, sanitized_content, created_at, created_at))

        cursor.execute("UPDATE posts SET comment_count = comment_count + 1 WHERE id = ?", (post_id,))
        cursor.execute("UPDATE users SET comment_count = comment_count + 1 WHERE login_id = ?", (author_id,))

        update_exp_level(author_id, 10)

        log_details = f"게시글(id:{post_id})에 댓글 작성. 내용:{sanitized_content}"
        if parent_comment_id:
            log_details = f"댓글(id:{parent_comment_id})에 답글 작성. 내용:{sanitized_content}"
        add_log('ADD_COMMENT', author_id, log_details)

        conn.commit()

    except Exception as e:
        print(f"Database error while adding comment: {e}")
        conn.rollback()
        return Response('<script>alert("댓글 작성 중 오류가 발생했습니다."); history.back();</script>')

    return redirect(url_for('post_detail', post_id=post_id))

# Comment Delete
@app.route('/comment/delete/<int:comment_id>', methods=['POST'])
@login_required
@check_banned
def delete_comment(comment_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. 삭제할 댓글 정보 조회 (권한 확인, post_id 및 내용 확보용)
    cursor.execute("SELECT author, post_id, content FROM comments WHERE id = ?", (comment_id,))
    comment = cursor.fetchone()

    if not comment:
        return Response('<script>alert("존재하지 않는 댓글입니다."); history.back();</script>')

    # 2. 권한 확인 (본인 또는 관리자만 삭제 가능)
    if comment['author'] != session['user_id'] and (not g.user or g.user['role'] != 'admin'):
        return Response('<script>alert("삭제 권한이 없습니다."); history.back();</script>')

    try:
        # --- 👇 로직 수정 시작 ---

        # 3. 해당 댓글의 reaction을 먼저 삭제합니다.
        cursor.execute("DELETE FROM reactions WHERE target_type = 'comment' AND target_id = ?", (comment_id,))

        # 4. 데이터베이스에서 댓글을 삭제합니다.
        cursor.execute("DELETE FROM comments WHERE id = ?", (comment_id,))

        # 5. 게시글의 댓글 수를 1 감소시킵니다.
        cursor.execute("UPDATE posts SET comment_count = comment_count - 1 WHERE id = ?", (comment['post_id'],))
        
        # 6. 사용자의 댓글 수를 1 감소시킵니다.
        cursor.execute("UPDATE users SET comment_count = comment_count - 1 WHERE login_id = ?", (comment['author'],))

        # 7. 경험치를 차감합니다.
        update_exp_level(comment['author'], -10)
        
        # --- 👆 로직 수정 끝 ---

        add_log('DELETE_COMMENT', session['user_id'], f"댓글 (id : {comment_id})를 삭제했습니다. 내용 : {comment['content']}")

        conn.commit()
    except Exception as e:
        print(f"Database error while deleting comment: {e}")
        add_log('ERROR', session['user_id'], f"Error deleting comment id {comment_id}: {e}")
        conn.rollback()
        return Response('<script>alert("댓글 삭제 중 오류가 발생했습니다."); history.back();</script>')

    return redirect(url_for('post_detail', post_id=comment['post_id']))

# Comment Edit
@app.route('/comment/edit/<int:comment_id>', methods=['POST'])
@login_required
@check_banned
def edit_comment(comment_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. 수정할 댓글 정보 조회 (권한 확인용)
    cursor.execute("SELECT author, post_id FROM comments WHERE id = ?", (comment_id,))
    comment = cursor.fetchone()

    if not comment:
        return Response('<script>alert("존재하지 않는 댓글입니다."); history.back();</script>')

    # 2. 권한 확인 (본인만 수정 가능)
    if comment['author'] != session['user_id']:
        return Response('<script>alert("수정 권한이 없습니다."); history.back();</script>')

    # 3. 폼에서 수정된 내용 가져오기 및 유효성 검사
    new_content = request.form.get('edit_content')
    if not new_content or not new_content.strip():
        return Response('<script>alert("댓글 내용을 입력해주세요."); history.back();</script>')
    
    try:
        # 4. 데이터베이스 업데이트
        updated_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sanitized_content = bleach.clean(new_content)
        
        query = "UPDATE comments SET content = ?, updated_at = ? WHERE id = ?"
        cursor.execute(query, (sanitized_content, updated_at, comment_id))
        add_log('EDIT_COMMENT', session['user_id'], f"댓글 (id : {comment_id})를 수정했습니다. 원본 : {comment['content']}, 내용 : {sanitized_content}")
        conn.commit()

    except Exception as e:
        print(f"Database error while editing comment: {e}")
        add_log('ERROR', session['user_id'], f"Error editing comment id {comment_id}: {e}")
        conn.rollback()
        return Response('<script>alert("댓글 수정 중 오류가 발생했습니다."); history.back();</script>')

    return redirect(url_for('post_detail', post_id=comment['post_id']))

# React (Like/Dislike) for Post and Comment
@app.route('/react/<target_type>/<int:target_id>', methods=['POST'])
@login_required
@check_banned
def react(target_type, target_id):
    reaction_type = request.form.get('reaction_type')
    user_id = session['user_id']
    
    if target_type not in ['post', 'comment'] or reaction_type not in ['like', 'dislike']:
        return jsonify({'status': 'error', 'message': '잘못된 접근입니다.'}), 400

    conn = get_db()
    conn.row_factory = sqlite3.Row # .Row 추가
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT reaction_type FROM reactions WHERE user_id = ? AND target_type = ? AND target_id = ?",
                       (user_id, target_type, target_id))
        existing_reaction = cursor.fetchone()

        if existing_reaction:
            if existing_reaction['reaction_type'] == reaction_type:
                cursor.execute("DELETE FROM reactions WHERE user_id = ? AND target_type = ? AND target_id = ?",
                               (user_id, target_type, target_id))
                add_log('CANCEL_REACTION', user_id, f"{target_type} (id: {target_id})에 대한 '{reaction_type}' 반응을 취소했습니다.")
            else:
                cursor.execute("UPDATE reactions SET reaction_type = ? WHERE user_id = ? AND target_type = ? AND target_id = ?",
                               (reaction_type, user_id, target_type, target_id))
                add_log('CHANGE_REACTION', user_id, f"{target_type} (id: {target_id})에 대한 반응을 '{existing_reaction['reaction_type']}'에서 '{reaction_type}'(으)로 변경했습니다.")
        else:
            cursor.execute("INSERT INTO reactions (user_id, target_type, target_id, reaction_type, created_at) VALUES (?, ?, ?, ?, ?)",
                           (user_id, target_type, target_id, reaction_type, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            add_log('ADD_REACTION', user_id, f"{target_type} (id: {target_id})에 '{reaction_type}' 반응을 추가했습니다.")

        conn.commit()

        # --- 👇 HOT 게시물 알림 로직 시작 ---
        # 1. '게시글'에 '좋아요'를 눌렀을 경우에만 확인
        if target_type == 'post' and reaction_type == 'like':
            # 2. 현재 '좋아요' 개수를 다시 계산
            cursor.execute("SELECT COUNT(*) FROM reactions WHERE target_type = 'post' AND target_id = ? AND reaction_type = 'like'", (target_id,))
            likes = cursor.fetchone()[0]

            # 3. '좋아요'가 정확히 10개가 되었는지 확인
            if likes == 10:
                # 4. 이 게시글에 대해 'hot_post' 알림이 이미 보내졌는지 확인 (중복 방지)
                cursor.execute("SELECT COUNT(*) FROM notifications WHERE action = 'hot_post' AND target_type = 'post' AND target_id = ?", (target_id,))
                already_notified = cursor.fetchone()[0]

                if already_notified == 0:
                    # 5. 게시글 작성자 정보를 가져와서 알림 생성
                    cursor.execute("SELECT author FROM posts WHERE id = ?", (target_id,))
                    post = cursor.fetchone()
                    if post:
                        create_notification(
                            recipient_id=post['author'],
                            actor_id=user_id, # 10번째 좋아요를 누른 사람
                            action='hot_post',
                            target_type='post',
                            target_id=target_id,
                            post_id=target_id
                        )
        # --- 👆 HOT 게시물 알림 로직 끝 ---


        cursor.execute("SELECT reaction_type, COUNT(*) as count FROM reactions WHERE target_type = ? AND target_id = ? GROUP BY reaction_type",
                       (target_type, target_id))
        reactions = {r['reaction_type']: r['count'] for r in cursor.fetchall()}
        likes = reactions.get('like', 0)
        dislikes = reactions.get('dislike', 0)

        cursor.execute("SELECT reaction_type FROM reactions WHERE user_id = ? AND target_type = ? AND target_id = ?",
                       (user_id, target_type, target_id))
        final_reaction_row = cursor.fetchone()
        user_reaction = final_reaction_row['reaction_type'] if final_reaction_row else None

        return jsonify({
            'status': 'success',
            'likes': likes,
            'dislikes': dislikes,
            'user_reaction': user_reaction
        })

    except Exception as e:
        print(f"Database error while reacting: {e}")
        conn.rollback()
        return jsonify({'status': 'error', 'message': '요청 처리 중 오류가 발생했습니다.'}), 500

@app.route('/yakgwan-view')
def yakgwan_view():
    return render_template('yakgwan-view.html')

# Profile Image Update
UPLOAD_FOLDER = 'static/images/profiles'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/update-profile-image', methods=['POST'])
@login_required
def update_profile_image():
    if 'profile_image' not in request.files:
        return Response('<script>alert("파일이 전송되지 않았습니다."); history.back();</script>')

    file = request.files['profile_image']

    if file.filename == '':
        return Response('<script>alert("파일을 선택해주세요."); history.back();</script>')

    if file and allowed_file(file.filename):
        conn = get_db()
        cursor = conn.cursor()

        # 1. 현재 사용자의 이전 이미지 경로를 DB에서 가져옵니다.
        cursor.execute("SELECT profile_image FROM users WHERE login_id = ?", (session['user_id'],))
        old_image_path_tuple = cursor.fetchone()
        if old_image_path_tuple:
            old_image_path = old_image_path_tuple[0]
            # 2. 기본 이미지가 아닐 경우에만 파일을 삭제합니다.
            if old_image_path and 'default' not in old_image_path:
                try:
                    # 'static'을 경로에 포함시켜야 합니다.
                    full_old_path = os.path.join('static', old_image_path)
                    if os.path.exists(full_old_path):
                        os.remove(full_old_path)
                except Exception as e:
                    print(f"Warning: 이전 프로필 이미지 삭제 실패: {e}")
                    add_log('WARNING', session['user_id'], f"이전 프로필 이미지 삭제 실패: {e}")

        filename = secure_filename(file.filename)
        unique_filename = str(uuid.uuid4()) + "_" + filename
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

        # --- 👇 이미지 최적화 로직 시작 ---
        img = Image.open(file.stream)

        # 이미지의 가로, 세로 중 더 긴 쪽을 300px에 맞추고 비율 유지
        img.thumbnail((300, 300))

        img.save(save_path, optimize=True)
        # --- 👆 이미지 최적화 로직 끝 ---

        db_path = 'images/profiles/' + unique_filename

        cursor.execute("UPDATE users SET profile_image = ? WHERE login_id = ?", (db_path, session['user_id']))
        add_log('UPDATE_PROFILE_IMAGE', session['user_id'], f"프로필 이미지를 '{unique_filename}'(으)로 변경했습니다.")
        conn.commit()

        return redirect(url_for('mypage'))
    else:
        return Response('<script>alert("허용되지 않는 파일 형식입니다. (png, jpg, jpeg)"); history.back();</script>')

# User Profile Page (URL은 nickname 기반 유지)
@app.route('/profile/<string:nickname>')
@login_required
def user_profile(nickname):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 닉네임을 기반으로 사용자 정보 조회
    cursor.execute("SELECT * FROM users WHERE nickname = ?", (nickname,))
    profile_user_data = cursor.fetchone()

    if not profile_user_data:
        return render_template('404.html', user=g.user), 404

    # 프로필 주인이 본인인지 확인
    is_own_profile = (g.user['nickname'] == nickname)
    
    # --- 로직 변경 ---
    # 프로필 공개 여부와 관계없이 항상 게시글과 댓글을 조회합니다.
    # 템플릿 단에서 출력 여부를 결정합니다.
    login_id = profile_user_data['login_id']

    # 사용자의 게시글 목록 조회
    posts_query = """
        SELECT p.id, p.title, p.comment_count, p.updated_at, b.board_name
        FROM posts p JOIN board b ON p.board_id = b.board_id
        WHERE p.author = ? ORDER BY p.updated_at DESC
    """
    cursor.execute(posts_query, (login_id,))
    user_posts = cursor.fetchall()

    # 사용자의 댓글 목록 조회
    comments_query = """
        SELECT c.content, c.post_id, c.updated_at, p.title AS post_title
        FROM comments c JOIN posts p ON c.post_id = p.id
        WHERE c.author = ? ORDER BY c.updated_at DESC
    """
    cursor.execute(comments_query, (login_id,))
    user_comments = cursor.fetchall()

    return render_template('profile.html', 
                           user=g.user, 
                           profile_user=profile_user_data, 
                           user_posts=user_posts, 
                           user_comments=user_comments,
                           is_own_profile=is_own_profile)

@app.route('/update-profile-info', methods=['POST'])
@login_required
def update_profile_info():
    profile_message = request.form.get('profile_message')
    club1 = request.form.get('club1')
    club2 = request.form.get('club2')
    club3 = request.form.get('club3')
    profile_public = request.form.get('profile_public')

    # profile_public 값 보정 (체크박스가 체크되지 않으면 값이 전송되지 않음)
    is_public = 1 if profile_public == 'on' else 0

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET
        profile_message = ?, clubhak = ?, clubchi = ?, clubjin = ?, profile_public = ?
        WHERE login_id = ?
    """, (profile_message, club1, club2, club3, is_public, session['user_id']))

    conn.commit()
    
    add_log('UPDATE_PROFILE_INFO', session['user_id'], "프로필 정보를 업데이트했습니다.")

    return redirect(url_for('mypage'))

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    user = g.user 

    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        # 1. 현재 비밀번호 확인
        if not user or 'pw' not in user or not bcrypt.check_password_hash(user['pw'], current_password):
            return Response('<script>alert("현재 비밀번호가 일치하지 않습니다."); history.back();</script>')

        # --- 👇 추가된 로직 시작 ---
        # 2. 현재 비밀번호와 새 비밀번호가 동일한지 확인
        if bcrypt.check_password_hash(user['pw'], new_password):
            return Response('<script>alert("새 비밀번호는 현재 비밀번호와 다르게 설정해야 합니다."); history.back();</script>')
        # --- 👆 추가된 로직 끝 ---

        # 3. 새 비밀번호 유효성 검사
        if len(new_password) < 6:
            return Response('<script>alert("새 비밀번호는 6자 이상이어야 합니다."); history.back();</script>')
        
        if new_password != confirm_password:
            return Response('<script>alert("새 비밀번호와 확인 비밀번호가 일치하지 않습니다."); history.back();</script>')

        # 4. 비밀번호 업데이트
        hashed_pw = bcrypt.generate_password_hash(new_password).decode('utf-8')
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET pw = ? WHERE login_id = ?", (hashed_pw, session['user_id']))
        conn.commit()

        add_log('CHANGE_PASSWORD', session['user_id'], "비밀번호를 변경했습니다.")
        
        return Response('<script>alert("비밀번호가 성공적으로 변경되었습니다."); window.location.href = "/mypage";</script>')

    return render_template('change_password.html', user=user)

@app.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    password = request.form.get('password')
    user = g.user

    if not bcrypt.check_password_hash(user['pw'], password):
        return Response('<script>alert("비밀번호가 일치하지 않아 계정을 삭제할 수 없습니다."); history.back();</script>')

    conn = get_db()
    cursor = conn.cursor()

    try:
        # --- 👇 수정된 부분 시작 ---
        
        # 재가입이 가능하도록 기존 고유 정보를 변경합니다.
        # 타임스탬프를 사용하여 혹시 모를 중복을 방지합니다.
        timestamp_suffix = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        original_login_id = session['user_id']
        
        deleted_login_id = f"deleted_{original_login_id}_{timestamp_suffix}"
        deleted_hakbun = f"deleted_{user['hakbun']}_{timestamp_suffix}"
        deleted_nickname = f"탈퇴한사용자_{str(uuid.uuid4())[:8]}"
        
        # 사용자 정보 비활성화 (Soft Delete)
        cursor.execute("""
            UPDATE users 
            SET 
                login_id = ?,
                hakbun = ?,
                nickname = ?, 
                pw = ?, 
                profile_image = 'images/profiles/defualt_images.jpeg',
                profile_message = '탈퇴한 사용자의 프로필입니다.',
                clubhak = NULL,
                clubchi = NULL,
                clubjin = NULL,
                profile_public = 0,
                autologin_token = NULL,
                status = 'deleted'
            WHERE login_id = ?
        """, (deleted_login_id, deleted_hakbun, deleted_nickname, str(uuid.uuid4()), original_login_id))
        
        # --- 👆 수정된 부분 끝 ---
        
        conn.commit()

        add_log('DELETE_ACCOUNT', original_login_id, f"사용자({original_login_id})가 계정을 삭제했습니다.")

        # 세션 정리 및 로그아웃 처리
        session.clear()
        resp = make_response(Response('<script>alert("계정이 안전하게 삭제되었습니다. 이용해주셔서 감사합니다."); window.location.href = "/";</script>'))
        resp.set_cookie('remember_token', '', max_age=0)
        return resp

    except Exception as e:
        conn.rollback()
        print(f"Error during account deletion: {e}")
        return Response('<script>alert("계정 삭제 중 오류가 발생했습니다."); history.back();</script>')
    
@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    posts_per_page = 20

    if not query:
        return redirect(request.referrer or url_for('main_page'))

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # FTS 검색어 형식으로 변경 (띄어쓰기를 AND 연산자로)
    # 예: "안녕 하세요" -> "안녕 AND 하세요"
    search_term_fts = ' AND '.join(query.split())
    # 닉네임 검색은 기존 LIKE 방식 유지
    search_term_like = f'%{query}%'

    try:
        # 1. 검색 결과 총 개수 조회 (FTS와 닉네임 검색 결과를 합산)
        # FTS를 사용하여 제목/내용 검색, LIKE를 사용하여 닉네임 검색
        count_query = """
            SELECT COUNT(DISTINCT p.id)
            FROM posts p
            JOIN users u ON p.author = u.login_id
            WHERE 
                (p.id IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?))
                OR (u.nickname LIKE ?)
        """
        cursor.execute(count_query, (search_term_fts, search_term_like))
        total_posts = cursor.fetchone()[0]
        total_pages = math.ceil(total_posts / posts_per_page) if total_posts > 0 else 1

        # 2. 현재 페이지에 해당하는 검색 결과 목록 조회
        offset = (page - 1) * posts_per_page
        search_query = """
            SELECT
                p.id, p.title, p.comment_count, p.updated_at, p.view_count,
                u.nickname,
                b.board_name,
                SUM(CASE WHEN r.reaction_type = 'like' THEN 1 WHEN r.reaction_type = 'dislike' THEN -1 ELSE 0 END) as net_reactions
            FROM posts p
            JOIN users u ON p.author = u.login_id
            JOIN board b ON p.board_id = b.board_id
            LEFT JOIN reactions r ON r.target_id = p.id AND r.target_type = 'post'
            WHERE 
                (p.id IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?))
                OR (u.nickname LIKE ?)
              AND u.status = 'active'
            GROUP BY p.id
            ORDER BY p.updated_at DESC
            LIMIT ? OFFSET ?
        """
        cursor.execute(search_query, (search_term_fts, search_term_like, posts_per_page, offset))
        posts = cursor.fetchall()

    except sqlite3.OperationalError as e:
        # FTS 구문 오류 등 예외 처리
        if "fts5" in str(e):
             return Response('<script>alert("검색어에 특수문자를 사용할 수 없습니다."); history.back();</script>')
        print(f"Error during search: {e}")
        return Response('<script>alert("검색 중 오류가 발생했습니다."); history.back();</script>')
    except Exception as e:
        print(f"Error during search: {e}")
        return Response('<script>alert("검색 중 오류가 발생했습니다."); history.back();</script>')
    
    return render_template('search_results.html',
                           posts=posts,
                           query=query,
                           total_posts=total_posts,
                           total_pages=total_pages,
                           current_page=page, user=g.user)

@app.route('/notifications/unread-count')
@login_required
def unread_notification_count():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM notifications WHERE recipient_id = ? AND is_read = 0", (g.user['login_id'],))
    count = cursor.fetchone()[0]
    return jsonify({'count': count})

@app.route('/notifications')
@login_required
def get_notifications():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    query = """
        SELECT n.*, u.nickname as actor_nickname
        FROM notifications n
        JOIN users u ON n.actor_id = u.login_id
        WHERE n.recipient_id = ?
        ORDER BY n.created_at DESC
        LIMIT 10
    """
    cursor.execute(query, (g.user['login_id'],))
    notifications = [dict(row) for row in cursor.fetchall()]
    return jsonify(notifications)

@app.route('/notifications/read/<int:notification_id>', methods=['POST'])
@login_required
def read_notification(notification_id):
    conn = get_db()
    cursor = conn.cursor()
    # 본인의 알림이 맞는지 확인 후 읽음 처리
    cursor.execute("UPDATE notifications SET is_read = 1 WHERE id = ? AND recipient_id = ?", (notification_id, g.user['login_id']))
    conn.commit()
    return jsonify({'status': 'success'})

@app.errorhandler(413)
def request_entity_too_large(error):
    return Response('<script>alert("업로드할 수 있는 파일의 최대 크기는 5MB입니다."); history.back();</script>'), 413

@app.errorhandler(404)
def page_not_found(error):
    user_data = g.user if 'user' in g else None
    return render_template('404.html', user=user_data), 404

# Server Drive Unit
if __name__ == '__main__':
    init_log_db()
    app.run(host='0.0.0.0', port=5000, debug=True)