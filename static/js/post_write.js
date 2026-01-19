$(document).ready(function() {
    $.summernote.lang['ko-KR'].color.cpSelect = 'color picker';

    const MAX_CHARS = 5000;
    const MAX_IMAGES = 5; 

    const postTitleInput = $('#post-title');
    const titleCounter = $('#current-title-chars');

    if (postTitleInput.val()) {
        titleCounter.text(postTitleInput.val().length);
    }

    postTitleInput.on('keyup', function() {
        const currentLength = $(this).val().length;
        titleCounter.text(currentLength);
    });

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

    const $pollSettings = $('#poll-settings');
    const $pollToggleBtn = $('#btn-toggle-poll');
    const $pollOptionsList = $('#poll-options-list');
    const $pollTitleInput = $('input[name="poll_title"]');

    // 1. 투표 첨부 토글
    $pollToggleBtn.on('click', function() {
        $(this).hide();
        $pollSettings.slideDown();
        $pollTitleInput.focus();
    });

    // 2. 투표 삭제 (취소)
    $('#btn-remove-poll').on('click', function() {
        $pollTitleInput.val('');
        $pollOptionsList.find('input').val('');
        // 옵션 개수 초기화 (2개만 남기기)
        while ($pollOptionsList.children().length > 2) {
            $pollOptionsList.children().last().remove();
        }
        $pollSettings.slideUp();
        $pollToggleBtn.show();
    });

    // 3. 항목 추가
    $('#btn-add-option').on('click', function() {
        const currentCount = $pollOptionsList.children().length;
        if (currentCount >= 10) {
            alert('투표 항목은 최대 10개까지 가능합니다.');
            return;
        }
        const newOption = `
            <div class="poll-option-item" style="display: flex; gap: 5px; margin-bottom: 8px;">
                <input type="text" name="poll_options[]" class="form-control" placeholder="항목 ${currentCount + 1}" maxlength="30" style="flex: 1;">
                <button type="button" class="btn-del-option" style="background:none; border:none; color:#d9534f; cursor:pointer;">&times;</button>
            </div>`;
        $pollOptionsList.append(newOption);
    });

    // 4. 항목 개별 삭제
    $(document).on('click', '.btn-del-option', function() {
        $(this).parent().remove();
        // placeholder 번호 재정렬
        $pollOptionsList.children().each(function(index) {
            $(this).find('input').attr('placeholder', `항목 ${index + 1}`);
        });
    });

    $('.btn-register').on('click', function(e) {
        e.preventDefault();

        const title = $('#post-title').val();
        
        // 1. 게시판 선택 유효성 검사
        const boardId = $('#board-select').val();
        if (!boardId) {
            alert('게시판을 선택해주세요.');
            $('#board-select').focus();
            return;
        }

        if (!title.trim()) {
            alert('제목을 입력해주세요.');
            $('#post-title').focus();
            return;
        }

        if (title.length > 100) {
            alert('제목은 100자를 초과할 수 없습니다.');
            return;
        }

        // Summernote 내용 가져오기
        let content = $('#summernote-editor').summernote('code');

        // --- [핵심 수정] 인곽콘 변환 로직 통합 ---
        // 폼 제출 직전에 이미지 태그(<img data-code="~1_0">)를 텍스트(~1_0)로 변환
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = content;

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

        // 이미지 개수 제한 검사 (인곽콘 제외)
        // tempDiv는 위에서 이미 인곽콘이 텍스트로 변환되었으므로, 남은 img 태그는 순수 이미지임
        const imageCount = $(tempDiv).find('img').length; 
        
        if (imageCount > MAX_IMAGES) {
            alert(`이미지는 최대 ${MAX_IMAGES}개까지만 첨부할 수 있습니다. (현재: ${imageCount}개)`);
            return;
        }

        $('#post-content').val(content);
        $('#post-form').submit();
    });
});