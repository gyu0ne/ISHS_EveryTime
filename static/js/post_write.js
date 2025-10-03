$(document).ready(function() {
    $.summernote.lang['ko-KR'].color.cpSelect = 'color picker';

    const MAX_CHARS = 5000;
    const MAX_IMAGES = 5; // 이미지 개수 제한 설정

    $('#summernote-editor').summernote({
        lang: 'ko-KR',
        height: 500,
        minHeight: 500,
        maxHeight: 500,
        focus: true,
        placeholder: '내용을 입력해주세요.',
        toolbar: [
            ['font', ['bold', 'underline']],
            ['fontname', ['fontname']],
            ['fontsize', ['fontsize']],
            ['color', ['forecolor', 'backcolor']],
            ['para', ['ul', 'ol', 'paragraph']],
            ['table', ['table']],
            ['insert', ['link', 'picture', 'video']]
        ],
        callbacks: {
            onKeyup: function(e) {
                updateCharCount(this);
            },
            onPaste: function(e) {
                setTimeout(() => {
                    updateCharCount(this);
                }, 10);
            },
            onChange: function(contents, $editable) {
                updateCharCount(this);
            }
            // onImageUpload 콜백은 제거합니다.
        }
    });
    
    $('#max-chars').text(MAX_CHARS.toLocaleString());
    updateCharCount($('#summernote-editor'));

    function updateCharCount(editorInstance) {
        const content = $(editorInstance).summernote('code');
        const text = $('<div>').html(content).text();
        const currentLength = text.length;

        const counterElement = $('#current-chars');
        const counterWrapper = $('.char-counter');
        
        counterElement.text(currentLength.toLocaleString());

        if (currentLength > MAX_CHARS) {
            counterWrapper.addClass('limit-exceeded');
        } else {
            counterWrapper.removeClass('limit-exceeded');
        }
    }
    
    // uploadImage 함수는 제거합니다.

    $('.btn-register').on('click', function(e) {
        e.preventDefault();

        // 1. 게시판 선택 유효성 검사
        const boardId = $('#board-select').val();
        if (!boardId) {
            alert('게시판을 선택해주세요.');
            $('#board-select').focus();
            return;
        }

        const title = $('#post-title').val();
        if (!title.trim()) {
            alert('제목을 입력해주세요.');
            $('#post-title').focus();
            return;
        }

        const content = $('#summernote-editor').summernote('code');
        const textContent = $('<div>').html(content).text();

        if ($('#summernote-editor').summernote('isEmpty')) {
            alert('내용을 입력해주세요.');
            $('#summernote-editor').summernote('focus');
            return;
        }
        
        if (textContent.length > MAX_CHARS) {
            alert(`내용은 ${MAX_CHARS.toLocaleString()}자를 초과할 수 없습니다.`);
            return;
        }

        // --- 이미지 개수 제한 검사 로직 추가 ---
        const imageCount = $(content).find('img').length;
        if (imageCount > MAX_IMAGES) {
            alert(`이미지는 최대 ${MAX_IMAGES}개까지만 첨부할 수 있습니다. (현재: ${imageCount}개)`);
            return;
        }

        $('#post-content').val(content);
        $('#post-form').submit();
    });
});