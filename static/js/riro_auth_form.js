const authbtn = document.getElementById('auth_btn');
const hakbunInput = document.getElementById('hakbun');
const pwInput = document.getElementById('password');

authbtn.addEventListener('click', async function() {
    const hakbun = hakbunInput.value;
    const pw = pwInput.value;

    try {
        const response = await fetch('/riro-auth', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 'hakbun': hakbun, 'pw': pw })
        });
    } catch (error) {
        console.log('Error :', error)
        alert('Error :', error)
    }
})