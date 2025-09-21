from flask import Flask, request, render_template, url_for, redirect, jsonify, session, g, Response, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
from functools import wraps
from datetime import datetime
import requests
import hashlib
import secrets
import sqlite3
import os

from route import *

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

bcrypt = Bcrypt(app)

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
            session.clear()
            session['user_id'] = user[0]
            session.permanent = True

# Main Page
@app.route('/')
def main_page():
    return render_template('main.html')

# For Login Required Page
# @login_required under @app.route
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
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

            print("API 호출 성공:") # for debuging
            print(api_result) # for debuging

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
                return Response(f'''
        <script>
            alert("이미 가입된 계정이 있습니다.");
            history.back();
        </script>
    ''')

            session['hakbun'] = api_result['student_number']
            session['name'] = api_result['name']
            session['gen'] = api_result['generation']

            return redirect('register')

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
    birth = data.get('birth')

    year = birth[0:4]
    month = birth[4:6]
    day = birth[6:8]

    try:
        datetime.date(int(year), int(month), int(day))
        birth_tf = 'True'
    except:
        birth_tf = 'False'

    if len(birth) != 8:
        birth_tf = 'False'

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

    print ({'login_id': id_tf, 'nickname': nickname_tf, 'birth': birth_tf})
    return {'login_id': id_tf, 'nickname': nickname_tf, 'birth': birth_tf}

# Check PW
@app.route('/check-pw-register/', methods=['POST'])
def check_pw_register():
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    pw = data.get('pw')
    pw_check = data.get('pw_check')

    if len(pw) < 6:
        pw_result = 'False'
    else:
        pw_result = 'True'
    
    if pw_check == pw:
        pw_check_result = 'False'
    else:
        pw_check_result = 'True'

    print ({'pw': pw_result, 'pw_check': pw_check_result})
    return {'pw': pw_result, 'pw_check': pw_check_result}

# Register
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect("/")
    
    conn = get_db()
    if 'hakbun' in session and 'name' in session and 'gen' in session:
        hakbun = session['hakbun']
        name = session['name']
        gen = session['gen']
    else:
        return redirect('riro-auth')

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
        
        return Response('<script> alert("회원가입이 완료되었습니다."); window.location.href = "/"; </script>') # After Register
    
    return render_template('register_form.html', hakbun=hakbun, name=name, gen=gen) # GET

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

@app.route('/logout')
def logout():
    session.clear()

    resp = make_response(redirect("/"))
    resp.set_cookie('remember_token', '', max_age=0)
    
    return resp

@app.route('/mypage')
def mypage():
    conn = get_db()
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()

    if 'user_id' in session:
        cursor.execute("SELECT * FROM users WHERE login_id = ?", (session['user_id'],))
        data = cursor.fetchone()

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

        cursor.execute("SELECT * FROM posts WHERE author = ?", (session['user_id'],))
        post_data = cursor.fetchall()

        posts = []
        comments = []
        board_name = []

        for i in post_data:
            posts.append(i['title'])
            comments.append(i['comment_count'])
            
            post_board_id = i['board_id']

            cursor.execute("SELECT board_name FROM board WHERE board_id = ?", (post_board_id,))
            board_name.append(cursor.fetchone()['board_name'])

        print(f'게시글 목록 : {posts}') # for debuging
        print(f'댓글 수 목록 : {comments}') # for debuging
        print(f'보드 이름 목록 : {board_name}') # for debuging
        print(f'게시글 수 : {post_count}, 댓글 수 : {comment_count}') # for debuging
        print(f'포인트 : {cash}') # for debuging
        print(f'가입일 : {output_join_date}') # for debuging
        print(f'생년월일 : {birth_year}년 {birth_month}월 {birth_day}일') # for debuging
        print(f'학번 : {hakbun}, 이름 : {name}, 닉네임 : {nick}, 기수 : {gen}') # for debuging
        print(f'레벨 : {level}, 경험치 : {exp}') # for debuging
        print(f'아이디 : {session["user_id"]}') # for debuging

        return render_template('my_page.html', hakbun=hakbun, name=name, gen=gen, nickname=nick, birth=f'{birth_year}.{birth_month}.{birth_day}', join_date=output_join_date, level=level, exp=exp, post_count=post_count, comment_count=comment_count, point=cash)
    else:
        return redirect('/login')

# Server Drive Unit
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)