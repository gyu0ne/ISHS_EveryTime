document.addEventListener('DOMContentLoaded', function() {
    const postsTab = document.getElementById('posts-tab');
    const commentsTab = document.getElementById('comments-tab');
    const postsList = document.getElementById('posts-list');
    const commentsList = document.getElementById('comments-list');

    postsTab.addEventListener('click', function(e) {
        e.preventDefault();
    
        postsTab.classList.add('active');
        commentsTab.classList.remove('active');
    
        postsList.style.display = 'block';
        commentsList.style.display = 'none';
    });

    commentsTab.addEventListener('click', function(e) {
        e.preventDefault();
        commentsTab.classList.add('active');
        postsTab.classList.remove('active');
    
        commentsList.style.display = 'block';
        postsList.style.display = 'none';
    });

    const changePicBtn = document.getElementById('change-pic-btn');
    const modal = document.getElementById('profile-image-modal');
    const closeModalBtn = document.getElementById('modal-close-btn');
    const fileInput = document.getElementById('profile-image-input');
    const fileNameDisplay = document.querySelector('.file-name-display');

    // '변경' 버튼 클릭 시 모달 열기
    changePicBtn.addEventListener('click', function() {
        modal.style.display = 'flex';
    });

    // '취소' 버튼 클릭 시 모달 닫기
    closeModalBtn.addEventListener('click', function() {
        modal.style.display = 'none';
    });

    // 모달 오버레이 클릭 시 닫기
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            modal.style.display = 'none';
        }
    });

    // 파일 선택 시 파일명 표시
    fileInput.addEventListener('change', function() {
        if (fileInput.files.length > 0) {
            fileNameDisplay.textContent = fileInput.files[0].name;
        } else {
            fileNameDisplay.textContent = '선택된 파일 없음';
        }
    });
});