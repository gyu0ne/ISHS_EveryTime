from flask import Flask, request, render_template, url_for, redirect, jsonify, session, g, Response, make_response
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
from functools import wraps
import requests
import hashlib
import secrets
import sqlite3
import bleach
import socket
import math
import html
import os

from route import *

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)

DATABASE = 'data.db'

# DB connect (first line of all route)
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

# Close DB connecting
@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

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
        date = (datetime.now()).strftime('%Y%m%d')

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
                print(content)
                print(content['1'], content['2'], content['3'])
                cursor.execute('INSERT INTO meals (date, breakfast, lunch, dinner) VALUES (?, ?, ?, ?)',(date, content['1'], content['2'], content['3']))
                conn.commit()
            
                cursor.execute('SELECT breakfast, lunch, dinner FROM meals WHERE date = ?', (date,))
                meal_data = cursor.fetchone()

                return [meal_data[0], meal_data[1], meal_data[2]]
            else:
                content = ["API 호출 실패","API 호출 실패","API 호출 실패"]

            return content

# Jinja2 Filter for Datetime Formatting
def format_datetime(value):
    # DB에서 가져온 날짜/시간 문자열을 datetime 객체로 변환
    # DB에 저장된 형식이 '%Y-%m-%d'이므로, 시간 정보를 추가하여 파싱
    post_time = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    now = datetime.now()
    
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
        # 한 달이 넘으면 원래 날짜 형식을 반환
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
        # board_id에 해당하는 게시글을 created_at 기준으로 내림차순(최신순) 정렬하여 상위 5개를 선택합니다.
        # users 테이블과 JOIN하여 작성자 닉네임도 함께 가져옵니다.
        query = """
            SELECT p.id, p.title, u.nickname, p.created_at
            FROM posts p
            JOIN users u ON p.author = u.login_id
            WHERE p.board_id = ?
            ORDER BY p.created_at DESC
            LIMIT 5
        """
        cursor.execute(query, (board_id,))
        posts = cursor.fetchall()
        return posts
    except Exception as e:
        # 데이터베이스 조회 중 오류가 발생하면 콘솔에 에러를 출력하고 빈 리스트를 반환합니다.
        print(f"Error fetching recent posts for board_id {board_id}: {e}")
        return []

# Main Page
@app.route('/')
def main_page():
    if 'user_id' in session:
        conn = get_db()
        conn.row_factory = sqlite3.Row  # 컬럼 이름으로 접근 가능하도록 설정
        cursor = conn.cursor()

        free_board_posts = get_recent_posts(1)
        info_board_posts = get_recent_posts(2)

        # 사용자 정보 조회
        cursor.execute("SELECT nickname, hakbun, login_id FROM users WHERE login_id = ?", (session['user_id'],))
        user_data = cursor.fetchone()

        bob_data = get_bob()

        # 사용자 정보가 있으면 템플릿에 전달
        if user_data:
            return render_template('main_logined.html', user=user_data, bob=bob_data, free_posts=free_board_posts, info_posts=info_board_posts)
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
        # 기타 예외 처리
        print(f"Error during Googlebot verification: {e}")
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

            cursor.execute('SELECT COUNT(*) FROM users WHERE name = ?', (api_result['name'],))
            count_name = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM users WHERE hakbun = ?', (api_result['student_number'],))
            count_hakbun = cursor.fetchone()[0]

            if count_name > 0 and count_hakbun > 0:
                pass
    #             return Response(f'''
    #     <script>
    #         alert("이미 가입된 계정이 있습니다.");
    #         history.back();
    #     </script>
    # ''')

            session['hakbun'] = api_result['student_number']
            session['name'] = api_result['name']
            session['gen'] = api_result['generation']

            return redirect('yakgwan')

        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP 에러 발생: {http_err}")
            print(f"응답 내용: {response.text}")
            return Response(f'''
    <script>
        alert("HTTP 오류 발생 : {http_err}, 응답 내용 : {response.text}")
        history.back();
    </script>
''')
        except requests.exceptions.RequestException as req_err:
            print(f"요청 중 에러 발생: {req_err}")
            return Response(f'''
    <script>
        alert("요청 중 오류 발생 : {req_err}")
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

    print ({'login_id': id_tf, 'nickname': nickname_tf})
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
        birth = request.form['birth']

        year = birth[0:4]
        month = birth[4:6]
        day = birth[6:8]

        try:
            datetime.date(int(year), int(month), int(day))
        except:
            return Response('<script> alert("생년월일 형식을 다시 확인하세요."); history.back(); </script>')

        if len(birth) != 8:
            return Response('<script> alert("생년월일 형식을 다시 확인하세요."); history.back(); </script>')

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
        default_profile = 'images/default_profile.jpg'
        
        # DATA INSERT to DB
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (hakbun, gen, name, pw, login_id, nickname, birth, profile_image, join_date, role, is_autologin, autologin_token, level, exp, post_count, comment_count, point) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'student', 0, '', 1, 0, 0, 0, 0)", (hakbun, gen, name, hashed_pw, id, nick, birth, default_profile, join_date))
        conn.commit()

        session.pop('hakbun', None)
        session.pop('name', None)
        session.pop('gen', None)
        session.pop('agree', None)

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
def mypage():
    if 'user_id' not in session:
        return redirect('/login')

    conn = get_db()
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE login_id = ?", (session['user_id'],))
    data = cursor.fetchone()

    if not data:
        return redirect('/login')

    hakbun = data['hakbun']
    name = data['name']
    gen = data['gen']
    nick = data['nickname']
    birth = data['birth']
    birth_year = birth[0:4]
    birth_month = birth[4:6]
    birth_day = birth[6:8]
    join_date = data['join_date']
    datetime_obj = datetime.strptime(join_date, '%Y-%m-%d %H:%M:%S')
    output_join_date = datetime_obj.strftime('%Y.%m.%d')
    level = data['level']
    exp = data['exp']
    post_count = data['post_count']
    comment_count = data['comment_count']
    cash = data['point']
    profile_image = data['profile_image']

    cursor.execute("SELECT * FROM posts WHERE author = ? ORDER BY created_at DESC", (session['user_id'],))
    post_data = cursor.fetchall()

    user_posts = []
    for post in post_data:
        post_board_id = post['board_id']
        cursor.execute("SELECT board_name FROM board WHERE board_id = ?", (post_board_id,))
        board_info = cursor.fetchone()
        board_name_str = board_info['board_name'] if board_info else "알 수 없음"
        
        created_at_dt = datetime.strptime(post['created_at'], '%Y-%m-%d %H:%M:%S')
        created_at_formatted = created_at_dt.strftime('%Y.%m.%d')

        user_posts.append({
            'id': post['id'],
            'title': post['title'],
            'comment_count': post['comment_count'],
            'board_name': board_name_str,
            'created_at': created_at_formatted
        })

    cursor.execute("SELECT * FROM comments WHERE author = ? ORDER BY created_at DESC", (session['user_id'],))
    comment_data = cursor.fetchall()

    user_comments = []
    for comment in comment_data:
        cursor.execute("SELECT title FROM posts WHERE id = ?", (comment['post_id'],))
        post_info = cursor.fetchone()
        post_title = post_info['title'] if post_info else "삭제된 게시글"

        created_at_dt = datetime.strptime(comment['created_at'], '%Y-%m-%d %H:%M:%S')
        created_at_formatted = created_at_dt.strftime('%Y.%m.%d')

        user_comments.append({
            'content': comment['content'],
            'post_title': post_title,
            'post_id': comment['post_id'],
            'created_at': created_at_formatted
        })

    return render_template('my_page.html', 
                           hakbun=hakbun, name=name, gen=gen, nickname=nick, 
                           birth=f'{birth_year}.{birth_month}.{birth_day}', profile_image=profile_image,
                           join_date=output_join_date, level=level, exp=exp, 
                           post_count=post_count, comment_count=comment_count, 
                           point=cash, user_posts=user_posts, user_comments=user_comments)

@app.route('/post-write', methods=['GET', 'POST'])
@login_required
def post_write():
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        # 1. 폼 데이터 수신
        title = request.form.get('title')
        content = request.form.get('content')
        board_id = request.form.get('board_id') # board_id 수신
        author_id = session['user_id']

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
        # data URI를 허용하도록 protocols에 'data' 추가
        sanitized_content = bleach.clean(content, tags=allowed_tags, attributes=allowed_attrs, protocols=['http', 'https', 'data'])

        # 4. 데이터베이스에 저장
        try:
            created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            query = """
                INSERT INTO posts
                (board_id, title, content, author, created_at, updated_at, view_count, like_count, dislike_count, comment_count, is_notice)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0)
            """
            cursor.execute(query, (board_id, title, sanitized_content, author_id, created_at, created_at))
            conn.commit()

            return redirect(url_for('post_list', board_id=board_id))
        except Exception as e:
            print(f"Database error: {e}")
            return Response('<script>alert("게시글 저장 중 오류가 발생했습니다."); history.back();</script>')

    # GET 요청 시: DB에서 게시판 목록을 가져와 템플릿으로 전달
    cursor.execute("SELECT board_id, board_name FROM board ORDER BY board_id")
    boards = cursor.fetchall() # (board_id, board_name) 튜플의 리스트
    return render_template('post_write.html', boards=boards)

@app.route('/board/<int:board_id>', defaults={'page': 1})
@app.route('/board/<int:board_id>/<int:page>')
@login_required
def post_list(board_id, page):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    posts_per_page = 20  # 페이지 당 게시글 수를 20으로 설정

    try:
        # 1. 게시판 정보 조회
        cursor.execute("SELECT board_name FROM board WHERE board_id = ?", (board_id,))
        board = cursor.fetchone()

        if not board:
            return Response('<script>alert("존재하지 않는 게시판입니다."); history.back();</script>')

        # 2. 공지사항 목록 조회 (is_notice = 1)
        notice_query = """
            SELECT p.id, p.title, u.nickname, p.created_at, p.view_count, p.like_count
            FROM posts p JOIN users u ON p.author = u.login_id
            WHERE p.board_id = ? AND p.is_notice = 1
            ORDER BY p.id DESC
        """
        cursor.execute(notice_query, (board_id,))
        notices = cursor.fetchall()

        # 3. 일반 게시글 총 개수 조회 (페이지네이션 계산용)
        cursor.execute("SELECT COUNT(*) FROM posts WHERE board_id = ? AND is_notice = 0", (board_id,))
        total_posts = cursor.fetchone()[0]
        total_pages = math.ceil(total_posts / posts_per_page)

        # 4. 현재 페이지에 해당하는 일반 게시글 목록 조회 (is_notice = 0)
        offset = (page - 1) * posts_per_page
        posts_query = """
            SELECT p.id, p.title, p.comment_count, p.created_at, p.view_count, p.like_count, u.nickname
            FROM posts p JOIN users u ON p.author = u.login_id
            WHERE p.board_id = ? AND p.is_notice = 0
            ORDER BY p.id DESC
            LIMIT ? OFFSET ?
        """
        cursor.execute(posts_query, (board_id, posts_per_page, offset))
        posts = cursor.fetchall()

    except Exception as e:
        print(f"Error fetching post list: {e}")
        return Response('<script>alert("게시글을 불러오는 중 오류가 발생했습니다."); history.back();</script>')

    return render_template('post_list.html',
                           board=board,
                           notices=notices,
                           posts=posts,
                           total_pages=total_pages,
                           current_page=page,
                           board_id=board_id)

@app.route('/post/<int:post_id>')
@login_required
def post_detail(post_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # 1. 게시글 정보 조회 (posts, users, board 테이블 JOIN)
        # 작성자 닉네임과 프로필 이미지, 게시판 이름을 함께 가져옵니다.
        query = """
            SELECT p.*, u.nickname, u.profile_image, b.board_name
            FROM posts p
            JOIN users u ON p.author = u.login_id
            JOIN board b ON p.board_id = b.board_id
            WHERE p.id = ?
        """
        cursor.execute(query, (post_id,))
        post = cursor.fetchone()

        if not post:
            return Response('<script>alert("존재하지 않거나 삭제된 게시글입니다."); history.back();</script>')

        # 2. 조회수 1 증가 (UPDATE)
        # 동일 사용자의 반복적인 조회수 증가를 막기 위한 로직은 추후 세션을 이용해 구현할 수 있습니다.
        cursor.execute("UPDATE posts SET view_count = view_count + 1 WHERE id = ?", (post_id,))
        conn.commit()

        # 3. 해당 게시글의 댓글 목록 조회 (comments, users 테이블 JOIN)
        # 댓글 작성자의 닉네임과 프로필 이미지를 함께 가져옵니다.
        comment_query = """
            SELECT c.*, u.nickname, u.profile_image
            FROM comments c
            JOIN users u ON c.author = u.login_id
            WHERE c.post_id = ?
            ORDER BY c.created_at ASC
        """
        cursor.execute(comment_query, (post_id,))
        comments = cursor.fetchall()

    except Exception as e:
        print(f"Error fetching post detail: {e}")
        return Response('<script>alert("게시글을 불러오는 중 오류가 발생했습니다."); history.back();</script>')

    return render_template('post_detail.html', post=post, comments=comments)

# Server Drive Unit
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)