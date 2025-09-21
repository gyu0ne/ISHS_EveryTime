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
});