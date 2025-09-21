// CSRF 토큰 설정
const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

// 아이디 입력 필드와 메시지 표시 영역을 가져옴
const loginIdInput = document.getElementById('login_id');
const nicknameInput = document.getElementById('nickname');
const idCheckMsg = document.getElementById('id-check-msg');
const nicknameCheckMsg = document.getElementById('nickname-check-msg');
const birthInput = document.getElementById('birth');
const birthCheckMsg = document.getElementById('birth-check-msg');

// 아이디 중복 확인
loginIdInput.addEventListener('blur', async function() {
    const id = loginIdInput.value;
    const nickname = nicknameInput.value;
    const birth = birthInput.value;

    // 아이디를 입력하지 않았으면 메시지를 지움
    if (id.length === 0) {
        idCheckMsg.textContent = '';
        return;
    }

    // fetch API를 사용해 서버에 POST 요청 보내기
    try {
        const response = await fetch('/check-register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ 'id': id, 'nick': nickname, 'birth': birth })
        });

        const data = await response.json(); // 서버 응답을 JSON으로 파싱

        // 서버 응답(data.exists)에 따라 메시지 업데이트
        if (data.login_id == 'True') {
            idCheckMsg.textContent = '이미 사용 중인 아이디입니다.';
            idCheckMsg.style.color = 'red';
        } else {
            idCheckMsg.textContent = '사용 가능한 아이디입니다.';
            idCheckMsg.style.color = 'green';
        }
    } catch (error) {
        console.error('Error:', error);
        idCheckMsg.textContent = '확인 중 오류가 발생했습니다.';
        idCheckMsg.style.color = 'orange';
    }
});

// 닉네임 중복 확인
nicknameInput.addEventListener('blur', async function() {
    const id = loginIdInput.value;
    const nickname = nicknameInput.value;
    const birth = birthInput.value;

    // 닉네임을 입력하지 않았으면 메시지를 지움
    if (nickname.length === 0) {
        nicknameCheckMsg.textContent = '';
        return;
    }

    // fetch API를 사용해 서버에 POST 요청 보내기
    try {
        const response = await fetch('/check-register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ 'id': id, 'nick': nickname, 'birth': birth })
        });

        const data = await response.json(); // 서버 응답을 JSON으로 파싱

        // 서버 응답(data.exists)에 따라 메시지 업데이트
        if (data.nickname == 'True') {
            nicknameCheckMsg.textContent = '이미 사용 중인 닉네임입니다.';
            nicknameCheckMsg.style.color = 'red';
        } else {
            nicknameCheckMsg.textContent = '사용 가능한 닉네임입니다.';
            nicknameCheckMsg.style.color = 'green';
        }
    } catch (error) {
        console.error('Error:', error);
        nicknameCheckMsg.textContent = '확인 중 오류가 발생했습니다.';
        nicknameCheckMsg.style.color = 'orange';
    }
});

birthInput.addEventListener('blur', async function() {
    const id = loginIdInput.value;
    const nickname = nicknameInput.value;
    const birth = birthInput.value;

    // 생년월일을 입력하지 않았으면 메시지를 지움
    if (birth.length === 0) {
        birthCheckMsg.textContent = '';
        return;
    }

    // fetch API를 사용해 서버에 POST 요청 보내기
    try {
        const response = await fetch('/check-register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ 'id': id, 'nick': nickname, 'birth': birth })
        });

        const data = await response.json(); // 서버 응답을 JSON으로 파싱

        // 서버 응답(data.exists)에 따라 메시지 업데이트
        if (data.birth == 'True') {
            birthCheckMsg.textContent = '';
            birthCheckMsg.style.color = 'red';
        } else {
            birthCheckMsg.textContent = '날짜 형식이 올바르지 않습니다.';
            birthCheckMsg.style.color = 'red';
        }
    } catch (error) {
        console.error('Error:', error);
        birthCheckMsg.textContent = '확인 중 오류가 발생했습니다.';
        birthCheckMsg.style.color = 'orange';
    }
})

const pwInput = document.getElementById('password');
const pwCheckMsg = document.getElementById('password-check-msg');
const pwConfirmInput = document.getElementById('password_confirm');
const pwConfirmMsg = document.getElementById('password-same-check-msg');

// 비밀번호 글자 수 확인
pwInput.addEventListener('blur', async function() {
    const pw = pwInput.value;
    const pw_check = pwConfirmInput.value;

    // 비밀번호를 입력하지 않았으면 메시지를 지움
    if (pw.length === 0) {
        pwCheckMsg.textContent = '';
        return;
    }

    // fetch API를 사용해 서버에 POST 요청 보내기
    try {
        const response = await fetch('/check-pw-register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ 'pw': pw, 'pw_check': pw_check })
        });

        const data = await response.json(); // 서버 응답을 JSON으로 파싱

        // 서버 응답(data.exists)에 따라 메시지 업데이트
        if (data.pw == 'True') {
            pwCheckMsg.textContent = '';
            pwCheckMsg.style.color = 'red';
        } else {
            pwCheckMsg.textContent = '비밀번호는 최소 6자 이상이어야 합니다.';
            pwCheckMsg.style.color = 'red';
        }

        if (data.pw_check == 'True' && data.pw == 'True') {
            pwConfirmMsg.textContent = '비밀번호가 일치하지 않습니다.';
            pwConfirmMsg.style.color = 'red';
        } else {
            pwConfirmMsg.textContent = '';
            pwConfirmMsg.style.color = 'red';
        }
    } catch (error) {
        console.error('Error:', error);
        pwCheckMsg.textContent = '확인 중 오류가 발생했습니다.';
        pwCheckMsg.style.color = 'orange';
    }
})

// 비밀번호 일치 여부 확인
pwConfirmInput.addEventListener('input', async function() {
    const pw = pwInput.value;
    const pw_check = pwConfirmInput.value;

    // 비밀번호를 입력하지 않았으면 메시지를 지움
    if (pw_check.length === 0) {
        pwConfirmMsg.textContent = '';
        return;
    }

    // fetch API를 사용해 서버에 POST 요청 보내기
    try {
        const response = await fetch('/check-pw-register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ 'pw': pw, 'pw_check': pw_check })
        });

        const data = await response.json(); // 서버 응답을 JSON으로 파싱

        // 서버 응답에 따라 메시지 업데이트
        if (data.pw_check == 'True' && data.pw == 'True') {
            pwConfirmMsg.textContent = '비밀번호가 일치하지 않습니다.';
            pwConfirmMsg.style.color = 'red';
        } else {
            pwConfirmMsg.textContent = '';
            pwConfirmMsg.style.color = 'red';
        }
    } catch (error) {
        console.error('Error:', error);
        pwConfirmMsg.textContent = '확인 중 오류가 발생했습니다.';
        pwConfirmMsg.style.color = 'orange';
    }
})