from gevent import monkey
monkey.patch_all()

from flask import Flask, request, render_template, url_for, redirect, jsonify, session, g, Response, make_response
from werkzeug.middleware.proxy_fix import ProxyFix
from bleach.css_sanitizer import CSSSanitizer
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from gevent.queue import Queue, Empty
from nfcl.core import ComciganAPI
from cachetools import TTLCache
from flask_bcrypt import Bcrypt
from flask_caching import Cache
from dotenv import load_dotenv
from functools import wraps
from flask import jsonify
from PIL import Image
import datetime
import requests
import hashlib
import secrets
import sqlite3
import shutil
import bleach
import socket
import uuid
import json
import math
import html
import os
import re

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

# 캐시 설정 추가 - 성능 향상을 위한 핵심
app.config['CACHE_TYPE'] = 'SimpleCache'  # 메모리 기반 캐시
app.config['CACHE_DEFAULT_TIMEOUT'] = 300  # 5분 캐시
cache = Cache(app)

bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

DATABASE = 'data.db'
LOG_DATABASE = 'log.db'

ACADEMIC_CLUBS = ["WIN", "TNT", "PLUTONIUM", "LOGIC", "LOTTOL", "RAIBIT", "QUASAR"]
HOBBY_CLUBS = ["책톡", "픽쳐스", "메카", "퓨전", "차랑", "스포츠문화부", "체력단련부", "I-FLOW", "아마빌레"]
CAREER_CLUBS = ["TIP", "필로캠", "천수동", "씽크빅", "WIZARD", "METEOR", "엔진"]
GUEST_USER_ID = '__guest__'

ETACON_UPLOAD_FOLDER = 'static/images/etacons'
ALLOWED_ETACON_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

os.makedirs(ETACON_UPLOAD_FOLDER, exist_ok=True)

def allowed_etacon_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_ETACON_EXTENSIONS

def save_etacon_image(file, sub_folder):
    """
    이미지를 저장하고 경로를 반환합니다.
    GIF는 최적화하여 저장하고, 정적 이미지는 포맷을 유지합니다.
    JPG 저장 시 투명 배경(RGBA) 오류를 방지합니다.
    sub_folder: 패키지별 폴더 (예: 'pack_1')
    """
    filename = secure_filename(file.filename)

    try:
        ext = filename.rsplit('.', 1)[1].lower()
    except IndexError:
        ext = 'jpg' # 확장자가 없는 예외적인 경우 기본값

    unique_filename = f"{uuid.uuid4().hex[:8]}.{ext}"
    
    save_dir = os.path.join(ETACON_UPLOAD_FOLDER, sub_folder)
    os.makedirs(save_dir, exist_ok=True)
    
    save_path = os.path.join(save_dir, unique_filename)
    
    # Pillow로 이미지 처리 (GIF 지원)
    try:
        img = Image.open(file)
        
        # GIF인 경우 save_all=True로 애니메이션 유지
        if file.filename.lower().endswith('.gif'):
            img.save(save_path, save_all=True, optimize=True, loop=0)
        else:
            # 정적 이미지는 포맷에 맞게 저장
            # [수정] JPG/JPEG 저장 시 RGBA 모드 오류 해결 로직 추가
            if ext in ['jpg', 'jpeg']:
                if img.mode in ('RGBA', 'LA'):
                    # 투명한 배경을 흰색으로 채워서 RGB로 변환 (투명->검은색 방지)
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    # 이미지의 알파 채널을 마스크로 사용하여 붙여넣기
                    background.paste(img, mask=img.split()[-1])
                    img = background
                elif img.mode == 'P':
                    # 팔레트 모드일 경우 RGB로 변환
                    img = img.convert('RGB')
            
            img.save(save_path, optimize=True)
            
        return f"images/etacons/{sub_folder}/{unique_filename}"
    except Exception as e:
        print(f"이미지 저장 실패: {e}")
        return None

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

def get_grade_class(hakbun):
    """
    학번(예: 2305)을 입력받아 학년(2)과 반(3)을 반환합니다.
    형식이 맞지 않으면 None, None을 반환합니다.
    """
    try:
        s_hakbun = str(hakbun)
        if len(s_hakbun) == 4:
            grade = int(s_hakbun[0])
            class_num = int(s_hakbun[1])
            return grade, class_num
    except (ValueError, IndexError):
        pass
    return None, None

def get_timetable_data(grade, class_num):
    """
    DB에서 시간표를 조회하고, 없거나 날짜가 지났으면 nfcl로 크롤링하여 갱신합니다.
    """
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    cursor = conn.cursor()

    # 1. DB 조회
    cursor.execute("SELECT week_schedule, updated_at FROM timetables WHERE grade = ? AND class_num = ?", (grade, class_num))
    row = cursor.fetchone()

    # 2. 데이터가 있고, 오늘 업데이트된 것이라면 DB 데이터 반환
    if row and row[1] == today:
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            pass # JSON 파싱 에러 시 재수집

    # 3. 데이터가 없거나 구형이라면 크롤링 (nfcl 사용)
    try:
        # headless=True로 설정하여 백그라운드에서 실행
        nfcl = ComciganAPI(headless=True)
        # 학교명 하드코딩 (필요 시 환경 변수로 분리 가능)
        data = nfcl.get_timetable("인천과학고등학교", grade, class_num)
        
        if "error" in data:
            print(f"NFCL Error: {data['error']}")
            # 에러 발생 시 기존 데이터가 있다면 반환, 없다면 None
            return json.loads(row[0]) if row else None

        week_schedule = data['timetable'] # 주간 시간표 딕셔너리

        # 4. DB 저장 (INSERT OR REPLACE)
        json_schedule = json.dumps(week_schedule, ensure_ascii=False)
        cursor.execute("""
            INSERT OR REPLACE INTO timetables (grade, class_num, week_schedule, updated_at)
            VALUES (?, ?, ?, ?)
        """, (grade, class_num, json_schedule, today))
        conn.commit()

        return week_schedule

    except Exception as e:
        print(f"Timetable Fetch Error: {e}")
        add_log('ERROR', 'SYSTEM', f"시간표 수집 실패 ({grade}-{class_num}): {e}")
        return None

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
    # 정적 파일 요청 등은 건너뜀
    if request.endpoint and 'static' in request.endpoint:
        return

    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE login_id = ?", (user_id,))
        g.user = cursor.fetchone()

        # [기존 로직] 제재 상태 만료 확인
        if g.user and g.user['status'] == 'banned' and g.user['banned_until']:
            try:
                banned_until_date = datetime.datetime.strptime(g.user['banned_until'], '%Y-%m-%d %H:%M:%S')
                if datetime.datetime.now() > banned_until_date:
                    cursor.execute("UPDATE users SET status = 'active', banned_until = NULL WHERE login_id = ?", (g.user['login_id'],))
                    conn.commit()
                    cursor.execute("SELECT * FROM users WHERE login_id = ?", (user_id,))
                    g.user = cursor.fetchone()
            except (ValueError, TypeError):
                pass

# 2. [필수] 차단된 사용자 확인 (반드시 위 함수보다 아래에 있어야 합니다!)
@app.before_request
def block_banned_users():
    # g.user가 아직 생성되지 않았거나(위 함수 누락/순서 에러), 비로그인 상태면 통과
    if not hasattr(g, 'user') or not g.user:
        return

    # 정적 리소스 및 로그아웃 등은 제외
    if request.endpoint and ('static' in request.endpoint or 'logout' in request.endpoint):
        return

    # 차단된 유저인지 확인
    if g.user['status'] == 'banned':
        # API, 댓글 작성 등 개별 기능 제한은 @check_banned에서 처리하므로 패스
        if request.path.startswith('/api/') or request.path.startswith('/react/') or request.path.startswith('/comment/'):
            return

        # "최초 접속" 알림 처리
        if not session.get('banned_notice_shown'):
            banned_until_str = "알 수 없음"
            if g.user['banned_until']:
                try:
                    dt = datetime.datetime.strptime(g.user['banned_until'], '%Y-%m-%d %H:%M:%S')
                    banned_until_str = dt.strftime('%Y년 %m월 %d일 %H:%M')
                except ValueError:
                    banned_until_str = g.user['banned_until']

            message = f"활동이 정지된 계정입니다.\\n(글 읽기는 가능하지만 작성 및 추천은 제한됩니다.)\\n\\n[해제 예정일]\\n{banned_until_str}"
            
            session['banned_notice_shown'] = True # 알림 확인 처리
            
            if request.method == 'GET':
                return Response(f'''
                    <script>
                        alert("{message}");
                        window.location.reload(); 
                    </script>
                ''')

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
    """알림을 생성하고 DB에 저장하는 함수 (익명 게시판 처리 추가)"""
    # 자기 자신에게는 알림을 보내지 않음
    if recipient_id == actor_id:
        return

    conn = get_db()
    cursor = conn.cursor()
    created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 1. 알림 DB 저장
    cursor.execute("""
        INSERT INTO notifications 
        (recipient_id, actor_id, action, target_type, target_id, post_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (recipient_id, actor_id, action, target_type, target_id, post_id, created_at))
    conn.commit()
    notification_id = cursor.lastrowid 

    # --- ▼ [수정] 익명 게시판 여부 확인 및 닉네임 마스킹 처리 ▼ ---
    
    # 해당 게시글이 어떤 게시판에 속해 있는지 조회
    cursor.execute("SELECT board_id FROM posts WHERE id = ?", (post_id,))
    post_row = cursor.fetchone()
    
    actor_nickname = '알 수 없는 사용자'

    # 게시판 ID가 3(익명게시판)인 경우, 닉네임을 '익명'으로 고정
    if post_row and post_row[0] == 3:
        actor_nickname = '익명'
    else:
        # 일반 게시판인 경우, 실제 유저 닉네임 조회
        if actor_id == GUEST_USER_ID:
            actor_nickname = '익명(비회원)'
        else:
            cursor.execute("SELECT nickname FROM users WHERE login_id = ?", (actor_id,))
            actor = cursor.fetchone()
            if actor:
                # row_factory 설정에 따라 인덱스 또는 키로 접근 (안전하게 인덱스 0 사용)
                actor_nickname = actor[0]

    # --- ▲ [수정] ---

    # 3. 클라이언트(브라우저)로 보낼 메시지 객체를 생성합니다.
    message_to_send = {
        'action': action,
        'actor_nickname': actor_nickname, # 수정된 닉네임 사용
        'post_id': post_id,
        'is_read': 0, 
        'id': notification_id
    }

    # 4. 알림 채널을 통해 해당 사용자에게 메시지 발행(publish)
    notification_channel.publish(recipient_id, message_to_send)

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

# Bob (School Meal Information) - 캐시 적용 (하루 단위로 변경되므로 30분 캐시)
@cache.memoize(timeout=1800)  # 30분 캐시
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
                f"?KEY={os.getenv('NEIS_API_KEY')}"
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
def update_exp_level(user_id, exp_change, commit=True):
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

    if commit:
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

# Get Recent Posts from board id (캐시 적용)
@cache.memoize(timeout=60)  # 1분 캐시
def get_recent_posts(board_id):
    """
    특정 게시판 ID를 받아 해당 게시판의 게시글을 최신순으로 5개 가져옵니다.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
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
        # sqlite3.Row를 dict로 변환 (캐시 가능하도록)
        return [dict(row) for row in posts]
    except Exception as e:
        add_log('ERROR', 'SYSTEM', f"Error fetching recent posts for board_id {board_id}: {e}")
        return []

@cache.memoize(timeout=120)  # 2분 캐시
def get_hot_posts():
    """최근 7일간 추천 수가 10개 이상인 게시글을 상위 5개까지 가져옵니다."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
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
    # sqlite3.Row를 dict로 변환 (캐시 가능하도록)
    return [dict(row) for row in cursor.fetchall()]

@cache.memoize(timeout=120)  # 2분 캐시
def get_trending_posts():
    """최근 24시간 동안 조회수가 10 이상인 게시글 중 가장 높은 글을 상위 5개까지 가져옵니다."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
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
    # sqlite3.Row를 dict로 변환 (캐시 가능하도록)
    return [dict(row) for row in cursor.fetchall()]

# 급식 API 엔드포인트 (비동기 로딩용)
@app.route('/api/bob')
def api_bob():
    bob_data = get_bob()
    if bob_data:
        return jsonify({
            'status': 'success',
            'data': {
                'breakfast': bob_data[0],
                'lunch': bob_data[1],
                'dinner': bob_data[2]
            }
        })
    return jsonify({'status': 'error', 'message': '급식 정보를 불러올 수 없습니다.'})

# 시간표 API 엔드포인트 (비동기 로딩용)
@app.route('/api/timetable')
def api_timetable():
    # 로그인 체크
    if 'user_id' not in session or not g.user:
        return jsonify({'status': 'error', 'message': '로그인이 필요합니다.'}), 401
    
    user_data = g.user
    timetable_today = []
    
    if user_data and user_data['hakbun']:
        grade, class_num = get_grade_class(user_data['hakbun'])
        
        if grade and class_num:
            full_timetable = get_timetable_data(grade, class_num)
            
            if full_timetable:
                weekday_map = {0: '월', 1: '화', 2: '수', 3: '목', 4: '금'}
                today_idx = datetime.datetime.now().weekday()
                target_day = weekday_map.get(today_idx, '월')
                timetable_today = full_timetable.get(target_day, [])
    
    return jsonify({
        'status': 'success',
        'data': timetable_today
    })

# Main Page
@app.route('/')
def main_page():
    if 'user_id' in session:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        free_board_posts = get_recent_posts(1)
        info_board_posts = get_recent_posts(3)
        hot_posts = get_hot_posts()
        trending_posts = get_trending_posts()
        
        user_data = g.user

        # 급식/시간표는 AJAX로 비동기 로딩하므로 제거

        if user_data:
            return render_template('main_logined.html', 
                                   user=user_data, 
                                   free_posts=free_board_posts, 
                                   info_posts=info_board_posts,
                                   hot_posts=hot_posts,
                                   trending_posts=trending_posts,
                           hakbun=user_data['hakbun'], 
                           name=user_data['name'], 
                           gen=user_data['gen'], 
                           nickname=user_data['nickname'], 
                           profile_image=user_data['profile_image'],
                           level=user_data['level'], 
                           exp=user_data['exp'], 
                           post_count=user_data['post_count'], 
                           comment_count=user_data['comment_count'], 
                           point=user_data['point'], 
                           academic_clubs=ACADEMIC_CLUBS,
                           hobby_clubs=HOBBY_CLUBS,
                           career_clubs=CAREER_CLUBS)
        else:
            session.clear()
            return render_template('main_notlogined.html')
    else:
        return render_template('main_notlogined.html')

googlebot_ip_cache = {}
googlebot_ip_cache = TTLCache(maxsize=1000, ttl=3600)

# Googlebot Verification Logic
def is_googlebot():
    """
    User-Agent와 DNS 양방향 조회를 통해 Googlebot을 검증합니다. (캐시 사용)
    User-Agent 스푸핑을 방지하기 위함입니다.
    """
    user_agent = request.user_agent.string
    # 1. User-Agent로 1차 필터링 (가장 빠름)
    if not user_agent or "Googlebot" not in user_agent:
        return False

    ip = request.remote_addr
    
    # 2. 로컬 IP는 봇으로 간주하지 않음
    if ip == '127.0.0.1':
        return False

    # 3. 캐시 확인 (가장 빈번한 케이스)
    if ip in googlebot_ip_cache:
        return googlebot_ip_cache[ip]

    try:
        # 4. 역방향 DNS 조회 (IP -> Hostname)
        hostname, _, _ = socket.gethostbyaddr(ip)

        # 5. Hostname 검증
        if not (hostname.endswith('.googlebot.com') or hostname.endswith('.google.com')):
            googlebot_ip_cache[ip] = False # 캐시에 '실패' 기록
            return False

        # 6. 순방향 DNS 조회 (Hostname -> IP)
        resolved_ip = socket.gethostbyname(hostname)

        # 7. IP 일치 확인 (최종 검증)
        if ip == resolved_ip:
            googlebot_ip_cache[ip] = True # 캐시에 '성공' 기록
            return True
        else:
            googlebot_ip_cache[ip] = False # 캐시에 '실패' 기록
            return False

    except (socket.herror, socket.gaierror):
        # DNS 조회 실패 (일시적 오류일 수 있으나, 일단 봇이 아닌 것으로 간주)
        googlebot_ip_cache[ip] = False
        return False
    except Exception as e:
        # 기타 예외 로깅
        # add_log 함수가 g.user를 필요로 할 수 있으므로, 여기서는 print를 사용합니다.
        print(f"Error during Googlebot verification for IP {ip}: {e}")
        googlebot_ip_cache[ip] = False
        return False

# For Login Required Page
# @login_required under @app.route
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        g.is_googlebot = is_googlebot() 
        
        if 'user_id' not in session and not g.is_googlebot:
            return Response('<script> alert("로그인 사용자만 접근할 수 있습니다."); history.back(); </script>')
        return f(*args, **kwargs)
    return decorated_function

# For Admin Required Page
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not g.user or g.user['role'] != 'admin':
            # API 요청인 경우 JSON으로, 아닌 경우 스크립트로 응답
            if request.path.startswith('/admin/'):
                return jsonify({'status': 'error', 'message': '관리자만 접근 가능합니다.'}), 403
            return Response('<script> alert("관리자만 접근 가능합니다."); history.back(); </script>')
        return f(*args, **kwargs)
    return decorated_function

@app.route('/stream')
@login_required
def stream():
    # --- ▼ [핵심 수정] ---
    # 제너레이터가 실행되기 전, 즉 컨텍스트가 살아있을 때 user_id를 미리 변수에 저장합니다.
    current_user_id = g.user['login_id']

    def event_stream():
        # 이제 제너레이터는 컨텍스트가 사라져도 안전한 'current_user_id' 변수를 사용합니다.
        messages = notification_channel.subscribe(current_user_id)
        
        try:
            while True:
                try:
                    message = messages.get(timeout=20)
                    yield f"data: {json.dumps(message)}\n\n"
                except Empty:
                    yield ":heartbeat\n\n"
        except GeneratorExit:
            # 클라이언트 연결이 끊어지면 정상적으로 구독 해제
            pass
        except Exception as e:
            # 스트림에서 다른 예외가 발생할 경우 로그를 남깁니다.
            print(f"An error occurred in the event stream for user {current_user_id}: {e}")
        finally:
            # 연결이 어떤 이유로든 종료될 때 항상 구독을 해제합니다.
            notification_channel.unsubscribe(current_user_id)
    # --- ▲ [핵심 수정] ---

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

        if not isinstance(birth, str):
            birth = str(birth)

        # 1. 입력값 길이 확인
        if len(birth) != 8:
            return Response('<script> alert("생년월일은 8자리로 입력해야 합니다."); history.back(); </script>')

        try:
            year = int(birth[0:4])
            month = int(birth[4:6])
            day = int(birth[6:8])

            datetime.date(int(year), int(month), int(day))
        except ValueError:
            return Response('<script> alert("생년월일 형식을 다시 확인하세요. 1"); history.back(); </script>')
        except Exception as e:
            print(e)
            return Response('<script> alert("생년월일 형식을 다시 확인하세요. 3"); history.back(); </script>')

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
        
        if len(name) <= 2 or len(name) >= 20:
            return Response('<script> alert("이름은 2자 이상 20자 이하로 입력해야 합니다."); history.back(); </script>')
        if len(id) <= 2 or len(id) >= 20:
            return Response('<script> alert("아이디는 2자 이상 20자 이하로 입력해야 합니다."); history.back(); </script>')
        
        hashed_pw = bcrypt.generate_password_hash(pw).decode('utf-8')
        join_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        default_profile = 'images/profiles/default_image.jpeg'
        
        # DATA INSERT to DB
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (hakbun, gen, name, pw, login_id, nickname, birth, profile_image, join_date, role, is_autologin, autologin_token, level, exp, post_count, comment_count, point) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'student', 0, '', 1, 0, 0, 0, 0)", (hakbun, gen, name, hashed_pw, id, nick, birth, default_profile, join_date))
        conn.commit()

        session.pop('hakbun', None)
        session.pop('name', None)
        session.pop('gen', None)
        session.pop('agree', None)

        add_log('CREATE_USER', id, f"'{nick}'({id})님이 가입했습니다.({hakbun}, {name})")

        return Response('<script> alert("회원가입이 완료되었습니다."); window.location.href = "/login"; </script>') # After Register
    
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
            
            session.clear()
            session['user_id'] = user[0]

            if remember:
                token = secrets.token_hex(32)
                hashed_token = hashlib.sha256(token.encode()).hexdigest()

                cursor.execute('UPDATE users SET autologin_token = ? WHERE login_id = ?', (hashed_token, user[0]))
                conn.commit()

                resp = make_response(redirect("/"))
                resp.set_cookie('remember_token', token, max_age=datetime.timedelta(days=90), httponly=True)
                return resp

            return redirect("/")
        else:
            return Response('<script> alert("아이디 또는 비밀번호가 올바르지 않습니다."); history.back(); </script>')

    return render_template('login_form.html') # GET

# Find ID (아이디 찾기)
@app.route('/find-id', methods=['GET', 'POST'])
def find_id():
    if 'user_id' in session:
        return redirect("/")

    if request.method == 'POST':
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

            # 리로스쿨 인증 성공 - 해당 이름과 학번으로 가입된 계정 찾기
            conn = get_db()
            cursor = conn.cursor()
            
            cursor.execute('SELECT login_id FROM users WHERE name = ? AND hakbun = ? AND status = "active"', 
                          (api_result['name'], api_result['student_number']))
            user = cursor.fetchone()

            if user:
                found_id = user[0]
                # 아이디 일부 마스킹 처리 (예: abc123 -> ab***3)
                if len(found_id) > 3:
                    masked_id = found_id[:2] + '*' * (len(found_id) - 3) + found_id[-1]
                else:
                    masked_id = found_id[0] + '*' * (len(found_id) - 1)
                
                return render_template('find_id_result.html', found_id=found_id, masked_id=masked_id)
            else:
                return Response('''
        <script>
            alert("해당 정보로 가입된 계정이 없습니다.");
            history.back();
        </script>
    ''')

        except requests.exceptions.HTTPError as http_err:
            add_log('ERROR', 'SYSTEM', f"HTTP error during Find ID: {http_err}")
            return Response('''
    <script>
        alert("HTTP 오류가 발생했습니다.")
        history.back();
    </script>
''')
        except requests.exceptions.RequestException as req_err:
            add_log('ERROR', 'SYSTEM', f"Request error during Find ID: {req_err}")
            return Response('''
    <script>
        alert("요청 중 오류가 발생했습니다.")
        history.back();
    </script>
''')

    return render_template('find_id.html')

# Find Password (비밀번호 찾기) - Step 1: 아이디 입력
@app.route('/find-password', methods=['GET', 'POST'])
def find_password():
    if 'user_id' in session:
        return redirect("/")

    if request.method == 'POST':
        login_id = request.form['login_id']
        
        # 해당 아이디가 존재하는지 확인
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT login_id, name, hakbun FROM users WHERE login_id = ? AND status = "active"', (login_id,))
        user = cursor.fetchone()

        if user:
            session['find_pw_login_id'] = login_id
            session['find_pw_name'] = user[1]
            session['find_pw_hakbun'] = user[2]
            return redirect('/find-password/verify')
        else:
            return Response('''
        <script>
            alert("해당 아이디로 가입된 계정이 없습니다.");
            history.back();
        </script>
    ''')

    return render_template('find_password.html')

# Find Password (비밀번호 찾기) - Step 2: 리로스쿨 인증
@app.route('/find-password/verify', methods=['GET', 'POST'])
def find_password_verify():
    if 'user_id' in session:
        return redirect("/")
    
    if 'find_pw_login_id' not in session:
        return redirect('/find-password')

    if request.method == 'POST':
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

            # 리로스쿨 인증 성공 - 이름과 학번이 일치하는지 확인
            # 공백 제거 및 문자열 변환하여 비교
            riro_name = str(api_result['name']).strip()
            riro_hakbun = str(api_result['student_number']).strip()
            db_name = str(session['find_pw_name']).strip()
            db_hakbun = str(session['find_pw_hakbun']).strip()
            
            if (riro_name == db_name and riro_hakbun == db_hakbun):
                session['find_pw_verified'] = True
                return redirect('/find-password/reset')
            else:
                return Response(f'''
        <script>
            alert("해당 계정의 정보와 일치하지 않습니다.\\n(리로스쿨: {riro_name}, {riro_hakbun} / 가입정보: {db_name}, {db_hakbun})");
            history.back();
        </script>
    ''')

        except requests.exceptions.HTTPError as http_err:
            add_log('ERROR', 'SYSTEM', f"HTTP error during Find Password verify: {http_err}")
            return Response('''
    <script>
        alert("HTTP 오류가 발생했습니다.")
        history.back();
    </script>
''')
        except requests.exceptions.RequestException as req_err:
            add_log('ERROR', 'SYSTEM', f"Request error during Find Password verify: {req_err}")
            return Response('''
    <script>
        alert("요청 중 오류가 발생했습니다.")
        history.back();
    </script>
''')

    return render_template('find_password_verify.html')

# Find Password (비밀번호 찾기) - Step 3: 새 비밀번호 설정
@app.route('/find-password/reset', methods=['GET', 'POST'])
def find_password_reset():
    if 'user_id' in session:
        return redirect("/")
    
    if not session.get('find_pw_verified') or 'find_pw_login_id' not in session:
        return redirect('/find-password')

    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if new_password != confirm_password:
            return Response('''
        <script>
            alert("비밀번호가 일치하지 않습니다.");
            history.back();
        </script>
    ''')

        if len(new_password) < 6:
            return Response('''
        <script>
            alert("비밀번호는 6자 이상이어야 합니다.");
            history.back();
        </script>
    ''')

        # 비밀번호 업데이트
        conn = get_db()
        cursor = conn.cursor()
        hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        
        cursor.execute('UPDATE users SET pw = ? WHERE login_id = ?', 
                      (hashed_password, session['find_pw_login_id']))
        conn.commit()

        add_log('RESET_PASSWORD', session['find_pw_login_id'], f"비밀번호가 재설정되었습니다.")

        # 세션 정리
        session.pop('find_pw_login_id', None)
        session.pop('find_pw_name', None)
        session.pop('find_pw_hakbun', None)
        session.pop('find_pw_verified', None)

        return Response('''
        <script>
            alert("비밀번호가 성공적으로 변경되었습니다.");
            window.location.href = "/login";
        </script>
    ''')

    return render_template('find_password_reset.html')

# logout
@app.route('/logout')
def logout():
    if 'user_id' in session: # 로그인 상태인지 확인
        conn = get_db()
        cursor = conn.cursor()
        # DB에서 자동 로그인 토큰 무효화
        cursor.execute('UPDATE users SET autologin_token = NULL WHERE login_id = ?', (session['user_id'],))
        conn.commit()

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
@check_banned
def post_write():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == 'POST':
        if not g.user:
            return Response('<script>alert("로그인이 필요합니다."); location.href="/login";</script>')
        # 1. 폼 데이터 수신
        title = request.form.get('title')
        content = request.form.get('content')
        board_id = request.form.get('board_id') # board_id 수신

        poll_title = request.form.get('poll_title')
        poll_options = request.form.getlist('poll_options[]')
        
        has_poll = False
        if poll_title and poll_options:
            # 빈 옵션 제거
            poll_options = [opt for opt in poll_options if opt.strip()]
            if len(poll_options) < 2:
                return Response('<script>alert("투표 항목은 최소 2개 이상이어야 합니다."); history.back();</script>')
            has_poll = True

        if not board_id:
             return Response('<script>alert("게시판을 선택해주세요."); history.back();</script>')
        
        cursor.execute("SELECT is_public FROM board WHERE board_id = ?", (board_id,))
        board = cursor.fetchone()

        if not board:
            return Response('<script>alert("존재하지 않는 게시판입니다."); history.back();</script>')

        is_public_board = board[0] == 1

        # 비회원이 비공개 게시판에 쓰려고 할 때 차단
        if not g.user and not is_public_board:
            return Response('<script>alert("로그인이 필요한 게시판입니다."); history.back();</script>')
        
        # 작성자 ID 설정 (로그인 시: 사용자 ID, 비로그인 시: 게스트 ID)
        author_id = g.user['login_id'] if g.user else GUEST_USER_ID

        if len(title) > 50:
            return Response('<script>alert("제목은 50자를 초과할 수 없습니다."); history.back();</script>')

        is_notice = 0
        if g.user and g.user['role'] == 'admin':
            is_notice = 1 if request.form.get('is_notice') == 'on' else 0

        target_grade = 0
        only_my_gen = request.form.get('only_my_gen') # 체크박스 값 확인 ('on' 또는 None)
        
        if only_my_gen == 'on':
            if g.user and g.user['gen']:
                try:
                    # 로그인한 사용자의 기수 정보를 가져와 설정
                    target_grade = int(g.user['gen'])
                except ValueError:
                    target_grade = 0 # 기수 정보 오류 시 전체 공개
            else:
                return Response('<script>alert("기수 정보를 찾을 수 없어 제한을 설정할 수 없습니다."); history.back();</script>')

        # 2. 서버 사이드 유효성 검사
        if not title or not content or not board_id:
            return Response('<script>alert("게시판, 제목, 내용을 모두 입력해주세요."); history.back();</script>')
        
        is_valid_size, img_idx = check_content_image_size(content, 15)
        if not is_valid_size:
            return Response(f'<script>alert("{img_idx}번째 이미지의 용량이 15MB를 초과합니다.\n"); history.back();</script>')

        plain_text_content = bleach.clean(content, tags=[], strip=True)
        if len(plain_text_content) > 5000:
            return Response('<script>alert("글자 수는 5,000자를 초과할 수 없습니다."); history.back();</script>')
        if len(title) > 50:
            return Response('<script>alert("제목은 50자를 초과할 수 없습니다."); history.back();</script>')
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

        if sanitized_content.count('<img') > 10:
            return Response('<script>alert("이미지는 최대 10개까지 첨부할 수 있습니다."); history.back();</script>')

        final_content = sanitized_content

        # 4. 데이터베이스에 저장
        try:
            created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            query = """
                INSERT INTO posts
                (board_id, title, content, author, created_at, updated_at, view_count, comment_count, is_notice, target_grade)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
            """
            cursor.execute(query, (board_id, title, final_content, author_id, created_at, created_at, is_notice, target_grade))

            post_id = cursor.lastrowid # last_insert_rowid() 대신 cursor.lastrowid 사용 권장

            if has_poll:
                cursor.execute("INSERT INTO polls (post_id, title, created_at) VALUES (?, ?, ?)", 
                               (post_id, poll_title, created_at))
                poll_id = cursor.lastrowid
                
                for option_text in poll_options:
                    cursor.execute("INSERT INTO poll_options (poll_id, option_text, vote_count) VALUES (?, ?, 0)",
                                   (poll_id, option_text))

            cursor.execute("UPDATE users SET post_count = post_count + 1 WHERE login_id = ?", (author_id,))

            update_exp_level(author_id, 50)

            conn.commit()

            add_log('CREATE_POST', author_id, f"'{title}' 글 작성(id : {post_id}). 내용 : {final_content}")

            return redirect(url_for('post_list', board_id=board_id))
        except Exception as e:
            print(f"Database error: {e}")
            add_log('ERROR', author_id, f"Error saving post: {e}")
            return Response('<script>alert("게시글 저장 중 오류가 발생했습니다."); history.back();</script>')

    else:
        # GET 요청 시, 쿼리 파라미터에서 board_id를 가져오려고 시도
        requested_board_id = request.args.get('board_id')

        if not g.user: # 비회원인 경우
            if not requested_board_id:
                # 비회원이 board_id 없이 /post-write에 접근하면 로그인 페이지로
                return redirect(url_for('login'))
                
            cursor.execute("SELECT board_name, is_public FROM board WHERE board_id = ?", (requested_board_id,))
            board = cursor.fetchone()
            
            if not board:
                return Response('<script>alert("존재하지 않는 게시판입니다."); history.back();</script>')
                
            if board['is_public'] == 1:
                # 비회원 + 공개 게시판 -> 비회원 글쓰기 페이지로
                return render_template('post_write_guest.html', board_id=requested_board_id, board_name=board['board_name'])
            else:
                # 비회원 + 비공개 게시판 -> 로그인 필요
                return Response('<script>alert("로그인이 필요한 게시판입니다."); location.href="/login";</script>')

        else: # 로그인한 회원인 경우
            # 기존 로직대로 게시판 목록을 전달
            cursor.execute("SELECT board_id, board_name FROM board ORDER BY board_id")
            boards = cursor.fetchall()
            return render_template('post_write.html', boards=boards)

@app.route('/post-write-guest/<int:board_id>', methods=['GET', 'POST'])
def post_write_guest(board_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. 해당 게시판 정보 확인
    cursor.execute("SELECT board_name, is_public FROM board WHERE board_id = ?", (board_id,))
    board = cursor.fetchone()

    if not board:
        return Response('<script>alert("존재하지 않는 게시판입니다."); history.back();</script>')
    
    # 2. 공개 게시판이 아니면 차단
    if board['is_public'] != 1:
        return Response('<script>alert("비회원은 이 게시판에 글을 쓸 수 없습니다."); history.back();</script>')
        
    # 3. 로그인한 유저가 이 URL로 접근하면 정식 글쓰기 페이지로 리디렉션
    if g.user:
        return redirect(url_for('post_write'))

    if request.method == 'POST':
        # 4. 폼 데이터 수신
        title = request.form.get('title')
        content = request.form.get('content')
        guest_nickname = request.form.get('guest_nickname')
        guest_password = request.form.get('guest_password')

        # 5. 유효성 검사
        if not all([title, content, guest_nickname, guest_password]):
            return Response('<script>alert("닉네임, 비밀번호, 제목, 내용을 모두 입력해주세요."); history.back();</script>')
        
        if len(guest_nickname) > 20:
            return Response('<script>alert("닉네임은 20자를 초과할 수 없습니다."); history.back();</script>')
        if len(guest_password) < 4:
            return Response('<script>alert("비밀번호는 4자 이상이어야 합니다."); history.back();</script>')

        plain_text_content = bleach.clean(content, tags=[], strip=True)
        if len(plain_text_content) > 5000 or len(title) > 50 or len(plain_text_content) == 0:
            return Response('<script>alert("제목(50자) 또는 내용(5000자) 길이를 확인해주세요."); history.back();</script>')

        # 6. 비밀번호 해시
        hashed_pw = bcrypt.generate_password_hash(guest_password).decode('utf-8')

        # 7. HTML 정제 (기존 post_write와 동일)
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
        allowed_css_properties = [
        'color', 'background-color', 'font-family', 'font-size', 
        'font-weight', 'text-align', 'text-decoration'
        ]
        css_sanitizer = CSSSanitizer(allowed_css_properties=allowed_css_properties)
        sanitized_content = bleach.clean(content, tags=allowed_tags, attributes=allowed_attrs, protocols=['http', 'https', 'data'], css_sanitizer=css_sanitizer)

        # 8. DB에 저장
        try:
            created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            query = """
                INSERT INTO posts
                (board_id, title, content, author, created_at, updated_at, view_count, comment_count, is_notice,
                 guest_nickname, guest_password)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
            """
            cursor.execute(query, (
                board_id, title, sanitized_content, GUEST_USER_ID, created_at, created_at,
                guest_nickname, hashed_pw
            ))
            conn.commit()

            post_id = cursor.lastrowid
            add_log('CREATE_GUEST_POST', session.get('guest_session_id', 'Guest'), f"'{title}' 글 작성(id : {post_id}) by {guest_nickname}")

            return redirect(url_for('post_list', board_id=board_id))
        except Exception as e:
            print(f"Database error: {e}")
            add_log('ERROR', session.get('guest_session_id', 'Guest'), f"Error saving guest post: {e}")
            return Response('<script>alert("게시글 저장 중 오류가 발생했습니다."); history.back();</script>')

    # GET 요청 시
    return render_template('post_write_guest.html', board_id=board_id, board_name=board['board_name'])

# Post List with Pagination
@app.route('/board/<int:board_id>', defaults={'page': 1})
@app.route('/board/<int:board_id>/<int:page>')
def post_list(board_id, page):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    posts_per_page = 20

    user_data = g.user

    is_bot = getattr(g, 'is_googlebot', False)

    try:
        # ▼▼▼ [수정] board_name 대신 is_public을 포함한 모든 정보를 가져옵니다. ▼▼▼
        cursor.execute("SELECT board_name, is_public FROM board WHERE board_id = ?", (board_id,))
        board = cursor.fetchone()

        if not board:
            return Response('<script>alert("존재하지 않는 게시판입니다."); history.back();</script>')

        # ▼▼▼ [추가] 공개 게시판이 아닐 경우에만 로그인을 확인합니다. ▼▼▼
        if not board['is_public'] and not user_data and not is_bot:
            return Response('<script> alert("로그인 사용자만 접근할 수 있습니다."); history.back(); </script>')
        # ▲▲▲ [추가] ▲▲▲

        # 2. 공지사항 목록 조회 (is_notice = 1) - 쿼리 수정
        notice_query = """
            SELECT
                p.id, p.title, u.nickname, p.updated_at, p.view_count, p.target_grade,
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
                p.id, p.title, p.comment_count, p.updated_at, p.view_count, u.nickname, p.target_grade,
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
        user_id_for_log = user_data['login_id'] if user_data else 'Googlebot'

        add_log('ERROR', user_id_for_log, f"Error fetching post list for board_id {board_id}, page {page}: {e}")
        return Response('<script>alert("게시글을 불러오는 중 오류가 발생했습니다."); history.back();</script>')

    return render_template('post_list.html', user=user_data,
                           board=board,
                           notices=notices,
                           posts=posts,
                           total_pages=total_pages,
                           current_page=page,
                           board_id=board_id,
                           GUEST_USER_ID=GUEST_USER_ID)

# Post Detail
@app.route('/post/<int:post_id>')
def post_detail(post_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    user_data = g.user
    
    is_bot = getattr(g, 'is_googlebot', False)

    try:
        # --- ▼▼▼ [수정] 게시글 정보 조회 시 board의 is_public 컬럼도 함께 조회합니다. ▼▼▼ ---
        query = """
            SELECT p.*, u.nickname, u.profile_image, b.board_name, b.is_public
            FROM posts p
            JOIN users u ON p.author = u.login_id
            JOIN board b ON p.board_id = b.board_id
            WHERE p.id = ?
        """
        # --- ▲▲▲ [수정] ---
        cursor.execute(query, (post_id,))
        post_data = cursor.fetchone()

        if not post_data:
            return Response('<script>alert("존재하지 않거나 삭제된 게시글입니다."); history.back();</script>')
    
        is_public_board = post_data['is_public'] == 1

        # ▼▼▼ [추가] 공개 게시판이 아닐 경우에만 로그인을 확인합니다. ▼▼▼
        if not post_data['is_public'] and not user_data and not is_bot:
            return Response('<script> alert("로그인 사용자만 접근할 수 있습니다."); history.back(); </script>')
        # ▲▲▲ [추가] ▲▲▲

        post = dict(post_data)

        if post['target_grade'] > 0:
            if not g.user:
                return Response('<script>alert("로그인이 필요한 글입니다."); location.href="/login";</script>')
            
            # 관리자(admin) 프리패스
            is_admin = g.user['role'] == 'admin'
            # 작성자 본인 프리패스
            is_author = g.user['login_id'] == post['author']
            
            if not is_admin and not is_author:
                try:
                    user_grade = int(g.user['gen'])
                except (ValueError, IndexError, KeyError):
                    user_grade = 0
                
                if user_grade != post['target_grade']:
                    return Response(f'<script>alert("{post["target_grade"]}기 학생만 조회할 수 있는 글입니다."); history.back();</script>')
        
        # --- ▼ [수정] 익명 게시판 처리를 위해 원본 작성자 ID와 게시판 ID 저장 ---
        post_author_id = post['author'] 
        board_id = post['board_id']

        if board_id == 3:
            post['nickname'] = '익명'
            post['profile_image'] = 'images/profiles/default_image.jpeg'
        # --- ▲ [수정] ---

        cursor.execute("SELECT * FROM polls WHERE post_id = ?", (post_id,))
        poll_row = cursor.fetchone()
        
        poll_data = None
        if poll_row:
            poll_data = dict(poll_row)
            poll_id = poll_data['id']
            
            # 옵션 목록 조회
            cursor.execute("SELECT * FROM poll_options WHERE poll_id = ?", (poll_id,))
            options_rows = cursor.fetchall()
            
            # 총 투표수 계산
            total_votes = sum(opt['vote_count'] for opt in options_rows)
            poll_data['total_votes'] = total_votes
            
            # 사용자 투표 여부 확인
            user_voted_option_id = None
            if g.user:
                cursor.execute("SELECT option_id FROM poll_history WHERE poll_id = ? AND user_id = ?", 
                               (poll_id, g.user['login_id']))
                history = cursor.fetchone()
                if history:
                    user_voted_option_id = history['option_id']
            
            # 옵션 데이터 가공 (비율 계산)
            options = []
            for opt in options_rows:
                opt_dict = dict(opt)
                if total_votes > 0:
                    opt_dict['percent'] = round((opt['vote_count'] / total_votes) * 100, 1)
                else:
                    opt_dict['percent'] = 0
                
                opt_dict['is_voted'] = (opt['id'] == user_voted_option_id)
                options.append(opt_dict)
                
            poll_data['options'] = options
            poll_data['user_voted_option_id'] = user_voted_option_id

        elif post['author'] == GUEST_USER_ID: # 게스트
            post['nickname'] = post['guest_nickname'] # 게스트 닉네임 사용
            post['profile_image'] = 'images/profiles/default_image.jpeg'

        post['created_at_datetime'] = datetime.datetime.strptime(post['created_at'], '%Y-%m-%d %H:%M:%S')
        post['updated_at_datetime'] = datetime.datetime.strptime(post['updated_at'], '%Y-%m-%d %H:%M:%S')

        user_id_for_reaction = None
        if g.user:
            user_id_for_reaction = g.user['login_id']
        elif is_public_board:
            if 'guest_session_id' not in session:
                session['guest_session_id'] = str(uuid.uuid4())
                user_id_for_reaction = session['guest_session_id']

        # ... (중략: 게시글 추천/조회수 로직은 동일) ...
        cursor.execute("SELECT reaction_type, COUNT(*) as count FROM reactions WHERE target_type = 'post' AND target_id = ? GROUP BY reaction_type", (post_id,))
        reactions = {r['reaction_type']: r['count'] for r in cursor.fetchall()}
        post['likes'] = reactions.get('like', 0)
        post['dislikes'] = reactions.get('dislike', 0)

        post['user_reaction'] = None

        if user_id_for_reaction:
            cursor.execute("SELECT reaction_type FROM reactions WHERE user_id = ? AND target_type = 'post' AND target_id = ?", (user_id_for_reaction, post_id,))
            user_reaction_row = cursor.fetchone()
            if user_reaction_row:
                post['user_reaction'] = user_reaction_row['reaction_type']

        viewed_posts = session.get('viewed_posts', [])
        if post_id not in viewed_posts:
            cursor.execute("UPDATE posts SET view_count = view_count + 1 WHERE id = ?", (post_id,))
            conn.commit()
            viewed_posts.append(post_id)
            session['viewed_posts'] = viewed_posts

        # --- ▼ [수정] 댓글 로직 수정 (정렬 순서 변경 및 익명 처리) ---
        comment_query = """
            SELECT c.*, u.nickname, u.profile_image
            FROM comments c
            JOIN users u ON c.author = u.login_id
            WHERE c.post_id = ?
            ORDER BY c.created_at ASC
        """
        cursor.execute(comment_query, (post_id,))
        all_comments = cursor.fetchall()
        
        comments_dict = {}

        etacon_codes = {c['etacon_code'] for c in all_comments if c['etacon_code']}
        etacon_map = {}

        if etacon_codes:
            placeholders = ','.join(['?'] * len(etacon_codes))
            # etacons 테이블에서 code와 image_path를 조회
            cursor.execute(f"SELECT code, image_path FROM etacons WHERE code IN ({placeholders})", list(etacon_codes))
            for code, path in cursor.fetchall():
                etacon_map[code] = path
        
        # 1. 모든 댓글을 딕셔너리로 변환하고, 'replies' 리스트와 reaction 정보를 초기화합니다.
        for comment_row in all_comments:
            comment = dict(comment_row)
            comment['replies'] = []

            if comment['etacon_code'] and comment['etacon_code'] in etacon_map:
                comment['etacon_path'] = etacon_map[comment['etacon_code']]
            else:
                comment['etacon_path'] = None

            if board_id == 3:
                seq = comment.get('anonymous_seq', 0)
                
                if comment['author'] == post_author_id:
                    comment['nickname'] = '익명 (작성자)'
                else:
                    if seq > 0:
                        comment['nickname'] = f'익명{seq}'
                    else:
                        comment['nickname'] = '익명'
                
                comment['profile_image'] = 'images/profiles/default_image.jpeg'

            cursor.execute("SELECT reaction_type, COUNT(*) as count FROM reactions WHERE target_type = 'comment' AND target_id = ? GROUP BY reaction_type", (comment['id'],))
            comment_reactions = {r['reaction_type']: r['count'] for r in cursor.fetchall()}
            comment['likes'] = comment_reactions.get('like', 0)
            comment['dislikes'] = comment_reactions.get('dislike', 0)
            
            if user_id_for_reaction:
                cursor.execute("SELECT reaction_type FROM reactions WHERE user_id = ? AND target_type = 'comment' AND target_id = ?", (user_id_for_reaction, comment['id']))
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

        comments_tree.reverse()

    except Exception as e:
        print(f"Error fetching post detail: {e}")
        user_id_for_log = user_data['login_id'] if user_data else 'Googlebot'
        add_log('ERROR', user_id_for_log, f"Error fetching post detail for post_id {post_id}: {e}")
        return Response('<script>alert("게시글을 불러오는 중 오류가 발생했습니다."); history.back();</script>')

    return render_template('post_detail.html', user=user_data, post=post, comments=comments_tree, poll=poll_data, GUEST_USER_ID=GUEST_USER_ID)

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
        
        if len(title) > 50:
            return Response('<script>alert("제목은 50자를 초과할 수 없습니다."); history.back();</script>')

        is_notice = 0
        if g.user and g.user['role'] == 'admin':
            is_notice = 1 if request.form.get('is_notice') == 'on' else 0
        
        # --- ▼ [수정] 서버 사이드 유효성 검사 강화 ---
        if not title or not content or not board_id:
            return Response('<script>alert("게시판, 제목, 내용을 모두 입력해주세요."); history.back();</script>')
        
        is_valid_size, img_idx = check_content_image_size(content, 15)
        if not is_valid_size:
            return Response(f'<script>alert("{img_idx}번째 이미지의 용량이 15MB를 초과합니다.\n"); history.back();</script>')
        
        # board_id가 실제 DB에 존재하는지 확인
        cursor.execute("SELECT COUNT(*) FROM board WHERE board_id = ?", (board_id,))
        if cursor.fetchone()[0] == 0:
            return Response('<script>alert("존재하지 않는 게시판입니다."); history.back();</script>')

        plain_text_content = bleach.clean(content, tags=[], strip=True)
        if len(plain_text_content) > 5000:
            return Response('<script>alert("글자 수는 5,000자를 초과할 수 없습니다."); history.back();</script>')
        if len(title) > 50:
            return Response('<script>alert("제목은 50자를 초과할 수 없습니다."); history.back();</script>')
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

        final_content = sanitized_content

        updated_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        query = "UPDATE posts SET board_id = ?, title = ?, content = ?, updated_at = ?, is_notice = ? WHERE id = ?"
        cursor.execute(query, (board_id, title, final_content, updated_at, is_notice, post_id))

        add_log('EDIT_POST', session['user_id'], f"게시글 (id : {post_id})를 수정했습니다. 제목 : {title} 내용 : {final_content}")

        conn.commit()

        return redirect(url_for('post_detail', post_id=post_id))
    else: # GET 요청
        cursor.execute("SELECT board_id, board_name FROM board ORDER BY board_id")
        boards = cursor.fetchall()
        
        # --- [누락된 코드 추가] ---
        # 수정 폼 진입 시, 텍스트 코드를 이미지로 변환하여 에디터에 표시
        post_dict = dict(post)
        # -----------------------

        # post=post 대신 post=post_dict 전달
        return render_template('post_edit.html', post=post_dict, boards=boards)

# Post Delete
@app.route('/post-delete/<int:post_id>', methods=['POST'])
@login_required
@check_banned
def post_delete(post_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row  # row_factory 설정 추가
    cursor = conn.cursor()

    cursor.execute("SELECT author, board_id, title FROM posts WHERE id = ?", (post_id,))
    post = cursor.fetchone()

    if not post:
        return Response('<script>alert("존재하지 않거나 삭제된 게시글입니다."); history.back();</script>')

    board_id = post['board_id']

    # 관리자는 다른 사람의 글도 삭제할 수 있도록 수정 (선택 사항)
    if post['author'] != session['user_id'] and (not g.user or g.user['role'] != 'admin'):
        return Response('<script>alert("삭제 권한이 없습니다."); history.back();</script>')

    try:
        # --- 👇 로직 수정 시작 ---
        cursor.execute("SELECT id FROM polls WHERE post_id = ?", (post_id,))
        poll = cursor.fetchone()
        
        if poll:
            poll_id = poll['id']
            cursor.execute("DELETE FROM poll_history WHERE poll_id = ?", (poll_id,))
            cursor.execute("DELETE FROM poll_options WHERE poll_id = ?", (poll_id,))
            cursor.execute("DELETE FROM polls WHERE id = ?", (poll_id,))

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
        update_exp_level(post['author'], -50, False)

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
@check_banned
def add_comment(post_id):
    content = request.form.get('comment_content')
    parent_comment_id = request.form.get('parent_comment_id', None)

    if not content or not content.strip():
        return Response('<script>alert("댓글 내용을 입력해주세요."); history.back();</script>')

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # [수정] 원본 게시글의 is_public과 author 정보 조회
        cursor.execute("SELECT author, board_id FROM posts WHERE id = ?", (post_id,))
        post = cursor.fetchone()
        if not post:
            return Response('<script>alert("원본 게시글이 존재하지 않습니다."); history.back();</script>')
            
        cursor.execute("SELECT is_public FROM board WHERE board_id = ?", (post['board_id'],))
        board = cursor.fetchone()
        is_public_board = board['is_public'] == 1

        author_id = None
        guest_nickname = None
        hashed_pw = None
        log_user_id = 'Guest'

        if g.user:
            # 1. 로그인 사용자
            author_id = g.user['login_id']
            log_user_id = g.user['login_id']
        elif is_public_board:
            # 2. 비회원 + 공개 게시판
            guest_nickname = request.form.get('guest_nickname')
            guest_password = request.form.get('guest_password')
            
            if not guest_nickname or not guest_password:
                return Response('<script>alert("비회원 댓글은 닉네임과 비밀번호가 필요합니다."); history.back();</script>')
            if len(guest_password) < 4:
                return Response('<script>alert("비밀번호는 4자 이상이어야 합니다."); history.back();</script>')

            author_id = GUEST_USER_ID
            hashed_pw = bcrypt.generate_password_hash(guest_password).decode('utf-8')
            log_user_id = session.get('guest_session_id', 'Guest')
        else:
            # 3. 비회원 + 비공개 게시판
            return Response('<script>alert("로그인이 필요한 게시판입니다."); history.back();</script>')


        created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sanitized_content = bleach.clean(content)

        final_content = sanitized_content

        anonymous_seq = 0

        if post['board_id'] == 3:
            if author_id == post['author']:
                anonymous_seq = 0
            else:
                cursor.execute(
                    "SELECT anonymous_seq FROM comments WHERE post_id = ? AND author = ? LIMIT 1", 
                    (post_id, author_id)
                )
                existing_seq_row = cursor.fetchone()

                if existing_seq_row:
                    anonymous_seq = existing_seq_row[0]
                else:
                    cursor.execute("SELECT MAX(anonymous_seq) FROM comments WHERE post_id = ?", (post_id,))
                    max_seq = cursor.fetchone()[0]
                    anonymous_seq = (max_seq if max_seq else 0) + 1
        
        query = """
            INSERT INTO comments 
            (post_id, author, content, created_at, updated_at, parent_comment_id,
             guest_nickname, guest_password, anonymous_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        if parent_comment_id:
            # --- 답글 로직 ---
            cursor.execute("SELECT parent_comment_id, author FROM comments WHERE id = ?", (parent_comment_id,))
            parent_comment = cursor.fetchone()
            
            if not parent_comment:
                return Response('<script>alert("답글을 작성할 원본 댓글이 존재하지 않습니다."); history.back();</script>')
            if parent_comment[0] is not None:
                return Response('<script>alert("대댓글에는 답글을 작성할 수 없습니다."); history.back();</script>')
            
            cursor.execute(query, (
                post_id, author_id, final_content, created_at, created_at, parent_comment_id,
                guest_nickname, hashed_pw, anonymous_seq
            ))

            # [수정] guest_nickname이 없는 (로그인한) 사용자에게만 알림
            if parent_comment['author'] != GUEST_USER_ID:
                create_notification(
                    recipient_id=parent_comment['author'],
                    actor_id=author_id, # 알림 행위자는 게스트일 수도, 회원일 수도 있음
                    action='reply',
                    target_type='comment',
                    target_id=parent_comment_id, 
                    post_id=post_id
                )
        else:
            # --- 새 댓글 로직 ---
            cursor.execute(query, (
                post_id, author_id, final_content, created_at, created_at, None,
                guest_nickname, hashed_pw, anonymous_seq
            ))
            
            # [수정] guest_nickname이 없는 (로그인한) 사용자에게만 알림
            if post['author'] != GUEST_USER_ID:
                create_notification(
                    recipient_id=post['author'],
                    actor_id=author_id,
                    action='comment',
                    target_type='post',
                    target_id=post_id,
                    post_id=post_id
                )

        # (게시글/사용자 댓글 수 업데이트)
        cursor.execute("UPDATE posts SET comment_count = comment_count + 1 WHERE id = ?", (post_id,))
        
        if g.user: # 로그인한 사용자만 카운트 및 경험치
            cursor.execute("UPDATE users SET comment_count = comment_count + 1 WHERE login_id = ?", (author_id,))
            update_exp_level(author_id, 10)

        log_details = f"게시글(id:{post_id})에 댓글 작성. 내용:{final_content}"
        if parent_comment_id:
            log_details = f"댓글(id:{parent_comment_id})에 답글 작성. 내용:{final_content}"
        add_log('ADD_COMMENT', log_user_id, log_details)

        conn.commit()

    except Exception as e:
        print(f"Database error while adding comment: {e}")
        conn.rollback()
        return Response('<script>alert("댓글 작성 중 오류가 발생했습니다."); history.back();</script>')

    return redirect(url_for('post_detail', post_id=post_id))

@app.route('/api/comment/etacon', methods=['POST'])
@check_banned
def add_etacon_comment():
    data = request.get_json()
    post_id = data.get('post_id')
    etacon_code = data.get('etacon_code')
    parent_comment_id = data.get('parent_comment_id')
    
    # 게스트 정보 (로그인 안 한 경우)
    guest_nickname = data.get('guest_nickname')
    guest_password = data.get('guest_password')

    if not post_id or not etacon_code:
        return jsonify({'status': 'error', 'message': '잘못된 요청입니다.'}), 400

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # 1. 게시글 정보 확인
        cursor.execute("SELECT author, board_id FROM posts WHERE id = ?", (post_id,))
        post = cursor.fetchone()
        if not post:
            return jsonify({'status': 'error', 'message': '게시글이 존재하지 않습니다.'}), 404
            
        cursor.execute("SELECT is_public FROM board WHERE board_id = ?", (post['board_id'],))
        board = cursor.fetchone()
        is_public_board = board['is_public'] == 1

        # 2. 작성자 정보 설정
        author_id = None
        hashed_pw = None
        log_user_id = 'Guest'

        if g.user:
            author_id = g.user['login_id']
            log_user_id = g.user['login_id']
            
            # [보유권 검증] 로그인 유저는 보유한 패키지인지 확인
            pack_id = int(etacon_code.split('_')[0].replace('~', ''))
            cursor.execute("SELECT 1 FROM user_etacons WHERE user_id = ? AND pack_id = ?", (author_id, pack_id))
            if not cursor.fetchone():
                return jsonify({'status': 'error', 'message': '보유하지 않은 인곽콘입니다.'}), 403

        elif is_public_board:
            # 비회원 검증
            if not guest_nickname or not guest_password:
                return jsonify({'status': 'error', 'message': '비회원은 닉네임과 비밀번호 입력 후 인곽콘을 선택해주세요.'}), 400
            if len(guest_password) < 4:
                return jsonify({'status': 'error', 'message': '비밀번호는 4자 이상이어야 합니다.'}), 400
            
            author_id = GUEST_USER_ID
            hashed_pw = bcrypt.generate_password_hash(guest_password).decode('utf-8')
            log_user_id = session.get('guest_session_id', 'Guest')
        else:
            return jsonify({'status': 'error', 'message': '로그인이 필요한 게시판입니다.'}), 403

        # 3. 익명 순서 처리 (익명게시판인 경우)
        anonymous_seq = 0
        if post['board_id'] == 3:
            if author_id == post['author']:
                anonymous_seq = 0
            else:
                cursor.execute("SELECT anonymous_seq FROM comments WHERE post_id = ? AND author = ? LIMIT 1", (post_id, author_id))
                row = cursor.fetchone()
                if row:
                    anonymous_seq = row[0]
                else:
                    cursor.execute("SELECT MAX(anonymous_seq) FROM comments WHERE post_id = ?", (post_id,))
                    max_seq = cursor.fetchone()[0]
                    anonymous_seq = (max_seq if max_seq else 0) + 1

        # 4. DB 저장
        created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        query = """
            INSERT INTO comments 
            (post_id, author, content, etacon_code, created_at, updated_at, parent_comment_id,
             guest_nickname, guest_password, anonymous_seq)
            VALUES (?, ?, '인곽콘', ?, ?, ?, ?, ?, ?, ?)
        """
        cursor.execute(query, (
            post_id, author_id, etacon_code, created_at, created_at, parent_comment_id,
            guest_nickname, hashed_pw, anonymous_seq
        ))
        comment_id = cursor.lastrowid # 생성된 댓글 ID

        # 5. 알림 전송 (자신이 쓴 글/댓글 제외)
        recipient_id = None
        action = 'comment'
        target_id = post_id # 알림 클릭 시 이동할 ID

        if parent_comment_id:
            cursor.execute("SELECT author FROM comments WHERE id = ?", (parent_comment_id,))
            parent = cursor.fetchone()
            if parent and parent['author'] != GUEST_USER_ID:
                recipient_id = parent['author']
                action = 'reply'
                target_id = parent_comment_id
        elif post['author'] != GUEST_USER_ID:
            recipient_id = post['author']
        
        if recipient_id:
            create_notification(recipient_id, author_id, action, 'post', target_id, post_id)

        # 6. 카운트 및 경험치
        cursor.execute("UPDATE posts SET comment_count = comment_count + 1 WHERE id = ?", (post_id,))
        if g.user:
            cursor.execute("UPDATE users SET comment_count = comment_count + 1 WHERE login_id = ?", (author_id,))
            update_exp_level(author_id, 10)

        add_log('ADD_ETACON', log_user_id, f"게시글(id:{post_id})에 인곽콘 댓글 작성.")
        conn.commit()
        
        return jsonify({'status': 'success', 'message': '인곽콘이 등록되었습니다.'})

    except Exception as e:
        conn.rollback()
        print(f"Error adding etacon comment: {e}")
        return jsonify({'status': 'error', 'message': '서버 오류가 발생했습니다.'}), 500

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
        # 3. 삭제할 대댓글(답글) 조회
        cursor.execute("SELECT id, author FROM comments WHERE parent_comment_id = ?", (comment_id,))
        replies = cursor.fetchall()

        # 삭제 대상 ID 목록 생성 (본문 + 대댓글)
        target_ids = [comment_id] + [r['id'] for r in replies]
        
        # SQL IN 절에 사용할 플레이스홀더 생성 (?, ?, ...)
        placeholders = ','.join(['?'] * len(target_ids))

        # 4. 연관된 Reaction(좋아요/싫어요) 일괄 삭제
        cursor.execute(f"DELETE FROM reactions WHERE target_type = 'comment' AND target_id IN ({placeholders})", target_ids)

        # 5. 사용자 스탯 업데이트 (삭제 대상 작성자들의 댓글 수 및 경험치 차감)
        # 5-1. 본 댓글 작성자 차감
        cursor.execute("UPDATE users SET comment_count = comment_count - 1 WHERE login_id = ?", (comment['author'],))
        update_exp_level(comment['author'], -10) # 헬퍼 함수 사용

        # 5-2. 대댓글 작성자들 차감
        for reply in replies:
            cursor.execute("UPDATE users SET comment_count = comment_count - 1 WHERE login_id = ?", (reply['author'],))
            update_exp_level(reply['author'], -10)

        # 6. 댓글 데이터 일괄 삭제
        cursor.execute(f"DELETE FROM comments WHERE id IN ({placeholders})", target_ids)

        # 7. 게시글의 전체 댓글 수 차감 (삭제된 총 개수만큼)
        total_deleted_count = len(target_ids)
        cursor.execute("UPDATE posts SET comment_count = comment_count - ? WHERE id = ?", (total_deleted_count, comment['post_id']))
        
        add_log('DELETE_COMMENT', session['user_id'], f"댓글 (id : {comment_id}) 및 대댓글 {len(replies)}개를 삭제했습니다. 내용 : {comment['content']}")
        
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
    cursor.execute("SELECT author, post_id, content FROM comments WHERE id = ?", (comment_id,))
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

        final_content = sanitized_content
        
        query = "UPDATE comments SET content = ?, updated_at = ? WHERE id = ?"
        cursor.execute(query, (final_content, updated_at, comment_id))
        add_log('EDIT_COMMENT', session['user_id'], f"댓글 (id : {comment_id})를 수정했습니다. 원본 : {comment['content']}, 내용 : {final_content}")
        conn.commit()

    except Exception as e:
        print(f"Database error while editing comment: {e}")
        add_log('ERROR', session['user_id'], f"Error editing comment id {comment_id}: {e}")
        conn.rollback()
        return Response('<script>alert("댓글 수정 중 오류가 발생했습니다."); history.back();</script>')

    return redirect(url_for('post_detail', post_id=comment['post_id']))

# React (Like/Dislike) for Post and Comment
@app.route('/react/<target_type>/<int:target_id>', methods=['POST'])
@check_banned
def react(target_type, target_id):
    if not g.user:
        return jsonify({'status': 'error', 'message': '비회원 유저는 게시글에 반응할 수 없습니다.'}), 403

    reaction_type = request.form.get('reaction_type')
    user_id = g.user['login_id']
    
    if target_type not in ['post', 'comment'] or reaction_type not in ['like', 'dislike']:
        return jsonify({'status': 'error', 'message': '잘못된 접근입니다.'}), 400

    conn = get_db()
    conn.row_factory = sqlite3.Row # .Row 추가
    cursor = conn.cursor()

    try:
        is_public_board = False
        if target_type == 'post':
            cursor.execute("SELECT b.is_public FROM posts p JOIN board b ON p.board_id = b.board_id WHERE p.id = ?", (target_id,))
            board = cursor.fetchone()
            if board: is_public_board = board['is_public'] == 1
        
        elif target_type == 'comment':
            cursor.execute("""
                SELECT b.is_public FROM comments c 
                JOIN posts p ON c.post_id = p.id 
                JOIN board b ON p.board_id = b.board_id 
                WHERE c.id = ?
            """, (target_id,))
            board = cursor.fetchone()
            if board: is_public_board = board['is_public'] == 1

        user_id_for_reaction = None
        if g.user:
            user_id_for_reaction = g.user['login_id']
        elif is_public_board:
            if 'guest_session_id' not in session:
                session['guest_session_id'] = str(uuid.uuid4())
            user_id_for_reaction = session['guest_session_id']
        else:
            return jsonify({'status': 'error', 'message': '로그인이 필요합니다.'}), 403
        
        if not user_id_for_reaction:
             return jsonify({'status': 'error', 'message': '세션 오류. 다시 시도해주세요.'}), 500

        # --- ▼ IDOR 방어 로직 추가 ▼ ---
        table_name = ''
        if target_type == 'post':
            table_name = 'posts'
        elif target_type == 'comment':
            table_name = 'comments'
        else:
            return jsonify({'status': 'error', 'message': '잘못된 대상 타입입니다.'}), 400

        cursor.execute(f"SELECT id FROM {table_name} WHERE id = ?", (target_id,))
        target_obj = cursor.fetchone()
        if not target_obj:
            return jsonify({'status': 'error', 'message': '존재하지 않는 대상입니다.'}), 404

        cursor.execute("SELECT reaction_type FROM reactions WHERE user_id = ? AND target_type = ? AND target_id = ?",
                       (user_id_for_reaction, target_type, target_id))
        existing_reaction = cursor.fetchone()

        if existing_reaction:
            if existing_reaction['reaction_type'] == reaction_type:
                cursor.execute("DELETE FROM reactions WHERE user_id = ? AND target_type = ? AND target_id = ?",
                               (user_id_for_reaction, target_type, target_id))
                add_log('CANCEL_REACTION', user_id_for_reaction, f"{target_type} (id: {target_id})에 대한 '{reaction_type}' 반응을 취소했습니다.")
            else:
                cursor.execute("UPDATE reactions SET reaction_type = ? WHERE user_id = ? AND target_type = ? AND target_id = ?",
                               (reaction_type, user_id_for_reaction, target_type, target_id))
                add_log('CHANGE_REACTION', user_id_for_reaction, f"{target_type} (id: {target_id})에 대한 반응을 '{existing_reaction['reaction_type']}'에서 '{reaction_type}'(으)로 변경했습니다.")
        else:
            cursor.execute("INSERT INTO reactions (user_id, target_type, target_id, reaction_type, created_at) VALUES (?, ?, ?, ?, ?)",
                           (user_id_for_reaction, target_type, target_id, reaction_type, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            add_log('ADD_REACTION', user_id_for_reaction, f"{target_type} (id: {target_id})에 '{reaction_type}' 반응을 추가했습니다.")

        conn.commit()

        # --- 👇 HOT 게시물 알림 로직 시작 ---
        # 1. '게시글'에 '좋아요'를 눌렀을 경우에만 확인
        if g.user and target_type == 'post' and reaction_type == 'like':
            # 2. 현재 '좋아요' 개수를 다시 계산
            cursor.execute("SELECT COUNT(*) FROM reactions WHERE target_type = 'post' AND target_id = ? AND reaction_type = 'like'", (target_id,))
            likes = cursor.fetchone()[0]

            # 3. '좋아요'가 정확히 10개가 되었는지 확인
            if likes == 10:
                # 4. 이 게시글에 대해 'hot_post' 알림이 이미 보내졌는지 확인 (중복 방지)
                cursor.execute("SELECT COUNT(*) FROM notifications WHERE action = 'hot_post' AND target_type = 'post' AND target_id = ?", (target_id,))
                already_notified = cursor.fetchone()[0]

                # 24시간 이내에 작성된 게시글에 대해서만 알림
                cursor.execute("SELECT created_at FROM posts WHERE id = ?", (target_id,))
                post = cursor.fetchone()
                if post:
                    post_created_at = datetime.datetime.strptime(post['created_at'], '%Y-%m-%d %H:%M:%S')
                    time_diff = datetime.datetime.now() - post_created_at
                    if time_diff.total_seconds() > 86400: # 24시간 = 86400초
                        already_notified = 1 # 24시간 초과 시 알림 보내지 않음

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
                       (user_id_for_reaction, target_type, target_id))
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
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

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
            if old_image_path and 'default_image.jpeg' not in old_image_path:
                try:
                    # 'static'을 경로에 포함시켜야 합니다.
                    full_old_path = os.path.join('static', old_image_path)
                    if os.path.exists(full_old_path):
                        os.remove(full_old_path)
                except Exception as e:
                    print(f"Warning: 이전 프로필 이미지 삭제 실패: {e}")
                    add_log('WARNING', session['user_id'], f"이전 프로필 이미지 삭제 실패: {e}")

        ext = file.filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4()}.{ext}"
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
        WHERE p.author = ? AND p.board_id != 3
        ORDER BY p.updated_at DESC
    """
    cursor.execute(posts_query, (login_id,))
    user_posts = cursor.fetchall()

    # 사용자의 댓글 목록 조회
    comments_query = """
        SELECT c.content, c.post_id, c.updated_at, p.title AS post_title
        FROM comments c JOIN posts p ON c.post_id = p.id
        WHERE c.author = ? AND p.board_id != 3
        ORDER BY c.updated_at DESC
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

    if club1 and club1 not in ACADEMIC_CLUBS:
        return Response('<script>alert("유효하지 않은 동아리 이름입니다."); history.back();</script>')
    if club2 and club2 not in HOBBY_CLUBS:
        return Response('<script>alert("유효하지 않은 동아리 이름입니다."); history.back();</script>')
    if club3 and club3 not in CAREER_CLUBS:
        return Response('<script>alert("유효하지 않은 동아리 이름입니다."); history.back();</script>')

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
        cursor.execute("UPDATE users SET pw = ?, autologin_token = NULL WHERE login_id = ?", (hashed_pw, session['user_id']))

        conn.commit()

        add_log('CHANGE_PASSWORD', session['user_id'], "비밀번호를 변경했습니다.")
        
        resp = make_response('<script>alert("비밀번호가 성공적으로 변경되었습니다. 다시 로그인해주세요."); window.location.href = "/logout";</script>')
        resp.set_cookie('remember_token', '', max_age=0)
        return resp

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
        old_image_path = user['profile_image']
        print(f"Old image path: {old_image_path}")  # 디버그 출력

        if old_image_path and 'default_image' not in old_image_path:
            try:
                # 'static/'을 포함한 전체 경로 생성
                full_path_to_delete = os.path.join('static', old_image_path)
                print(f"Full path to delete: {full_path_to_delete}")  # 디버그 출력
                if os.path.exists(full_path_to_delete):
                    os.remove(full_path_to_delete)
            except Exception as e:
                # 파일 삭제에 실패해도 전체 프로세스에 영향을 주지 않도록 로그만 남김
                print(f"Warning: 프로필 이미지 파일 삭제 실패: {e}")
                add_log('WARNING', original_login_id, f"프로필 이미지 파일 삭제 실패({old_image_path}): {e}")
        # 1. 재가입이 가능하도록 고유 정보를 변경할 값을 준비합니다.
        timestamp_suffix = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        original_login_id = session['user_id']
        
        deleted_login_id = f"deleted_{original_login_id}_{timestamp_suffix}"
        deleted_hakbun = f"deleted_{user['hakbun']}_{timestamp_suffix}"
        deleted_nickname = f"탈퇴한 사용자_{str(uuid.uuid4())[:8]}"

        # 2. 탈퇴할 사용자가 작성한 게시글의 author를 새로운 deleted_login_id로 업데이트합니다.
        cursor.execute("UPDATE posts SET author = ? WHERE author = ?", (deleted_login_id, original_login_id))

        # 3. 탈퇴할 사용자가 작성한 댓글의 author를 새로운 deleted_login_id로 업데이트합니다.
        cursor.execute("UPDATE comments SET author = ? WHERE author = ?", (deleted_login_id, original_login_id))
        
        # 4. 사용자 정보 비활성화 (Soft Delete)
        cursor.execute("""
            UPDATE users 
            SET 
                login_id = ?,
                hakbun = ?,
                nickname = ?, 
                pw = ?, 
                profile_image = 'images/profiles/default_image.jpeg',
                profile_message = '탈퇴한 사용자의 프로필입니다.',
                clubhak = NULL,
                clubchi = NULL,
                clubjin = NULL,
                profile_public = 0,
                autologin_token = NULL,
                status = 'deleted'
            WHERE login_id = ?
        """, (deleted_login_id, deleted_hakbun, deleted_nickname, str(uuid.uuid4()), original_login_id))

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
    
def clean_fts_query(text):
    """
    FTS5 검색 쿼리에서 문법 오류를 일으킬 수 있는 특수문자를 제거합니다.
    """
    # 1. 알파벳, 숫자, 한글, 공백만 남기고 모두 제거 (정규표현식 사용)
    # import re 가 상단에 되어있어야 합니다. (이미 되어 있습니다)
    cleaned_text = re.sub(r'[^\w\s]', '', text)
    
    # 2. 양쪽 공백 제거
    return cleaned_text.strip()

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
    
    # FTS 검색어 형식
    search_term_fts = ' AND '.join(query.split())
    # LIKE 검색어 형식
    search_term_like = f'%{query}%'

    cleaned_query = clean_fts_query(query)
    search_term_fts = ' AND '.join(cleaned_query.split())

    try:
        # [수정] 닉네임 검색(u.nickname)과 게스트 닉네임 검색(p.guest_nickname)을 모두 포함
        count_query = """
            SELECT COUNT(DISTINCT p.id)
            FROM posts p
            LEFT JOIN users u ON p.author = u.login_id
            WHERE 
                (p.id IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?))
                OR (p.board_id != 3 AND u.nickname LIKE ?) 
                OR (p.guest_nickname LIKE ?)
        """
        cursor.execute(count_query, (search_term_fts, search_term_like, search_term_like))
        total_posts = cursor.fetchone()[0]
        total_pages = math.ceil(total_posts / posts_per_page) if total_posts > 0 else 1

        # 2. 현재 페이지에 해당하는 검색 결과 목록 조회
        offset = (page - 1) * posts_per_page
        search_query = """
            SELECT
                p.id, p.title, p.comment_count, p.updated_at, p.view_count,
                p.author, p.guest_nickname, p.board_id,
                CASE WHEN p.board_id = 3 THEN '익명' ELSE u.nickname END as nickname,
                b.board_name,
                SUM(CASE WHEN r.reaction_type = 'like' THEN 1 WHEN r.reaction_type = 'dislike' THEN -1 ELSE 0 END) as net_reactions
            FROM posts p
            JOIN board b ON p.board_id = b.board_id
            LEFT JOIN users u ON p.author = u.login_id
            LEFT JOIN reactions r ON r.target_id = p.id AND r.target_type = 'post'
            WHERE 
                (
                    (p.id IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?))
                    OR (p.board_id != 3 AND u.nickname LIKE ?)
                    OR (p.guest_nickname LIKE ?)
                )
              AND (u.status = 'active' OR u.status IS NULL OR u.status = 'deleted')
            GROUP BY p.id
            ORDER BY p.updated_at DESC
            LIMIT ? OFFSET ?
        """
        cursor.execute(search_query, (search_term_fts, search_term_like, search_term_like, posts_per_page, offset))
        posts = cursor.fetchall()

    except sqlite3.OperationalError as e:
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
                           current_page=page, 
                           user=g.user,
                           GUEST_USER_ID=GUEST_USER_ID) # [추가] GUEST_USER_ID 전달

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
    # 게시글의 board_id를 함께 조회하여 익명게시판 여부 확인
    query = """
        SELECT n.*, u.nickname as actor_nickname, p.board_id
        FROM notifications n
        JOIN users u ON n.actor_id = u.login_id
        LEFT JOIN posts p ON n.post_id = p.id
        WHERE n.recipient_id = ?
        ORDER BY n.created_at DESC
        LIMIT 10
    """
    cursor.execute(query, (g.user['login_id'],))
    notifications = []
    for row in cursor.fetchall():
        notification = dict(row)
        # 익명게시판(board_id=3)인 경우 닉네임을 '익명'으로 마스킹
        if notification.get('board_id') == 3:
            notification['actor_nickname'] = '익명'
        notifications.append(notification)
    return jsonify(notifications)

@app.route('/notifications/read/<int:notification_id>', methods=['POST'])
@login_required
def read_notification(notification_id):
    conn = get_db()
    cursor = conn.cursor()
    # 본인의 알림이 맞는지 확인 후 삭제 처리 (한번 본 알림은 사라지게 함)
    cursor.execute("DELETE FROM notifications WHERE id = ? AND recipient_id = ?", (notification_id, g.user['login_id']))
    conn.commit()
    return jsonify({'status': 'success'})

@app.errorhandler(413)
def request_entity_too_large(error):
    return Response('<script>alert("업로드할 수 있는 파일의 최대 크기는 5MB입니다."); history.back();</script>'), 413

@app.errorhandler(404)
def page_not_found(error):
    user_data = g.user if 'user' in g else None
    return render_template('404.html', user=user_data), 404

@app.route('/admin/check-author', methods=['POST'])
@login_required
@admin_required
def check_author_info():
    data = request.get_json()
    target_type = data.get('target_type')
    target_id = data.get('target_id')

    if not target_type or not target_id or target_type not in ['post', 'comment']:
        return jsonify({'status': 'error', 'message': '잘못된 요청입니다.'}), 400

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    author_login_id = None
    try:
        if target_type == 'post':
            cursor.execute("SELECT author FROM posts WHERE id = ?", (target_id,))
        elif target_type == 'comment':
            cursor.execute("SELECT author FROM comments WHERE id = ?", (target_id,))

        result = cursor.fetchone()
        if result:
            author_login_id = result['author']

        if author_login_id:
            # 'deleted_'로 시작하는 탈퇴한 사용자인지 확인
            if author_login_id.startswith('deleted_'):
                 return jsonify({
                    'status': 'success', 
                    'name': '탈퇴한 사용자', 
                    'hakbun': 'N/A'
                })

            cursor.execute("SELECT name, hakbun FROM users WHERE login_id = ?", (author_login_id,))
            user_info = cursor.fetchone()
            if user_info:
                return jsonify({
                    'status': 'success', 
                    'name': user_info['name'], 
                    'hakbun': user_info['hakbun']
                })

        return jsonify({'status': 'error', 'message': '작성자 정보를 찾을 수 없습니다.'}), 404

    except Exception as e:
        add_log('ERROR', g.user['login_id'], f"Error checking author info: {e}")
        return jsonify({'status': 'error', 'message': '서버 오류가 발생했습니다.'}), 500
    
@app.route('/guest-auth/<action>/<target_type>/<int:target_id>', methods=['GET', 'POST'])
def guest_auth(action, target_type, target_id):
    """
    비회원 글/댓글 수정 및 삭제를 위한 비밀번호 인증 페이지
    action: 'edit' 또는 'delete'
    target_type: 'post' 또는 'comment'
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. 유효성 검사
    if action not in ['edit', 'delete'] or target_type not in ['post', 'comment']:
        return render_template('404.html', user=g.user), 404

    # 2. 대상 객체(게시글/댓글) 정보 조회
    table_name = 'posts' if target_type == 'post' else 'comments'
    
    # post_id는 댓글에서 원본 게시글로 돌아가기 위해 필요
    post_id_column = "id as post_id" if target_type == 'post' else "post_id"
    cursor.execute(f"SELECT author, guest_password, {post_id_column} FROM {table_name} WHERE id = ?", (target_id,))
    target_obj = cursor.fetchone()

    if not target_obj:
        return Response('<script>alert("대상이 존재하지 않습니다."); history.back();</script>')

    # 3. 게스트 객체 여부 확인
    if target_obj['author'] != GUEST_USER_ID or not target_obj['guest_password']:
        return Response('<script>alert("비회원 게시글/댓글이 아니거나, 비밀번호가 설정되지 않았습니다."); history.back();</script>')

    hashed_pw = target_obj['guest_password']

    # 4. (POST) 비밀번호 제출 처리
    if request.method == 'POST':
        password = request.form.get('password')
        if not password:
            return Response('<script>alert("비밀번호를 입력하세요."); history.back();</script>')

        # 5. 비밀번호 확인
        if bcrypt.check_password_hash(hashed_pw, password):
            # 비밀번호 일치!
            # 세션에 임시 인증 토큰 저장
            session[f'guest_auth_{target_type}_{target_id}'] = True 
            
            if action == 'edit':
                if target_type == 'post':
                    return redirect(url_for('post_edit_guest', post_id=target_id))
                else:
                    return redirect(url_for('comment_edit_guest', comment_id=target_id))
            
            elif action == 'delete':
                if target_type == 'post':
                    # 삭제는 POST로 처리하는 것이 원칙이나, 편의를 위해 GET으로 리디렉션하여 처리
                    return redirect(url_for('post_delete_guest', post_id=target_id))
                else:
                    return redirect(url_for('comment_delete_guest', comment_id=target_id))
        else:
            return Response('<script>alert("비밀번호가 일치하지 않습니다."); history.back();</script>')

    # 6. (GET) 비밀번호 입력 폼 표시
    action_text = f"{'게시글' if target_type == 'post' else '댓글'} {'수정' if action == 'edit' else '삭제'}"
    return render_template('guest_auth.html', 
                           user=g.user, 
                           action_text=action_text,
                           action=action, 
                           target_type=target_type, 
                           target_id=target_id)


@app.route('/post-delete-guest/<int:post_id>', methods=['GET'])
def post_delete_guest(post_id):
    """
    (GET) 인증된 게스트의 게시글 삭제 처리
    """
    # 1. 세션 인증 토큰 확인 및 제거
    if not session.pop(f'guest_auth_post_{post_id}', None):
        return Response('<script>alert("인증이 필요합니다."); location.href="/";</script>')

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT author, board_id, title FROM posts WHERE id = ? AND author = ?", (post_id, GUEST_USER_ID))
    post = cursor.fetchone()

    if not post:
        return Response('<script>alert("삭제할 수 없거나 존재하지 않는 게시글입니다."); history.back();</script>')

    board_id = post['board_id']
    title_for_log = post['title']

    try:
        # post_delete 로직에서 사용자 스탯(경험치, 카운트) 관련 부분만 제거
        cursor.execute("SELECT id, author FROM comments WHERE post_id = ?", (post_id,))
        comments = cursor.fetchall()
        
        if comments:
            comment_ids = [c['id'] for c in comments]
            placeholders = ', '.join('?' for _ in comment_ids)
            cursor.execute(f"DELETE FROM reactions WHERE target_type = 'comment' AND target_id IN ({placeholders})", comment_ids)
            
            comment_authors_counts = {}
            for c in comments:
                author = c['author']
                if author != GUEST_USER_ID: # 로그인한 유저의 댓글 카운트만 차감
                    comment_authors_counts[author] = comment_authors_counts.get(author, 0) + 1
            
            for author, count in comment_authors_counts.items():
                cursor.execute("UPDATE users SET comment_count = comment_count - ? WHERE login_id = ?", (count, author))

        cursor.execute("DELETE FROM reactions WHERE target_type = 'post' AND target_id = ?", (post_id,))
        cursor.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
        cursor.execute("DELETE FROM posts WHERE id = ?", (post_id,))

        add_log('DELETE_GUEST_POST', session.get('guest_session_id', 'Guest'), f"게스트 게시글 (id : {post_id})를 삭제했습니다. 제목 : {title_for_log}")
        conn.commit()

    except Exception as e:
        print(f"Error during guest post deletion: {e}")
        add_log('ERROR', session.get('guest_session_id', 'Guest'), f"Error deleting guest post id {post_id}: {e}")
        conn.rollback()
        return Response('<script>alert("게시글 삭제 중 오류가 발생했습니다."); history.back();</script>')

    return redirect(url_for('post_list', board_id=board_id))


@app.route('/comment-delete-guest/<int:comment_id>', methods=['GET'])
def comment_delete_guest(comment_id):
    """
    (GET) 인증된 게스트의 댓글 삭제 처리
    """
    # 1. 세션 인증 토큰 확인 및 제거
    if not session.pop(f'guest_auth_comment_{comment_id}', None):
        return Response('<script>alert("인증이 필요합니다."); location.href="/";</script>')

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT author, post_id, content FROM comments WHERE id = ? AND author = ?", (comment_id, GUEST_USER_ID))
    comment = cursor.fetchone()

    if not comment:
        return Response('<script>alert("삭제할 수 없거나 존재하지 않는 댓글입니다."); history.back();</script>')

    try:
        # delete_comment 로직에서 사용자 스탯 관련 부분만 제거
        cursor.execute("DELETE FROM reactions WHERE target_type = 'comment' AND target_id = ?", (comment_id,))
        cursor.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        cursor.execute("UPDATE posts SET comment_count = comment_count - 1 WHERE id = ?", (comment['post_id'],))

        add_log('DELETE_GUEST_COMMENT', session.get('guest_session_id', 'Guest'), f"게스트 댓글 (id : {comment_id})를 삭제했습니다. 내용 : {comment['content']}")
        conn.commit()
    except Exception as e:
        print(f"Database error while deleting guest comment: {e}")
        add_log('ERROR', session.get('guest_session_id', 'Guest'), f"Error deleting guest comment id {comment_id}: {e}")
        conn.rollback()
        return Response('<script>alert("댓글 삭제 중 오류가 발생했습니다."); history.back();</script>')

    return redirect(url_for('post_detail', post_id=comment['post_id']))


@app.route('/post-edit-guest/<int:post_id>', methods=['GET', 'POST'])
def post_edit_guest(post_id):
    """
    (GET/POST) 인증된 게스트의 게시글 수정
    """
    # 1. 세션 인증 토큰 확인 (제출 전까지 제거하지 않음)
    if not session.get(f'guest_auth_post_{post_id}'):
        return Response('<script>alert("인증이 필요합니다."); location.href="/";</script>')
    
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM posts WHERE id = ? AND author = ?", (post_id, GUEST_USER_ID))
    post = cursor.fetchone()

    if not post:
        return Response('<script>alert("존재하지 않는 게시글입니다."); history.back();</script>')
    
    if request.method == 'POST':
        # (POST) 수정 폼 제출
        title = request.form.get('title')
        content = request.form.get('content')
        
        # (post_edit에서 복사)
        if not title or not content:
            return Response('<script>alert("제목, 내용을 모두 입력해주세요."); history.back();</script>')
        if len(title) > 50:
            return Response('<script>alert("제목은 50자를 초과할 수 없습니다."); history.back();</script>')
        plain_text_content = bleach.clean(content, tags=[], strip=True)
        if len(plain_text_content) > 5000:
            return Response('<script>alert("글자 수는 5,000자를 초과할 수 없습니다."); history.back();</script>')
        if len(plain_text_content) == 0:
            return Response('<script>alert("내용을 입력해주세요."); history.back();</script>')

        # (post_edit에서 복사)
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
        allowed_css_properties = [
        'color', 'background-color', 'font-family', 'font-size', 
        'font-weight', 'text-align', 'text-decoration'
        ]
        css_sanitizer = CSSSanitizer(allowed_css_properties=allowed_css_properties)
        sanitized_content = bleach.clean(content, tags=allowed_tags, attributes=allowed_attrs, protocols=['http', 'https', 'data'], css_sanitizer=css_sanitizer)

        updated_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 게스트는 게시판 이동, 공지 설정 불가
        query = "UPDATE posts SET title = ?, content = ?, updated_at = ? WHERE id = ?"
        cursor.execute(query, (title, sanitized_content, updated_at, post_id))
        
        add_log('EDIT_GUEST_POST', session.get('guest_session_id', 'Guest'), f"게스트 게시글 (id : {post_id})를 수정했습니다.")
        conn.commit()

        # 수정 완료 후 인증 토큰 제거
        session.pop(f'guest_auth_post_{post_id}', None)
        return redirect(url_for('post_detail', post_id=post_id))

    else: 
        # (GET) 수정 페이지 표시
        return render_template('post_edit_guest.html', post=post)


@app.route('/comment-edit-guest/<int:comment_id>', methods=['GET', 'POST'])
def comment_edit_guest(comment_id):
    """
    (GET/POST) 인증된 게스트의 댓글 수정
    """
    # 1. 세션 인증 토큰 확인
    if not session.get(f'guest_auth_comment_{comment_id}'):
        return Response('<script>alert("인증이 필요합니다."); location.href="/";</script>')

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM comments WHERE id = ? AND author = ?", (comment_id, GUEST_USER_ID))
    comment = cursor.fetchone()

    if not comment:
        return Response('<script>alert("존재하지 않는 댓글입니다."); history.back();</script>')
    
    if request.method == 'POST':
        # (POST) 수정 폼 제출
        new_content = request.form.get('edit_content')
        if not new_content or not new_content.strip():
            return Response('<script>alert("댓글 내용을 입력해주세요."); history.back();</script>')
        
        try:
            updated_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            sanitized_content = bleach.clean(new_content)
            
            query = "UPDATE comments SET content = ?, updated_at = ? WHERE id = ?"
            cursor.execute(query, (sanitized_content, updated_at, comment_id))
            
            add_log('EDIT_GUEST_COMMENT', session.get('guest_session_id', 'Guest'), f"게스트 댓글 (id : {comment_id})를 수정했습니다.")
            conn.commit()

            # 수정 완료 후 인증 토큰 제거
            session.pop(f'guest_auth_comment_{comment_id}', None)
        except Exception as e:
            print(f"Database error while editing guest comment: {e}")
            add_log('ERROR', session.get('guest_session_id', 'Guest'), f"Error editing guest comment id {comment_id}: {e}")
            conn.rollback()
            return Response('<script>alert("댓글 수정 중 오류가 발생했습니다."); history.back();</script>')
        
        return redirect(url_for('post_detail', post_id=comment['post_id']))
    
    else: 
        # (GET) 수정 페이지 표시
        return render_template('comment_edit_guest.html', comment=comment, user=g.user)

@app.route('/etacon/request', methods=['GET', 'POST'])
@check_banned
@login_required
def etacon_request():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        price = request.form.get('price', type=int)
        
        # 유효성 검사
        if not name or price is None:
            return Response('<script>alert("필수 정보를 입력해주세요."); history.back();</script>')
        
        if price < 0:
            return Response('<script>alert("가격은 0 이상이어야 합니다."); history.back();</script>')

        # 썸네일 및 인곽콘 이미지들
        thumbnail = request.files.get('thumbnail')
        etacon_files = request.files.getlist('etacon_files') # 다중 파일 업로드

        if not thumbnail or not etacon_files or len(etacon_files) == 0:
             return Response('<script>alert("썸네일과 인곽콘 이미지를 최소 1개 이상 업로드해야 합니다."); history.back();</script>')
        
        if len(etacon_files) > 10:
            return Response('<script>alert("인곽콘 이미지는 한 팩당 최대 10개까지만 등록할 수 있습니다."); history.back();</script>')

        def validate_image_ratio(file_obj):
            """이미지가 1:1 비율인지 확인합니다."""
            try:
                img = Image.open(file_obj)
                width, height = img.size
                file_obj.seek(0)
                return width == height
            except Exception as e:
                print(f"이미지 검사 오류: {e}")
                return False

        if thumbnail and allowed_etacon_file(thumbnail.filename):
            if not validate_image_ratio(thumbnail):
                return Response('<script>alert("썸네일 이미지는 정방형(1:1 비율)이어야 합니다."); history.back();</script>')

        for file in etacon_files:
            if file and allowed_etacon_file(file.filename):
                if not validate_image_ratio(file):
                    return Response(f'<script>alert("모든 인곽콘 이미지는 1:1 비율이어야 합니다.\\n확인 필요: {file.filename}"); history.back();</script>')

        conn = get_db()
        cursor = conn.cursor()

        try:
            # 1. 패키지 기본 정보 저장 (ID 확보를 위해 먼저 INSERT)
            created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("""
                INSERT INTO etacon_packs (name, description, price, thumbnail, uploader_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """, (name, description, price, '', g.user['login_id'], created_at))
            
            pack_id = cursor.lastrowid
            pack_folder = f"pack_{pack_id}"

            # 2. 썸네일 저장 및 업데이트
            thumb_path = save_etacon_image(thumbnail, pack_folder)
            if not thumb_path:
                raise Exception("썸네일 저장 실패")
            
            cursor.execute("UPDATE etacon_packs SET thumbnail = ? WHERE id = ?", (thumb_path, pack_id))

            # 3. 개별 인곽콘 이미지 저장
            for idx, file in enumerate(etacon_files):
                if file and allowed_etacon_file(file.filename):
                    img_path = save_etacon_image(file, pack_folder)
                    if img_path:
                        # 코드 형식: ~packID_index (예: ~15_0, ~15_1) -> 유니크하고 파싱하기 쉬움
                        code = f"~{pack_id}_{idx}"
                        cursor.execute("INSERT INTO etacons (pack_id, image_path, code) VALUES (?, ?, ?)", 
                                       (pack_id, img_path, code))

            conn.commit()
            add_log('REQUEST_ETACON', g.user['login_id'], f"인곽콘 패키지 '{name}' 등록을 요청했습니다.")
            return Response('<script>alert("인곽콘 등록 요청이 완료되었습니다. 관리자 승인 후 상점에 공개됩니다."); location.href="/mypage";</script>')

        except Exception as e:
            conn.rollback()
            print(f"인곽콘 등록 중 오류: {e}")
            return Response(f'<script>alert("오류가 발생했습니다."); history.back();</script>')

    return render_template('etacon/request.html', user=g.user)

@app.route('/admin/etacon/requests')
@login_required
@admin_required
def admin_etacon_requests():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. 대기 중인 패키지 기본 정보 조회
    cursor.execute("SELECT * FROM etacon_packs WHERE status = 'pending' ORDER BY created_at DESC")
    pack_rows = cursor.fetchall()
    
    requests_data = []
    
    # 2. 각 패키지에 포함된 인곽콘 이미지 리스트 조회 및 병합
    for row in pack_rows:
        pack = dict(row) # Row 객체를 dict로 변환 (데이터 추가를 위해)
        
        # 해당 패키지의 이미지 경로들 조회
        cursor.execute("SELECT image_path FROM etacons WHERE pack_id = ?", (pack['id'],))
        images = [img['image_path'] for img in cursor.fetchall()]
        
        pack['images'] = images # 이미지 리스트 추가
        requests_data.append(pack)
    
    return render_template('admin/etacon_requests.html', requests=requests_data, user=g.user)

@app.route('/admin/etacon/approve/<int:pack_id>', methods=['POST'])
@login_required
@admin_required
def approve_etacon(pack_id):
    conn = get_db()
    cursor = conn.cursor()
    
    # 상태를 approved로 변경
    cursor.execute("UPDATE etacon_packs SET status = 'approved' WHERE id = ?", (pack_id,))
    
    # (선택) 등록한 유저에게 자동으로 해당 패키지 지급 (자기가 만든 건 무료로 쓰게)
    cursor.execute("SELECT uploader_id FROM etacon_packs WHERE id = ?", (pack_id,))
    pack = cursor.fetchone()
    if pack:
        uploader_id = pack[0]
        cursor.execute("INSERT OR IGNORE INTO user_etacons (user_id, pack_id, purchased_at) VALUES (?, ?, ?)",
                       (uploader_id, pack_id, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    
    conn.commit()
    add_log('APPROVE_ETACON', g.user['login_id'], f"인곽콘 패키지 {pack_id}번을 승인했습니다.")
    return jsonify({'status': 'success'})

@app.route('/admin/etacon/reject/<int:pack_id>', methods=['POST'])
@login_required
@admin_required
def reject_etacon(pack_id):
    conn = get_db()
    cursor = conn.cursor()
    
    # DB에서 삭제
    cursor.execute("DELETE FROM etacon_packs WHERE id = ?", (pack_id,))
    conn.commit()
    
    try:
        shutil.rmtree(os.path.join(ETACON_UPLOAD_FOLDER, f"pack_{pack_id}"))
    except:
        pass

    add_log('REJECT_ETACON', g.user['login_id'], f"인곽콘 패키지 {pack_id}번을 거절(삭제)했습니다.")
    return jsonify({'status': 'success'})

@app.route('/etacon/shop')
@login_required
def etacon_shop():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT p.*, 
               (SELECT COUNT(*) FROM user_etacons ue WHERE ue.pack_id = p.id AND ue.user_id = ?) as is_owned
        FROM etacon_packs p
        WHERE p.status = 'approved'
        ORDER BY p.created_at DESC
    """, (g.user['login_id'],))
    pack_rows = cursor.fetchall()

    # [추가] "인곽콘 승인" 로직과 동일하게, 각 패키지별 상세 이미지 목록을 조회하여 병합
    packs = []
    for row in pack_rows:
        pack = dict(row) # Row 객체를 dict로 변환
        
        # 해당 패키지의 이미지 경로들 조회
        cursor.execute("SELECT image_path FROM etacons WHERE pack_id = ?", (pack['id'],))
        images = [img['image_path'] for img in cursor.fetchall()]
        
        pack['images'] = images # 이미지 리스트 추가
        packs.append(pack)
    
    return render_template('etacon/shop.html', packs=packs, user=g.user)

@app.route('/etacon/buy/<int:pack_id>', methods=['POST'])
@login_required
def buy_etacon(pack_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. 패키지 정보 확인
    cursor.execute("SELECT * FROM etacon_packs WHERE id = ? AND status = 'approved'", (pack_id,))
    pack = cursor.fetchone()
    
    if not pack:
        return jsonify({'status': 'error', 'message': '존재하지 않거나 판매 중지된 패키지입니다.'}), 404
        
    # 2. 이미 보유 중인지 확인
    cursor.execute("SELECT * FROM user_etacons WHERE user_id = ? AND pack_id = ?", (g.user['login_id'], pack_id))
    if cursor.fetchone():
        return jsonify({'status': 'error', 'message': '이미 보유하고 있는 패키지입니다.'}), 400
        
    # 3. 포인트 확인 및 차감
    if g.user['point'] < pack['price']:
        return jsonify({'status': 'error', 'message': '포인트가 부족합니다.'}), 400
        
    try:
        # 포인트 차감
        cursor.execute("UPDATE users SET point = point - ? WHERE login_id = ?", (pack['price'], g.user['login_id']))
        # 패키지 지급
        cursor.execute("INSERT INTO user_etacons (user_id, pack_id, purchased_at) VALUES (?, ?, ?)",
                       (g.user['login_id'], pack_id, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        
        add_log('BUY_ETACON', g.user['login_id'], f"인곽콘 패키지 '{pack['name']}'을 구매했습니다. (-{pack['price']}P)")
        return jsonify({'status': 'success', 'message': '구매가 완료되었습니다!'})
        
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': '구매 처리 중 오류가 발생했습니다.'}), 500

@app.route('/api/my-etacons')
@login_required
def my_etacons():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 사용자가 보유한 패키지의 모든 인곽콘 조회
    query = """
        SELECT e.code, e.image_path, p.name as pack_name, p.id as pack_id
        FROM etacons e
        JOIN user_etacons ue ON e.pack_id = ue.pack_id
        JOIN etacon_packs p ON e.pack_id = p.id
        WHERE ue.user_id = ?
        ORDER BY ue.purchased_at DESC, e.id ASC
    """
    cursor.execute(query, (g.user['login_id'],))
    rows = cursor.fetchall()
    
    # 패키지별로 그룹화하여 JSON 반환
    result = {}
    for row in rows:
        pack_name = row['pack_name']
        if pack_name not in result:
            result[pack_name] = []
        result[pack_name].append({
            'code': row['code'],
            'image_path': row['image_path']
        })
        
    return jsonify(result)

@app.route('/api/vote', methods=['POST'])
@login_required
@check_banned
def vote_api():
    data = request.get_json()
    
    # [수정 1] 프론트엔드에서 문자열로 넘어올 수 있으므로 int()로 변환하여 비교해야 함
    try:
        poll_id = int(data.get('poll_id'))
        option_id = int(data.get('option_id'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': '잘못된 데이터 형식입니다.'}), 400
    
    if not poll_id or not option_id:
        return jsonify({'status': 'error', 'message': '잘못된 요청입니다.'}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        user_id = g.user['login_id']
        current_voted_option_id = option_id # 기본적으로 현재 선택한 항목으로 설정
        
        # 1. 이미 투표했는지 확인
        cursor.execute("SELECT id, option_id FROM poll_history WHERE poll_id = ? AND user_id = ?", (poll_id, user_id))
        history = cursor.fetchone()
        
        if history:
            history_id = history[0]
            old_option_id = history[1] # DB에서 가져온 값 (Integer)
            
            # [수정 2] 자료형이 맞춰졌으므로 이제 정확한 비교가 가능함
            if old_option_id == option_id:
                # [투표 취소] 같은 항목을 다시 누름 -> 기록 삭제 및 카운트 감소
                cursor.execute("UPDATE poll_options SET vote_count = vote_count - 1 WHERE id = ?", (old_option_id,))
                cursor.execute("DELETE FROM poll_history WHERE id = ?", (history_id,))
                
                current_voted_option_id = None # 선택된 항목 없음 (취소됨)
                action_type = "CANCEL"
            else:
                # [투표 변경] 다른 항목 누름 -> 기존 감소, 신규 증가, 기록 수정
                cursor.execute("UPDATE poll_options SET vote_count = vote_count - 1 WHERE id = ?", (old_option_id,))
                cursor.execute("UPDATE poll_options SET vote_count = vote_count + 1 WHERE id = ?", (option_id,))
                cursor.execute("UPDATE poll_history SET option_id = ? WHERE id = ?", (option_id, history_id))
                
                action_type = "CHANGE"
        else:
            # [신규 투표]
            cursor.execute("INSERT INTO poll_history (poll_id, user_id, option_id) VALUES (?, ?, ?)", 
                           (poll_id, user_id, option_id))
            cursor.execute("UPDATE poll_options SET vote_count = vote_count + 1 WHERE id = ?", (option_id,))
            
            action_type = "VOTE"
            
        conn.commit()
        
        # 2. 최신 투표 현황 조회하여 반환
        cursor.execute("SELECT id, vote_count FROM poll_options WHERE poll_id = ?", (poll_id,))
        updated_options = cursor.fetchall()
        
        total_votes = sum(opt[1] for opt in updated_options)
        
        results = []
        for opt in updated_options:
            percent = 0
            if total_votes > 0:
                percent = round((opt[1] / total_votes) * 100, 1)
            results.append({
                'id': opt[0],
                'vote_count': opt[1],
                'percent': percent,
                # JS에서 비교 시 문자열/숫자 차이가 있을 수 있으므로 여기서는 단순 데이터만 전달
                'is_voted': (opt[0] == current_voted_option_id) 
            })
            
        # 메시지 설정
        if action_type == "CANCEL":
            msg = "투표를 취소했습니다."
        elif action_type == "CHANGE":
            msg = "투표를 변경했습니다."
        else:
            msg = "투표했습니다."

        return jsonify({
            'status': 'success',
            'message': msg,
            'total_votes': total_votes,
            'options': results,
            'user_voted_option_id': current_voted_option_id # 취소 시 null 반환됨
        })
        
    except Exception as e:
        conn.rollback()
        print(f"Vote error: {e}")
        return jsonify({'status': 'error', 'message': '투표 처리 중 오류가 발생했습니다.'}), 500

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 현재 차단된 사용자 목록 조회 (만료일이 남았거나 status가 banned인 경우)
    cursor.execute("""
        SELECT * FROM users 
        WHERE status = 'banned' 
        ORDER BY banned_until DESC
    """)
    banned_users = cursor.fetchall()
    
    return render_template('admin/manage_users.html', banned_users=banned_users, user=g.user)

@app.route('/admin/users/ban', methods=['POST'])
@login_required
@admin_required
def admin_ban_user():
    name = request.form.get('name')
    hakbun = request.form.get('hakbun')
    duration = request.form.get('duration', type=int)
    reason = request.form.get('reason', '') # 차단 사유 (로그용)
    
    if not name or not hakbun or not duration:
        return Response('<script>alert("이름, 학번, 기간을 모두 입력해주세요."); history.back();</script>')
        
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. 사용자 찾기 (동명이인 방지를 위해 학번까지 확인)
    cursor.execute("SELECT login_id, nickname FROM users WHERE name = ? AND hakbun = ?", (name, hakbun))
    target_user = cursor.fetchone()
    
    if not target_user:
        return Response('<script>alert("해당 정보(이름/학번)와 일치하는 사용자를 찾을 수 없습니다."); history.back();</script>')
    
    user_id = target_user[0]
    nickname = target_user[1]
    
    # 관리자는 차단 불가
    cursor.execute("SELECT role FROM users WHERE login_id = ?", (user_id,))
    if cursor.fetchone()[0] == 'admin':
         return Response('<script>alert("관리자 계정은 차단할 수 없습니다."); history.back();</script>')

    # 2. 차단 만료일 계산
    banned_until = (datetime.datetime.now() + datetime.timedelta(days=duration)).strftime('%Y-%m-%d %H:%M:%S')
    
    # 3. DB 업데이트
    cursor.execute("UPDATE users SET status = 'banned', banned_until = ? WHERE login_id = ?", (banned_until, user_id))
    conn.commit()
    
    add_log('BAN_USER', g.user['login_id'], f"사용자 차단: {nickname}({name}, {hakbun}) - {duration}일. 사유: {reason}")
    
    return Response(f'<script>alert("{nickname}님을 {duration}일간 차단했습니다."); location.href="/admin/users";</script>')

@app.route('/admin/users/unban', methods=['POST'])
@login_required
@admin_required
def admin_unban_user():
    name = request.form.get('name')
    hakbun = request.form.get('hakbun')
    
    if not name or not hakbun:
         return Response('<script>alert("이름과 학번을 입력해주세요."); history.back();</script>')

    conn = get_db()
    cursor = conn.cursor()
    
    # 사용자 찾기
    cursor.execute("SELECT login_id, nickname FROM users WHERE name = ? AND hakbun = ?", (name, hakbun))
    target_user = cursor.fetchone()
    
    if not target_user:
        return Response('<script>alert("해당 정보(이름/학번)와 일치하는 사용자를 찾을 수 없습니다."); history.back();</script>')
        
    user_id = target_user[0]
    nickname = target_user[1]
    
    # 차단 해제 업데이트
    cursor.execute("UPDATE users SET status = 'active', banned_until = NULL WHERE login_id = ?", (user_id,))
    conn.commit()
    
    add_log('UNBAN_USER', g.user['login_id'], f"사용자 차단 해제: {nickname}({name}, {hakbun})")
    
    return Response(f'<script>alert("{nickname}님의 차단을 해제했습니다."); location.href="/admin/users";</script>')

def check_content_image_size(content, max_mb=15):
    """
    HTML 본문(content) 내의 Base64 이미지들의 실제 용량을 계산하여
    지정된 크기(max_mb)를 초과하는지 검사합니다.
    """
    if not content:
        return True, 0

    # 이미지 태그에서 Base64 데이터 추출 (data:image/...;base64, 부분 이후)
    # 정규식으로 src 속성의 값을 찾습니다.
    base64_images = re.findall(r'src=["\']data:image/[a-zA-Z]+;base64,([^"\']+)["\']', content)
    
    limit_bytes = max_mb * 1024 * 1024 # 5MB를 바이트로 변환
    
    for idx, b64_data in enumerate(base64_images):
        # Base64 문자열 길이로 실제 파일 크기 추산
        # 공식: (Base64 길이 * 3) / 4
        # (패딩 '=' 처리는 오차가 미미하므로 생략)
        real_size = (len(b64_data) * 3) / 4
        
        if real_size > limit_bytes:
            # 용량 초과 시, 몇 번째 이미지인지 반환 (False, 순서)
            return False, idx + 1
            
    return True, 0

# Server Drive Unit
if __name__ == '__main__':
    from gevent.pywsgi import WSGIServer
    
    init_log_db()    
    
    http_server = WSGIServer(('0.0.0.0', 5000), app)
    print("Starting server on http://0.0.0.0:5000")
    http_server.serve_forever()