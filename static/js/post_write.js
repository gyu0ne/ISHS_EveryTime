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

    // 이미지 파일 크기 제한 (MB 단위)
    // Windows 탐색기는 1MB = 1,000,000 바이트(10진법)로 표시하지만
    // 프로그래밍에서는 1MB = 1,048,576 바이트(2진법)를 사용
    // 사용자 혼란 방지를 위해 10진법 기준으로 계산
    const MAX_IMAGE_SIZE_MB = 5;  // 개별 이미지 최대 5MB
    const MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1000 * 1000;  // 10진법 기준 (Windows 탐색기와 동일)
    const MAX_TOTAL_IMAGE_SIZE_MB = 25;  // 총 이미지 용량 최대 25MB (5개 × 5MB)
    const MAX_TOTAL_IMAGE_SIZE_BYTES = MAX_TOTAL_IMAGE_SIZE_MB * 1000 * 1000;

    // 바이트를 MB로 변환 (10진법, Windows 탐색기와 동일)
    function bytesToMB(bytes) {
        return (bytes / 1000 / 1000).toFixed(2);
    }

    // 이미지 삽입 공통 함수
    function insertImageWithValidation(editor, file, fileName) {
        // 이미지 파일인지 확인
        if (!file.type.startsWith('image/')) {
            alert('이미지 파일만 업로드할 수 있습니다.');
            return;
        }
        
        // 파일 크기 검사
        if (file.size > MAX_IMAGE_SIZE_BYTES) {
            alert(`이미지 "${fileName}"의 크기가 너무 큽니다.\n\n` +
                  `• 현재 크기: ${bytesToMB(file.size)}MB\n` +
                  `• 최대 허용: ${MAX_IMAGE_SIZE_MB}MB\n\n` +
                  `이미지를 압축하거나 더 작은 이미지를 사용해주세요.`);
            return;
        }
        
        // 크기 검사 통과 시 Base64로 변환하여 삽입
        const reader = new FileReader();
        reader.onloadend = function() {
            editor.summernote('insertImage', reader.result);
        };
        reader.readAsDataURL(file);
    }

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
                const clipboardData = e.originalEvent.clipboardData;
                if (clipboardData && clipboardData.items) {
                    for (let i = 0; i < clipboardData.items.length; i++) {
                        const item = clipboardData.items[i];
                        if (item.type.indexOf('image') !== -1) {
                            // 이미지 붙여넣기 시 기본 동작 완전 차단 (중복 알림 방지)
                            e.preventDefault();
                            e.stopPropagation();
                            
                            const file = item.getAsFile();
                            if (file) {
                                insertImageWithValidation($(this), file, '붙여넣은 이미지');
                            }
                            return;
                        }
                    }
                }
                setTimeout(() => {
                    updateCharCount(this);
                }, 10);
            },
            onChange: function(contents, $editable) {
                updateCharCount(this);
                updateImageSizeDisplay(this);
            },
            onImageUpload: function(files) {
                // 드래그앤드롭 또는 파일 선택으로 이미지 업로드 시 크기 검사
                const editor = $(this);
                
                for (let i = 0; i < files.length; i++) {
                    insertImageWithValidation(editor, files[i], files[i].name);
                }
            }
        }
    });
    
    $('#max-chars').text(MAX_CHARS.toLocaleString());
    updateCharCount($('#summernote-editor'));
    updateImageSizeDisplay($('#summernote-editor'));

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

    // 이미지 용량 표시 업데이트 함수
    function updateImageSizeDisplay(editorInstance) {
        const content = $(editorInstance).summernote('code');
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = content;
        
        // 인곽콘(이모티콘) 제외하고 순수 이미지만 카운트
        const images = $(tempDiv).find('img').not('[data-code]');
        const imageCount = images.length;
        
        let totalImageSize = 0;
        images.each(function() {
            const src = $(this).attr('src');
            if (src && src.startsWith('data:image')) {
                const base64Data = src.split(',')[1];
                if (base64Data) {
                    totalImageSize += (base64Data.length * 3) / 4;
                }
            }
        });
        
        const totalSizeMB = totalImageSize / 1000 / 1000;
        
        // UI 업데이트
        $('#current-image-count').text(imageCount);
        $('#max-image-count').text(MAX_IMAGES);
        $('#current-image-size').text(totalSizeMB.toFixed(2));
        $('#max-image-size').text(MAX_TOTAL_IMAGE_SIZE_MB);
        
        // 제한 초과 시 경고 색상
        const sizeCounter = $('.image-size-counter');
        if (imageCount > MAX_IMAGES || totalImageSize > MAX_TOTAL_IMAGE_SIZE_BYTES) {
            sizeCounter.css('color', '#ff6b6b');
        } else if (imageCount > 0) {
            sizeCounter.css('color', '#5cb85c');
        } else {
            sizeCounter.css('color', '#888');
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
        const images = $(tempDiv).find('img');
        const imageCount = images.length; 
        
        if (imageCount > MAX_IMAGES) {
            alert(`이미지는 최대 ${MAX_IMAGES}개까지만 첨부할 수 있습니다. (현재: ${imageCount}개)`);
            return;
        }

        // 총 이미지 용량 검사 (Base64 데이터 크기 계산)
        let totalImageSize = 0;
        images.each(function() {
            const src = $(this).attr('src');
            if (src && src.startsWith('data:image')) {
                // Base64 문자열에서 실제 바이트 크기 계산
                // Base64는 원본 대비 약 1.33배 크기이므로, 실제 크기 = Base64 길이 * 3/4
                const base64Data = src.split(',')[1];
                if (base64Data) {
                    totalImageSize += (base64Data.length * 3) / 4;
                }
            }
        });

        if (totalImageSize > MAX_TOTAL_IMAGE_SIZE_BYTES) {
            alert(`총 이미지 용량이 너무 큽니다.\n\n` +
                  `• 현재 총 용량: ${bytesToMB(totalImageSize)}MB\n` +
                  `• 최대 허용: ${MAX_TOTAL_IMAGE_SIZE_MB}MB\n\n` +
                  `일부 이미지를 삭제하거나 압축해주세요.`);
            return;
        }

        $('#post-content').val(content);
        $('#post-form').submit();
    });
});