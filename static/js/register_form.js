// CSRF 토큰 설정
const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

// 입력 필드 및 메시지 영역 요소 가져오기
const loginIdInput = document.getElementById('login_id');
const nicknameInput = document.getElementById('nickname');
const birthInput = document.getElementById('birth');
const pwInput = document.getElementById('password');
const pwConfirmInput = document.getElementById('password_confirm');

const idCheckMsg = document.getElementById('id-check-msg');
const nicknameCheckMsg = document.getElementById('nickname-check-msg');
const birthCheckMsg = document.getElementById('birth-check-msg');
const pwCheckMsg = document.getElementById('password-check-msg');
const pwConfirmMsg = document.getElementById('password-same-check-msg');

// Helper function to set message style
const setMessage = (element, message, color) => {
    element.textContent = message;
    element.style.color = color;
};

// 1. 아이디 중복 확인 (서버 DB 조회 필요) - 'blur' 이벤트 유지
loginIdInput.addEventListener('blur', async function() {
    const id = this.value;
    if (id.length === 0) {
        setMessage(idCheckMsg, '', 'black');
        return;
    }

    try {
        const response = await fetch('/check-register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            // 서버에는 아이디와 닉네임 정보만 보냄
            body: JSON.stringify({ 'id': id, 'nick': nicknameInput.value })
        });
        const data = await response.json();
        if (data.login_id === 'True') {
            setMessage(idCheckMsg, '이미 사용 중인 아이디입니다.', 'red');
        } else {
            setMessage(idCheckMsg, '사용 가능한 아이디입니다.', 'green');
        }
    } catch (error) {
        console.error('Error:', error);
        setMessage(idCheckMsg, '확인 중 오류 발생', 'orange');
    }
});

// 2. 닉네임 중복 확인 (서버 DB 조회 필요) - 'blur' 이벤트 유지
nicknameInput.addEventListener('blur', async function() {
    const nickname = this.value;
    if (nickname.length === 0) {
        setMessage(nicknameCheckMsg, '', 'black');
        return;
    }

    try {
        const response = await fetch('/check-register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ 'id': loginIdInput.value, 'nick': nickname })
        });
        const data = await response.json();
        if (data.nickname === 'True') {
            setMessage(nicknameCheckMsg, '이미 사용 중인 닉네임입니다.', 'red');
        } else {
            setMessage(nicknameCheckMsg, '사용 가능한 닉네임입니다.', 'green');
        }
    } catch (error) {
        console.error('Error:', error);
        setMessage(nicknameCheckMsg, '확인 중 오류 발생', 'orange');
    }
});

// 3. 생년월일 유효성 검사 (클라이언트) - 'input' 이벤트로 실시간 검사
birthInput.addEventListener('input', function() {
    const birth = this.value;
    // 정규식으로 8자리 숫자인지 확인
    const regex = /^\d{8}$/;

    if (birth.length === 0) {
        setMessage(birthCheckMsg, '', 'black');
        return;
    }

    if (!regex.test(birth)) {
        setMessage(birthCheckMsg, '날짜 형식(8자리 숫자)이 올바르지 않습니다.', 'red');
        return;
    }

    // 유효한 날짜인지 확인
    const year = parseInt(birth.substring(0, 4), 10);
    const month = parseInt(birth.substring(4, 6), 10) - 1; // month는 0부터 시작
    const day = parseInt(birth.substring(6, 8), 10);
    const d = new Date(year, month, day);

    if (d.getFullYear() === year && d.getMonth() === month && d.getDate() === day) {
        setMessage(birthCheckMsg, '', 'green'); // 유효하면 메시지 없음
    } else {
        setMessage(birthCheckMsg, '유효하지 않은 날짜입니다.', 'red');
    }
});


// 4. 비밀번호 유효성 검사 (클라이언트) - 'input' 이벤트로 실시간 검사
function validatePasswords() {
    const pw = pwInput.value;
    const pw_check = pwConfirmInput.value;

    // 비밀번호 길이 검사
    if (pw.length > 0 && pw.length < 6) {
        setMessage(pwCheckMsg, '비밀번호는 최소 6자 이상이어야 합니다.', 'red');
    } else {
        setMessage(pwCheckMsg, '', 'black');
    }

    // 비밀번호 일치 여부 검사
    if (pw_check.length > 0 && pw !== pw_check) {
        setMessage(pwConfirmMsg, '비밀번호가 일치하지 않습니다.', 'red');
    } else {
        setMessage(pwConfirmMsg, '', 'black');
    }
}

pwInput.addEventListener('input', validatePasswords);
pwConfirmInput.addEventListener('input', validatePasswords);