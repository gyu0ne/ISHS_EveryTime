from flask import Flask, request, render_template, url_for, redirect, jsonify, session, g, Response
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import hashlib
import datetime
import sqlite3
import os
import requests

from route import *

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

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

# Main Page
@app.route('/')
def main_page():
    return render_template('main.html')

# Riro Auth
@app.route('/riro-auth', methods=['GET', 'POST'])
def riro_auth():
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
            response = requests.get(f"{base_url}{endpoint}", params=payload)

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
        
        hashed_pw = hashlib.sha256(pw.encode()).hexdigest()
        join_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        default_profile = 'images/default_profile.jpg'
        
        # DATA INSERT to DB
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (hakbun, gen, name, pw, login_id, nickname, birth, profile_image, join_date, role, is_autologin, autologin_token, level, exp, post_count, comment_count, point) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'student', 0, '', 1, 0, 0, 0, 0)", (hakbun, gen, name, hashed_pw, id, nick, birth, join_date, default_profile))
        conn.commit()
        
        return Response('<script> alert("회원가입이 완료되었습니다."); window.location.href = "/"; </script>') # After Register
    
    return render_template('register_form.html', hakbun=hakbun, name=name, gen=gen) # GET

# Server Drive Unit
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)